# VWA 本机 Agent / MCP 接口

本文按当前 `workspace/vwa` 工作树中的 `src/agent_mcp.py`、`agent_control.py`、`agent_workflow.py` 及其他已落盘模块，说明 **55 个 stdio 工具**。工具总数由 `agent_control.TOOLS` 和 MCP 注册表共同核对；实际可用工具、代码身份和格式能力仍以 `vwa_system_capabilities().data.tools`、`code_revision` 及真实业务回执为准。

`vwa_assets_import` 也可导入授权目录内的伴奏 WAV，支持整数 PCM 与浮点 WAV，按流式摘要生成独立快照；其 artifact 可用于 `instrumental_artifact_id`。`vwa_reference_import(kind="audio")` 用于整数 PCM 参考测量与摘录，不能用它替代浮点伴奏导入。伴奏首版上限为 2 GiB。

逐段风格汇总遵守现有引擎的全局目标轨语义：一次生成可为一个演唱轨逐段设置风格，其余演唱轨逐段选择保留原表现。不同轨道的独立分段风格不会被静默合并到所有轨道；这种请求返回 `segmented_multi_track_style`，应拆成独立生成轮次。

本文是接口契约，不是整套功能的验收报告。2026-10-27《牵丝戏》真实歌曲复盘建立在历史 45 工具版本上，暴露了跨音高短重叠、导出后无法换入修正 MIDI、风格相邻边界误判、长任务轮询、返回载荷过大、伴奏缺失和交付无统一清单等问题。本文纳入当前源码中对应的接口；这些新增路径是否已在新版本中完成真实整曲复跑，必须以新的绑定回执为准。本文不声称当前 55 工具已经通过完整真实歌曲试测，也不包含声库适配、合成、试听或声学发音验收。

MCP 是可选运行环境；VWA GUI 和推理 Python 不需要安装 MCP SDK。服务不提供发布、账号导出、任意命令执行或平台投稿能力。

## 推荐的一体化流程

首选 `vwa_workflow_create`：歌曲链接/本地音乐、已有 MIDI、已有受管准备任务三者必须且只能选择一个。创建后连续读取 `vwa_workflow_get().next`，按其中的工具、参数、`expected_revision` 和提示推进。

```json
{
  "source": "BV1XXXXXXXXX",
  "options": {
    "language": "zh",
    "voice_mode": "dual"
  },
  "brief": "用户提供的创作背景与风格要求",
  "reference_lyrics": "已获准使用的参考歌词",
  "formats": ["svp"],
  "review_mode": "segments"
}
```

已有 MIDI 时，先 `vwa_assets_import` 导入独立快照，再以 `score_artifact_id` 创建工作流。已有受管准备任务时，只传 `preparation_job_id`，不要再传 `source` 或 `score_artifact_id`。

```json
{
  "preparation_job_id": "job_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "options": {"language": "zh", "voice_mode": "dual"},
  "brief": "沿已完成的准备阶段继续核对与导出。",
  "reference_lyrics": "与准备任务一致的参考歌词",
  "formats": ["svp"],
  "review_mode": "segments"
}
```

`formats` 省略时为 `["svp"]`。可显式包含不重复的 `svp`、`ustx`、`vsqx`；有非空 `brief` 时必须包含 `svp`，因为调用方风格语义只写入 SVP 风格链路。`review_mode` 省略时默认为 `segments` 逐段验收；旧客户端必须显式提交 `"legacy"` 才能走兼容路径。

工作流的业务阶段为：

```text
created -> preparing -> review -> reviewing -> reviewed
        -> exporting -> project_ready -> styling（可选）-> ready

project_ready -- 显式音素修正 --> refining --> project_ready
project_ready -- export_review=needs_revision --> review
project_ready -- finish=true 或不要求风格 --> ready
```

`reviewed` 仅在 segments 模式下所有 MIDI 段均已 checked 后成立。`ready` / `completed=true` 只表示当前工作流选定的可编辑工程候选已生成。它不代表声库已加载、歌曲已合成、发音已试听或平台已发布。

推荐调用顺序：

1. 读取 `vwa_system_capabilities`，确认 55 工具、`code_revision`、`bounded_wait_ms=60000`、`instrumental_export_formats=["svp"]` 和共享草稿状态。
2. 创建或取得工作流，读取 `vwa_workflow_get` 的 `stage`、`revision`、`operation` 和 `next`。
3. 运行中优先用 `vwa_operations_wait(after_cursor=上一次 poll_cursor)` 有界等待；准备任务也可用 `vwa_jobs_get(wait_ms<=60000, after_cursor=上一次 poll_cursor)`。不要无界忙等。
4. 在 `review` 按 `review_progress.current` 逐段调用 `vwa_workflow_review(segment_id=...)`，核对本段音符、歌词和参考证据。不要用一份全曲摘要代替逐段检查。
5. 用 `vwa_workflow_apply_review(segment_id=...)` 提交本段结构、歌词、音高/时值修改，或提交有依据的空修改表示保留；edit 只能引用本段 ID。发生实际修改后先重读本段，全部段 checked 后才进入 `reviewed`。
6. 开始导出前，用 `vwa_workflow_advance(export_options=...)` 设置 SVP 版本、轨道名、工程名和伴奏来源。导出后按当前 `report_sha256` 检查 `export_summary`。
7. 导出监督只接受无 blocker、全部必需损失代码均已逐项认可、源 MIDI 与候选 SHA 仍一致的报告。硬差异提交 `needs_revision`，回到逐段 `review`。
8. 有风格要求时，用 `vwa_workflow_style_review` 和 `vwa_workflow_style_decide` 逐段保留原表现或提交该段风格；全部完成后 `workflow_advance` 自动汇总并进入 `styling -> ready`。全部保留时不启动风格运算。
9. `ready` 后调用 `vwa_workflow_delivery` 生成可移动交付包。交付前仍需检查包内 `manifest.json`、`SHA256SUMS`、伴奏相对引用和 `not_run`。
10. 声库合成、听感、混音、视频装配及投稿都是外部步骤。

所有修改携带当前工作流 `revision` 或文件 SHA。遇到 `workflow_conflict`、`stale_plan`、`stale_artifact`、`stale_draft_plan`、`export_review_conflict`、`review_edit_conflict`、`ambiguous_note_mapping` 或 `monophonic_overlap` 时，先重新读取当前状态；不得猜 ID、复用过期报告或静默覆盖。

## 55 个公开工具

成功响应为 `structured_content = {schema_version: 1, data: {...}}`。业务失败为 MCP `isError=true`，错误正文是可解析 JSON：`{code, message, component, retryable, remediation, details?}`。异步对象的 `id` 用作 `operation_id`。

