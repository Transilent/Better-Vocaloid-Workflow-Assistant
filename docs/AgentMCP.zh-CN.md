# VWA 本机 Agent / MCP 接口

本文按当前本地工作树源码描述 45 个 stdio 工具，覆盖歌曲准备、MIDI 核对与修正、已有 F0/WAV 参考、工程导出与监督、SVP/VSQX 显式音素候选、GUI 共享片段草稿和持久一体化工作流。MCP 是可选运行环境；VWA 的 GUI 和推理 Python 不需要安装 SDK。

实际可用工具和代码身份以 `vwa_system_capabilities().data.tools`、`code_revision` 及实际调用回执为准。本文说明接口契约，不证明整曲准备、读音候选、GUI 草稿同步、导出或任何一次操作已经验收。文中的 JSON 是字段示意；`..._PLACEHOLDER` 必须替换成当前调用返回值。服务没有发布、账号导出或任意命令执行能力。

## 推荐的一体化流程

新安装默认使用一体化入口，把准备、核对、工程导出、可选音素修正和可选风格串成持久工作流。已有 MIDI 先由 `vwa_assets_import` 导入，再用 `score_artifact_id` 创建；Bilibili 的含 BV 号链接或允许目录内音乐可直接作为 `source`。`source` 与 `score_artifact_id` 必须二选一。

```json
{
  "source": "BV_PLACEHOLDER",
  "options": {"language": "zh", "voice_mode": "dual"},
  "brief": "用户提供的创作背景",
  "reference_lyrics": "已获准使用的参考歌词",
  "formats": ["svp", "vsqx"]
}
```

`formats` 省略时默认为 `["svp"]`。显式格式可包含不重复的 `svp`、`ustx`、`vsqx`；有非空 `brief` 时必须同时包含 `svp`。当前链接准备入口只适配 Bilibili。

工作流经以下阶段。`project_ready` 只表示可检查的导出工程已生成，不表示导出已经认可：

```text
created -> preparing -> review -> reviewing -> reviewed
        -> exporting -> project_ready -> styling（可选）-> ready

project_ready -- 显式音素修正 --> refining --> project_ready（新的监督报告）

project_ready -- export_review=needs_revision --> review
```

建议调用顺序：

1. 先读 `vwa_system_capabilities`，确认工具清单、`code_revision`、格式能力和 `shared_gui_drafts` 状态。
2. 用 `vwa_workflow_create` 创建；之后按 `vwa_workflow_get().next` 返回的工具、参数、`expected_revision` 和 `after_seconds` 推进。不要自行猜阶段 ID、报告 SHA 或修订。
3. 在 `review` 用 `vwa_workflow_review` 分页读取音符和歌词。`limit` 为 1–200，按 `next_offset` 读完；规则提示只是待审线索。
4. 用 `vwa_workflow_apply_review` 提交结构、歌词或音高/时值修改，或在确有理由时提交空修改表示保留。只有成功的核对操作被消费，且 MIDI SHA 仍一致，才进入 `reviewed`。
5. 导出后读取 `export_summary`。先核对候选的音符、歌词、实际秒数、转换损失和 SVP/VSQX 原生读音事实，再按当前 `report_sha256` 提交 `export_review`。
6. 若必须改写显式音素，在 `project_ready` 调用 `vwa_workflow_pronunciation_apply`。流程进入 `refining`，成功后回到 `project_ready`，生成新的导出监督报告并清除旧 `export_review`；必须重新监督。
7. 导出已认可后，可提交显式 `style_proposal`，或提交由共享草稿生成的 `style_plan_id`。两者互斥。没有风格需求且导出已认可时可用 `finish=true`。
8. `completed=true` 只表示可编辑工程已就绪。声库合成、试听、混音、视频装配和平台投稿仍是外部步骤。

所有修改都要携带当前工作流 `revision` 或当前文件 SHA。出现 `workflow_conflict`、`stale_plan`、`stale_artifact`、`stale_draft_plan`、`export_review_conflict`、`review_edit_conflict` 或 `ambiguous_note_mapping` 时，重新读取当前状态；不得猜 ID、复用过期报告或静默覆盖。

## 45 个公开工具

