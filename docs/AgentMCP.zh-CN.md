# VWA 本机 Agent / MCP 接口

本文按当前本地工作树源码描述 38 个 stdio 工具。覆盖歌曲准备与续跑、MIDI 音符/歌词/结构核对与修正、已有 F0 和 PCM WAV 参考、SVP/USTX/VSQX 工程导出、导出后监督，以及持久一体化工作流。MCP 是可选运行环境；VWA 的 GUI 和推理 Python 不需要安装 SDK。

实际可用工具以 `vwa_system_capabilities().data.tools` 和其中的 `code_revision` 为准。本文更新只说明实现契约；不以文档本身证明整曲推理、声库合成、试听、GUI 草稿同步或任何一次实际导出的验收结果。文中的 JSON 均为字段示意；带 `..._PLACEHOLDER` 的 ID、示例 revision、SHA 和损失代码必须替换成当前调用返回的值，示例不表示对象存在或验收成功。

## 推荐：连续推进一体化工作流

含 BV 号的 Bilibili 链接或允许目录内的音乐可作为 `source`；当前链接适配器只支持 Bilibili，其他站点需另行适配。已有 MIDI 应先由 `vwa_assets_import` 导入，再以 `score_artifact_id` 创建。`source` 与 `score_artifact_id` 必须二选一。 使用已有 MIDI 时直接从 `review` 阶段开始，跳过准备阶段。可同时提供准备 `options`、创作背景 `brief` 和已获准使用的 `reference_lyrics`。`formats` 省略时默认 `["svp"]`；显式列表可含不重复的 `svp`、`ustx`、`vsqx`，非空 `brief` 要求同时导出 SVP。

```json
{
  "source": "BV_PLACEHOLDER",
  "options": {"language": "zh", "voice_mode": "dual"},
  "brief": "用户提供的创作背景",
  "formats": ["svp", "ustx", "vsqx"]
}
```

工作流阶段为：

```text
created -> preparing -> review -> reviewing -> reviewed
        -> exporting -> project_ready -> styling（可选）-> ready
```

`vwa_workflow_get` 返回 `id`、`revision`、`stage`、真实操作状态和 `next`。Agent 应按 `next.tool`、`next.arguments` 连续操作；运行中的默认查询间隔由 `next.after_seconds` 给出。`include_details=true` 可读取完整工作流记录。`completed=true` 只表示可编辑工程已就绪，不表示已声库合成、试听或投稿。

核对阶段的顺序如下：

1. `vwa_workflow_review` 同时返回音符、歌词和提示。`limit` 为 1–200，提示共同分页；按 `next_offset` 读完。规则提示仍需结合音乐语境判断。
2. `vwa_workflow_apply_review` 提交有理由的保留结论或明确修改。可同时提交结构、歌词和音高/时值修改；亦可提交空 edits 并说明为何保留。
3. 只有成功的核对操作被消费、且当前 MIDI 摘要仍与核对回执一致，才进入 `reviewed` 和导出。
4. 工程导出完成后进入 `project_ready`。一体化流程默认生成 `export_summary`，必须先提交 `export_review`；未认可的实际差异不能靠 `finish=true` 跳过。
5. 有创作背景时，可读取导出 SVP 的 `context` 后提交显式 `style_proposal`；没有风格需求时在导出监督已接受后使用 `finish=true`。

所有修改操作都携带或校验 current `revision`/source SHA。发生 `workflow_conflict`、`stale_note_id`、`ambiguous_note_mapping` 或 `review_edit_conflict` 时，重新读取当前状态；不得猜测另一个音符 ID 或静默覆盖。

## 已提供的 38 个工具

下表斜杠分隔的行包含多个工具，名称合计为 38 个。