| # | 工具 | 用途 |
|---:|---|---|
| 1 | `vwa_system_capabilities` | 工具清单、能力、代码身份、外部交接与限制 |
| 2 | `vwa_system_health` | 业务 Python、关键依赖身份；不运行推理或合成 |
| 3 | `vwa_jobs_list` | 分页查看允许任务目录中的准备任务，`limit=1..100` |
| 4 | `vwa_jobs_get` | 读取真实阶段与当前视频 `source_summary`；`wait_ms<=60000` |
| 5 | `vwa_artifacts_list` | 列出登记产物或读取指定任务产物索引 |
| 6 | `vwa_artifacts_inspect` | 读取产物信息；`verify=true` 启动可取消的 SHA 核验操作 |
| 7 | `vwa_operations_get` | 读取持久操作状态、完整结果、失败或中断 |
| 8 | `vwa_operations_cancel` | 请求协作取消；`cancel_requested` 不等于已取消 |
| 9 | `vwa_assets_import` | 从允许素材目录导入 MIDI/SVP 独立快照 |
| 10 | `vwa_score_read` | 读取 MIDI 音符、tick、秒数、歌词关系和稳定 ID |
| 11 | `vwa_score_review` | 返回可定位到音符的核对提示，包括单声部重叠预检 |
| 12 | `vwa_score_patch_plan` | 校验音高/绝对 tick 时值修改；可显式移动唯一所属歌词 |
| 13 | `vwa_score_patch_apply` | 执行已保存方案并生成独立 MIDI 候选 |
| 14 | `vwa_context_read` | 分页读取 SVP 歌词、音符、轨道、预设与风格证据 |
| 15 | `vwa_style_plan` | 保存调用方提供的风格语义、完整音符范围和理由 |
| 16 | `vwa_plans_get` | 执行前重读不可变方案、完整 ID、diff 和理由 |
| 17 | `vwa_tuning_start` | 从风格方案生成独立 SVP 候选操作 |
| 18 | `vwa_tuning_result` | 读取风格候选、partial 原因及外部合成交接 |
| 19 | `vwa_preparation_plan` | 校验 Bilibili/本地音乐准备方案 |
| 20 | `vwa_preparation_create` | 从准备或复用方案创建新的隔离准备任务 |
| 21 | `vwa_preparation_start` | 运行或续跑受管准备任务，保留阶段回执 |
| 22 | `vwa_lyrics_read` | 读取 MIDI 歌词、音符关系、未知项与歧义 |
| 23 | `vwa_lyrics_review` | 核对漏词、重复、错位和参考歌词覆盖 |
| 24 | `vwa_lyrics_patch_plan` | 规划音符级歌词替换或插入 |
| 25 | `vwa_lyrics_patch_apply` | 生成独立歌词修正 MIDI 与回执 |
| 26 | `vwa_exports_plan` | 规划 MIDI 到 SVP/USTX/VSQX，预检重叠并解析伴奏 |
| 27 | `vwa_exports_start` | 执行导出、回读候选并保留独立审计 |
| 28 | `vwa_workflow_create` | 一体化创建工作流；`review_mode` 默认 `segments`，显式 `legacy` 兼容旧路径 |
| 29 | `vwa_workflow_get` | 读取阶段、修订、操作和下一步；默认 summary |
| 30 | `vwa_workflow_review` | 分页读取当前段或旧模式当前页的音符、歌词、参考证据和提示 |
| 31 | `vwa_workflow_apply_review` | 提交当前段结构、歌词、音高/时值修改或保留理由 |
| 32 | `vwa_workflow_advance` | 推进阶段、设置导出选项、提交导出决策、风格或结束 |
| 33 | `vwa_structure_patch_plan` | 规划 MIDI 音符插入/删除及歌词归属策略 |
| 34 | `vwa_structure_patch_apply` | 生成独立结构修正 MIDI 并复核事件 |
| 35 | `vwa_reference_import` | 导入已有 F0 NPZ 或整数 PCM WAV 快照 |
| 36 | `vwa_reference_pitch_review` | 用显式时间原点条件性比较 MIDI 与已有 F0 |
| 37 | `vwa_reference_audio_read` | 读取 WAV 头、局部 PCM 测量或生成试听摘录 |
| 38 | `vwa_workflow_references_set` | 绑定或清除工作流 F0/WAV 参考与秒数 |
| 39 | `vwa_pronunciation_read` | 分页读取 MIDI/SVP/VSQX 原字和原生音素事实 |
| 40 | `vwa_pronunciation_patch_plan` | 规划 SVP/VSQX 明确音素替换，单次最多 512 项 |
| 41 | `vwa_pronunciation_patch_apply` | 生成只改指定音素的独立工程候选 |
| 42 | `vwa_workflow_pronunciation_apply` | 在工作流内批量改音素并重建监督报告 |
| 43 | `vwa_drafts_get` | 读取指定 GUI 实例的共享片段草稿和字节 revision |
| 44 | `vwa_drafts_publish` | 把已校验风格方案按 CAS 发布到共享草稿 |
| 45 | `vwa_drafts_plan` | 从指定 revision 的 GUI 草稿生成绑定方案 |
| 46 | `vwa_operations_wait` | 有界等待操作完成或实际变化，单次最长 60 秒 |
| 47 | `vwa_preparation_reuse_plan` | 规划复用已完成准备阶段；源任务只读 |
| 48 | `vwa_workflows_list` | 分页检索工作流阶段、生命周期和标识 |
| 49 | `vwa_workflow_rebind_score` | 同一活跃工作流换入修正 MIDI 并回到 `review` |
| 50 | `vwa_workflow_close` | 关闭活跃性并保留证据，不删除任务或工程 |
| 51 | `vwa_workflow_archive` | 归档并保留证据、回执和检索入口 |
| 52 | `vwa_workflow_clone` | 从保留 MIDI 或原输入克隆为新的可继续工作流 |
| 53 | `vwa_workflow_delivery` | 生成受验证的工程、相对伴奏、回执、README 与 SHA 清单 |
| 54 | `vwa_workflow_style_review` | 读取当前待查风格片段、当前段音符与相邻只读上下文 |
| 55 | `vwa_workflow_style_decide` | 逐段提交风格或保留决策；全部完成后供 workflow 自动汇总 |

资源 `vwa://capabilities` 与 `vwa://workflow` 提供能力清单和简要指南。工具是否可调用仍以当前 capabilities 和业务回执为准。

## 安装、配置与授权根

Windows x64、CPython 3.12。SDK 固定为 `mcp==2.3.0`，传递依赖和平台 wheel 摘要固定在 `packaging/requirements-mcp.lock.txt`。按项目方法生成根 Python 文件后运行：

```powershell
./tools/Prepare-Source.ps1
./tools/Setup-AgentMCP.ps1 `
  -BootstrapPython 'C:/Python312/python.exe' `
  -WorkerPython 'C:/VWA/dependencies/vocal2midi/python/python.exe' `
  -ApplicationRoot 'C:/VWA' `
  -ControlDirectory 'C:/VWA-Agent'
```

`BootstrapPython` 创建独立 SDK 环境；`WorkerPython` 是现有 VWA 业务 Python。复用环境时增加 `-RuntimeDirectory`。setup 不注册 MCP 宿主，不复制模型或账号，也不修改已有 `config.json`。

