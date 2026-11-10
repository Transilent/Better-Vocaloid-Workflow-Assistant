# VWA feedback and support / 反馈与求助

Use [New issue](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose) and choose **Bug report**, **Feature request** or **Help**. Chinese, English and Japanese are welcome. Search [existing issues](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues) first; add evidence to a matching issue rather than opening duplicates.

从[新建反馈](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose)选择**问题反馈、功能建议或使用求助**。一个 Issue 描述一个问题，标题写出发生位置和具体症状，例如“[Bug]: 简易混音拖动进度条后暂停”。不确定原因或不知道版本也可以求助；不要为了填表猜测信息。

## What to include / 应提供的信息

- Bugs: VWA version or development commit, installation source, Windows version, expected/actual behavior and steps/frequency. Add only relevant hardware, audio-device, model or input-format details.
- Suggestions: the task you want to complete, where the current workflow falls short and desired behavior. An implementation design is optional.
- Help: the goal, page/options, messages shown and what you tried.

错误报告附版本/提交、安装来源、Windows 版本、预期与实际表现、复现步骤和频率。卡顿或音频问题请补充相关设备、素材时长、采样率、触发操作和等待时间。功能建议先描述使用场景；用法问题选“使用求助”即可。

The forms apply existing `bug`, `enhancement` or `question` labels. Maintainers may reclassify, link duplicates or request missing information; these labels do not promise a delivery date. Feature suggestions do not require a PR.

## Logs and attachments / 日志与附件

**Issues and uploaded attachments in this public repository are public.** Review both text and files before submitting. VWA does not collect or upload logs when you open the feedback page.

**公开仓库中的正文和附件都可被他人访问。打开反馈页不会自动收集或上传日志。** 日志不是必填；优先提交错误原文附近的少量内容。截图请遮挡账号、路径、歌词或作品信息。

If relevant, manually select a short excerpt from:

| File | When it helps |
| --- | --- |
| `launcher.log` in the VWA program folder | Startup failures |
| `diagnostics.log` in the VWA program folder | Output from `tools/Start-Diagnostics.bat`, if used |
| `pipeline.log` in the affected task folder | Preparation/processing failures; the task/results page can help locate it |

按问题选择相关片段：启动看程序目录的 `launcher.log`；使用诊断启动后看 `diagnostics.log`；准备任务失败看对应任务目录的 `pipeline.log`。不存在的日志不必补造，也无需提交整个目录。原始配置和 JSON 状态文件可能包含路径或作品信息，除非已明确筛选脱敏，否则不要附上。

Remove usernames, full paths, cookies, tokens, account information, lyrics and private project/media names. Do not upload full configuration, `cache/accounts`, browser profiles, projects, songs/stems, models or voicebank files. Prefer a small synthetic or otherwise shareable example. Ask in the issue before sending additional material if you cannot safely redact it; no private upload channel is provided by this setup.

不要上传账号目录、浏览器资料、完整配置、工程、歌曲/分轨、模型或声库。建议用可公开的小型示例复现；需要进一步材料时先在 Issue 讨论合适的范围。本轮没有提供私密日志上传渠道。截图/ZIP 可拖入表单的附件文字框，建议小于 20 MB；GitHub 的实际附件限制仍适用。

## Community

**QQ group / 用户交流群：490619616**

Use the group for discussion and quick guidance. Important bugs and suggestions should have an issue so their evidence, decisions and fixes remain traceable. Group messages/files are visible to other members; the same redaction guidance applies. This number is not an invitation URL or a promise of immediate support.

群内适合交流和简短答疑。需要持续跟踪的问题请保留 Issue 编号；群聊和群文件也不适合贴未检查的日志。没有 GitHub 账号时可先在群内描述症状，由维护者按需整理为可公开的问题。

## Contributing

Code and documentation changes use a focused branch and pull request to `main`. See [English contribution guide](../CONTRIBUTING.md) or [中文贡献规范](../CONTRIBUTING.zh-CN.md). Use the PR template to explain behavior, verification and any project/preset/interface migration.