当前 `agent_control.TOOLS` 与 MCP 注册表合计 45 个名称。下表逐项列出；工具是否可调用仍以 `vwa_system_capabilities` 和实际业务回执为准。

| 工具 | 用途 |
|---|---|
| `vwa_system_capabilities` | 工具清单、能力、代码身份、外部交接与限制 |
| `vwa_system_health` | 业务 Python、关键依赖身份；不运行推理或合成 |
| `vwa_jobs_list` | 分页查看允许任务目录中的准备任务 |
| `vwa_jobs_get` | 读取准备任务真实阶段、子步骤和中断状态 |
| `vwa_artifacts_list` | 列出登记产物或读取指定任务产物索引 |
| `vwa_artifacts_inspect` | 读取产物信息；`verify=true` 启动 SHA 核验操作 |
| `vwa_operations_get` | 读取持久操作状态、结果、失败或中断 |
| `vwa_operations_cancel` | 请求协作取消；`cancel_requested` 不等于已取消 |
| `vwa_assets_import` | 导入允许目录中的 MIDI/SVP 独立快照 |
| `vwa_score_read` | 读取 MIDI 音符、时间与稳定 ID |
| `vwa_score_review` | 返回可定位到音符的规则核对提示 |
| `vwa_score_patch_plan` | 校验 MIDI 音高/绝对 tick 时值修改方案 |
| `vwa_score_patch_apply` | 执行已保存方案并生成独立 MIDI 候选 |
| `vwa_context_read` | 读取 SVP 歌词、音符、时间、轨道与预设证据 |
| `vwa_style_plan` | 校验调用方风格语义、整音符范围、理由和分段时间 |
| `vwa_plans_get` | 执行前重读不可变方案、diff 和理由 |
| `vwa_tuning_start` | 从风格方案生成独立 SVP 候选操作 |
| `vwa_tuning_result` | 读取风格候选操作及外部合成交接 |
| `vwa_preparation_plan` | 校验 Bilibili/本地音乐准备方案 |
| `vwa_preparation_create` | 从准备方案创建隔离准备任务 |
| `vwa_preparation_start` | 启动或续跑准备任务，保留真实阶段回执 |
| `vwa_lyrics_read` | 读取 MIDI 歌词、音符关系和未知项 |
| `vwa_lyrics_review` | 核对漏词、重复、错位和参考歌词覆盖 |
| `vwa_lyrics_patch_plan` | 规划音符级歌词替换或插入 |
| `vwa_lyrics_patch_apply` | 生成独立歌词修正 MIDI 与回执 |
| `vwa_structure_patch_plan` | 规划 MIDI 音符插入/删除及歌词归属策略 |
| `vwa_structure_patch_apply` | 生成独立结构修正 MIDI 并复核事件 |
| `vwa_reference_import` | 导入已有 F0 NPZ 或整数 PCM WAV 快照 |
| `vwa_reference_pitch_review` | 用显式时间原点条件性比较 MIDI 与已有 F0 |
| `vwa_reference_audio_read` | 读取 WAV 头、测量局部 PCM 或生成试听摘录 |
| `vwa_pronunciation_read` | 分页读取 MIDI/SVP/VSQX 原字和原生音素事实 |
| `vwa_pronunciation_patch_plan` | 规划 SVP/VSQX 明确音素替换；公开上限 512 项编辑 |
| `vwa_pronunciation_patch_apply` | 生成只改指定音素的独立工程候选 |
| `vwa_drafts_get` | 读取与指定 GUI 实例共用的片段草稿和字节修订 |
| `vwa_drafts_publish` | 把已校验风格方案按 CAS 发布到共享草稿 |
| `vwa_drafts_plan` | 从指定 revision 的 GUI 片段草稿生成绑定方案 |
| `vwa_exports_plan` | 规划 MIDI 到 SVP/USTX/VSQX 的工程导出 |
| `vwa_exports_start` | 执行导出、回读候选并保留独立审计 |
| `vwa_workflow_create` | 一体化创建链接/本地音乐或已有 MIDI 工作流 |
| `vwa_workflow_get` | 读取工作流修订、阶段、操作和下一步 |
| `vwa_workflow_review` | 分页读取工作流音符、歌词、参考证据和提示 |
| `vwa_workflow_references_set` | 绑定或清除本次工作流的 F0/WAV 参考与秒数 |
| `vwa_workflow_apply_review` | 提交结构、歌词、音高/时值修改或保留理由 |
| `vwa_workflow_pronunciation_apply` | 批量修正当前导出工程的明确音素并重新审计 |
| `vwa_workflow_advance` | 推进阶段、提交导出决策、使用风格方案或结束 |

