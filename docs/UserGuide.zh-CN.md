# 使用指南

[English](UserGuide.en.md) | [简体中文](UserGuide.zh-CN.md) | [README](../README.zh-CN.md)

## 输入

完整便携目录已带运行库；源码目录的启动器会先安装依赖。优先读取本目录或上一级的完整 ZIP，否则下载 Release 文件（约 5.7 GB）。安装校验依赖哈希，保留助手设置、任务和登录会话；中断后再次运行启动器即可继续。

双击 `Start-Assistant.bat`，打开“处理音乐”。“音乐来源”支持“B站 BV 号 / 链接”和“本地音乐文件”。“选择文件”打开文件选择器。本地输入无需 BV，先保存副本再解码，原文件不变。原文件移动后仍可从副本继续任务。

## 分离与识别

侧栏提供“处理音乐”“模型与组件”“任务与结果”“发布准备”。参考歌词、高级设置
和日志默认折叠；常用处理页只有一个“开始处理”主要操作。

| 选项 | 用途 |
| --- | --- |
| 自动分离 · 主唱 / 和声双轨 | 两级分离，分别识别主唱、和声 |
| 导入外部分轨 | 使用完整主唱、和声、伴奏 WAV |
| 合并人声 · 单轨 MIDI | 只提取合并人声 |
| 中文 / 日语 | 歌词处理语言 |
| 识别歌词并写入 MIDI | 关闭时只提取音符 |
| 精细提取 | 开启为 16 步 GAME，关闭为 8 步 |

外部分轨需来自同一输入，从 0 秒导出完整范围，保留开头静音。单声道和 48 kHz WAV 会转换；长度不符会拒绝。长度相同不能证明没有整体偏移。

展开“参考歌词（可选）”填写歌词，空格、标点和换行会去除。主唱与和声分别填写，和声留空时独立识别。不要粘贴作者名单或完整简介。

## 可选模型组件

在“模型与组件”选择 CPU 或 NVIDIA GPU / CUDA，点击“下载并安装”。下载量约
为 0.46 GB 或 3.52 GB，安装后约需 1.56 GB 或 6.14 GB。界面显示安装与临时
下载所需空间。基础环境保留；同一时间只保留一份可选推理环境。

下载使用固定版本及 SHA-256 校验，支持取消和续传。验证失败不会启用不完整组件。
重试可继续下载；修复已安装环境可用“更多 → 重新安装所选运行库”。

安装成功后点击“使用此模型”，或在处理页选择 BS-RoFormer。“高级设置”可指定
新模型设备：自动模式优先可用 CUDA，否则使用 CPU；明确指定 CUDA 而不可用时会报错。
Karaoke 2 可直接使用。迁移助手时保留完整 `dependencies/optional` 文件夹。

模型按音乐角色分离，不按歌姬身份识别。需试听串音、漏音及错字；更多步数不能保证更高准确率。

中文歌词使用 Qwen ASR；日语保留包内 Romaji ASR，再由 HubertFA 对齐，选择日语不会改用 Qwen 权重。识别为空或对齐不可用的片段保留仅音符结果，并写入对齐报告；单个片段出错不会丢弃其他已对齐片段。日语短音节修复不会跨过超过 150 毫秒的停顿。

## 运行与恢复

“开始处理”新建任务。在“任务与结果”选中任务，用“继续任务”沿用保存参数；
“重新分离 / MIDI”按处理页的当前选项新建任务并复用输入。“取消处理”在运行时显示，
模型最多等待 30 秒后强制停止；“打开结果目录”查看选中任务。

输入、设置变化或 MIDI 损坏使相应缓存失效。主唱完成后可只续跑和声。静音和声会输出空轨并提示。

## 结果

| 任务内文件 | 内容 |
| --- | --- |
| `audio/source.wav` | 完整解码原音频，44.1 kHz 双声道浮点 WAV |
| `audio/lead.wav`、`audio/backing.wav` | 主唱、和声 |
| `audio/instrumental.wav`、`audio/vocals.wav` | 伴奏、合并人声 |
| `midi/voices.mid` | 两个声部音符轨及速度元数据 |
| `midi/lead/lead.mid`、`midi/backing/backing.mid` | 单独导入的声部 MIDI |
| `midi/vocals.mid` | 单声部结果 |
| `midi/lead/lead_alignment.json`、`midi/backing/backing_alignment.json` | 片段秒数、对齐覆盖与回退原因，不含歌词原文 |
| `midi/需要核对的音符.csv` | 按秒记录的核对提示，不自动改音高 |
| `original-cover.*` | BV 输入的本家封面 |
| `status.json` 与日志 | 步骤和诊断 |

MIDI 以 120 BPM 表示实际时间，关闭量化；120 不是检测出的歌曲速度。修改工程 BPM 时应保持秒数位置。调音与最终音视频对齐由人完成。

在 VOCALOID 6 中，先建工程，再使用“文件 → 导入”载入 `voices.mid` 或单独声部 MIDI。“打开”用于工程文件。导入对话框若提供歌词编码选项，请选 UTF-8；6.2 起支持读取 MIDI 内的歌词。参见[官方手册](https://rsc-net.vocaloid.com/assets/pdf_files/bb/VOCALOID_Reference_Manual_ENG.pdf)与[MIDI 导入说明](https://www.vocaloid.com/en/support/faq/617)。

## 发布准备

1. 在“发布准备”选择来源任务或读取 BV 资料。
2. 选择同时含视频、音频的成品。
3. 填写翻唱歌姬、调音署名，套用 `【歌姬翻唱】歌名` 或自行编辑。
4. 核对每个平台的简介，来源署名是可编辑草稿。
5. 获取本家封面，或主动选择截图、手选图片。本地音乐需手选图片并补全来源与 STAFF，或另行读取 BV。
6. 点击“生成发布资料”，4:3、16:9 封面必要时补边，保留整图。
7. 展开“账号与上传控制”，在各平台专用浏览器登录，完成后关闭登录窗口。
8. 点击主按钮“上传所选平台”；程序等待上传完成，填写文字和封面，保留浏览器。
9. 检查分区、标签、来源及其他必填项，手动发布。

成品或封面改变后重新生成发布包。“继续填写”可更新仍打开且视频相同的上传页面，不重复上传视频。浏览器关闭后需重新上传。网站改版可能需要人工处理或更新选择器。

## 命令行与诊断

在解压后的程序目录执行：

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --source 'BV_IDENTIFIER' --voice-mode dual
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --audio-file 'D:\Music\song.mp3' --voice-mode dual
& '.\dependencies\vocal2midi\python\python.exe' -B .\pipeline.py --job 'JOB_FOLDER'
& '.\dependencies\vocal2midi\python\python.exe' -B .\separate_audio.py --input 'INPUT_AUDIO' --output 'EMPTY_OUTPUT_FOLDER'
```

`--notes-only` 仅音符；`--midi-steps 8|16|32` 设置步数；`--until separation` 在 MIDI 前停止。三种输入参数互斥。本地分离输出目录必须为空。

`Check-Dependencies.bat` 校验依赖；启动错误见 `launcher.log`。CPU/GPU 切换脚本修改设备设置，重启后生效。

`cache/` 保存临时文件、专用登录会话，`jobs/` 保存音频和 MIDI，`publish-packages/` 保存发布资料。它们不进入 Git 或干净发行包。每台新电脑需重新登录。
