# 更新兼容性

[English](UpdateProtocol.en.md) | [简体中文](UpdateProtocol.zh-CN.md) | [验证说明](Verification.zh-CN.md) | [README](../README.zh-CN.md)

本文记录开发源码中的更新与命名兼容规则，不表示新版本或全部所述附件已经发布。

## 规范名称

`VWA.exe` 是规范应用启动器，`VWA-Updater.exe` 是规范的一次性兼容更新工具。新的完整便携包只提供 `VWA.exe`，不包含 `BVWA.exe` 启动器。

更新符合条件且已有旧启动器的安装时，更新器可以保留隐藏的 `BVWA.exe` 快捷入口别名。该别名使用与 `VWA.exe` 相同的启动器文件内容，使已有快捷方式继续打开升级后的应用。它不会加入新的便携包，也不是第二个应用。

VWA 是产品和包名变更，远程仓库及其 Release 地址仍为 `Transilent/Better-Vocaloid-Workflow-Assistant`。已发布客户端读取的更新标识继续由兼容层提供。

## 便携包与元数据

当前 VWA 包由发行目录中的 `portable_downloads.full` 与 `portable_downloads.jpn` 标识，分别对应 `VWA-Windows-x64.zip` 和 `VWA-Windows-x64-JPN.zip`。

分卷包使用各自的元数据：全量包使用 `portable-metadata-full.json`，JPN 包使用 `portable-metadata-jpn.json`。请使用该包附带的合并工具，核对与包型相符的分卷和元数据。

顶层 `release-assets.json` 的 `archive` 与 `parts` 字段保留为 BVWA 根目录旧版兼容全量包的元数据，固定的 `tools/Install-Runtime.ps1` 路径仍读取该兼容入口。请勿使用这些字段识别当前 VWA 全量包或 JPN 包分卷。

## 更新清单与状态

`release-assets.json` 继续用已安装 `dependencies/manifest.json` 的 SHA-256 标识基础运行库。版本号不能单独代表运行库。`updates` 数组按来源运行库提供入口，并记录第一个引导包、运行库与程序增量、大小及 SHA-256。

准备暂存和实际安装前都会按大小与 SHA-256 核对复用文件。已安装版本会阻止旧计划降级或重复安装目标版本；只有匹配的 `upgrade_pending` 标记可作为同版本例外，让已校验的第一个引导包完成流程。

已发布客户端写入的私有状态格式仍可读取。兼容标识继续保留在安装客户端读取的位置，因此界面名称变更不要求清空设置、任务或登录信息。未变化的模型文件继续原地复用。

## 固定三文件第一跳与旧版别名

兼容第一跳固定包含且只包含三个文件：`app.py`、`version.json` 和 `tools/update_apply.py`。`version.json` 将该小包绑定到目标版本，替换后的 `app.py` 在新界面模块安装前启动迁移窗口。第一跳先校验自身，再准备匹配的运行库与程序增量，并在一次重启前完成其余事务。

规范引导包也可以通过可选的 BVWA 名称别名发布；别名文件与规范归档的字节、大小和 SHA-256 完全相同。别名仅供无法读取新文件名的旧客户端使用。旧版顶层固定更新字段继续使用 BVWA 名称：

| 已安装客户端 | 兼容入口 |
| --- | --- |
| 当前 VWA 客户端 | 从 `updates` 按运行库哈希选择路径 |
| 已发布 0.4.1 客户端 | `update`：`BVWA-App-Update.zip`，随后进入迁移窗口 |
| 已发布 0.4.0 客户端 | `program_update`：`BVWA-Program-Update.zip`，随后进入迁移窗口 |
| 0.3.0 / 0.3.1 | 官方发行提供时使用一次性 `VWA-Updater.exe` |

上述 BVWA 文件名是有意保留的旧协议名称，不是新安装或当前 VWA 包的启动器名称。

## 双重锁与安装安全

应用和兼容路径使用双重锁。启动或准备更新时会同时取得 `cache/BVWA.lock` 与 `cache/VWA.lock`；任一锁已被占用时，程序会释放已经取得的锁并停止操作。该互锁防止规范启动器与升级后快捷入口别名同时运行两个实例。

安装还会检查任务、发布浏览器、组件安装和下载的活动锁。文件先暂存并校验再替换，事务期间保留备份，替换失败时恢复原文件。更新器不会强制关闭用户正在运行的任务。

## 兼容更新工具

只从提供该工具的官方发行页下载 `VWA-Updater.exe`。关闭助手及其处理、上传窗口后，将工具放在已安装的 `VWA.exe` 旁运行，或按提示选择原应用目录。升级后的安装也可通过保留的 `BVWA.exe` 别名找到。

选择 GitHub 直连、GitProxy 加速或自动切换，检查版本并下载。文件全部校验后，点击“安装并启动助手”。工具不发布内容，也不进行定时后台检查。错误记录位于 `cache/updates/compatibility.log`，安装结果位于 `cache/updates/last-result.json`。

## 维护约束

1. 保持 `packaging/update-clients.json`、已发布客户端固定素材及其哈希不变。
2. 第一跳白名单固定为 `app.py`、`version.json` 和 `tools/update_apply.py`，不要向其中加入新界面模块。
3. 当前 VWA 包通过 `portable_downloads.full` 与 `portable_downloads.jpn` 发布。分卷时发布对应的全量或 JPN 便携元数据及分卷，不得混用包型。
4. 顶层 `release-assets.json` 的 `archive` 与 `parts` 必须继续兼容固定的 BVWA 根目录 `Install-Runtime` 路径；该入口与当前 VWA 包发现相互独立。
5. 顶层旧版更新固定使用 `BVWA-App-Update.zip` 与 `BVWA-Program-Update.zip`。BVWA 名称的引导包别名必须与规范归档逐字节一致。
6. 验证新包只包含 `VWA.exe`，隐藏的 `BVWA.exe` 别名只在迁移已有安装时创建。