| 工具 | 用途 |
|---|---|
| `vwa_system_capabilities` | 工具清单、能力、代码身份、外部交接与未实现缺口 |
| `vwa_system_health` | 业务 Python、关键依赖身份；不运行推理或合成 |
| `vwa_jobs_list` / `vwa_jobs_get` | 分页任务、真实阶段进度与中断状态 |
| `vwa_artifacts_list` / `vwa_artifacts_inspect` | 产物清单与回执、可选 SHA 核验操作 |
| `vwa_operations_get` / `vwa_operations_cancel` | 持久操作结果与协作取消 |
| `vwa_assets_import` | 导入 MIDI/SVP 独立输入快照 |
| `vwa_score_read` / `vwa_score_review` | MIDI 音符、稳定 ID、规则提示与未知项 |
| `vwa_score_patch_plan` / `vwa_score_patch_apply` | 音高/绝对 tick 时值方案及独立候选 |
| `vwa_structure_patch_plan` / `vwa_structure_patch_apply` | 音符插入/删除方案及独立 MIDI 候选 |
| `vwa_context_read` | 读取 SVP 歌词、音符、时间、轨道和预设证据 |
| `vwa_style_plan` | 校验调用方语义方案、整音符范围与理由 |
| `vwa_plans_get` | 执行前重读保存的方案 |
| `vwa_tuning_start` / `vwa_tuning_result` | 生成真实 SVP 风格候选并报告外部合成交接 |
| `vwa_preparation_plan` / `vwa_preparation_create` / `vwa_preparation_start` | 来源计划、独立准备任务、持锁续跑与真实阶段 |
| `vwa_lyrics_read` / `vwa_lyrics_review` | 中日文歌词、音符关系、覆盖和参考文本差异 |
| `vwa_lyrics_patch_plan` / `vwa_lyrics_patch_apply` | 明确替换/插入歌词并生成独立 MIDI |
| `vwa_reference_import` | 导入已有 schema-1 F0 NPZ 或整数 PCM WAV 快照 |
| `vwa_reference_pitch_review` | 以显式 `time_origin` 条件性比较 MIDI 与已有 F0 |
| `vwa_reference_audio_read` | 读取 WAV 头、测量不超过 30 秒的 PCM 窗口或创建摘录操作 |
| `vwa_exports_plan` / `vwa_exports_start` | MIDI 到 SVP/USTX/VSQX，并附加独立回读核对 |
| `vwa_workflow_create` / `vwa_workflow_get` | 一体化创建、持久阶段、修订和下一步 |
| `vwa_workflow_review` / `vwa_workflow_apply_review` / `vwa_workflow_advance` | 监督核对、组合修正、导出认可与连续推进 |
| `vwa_workflow_references_set` | 在核对阶段绑定或清除本次工作流的参考及对齐秒数 |

成功响应为 `structured_content = {schema_version: 1, data: {...}}`。业务失败为 MCP `isError=true`，错误文本包含 JSON 的 `code` 和 `message`。异步操作对象的 `id` 是后续 `operation_id`。资源 `vwa://capabilities` 与 `vwa://workflow` 分别提供能力清单和流程指南。

## 安装、连接与原安装保护

Windows x64、CPython 3.12。SDK 仍固定为 `mcp==2.3.0`，传递依赖和此平台 wheel 摘要固定在 `packaging/requirements-mcp.lock.txt`。先按项目方法生成根 Python 文件，再运行：

```powershell
./tools/Prepare-Source.ps1
./tools/Setup-AgentMCP.ps1 `
  -BootstrapPython 'C:/Python312/python.exe' `
  -WorkerPython 'C:/VWA/dependencies/vocal2midi/python/python.exe' `
  -ApplicationRoot 'C:/VWA' `
  -ControlDirectory 'C:/VWA-Agent'
```

`BootstrapPython` 创建独立 SDK 环境；`WorkerPython` 是现有 VWA Python 环境。复用已建 SDK 环境时增加 `-RuntimeDirectory`。setup 只安装可选依赖、生成本机配置与入口；不注册 MCP 宿主，不复制模型或账号，已存在的 `config.json` 保留。示例路径均须替换为本机真实路径。

操作者应审阅 `config.json` 的 `allowed_asset_roots`、`allowed_job_roots` 和 `state_root`。路径经过允许根约束及链接/重解析点检查；`state_root` 必须独立于 GUI 安装目录和用户任务目录。默认授权不把原安装、模型、账号或任务原数据变成可任意写入的服务状态。宿主应启动 `Start-VWA-MCP.cmd` 或等价的独立 Python `agent_mcp.py --config 绝对路径`；stdio stdout 只承载 SDK JSON-RPC。

