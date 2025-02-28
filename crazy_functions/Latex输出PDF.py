from toolbox import update_ui, trimmed_format_exc, get_conf, get_log_folder, promote_file_to_downloadzone
from toolbox import CatchException, report_exception, update_ui_lastest_msg, zip_result, gen_time_str
from functools import partial
import glob, os, requests, time, json, tarfile

pj = os.path.join
ARXIV_CACHE_DIR = os.path.expanduser(f"~/arxiv_cache/")


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=- 工具函数 =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
# 专业词汇声明  = 'If the term "agent" is used in this section, it should be translated to "智能体". '
def switch_prompt(pfg, mode, more_requirement):
    """
    Generate prompts and system prompts based on the mode for proofreading or translating.
    Args:
    - pfg: Proofreader or Translator instance.
    - mode: A string specifying the mode, either 'proofread' or 'translate_zh'.

    Returns:
    - inputs_array: A list of strings containing prompts for users to respond to.
    - sys_prompt_array: A list of strings containing prompts for system prompts.
    """
    n_split = len(pfg.sp_file_contents)
    if mode == 'proofread_en':
        inputs_array = [r"Below is a section from an academic paper, proofread this section." +
                        r"Do not modify any latex command such as \section, \cite, \begin, \item and equations. " + more_requirement +
                        r"Answer me only with the revised text:" +
                        f"\n\n{frag}" for frag in pfg.sp_file_contents]
        sys_prompt_array = ["You are a professional academic paper writer." for _ in range(n_split)]
    elif mode == 'translate_zh':
        inputs_array = [
            r"Below is a section from an English academic paper, translate it into Chinese. " + more_requirement +
            r"Do not modify any latex command such as \section, \cite, \begin, \item and equations. " +
            r"Answer me only with the translated text:" +
            f"\n\n{frag}" for frag in pfg.sp_file_contents]
        sys_prompt_array = ["You are a professional translator." for _ in range(n_split)]
    else:
        assert False, "未知指令"
    return inputs_array, sys_prompt_array


def desend_to_extracted_folder_if_exist(project_folder):
    """ 
    Descend into the extracted folder if it exists, otherwise return the original folder.

    Args:
    - project_folder: A string specifying the folder path.

    Returns:
    - A string specifying the path to the extracted folder, or the original folder if there is no extracted folder.
    """
    maybe_dir = [f for f in glob.glob(f'{project_folder}/*') if os.path.isdir(f)]
    if len(maybe_dir) == 0: return project_folder
    if maybe_dir[0].endswith('.extract'): return maybe_dir[0]
    return project_folder