控制配置的路径字段都是绝对路径。`shared_draft_root` 与 `allowed_delivery_roots` 可省略，但不能猜测或自动扩大：

```json
{
  "schema": 1,
  "application_root": "C:/VWA/application",
  "worker_python": "C:/VWA/dependencies/vocal2midi/python/python.exe",
  "state_root": "C:/VWA-Agent/state",
  "allowed_asset_roots": ["C:/VWA/assets"],
  "allowed_job_roots": ["C:/VWA/jobs"],
  "shared_draft_root": "C:/VWA/application/cache/style-region-drafts",
  "allowed_delivery_roots": ["D:/VWA-Deliveries"]
}
```

`shared_draft_root` 必须位于指定 GUI 实例的 `cache/style-region-drafts`，并与 MCP state、素材根、任务根分离。省略或 `null` 时共享草稿工具返回 `shared_drafts_disabled`。

`allowed_delivery_roots` 是外部交付的唯一授权目录列表。每个目录必须已存在，不能与应用目录重叠；不能包含 junction/symlink 或重解析点。未配置时只能写入 MCP `state_root/outputs/deliveries` 下的默认交付目录。`destination` 若不在授权根内，返回 `outside_delivery_roots`。

## 等待、紧凑读取与分页

### 等待最长 60 秒

`vwa_operations_wait` 的 `timeout_ms` 严格为 `0..60000`，默认 30000 ms。它是有界等待，不是后台任务续命：

```json
{
  "operation_id": "op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "timeout_ms": 60000,
  "after_cursor": "上一次响应中的 poll_cursor",
  "return_on": "terminal",
  "detail": "summary"
}
```

- `return_on="terminal"`：成功、失败、取消或中断时返回，或到超时返回当前状态。
- `return_on="change"`：状态、进度、错误或取消标志实际变化时返回。第二次及以后应把上次 `poll_cursor` 放进 `after_cursor`。
- `detail="summary"`：默认只返回状态、进度、错误、时间、`result_available` 和 `poll_cursor`；完整结果用 `vwa_operations_get(operation_id)`。
- `detail="full"`：本次响应允许携带完整操作记录。
- `timed_out=true` 表示尚未终结，不等于失败。保留 `poll_cursor` 后再次有界等待，或先 `operations_get` 查看细节。

`vwa_jobs_get` 的 `wait_ms` 同样限制在 60000 ms，并使用返回的 `poll_cursor` 作为下一次 `after_cursor`。准备任务运行中仍应遵守各阶段真实状态，不要因一次 `timed_out` 重开任务。

工作流自己的 `next` 是控制面的下一步建议。导出、准备、风格等操作正在运行时，优先等待其 `operation_id`，再按 `workflow_get.next` 推进。

### `summary` / `notes` / `full`

| 工具 | 默认 | 可选层级 | 分页与完整性 |
|---|---|---|---|
| `vwa_workflow_get` | `summary` | `summary/full` | summary 保留继续所需 ID、revision、stage、candidate、`next`、操作状态、报告 SHA 和损失要求；`include_details=true` 或 `detail="full"` 读取完整回执 |
| `vwa_workflow_review` | `notes` | `summary/notes/full` | 音符默认 `limit=20`，服务限制 `1..200`；按 `next_offset` 读完。summary 不含音符页；notes 是当前段/当前页可验收证据；full 保留当前段/当前页完整原始证据，仍不扩大范围 |
| `vwa_context_read` | `notes` | `summary/notes/full` | 默认 `limit=20`，服务限制 `1..500`；按 `offset/next_offset` 分页。full 保留当前页完整证据 |
| `vwa_style_plan` | `summary` | `summary/full` | summary 把各层 `*note_ids` 替换为对应 `_count`；完整 aligned IDs 用 `vwa_plans_get` 或 `detail="full"` |
| `vwa_operations_wait` | `summary` | `summary/full` | summary 不重复完整结果；full 仅用于需要审计的等待 |

`include_note_ids=false` 是默认值：阅读仍保留定位所需的标量与说明，但会压缩重复的 ID 数组。需要提交精确 note edit 时，不能凭空补 ID；应先读取当前页、在确认分页范围内后显式请求 ID，或读取相应专门工具。

`vwa_workflow_review` 的 `detail="notes"` 在 segments 模式只返回当前未查段，在 legacy 模式返回当前页；`evidence.score.notes` 不包含范围外 ID。`detail="full"` 只保留当前段/当前页完整证据，不替代逐段决策，也不表示一次返回整曲。

`workflow_view.summary_target_bytes=16384` 是紧凑视图的设计目标，不是对任意内容都成立的硬上限。实际大小取决于当页错误、候选数量和人工理由；必须读取 `view.raw_evidence_omitted`、`details_tool` 和 `next_offset`，不能把摘要当作验收证据。

## 默认逐段核对：MIDI 与风格

`vwa_workflow_create.review_mode` 默认为 **`segments`**。Agent 必须按当前段读取证据、提交该段判断，再处理下一段；不能以一次全曲摘要或首屏证据代替逐段验收。`review_mode="legacy"` 仅作为旧客户端的显式兼容路径，必须主动传入；`detail="full"` 仍保留为显式读取兼容，但在 segments 模式中只表示当前段的完整证据，不会跳过或汇总其他段。

分段由纯本地、确定性、只用标准库的 `agent_segments.py` 生成，无模型或语义推断：

- 按 `(track_index, channel)` 分声部；伴奏轨不进入 Agent 核对段。
- 每个固定 10 秒窗口从项目秒 0 锚定，窗口内最多 16 个音符。
- 同一声部相邻音符间隔达到或超过 0.65 秒时另起一段。
- 完整音符不拆分；单个音符本身超过 10 秒时独占一段，并标记 `reason="unbreakable_long_note_exceeds_max_seconds"`。
- 每段有确定性 `id`、`fingerprint`、`index`、声部、秒/tick 范围、`note_count` 和 `note_ids`。计划或音符内容变化后旧段会变成 `stale_segment`。

工作流 summary 中的 `review_progress` 是逐段进度，不是全曲验收：

```json
{
  "mode": "segments",
  "kind": "midi",
  "checked": 3,
  "total": 18,
  "complete": false,
  "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "current": {
    "id": "segment-aaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbbbbbb",
    "fingerprint": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "index": 3,
    "track_index": 0,
    "channel": 0,
    "start_seconds": 30.0,
    "end_seconds": 40.0,
    "note_count": 14
  }
}
```

### MIDI 逐段复核

推荐循环：

```text
workflow_get
  -> workflow_review(segment_id=current.id 或省略以取下一待查段)
  -> 核对本段音符、歌词、参考与 hints
  -> workflow_apply_review(segment_id=同一段, ...)
  -> operations_wait / workflow_advance -> workflow_get
  -> checked / total 增加后读取下一段
```

