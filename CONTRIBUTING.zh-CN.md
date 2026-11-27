# VWA 贡献规范

[English](CONTRIBUTING.md) · [开发者总指南](docs/DeveloperGuide.zh-CN.md) · [AMD 显卡适配指南](docs/AmdGpuAdaptation.zh-CN.md) · [反馈与日志说明](.github/SUPPORT.md)

欢迎使用中文、English 或日本語提交 Issue 和 PR。用户交流群：**490619616**。

## 提交 Issue

先搜索[已有问题](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues)，再从[新建反馈](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose)选择类型：

| 类型 | 应说明的信息 | 自动标签 |
| --- | --- | --- |
| 问题反馈 | 版本/提交、安装来源、Windows 版本、预期与实际表现、复现步骤和频率；仅补充相关设备、模型与选项 | `bug` |
| 功能建议 | 想完成的任务、现有流程的困难、期望行为；替代方案或参考项目可选 | `enhancement` |
| 使用求助 | 目标、所在页面与选项、看到的提示、已尝试的方法；不知道原因或版本也可以求助 | `question` |

一个 Issue 只描述一个问题，标题写清位置和症状，例如：

- `[Bug]: 简易混音拖动进度条后暂停`
- `[Feature]: 从仓库地址附加导入预设`
- `[Question]: 调音页如何选择演唱轨`

已有同类问题时补充复现信息，不重复开单；相关问题互相引用。无法稳定复现时可以说明“偶发”，不要为了填表猜测信息。标签仅用于分类，维护者按需标记重复、补充信息和优先级，不代表已承诺排期。建议不要求同时提供 PR。

**正文与附件公开可见。** 日志和截图不是必填。按[日志说明](.github/SUPPORT.md#logs-and-attachments--日志与附件)选择少量相关片段，删除账号、Cookie、令牌、用户名、完整路径、歌词和私人作品信息。不要上传完整配置、账号/浏览器目录、工程、歌曲/分轨、模型或声库。打开软件中的反馈入口只会打开浏览器，不会自动收集或上传。

## 提交 PR

1. 从当前 `main` 创建分支或 Fork，例如 `fix/mixdown-seek`、`docs/feedback-guide`。PR 的目标分支为 `main`。
2. 一个 PR 围绕一个目标，避免无关格式化。大规模架构、依赖、公开接口或数据格式改动先开 Issue 讨论；小修复和文档改动无需预先开单。
3. 填写默认 PR 模板：问题和结果、主要设计取舍、关联 Issue、验证、影响与迁移。仅引用上下文用 `Refs #123`；确实解决时才用 `Closes #123`。未完成的工作可先开 Draft。
4. 如实记录实际执行的命令或操作、结果、失败与未验证范围。界面修改附相关尺寸/缩放的截图；性能和音频结论写出素材、设备与测量条件。文档改动做内容和链接检查即可。离屏测试、真实设备和真人听感分别说明。
5. 根据审阅追加提交，最终 PR 说明应反映当前实现。工程、预设、设置、依赖、接口或格式有变化时，说明影响；破坏性变更写清迁移与恢复方法。

PR 标题和提交说明采用 `类型(范围): 简洁说明结果`，范围可省略，正文语言不限。常用类型：

| 类型 | 用途 |
| --- | --- |
| `feat` / `fix` | 功能 / 修复 |
| `refactor` / `perf` | 架构整理 / 性能 |
| `docs` / `test` | 文档 / 测试 |
| `build` / `ci` / `chore` | 构建 / CI / 维护 |

例如 `fix(mixdown): 修复定位后的播放状态`。破坏性变更加 `!`，例如 `feat(presets)!: 调整预设格式`，并在正文说明迁移。此约定帮助审阅，不设置自动标题拦截。

## 开发与验证

完整环境搭建、原生 DLL 编译与架构护栏详见[开发者总指南](docs/DeveloperGuide.zh-CN.md)；计划参与 AMD 显卡（A卡）适配的协作者请先阅读[AMD 显卡适配指南](docs/AmdGpuAdaptation.zh-CN.md)。

应用源码只改 `src/`，原生 C/C++ 音频库在 `src/native/`（由 `CMakeLists.txt` + `CMakePresets.json` 构建并用 CTest 验证），构建元数据在 `packaging/`。新文件按用途登记 `packaging/source-files.json`、`packaging/portable-files.json`；可执行回归脚本登记 `tests/run_tests.py`。运行 `tools/Prepare-Source.ps1` 更新根目录生成副本，不手改或提交这些副本。

提 PR 前请确保通过以下与 `.github/workflows/ci.yml` 对齐的本地检查：

- **布局与清单一一对齐**：`tools/Prepare-Source.ps1` + `python -B tools/verify_source_layout.py` + `tests/test_source_layout.py`
- **代码质量与架构护栏**：`ruff check src tools tests` + `tests/test_architecture_guards.py`（锁定 Tarjan 全图 **0 循环依赖**、新文件 **<= 800 行棘轮**、领域模块禁止导入 Qt、禁止裸 `except Exception: pass`）
- **多语言 100% 覆盖**：`python -B tools/extract_i18n.py --check --max-missing-en 0 --max-missing-ja 0`（新增中文文案必须在 `assets/i18n/additions.json` 中同步补齐英/日译文）
- **L0/L1 分层回归**：`tests/test_tier1_infra.py`、`tests/test_tier2_infra.py`、`tests/test_tier3_ux.py`，界面或交互改动另跑 `tests/run_tests.py`（用 `BVWA_TEST_SELECTION` 选择 `test_quick_desktop.py`、`test_user_experience_guards.py` 等受影响脚本）

使用已准备的开发运行库；GitHub 源码包不是完整安装包。`tools/Start-Diagnostics.bat` 用于诊断启动和写日志，不负责安装运行库。参考[便携版说明](docs/Portable.zh-CN.md)，通过项目 Python 执行 `tests/run_tests.py`。测试沿用隔离机制，不直接操作个人工程和账号目录。

按改动范围验证，不把全量推理、声库合成或完整发行构建当普通文档 PR 的门槛。验收请按 `unit_isolated`（隔离单测）、`real_material_dsp`（真实素材/硬件实测）、`quick_offscreen`（离屏界面）、`human_listening`（真人试听）四维分列汇报；自动化通过、启动成功或覆盖率都不能代替真人听感。生成副本、模型、浏览器、日志、本机记忆和账号资料不提交到 Git。

文档以英文为主，同时更新中文对应说明；涉及界面文本时补齐简体中文、英语、日语。面向用户的说明直接描述功能、操作和恢复方式，只有影响用户选择时才展示实现细节。

## 发行边界

PR 不等于发行授权。正式打包和发布前，完成自动化与界面检查，提供本地版本供用户测试；得到明确确认后再使用 `--user-test-confirmed` 构建发行包，该参数不能代替用户确认。版本、归档、哈希、实际包启动和代表性任务按[英文发行流程](CONTRIBUTING.md#release-process)核对；普通反馈和贡献规范修改不触发发版。
