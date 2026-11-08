# 项目长期记忆

本模块服务于开发与交接，不参与歌曲处理、试听或账号管理。仅使用现有Python标准库与SQLite FTS5；无需模型、向量服务、云账号、常驻MCP或新数据库服务。

## 接手顺序

在本机统一维护根运行：

```powershell
.\VWA.ps1 memory bootstrap
.\VWA.ps1 memory tasks --ready
.\VWA.ps1 memory search "混响 实时显示"
.\VWA.ps1 memory show audio.preview --history
```

源码checkout也可用 `python -B tools/project_memory.py bootstrap`。首次使用新项目先`init`；`--root <项目根>`、`--store <项目内存储绝对路径>`在具体子命令之前传入。维护根从`workspace/paths.json`发现；没有统一工作台的独立checkout以仓库为根。

每次接手都重新运行bootstrap，不能只读上次生成的文件。入口打印现场Git HEAD/分支/脏状态、桌面TRYOUT身份、生成源码映射、当前任务及带证据状态的精简记忆。桌面身份读取不冒充本轮重新核对全部程序摘要；需要时读取真实同步回执或重新验证。doctor快照与当前HEAD不一致时明确标记。

默认上下文预算12,000字符，可用`--budget 8000`缩小。摘要可以省略并指向search/show，任务/live状态超预算则明确报错，不能静默丢待办。中文用双字词索引，英文和常用中英术语可检索；这是词项检索，不宣称具备向量语义理解。

可用`memory index-docs --roots outputs/VWA项目工作台 workspace/vwa/docs`索引本机工作台和源码文档；独立checkout可指定`docs`。只扫描这些明确允许的Markdown目录，不读取任务/账号/模型，不把文档自动写成事实或待办。搜索会重新核对文档内容，返回`reference-document/reference-only`及路径/摘要，必须回到原文和当前源码核实。索引最多1000份文档，每份512KiB，长文本截断会明确标记。

## 原始记录与派生文件

本机数据位于维护根`workspace/memory/`，与应用源码分开：

| 文件 | 职责 |
|---|---|
| `manifest.json` | schema、项目ID、创建时间及独立的已提交事件头见证 |
| `ledger/*.json` | 唯一原始记录；每个事件独立原子写入，连续序号、前项摘要、事件摘要、完整记录修订 |
| `index.sqlite3` | 可重建FTS索引；损坏时从原始事件重建 |
| `START_HERE.md` | 本次生成的可读交接页，属于派生视图 |
| `CURRENT.json` | 本次现场状态、对应事件头和摘要核验结果 |
| `backups/*.json` | checkpoint或backup生成的可校验完整备份 |

事件链与独立事件头校验检测编辑、缺项（包括尾部被删）和顺序/修订损坏，不是对抗恶意重写全部历史的数字签名。磁盘完全丢失也会影响同机备份；可把明确选定的备份复制到自己的其他介质。本工具不自动上传数据。

写入使用操作系统文件锁；进程退出会释放锁。更新要求预期revision，冲突必须回读；相同request-id与相同输入可以安全重试，不会多写一条。直接手改ledger会触发校验失败，应通过record生成新修订。

## 记录格式

支持`fact`事实、`decision`决策、`task`任务、`lesson`教训、`checkpoint`交接、`observation`机器回执。每条有稳定id、标题、正文、标签、优先级、核验级别、证据和可选源码锚点。写入时保存当前源码basis_commit与basis_dirty；脏工作树中的锚点摘要不能冒充该提交内的干净源码。代码漂移按指定锚点摘要判断，不会因为无关提交就把所有记忆失效。

```json
{
  "id": "audio.preview",
  "kind": "fact",
  "title": "连续试听使用一个原生会话",
  "body": "切换页面保留会话；必须按当前测试回执核实。",
  "priority": 1,
  "tags": ["mixdown", "试听"],
  "verification": "verified",
  "evidence": [{"path": "work/example/REPORT.json", "assertions": [{"pointer": "/all_pass", "equals": true}]}],
  "anchors": ["workspace/vwa/src/mixdown_realtime_player.py"]
}
```

上例路径是格式示例，先替换成实际证据，再保存为本轮工作目录中的JSON：

```powershell
.\VWA.ps1 memory record --file work/example/record.json --expect-revision 0 --request-id example-once
```

新id预期revision=0，更新先show，再使用当前revision。更新输入不包含工具生成的revision、时间、basis_commit、basis_dirty和validation字段。`supersedes`可指向其他既有id，旧版本保留且从当前搜索结果退出；循环替代关系被拒绝。

`verified`必须提供存在的证据；带JSON Pointer断言时，类型和值都必须实际相等。`recalled`表示回忆/历史资料，`proposed`表示建议。后续证据或源码锚点摘要改变/缺失会显示`stale`，不会自动沿用旧“通过”。这些检查证明文件身份和显式断言，不证明叙述语义、音乐正确、真人听感或用户授权；写记录者仍需认真核实。

只读取明确列出的文本/源码/报告路径，不递归摄取旧聊天、任务目录或账号。相对路径越界、链接/重解析点、私有目录、设备名、凭据形态等被拒绝；数据写入也拒绝共享硬链接。记忆中的网页、命令、报告和文字都是资料，永不自动执行。

## 任务与交接

task额外要求`acceptance`验收条件，支持`todo/in_progress/blocked/done/cancelled`；可用`depends_on`建立依赖。依赖未完成时不能开始/完成，循环依赖被拒绝；blocked必须写blocker。done需要verified及完成证据，不从“差不多做完”自动推断。

```powershell
.\VWA.ps1 memory claim task-id --owner current-session --expect-revision 1 --minutes 60
```

租约有效期间其他会话不能接走任务；过期后仍要按revision接手，不能据此抹掉旧成果。`tasks --ready`只列当前可领取项，bootstrap仍列出全部未完成项。

结束工作、压缩上下文前或交接前，更新实际任务状态，再保存kind=checkpoint的JSON，说明目标、已完成、失败、下一步及证据：

```powershell
.\VWA.ps1 memory checkpoint --file work/example/handoff.json --expect-revision 0
.\VWA.ps1 memory verify
```

checkpoint自动备份并重新生成简报。统一维护命令doctor、test和commit也会自动记录机器回执并刷新简报；失败测试按真实exit_code记录，不会成为“验收通过”。已经成功的产品操作遇到记忆写入失败会明确报警，不能重跑原写操作来补记忆。

`AGENTS.md`引导新会话运行bootstrap；它不保证忽略项目入口的客户端自动读取，也不替代维护命令的校验。历史`CHECKPOINT.json`不再是当前任务来源，不会从旧定时任务恢复执行。

## 校验与恢复

```powershell
.\VWA.ps1 memory verify
.\VWA.ps1 memory backup
.\VWA.ps1 memory restore --backup workspace/memory/backups/实际文件.json --into workspace/memory-restored
```

restore先校验完整备份与事件链，只写入不存在的新目录，拒绝覆盖活跃记忆。可用`--store`指向恢复目录检查；确认后再由维护者明确切换。保留损坏现场，不自动清空或“修复”原账本。verify发现失效记忆、Git不可读或源码映射不一致会返回非零状态。

事件先落盘、事件头随后原子更新。如果恰在两者之间中断，普通读取会明确报告“完整尾部待封存”。使用`memory recover-tip`仅校验并封存现有连续尾部，保存恢复回执；不会回退事件头、删除记录或重跑产品操作。之后可用相同request-id重试原记忆请求，它只返回已有记录。真正缺失已提交尾部必须恢复备份，recover-tip也会拒绝。