成功响应为 `structured_content = {schema_version: 1, data: {...}}`。业务失败为 MCP `isError=true`，错误正文是可解析的 `{code, message, details?}` JSON。异步对象的 `id` 用作后续 `operation_id`。资源 `vwa://capabilities` 与 `vwa://workflow` 提供能力清单和简要指南。

## 安装、配置与共享草稿目录

Windows x64、CPython 3.12。SDK 固定为 `mcp==2.3.0`，传递依赖和此平台 wheel 摘要固定在 `packaging/requirements-mcp.lock.txt`。按项目方法生成根 Python 文件后运行：

```powershell
./tools/Prepare-Source.ps1
./tools/Setup-AgentMCP.ps1 `
  -BootstrapPython 'C:/Python312/python.exe' `
  -WorkerPython 'C:/VWA/dependencies/vocal2midi/python/python.exe' `
  -ApplicationRoot 'C:/VWA' `
  -ControlDirectory 'C:/VWA-Agent'
```

`BootstrapPython` 创建独立 SDK 环境；`WorkerPython` 是现有 VWA 业务 Python。复用已建环境时增加 `-RuntimeDirectory`。setup 不注册 MCP 宿主，不复制模型或账号，也不修改已有 `config.json`。

新安装首次生成配置时，`shared_draft_root` 默认为 `ApplicationRoot/cache/style-region-drafts`。打包示例 `packaging/agent-mcp.example.json` 也包含同一字段；这样指定 GUI 实例的片段草稿默认与 MCP 可见目录一致：

```json
{
  "schema": 1,
  "application_root": "C:/VWA/application",
  "worker_python": "C:/VWA/application/dependencies/vocal2midi/python/python.exe",
  "state_root": "C:/VWA-Agent/state",
  "allowed_asset_roots": ["C:/VWA-Agent/exchange"],
  "allowed_job_roots": ["C:/VWA-Agent/jobs"],
  "shared_draft_root": "C:/VWA/application/cache/style-region-drafts"
}
```

已有配置若缺少 `shared_draft_root` 或该值为 `null`，Control 仍兼容该旧配置，但不会自动启用或猜测一个目录；`vwa_system_capabilities().data.shared_gui_drafts` 为 `false`，三个草稿工具返回 `shared_drafts_disabled`。要启用时，操作者必须手工加入与目标 GUI 实例相同的绝对目录，并重新启动 MCP 服务。旧配置不会被 setup 自动改写。本机主助手接入时只启用独立试用根下的 cache，不能把原安装或其他版本的草稿目录擅自当成共享根。

`shared_draft_root` 必须是绝对路径，不能是文件、可写 junction/symlink 或含重解析点的祖先，并且必须与 MCP `state_root`、素材根、任务根分开。共享的是同一 GUI 安装实例的 `cache/style-region-drafts`，不是用户全局偏好。不同 `ApplicationRoot` 或不同版本的 GUI 可能看到不同草稿；跨实例前应显式核对 `drafts_get().path` 和 `input_sha256`。

操作者仍应审阅 `allowed_asset_roots`、`allowed_job_roots` 和 `state_root`。`state_root` 必须位于 GUI 安装和用户任务目录之外；允许根必须已存在。宿主应启动 `Start-VWA-MCP.cmd` 或等价的独立 Python `agent_mcp.py --config 绝对路径`；stdio stdout 只承载 SDK JSON-RPC。协议由固定官方 SDK 处理，业务长任务使用 operation ID，不要求宿主实现 Tasks 扩展。

## 读音事实与显式音素候选

### 先读事实，再决定是否写入

