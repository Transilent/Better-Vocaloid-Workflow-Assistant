# Better Vocaloid Workflow Assistant

面向 Windows 64 位的调音流程助手：输入 B站 BV 号或链接，自动下载原视频和音频、分离人声与伴奏、识别主唱及和声 MIDI，并准备双平台上传资料。

## 使用便携版

从本仓库的 **Releases** 获取 `BVWA-Windows-x64` 便携包。完整 ZIP 解压后是 `BVWA` 文件夹；如果下载的是分卷附件，请将所有 `.zip.001`、`.zip.002` 等文件放在同一目录，使用 7-Zip 打开 `.zip.001` 解压，或运行 Release 中的 `Merge-PortableParts.ps1` 得到完整 ZIP（对应仓库的 `tools/合并分卷.ps1`）。

1. 将整个文件夹解压到可写目录，例如 `D:\BVWA`，不要直接在压缩包内启动。
2. 双击 **启动助手.bat**。无需另外安装 Python、模型、FFmpeg 或浏览器。
3. 输入 BV 号，或将“音乐来源”切换为 **导入本地音乐**，选择 WAV / MP3 / FLAC / M4A 等文件，再选择分离方式和歌词语言，开始处理。
4. 将 `jobs/任务目录/midi/voices.mid` 与伴奏导入调音软件。调音及成品视频对齐合成由人完成。
5. 在“发布准备 / 上传”页选择成品 MP4，填写歌姬与署名，准备封面和简介。
6. 新电脑首次上传时分别登录 B站、小红书。上传填写完成后核对页面，手动点击最终发布。

推荐 Windows 10/11 x64、16 GB 及以上内存、至少 12 GB 可用磁盘空间。DirectML / Vulkan 可使用兼容显卡；显卡运行遇到问题时，双击 **切换CPU模式.bat** 后重新启动，CPU 推理较慢。便携包适用于 Windows x64，未提供 macOS、Linux 或 Windows ARM 原生版本。

## 三种处理方式

| 方式 | 适用场景 | 输出 |
| --- | --- | --- |
| 模型全自动 | 无需打开分离软件 | 主唱、和声、伴奏、双轨 MIDI |
| 软件分轨导入 | 先用 SpectraLayers 获得更好的音频 | 导入完整主唱、和声、伴奏 WAV，再提取双轨 MIDI |
| 合并人声单轨 | 只需要一个人声轨 | 人声、伴奏、单声部 MIDI |

模型全自动使用 Inst HQ 3 和 Karaoke 2 两级 MDX 分离；MIDI 使用随包保存的 Vocal2Midi ONNX 推理版本。软件导入方式保留为可选方案。主唱和和声可以分别填写参考歌词，歌词识别和音准仍需试听核对。

本地音乐无需 BV 号，跳过网络抓取及下载，保存原文件副本，再统一解码、分离和提取 MIDI。输入文件不会被修改。支持继续上次任务、重做分离及三种处理方式；参考歌词可直接手填。本地音乐没有对应的视频封面和 STAFF，发布时可手填来源、选封面，或另外读取 BV 资料。

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --audio-file 'D:\Music\歌曲.mp3' --voice-mode dual
```

封面默认读取本家在 B站展示的原始封面。B站 4:3 封面采用完整图片上下补边，16:9 保留原比例。原图获取失败时会提示。标题支持 `【歌姬翻唱】歌名`，简介保留来源视频、引用原曲及 STAFF，可手动编辑。

## 源码与完整备份

仓库保存助手全部源码、嵌入的 Vocal2Midi 和浏览器自动化源码、配置、测试、打包工具及逐文件 SHA-256 依赖清单。Python、模型、FFmpeg 和浏览器二进制保存于私有 Release 的分卷便携附件；其中单个模型大于 GitHub 的普通文件限制，故不作为 Git blob 提交。

**仅下载 GitHub 自动生成的 Source code.zip 无法直接启动。** 获取完整便携包即可使用；源码开发可先解压便携版，再将 Git 仓库内容覆盖到 `BVWA`。

项目不打包登录会话、账号令牌、私人视频、歌曲、生成的 MIDI、任务历史或发布草稿。`cache/`、`jobs/` 和 `publish-packages/` 在运行后生成并由 Git 忽略。新电脑使用独立会话，需要重新登录。

## 开发与测试

```powershell
# 在已恢复依赖的项目目录执行
& '.\dependencies\vocal2midi\python\python.exe' -B .\launch.py --check
& '.\dependencies\vocal2midi\python\python.exe' -B .\check_dependencies.py --full
& '.\dependencies\vocal2midi\python\python.exe' -B .\tests\run_tests.py

# 从当前目录中的依赖构建 ZIP，产物放在项目外
& '.\dependencies\vocal2midi\python\python.exe' -B .\tools\build_portable.py --output '..\BVWA-Windows-x64.zip'
```

测试使用合成音视频和本地网页，验证双比例封面、HTTP 文件上传、标题简介填写及最终发布保护；不登录、不向外部平台上传测试文件。打包工具按明确的文件清单取文件，不收集运行时个人资料。

详细操作见 [使用说明](docs/使用说明.md)，文件结构见 [结构说明](docs/结构说明.md)，依赖和许可证见 [第三方说明](THIRD_PARTY_NOTICES.md)。
