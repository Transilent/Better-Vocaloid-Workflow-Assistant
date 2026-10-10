# 术力口工作流助手

Better Vocaloid Workflow Assistant (BVWA)

[English](README.md) | [简体中文](README.zh-CN.md)

Windows x64 便携版。从 B站视频或本地音乐生成 MIDI，人工调音与剪辑后准备上传。

## 启动

1. 将完整 `BVWA` 文件夹解压到可写目录，例如 `D:\BVWA`。
2. 双击 **BVWA.exe**，启动时不会显示控制台窗口。
3. 在“处理音乐”选择 BV / 链接或本地音乐，选择分离方案、歌词语言，点击“开始处理”。
4. 在“任务与结果”查看 MIDI、WAV；任务中断后可“继续任务”。

侧栏包含处理音乐、模型与组件、任务与结果、发布准备、工作流、缓存与磁盘、歌词转字幕、设置与更新。参考歌词和日志
按需展开。调音、成品音视频对齐由人完成。VOCALOID 6 中通过“文件 → 导入”载入
MIDI，歌词选择 UTF-8。

## 从 v0.2.0 升级

v0.3.0 调整了基础运行库清单，需要下载完整便携包。先关闭助手，再解压到新的文件夹。在同一电脑保留数据时，按需从旧目录复制 `config.json`、`jobs/`、`publish-packages/`、`last_job.txt`、`cache/workspace.json` 和 `cache/publish-browser/`。已安装的 BS-RoFormer 可复制 `dependencies/optional/bs-roformer/` 保留。自定义任务目录继续放在原位置。不要用旧程序文件或更新缓存覆盖新版；换电脑后需重新登录发布平台。

程序更新包用于基础运行库相同的安装目录；运行库不同的版本会在“设置与更新”中提示下载完整版。

## 保存目录

在“输出与处理设置”点击“选择目录”，或输入新的文件夹路径。默认使用助手目录的 `jobs` 文件夹。每个任务会新建独立子文件夹，保存下载的视频（BV 输入为 `video/source.mp4`）、音频和 MIDI。助手会记住所选目录及以前的任务位置；继续任务沿用原目录，重新处理则在当前选定目录中新建任务。

## 歌词与更新

中文模式下，在“中文歌词格式”选择汉字或拼音。设置同时用于两个声部，并随任务保存。

在“设置与更新”检查新版本，选择官方或加速源。可开启启动检查和自动下载；校验完成后重启安装。更新保留配置、任务、平台登录和已安装模型。

GitHub 文件支持 GitProxy，Hugging Face 模型支持 HF-Mirror。下载文件均进行 SHA-256 校验。启动诊断可运行 `tools/Start-Diagnostics.bat`。

## 可选模型

Karaoke 2 已内置。使用 BS-RoFormer：打开“模型与组件”，选择运行库，
点击“下载并安装”，完成后点击“使用此模型”。

| 运行库 | 下载量 | 预计安装占用 |
| --- | --- | --- |
| CPU | 0.46 GB | 1.56 GB |
| NVIDIA GPU / CUDA | 3.52 GB | 6.14 GB |

安装器显示所需临时空间、核对文件，并支持取消与重试。安装后可离线使用。
只保留一套已启用的可选运行库。CPU 与 CUDA 保留原分离权重，日语歌词仍使用
Romaji ASR。分离、音高和歌词需要试听校正。

## 发布准备

选择来源任务和成品视频，核对本家原始封面，编辑各平台标题与简介。
点击“生成发布资料”后，主按钮切换为“上传所选平台”。登录入口在
“账号与上传控制”中。上传后在网页检查并手动发布。

## 文件与排错

- 保留完整的 `dependencies`、`models`、`assets`、`components` 文件夹。
  换电脑时复制整个 BVWA 目录。
- 任务、账号和草稿分别存于 `jobs`、`cache`、`publish-packages`。
  每台新电脑需要重新登录平台。
- 启动错误见 `launcher.log`。
- `tools/Check-Dependencies.bat` 检查运行库完整性。
- `tools/Use-CPU.bat`、`tools/Use-GPU.bat` 切换基础分离和 MIDI 的 CPU / DirectML
  模式；也可在工作流中分别设置基础分离、MIDI 与可选模型设备。