`vwa_pronunciation_read({artifact_id, limit, offset})` 分页读取当前登记工程，默认 `limit=100`，允许 1–500；公开工具的工程读取上限为 32 MiB。返回工程 `source_sha256`、`format`、`format_version`、时间基准、轨道、`note_count`、音符页、`phoneme_summary`、`write_supported` 和告警。分页状态在 `pagination` 中，使用 `pagination.next_offset` 继续读取；不能把首屏或导出的前 50 个音符当成整曲。 读取结果还明确返回 `inference_performed=false`、`g2p_performed=false`、`provider_access_performed=false` 和 `acoustic_pronunciation_verified=false`。

每个音符在源版本内使用稳定 `note_ref`，并提供 `track_index`/`track_name`、`group`/`group_name`、`pitch`、`onset`/`duration`、秒数、`lyric` 以及 `phonemes`、`phoneme_state`、`phoneme_override`、`phoneme_locked`、`phoneme_source` 和 `editable`。MIDI 的 `note_ref` 是现有 `note_id`。

`phoneme_state` 的当前值是：

- `unknown`：源工程没有可证明的音素事实，不能自动填一个读音；MIDI 未存音素就是未知。
- `derived_unverified`：存在音素文本，但来自未锁定的派生/未确认事实，不能等同于显式写入值。
- `explicit`：源工程明确写入；这仍只证明文件中写了什么，不证明读音正确，更不证明声库会按该写法发音。

公开能力声明读取 `midi`、`svp`、`vsqx`；USTX 在当前实现中明确不支持。MIDI/SVP 可通过 `vwa_assets_import` 形成快照；VSQX 可由工作流导出登记。SVP 使用非空 `library`、任一轨道的非空 `groups`，或无法可靠展开的引用会被明确拒绝，不能伪造“已展开”的音符；当前只展开受支持轨道的 `mainGroup.notes`。VSQX v3/v4 只在受支持的 UTF-8/BOM 结构边界内读取；DTD/ENTITY、非 UTF-8、重叠复调和无法保持语义的工程会拒绝。

导出流程遇到暂不支持展开的原生读音结构时，保留已独立核对的音乐工程，将读音证据标成 `unknown`，并要求监督调用方明确确认 `native_phonemes_unavailable`。这不代表读音通过；工程摘要不匹配仍会中止。

读取页中的 `tracks[].note_refs` 仅包含本页音符，`note_refs_total` 保留各轨总数。`timing.tempo_events` 最多展示 1024 项，`tempo_events_total` 与 `tempo_events_truncated` 明确是否省略；音符秒数计算仍使用完整时间线。

### 低层候选：计划与应用分离

`vwa_pronunciation_patch_plan({artifact_id, expected_sha256, edits})` 只规划 SVP/VSQX 的明确音素替换；公开工具的工程读取上限为 32 MiB。每项必须严格为：

```json
{"note_ref": "NOTE_REF_PLACEHOLDER", "phonemes": "PHONEMES_PLACEHOLDER"}
```

公开工具的编辑上限是 **512 项**，每个 `phonemes` 字符串上限是 **256 字符**。这不是无限批处理。未知字段、重复或未知 `note_ref`、过期来源 SHA、控制字符、过长值和不支持的工程结构会被拒绝。

- SVP：空字符串表示清除对应的显式音素 override。
- VSQX：拒绝空字符串；非空字符串设置对应音素，并把相应 phoneme lock 设为 `1`。方案会列明 lock 变化。

检查 `vwa_plans_get` 和 `vwa_pronunciation_patch_plan` 返回的 `diff`、`lock_changes`、`expected_candidate_sha256` 与校验标志后，才调用 `vwa_pronunciation_patch_apply({plan_id, idempotency_key})`。应用返回独立 `candidate_path`、`candidate_sha256`、`receipt_path`、`receipt_sha256`、`source_unchanged` 和明确音素差异；原工程保持不变。候选会重新解析并检查音符音高、时序、原字、速度、曲线及非音素 metadata；如出现计划篡改、来源变化、摘要碰撞或结构化差异，操作失败。用 `vwa_operations_get` 读取最终状态。

### 工作流内重审与失效规则

`vwa_workflow_pronunciation_apply` 只接受当前 `project_ready` 的现有导出工程，调用参数包含 `workflow_id`、当前 `expected_revision`、理由和 `changes`。每个 change 为：