`vwa_workflow_review` 新增可选 `segment_id`。segments 模式只应读取下一条尚未完成的 MIDI 段；省略时自动选择该段，显式值仅用于重读当前段，不能提前跳到其他段。返回的 `evidence.score.notes`、lyrics 关系和 hints 只限当前段，`segment` 不重复展开全部 note IDs，`review_progress` 给出 `checked/total/current/complete`。`limit/offset` 仍可对当前段内证据分页，默认 `limit=20` 可覆盖常规最大 16 音符段，但不代表可以跳过页内逐项检查；单音超过 10 秒时仍作为完整单段返回。`detail="full"` 只展开当前段完整证据。

```json
{
  "workflow_id": "flow_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "segment_id": "segment-aaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbbbbbb",
  "detail": "notes",
  "limit": 10,
  "offset": 0
}
```

`vwa_workflow_apply_review` 新增可选 `segment_id`。省略时提交给下一条待查段；显式时必须先读取该段并按同一段提交。规则如下：

- `note_edits`、`lyric_edits` 和删除操作的 `note_id` 必须属于该段 `note_ids`。相邻段或“看似相似”的全曲音符都会报 `segment_edit_outside_scope`。
- 插入音符必须位于该段声部的 `[start_tick, end_tick)`，且 `track_index/channel` 与段一致；无法用插入掩盖另一段的问题。
- 仅阅读并保留原结果时，提交有依据的 `rationale` 和空 edit；操作成功后该段记为 `checked`。
- 提交实际修改时，本段不会被直接记完成。必须重新读取该段、确认新 MIDI 中的本段结果，再提交保留/确认；`operation.result.last_segment_result.requires_reread=true` 表示这一要求。
- 新 MIDI 会重建分段。仍有效的已完成段决策按稳定指纹保留；受当前 `monophonic_overlap` 影响的段会撤销 `checked`，必须修复并重读。
- 只有所有段都 checked 且当前候选无阻断时，工作流才进入 `reviewed`；否则保持 `review`。`checked` 是逐段提交结果，不是声学验收。

```json
{
  "workflow_id": "flow_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_revision": 6,
  "segment_id": "segment-aaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbbbbbb",
  "rationale": "逐音核对本段歌词、音高与时值；仅修正该段的错字和短重叠。",
  "note_edits": [
    {
      "note_id": "note_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "onset_tick": 1920
    }
  ],
  "move_attached_lyrics": true
}
```

### 风格逐段决定

导出监督已接受且 `brief` 非空时，segments 模式会从选定 SVP 生成 `style_segments`。调用方必须对每段明确选择“保留原表现”或“应用该段风格”。

`vwa_workflow_style_review(workflow_id, segment_id=None)`：

- 省略 `segment_id` 时读取下一未决定段；显式值必须属于当前 `style_segments` 计划。
- 返回该段 `segment`、当前段完整音符 `context.notes`、最多各两个相邻音符的只读 `adjacent_context`、preset/technique 清单和 `progress.checked/total`。
- `scope="current_segment_only"`；相邻上下文只用于衔接判断，不能作为本段编辑目标。

```json
{
  "workflow_id": "flow_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "segment_id": "segment-dddddddddddddddd-eeeeeeeeeeeeeeeeeeee"
}
```

`vwa_workflow_style_decide` 必须带当前 revision、段 ID 和 1–4000 字符理由：

- 不传 `proposal` 表示 `keep_original`，记录为本段保留原表现。
- 传 `proposal` 表示 `apply_style`。其 `track_indices` 必须恰好是 `[segment.track_index]`，`regions` 必须对齐为完整音符，且所有实际选中的 note ID 必须属于该段；越界报 `segment_style_outside_scope`。
- 每次决定都会增加工作流 revision 和 `checked`，必须重新读取当前状态，不能复用旧 revision。

```json
{
  "workflow_id": "flow_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_revision": 10,
  "segment_id": "segment-dddddddddddddddd-eeeeeeeeeeeeeeeeeeee",
  "rationale": "本段歌词与音高稳定，保留原表现。",
  "proposal": null
}
```

需要应用风格时，`proposal` 使用前面完整 StyleProposal JSON 的同形结构，只把 `track_indices` 和 time regions 限制在当前段。全部风格段完成后：

- `review_progress.kind` 为 `style` 且 `complete=true`。
- `workflow_get().next` 指向 `workflow_advance`；该步骤自动汇总各段 proposal。
- 若所有段都 `keep_original`，工作流不启动风格运算，直接进入 `ready`。
- 若至少一段应用风格，服务合并 track indices 与按时间排序的 regions，自动生成风格方案并进入 `styling`。

segments 模式在全部风格段完成前不接受另一份整曲 `style_proposal`、`style_plan_id` 或 `finish=true`：这些输入分别报 `segmented_style_required` 或 `style_segments_incomplete`。全部完成后由工作流自动汇总，不要求再手工拼一份整曲 JSON。

旧客户端可显式创建 `review_mode="legacy"` 工作流，继续旧的分页全曲核对和整份风格提交路径。legacy 是兼容入口，不是默认路径；无论 `detail="full"` 还是旧的摘要读取，都不能把未逐段完成的 segments 工作流直接标为 `reviewed`。

## 风格方案、完整 JSON 与半开区间

`vwa_style_plan` 只保存调用方明确提供的音乐语义，不推断情绪，不调用模型。它要求目标轨道可用于风格，区域落在完整音符上，片段最多 128 个。最终字段名固定为 **`style_technique`**；输入模型兼容旧别名 **`technique`**，但输出、保存方案和执行回执都规范为 `style_technique`。新调用应始终使用正式字段，不要同时写两个别名。

下面的 JSON 可直接作为 `vwa_style_plan` 参数；替换占位 ID、秒数、理由和证据即可。两个示例区域在 `20.000000` 处首尾相接，表示半开区间相邻。

```json
{
  "artifact_id": "art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "detail": "summary",
  "proposal": {
    "track_indices": [0],
    "brief": "主歌克制、副歌增强戏腔，但保持原音高和原歌词。",
    "regions": [
      {
        "start": 12.500000,
        "end": 20.000000,
        "preset": "gentle",
        "strength": 0.45,
        "style_pitch": true,
        "detail": {
          "onset": 0.85,
          "sustain": 1.0,
          "connection": 0.9,
          "release": 0.8
        },
        "style_technique": "none",
        "technique_strength": 0.0,
        "technique_pitch": false,
        "rationale": "主歌使用温和表达，避免盖过原唱气口。",
        "evidence_refs": []
      },
      {
        "start": 20.000000,
        "end": 28.250000,
        "preset": "bold",
        "strength": 0.62,
        "style_pitch": true,
        "detail": {
          "onset": 1.0,
          "sustain": 0.9,
          "connection": 1.0,
          "release": 0.85
        },
        "style_technique": "chinese_opera",
        "technique_strength": 0.55,
        "technique_pitch": true,
        "rationale": "副歌增加戏腔色彩，强度低于原声主体。",
        "evidence_refs": ["track_aaaaaaaaaaaaaaaaaaaaaaaa"]
      }
    ]
  }
}
```

字段契约：

