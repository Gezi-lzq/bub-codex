# ADR 0002: Tape、Anchor、Handoff 与 Codex thread 的语义关系

日期：2026-06-10

## 状态

Accepted

## 背景

`bub-codex` 的目标不是让 Bub 包住一个长期运行的 `codex e` 子进程，而是让 Codex 作为 Bub-native coding runtime 参与 Bub 的 session、tape、hook、tool 和 observability 模型。

这要求我们明确几组容易混淆的身份和边界：

- Bub `session_id`
- Bub tape
- Bub `Anchor`
- Bub `Handoff`
- Codex `thread_id`
- Codex turn id

如果这些概念被合并成一个“会话”概念，后续很容易让 Codex thread 变成唯一状态源，进而削弱 Bub tape 的事实账本地位。

## 决策

采用以下核心不变量：

```text
tape owns history.
Anchor owns committed context materialization boundaries.
Codex thread owns executable model context.
```

中文定义：

```text
tape 负责保存事实历史。
Anchor 负责声明已经提交的 LLM context materialization 起点。
Codex thread 负责承载实际执行时的模型上下文。
```

因此，v0 不支持 `soft Anchor`。如果只是阶段标记、备注、checkpoint 或 UI timeline marker，而且不会作为 Codex compact / new thread 的 materialization 起点，不创建 Anchor。

## 身份关系

一个 Bub `session_id` 映射到一个 primary tape：

```text
session_id -> primary tape
```

一个 primary tape 可以在生命周期中绑定多个 Codex `thread_id`：

```text
primary tape -> thread_id t1 -> thread_id t2 -> thread_id t3
```

任意时刻，一个 Bub session 默认只有一个 active Codex thread：

```text
session_id
  active_tape
  active_anchor_id?
  active_thread_id
```

Codex thread 的切换必须写入 tape：

```text
codex.thread.bound
  session_id
  tape_id
  thread_id
  previous_thread_id?
  anchor_creation_id?
  anchor_id?
  reason: start | resume | handoff | reset | compact | fork
```

`codex.thread.bound` 必须带 `anchor_id`。new thread binding 总是绑定到一个已经存在的 Anchor。`anchor_creation_id` 只用于追踪创建 Anchor 的事务，不用于替代 `anchor_id` 作为 thread binding identity。

## Tape

Tape 是 Bub 的 canonical fact record。它记录 session 中发生过什么，而不是简单保存 prompt transcript。

Tape 应包含：

- inbound message
- Codex thread binding
- turn lifecycle
- completed Codex items
- tool calls and results
- file changes
- subagent lineage
- compaction events
- Anchor / Handoff
- errors and interruptions

Tape 不应该被 Codex rollout JSONL、Langfuse trace、OTel span 或 Obelisk query table 替代。这些都应该是 projection 或 enrichment source。

## Anchor

Anchor 是 tape 上的 context materialization boundary event，用于声明：

```text
从这里开始，后续 LLM runtime context 应从这个边界 materialize。
```

Anchor 可以携带：

- `anchor_id`
- `method`: `compact | new_thread`
- `reason`
- `created_at`
- `state`
- `refs`

`summary` 不是 Anchor 的必填字段。Anchor 的成立条件是 Bub 提交了一个 context materialization boundary，而不是 thread 是否已经成功绑定，也不是 summary 是否可得。

v0 Anchor schema 保持最小顶层字段，避免过早固定大 schema：

```text
bub.anchor.created
  anchor_id
  method
  reason
  created_at
  state: {...}
  refs: {...}
```

扩展字段进入 `state` 或 `refs`：

```text
state:
  owner?
  summary?
  summary_status?

refs:
  source_anchor_creation_id?
  previous_anchor_id?
  thread_id?
  previous_thread_id?
  source_refs?
```

不同来源的建议：

- bootstrap Anchor：`state.owner=human`，`summary` 可为空。
- compact Anchor：`summary` 尽力从 Codex rollout compaction snapshot 提取；不可得时记录 `summary_status=parse_failed` 或 `summary_status=unavailable`。
- new-thread handoff Anchor：通常应有 summary，因为 materialization context 应包含 handoff summary。

Anchor 不是：

- Codex thread
- thread fork
- 任意阶段标记
- 普通 checkpoint
- UI timeline marker
- 必须发起一次 LLM call 的命令
- 必须切换 agent 的动作

Anchor 创建后可以暂时没有 Codex thread binding。启动或 resume 时，如果最近 Anchor 没有 thread binding，runtime 可以从该 Anchor materialize 一个新 thread。

也就是说：

```text
thread_id 决定 Codex runtime continuity。
anchor_id 决定已经提交的 LLM context materialization 边界。
```

## Anchor Creation

Anchor creation 是在 Anchor 存在之前的 attempt / transaction。

```text
anchor_creation_id = 创建 Anchor 的 attempt / transaction
anchor_id = 已提交的 context materialization boundary
```

所有 Anchor 创建都先写：