def move_project(project_folder, arxiv_id=None):
    """ 
    Create a new work folder and copy the project folder to it.

    Args:
    - project_folder: A string specifying the folder path of the project.

    Returns:
    - A string specifying the path to the new work folder.
    """
    import shutil, time
    time.sleep(2)  # avoid time string conflict
    if arxiv_id is not None:
        new_workfolder = pj(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')
    else:
        new_workfolder = f'{get_log_folder()}/{gen_time_str()}'
    try:
        shutil.rmtree(new_workfolder)
    except:
        pass

    # align subfolder if there is a folder wrapper
    items = glob.glob(pj(project_folder, '*'))
    items = [item for item in items if os.path.basename(item) != '__MACOSX']
    if len(glob.glob(pj(project_folder, '*.tex'))) == 0 and len(items) == 1:
        if os.path.isdir(items[0]): project_folder = items[0]

    shutil.copytree(src=project_folder, dst=new_workfolder)
    return new_workfolder


def arxiv_download(chatbot, history, txt, allow_cache=True):
    def check_cached_translation_pdf(arxiv_id):
        translation_dir = pj(ARXIV_CACHE_DIR, arxiv_id, 'translation')
        if not os.path.exists(translation_dir):
            os.makedirs(translation_dir)
        target_file = pj(translation_dir, 'translate_zh.pdf')
        if os.path.exists(target_file):
            promote_file_to_downloadzone(target_file, rename_file=None, chatbot=chatbot)
            target_file_compare = pj(translation_dir, 'comparison.pdf')
            if os.path.exists(target_file_compare):
                promote_file_to_downloadzone(target_file_compare, rename_file=None, chatbot=chatbot)
            return target_file
        return False

    def is_float(s):
        try:
            float(s)
            return True
        except ValueError:
            return False

    if ('.' in txt) and ('/' not in txt) and is_float(txt):  # is arxiv ID
        txt = 'https://arxiv.org/abs/' + txt.strip()
    if ('.' in txt) and ('/' not in txt) and is_float(txt[:10]):  # is arxiv ID
        txt = 'https://arxiv.org/abs/' + txt[:10]

    if not txt.startswith('https://arxiv.org'): 
        return txt, None    # 是本地文件，跳过下载
    
    # <-------------- inspect format ------------->
    chatbot.append([f"检测到arxiv文档连接", '尝试下载 ...'])
    yield from update_ui(chatbot=chatbot, history=history)
    time.sleep(1)  # 刷新界面

    url_ = txt  # https://arxiv.org/abs/1707.06690
    if not txt.startswith('https://arxiv.org/abs/'):
        msg = f"解析arxiv网址失败, 期望格式例如: https://arxiv.org/abs/1707.06690。实际得到格式: {url_}。"
        yield from update_ui_lastest_msg(msg, chatbot=chatbot, history=history)  # 刷新界面
        return msg, None
    # <-------------- set format ------------->
    arxiv_id = url_.split('/abs/')[-1]
    if 'v' in arxiv_id: arxiv_id = arxiv_id[:10]
    cached_translation_pdf = check_cached_translation_pdf(arxiv_id)
    if cached_translation_pdf and allow_cache: return cached_translation_pdf, arxiv_id

    url_tar = url_.replace('/abs/', '/e-print/')
    translation_dir = pj(ARXIV_CACHE_DIR, arxiv_id, 'e-print')
    extract_dst = pj(ARXIV_CACHE_DIR, arxiv_id, 'extract')
    os.makedirs(translation_dir, exist_ok=True)

    # <-------------- download arxiv source file ------------->
    dst = pj(translation_dir, arxiv_id + '.tar')
    if os.path.exists(dst):
        yield from update_ui_lastest_msg("调用缓存", chatbot=chatbot, history=history)  # 刷新界面
    else:
        yield from update_ui_lastest_msg("开始下载", chatbot=chatbot, history=history)  # 刷新界面
        proxies = get_conf('proxies')
        r = requests.get(url_tar, proxies=proxies)
        with open(dst, 'wb+') as f:
            f.write(r.content)
    # <-------------- extract file ------------->
    yield from update_ui_lastest_msg("下载完成", chatbot=chatbot, history=history)  # 刷新界面
    from toolbox import extract_archive
    extract_archive(file_path=dst, dest_dir=extract_dst)
    return extract_dst, arxiv_id


def pdf2tex_project(pdf_file_path):
    # Mathpix API credentials
    app_id, app_key = get_conf('MATHPIX_APPID', 'MATHPIX_APPKEY')
    headers = {"app_id": app_id, "app_key": app_key}

    # Step 1: Send PDF file for processing
    options = {
        "conversion_formats": {"tex.zip": True},
        "math_inline_delimiters": ["$", "$"],
        "rm_spaces": True
    }

    response = requests.post(url="https://api.mathpix.com/v3/pdf",
                             headers=headers,
                             data={"options_json": json.dumps(options)},
                             files={"file": open(pdf_file_path, "rb")})

    if response.ok:
        pdf_id = response.json()["pdf_id"]
        print(f"PDF processing initiated. PDF ID: {pdf_id}")

        # Step 2: Check processing status
        while True:
            conversion_response = requests.get(f"https://api.mathpix.com/v3/pdf/{pdf_id}", headers=headers)
            conversion_data = conversion_response.json()

            if conversion_data["status"] == "completed":
                print("PDF processing completed.")
                break
            elif conversion_data["status"] == "error":
                print("Error occurred during processing.")
            else:
                print(f"Processing status: {conversion_data['status']}")
                time.sleep(5)  # wait for a few seconds before checking again

        # Step 3: Save results to local files
        output_dir = os.path.join(os.path.dirname(pdf_file_path), 'mathpix_output')
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        url = f"https://api.mathpix.com/v3/pdf/{pdf_id}.tex"
        response = requests.get(url, headers=headers)
        file_name_wo_dot = '_'.join(os.path.basename(pdf_file_path).split('.')[:-1])
        output_name = f"{file_name_wo_dot}.tex.zip"
        output_path = os.path.join(output_dir, output_name)
        with open(output_path, "wb") as output_file:
            output_file.write(response.content)
        print(f"tex.zip file saved at: {output_path}")

        import zipfile
        unzip_dir = os.path.join(output_dir, file_name_wo_dot)
        with zipfile.ZipFile(output_path, 'r') as zip_ref:
            zip_ref.extractall(unzip_dir)

        return unzip_dir

    else:
        print(f"Error sending PDF for processing. Status code: {response.status_code}")
        return None


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-= 插件主程序1 =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=    


@CatchException
def Latex英文纠错加PDF对比(txt, llm_kwargs, plugin_kwargs, chatbot, history, system_prompt, user_request):
    # <-------------- information about this plugin ------------->
    chatbot.append(["函数插件功能？",
                    "对整个Latex项目进行纠错, 用latex编译为PDF对修正处做高亮。函数插件贡献者: Binary-Husky。注意事项: 目前仅支持GPT3.5/GPT4，其他模型转化效果未知。目前对机器学习类文献转化效果最好，其他类型文献转化效果未知。仅在Windows系统进行了测试，其他操作系统表现未知。"])
    yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面

    # <-------------- more requirements ------------->
    if ("advanced_arg" in plugin_kwargs) and (plugin_kwargs["advanced_arg"] == ""): plugin_kwargs.pop("advanced_arg")
    more_req = plugin_kwargs.get("advanced_arg", "")
    _switch_prompt_ = partial(switch_prompt, more_requirement=more_req)

    # <-------------- check deps ------------->
    try:
        import glob, os, time, subprocess
        subprocess.Popen(['pdflatex', '-version'])
        from .latex_fns.latex_actions import Latex精细分解与转化, 编译Latex
    except Exception as e:
        chatbot.append([f"解析项目: {txt}",
                        f"尝试执行Latex指令失败。Latex没有安装, 或者不在环境变量PATH中。安装方法https://tug.org/texlive/。报错信息\n\n```\n\n{trimmed_format_exc()}\n\n```\n\n"])
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- clear history and read input ------------->
    history = []
    if os.path.exists(txt):
        project_folder = txt
    else:
        if txt == "": txt = '空空如也的输入栏'
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到本地项目或无权访问: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return
    file_manifest = [f for f in glob.glob(f'{project_folder}/**/*.tex', recursive=True)]
    if len(file_manifest) == 0:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到任何.tex文件: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- if is a zip/tar file ------------->
    project_folder = desend_to_extracted_folder_if_exist(project_folder)

    # <-------------- move latex project away from temp folder ------------->
    project_folder = move_project(project_folder, arxiv_id=None)

    # <-------------- if merge_translate_zh is already generated, skip gpt req ------------->
    if not os.path.exists(project_folder + '/merge_proofread_en.tex'):
        yield from Latex精细分解与转化(file_manifest, project_folder, llm_kwargs, plugin_kwargs,
                                       chatbot, history, system_prompt, mode='proofread_en',
                                       switch_prompt=_switch_prompt_)

    # <-------------- compile PDF ------------->
    success = yield from 编译Latex(chatbot, history, main_file_original='merge',
                                   main_file_modified='merge_proofread_en',
                                   work_folder_original=project_folder, work_folder_modified=project_folder,
                                   work_folder=project_folder)

    # <-------------- zip PDF ------------->
    zip_res = zip_result(project_folder)
    if success:
        chatbot.append((f"成功啦", '请查收结果（压缩包）...'))
        yield from update_ui(chatbot=chatbot, history=history);
        time.sleep(1)  # 刷新界面
        promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)
    else:
        chatbot.append((f"失败了",
                        '虽然PDF生成失败了, 但请查收结果（压缩包）, 内含已经翻译的Tex文档, 也是可读的, 您可以到Github Issue区, 用该压缩包+对话历史存档进行反馈 ...'))
        yield from update_ui(chatbot=chatbot, history=history);
        time.sleep(1)  # 刷新界面
        promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)

    # <-------------- we are done ------------->
    return success


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-= 插件主程序2 =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=    

