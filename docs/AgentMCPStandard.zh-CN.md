# VWA MCP：12 个任务入口

MCP 服务固定提供 12 个工具，不再提供 `--profile legacy` 或独立注册的 61 工具目录。原 61 个工具名只保留在[历史接口与动作映射](AgentMCP.zh-CN.md)中，供理解旧记录和逐项核对能力。历史工作流或底层 `next` 若仍带有旧工具名，服务会在内部归一为当前 `request.action` 后再返回 12 工具调用；客户端和用户歌词、理由、标题等数据不会被改写。更新服务或连接后应重新发现工具，并以实际 `tools/list` 与能力回执核对。

12 个入口复用相同的受管任务、工作流、原唱 F0、GUI 草稿及质量检查。`code_revision` 标识业务代码，`interface_revision` 标识 MCP 接口代码；工作流整数 `revision`、草稿 revision 和产物 SHA 是不同的身份，不能互相替代。

| 工具 | 职责 |
|---|---|
| `vwa_start` | 创建工作流或准备任务；可使用歌曲来源、受管任务或已有 MIDI。 |
| `vwa_status` | 查询能力、健康、任务、操作、产物、计划、草稿和工作流。 |
| `vwa_evidence` | 分页读取音符、歌词及参考时间轴、参考音高、音频证据、风格和发音。 |
| `vwa_decide` | 提交明确的核对决定、音符/歌词/结构/读音修改或风格方案。 |
| `vwa_run` | 执行已批准的准备、验证、导出和风格生成步骤。 |
| `vwa_wait` | 有界等待操作或准备任务，单次最多 60000 毫秒。 |
| `vwa_cancel` | 请求协作取消；请求取消与任务实际取消是不同状态。 |
| `vwa_input` | 导入素材、绑定参考，或按明确请求抓取歌词快照。 |
| `vwa_search` | 搜索外部歌词来源，供调用方核对版本。 |
| `vwa_recover` | 计划或启动声部修复、准备阶段复用。 |
| `vwa_delivery` | 明确认可或退回指定报告；可在就绪时生成受管交付包。 |
| `vwa_manage` | 更换 MIDI、克隆、关闭或归档工作流，并保留证据。 |

除等待、取消、搜索外，入口使用有类型的 `request`；其中 `action` 选择固定操作。为保留原有能力，基础动作使用旧工具名去掉 `vwa_` 的名称，例如 `workflow_review`。请求字段以当前连接的工具 schema 为准；不支持任意命令和无限制的通用执行器。

## 示例：导入已有 MIDI 并核对

以下为单次工具调用参数，实际 artifact ID、工作流 ID、revision、operation ID 和文件路径来自前一步结果。路径必须处于配置允许的素材目录。

1. 查询能力：`vwa_status`。

   ```json
   {"request":{"action":"system_capabilities"}}
   ```

2. 导入 MIDI：`vwa_input`。

   ```json
   {"request":{"action":"assets_import","path":"C:/VWA/exchange/song.mid"}}
   ```

3. 创建整曲审核工作流：`vwa_start`。

   ```json
   {"request":{"action":"workflow_create","score_artifact_id":"<上一步的 artifact id>","formats":["svp"],"review_mode":"full","idempotency_key":"<本次任务唯一键>"}}
   ```

4. 分页读取：`vwa_evidence`。按返回的 `next_offset` 继续，直至已读取所有需要判断的音符、歌词和提示。

   ```json
   {"request":{"action":"workflow_review","workflow_id":"<workflow id>","limit":100,"offset":0}}
   ```

5. 根据实际核对结果提交明确决定：`vwa_decide`。若需要修改，在同一请求中按 schema 提供具体音符、歌词或结构修改，并在修改后回读候选。

   ```json
   {"request":{"action":"workflow_apply_review","workflow_id":"<workflow id>","expected_revision":0,"rationale":"<实际核对后的保留或修改理由>"}}
   ```

6. 等待返回的操作，再根据工作流 `next` 继续。超时只表示该次等待结束，不能视为失败或完成。

   ```json
   {"operation_id":"<operation id>","timeout_ms":60000,"detail":"full"}
   ```

服务不会替调用方判断歌词、音乐、风格或声学发音。`full` 是整曲一次明确决定，默认如此；`segments` 仍逐段核对，不能用查看相邻上下文代替当前段的决定。查看摘要或收到分页不代表已完成审核。

已有受管目录中的 MIDI 可以在 `vwa_start` 的 `workflow_create` 请求中直接提供 `midi_path`，省去单独的素材导入。也可以在创建时通过 `export_options` 一并记录格式相关的来源和选项，例如 `pitch_source_job_id`、`pitch_bindings`、`instrumental_source_job_id`、`instrumental_artifact_id`、`include_instrumental` 和 `preserve_pitch`。这些是受管对象 ID，不是内部文件路径；后端在真正导出前仍会核验来源、摘要及声部映射。相同幂等键只能用于相同的创建请求和初始导出选项。

`workflow_apply_review` 可显式设置 `run_until_gate=true` 和最多 60000 毫秒的 `wait_ms`，让服务在这次明确决定后继续确定性阶段；预算用完时返回实际状态及下一步。修改音符、歌词或结构后，必须回读修改候选并重新明确确认，才能继续导出。这个选项不会自动完成音乐判断，也不能代替阅读证据分页。

## 歌词先输入与时间轴参考