```text
bub.anchor.creation.started
  anchor_creation_id
  method: compact | new_thread
  initiator: human | bub_runtime | codex_runtime
  reason
  session_id
  tape_id
  active_anchor_id_before?
  active_thread_id_before
```

如果 Anchor 创建失败：

```text
bub.anchor.creation.failed
  anchor_creation_id
  method
  error
```

失败时没有 `anchor_id`。

## Codex Thread Context 与 Tape 的关系

Codex thread context 是运行时投影，不是事实源。

关系是一个闭环：

```text
tape facts
  -> Bub context assembler / Anchor selector
  -> Codex thread input/context

Codex thread stream/items
  -> Bub event mapper
  -> tape append
```

Anchor 不用于表达“只改变 Bub 语义归属但不改变 LLM 物理上下文”的边界。阶段标记、备注、人工 checkpoint 或 UI timeline marker 应使用其他 event type，例如：

```text
tape.marker.created
checkpoint.created
phase.changed
note.added
```

## Handoff

Handoff 是创建 Anchor 的 transition。v0 中，Handoff 创建的 Anchor 必须作为 Codex compact 或 new thread 的 context materialization boundary。

两种 v0 模式：

```text
compact handoff:
  compact same Codex thread
  create Anchor after compaction succeeds

fresh handoff:
  create Anchor
  create or bind new Codex thread from that Anchor
```

建议默认语义：

- context overflow 默认使用 `Anchor + compact`，compact 不够再 new thread。
- Codex auto compact 成功后也创建 Anchor，`initiator=codex_runtime`，`reason=auto_compact`。
- 真正的人/agent handoff 默认使用 `Anchor + new thread`。
- 普通 checkpoint 不创建 Anchor，除非它将作为 compact 或 new thread 的 materialization 起点。

## Anchor 与物理上下文控制

v0 支持两种 Anchor materialization method，但两者顺序不同：

- `new_thread`：先创建 Anchor，再从该 Anchor materialize / bind Codex thread。
- `compact`：先完成 Codex compact，再根据 compact result 创建 Anchor。

原因是 `new_thread` 需要一个明确的 context materialization 起点来构造新 thread；而 `compact` 的边界和 summary/replacement history 来自 Codex compact 结果，Anchor 应提交在 compact 成功之后。

### Anchor + Compact

```text
bub.anchor.creation.started
  anchor_creation_id
  method: compact
  initiator: human | bub_runtime | codex_runtime
  reason: user_requested | context_overflow | auto_compact | handoff
  active_thread_id_before: t1

codex.thread.compaction.started
  anchor_creation_id
  thread_id: t1
  trigger: manual | auto

codex.thread.compacted
  anchor_creation_id
  thread_id: t1
  trigger: manual | auto
  snapshot_ref?
  parse_status?

bub.anchor.created
  anchor_id: a2
  source_anchor_creation_id: anchor_creation_id
  method: compact
  initiator: human | bub_runtime | codex_runtime
  reason: user_requested | context_overflow | auto_compact | handoff
  thread_id: t1
```

效果：

- Codex 在同一个 thread 中压缩历史。
- thread_id 不变。
- compact summary / replacement history 可作为 Anchor state enrichment。
- compact 成功但 rollout summary 解析失败时，仍然创建 Anchor，并记录 `summary_status=parse_failed` 或 `summary_status=unavailable`。
- compaction snapshot 解析失败时，v0 不自动触发 Bub 自己再总结一次；只记录状态，后续由人工或显式命令补 summary。
- compact 失败时不创建 Anchor，只记录 `codex.thread.compaction.failed` 和 `bub.anchor.creation.failed`。

### Anchor + New Thread

```text
bub.anchor.creation.started
  anchor_creation_id
  method: new_thread
  active_thread_id_before: t1

bub.anchor.created
  anchor_id: a2
  source_anchor_creation_id: anchor_creation_id
  method: new_thread
  previous_thread_id: t1

bub.context.materialized
  materialization_id
  anchor_id: a2
  selected_fact_refs
  input_ref
  token_estimate?

codex.thread.bound
  anchor_id: a2
  previous_thread_id: t1
  thread_id: t2
  reason: fresh_handoff
  archived_previous: false
```

效果：

- 新 Codex thread 的初始上下文由 Bub 从 tape + Anchor state + selected facts 组装。
- Anchor 是新 thread context 的 materialization 起点。
- `bub.context.materialized` 是 new-thread 场景的必需审计事件，用于记录新 thread 初始上下文由哪些 tape facts/materials 组装而来。
- v0 不自动 archive previous thread，只记录 active binding 切换。
- 新 thread 不直接复制旧 Codex thread 的完整 history。旧 thread 可以作为 source reference，但不是新 thread 的上下文源。
- 如果 Anchor 已创建但 Codex thread bind 失败，Anchor 保留。失败的是 runtime binding，不是 Anchor boundary；后续启动时可从该 Anchor 自动重试 thread materialization。

## Runtime Context Resolution

启动或 resume 一个 Bub session 时，runtime 应先从 tape 找到最近的 committed Anchor，再查找该 Anchor 附近是否已有可用 Codex thread binding：