@CatchException
def Latex翻译中文并重新编译PDF(txt, llm_kwargs, plugin_kwargs, chatbot, history, system_prompt, user_request):
    # <-------------- information about this plugin ------------->
    chatbot.append([
        "函数插件功能？",
        "对整个Latex项目进行翻译, 生成中文PDF。函数插件贡献者: Binary-Husky。注意事项: 此插件Windows支持最佳，Linux下必须使用Docker安装，详见项目主README.md。目前仅支持GPT3.5/GPT4，其他模型转化效果未知。目前对机器学习类文献转化效果最好，其他类型文献转化效果未知。"])
    yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面

    # <-------------- more requirements ------------->
    if ("advanced_arg" in plugin_kwargs) and (plugin_kwargs["advanced_arg"] == ""): plugin_kwargs.pop("advanced_arg")
    more_req = plugin_kwargs.get("advanced_arg", "")
    no_cache = more_req.startswith("--no-cache")
    if no_cache: more_req.lstrip("--no-cache")
    allow_cache = not no_cache
    _switch_prompt_ = partial(switch_prompt, more_requirement=more_req)

    # <-------------- check deps ------------->
    try:
        import glob, os, time, subprocess
        subprocess.Popen(['pdflatex', '-version'])
        from .latex_fns.latex_actions import Latex精细分解与转化, 编译Latex
    except Exception as e:
        chatbot.append([f"解析项目: {txt}",
                        f"尝试执行Latex指令失败。Latex没有安装, 或者不在环境变量PATH中。安装方法https://tug.org/texlive/。报错信息\n\n```\n\n{trimmed_format_exc()}\n\n```\n\n"])
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- clear history and read input ------------->
    history = []
    try:
        txt, arxiv_id = yield from arxiv_download(chatbot, history, txt, allow_cache)
    except tarfile.ReadError as e:
        yield from update_ui_lastest_msg(
            "无法自动下载该论文的Latex源码，请前往arxiv打开此论文下载页面，点other Formats，然后download source手动下载latex源码包。接下来调用本地Latex翻译插件即可。", 
            chatbot=chatbot, history=history)
        return

    if txt.endswith('.pdf'):
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"发现已经存在翻译好的PDF文档")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    if os.path.exists(txt):
        project_folder = txt
    else:
        if txt == "": txt = '空空如也的输入栏'
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到本地项目或无法处理: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    file_manifest = [f for f in glob.glob(f'{project_folder}/**/*.tex', recursive=True)]
    if len(file_manifest) == 0:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到任何.tex文件: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- if is a zip/tar file ------------->
    project_folder = desend_to_extracted_folder_if_exist(project_folder)

    # <-------------- move latex project away from temp folder ------------->
    project_folder = move_project(project_folder, arxiv_id)

    # <-------------- if merge_translate_zh is already generated, skip gpt req ------------->
    if not os.path.exists(project_folder + '/merge_translate_zh.tex'):
        yield from Latex精细分解与转化(file_manifest, project_folder, llm_kwargs, plugin_kwargs,
                                       chatbot, history, system_prompt, mode='translate_zh',
                                       switch_prompt=_switch_prompt_)

    # <-------------- compile PDF ------------->
    success = yield from 编译Latex(chatbot, history, main_file_original='merge',
                                   main_file_modified='merge_translate_zh', mode='translate_zh',
                                   work_folder_original=project_folder, work_folder_modified=project_folder,
                                   work_folder=project_folder)

    # <-------------- zip PDF ------------->
    zip_res = zip_result(project_folder)
    if success:
        chatbot.append((f"成功啦", '请查收结果（压缩包）...'))
        yield from update_ui(chatbot=chatbot, history=history);
        time.sleep(1)  # 刷新界面
        promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)
    else:
        chatbot.append((f"失败了",
                        '虽然PDF生成失败了, 但请查收结果（压缩包）, 内含已经翻译的Tex文档, 您可以到Github Issue区, 用该压缩包进行反馈。如系统是Linux，请检查系统字体（见Github wiki） ...'))
        yield from update_ui(chatbot=chatbot, history=history);
        time.sleep(1)  # 刷新界面
        promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)

    # <-------------- we are done ------------->
    return success