协议由固定版本的官方 SDK 处理现代请求与传统 initialize 客户端。业务长任务使用 operation ID 查询，不要求宿主实现 Tasks 扩展。MCP 不提供任意命令执行、账号导出或平台发布。

## MIDI 音符、歌词与结构修改

### 音高与时值

`vwa_score_patch_plan({artifact_id, expected_sha256, edits})` 的每项 edits 为：

```json
{"note_id": "NOTE_ID_PLACEHOLDER", "pitch": 60, "onset_tick": 480, "duration_tick": 240}
```

`pitch`、`onset_tick` 和 `duration_tick` 均可按需省略；`onset_tick`/`duration_tick` 使用当前 MIDI 的绝对 tick，不是秒。检查方案 diff 后调用 `vwa_score_patch_apply({plan_id, idempotency_key})`，再查询 `vwa_operations_get` 的 `artifacts`。源摘要过期、未知或重复 ID、布尔值冒充整数、非有限数、模糊配对及无法安全保持的事件关系会被拒绝。

### 结构插入与删除

结构工具只写标准 MIDI，当前不写 SVP 音符。`vwa_structure_patch_plan` 接受 1–1024 项非空 edits；删除和插入可在同一方案中混用。候选生成后重新解析并核对轨道数、全部未删除事件、tempo、拍号、通道、力度和实际音符集合。

删除必须显式给出歌词归属策略：

```json
{"operation": "delete", "note_id": "NOTE_ID_PLACEHOLDER", "lyric_policy": "preserve"}
```

- `preserve` 保留相关歌词事件，可能形成孤立歌词，不会猜测并重新归属。
- `delete_owned` 只在音符拥有唯一歌词事件时删除它；归属不唯一时拒绝。
- 同一歌词事件不能被多个删除操作声明为独占；重复删除或音符配对歧义也会拒绝。

插入必须给出以下音符字段，歌词可显式提供，也可省略：

```json
{
  "operation": "insert",
  "track_index": 0,
  "channel": 0,
  "pitch": 60,
  "onset_tick": 960,
  "duration_tick": 480,
  "velocity": 96,
  "lyric": "啊"
}
```

省略 `lyric` 表示不创建、也不推断歌词；若邻域既有歌词会因此改变唯一归属，方案拒绝。提供歌词时，目标轨道同 onset 不能有另一音符，归属窗口也不能已有歌词。最终状态若产生同轨、同通道、同音高重叠，或既有歌词归属争议，均拒绝。

检查 diff 后调用 `vwa_structure_patch_apply({plan_id, idempotency_key})`。应用时会重读源文件、以同一计划重新生成候选，并要求 candidate SHA 与方案一致；源文件和候选都不会被覆盖。

### 一体化组合修改

`vwa_workflow_apply_review` 可一次接收 `structure_edits`、`lyric_edits`、`note_edits` 和核对理由。后台固定按以下顺序执行：

```text
structure -> lyrics -> pitch/time
```

每产生一个新 MIDI，后续编辑会按 `track_index + channel + pitch + onset_tick + duration_tick` 将未变音符重新关联到新的稳定 ID。删除目标若同时出现在歌词或音高编辑中，以 `review_edit_conflict` 拒绝；同形音符不能唯一关联时，以 `ambiguous_note_mapping` 拒绝。组合操作失败不会把未经核验的中间候选当作已核对结果。

## 已有 F0 与 PCM WAV 参考

`vwa_reference_import` 只把允许目录中的既有文件复制、验证为只读快照。它不执行音高提取、转写、推理、声库合成或网络 provider 调用。导入结果带有原始路径、摘要和元数据；参考文件来源仍是 `caller_supplied_unverified`。

### schema-1 F0 NPZ

F0 文件必须严格包含：

- `midi_pitch.npy`：一维 `float32` 或 `float64`；
- `voiced_mask.npy`：同形状布尔数组；
- `time_step.npy`：0.001–0.1 秒的数值标量；
- `schema.npy`：整数标量，值必须为 1。

接口导入文件的当前上限为 32 MiB。声帧内的 MIDI 音高必须有限且在 0–127；归档成员、解压量、CRC、压缩比和数组形状都会校验。数组本身不从读取接口返回。

