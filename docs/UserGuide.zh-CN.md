# 使用指南

[English](UserGuide.en.md) | [简体中文](UserGuide.zh-CN.md) | [README](../README.zh-CN.md)

## 输入

双击 `Start-Assistant.bat`。“音乐来源”支持“B站 BV 号 / 链接”和“导入本地音乐”。“选择音乐…”打开文件选择器。本地输入无需 BV，先保存副本再解码，原文件不变。原文件移动后仍可从副本继续任务。

## 分离与识别

| 选项 | 用途 |
| --- | --- |
| 模型双轨 | 两级分离，分别识别主唱、和声 |
| 外部分轨导入 | 使用完整主唱、和声、伴奏 WAV |
| 合并人声单轨 | 只提取合并人声 |
| 中文 / 日语 | 歌词处理语言 |
| 识别歌词并写入 MIDI | 关闭时只提取音符 |
| 精细提取 | 开启为 16 步 GAME，关闭为 8 步 |

外部分轨需来自同一输入，从 0 秒导出完整范围，保留开头静音。单声道和 48 kHz WAV 会转换；长度不符会拒绝。长度相同不能证明没有整体偏移。

参考歌词只填歌词，空格、标点和换行会去除。主唱与和声分别填写，和声留空时独立识别。不要粘贴作者名单或完整简介。

模型按音乐角色分离，不按歌姬身份识别。需试听串音、漏音及错字；更多步数不能保证更高准确率。

## 运行与恢复

“开始处理”新建任务；“继续上次任务”沿用保存参数；“重做上次分离 / MIDI”按当前选项新建任务并复用输入；“取消”结束处理树，模型最多等待 30 秒后强制停止；“打开结果目录”查看任务。

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
| `midi/需要核对的音符.csv` | 按秒记录的核对提示，不自动改音高 |
| `original-cover.*` | BV 输入的本家封面 |
| `status.json` 与日志 | 步骤和诊断 |

MIDI 以 120 BPM 表示实际时间，关闭量化；120 不是检测出的歌曲速度。修改工程 BPM 时应保持秒数位置。调音与最终音视频对齐由人完成。

## 发布准备

1. 在“发布准备 / 上传”选择来源任务或读取 BV 资料。
2. 选择同时含视频、音频的成品。
3. 填写翻唱歌姬、调音署名，套用 `【歌姬翻唱】歌名` 或自行编辑。
4. 核对每个平台的简介，来源署名是可编辑草稿。
5. 获取本家封面，或主动选择截图、手选图片。本地音乐需手选图片并补全来源与 STAFF，或另行读取 BV。
6. 生成发布包，4:3、16:9 封面必要时补边，保留整图。
7. 在各平台专用浏览器登录，完成后关闭登录窗口。
8. 启动上传填写；程序等待上传完成，填写文字和封面，保留浏览器。
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