```json
{
  "artifact_id": "ARTIFACT_ID_PLACEHOLDER",
  "expected_sha256": "CANDIDATE_SHA256_PLACEHOLDER",
  "edits": [
    {"note_ref": "NOTE_REF_PLACEHOLDER", "phonemes": "PHONEMES_PLACEHOLDER"}
  ]
}
```

公开上限为一次 **1–3 个工程**，每个 target **1–256 项编辑**，每个音素串 **256 字符**；目标必须无重复，且每个 SHA 等于当前候选。USTX 不能作为音素写入目标。

该调用启动本服务拥有的持久 `workflow_pronunciation` 操作，阶段进入 `refining`。成功消费后：

- 生成新的独立 SVP/VSQX 候选，原导出候选仍保留且摘要不变。
- 从源 MIDI 与候选重新核对音符、歌词、实际秒数和结构；只允许明确音素及 VSQX lock 的预期变化。
- 用新操作和当前候选生成新的 `export_summary`，旧 `export_review` 被清除，旧报告不能继续使用。
- 转换损失中包含 `explicit_phonemes_unverified`，即使写入和结构核对成功，也不代表声学读音正确。
- 回到 `project_ready` 后重新读取新报告，按新 `report_sha256` 再提交 `export_review`。

## GUI 共享片段草稿

### 修订与 CAS

`vwa_drafts_get({artifact_id})` 只接受当前登记 SVP，返回 `exists`、`input_sha256`、`revision`、`data`、`path`、`legacy`、`artifact_sha256` 和 `source_key_basis`。`revision` 是草稿文件**实际原始字节的 SHA-256**，不是草稿内自报的版本号；手动修改旧格式文件也会改变其 revision。`source_key_basis` 为 `candidate_sha256` 时使用候选 SHA；带原工程来源的导入输入为 `original_file_sha256`，用原文件 SHA 与 GUI 对齐，同时另行报告快照 `artifact_sha256`。

`vwa_drafts_publish` 只能发布当前代码版本的已校验 `style` plan，并会重新校验计划与当前 SVP。`expected_revision=null` 只用于目标尚不存在的新建；更新必须提交刚读到的准确 revision。写入使用跨进程锁、严格 compare-and-swap 和原子替换。已有同步元数据时，相同 payload 且期望仍匹配可复用当前 revision；旧格式首次写入会补上 `_sync`。

CAS 不匹配返回 `draft_conflict`，详情包含 `expected_revision`、`current_revision` 和 `recovery_path`。主草稿文件保持原样，恢复文件保存当前数据、待写数据和双方 revision。调用方应重新读取并明确决定；不要只把 expected 改成 current 后盲重试，也不要把恢复文件静默当作已合并结果。

### 从 GUI 草稿回到工作流

`vwa_drafts_plan({artifact_id, expected_revision, brief})` 读取指定精确 revision，要求草稿处于 `mode=regions`，有非空片段和目标轨道，并由完整 SVP 音符重新校验每个片段。片段边缘落在音符内部会报 `incomplete_note_boundary`；缺轨道、空草稿或旧 revision 分别明确失败。生成的保存方案带 `shared_draft` 绑定，至少含 `input_sha256` 和 `revision`。

推荐闭环：

```text
GUI/API drafts_get
  -> drafts_plan(expected_revision)
  -> plans_get 审阅
  -> workflow_advance(style_plan_id=...)
  -> start_plan 启动前重验草稿 revision
  -> styling -> ready
```

`vwa_workflow_advance` 的 `style_plan_id` 与 `style_proposal` 互斥。`style_plan_id` 必须是当前版本的 `style` plan，且 `plan.artifact_id` 必须精确等于工作流的 `flow.project_artifact_id`，否则报 `wrong_style_source`。启动操作时会再次调用草稿绑定检查；草稿在计划和启动之间发生任何字节修订，会报 `stale_draft_plan`，应重新 `drafts_get` 和 `drafts_plan`。这条路径把 GUI 草稿、MCP 风格方案和当前工作流串成一个闭环，不再需要另起孤立调音结果。

没有工作流时，仍可把该草稿方案交给 `vwa_tuning_start`；启动前同样会重验绑定。若只是读取方案而不执行，`vwa_plans_get` 不生成候选。

### GUI 外部更新与冲突