新歌曲开始准备前，若已有经过人工核对的正式歌词，应优先把它传入准备计划的 `options.lyrics`；已有准备任务的恢复计划也可以在明确确认后传入该项并重跑所需阶段。工作流创建还接受 `reference_lyrics` 作为当前 MIDI 的歌词核对文本。歌词不是必填项，没有可靠来源时仍可自动识别；不因缺少歌词而阻断流程。

外部时间轴是可选参考。`vwa_search` 只检索版本，`vwa_input` 的 `lyric_timeline_fetch`／`lyric_timeline_import` 只保存带摘要的快照，`vwa_evidence` 的 `lyric_timeline_read` 只分页读取行、时间和来源状态。这三步都不会绑定工作流、重排歌词或改写 MIDI。需要把时间轴作为核对参考时，调用方须先明确核对原唱／翻唱、时长、版本和偏移，再以带 `expected_revision`、`rationale` 的 `workflow_references_set` 显式绑定；绑定歌词时 `lyric_version_note` 仍为必填。`lyrics_read` 只读 MIDI 歌词事件，传入时间轴 artifact 会提示改用 `lyric_timeline_read`。

歌词事件归属有歧义时，可先用 `lyrics_read` 取得绑定完整源 SHA 的 `event_id`，再通过 `lyrics_patch_plan` 明确提交 `move_event` 或 `delete_event`，回读差异后使用 `lyrics_patch_apply` 并重新读取候选。目标音符必须在同轨且归属唯一；事件 ID 不可跨候选版本使用。事件级操作暂不支持直接放入 `workflow_apply_review`：先独立修补并回读，再绑定或创建工作流。普通歌词文本和音符编辑中的未变化项会单独计数，不会阻断同批有效修改。

## 原唱连续 F0 和导出

修正 MIDI 的离散音符不能替代原唱连续 F0。导出前应保留同源受管准备任务或显式导入并核对 F0 参考；`pitch_source_job_id`、`pitch_bindings`、伴奏来源和 `preserve_pitch` 等选项仍由既有导出逻辑验证。来源缺失、SHA 不一致、声部映射不明确或格式回读失败时，不得静默当作原唱音高已保留。

GUI 与 MCP 使用共同的 F0 曲线构建逻辑：仅对同一音符内部、远离首尾且两侧走势一致的孤立大尖刺作局部平滑；持续偏移和边界处的剧烈变化保留并提示核实。无声段默认保持断开。回执中的 `fidelity` 和修正帧列表区分源文件未变、有效 F0 回读通过与输出曲线逐帧无损；发生平滑时不能将这三者混为一谈。提示数量超过回执上限时会标记截断，不能将截断的列表视为完整异常清单。

`track_names` 只为演唱轨命名，不包含伴奏；伴奏使用独立来源选项。若导出已失败，可在读取当前工作流与失败详情后，用当前 `expected_revision`、`retry=true` 和修正后的 `export_options` 重新发起；后端重新检查已审核源和来源身份，并保留失败记录，导出报告仍须显式审阅。

导出候选完成后读取监督报告，依据其 SHA、音符/歌词/时间回读和实际损失逐项接受或退回。最终 `ready` 是可编辑工程就绪，不能据此声称完成声库合成、人工试听或发音验收。当前真实整曲音高回读通过范围以 SVP 的既有验收报告为准；USTX/VSQX 仍以逐文件回执和阻断结果为准。

对已经实际核对的报告，可以一次调用 `vwa_delivery`：在 `request` 中提供 `action="workflow_delivery"`、工作流 ID、当前 `expected_revision`，以及含 `report_sha256`、`decision`、`rationale`、`accepted_loss_codes` 的 `export_review`。`decision="accept"` 时只认可调用方列出的损失；需要修改时使用 `needs_revision`。若仍有风格判断等未完成阶段，返回当前工作流及下一步，不会伪称交付已生成。需要指定交付位置时，`destination` 必须处于服务允许的目录；`name` 是文件名而不是路径。

## 失败、恢复与历史工作流

恢复入口不会自动改变用户选择的双声部模式。`preparation_repair_plan` 保存方案，不运行模型；`preparation_repair_start` 在新受管任务中复核声部分离结果，不覆盖源任务。需要继续 MIDI 时，按结果中的下一步操作。

对不确定是否提交成功的请求，先读取操作或工作流；同一个幂等键仅可用于同一逻辑请求。旧 `expected_revision` 冲突时重新读取并重新判断，不能盲目覆盖 GUI 草稿。已有 operation 中断后是否重试应先核对来源 SHA、计划与当前版本。

业务代码更新后，旧工作流的证据仍可读取；若服务提示 `stale_workflow`，可用 `vwa_manage` 的 `workflow_clone` 从保留的 MIDI 和来源建立新工作流，重新核对音符、歌词与导出报告。克隆不继承旧审核结论；受管任务、原始工程、旧候选和历史记录都不会因接口收敛而删除。

服务只把明确的协议续作字段和错误建议转换为当前动作；任意 `payload`、`result` 用户内容以及歌词、理由等素材保持原样。历史 `next` 中的旧工具名因此仍能生成可调用的 12 工具请求，同时不会把用户内容中的伪工具名当作指令。

工具注解按整个入口可能发生的副作用保守标注。例如 `vwa_input` 包含明确触发的网络抓取，也包含本地导入；`vwa_status` 的部分查询会登记索引或记录中断，不能一概声明严格只读。权限由服务端路径和任务校验控制，不由注解代替。
