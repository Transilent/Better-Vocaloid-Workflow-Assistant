# VWA 本机 Agent / MCP 接口

当前提供 32 个 stdio 工具，覆盖歌曲准备/续跑、MIDI 音符与歌词核对和修正、SVP/USTX/VSQX 工程导出、显式语义风格候选，以及持久一体化工作流。MCP 是可选运行环境；VWA 的 GUI 和推理 Python 不需要安装 SDK。

## 推荐：从链接连续得到工程

用户贴入含 BV 号的 Bilibili 链接后，Agent 优先调用 `vwa_workflow_create`，填写 `source`、已知的 `options`、创作背景 `brief` 和有来源的 `reference_lyrics`。可只给 source，默认走中文、主唱/和声准备；支持的站点目前是 Bilibili，其他站点链接需另接适配器。已有 MIDI 则先导入，再用 `score_artifact_id` 创建，二者选一。

工作流返回 `id`、`revision`、`stage` 和 `next`，其中直接给出下一步工具、参数与查询间隔。Agent 按 next 连续调用即可，不需要用户逐步确认默认的可逆处理。实际阶段是：created → preparing → review → reviewing → reviewed → exporting → project_ready → styling（可选）→ ready。

- `vwa_workflow_get(workflow_id)` 默认返回简洁状态、真实子步骤进度和 next；`include_details=true` 可读完整回执。`completed=true` 只表示工程已就绪。
- `vwa_workflow_review(workflow_id, limit=100, offset=0)` 一次给出音符、歌词及参考文本核对资料；音符、歌词事件与提示共同分页，按 next_offset 读完。不要把规则提示直接当成已证实错误。
- `vwa_workflow_apply_review(workflow_id, expected_revision, rationale, note_edits, lyric_edits)` 提交修改或有理由的保留结论。可以一次修正歌词和音符，后台先处理歌词，再安全关联新的音符 ID。没有核对理由不能跳到导出。
- `vwa_workflow_advance(workflow_id, expected_revision, ...)` 消费已完成结果并进入下一阶段。创作背景非空时，读取导出 SVP 的 context，再把明确的 `style_proposal` 交给 advance；不需要风格时使用 `finish=true`。

版本冲突先重新 get，避免两个 Agent 静默覆盖。后端失败、宿主中断或成功回执缺少产物时，保留旧记录，按 next 提示检查后 retry。歌曲链接下载和声部/MIDI 提取会执行本机模型；计划阶段只报告依赖、网络和推理需求。示例测试使用小型自制谱面和隔离流水线夹具，不代表已验证某首真实整曲的转写质量。

## 安装与连接

Windows x64、CPython 3.12。SDK 固定为 `mcp==2.3.0`，传递依赖及此平台 wheel 摘要固定在 `packaging/requirements-mcp.lock.txt`。先按项目方法生成根 Python 文件，再运行：

```powershell
./tools/Prepare-Source.ps1
./tools/Setup-AgentMCP.ps1 `
  -BootstrapPython 'C:/Python312/python.exe' `
  -WorkerPython 'C:/VWA/dependencies/vocal2midi/python/python.exe' `
  -ApplicationRoot 'C:/VWA' `
  -ControlDirectory 'C:/VWA-Agent'
```

BootstrapPython 用于创建独立虚拟环境；WorkerPython 是已有 VWA Python。若复用已建 SDK 环境，增加 `-RuntimeDirectory`。setup 仅安装可选依赖、生成本机配置与入口，不注册宿主、不复制模型或账号。已存在的 `config.json` 会保留。示例路径需换成本机真实目录。

审阅 `C:/VWA-Agent/config.json` 的 `allowed_asset_roots`、`allowed_job_roots`。默认只授予新建的 exchange 和 jobs。需要读取已有任务时，由操作者配置该任务父目录；路径经过根目录约束和链接/重解析点检查。`state_root` 必须独立于应用和用户任务。把 `client-config.json` 中的 command/args/env 配置到支持 stdio 的 MCP 宿主；入口是独立 Python 运行 `agent_mcp.py --config 绝对路径`。

服务 stdout 只承载 SDK JSON-RPC，业务 stdout/stderr 写在独立操作/调用日志。`Start-VWA-MCP.cmd` 是 stdio 入口，适合由宿主启动。协议现代请求与传统 initialize 客户端均交给官方 SDK；业务长任务使用 operation ID 查询，不要求 Tasks 扩展。

## 已提供的 32 个工具