GUI 会周期性或显式读取共享 revision。发现其他 writer 更新时只提示“共享草稿已更新；可载入最新草稿”，不会在用户拖动、输入或编辑片段时静默替换当前内容。

- “载入最新草稿”显式采用共享版本，并把撤销记录重置到该版本。
- “以当前保存新修订”是显式覆盖意图；若有冲突，先确保恢复文件已保留共享版本和当前版本，然后再做一次 CAS。保存期间若又发生竞争，继续保留当前 UI 与恢复记录并报告冲突。
- 正常没有冲突时，原有自动暂存体验保持不变。旧 `schema_version=1` 扁平草稿仍可恢复；`legacy=true` 仅说明文件尚无同步元数据。
- GUI 本地从未保存且外部先创建草稿的竞争，也应保留双方，不能覆盖后声称已同步。

第一版可协同使用的是**按输入工程隔离的时间片段**。草稿文件为兼容 GUI 仍会保存模式、选区、目标轨道、当前/下一段工具设置和四阶段细分参数等状态，但这些字段不等于共享全局偏好；MCP 草稿规划只接受 `mode=regions` 的完整时间片段。旧 GUI 全局偏好不会被 MCP 自动改写。多个 GUI 窗口、不同版本或不同 `ApplicationRoot` 不保证共用同一集合，必须按实际 `shared_draft_root` 判断。

草稿文件上限 1 MiB，片段上限 128 个；数值必须有限，轨道必须是不重复的非负整数。JSON 不得含 NaN/Infinity，路径不得通过链接或重解析点逃逸。

## 风格区域与四阶段细分

`vwa_style_plan` 接受调用方给出的 `track_indices`、`brief` 和 `regions`。每个 `StyleRegion` 包含秒数 `start`/`end`、`preset`（`none/gentle/sad/bold`）、`strength`、`style_pitch`、理由和可选证据引用；唱腔层可选 `none/chinese_opera` 及独立强度和音高开关。

`StyleRegion.detail` 是可选的四阶段强度对象，新增但保持 GUI 现有细分参数：

```json
{
  "detail": {
    "onset": 1.0,
    "sustain": 1.0,
    "connection": 1.0,
    "release": 1.0
  }
}
```

`onset`、`sustain`、`connection`、`release` 各自可在 `0..1`，省略或整体为 `null` 时按 `1.0` 处理；对象中也可只给出部分阶段。它们是对应阶段乘数，与整体 `strength` 分开，不改变调用方语义理由。GUI 的“起音/持续/衔接/收尾”百分比会原样进入草稿、草稿生成的风格方案和最终引擎选项；MCP 可读可写同一字段。数值效果仍需实际工程与试听判断，不能据参数值声称听感达标。

实际风格规划仍要求片段不重叠、目标轨道可用于风格、片段在项目秒时间轴上且能对齐完整音符。`StyleProposal` 模型可承载较多行，但当前工作室实际限制最多 128 个片段，应按 128 处理。`none` 风格只有在唱腔不为 `none` 时可用。服务不调用语义模型；`brief`、理由和证据由调用方提供。

## MIDI、参考与导出

### MIDI 编辑

音高/时值从 `vwa_score_read` 当前版本生成方案。每项 `ScoreEdit` 可按需包含 `pitch`、`onset_tick`、`duration_tick`；时间使用绝对 MIDI tick，不是秒。检查 diff 后执行方案，候选不覆盖源。

结构工具只写标准 MIDI，当前不提供 SVP/VSQX 的音符插入、删除或歌词结构改写。删除必须指定 `lyric_policy=preserve|delete_owned`；插入必须给出轨道、通道、音高、tick 时值、力度和可选歌词。归属不唯一、同形音符歧义、复调或重叠冲突会拒绝。

工作流 `vwa_workflow_apply_review` 的顺序固定为：

```text
structure -> lyrics -> pitch/time
```

每生成新 MIDI 后，未变音符按轨道、通道、音高、起音和时长重映射稳定 ID。删除目标同时出现在其他 edit、或同形音符不能唯一匹配时，分别报 `review_edit_conflict`、`ambiguous_note_mapping`。

### F0 与 PCM WAV