| 字段 | 约束 |
|---|---|
| `track_indices` | 非空、不重复的非负索引；目标轨道必须 `eligible_for_style=true` |
| `brief` | 非空调用方说明，服务不以它推断风格 |
| `start`, `end` | 项目秒；`0 <= start < end` |
| `preset` | `none/gentle/sad/bold` |
| `strength` | `0..1` |
| `style_pitch` | 布尔值 |
| `detail` | 可选，四阶段乘数；`onset/sustain/connection/release` 各自 `0..1` |
| `style_technique` | `none/chinese_opera`；兼容输入别名 `technique` |
| `technique_strength` | `0..1` |
| `technique_pitch` | 布尔值 |
| `rationale` | 非空、最多 2000 字符 |
| `evidence_refs` | 当前源和所选轨道中真实存在的 note/track ID 数组，不得重复 |

区域使用半开区间 **`[start, end)`**。`[a,b)` 与 `[b,c)` 合法相邻；真正的正长度交集，或整音符对齐后共用完整音符，仍会拒绝。公开秒数保留 6 位小数，规划器只在距真实音符边界不超过 `0.0000005` 秒的有界窗口内把显示值回映到真实边界；这不是扩大重叠的容差，也不会把任意接近的片段自动合并。

请求区域会对齐到被触及的完整音符，因此实际 `regions[].start/end` 可能扩展到完整音符范围。`requested_start/requested_end` 保留请求值，`aligned_note_ids` 给出实际归属。不得用人为留空音符来绕开真正重叠；应回到 `vwa_context_read` 核对秒数和音符边界。

默认 `detail="summary"` 会把所有层级的 `*note_ids` 数组压缩为 `<字段>_count`，包括 `aligned_ranges[].note_ids`，并返回：

```json
{
  "details_tool": {
    "tool": "vwa_plans_get",
    "arguments": {"plan_id": "plan_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
  },
  "view": {
    "detail": "summary",
    "note_ids_omitted": true
  }
}
```

执行前需要完整 aligned IDs、原始 proposal、引擎片段或 diff 时，调用 `vwa_plans_get(plan_id)`；也可在 `vwa_style_plan` 首次调用时传 `detail="full"`。执行本身仍会重新校验方案摘要、源 SVP SHA、完整音符归属和共享草稿 revision。

`vwa_tuning_start` 的成功操作只表示本地 SVP 候选已生成。结果中的 `status=partial` 不是通过；应读取 `partial_reason_counts`、`partial_remediation`、`partial_advice`、`applied_controls`、`skipped_controls` 和 `protected_note_count`，逐项处理原因。`awaiting_external_synthesis` 仍表示声库合成在外部进行。

## 歌曲摘要与准备阶段复用

`vwa_jobs_get` 的 `source_summary` 只面向当前视频和选中分 P：包含 `source_kind`、`bvid`、标题、作者、时长、分 P 数/当前页、封面 URL、描述短摘要、受限权利标志、是否含合集、voice plan 和 `raw_evidence` 路径。合集完整数据、字幕全文、正文和分 P 全表不内联返回；原始 `source-info.json` 仅在 `raw_evidence.path` 指向只读证据。

`voice_mode` 必须按真实含义使用：

- `single`：混合人声＋伴奏。
- `dual`：主唱＋和声/伴奏人声。

两者都不是按歌手音色拆分对唱；`supports_duet_singer_separation=false`。未知或串音内容仍需在 review 中人工处理。

已有准备任务的只读复用链：

```text
vwa_preparation_reuse_plan(source_job_id, options, reuse_stages)
  -> vwa_preparation_create(plan_id)
  -> 得到新 job_id
  -> vwa_workflow_create(preparation_job_id=新 job_id, ...)
  -> vwa_workflow_advance / next
```

| 步骤 | 输入 | 结果 |
|---|---|---|
| `vwa_preparation_reuse_plan` | 源 `job_id`、目标 options、可选 `metadata/download/audio/separation` | 不可变复用计划；列出来源 SHA、兼容性、`reuse_stages` 与 `run_stages` |
| `vwa_preparation_create` | 上述 `plan_id` | 新建受管任务；只复制已重新校验的阶段文件，不改写源任务 |
| `vwa_workflow_create` | 新 `preparation_job_id`，以及 `options`、`brief`、`reference_lyrics`、`formats` | 工作流采用已有受管任务；不要同时传 `source` 或 `score_artifact_id` |

示例：

```json
{
  "source_job_id": "job_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "options": {
    "language": "zh",
    "lyrics": "修订后的参考歌词",
    "recognize_lyrics": true,
    "voice_mode": "dual",
    "alignment_engine": "hfa",
    "midi_steps": 16
  },
  "reuse_stages": ["metadata", "download", "audio", "separation"]
}
```

复用规则：

- 源任务严格只读，不写回源 `request.json/status.json` 或阶段产物。
- 计划绑定源请求、源状态、目标 options 和候选文件 SHA；计划生成后源内容变化返回 `reuse_plan_stale`。
- 复用阶段按依赖前缀计算；只可选 `metadata`、`download`、`audio`、`separation`。请求较后阶段时会核对其依赖，不保证任意阶段单独复用。
- 变更歌词、模型或 MIDI 后段选项，不应让已完成音频阶段无理由重做；但必须使用新计划，不能复用过期回执。
- `voice_mode` 变化导致分离依赖不匹配时，不再复用该分离阶段，返回 `voice_mode_changed` 并使用 `run_stages` 重跑所需部分。
- 新任务只复制当前任务的必要阶段文件，不使用 junction；目标已有内容必须 SHA 相同，否则 `reuse_collision`，绝不覆盖。
- 新建任务后要把**新 job_id**交给工作流。源 job_id 只用于计划和证据。

## MIDI 编辑、歌词联动与单声部预检

音高/时值从 `vwa_score_read` 的当前 SHA 生成方案。每项 `ScoreEdit` 可按需包含 `pitch`、`onset_tick`、`duration_tick`；时间使用绝对 MIDI tick，`duration_tick` 大于 0。

若只改音符时值，默认 `move_attached_lyrics=false`，旧歌词事件不会被隐含移动。需要歌词随音符 onset 移动时，必须显式：

```json
{
  "artifact_id": "art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "move_attached_lyrics": true,
  "edits": [
    {
      "note_id": "note_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "onset_tick": 1920,
      "duration_tick": 480
    }
  ]
}
```

只有原本唯一关联到该音符的 lyrics 事件会按 onset 位移同步移动；关联缺失、同一事件竞争多个音符或其他歧义会拒绝计划。计划/回执和候选回读会验证新歌词关联。`vwa_workflow_apply_review` 同样有显式 `move_attached_lyrics`，默认为 `false`。

结构工具只写标准 MIDI。删除必须指定 `lyric_policy="preserve|delete_owned"`；插入必须给 `track_index`、`channel`、音高、onset、duration、velocity 和可选歌词。工作流修改顺序固定为：

```text
structure -> lyrics -> pitch/time
```