| 工具 | 用途 |
|---|---|
| `vwa_system_capabilities` | 真实能力、代码身份、外部交接与缺口 |
| `vwa_system_health` | 业务 Python、依赖版本，免推理检查 |
| `vwa_jobs_list` / `vwa_jobs_get` | 分页任务、实际阶段进度与中断状态 |
| `vwa_artifacts_list` / `vwa_artifacts_inspect` | 产物回执、可取消 SHA 核验 |
| `vwa_operations_get` / `vwa_operations_cancel` | 持久状态、结果、协作取消 |
| `vwa_assets_import` | 导入 MIDI/SVP 的独立输入快照 |
| `vwa_score_read` / `vwa_score_review` | 音符表、ID、规则提示与未知项 |
| `vwa_score_patch_plan` / `vwa_score_patch_apply` | MIDI 音高/时值方案、diff、独立候选 |
| `vwa_context_read` | SVP 歌词、音符、时间、轨道与预设证据 |
| `vwa_style_plan` | 检查调用方语义方案、整音符范围与理由 |
| `vwa_plans_get` | 执行前重读保存的方案 |
| `vwa_tuning_start` / `vwa_tuning_result` | 真实 SVP 候选与外部合成交接 |
| `vwa_preparation_plan` / `vwa_preparation_create` / `vwa_preparation_start` | 来源计划、独立准备任务、持锁取消与真实续跑 |
| `vwa_lyrics_read` / `vwa_lyrics_review` | 中日文歌词、音符关系、覆盖与参考差异 |
| `vwa_lyrics_patch_plan` / `vwa_lyrics_patch_apply` | 明确插入/替换歌词的独立 MIDI 候选 |
| `vwa_exports_plan` / `vwa_exports_start` | MIDI 到 SVP/USTX/VSQX，回读音符/歌词并记录转换损失 |
| `vwa_workflow_create` / `vwa_workflow_get` | 一体化入口、持久阶段、修订和下一步 |
| `vwa_workflow_review` / `vwa_workflow_apply_review` / `vwa_workflow_advance` | 监督核对、组合修正、连续推进 |

成功响应有 `structured_content = {schema_version: 1, data: {...}}`；业务失败为 MCP `isError=true`，错误文本包含 JSON 的 `code` 和 `message`。操作对象的 `id` 是后续 `operation_id`。资源 `vwa://capabilities` 和 `vwa://workflow` 分别提供能力与操作指南。

## 歌词与工程导出

歌词方案的每项 edits 为 `{note_id, lyric, operation: "replace" | "insert"}`。替换要求原来有唯一对应事件；插入要求没有对应歌词，也不存在同 onset 的归属竞争。无论修改哪种文字，音高、节奏、力度、通道、速度表和其他 meta 均需保持。中日文原字保留，标点、延音、一字多音、歌词缺失与读音未知分别表达；首版不推断发音，也不从网上自动抓歌词或调用语义 provider。

`vwa_exports_plan` 默认导出 SVP，可另选 ustx/vsqx；`vwa_exports_start` 在独立目录使用现有 LibreSVIP，实际回读音符、歌词与时序。转换格式会记录量化、归一化速度表及未映射的 MIDI 元数据，不把普通 controller/pitchwheel 伪报为编辑器自动化。VSQX 会恢复并复核中日文歌词。

导出运行库默认取 application_root 下 dependencies/export；开发者以独立源码为 application_root 时，可在本机配置补充 `export_runtime` 的明确绝对路径，引用已有只读运行库。转换缓存只写本服务候选目录。准备启动也只接受本服务创建并登记请求摘要的任务；已有任务可查询/导入谱面，任意 request.json 的 tools 不会成为执行授权。

## MIDI 核对与修正

1. `vwa_assets_import({path: 允许目录内的绝对路径})` 得到 `art_...` 与输入摘要。
2. `vwa_score_read({artifact_id})` 和 `vwa_score_review({artifact_id})` 读取音符、原始轨道、节奏及规则提示。提示仅是复核线索，需要结合音乐背景判断。
3. 调用 `vwa_score_patch_plan({artifact_id, expected_sha256, edits})`。每项 edits 为 `{note_id, pitch?, onset_tick?, duration_tick?}`；音高为 MIDI 0–127，时间单位为当前 MIDI 的绝对 tick，duration 必须正数。只改明确指定的字段。
4. 检查返回 diff，再调用 `vwa_score_patch_apply({plan_id, idempotency_key})`。查询 `vwa_operations_get({operation_id})`，从成功结果的 `artifacts` 获取新 MIDI 和摘要。

ID 绑定源版本和事件位置；源摘要过期、未知/重复 ID、非有限数、布尔值冒充整数、模糊音符配对和不支持的时序关系会被拒绝。MIDI 候选保留未修改的事件、轨道、通道、力度、tempo 与 meta 语义；重新序列化后的二进制排布可不同。SVP 的音符写入尚未提供。复杂 MIDI 的局限以 read/review/plan 的实际报告为准，拒绝时不自动降级覆盖原曲。