### PCM WAV

只接受未压缩 RIFF/WAVE PCM（format tag 1），支持 8、16、24、32 位整数采样、1–32 声道和 1–384000 Hz 采样率。接口导入上限为 256 MiB。

`vwa_reference_audio_read` 未给 `start`/`end` 时只读取 WAV 头，不做逐帧分析。指定范围时：

- `start`/`end` 使用 WAV 秒数；只给一端时另一端取文件起点或终点，范围必须在文件内且 `end > start`；
- 分析窗口最多 30 秒，边界按 PCM 帧向外对齐；
- 返回峰值、RMS、静音帧比例等信号测量，返回 `semantic_interpretation: null`；
- PCM 分析不等于音高、歌词、情绪、听感或合成结果。

`make_excerpt=true` 必须同时给出不超过 30 秒的 `start` 和 `end`。该调用启动 `reference_excerpt` 操作，成功后产生逐帧复制的 PCM WAV 摘录；不重采样、不改音高、不覆盖源文件。用 `vwa_operations_get` 读取摘录产物。

### 显式绑定的时间原点

工作流在 `review` 或 `reviewed` 阶段通过 `vwa_workflow_references_set` 绑定参考。新增或改变参考会把阶段退回 `review`，使旧核对结论失效；两个参考 ID 都留空并在理由中说明时可清除参考。`rationale` 必填。

```json
{
  "workflow_id": "WORKFLOW_ID_PLACEHOLDER",
  "expected_revision": 4,
  "pitch_artifact_id": "ARTIFACT_ID_PLACEHOLDER",
  "audio_artifact_id": null,
  "track_indices": [0],
  "time_origin": 1.25,
  "audio_time_origin": 0.0,
  "rationale": "说明参考来源、对齐依据或清除理由"
}
```

- `track_indices=null` 表示全部轨道；显式数组必须非空、无重复并存在于 MIDI 中。直接调用 `vwa_reference_pitch_review` 时，分页 `limit` 为 1–500、`offset` 非负，结果按 `next_offset` 继续读取。
- `time_origin` 是 F0 第 0 帧在 MIDI 秒时间轴上的位置。F0 自身的帧时间相对第 0 帧；没有显式原点就不能把帧号当作歌曲时间。
- `audio_time_origin` 是 WAV 第 0 秒在 MIDI 秒时间轴上的对应位置，用于工作流按音符秒数建议局部摘录。它不会自动寻找对齐。
- 若同一 tick 存在冲突 tempo，秒数映射有歧义，参考音高复核会拒绝。
- 参考音高按 MIDI 实际 tempo map 转成秒后，以音符半开区间 `[onset, end)` 为范围，将边界按 F0 帧步长向上取整并裁剪到可用帧；不是直接按 MIDI tick 截取数组。
- 单路 F0 无法可靠拆分和弦或复调。重叠音符显示 `ambiguous_polyphonic_overlap`，不会伪造唯一归属。
- 音高偏移提示是条件性复核线索，`automatic_patch=false`，不会自动改写 MIDI。

F0 的 `reviewed`/`partial`/`unknown` 是当前测量覆盖状态，不是“参考为真”或“整曲已通过”。工作流复习给出 `available_references` 时，也仅表示准备任务目录中发现了候选 NPZ/WAV，并附带导入调用；`not_yet_verified` 不能当成已核验参考。

## 导出监督、tick 与真实秒数

### 一体化工作流的默认监督

`vwa_exports_start`/工作流内部导出仍使用现有 LibreSVIP 转换器，回读音符和歌词，并由独立审计重新读取源 MIDI 与每个候选工程。低层导出操作结果包含 `independent_audit`，但 `vwa_exports_start` 本身不建立工作流的 `export_summary`/`export_review` 门槛；直接使用低层工具时仍须检查该操作和独立审计。一体化工作流消费成功结果时进一步校验回执摘要、源摘要、已登记产物、独立审计的 `audit_sha256` 及其逐候选绑定，自动生成 `export_summary`；这些身份不一致时直接报 `export_report_mismatch`，不能进入接受。这是工作流默认的导出监督报告，不需要单独调用一个工具。

`export_summary` 的关键字段：