每生成新候选，未变音符按 `(track_index, channel, pitch, onset_tick, duration_tick)` 重映射稳定 ID。删除目标又出现在其他 edit，报 `review_edit_conflict`；同形音符无法唯一映射，报 `ambiguous_note_mapping`。

单声部预检按目标声部 **`(track_index, channel)`** 分组：

- 音符区间为半开 `[onset_tick, end_tick)`；首尾相接不算重叠。
- 只有同一组内的正长度交集产生 `code="monophonic_overlap"`。
- 不同轨道或不同 channel 可合法并行；导出器可能把它们变成不同目标声部，不能仅为了“全局单声部”而误删合法复调。
- 冲突行含 `voice_key`、双方 `note_ids`、音高、overlap tick/秒和 `cross_pitch`，最多返回 100 行。
- `vwa_score_review` 把冲突列为确定性线索；`vwa_exports_plan` 在转换前预检并在存在冲突时拒绝计划，返回结构化 `details.conflicts`。
- 工作流 `apply_review` 在提交修正 MIDI 前再次预检；当前候选有冲突时不会直接进入 `reviewed`。这不是自动删音。

从历史真实复盘看，15 ms 的跨音高重叠就可能让 LibreSVIP 在后期导入失败。遇到 `monophonic_overlap`，应停止重试原导出计划，读取双方位置并生成新的 MIDI，再用 `vwa_workflow_rebind_score` 换入同一工作流。

## 导出选项与可移动伴奏包

`vwa_exports_plan` 和 `vwa_exports_start` 支持低层显式导出；一体化工作流应在 `review` 或 `reviewed` 通过 `vwa_workflow_advance(export_options=...)` 设置，并在导出开始前重读 revision。

```json
{
  "workflow_id": "flow_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_revision": 5,
  "export_options": {
    "language": "zh",
    "default_bpm": 120.0,
    "svp_version": "1.9",
    "include_instrumental": true,
    "instrumental_source_job_id": "job_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "track_names": ["主唱", "和声"],
    "project_name": "牵丝戏-试测"
  }
}
```

`.svp` 专属选项：

| 字段 | 契约 |
|---|---|
| `include_instrumental` | 布尔值。显式 `false` 强制关闭；低层没有来源时默认关闭；一体化工作流会把当前 `job_id` 作为伴奏发现来源 |
| `instrumental_artifact_id` | 使用已登记 WAV 产物作为伴奏；与 `instrumental_source_job_id` 二选一 |
| `instrumental_source_job_id` | 从受管任务的 `audio/instrumental.wav` 解析伴奏；源任务只读 |
| `instrumental_path` | 只供控制层解析后的 worker 内部字段；公开调用传它会报 `unsafe_media_path` |
| `track_names` | 非空短文本数组；数量必须等于 SVP 演唱轨数，不能把伴奏轨算入 |
| `project_name` | 写入 SVP `renderConfig.filename` 的短文本；不能含路径分隔符、换行或 NUL |

约束：

- `include_instrumental=true` 时，必须能由 `instrumental_artifact_id` 或 `instrumental_source_job_id` 解析出存在的 PCM WAV；否则 `instrumental_missing`。
- 同时指定 artifact 和不同来源任务会报 `conflicting_instrumental_source`；关闭伴奏时不能同时给伴奏 artifact。
- 从带准备任务的工作流导出时，若未指定新伴奏来源，工作流会把当前 `job_id` 作为 `instrumental_source_job_id` 默认发现来源；找不到 WAV 不会伪造伴奏。
- 首版只在 **SVP** 中生成可移动的相对伴奏包。伴奏复制到 SVP 同目录，引用写为相对文件名；回读验证伴奏轨数量、SHA、音频时长、文件存在和演唱轨未变。
- 若 `formats` 同时含 SVP 与其他格式，只有 SVP 声明 `instrumental_included=true`；USTX/VSQX 明确返回 `format_not_supported`，不会被说成已经包含伴奏。
- `track_names` 与 `project_name` 首版只写入 SVP；其他格式返回 `metadata_not_supported_for_format`。仍会导出工程，但元数据未应用。
- 渲染器回读后，候选中的 `instrumental_included`、`instrumental_reason`、`media.relative_paths`、`media.sha256` 和 `media.duration_seconds` 是核对依据。MCP 成功不等于用户已经试听。

`svp_version` 支持字符串 `"1.9"`、`"1.11"`、`"2.0"`；`default_bpm` 为 `1..1000`。只演唱 SVP 时省略伴奏或显式传 `include_instrumental=false`，结果应明确说明未包含伴奏，而不是静默当作便携试听包。

## 导出监督

导出完成后，先用 `vwa_workflow_get` 的紧凑 summary 检查：

| 字段 | 含义 |
|---|---|
| `export_summary.report_sha256` | 当前不可变监督报告 SHA；决策必须携带 |
| `source_sha256` | 本次源 MIDI SHA |
| `candidates[]` | artifact、格式、候选 SHA、音符/歌词计数、伴奏是否包含及媒体证据 |
| `candidates[].pronunciation` | 绑定候选 SHA 的 SVP/VSQX 前 50 note 原生读音事实或 `unknown` |
| `blockers` / `can_accept` | 硬差异与是否仍可审计接受 |
| `required_loss_codes` | 本次必须逐项认可的转换损失/未知项代码 |
| `warnings` / `information` | 告警与信息项 |
| `independent_audit` | 独立回读候选的音符、歌词和实际秒数比较 |

需要完整候选和操作原始证据时，用 `vwa_workflow_get(include_details=true)` 或读取对应 `vwa_operations_get`。默认 summary 保留接续所需的报告 SHA、损失要求和候选摘要，但不重复全部原生 notes。

```json
{
  "workflow_id": "flow_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_revision": 8,
  "export_review": {
    "report_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "decision": "accept",
    "rationale": "已核对候选音符、歌词、实际秒数、伴奏相对引用和逐项损失。",
    "accepted_loss_codes": ["tempo_table_normalized"]
  }
}
```

`accept` 只有在无 blocker、`accepted_loss_codes` 恰好覆盖当前 `required_loss_codes`、源 MIDI 与全部候选 SHA 仍一致时成立。硬差异提交 `decision="needs_revision"`，工作流回到 `review`，旧摘要保存在 `previous_export_summary`。`finish=true` 不能跳过未接受的报告。

`tick_roundtrip_verified=true` 只证明 tick 往返；实际播放秒数由双方 tempo map 独立比较。有效时间线相同但速度表结构重排可能是需认可损失，`tempo_content_mismatch` 是硬差异。

## 工作流生命周期与失败恢复

业务阶段和生命周期是两个维度：

- `stage`：`created/preparing/review/reviewing/reviewed/exporting/project_ready/styling/refining/ready`。
- `lifecycle`：`active/closed/archived`。
- `operation.state`：`queued/running/succeeded/failed/cancelled/interrupted`。