候选、方案和回执均独立保存。换回原始 artifact 即回退到原曲；选择性接受可从同一原曲提交较小的 edits。首版没有单独的候选合并工具。

## 语义背景与风格

`vwa_context_read({artifact_id, track_indices})` 返回可用证据、歌词和未知项。Agent 结合用户背景填写 proposal；本服务校验并执行显式方案，不隐式调用模型，也不恢复已暂停的自动歌词语义路线。

```json
{
  "track_indices": [0],
  "brief": "思念故乡，首句克制，字头略带戏腔。",
  "regions": [{
    "start": 0.1, "end": 0.3,
    "preset": "sad", "strength": 0.5, "style_pitch": true,
    "style_technique": "chinese_opera",
    "technique_strength": 0.4, "technique_pitch": true,
    "rationale": "按用户要求，为首音保留克制的悲伤并加入少量戏腔。",
    "evidence_refs": []
  }]
}
```

将 proposal 传给 `vwa_style_plan({artifact_id, proposal})`，审阅 `aligned_ranges`、`style_regions`、理由和证据，再 `vwa_tuning_start({plan_id, idempotency_key})`。`vwa_tuning_result` 返回真实引擎生成的候选、session 与 producer receipt。风格为 `none/gentle/sad/bold`，唱腔是独立层 `none/chinese_opera`；强度 0–1。`preset=none` 仅适合独立唱腔，情绪层的 style_pitch 应设 false。

时间为工程秒数，已计入 tempo 和轨道偏移。范围扩展到所选轨道上相交音符的完整边界，静音选区和扩展后重叠会拒绝。不同 region 共用全局 track_indices，不接受片段私自改变目标轨。首版对链接库组、裁剪/复杂组引用等无法可靠展开的轨道明确拒绝；不会把未支持的音符当成静音继续生成。

方案保存在服务独立仓库，生成仍复用现有不可变调音 session。尚未与 GUI 的自动暂存草稿双向同步；GUI 可另行打开生成的 SVP。两个 Agent 可分别保存方案，方案内容哈希及操作锁防止同一方案并发叠加。

## 结果、重试与取消

操作状态为 queued/running/succeeded/failed/cancelled/interrupted。相同 action 和 idempotency_key 对同一请求复用原操作；换了输入必须用新键，否则返回 idempotency_conflict。失败/中断回执保留；检查原因后，用新键继续，底层候选/session 会核验已有结果并复用。

取消只作用于本服务创建的业务进程，采用协作检查点；`cancel_requested=true` 不等于已 cancelled。结果提交完成后收到取消，成功状态仍成立。stdio 断开或控制服务重启后，操作记录和已提交结果仍在 state 中，可用同一配置继续查询。宿主可能在关闭 stdio 时终止其拥有的整个进程树，Windows 官方 SDK 客户端即如此；首版不保证进行中的计算跨宿主关闭继续运行，也不绕过宿主进程清理策略。记录仍运行但所属锁已释放时报告 interrupted，检查后以新重试键重试，原有独立 session/候选会经核验复用。查询不会制造估算的总进度。

`vwa_artifacts_list` 返回清单事实，`verification=not_checked` 不代表摘要已验证。大文件用 `vwa_artifacts_inspect(..., verify=true)` 返回可取消核验操作。未登记的外部引用、缺失文件、摘要失效分别报告。服务不调用任务索引迁移或重建函数。

输入快照支持最大 32 MiB MIDI/SVP。SVP 的音频相对引用在快照中改为经目录校验的绝对引用，并保留原始文件摘要及引用变更回执；不复制音频，不修改原曲。原歌词、工程名和语义理由均当作素材数据，不能充当安装、执行或上传指令。

## 后续阶段

P0 控制底座、P1 核心候选和歌词修正、P2 的准备/续跑与逐格式工程导出已提供，一体化入口能组织这些步骤。GUI 共享草稿、参考音高/音频证据的局部诊断、校准、外部合成回填、视频/封面和发布准备继续按既有核对方案推进。P3 的 SV 宿主合成与最终投稿需要专门适配并取得实际成功证据。

`awaiting_external_synthesis` 表示只有工程候选生成。首版未声库合成、试听、推断声库听感或提交平台发布。能力发现会明确返回尚未接入的环节。

协议实现依据：[官方 Python SDK](https://py.sdk.modelcontextprotocol.io/)、[客户端接口](https://py.sdk.modelcontextprotocol.io/client/)、[工具与输入输出](https://py.sdk.modelcontextprotocol.io/servers/tools/)。本项目使用单独固定的可选 SDK 环境，实际协议由安装版本执行。
