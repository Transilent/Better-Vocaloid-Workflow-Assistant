# 统一预设格式与本机库

[English](PresetLibrary.en.md) | [使用指南](UserGuide.zh-CN.md)

开发版的“设置与更新 → 本机预设库”管理准备流程、混音、调音方案和风格配方，提供文件预览与导入、复制另存、收藏、本机别名、删除和导出。现有的 6 个混音预设和 4 个准备流程为只读条目，先复制即可得到自己的版本。

当前阶段只管理数据。混音台和调音页尚未接入“保存当前参数 / 应用”入口，导入不会改变工程或声音。风格配方可保存与分享，尚不执行；没有在线目录、自动下载或 GitHub 账号连接。

## 使用和分享

1. 选中条目查看 JSON；复制内置条目，或在预览并确认后导入 `.vwa-preset.json` 文件。
2. 重命名仅修改本机显示别名，收藏仅修改库属性；两者不会改动原始文件、内容摘要或共享包中的名称。
3. 导出独立 JSON 文件，可自行提交 GitHub 或通过其他平台分享。接收方下载到本机后确认导入。目标文件已存在时拒绝覆盖。
4. 修改实际参数可从示例或导出文件制作新文件，使用新版本号。同一 ID、同一版本的不同内容会被拒绝；应用内参数编辑器留待后续。

示例：[准备流程](examples/presets/preparation.vwa-preset.json)、[混音](examples/presets/mixdown.vwa-preset.json)、[调音方案](examples/presets/tuning.vwa-preset.json)、[风格配方](examples/presets/style_recipe.vwa-preset.json)。它们通过格式校验，不代表执行入口已开放。直接导出的内置条目保留 `builtin.*` 身份，用于查看和归档；对外分享可导入的变体，请先复制另存。

## v1 文件规范

UTF-8 JSON，允许 BOM，上限 256 KiB，嵌套深度最多 16。未知字段、重复键、NaN/Infinity、非法文本、脚本、工程路径和设备绑定不接受。元数据仅为文本，不作为链接、路径或命令执行。

顶层必需字段为 `format: "vwa.preset"`、整数 `format_version: 1`、`id`、`type`、`version`、`metadata`、`requires`、`binding_slots`、`payload`。ID 使用小写 ASCII 命名空间，例如 `user.example.clear_vocal`，最长 128 字符，`builtin.*` 为内部保留。版本为完整 SemVer，例如 `0.1.0`、`1.0.0-rc.1`。

| 类型 | 约束与含义 |
| --- | --- |
| `preparation` | 受支持的流程图与明确列出的 16 项可移植选项。外部分轨只声明输入槽；不保存文件名、歌词、硬件和线程设置。 |
| `mixdown` | 按高通、低架、钟形、高架排序的四段 EQ，独立启停与保留值、压缩、混响、可选声像模板。总输出宽度必须在 `apply_scope` 明确声明，因为可能影响伴奏。 |
| `tuning` | 明确的表现来源、风格、技法、强度及密度。声明目标轨、参考音频、校准方案或保留工程的必需槽，不携带真实工程绑定。 |
| `style_recipe` | `style` / `technique` 角色，音符相位 0–1 上的声明式加法曲线。仅响度、张力、气声、实声四通道，每通道 2–64 点，从 0 到 1 严格递增。响度增量 ±12，其他通道 ±1；v1 只允许关闭的音高配方。 |

`requires` 固定预设 API 和对应引擎契约；能力名称仅为声明。外部风格/技法引用使用精确的 `id + version + sha256`，并与依赖清单完全一致。库可保管未解析依赖，不会自动下载、执行或声称已可应用。后续适配器还须核对能力、引用类型、角色、摘要与工程输入，提供改变预览。

[JSON Schema](../packaging/preset.schema.json) 提供结构校验，以 `preset_documents.validate_document` 完整校验为准。重复键、字节/深度预算、严格整数、非空白文本，以及流程图、槽位、宽度与依赖一致性等业务规则还会在运行时检查。修改受支持字段时须同步 Schema、示例和验证。

## 存储与恢复

默认目录为应用下的 `user-presets/`，与配置、任务和工程分离。只查看不创建目录；该目录不进入安装包，也不由程序更新覆盖。备份时复制整个目录。

每个版本保留原始字节与 SHA-256。相同 ID、版本、摘要的重复导入幂等；仅空格变化也算不同内容，须新版本。不同版本可共存。复制产生新 `user.*` ID 和 `0.1.0` 版本。删除仅移除当前索引条目，保留不可变文件和恢复历史，不立即回收空间。

写入使用跨进程侧锁和预期 revision；其他窗口修改后，本窗口旧操作提示冲突，刷新后重新确认。内容先落盘，索引最后原子替换。损坏索引或引用文件明确报错。“恢复上一索引”是显式操作，保留损坏现场，恢复最近一次可用的前一索引；不能保证挽回所有历史或损坏内容。库、导入与导出路径不得经过符号链接或 Windows 重解析点，请使用普通本地目录。

## 命令行

在准备好源码布局的应用目录，使用现有 Python：

```powershell
python -B preset_cli.py list
python -B preset_cli.py example mixdown
python -B preset_cli.py validate .\my.vwa-preset.json
python -B preset_cli.py import .\my.vwa-preset.json
python -B preset_cli.py export user.example.clear_vocal 0.1.0 .\shared.vwa-preset.json
python -B preset_cli.py --root C:\VWA-Presets list
```

还支持 `schema`、`show`、`copy --name`、`rename --alias`、`favorite on|off`、`delete`、`recover`。输出 JSON，失败返回码 2；界面和 CLI 共用相同格式及事务规则。