#  =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=- 插件主程序3  =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=   

@CatchException
def PDF翻译中文并重新编译PDF(txt, llm_kwargs, plugin_kwargs, chatbot, history, system_prompt, web_port):
    # <-------------- information about this plugin ------------->
    chatbot.append([
        "函数插件功能？",
        "将PDF转换为Latex项目，翻译为中文后重新编译为PDF。函数插件贡献者: Marroh。注意事项: 此插件Windows支持最佳，Linux下必须使用Docker安装，详见项目主README.md。目前仅支持GPT3.5/GPT4，其他模型转化效果未知。目前对机器学习类文献转化效果最好，其他类型文献转化效果未知。"])
    yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面

    # <-------------- more requirements ------------->
    if ("advanced_arg" in plugin_kwargs) and (plugin_kwargs["advanced_arg"] == ""): plugin_kwargs.pop("advanced_arg")
    more_req = plugin_kwargs.get("advanced_arg", "")
    no_cache = more_req.startswith("--no-cache")
    if no_cache: more_req.lstrip("--no-cache")
    allow_cache = not no_cache
    _switch_prompt_ = partial(switch_prompt, more_requirement=more_req)

    # <-------------- check deps ------------->
    try:
        import glob, os, time, subprocess
        subprocess.Popen(['pdflatex', '-version'])
        from .latex_fns.latex_actions import Latex精细分解与转化, 编译Latex
    except Exception as e:
        chatbot.append([f"解析项目: {txt}",
                        f"尝试执行Latex指令失败。Latex没有安装, 或者不在环境变量PATH中。安装方法https://tug.org/texlive/。报错信息\n\n```\n\n{trimmed_format_exc()}\n\n```\n\n"])
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- clear history and read input ------------->
    if os.path.exists(txt):
        project_folder = txt
    else:
        if txt == "": txt = '空空如也的输入栏'
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到本地项目或无法处理: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    file_manifest = [f for f in glob.glob(f'{project_folder}/**/*.pdf', recursive=True)]
    if len(file_manifest) == 0:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到任何.pdf文件: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return
    if len(file_manifest) != 1:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"不支持同时处理多个pdf文件: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return
    app_id, app_key = get_conf('MATHPIX_APPID', 'MATHPIX_APPKEY')
    if len(app_id) == 0 or len(app_key) == 0:
        report_exception(chatbot, history, a=f"请配置 MATHPIX_APPID 和 MATHPIX_APPKEY")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- convert pdf into tex ------------->
    project_folder = pdf2tex_project(file_manifest[0])

    # Translate English Latex to Chinese Latex, and compile it
    file_manifest = [f for f in glob.glob(f'{project_folder}/**/*.tex', recursive=True)]
    if len(file_manifest) == 0:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到任何.tex文件: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- if is a zip/tar file ------------->
    project_folder = desend_to_extracted_folder_if_exist(project_folder)

    # <-------------- move latex project away from temp folder ------------->
    project_folder = move_project(project_folder)

    # <-------------- if merge_translate_zh is already generated, skip gpt req ------------->
    if not os.path.exists(project_folder + '/merge_translate_zh.tex'):
        yield from Latex精细分解与转化(file_manifest, project_folder, llm_kwargs, plugin_kwargs,
                                       chatbot, history, system_prompt, mode='translate_zh',
                                       switch_prompt=_switch_prompt_)

    # <-------------- compile PDF ------------->
    success = yield from 编译Latex(chatbot, history, main_file_original='merge',
                                   main_file_modified='merge_translate_zh', mode='translate_zh',
                                   work_folder_original=project_folder, work_folder_modified=project_folder,
                                   work_folder=project_folder)

    # <-------------- zip PDF ------------->
    zip_res = zip_result(project_folder)
    if success:
        chatbot.append((f"成功啦", '请查收结果（压缩包）...'))
        yield from update_ui(chatbot=chatbot, history=history);
        time.sleep(1)  # 刷新界面
        promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)
    else:
        chatbot.append((f"失败了",
                        '虽然PDF生成失败了, 但请查收结果（压缩包）, 内含已经翻译的Tex文档, 您可以到Github Issue区, 用该压缩包进行反馈。如系统是Linux，请检查系统字体（见Github wiki） ...'))
        yield from update_ui(chatbot=chatbot, history=history);
        time.sleep(1)  # 刷新界面
        promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)

    # <-------------- we are done ------------->
    return success
