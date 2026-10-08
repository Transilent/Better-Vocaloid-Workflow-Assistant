# 验证与限制

[English](Verification.en.md) | [简体中文](Verification.zh-CN.md) | [README](../README.zh-CN.md)

## 可复现检查

在解压后的程序目录执行：

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B .\launch.py --check
& '.\dependencies\vocal2midi\python\python.exe' -B .\check_dependencies.py --full
& '.\dependencies\vocal2midi\python\python.exe' -B .\tests\run_tests.py
```

依次检查启动要求、全部固定依赖哈希和离屏回归。回归使用生成素材与本地测试页面，不发布内容、不要求账号。

| 测试组 | 范围 |
| --- | --- |
| 核心 | 阶段复用、取消、分轨转换、MIDI 校验 |
| 日语 MIDI | 短音节停顿、假名位置、音素保留、片段错误隔离、取消及完整双轨写入 |
| 界面 | 输入选择、任务状态、发布控件 |
| 本地音乐 | WAV/MP3/FLAC/M4A、副本、续跑、错误输入、不请求来源 API |
| 浏览器 | 登录会话、进程、上传状态 |
| 平台适配 | 本地页面文件、文字及封面控件 |
| 双封面 | 4:3 与 16:9 独立处理 |
| 启动器 | 唯一入口、依赖检查、CPU/GPU 设置 |
| 依赖安装 | 离线安装、本地 HTTP 分卷下载、哈希、中断重试和用户数据保留 |

## 发行验证

将完整 ZIP 解压到独立目录，逐个核对 CRC、大小、SHA-256，并与固定依赖清单交叉检查。使用解压后的 Python 检查启动并运行回归。分卷和 ZIP 哈希保存在 `release-assets.json`。

实际模型验证覆盖本地分离、歌词识别和双声部 MIDI。短素材测试不能证明任意歌曲的准确率。发行报告不包含账号、私人素材或本机安装路径。

## 限制

- 文件校验通过仍需在新电脑检查启动，硬件、驱动和安全软件可能不同。
- 分离可能串音，和声较弱时会漏音或空轨。
- 歌词和音高需要人工校正，参考歌词也不能保证正确。
- 本地网页测试验证适配逻辑，平台更新可能改变实际兼容性；新电脑需重新登录。
- 助手准备上传页面，不检查所有平台字段，也不自动发布。