完整指南及源码见[项目仓库](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant)。
授权说明见[第三方说明](THIRD_PARTY_NOTICES.zh-CN.md)。

## 相关项目

助手使用 [Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi)、
[UVR 模型](https://github.com/TRvlvr/model_repo)、
[Pymss](https://github.com/pymss-project/pymss)、
[BS-RoFormer 权重](https://huggingface.co/becruily/bs-roformer-karaoke)、
[PyTorch](https://pytorch.org/)、[ONNX Runtime](https://github.com/microsoft/onnxruntime)、
[yt-dlp](https://github.com/yt-dlp/yt-dlp)、[FFmpeg](https://ffmpeg.org/)、
[Playwright](https://github.com/microsoft/playwright-python)、
[PyQt](https://www.riverbankcomputing.com/software/pyqt/) 及其支持库。
界面布局参考 [Pymss Studio](https://github.com/pymss-project/pymss-studio)。
原有致谢和许可随相应组件保留。

## 工作流、任务与恢复

在**处理音乐**顶部的“工作流预设”中选择内置或自建预设，添加歌曲后点击“开始处理”。流程、模型、语言、歌词格式和设备会一起应用。需要修改时打开**工作流**，保存后点击“用于处理音乐”返回；两个页面的预设选择保持同步。

把左侧模块拖入画布，靠近兼容的前后模块时会显示连接预览；松开后自动吸附并连接，拉远并松开可断开。双击左侧模块会添加到前一个模块之后。点击模块编辑参数，右键可移除模块或连接。**整理画布**按处理顺序排列并连接相邻模块，Ctrl + 滚轮可缩放。

支持的顺序是**音乐输入 → 分离声部 → MIDI → 字幕**。在分离节点结束时只导出音频，不运行 MIDI 提取；字幕节点根据 MIDI 歌词事件生成 SRT 和 WebVTT。缺少连接或顺序错误时会提示修正，不会创建任务。歌曲、BV 和参考歌词在处理页面填写。

选中节点后，可修改语言、模型、歌词格式、提取精度和设备。基础分离与 MIDI 支持 CPU / DirectML；可选 BS-RoFormer 支持自动 / CPU / CUDA。当前参数和自建预设会保存在本机，预设不包含歌曲路径或参考歌词。继续任务使用该任务建立时的参数；**用当前工作流重做**会使用原输入与当前工作流创建新任务。

**任务与结果**支持搜索、状态筛选、排序和重命名。任务菜单提供打开日志、把 MIDI 歌词送入字幕工具和删除任务。删除前会显示目录与占用，并请求确认；删除任务会永久移除对应文件。异常退出后，原任务会标为“已中断”，可继续处理。

进度区域显示当前阶段、声部或模型子任务、已完成阶段和已用时间。能测量的环节显示片段数或下载大小与速度；阶段数量不代表剩余时间估算。失败后可查看原因并重试；设备错误提供“改用 CPU 继续”，磁盘错误可跳转检查占用。已完成且验证通过的输出会复用。

## 缓存管理与歌词字幕

在**缓存与磁盘**扫描占用，勾选缓存或任务临时文件，确认后清理。任务结果、已安装模型、登录信息和预设不会被清理。处理、组件安装或上传准备运行时，缓存操作会等待它们结束。清除任务临时文件后，重试时可能需要重新生成。

**歌词转字幕**支持 UTF-8 的 LRC、TXT、带时间的 CSV（`start,end,text` 三列，单位为秒）及 MIDI 歌词事件。可预览、逐行修改时间与文字、调整整体偏移，再导出 UTF-8 的 SRT 或 WebVTT。LRC 保留原时间戳，支持重复时间、offset 标签与带时间的空行；末行没有结束标记时默认显示 3 秒。MIDI 分句按歌词停顿推断。纯文本没有时间信息，工具会按指定总时长均分作为初始估算，需要人工核对。

便携版清单已排除开发安装器、第三方测试文件、未使用的 Qt Quick 资源和流程不会加载的备用 HFA 文件。现用中日文 ASR、音高与对齐权重保持不变。目前可减少约 447 MB 解压体积，实际压缩大小会在批准打包后测量。
