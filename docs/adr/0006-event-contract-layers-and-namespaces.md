# ADR 0006: Event Contract Layers and Namespaces

日期：2026-06-10

## 状态

Accepted

## 背景

`bub-codex` 需要把 Codex runtime 的行为写入 Bub tape，同时保留未来接入其他 Agent core 的空间。

前序调研和 spike 已经确认：

- Bub tape 是 canonical fact record，不是 Langfuse trace、OTel span、Obelisk query table 或 Codex rollout JSONL。
- Codex SDK/app-server/rollout 会产生不同形状的 source signals。
- `CodexFact` 可以把这些 Codex-specific source signals 归一成 adapter facts。
- 部分 adapter facts 可以直接投影成 runtime-namespaced tape events，例如 `codex.turn.started`。
- 部分 Bub 语义需要自己的 domain events，例如 `bub.anchor.created`、`bub.context.materialized`。
- Langfuse 的 Agent trace 粒度是 observability projection，适合调试和评估，但不应反向定义 Bub tape 粒度。

当前需要决定的不是完整 tape schema，而是事件合同的分层和 namespace 原则。

## 决策

采用四层事件合同视角：

```text
source signal
  -> adapter fact
  -> tape event
  -> projection
```

### Source signal

Source signal 是运行时原始输入，例如：

```text
Codex SDK notification
Codex app-server request
Codex rollout JSONL item
Codex thread.read completed item
```

Source signal 由具体 runtime 定义。Bub 可以保存其引用、hash、raw payload 或解析状态，但不把 source signal 的原始 shape 当作 Bub tape schema。

### Adapter fact

Adapter fact 是 runtime-specific 防腐层。

Codex 当前使用 `CodexFact`：

```text
CodexFact
  kind
  event_id
  source
  payload
  thread_id?
  turn_id?
  item_id?
  occurred_at?
```

Adapter fact 的职责是：

- 隔离 SDK generated types、private app-server route、rollout internal format。
- 对同一 runtime 的多种来源做归一化。
- 保留 source attribution，便于回溯和调试。
- 为 tape projection 提供稳定输入。

Adapter fact 不承诺成为通用 Agent event model。

### Tape event

Tape event 是 Bub 接受并提交到 tape 的事实。

Tape event 可以分为两类：

```text
bub.*      Bub domain events
codex.*    Codex runtime-attributed events
```

允许把 runtime-namespaced events 写入 Bub tape。比如：

```text
codex.turn.started
codex.turn.completed
codex.thread.compacted
codex.thread.bound
codex.compaction.snapshot
```

当语义已经属于 Bub domain，并且不应该绑定到某个 runtime 时，使用 `bub.*`。比如：

```text
bub.anchor.creation.started
bub.anchor.created
bub.context.materialized
bub.tool.call.started
bub.tool.call.completed
bub.side_effect.completed
```

`bub.*` 事件不应只是把 Codex 字段换个名字。它必须表达 Bub 愿意长期承诺的语义。

### Projection

Projection 是从 tape 或 adapter facts 派生的观测、查询或展示视图，例如：

```text
Langfuse session / trace / observation tree
OpenTelemetry trace/span
UI timeline
Obelisk-style query table
evaluation dataset
training artifact
```

Projection 可以聚合、截断、脱敏、重排成树状视图。Projection 不反向定义 tape 的 canonical event 粒度。

## Namespace 原则

### 允许 runtime-specific events

不同 Agent core 有不同 runtime event 是正常的。v0 不要求所有 runtime 都被压进统一的 `bub.agent.*` ontology。

例如 Codex 可以有：

```text
codex.turn.materialization.started
codex.thread.bound
codex.compaction.snapshot
```

未来另一个 runtime 可以有自己的 namespace：

```text
other_runtime.session.resumed
other_runtime.memory.compacted
other_runtime.plan.updated
```

这些事件可以共存于 Bub tape，只要它们带有清楚的 source attribution、identity refs 和 projection 规则。

### 慎重引入 generic Bub events

通用 Bub events 应该从多个 runtime 的重复语义中提炼，而不是在第一个 Codex adapter 中预设完整 ontology。

当前可以使用 `bub.*` 的场景：

- Anchor / context materialization 是 Bub 对 LLM context lifecycle 的领域定义。
- Bub tool call / side effect projection 是 Bub 对可审计外部动作的领域定义。
- Bub session/tape identity 是 Bub 自己的 runtime identity。

当前不急于定义：

```text
bub.agent.step.*
bub.llm.generation.*
bub.runtime.thread.*
```

这些概念需要等更多 Agent core 接入后再判断共性。

### Source attribution 必须保留

从 adapter fact 投影到 tape event 时，应保留来源引用，例如：

```text
payload.source_fact_id
payload.refs.snapshot_fact_id
payload.refs.materialization_turn_id
thread_id
turn_id
item_id / tool_call_id
```

这保证 tape event 既是 Bub canonical fact，又能追溯到 runtime source signal。

## 示例

### Codex turn

```text
source signal:
  SDK notification method=turn/started

adapter fact:
  codex.turn.started

tape event:
  codex.turn.started
    purpose=user_turn
    source_fact_id=<fact>

projection:
  Langfuse generation / agent observation 的一部分
```

### Compact Anchor

```text
source signals:
  item/completed contextCompaction
  rollout compacted item

adapter facts:
  codex.thread.compacted
  codex.compaction.snapshot

tape events:
  bub.anchor.creation.started
  codex.thread.compacted
  codex.compaction.snapshot
  bub.anchor.created
```

这里 `bub.anchor.created` 是 Bub domain event，不是 Codex source signal 的重命名。

### New-thread materialization

```text
Bub creates:
  bub.anchor.created
  bub.context.materialized

Codex emits:
  turn/started
  turn/completed

Adapter emits:
  codex.turn.started
  codex.turn.completed

Tape records:
  codex.turn.materialization.started
  codex.turn.materialization.completed
  codex.thread.bound
```

`codex.thread.bound` 表示 Codex thread 已经 materialized enough to resume。它不是 `thread_start` 成功的直接转写。

## 后果

- Bub tape 可以记录 Codex-specific facts，而不假装它们是通用 Agent events。
- Bub domain events 的引入门槛更高，避免过早抽象。
- Codex adapter 可以随着 SDK/app-server/rollout shape 变化而调整，不迫使 Bub tape 跟着改。
- Langfuse/OTel/UI 可以从 tape 派生，而不是把观测模型反向施加给 canonical history。
- 未来适配更多 runtimes 后，可以从实际重复模式中提炼稳定 `bub.*` vocabulary。

## 非目标

- 不定义完整 Bub tape schema。
- 不定义所有 Agent core 的统一 event ontology。
- 不要求每个 `CodexFact` 都一对一写入 tape。
- 不要求 Langfuse observation type 与 Bub tape event type 对齐。
- 不把 Codex rollout JSONL、SDK generated types 或 app-server JSON-RPC method names作为 Bub domain model。

## 后续

- 检查现有 `codex.*` 与 `bub.*` 命名，避免把 Codex-specific 语义伪装成 Bub generic event。
- 为 Bub dynamic tool dispatcher 增加 host-side invocation audit events。
- 继续收敛 context assembler 的 `input_ref` / `input_sha256` / `selected_fact_refs` 语义。
- 未来接入第二个 Agent core 后，重新评估哪些事件可以提炼成稳定 `bub.*` vocabulary。