```text
resolve_runtime_context(session_id):
  tape = primary tape
  anchor = latest committed Anchor
  binding = latest codex.thread.bound for anchor

  if binding exists:
    resume binding.thread_id
  else:
    materialize new Codex thread from anchor
```

如果全新的 Bub session 还没有任何 Anchor，v0 应先创建 bootstrap Anchor，再从该 Anchor materialize / bind Codex thread：

```text
bub.anchor.creation.started
  method: new_thread
  reason: session_start

bub.anchor.created
  method: new_thread
  reason: session_start
  state:
    owner: human

bub.context.materialized
  source: empty_tape + initial_user_input + workspace_metadata

codex.thread.bound
  anchor_id
  reason: session_start
```

Bootstrap Anchor 的外形应尽量保持 Bub builtin 兼容：语义上对应 builtin `session/start` anchor，默认 `state.owner=human`。与 Bub builtin 一样，Anchor 先存在；差异在于 `bub-codex` 随后会显式把 Codex thread binding 写入 tape。

当最近 Anchor 没有任何 thread binding 时，v0 可以自动从该 Anchor materialize 一个新的 Codex thread，不需要用户确认。这是正常 runtime materialization，不是异常恢复：

```text
codex.thread.bound
  session_id
  tape_id
  anchor_id
  thread_id
  reason: anchor_materialization
```

如果最近 Anchor 已有 thread binding，但 Codex thread resume 失败，v0 不自动绑定新 thread，也不自动创建新 Anchor。resume 失败是异常情况，应先暴露给用户或上层 runtime：

```text
codex.thread.resume.failed
  session_id
  tape_id
  anchor_id
  thread_id
  error
```

原因是：resume 失败说明 Codex runtime instance 或本地状态异常，不等同于上下文边界需要变化。自动 materialize 新 thread 可能掩盖数据损坏、thread 丢失、SDK/runtime bug 或身份绑定错误。

## 事件归属

后续 Codex-derived tape events 应同时带上 Bub 与 Codex 身份：

```text
codex.turn.completed
  session_id
  tape_id
  anchor_id?
  thread_id
  turn_id
```

一个 Codex turn 可以跨 Anchor。比如 Codex auto compact 发生在普通 turn 中途时：

```text
turn_id = tr1

events before auto compact:
  anchor_id = a1

auto compact succeeds:
  bub.anchor.created -> a2

events after auto compact:
  anchor_id = a2
```

因此，`anchor_id` 是 event-level attribution，不是 turn-level invariant。`codex.turn.started` 可以记录 turn 开始时的 `anchor_id`，`codex.turn.completed` 可以记录 turn 结束时的 `anchor_id`，中间 item events 以事件发生时的 active Anchor 为准。

`active_anchor_id` 不作为 canonical mutable state 单独存储。默认从 tape 推导：

```text
active_anchor_id = latest committed bub.anchor.created
```

`active_thread_id` 也仅从 tape 推导：

```text
active_thread_id =
  latest codex.thread.bound
  for active_anchor_id
```

如果同一个 Anchor 下有多个 `codex.thread.bound`，v0 使用 `latest bound wins`：

```text
active_thread_id =
  latest codex.thread.bound
  where anchor_id = active_anchor_id
```

后续可以引入显式失效事件：

```text
codex.thread.unbound
codex.thread.archived
```

v0 不引入 `.bub-codex-threads.json` 这类 binding file，也不把 binding cache 作为状态源。若未来建立 projection/cache，它只能由 tape 重建，不能成为 canonical state。

这样系统可以分别回答两个问题：

```text
如何重建 Bub 事实历史？
  读取 tape facts。

当前 LLM 物理上下文边界是什么？
  查看最近成功创建的 anchor_id。

如何恢复 Codex runtime？
  resume / bind thread_id。
```

## 影响

这个模型让 Bub tape 不被 Codex thread 吞掉。Codex thread 可以被 resume、compact、fork、archive 或替换，但 Bub 的事实历史仍然存在于 tape。

同时，它让 Anchor 不被误解成“任意语义标记”。Anchor 是已提交的 LLM 物理上下文边界；是否通过 compact 或 new thread materialize，由 `method` 表达。

## 非目标

- 不把 Codex `thread_id` 当作 Bub `session_id`。
- 不把 Codex thread history 当作 canonical tape。
- 不支持 soft Anchor 作为 v0 Anchor method。
- 不把普通 checkpoint、phase marker、note 建模为 Anchor。
- 不要求每个 Handoff 都 compact。
- 不引入 rollback；错误和修正通过追加 facts 表达。

## 后续工作

- 定义 `bub.anchor.creation.started`、`bub.anchor.creation.failed`、`bub.anchor.created`、`bub.handoff.created`、`codex.thread.bound`、`codex.thread.compacted` 的 schema。
- 定义 `active_anchor_id` 的存储位置和切换规则。
- 定义 context assembler 如何从 Anchor、summary、source refs 和 recent facts 构造新 Codex thread input。
- 为 `Anchor + compact` 接入 ADR 0001 中的 rollout compaction snapshot。