`vwa_workflows_list(limit=1..100, offset, status, query)` 可按阶段、生命周期或实际操作状态过滤，并检索工作流 ID、来源、brief、job ID、MIDI/project/final artifact ID。它保留旧修订证据的检索入口。

### 换入修正 MIDI：`rebind_score`

工作流重试使用 `vwa_workflow_advance(retry=true)`。它只重新执行当前阶段保存的同一方案：

- `preparing`：重跑当前准备计划。
- `exporting`：重跑当前 `active_plan_id`。
- `styling`：重跑当前风格计划。
- `reviewing`：回到 `review` 重新提交核对决定。
- `refining`：重跑当前音素操作。

若已生成修正 MIDI，应调用 `vwa_workflow_rebind_score`：

```json
{
  "workflow_id": "flow_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_revision": 9,
  "score_artifact_id": "art_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "reason": "按 monophonic_overlap 冲突修正短重叠后重新核对。"
}
```

前置与结果：

- 工作流必须 `active`，操作不能是 `queued/running`；先取消并等待结束，否则 `workflow_busy`。
- 新输入必须是 MIDI；非服务输出快照会先导入为独立素材。
- 当前修订及失败操作、旧报告和旧候选保留，`recovery_history` 记录保留证据路径及 SHA。
- 阶段回到 `review`；清空旧核对认可、导出摘要/决策、工程候选和最终候选；segments 模式会为新 MIDI 重建分段并清空逐段 checked。
- 必须重新 review、重新导出并按新 `report_sha256` 认可。旧 `accept` 不会继承。

### 关闭、归档与克隆

`vwa_workflow_close` 与 `vwa_workflow_archive` 都要求当前 revision 和非空理由；运行中操作必须先取消并等待终结。它们不删除任务、artifact、操作或修订证据。关闭/归档后普通业务修改返回 `workflow_closed`，`vwa_workflow_get().next` 指向 clone。

`vwa_workflow_clone`：

- 从保留 MIDI 克隆（`score_artifact_id` 可覆盖）或按旧请求重新创建。
- 支持旧代码版本工作流继续处理。
- 生成新的工作流 ID 和新的 revision 历史。
- 不继承旧 review/export acceptance；必须重新 review 和 export。
- 使用新的 `idempotency_key`，避免请求冲突。

### 错误恢复表

| 错误 | 含义与恢复 |
|---|---|
| `workflow_conflict` | revision 已变化；`workflow_get` 后使用最新 revision |
| `workflow_busy` | 操作仍 queued/running；`operations_wait`，必要时 `operations_cancel` 后再等待 |
| `stale_workflow` | 业务代码已改变；用 `workflow_clone` 从保留输入继续 |
| `workflow_closed` | 已关闭或归档；证据保留，clone 后继续 |
| `stale_plan` / `stale_draft_plan` | 方案或共享草稿已过期；重新读取并生成方案 |
| `stale_artifact` | 文件身份变化；`artifacts_inspect(verify=true)` 或重新导入/准备 |
| `review_invalidated` | 核对后 MIDI 改变；重新 review |
| `export_review_conflict` | 源 MIDI、候选或报告已改变；读取新报告，不沿用旧认可 |
| `monophonic_overlap` | 同一 `(track_index, channel)` 有正重叠；修正 MIDI 后 review/rebind |
| `export_converter_rejected` | 转换器拒绝候选；检查转换日志和重叠，必要时 rebind，不重复旧计划 |
| `retry_requires_rebind` | 输入已变化；换入新 MIDI，不要机械 retry |
| `unsafe_media_path` | 调用了公开禁用的 `instrumental_path`；改用 artifact/job ID |
| `outside_delivery_roots` | 外部 delivery 目录未授权；选配置内的目录 |
| `delivery_conflict` | 目标有未知/不一致内容；保留现场并使用新名称或目标 |

同一 action 与 `idempotency_key`、相同输入会复用原操作；同键换输入报 `idempotency_conflict`。失败后应使用新键重试相应操作。错误对象中的 `retryable` 只是提示，不替代当前输入、SHA 和工作流阶段检查。

## 统一交付包

`vwa_workflow_delivery` 只接受当前完整且 `stage="ready"` 的工作流，并要求：

- 当前 `export_review.decision="accept"` 与 `export_summary.report_sha256` 精确匹配。
- 无导出 blocker，全部 `required_loss_codes` 已认可。
- 源 MIDI、导出候选、最终工程、风格候选和调音回执的 SHA 可重验。
- 若应用风格，最终 SVP 必须唯一绑定到已接受的导出 SVP，并有一致的调音回执和 `partial` 证据。

```json
{
  "workflow_id": "flow_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_revision": 12,
  "destination": "D:/VWA-Deliveries"
}
```

`destination` 省略时写入 MCP state 的 `outputs/deliveries/<flow_id>/<revision>`。指定外部目录时必须位于 `allowed_delivery_roots`；`name` 只能是服务可验证的安全短名称。关闭/归档的记录在阶段和证据仍满足上述条件时也可按当前 revision 交付，生命周期状态不会伪造新的验收。

包内容：

- 选定的最终工程：SVP、USTX 或 VSQX。
- SVP 支持的首版相对伴奏媒体；媒体复制、身份和时长经回读验证。
- 有风格时的 `tuning-receipt.json`。
- `README.txt`、`manifest.json`、`SHA256SUMS`。
- 音符/歌词/秒数审计摘要、导出报告 SHA、风格状态及 partial 原因。
- 明确的 `not_run`：声库合成、声学试听与发音正确性、模型推理/语义判断、平台上传。

返回值关键字段为 `manifest_path`、`manifest_sha256`、`sha256`、`files`、`complete`、`reused`、`summary` 和 `limitations`。交付包不回传直接执行命令，不复制账号或整个任务。

写入是排他式的：目标不存在时先在临时目录构建并验证，再发布；目标已存在时只有在 manifest、项目 SHA、README 和 SHA256SUMS 全部证明为同一交付时才复用，否则 `delivery_conflict`。不得删除未知文件后覆盖。

若 `complete=false`、文件 SHA 不一致、伴奏引用为绝对路径、伴奏轨身份/时长不符或 `not_run` 未出现，不能把包当作完整交付。

## F0、PCM 与工作流参考

`vwa_reference_import` 只导入允许目录中已有的 F0 NPZ 或 PCM WAV，不执行音高提取、转写、推理或合成。F0 NPZ 必须是 schema 1，含 `midi_pitch.npy`、`voiced_mask.npy`、`time_step.npy` 和 `schema.npy`；上限 32 MiB。WAV 只接受未压缩 RIFF/WAVE PCM 整数采样，上限 256 MiB。

`vwa_reference_pitch_review` 使用显式 `time_origin` 把 F0 第 0 帧放到 MIDI 秒时间轴，再按 tempo map 与音符半开区间 `[onset, end)` 比较。单路 F0 不能可靠拆和弦或复调；重叠应显示 `ambiguous_polyphonic_overlap`，`automatic_patch=false`。参考只是条件性证据，不是真值。