| 字段 | 含义 | Agent 应做什么 |
|---|---|---|
| `report_sha256` | 当前不可变监督报告的 SHA-256 | 后续决策必须携带此值 |
| `blockers` / `can_accept` | 音符、歌词、实际秒数、tempo、拍号、路径或解析等硬阻断（hardblock）；存在 blocker 时 `can_accept=false` | 使用 `needs_revision`，不能 `accept` |
| `required_loss_codes` | 本次必须逐项明确的转换损失、归一化或未知项代码 | `accept` 时完整、准确列出 |
| `warnings` | 需认可的报告告警 | 其代码进入 `required_loss_codes` |
| `information` | 如零计数且未激活的 controller 提示 | 不要求作为损失认可 |
| `candidates` | 格式、artifact、candidate SHA 和 tick/tempo/拍号核验摘要 | 与 `export_summary` 的当前版本一起核对 |
| `independent_audit` | 独立加载候选后的比较；`inference_performed=false` | 状态为 `unknown` 时不得声称为已验证 |

接受是调用方 Agent 的明确决定，输出会记录 `caller_reviewed=true`、`acoustic_validation=false`。它不证明声库听感、发音语义或主观质量。`required_loss_codes` 非空时，不能用空数组、通配或自由文本代替逐项代码认可；硬阻断也不能靠认可代码绕过。报告和候选 SHA 任一改变，都必须重新读取并核对。

### 按报告 SHA 提交决策

以下 JSON 只示字段结构；`WORKFLOW_ID_PLACEHOLDER`、`REPORT_SHA256_FROM_EXPORT_SUMMARY` 和 `LOSS_CODE_FROM_REQUIRED_LOSS_CODES` 均不可原样调用，也不代表实际验收。真实调用必须逐项填入当前 `vwa_workflow_get`/`export_summary` 的 ID、64 位小写 SHA 和完整损失代码。

```json
{
  "workflow_id": "WORKFLOW_ID_PLACEHOLDER",
  "expected_revision": 7,
  "export_review": {
    "report_sha256": "REPORT_SHA256_FROM_EXPORT_SUMMARY",
    "decision": "accept",
    "rationale": "已核对导出后的音符、歌词和实际秒数，并逐项说明损失。",
    "accepted_loss_codes": ["LOSS_CODE_FROM_REQUIRED_LOSS_CODES"]
  }
}
```

`accept` 同时满足以下条件才会被接受：

- `decision.decision` 为 `accept`；
- `report_sha256` 等于当前报告；
- 监督报告 `can_accept=true`，没有硬 blocker；
- `accepted_loss_codes` 恰好覆盖 `required_loss_codes`；
- 不重复、不夹带当前报告未要求的代码；
- 当前源 MIDI 和全部候选 SHA 仍与报告一致。

存在硬差异时，用同一 `report_sha256` 提交退回：

```json
{
  "workflow_id": "WORKFLOW_ID_PLACEHOLDER",
  "expected_revision": 7,
  "export_review": {
    "report_sha256": "REPORT_SHA256_FROM_EXPORT_SUMMARY",
    "decision": "needs_revision",
    "rationale": "说明哪个音符、歌词或实际秒数差异需要修正。",
    "accepted_loss_codes": []
  }
}
```

`needs_revision` 把工作流退到 `review` 阶段并保留 `previous_export_summary`，不会把旧监督报告当作新验收，也不自动修改 MIDI。随后应重新 `workflow_review`、`workflow_apply_review`，消费修正后再重新导出。已有风格需求也不能绕过这一步；`finish=true` 无法跳过未接受的导出报告。

### tick 不能替代真实秒数

| 时间或位置 | 单位与依据 | 用途 |
|---|---|---|
| `onset_tick`、`duration_tick`、`ticks_per_beat` | MIDI 绝对 tick / PPQN | 音高时值和结构增删的稳定编辑坐标 |
| SVP 风格区间 `start`、`end` | 编辑器工程秒数 | 已计入工程 tempo 与轨道偏移的风格范围 |
| F0 `time_step_seconds`、`time_origin` | 帧步长秒数及 F0 第 0 帧在 MIDI 的时间 | 将已有音高帧条件性关联到音符 |
| WAV `start`、`end` | PCM / WAV 秒数 | 单次分析或摘录最多 30 秒；边界按 PCM 帧对齐 |
| 导出独立审计的 timing | 源与候选各自实际 tempo map 下的秒数 | 发现 tick 看似一致但播放时间不同的转换差异 |