`vwa_reference_import` 只导入允许目录中的既有文件，不执行音高提取、转写、推理或合成。F0 NPZ 必须是 schema 1，含 `midi_pitch.npy`、`voiced_mask.npy`、`time_step.npy` 和 `schema.npy`；导入上限 32 MiB。PCM WAV 只接受未压缩 RIFF/WAVE PCM 整数采样，导入上限 256 MiB。

`vwa_reference_pitch_review` 通过显式 `time_origin` 把 F0 第 0 帧放到 MIDI 秒时间轴，再按 tempo map 使用音符半开区间 `[onset, end)`。单路 F0 不能可靠拆和弦或复调；重叠显示 `ambiguous_polyphonic_overlap`，`automatic_patch=false`。它是条件性线索，不是真值。

`vwa_reference_audio_read` 未给范围时只读 WAV 头。指定 `start`/`end` 用 WAV 秒数，单次最多 30 秒，边界按 PCM 帧向外对齐，返回信号测量而 `semantic_interpretation=null`。`make_excerpt=true` 必须同时给不超过 30 秒的范围，生成逐帧复制的 PCM 摘录，不重采样、不改音高、不覆盖源。

工作流可在 `review` 或 `reviewed` 用 `vwa_workflow_references_set` 绑定参考；任何新增或改变都会把阶段退回 `review`，使旧核对失效。`time_origin` 与 `audio_time_origin` 都必须由调用方给出依据，服务不会自动发现对齐。

### 导出监督与读音预览

真实导出操作完成后，一体化工作流生成 `export_summary`。`summary.candidates[]` 中每个 SVP/VSQX 候选默认带有 `pronunciation` 原生读音预览，最多前 50 个 note；该预览的报告 `source_sha256` 必须等于候选 SHA。空读取、旧 fixture 或旧操作没有该证据时明确为 `unknown`，原因可包含 `native_phonemes_not_read`，不能把缺失当成“无音素”或“读音通过”。

前 50 个 note 只供导出监督快速检查。后续必须用 `vwa_pronunciation_read(artifact_id, limit, offset)` 按 `next_offset` 读取完整事实。`refining` 产生的新工程组也应带同样的候选绑定预览；缺少预览时仍明确 `unknown`。

`export_summary` 的关键字段：

| 字段 | 含义 |
|---|---|
| `report_sha256` | 当前不可变监督报告 SHA；决策必须携带 |
| `source_sha256` | 本次源 MIDI SHA |
| `candidates[]` | artifact、格式、候选 SHA、音符/歌词计数、tick/tempo/拍号核验 |
| `candidates[].pronunciation` | 绑定候选 SHA 的 SVP/VSQX 前 50 note 原生读音事实或 `unknown` |
| `blockers` / `can_accept` | 硬差异与是否仍可接受 |
| `required_loss_codes` | 本次必须逐项认可的转换损失/未知项代码 |
| `warnings` / `information` | 需认可告警与仅信息项 |
| `independent_audit` | 独立回读候选的音符/歌词/实际秒数比较 |

`export_review` 必须按当前 `report_sha256` 提交。`accept` 只有在无 blocker、`accepted_loss_codes` 恰好覆盖当前 `required_loss_codes`、源 MIDI 和全部候选 SHA 仍一致时成立。硬差异使用 `needs_revision`，工作流退回 `review` 并保留旧摘要作历史；不会自动修改 MIDI。`finish=true` 不能跳过未接受的报告。

```json
{
  "workflow_id": "WORKFLOW_ID_PLACEHOLDER",
  "expected_revision": 7,
  "export_review": {
    "report_sha256": "REPORT_SHA256_PLACEHOLDER",
    "decision": "accept",
    "rationale": "已核对候选音符、歌词、实际秒数、读音写入事实和逐项损失。",
    "accepted_loss_codes": ["LOSS_CODE_PLACEHOLDER"]
  }
}
```

接受记录 `caller_reviewed=true`、`acoustic_validation=false`。它不证明声库听感、发音语义或主观质量。候选格式转换、读音预览或结构核验任一改变后，必须重读报告。

低层 `vwa_exports_start` 没有替工作流建立 `export_review` 门槛；其结果仍需检查 `independent_audit` 和候选摘要。不要因为低层操作成功就直接 `finish=true`。