`vwa_reference_audio_read` 未给范围时只读 WAV 头。给定 `start/end` 时单次最多 30 秒，边界向 PCM 帧外对齐；`make_excerpt=true` 生成逐帧复制摘录，不重采样、不改音高、不覆盖源。

工作流在 `review` 或 `reviewed` 用 `vwa_workflow_references_set` 绑定参考。任何新增、替换或清除都会使旧 review 失效并回到 `review`。`time_origin` 与 `audio_time_origin` 必须由调用方给出依据；服务不会自动猜对齐。

## 读音事实与显式音素候选

读音工具不执行 G2P、辞典自动选读音、多音字推断、模型推理或联网。未知读音保持 `unknown`。

`vwa_pronunciation_read(artifact_id, limit, offset)` 分页读取原字和实际写入的音素事实；按 `next_offset` 读完。导出 summary 中的前 50 note 预览只供快速检查，不替代完整分页。

低层音素链路：

```text
pronunciation_read
  -> pronunciation_patch_plan(expected_sha256, edits)
  -> plans_get
  -> pronunciation_patch_apply(plan_id, new idempotency_key)
  -> operations_wait / operations_get
```

公开上限是一次 1 到 3 个工程，每个 target 1 到 256 项编辑，每个音素串最多 256 字符。USTX 不能作为写入目标。目标必须无重复，SHA 必须等于当前候选。

工作流内使用 `vwa_workflow_pronunciation_apply`：

```json
{
  "workflow_id": "flow_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_revision": 9,
  "rationale": "按明确的项目读音表写入音素；未知项保持未知。",
  "changes": [
    {
      "artifact_id": "art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "expected_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "edits": [
        {"note_ref": "note_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "phonemes": "xian1"}
      ]
    }
  ]
}
```

调用后进入 `refining`。成功得到新的独立候选并重建监督报告，原候选保留；旧 `export_review` 清除，必须在新的 `project_ready` 重新按新 `report_sha256` 认可。转换损失仍包含 `explicit_phonemes_unverified`。写入文件只证明明确音素已写入，不证明实际发音正确。

## GUI 共享片段草稿

`vwa_drafts_get(artifact_id)` 只接受当前登记 SVP，返回 `exists`、`input_sha256`、`revision`、`data`、`path`、`legacy`、`artifact_sha256` 和 `source_key_basis`。`revision` 是草稿文件实际字节的 SHA-256；旧格式文件被外部手动修改也会改变 revision。

`vwa_drafts_publish` 只能发布当前代码版本、当前源 SVP 的已校验风格方案。`expected_revision=null` 仅用于新建；更新必须用刚读到的准确 revision。写入使用跨进程锁、严格 CAS 和原子替换。

CAS 冲突返回 `draft_conflict`，详情含 `expected_revision`、`current_revision` 和 `recovery_path`。主草稿保持原样，恢复文件保留当前数据、待写数据和双方 revision。重新读取后显式决定，不得只把 expected 改成 current 后盲重试。

`vwa_drafts_plan` 读取精确 revision，要求 `mode=regions`、非空片段和目标轨道，并由完整 SVP 音符重新校验。边缘落在音符内部报 `incomplete_note_boundary`。推荐闭环：

```text
drafts_get
  -> drafts_plan(expected_revision)
  -> plans_get
  -> workflow_advance(style_plan_id=...)
  -> styling -> ready
```

`style_plan_id` 与 `style_proposal` 互斥；方案必须绑定当前工作流 `project_artifact_id`。启动时再次检查草稿 revision，计划与启动之间发生字节变化会报 `stale_draft_plan`。

首版共享范围仅是按输入工程隔离的时间片段，不是 GUI 全局偏好、不同安装实例或其他歌曲。省略 `shared_draft_root` 时三个草稿工具返回 `shared_drafts_disabled`。

## 时间、操作状态与取消

| 时间或位置 | 单位与依据 |
|---|---|
| `onset_tick`, `duration_tick`, `ticks_per_beat` | MIDI 绝对 tick / PPQN；SVP 读取使用其内部 tick 基准 |
| SVP 风格片段 `start`, `end` | 项目秒数，已计入 tempo/轨道偏移；半开区间 |
| F0 `time_step_seconds`, `time_origin` | 帧步长与 F0 第 0 帧在 MIDI 秒时间轴的位置 |
| WAV `start`, `end` | PCM/WAV 秒；分析/摘录最多 30 秒 |
| 导出独立审计 timing | 源与候选各自 tempo map 下的实际秒数 |

操作状态为 `queued/running/succeeded/failed/cancelled/interrupted`。`succeeded` 只表示候选或回执已提交，不自动表示声学、听感或发布完成。

取消只作用于本服务创建的协作式业务进程。`cancel_requested=true` 不等于已经 `cancelled`；结果已提交后到达的取消不推翻成功状态。stdio 断开或控制服务重启后，持久操作记录与已提交结果仍在独立 state；宿主关闭进程树时，进行中的推理、准备或导出不保证继续。

所有失败先读取 `code`、`details`、`component`、`retryable` 和 `remediation`。`retryable=true` 仍需核对输入身份和当前 revision。

## MCP 协议与环境

成功结构：

```json
{
  "schema_version": 1,
  "data": {}
}
```

业务失败通过 MCP `isError=true` 返回，正文为 JSON 错误对象。Pydantic 输入模型禁止未知字段；字段名、布尔值和数组层级必须匹配当前 schema。

示例中的 `...PLACEHOLDER`、`flow_aaa...`、`art_bbb...`、`job_...` 都必须替换为实际返回值，不能猜测或复用其他任务 ID。

通行边界：

- 未验证的真实歌曲、声库、模型或平台过程不得因代码审阅或夹具测试写成“已通过”。
- 服务不自动执行歌词语义推断、声库合成、试听、声学测量或自动回填。
- 音频伴奏首版仅支持 SVP 的相对媒体包；USTX/VSQX 的伴奏和元数据能力必须按 `instrumental_included=false`、`format_not_supported` 或 `metadata_not_supported_for_format` 如实报告。
- SVP 复杂库组、无法安全展开的引用或任意方言不在支持承诺内；越界应拒绝。
- VSQX 只在受支持的有限 v3/v4 结构内处理；不承诺任意 VSQX 库、复杂复调、DTD 或全部编辑器版本。
- 公开音素写入有批量上限；底层函数能接受更多不代表公开工具已通过大批量验收。
- GUI 共享草稿依赖同一指定应用实例和配置根；其他安装、歌曲和全局偏好不自动同步。
- `ready` 和 `complete=true` 是工程/交付证据状态，不是声库适配或合成完成声明。
- 当前 55 工具的相关改进仍需以新的真实歌曲绑定回执完成终验。

协议实现依据：[官方 Python SDK](https://py.sdk.modelcontextprotocol.io/)、[客户端接口](https://py.sdk.modelcontextprotocol.io/client/)、[工具与输入输出](https://py.sdk.modelcontextprotocol.io/servers/tools/)。本项目使用单独固定的可选 SDK 环境，实际协议以安装版本执行。