导出报告中的 `tick_roundtrip_verified=true` 只表示该候选的 tick 往返核验；它不能证明真实播放秒数一致。独立审计会分别用双方 tempo map 把音符 onset/end 转为秒，再在基础 2 ms 容差与 MIDI→480 PPQN 量化容差内比较。若秒级差异超限，报告给出 `timing_seconds_mismatch` 硬 blocker。`tempo_table_structure_normalized` 等有效时间线仍一致的重排可以是需逐项认可的损失；`tempo_content_mismatch` 是硬差异。

定点 tick 和实际秒数都一致，也不等于已声学验证、已合成或已试听。

## 状态、重试与取消

操作状态为 `queued`、`running`、`succeeded`、`failed`、`cancelled`、`interrupted`。同一 action 和 `idempotency_key` 对同一请求复用原操作；同一键换了输入返回 `idempotency_conflict`。失败和中断回执保留；检查原因后，以新键重试。

取消只作用于本服务创建的协作式业务进程。`cancel_requested=true` 不等于已经 `cancelled`；结果已提交后到达的取消不会推翻成功状态。stdio 断开或控制服务重启后，操作记录和已提交结果仍在独立 state 中，可用同一配置查询。宿主可能在关闭 stdio 时终止它拥有的整个进程树；本实现不保证进行中的推理或导出跨宿主关闭继续运行。

`vwa_artifacts_list` 的 `verification=not_checked` 只表示尚未核验 SHA。大文件用 `vwa_artifacts_inspect(..., verify=true)` 启动核验操作。未登记引用、缺失文件、摘要失效分别报告；服务不会借任务查询自动迁移或重建用户任务索引。

输入快照支持最大 32 MiB MIDI/SVP。SVP 音频相对引用在快照中改为经目录检查的绝对引用，并保留原始摘要与引用变更回执；不复制音频，不修改原曲。歌词、工程名、路径、参考元数据和语义理由均是不可信素材，不能成为安装、执行或上传指令。

## 语义风格与当前边界

`vwa_context_read` 返回 SVP 歌词、音符、时间、轨道和预设证据。Agent 结合用户背景提供显式 `style_proposal`；`vwa_style_plan` 校验整音符边界、重叠、理由和全局轨道，`vwa_tuning_start` 生成独立 SVP 候选。风格为 `none/gentle/sad/bold`，唱腔层为 `none/chinese_opera`，强度 0–1。服务不隐式调用语义模型，也不恢复已暂停的自动歌词语义路线。

方案保存在 MCP 独立仓库，生成复用现有调音 session。GUI 的自动暂存草稿与服务方案尚未双向同步；GUI 可另行打开生成 SVP。方案内容哈希和操作锁防止把不同方案静默叠加。

当前仍明确保留以下缺口：

- 未以本接口执行并验收真实整曲的下载、分离、转写、核对和全流程导出；局部自制样本或字段示例不能替代整曲证据。
- 未执行 SV 宿主声库合成、试听、推断声库听感或自动回填；`awaiting_external_synthesis` 只表示工程候选已生成。
- 未实现 GUI 自动暂存草稿与 MCP 方案的双向同步。
- 未把视频/混音装配、封面和平台投稿纳入已提供服务。
- 已有 F0 NPZ 与 PCM WAV 是调用方素材，导入/比对不执行音高提取、转写、推理、合成或网络 provider 访问。
- 参考音高提示、音频 RMS/静音测量、导出损失认可都不是真值、声学验证或最终听感结论。
- SVP 歌词/音符写入和候选合并工具尚未提供；当前结构写入仅针对 MIDI。

协议实现依据：[官方 Python SDK](https://py.sdk.modelcontextprotocol.io/)、[客户端接口](https://py.sdk.modelcontextprotocol.io/client/)、[工具与输入输出](https://py.sdk.modelcontextprotocol.io/servers/tools/)。本项目使用单独固定的可选 SDK 环境，实际协议由安装版本执行。