## tick、秒数与状态

| 时间或位置 | 单位与依据 |
|---|---|
| `onset_tick`, `duration_tick`, `ticks_per_beat` | MIDI 绝对 tick / PPQN |
| SVP 风格片段 `start`, `end` | SVP 项目秒数，已计入 tempo/轨道偏移 |
| F0 `time_step_seconds`, `time_origin` | 帧步长与 F0 第 0 帧在 MIDI 的秒位置 |
| WAV `start`, `end` | PCM/WAV 秒数；分析/摘录最多 30 秒 |
| 导出独立审计 timing | 源与候选各自 tempo map 下的实际秒数 |

`tick_roundtrip_verified=true` 只证明 tick 往返，不能证明实际播放秒数一致。独立审计分别用双方 tempo map 转换音符 onset/end，在基础 2 ms 容差及 MIDI→480 PPQN 量化容差内比较；超出产生 `timing_seconds_mismatch` 硬 blocker。有效时间线相同但速度表结构重排可以是需认可的损失，`tempo_content_mismatch` 是硬差异。

操作状态为 `queued`、`running`、`succeeded`、`failed`、`cancelled`、`interrupted`。同一 action 与 `idempotency_key` 对相同输入复用原操作；同键换输入报 `idempotency_conflict`。失败/中断先读原因，再以新键重试。工作流自身的 `retry=true` 仍属于已记录工作流操作，按其 `next` 使用。

取消只作用于本服务创建的协作式业务进程。`cancel_requested=true` 不等于已经 `cancelled`；结果已提交后到达的取消不推翻成功状态。stdio 断开或控制服务重启后，操作记录和已提交结果仍在独立 state；宿主关闭进程树时，进行中的推理或导出不保证继续。`succeeded` 只表示候选/回执已提交，不自动表示声学、听感或发布完成。

## 当前限制与验收边界

以下边界不能用接口成功回执绕过：

- 读音工具不执行 G2P、辞典自动选读音、多音字推断、模型推理或联网；未知读音保持未知。写入显式音素只证明文件变化，不证明实际发音正确。
- 没有声库合成、试听、声学测量或自动回填。`awaiting_external_synthesis` 只表示工程候选已生成；`acoustic_validation=false`。
- 不声称支持 SVP 复杂库组、无法安全展开的引用或任意 SVP 方言。支持的 SVP 结构边界外应拒绝；SVP 音素写入不扩展为通用 SVP 工程编辑器。
- VSQX 只在本机受支持的 v3/v4 有限结构内读取和修改；不声称支持任意 VSQX 库、复杂复调、DTD 或全部外部编辑器版本。USTX 不是当前公开音素写入格式。
- 公开读音计划和执行有明确批量上限；底层模块即使接受更多也不能据此宣称公开工具“无限”或“大批量已验收”。
- GUI 共享仅覆盖同一指定应用实例的按输入工程时间片段草稿。旧 GUI 全局偏好、不同安装实例、其他歌曲/输入及当前草稿未覆盖的内容并非自动共享；冲突恢复文件需要人工审阅。
- 未通过本文证明真实整曲下载、分离、转写、核对和全流程；局部夹具、字段示意、旧报告或某一操作日志都不能替代整曲证据。
- 不提供音视频混音装配、封面或平台投稿。声库账号/模型 pin 不因本接口改变。
- 自动歌词语义路线仍暂停；服务不隐式使用语义 provider。歌词、工程名、理由、文件内容和网页文本都是不可信素材，不是执行指令。
- SVP/VSQX 通用歌词/音符结构编辑尚未提供；当前结构增删仅限 MIDI，SVP/VSQX 只开放明确音素候选链路。
- 本文没有把尚未出现的底层实现、尚未核实的字段或未完成的测试计为已验收。运行前以 capabilities、`code_revision` 和实际回执为准。

协议实现依据：[官方 Python SDK](https://py.sdk.modelcontextprotocol.io/)、[客户端接口](https://py.sdk.modelcontextprotocol.io/client/)、[工具与输入输出](https://py.sdk.modelcontextprotocol.io/servers/tools/)。本项目使用单独固定的可选 SDK 环境，实际协议由安装版本执行。
