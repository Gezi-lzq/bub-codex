# Source signal、Adapter fact 与 Tape event 的边界说明

日期：2026-06-10

## 背景

当前 `bub-codex` 已经有四层事件处理视角：

```text
Codex source signal
  -> CodexFact adapter fact
  -> Bub tape event
  -> Langfuse / OTel / UI / query table projections
```

其中前三层是当前 tape schema 讨论的重点；projection 是后续从 tape 派生出去的观测或查询视图。

但 `source signal` 和 `adapter fact` 都不是 Bub 领域概念。它们更像运行时接入层的防腐边界。如果不把这两层讲清楚，后续讨论 tape schema 时容易把 Codex SDK 的形状误认为 Bub 的长期模型。

本文目标不是定最终 schema，而是用真实路径解释每一层的职责。

## 核心三层定义

### Source signal

`source signal` 是 Codex 原始发出的信号。它可能来自：

- SDK stream notification，例如 `turn/started`、`item/completed`。
- app-server request，例如 `item/tool/call`。
- rollout JSONL，例如 compacted item。
- `thread.read(include_turns=True)` 的 completed item projection。

这一层的特点：

- 由 Codex 定义。
- 形状可能跟 SDK generated model、app-server private route 或 rollout internal format 绑定。
- 对 Bub 来说是输入材料，不是 canonical history。
- 可以原样保存引用或 raw payload，但不应该直接成为 Bub 领域模型。

### Adapter fact

`adapter fact` 是 `bub-codex` 对 Codex source signal 的归一化解释。当前代码里的类型是 `CodexFact`：

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

这一层的特点：

- 仍然是 Codex-specific。
- 目的是隔离 source signal 的不稳定形状。
- 保留 `source`、`payload`、`event_id`，方便回溯。
- 不承诺成为 Bub 的最终 tape schema。
- 可以把不同来源的同类信号归一成同一种事实。

比如 SDK stream 的 `turn/started` 和以后可能从 rollout/read path 看到的 turn start，都可以归一成：

```text
codex.turn.started
```

### Tape event

`tape event` 是 Bub 认可并提交到 tape 的事实事件。

这一层的特点：

- 服务 Bub 的 canonical history、replay、audit、context reconstruction。
- 比 Langfuse trace 更细、更完整。
- 可以包含 runtime-namespaced events，例如 `codex.turn.started`。
- 也可以包含 Bub domain events，例如 `bub.anchor.created`。
- 应该带上 source refs，说明它从哪些 adapter facts 或 tape facts 推导而来。

## 示例一：普通 turn started

Source signal：

```text
SDK notification:
method = turn/started
payload.turn.id = turn_123
payload.turn.threadId = thread_abc
```

Adapter fact：

```text
CodexFact
  kind = codex.turn.started
  source = sdk_stream
  thread_id = thread_abc
  turn_id = turn_123
  payload = 原始 notification payload
```

Tape event：

```text
codex.turn.started
  payload.purpose = user_turn
  payload.source_fact_id = <CodexFact.event_id>
  thread_id = thread_abc
  turn_id = turn_123
```

这里 adapter fact 和 tape event 看起来几乎一对一，但语义不同：

- `CodexFact` 表示“Codex adapter 观察到一个 turn start 信号”。
- `TapeEvent` 表示“Bub tape 接受这是当前 session/tape 中一次 Codex turn 的开始事实”。

如果未来 Codex 把 notification method 改名，只需要改 adapter；tape event 可以保持 `codex.turn.started`。

## 示例二：dynamic tool call

Source signal：

```text
app-server request:
method = item/tool/call
params.threadId = thread_abc
params.turnId = turn_123
params.callId = call_1
params.namespace = bub
params.tool = demo_echo
params.arguments = {...}
```

Adapter fact：

```text
CodexFact
  kind = codex.dynamic_tool.requested
  source = sdk_server_request
  thread_id = thread_abc
  turn_id = turn_123
  item_id = call_1
  payload = 原始 params
```

当前 v0 还没有把这个 request fact 单独投影成 Bub tool invocation event。已实现的 tool projection 主要来自 `item/started` / `item/completed` 中的 `dynamicToolCall` item：

```text
CodexFact
  kind = codex.item.started
  payload.item.type = dynamicToolCall
```

Tape event：

```text
bub.tool.call.started
  payload.tool_call_id = <item id>
  payload.tool_kind = dynamicToolCall
  payload.tool_name = bub/demo_echo
  payload.executor = client_dynamic_tool
  payload.input_sha256 = ...
  payload.input_preview = ...
  payload.source_fact_id = <CodexFact.event_id>
```

这个例子说明一个重要点：adapter fact 可以比 tape event 更接近 Codex runtime 细节。Bub tape 不一定要把每个 adapter fact 原样提交；它可以选择更适合 Bub 审计语义的事件。

后续如果要精确审计 Bub dynamic tool provider，可以新增 Bub-side 事件，例如：

```text
bub.tool.invocation.requested
bub.tool.invocation.started
bub.tool.invocation.completed
bub.tool.invocation.failed
```

但这应该来自 Bub tool dispatcher 的事实，而不是只依赖 Codex completed item 反推。

## 示例三：compact 生成 Anchor

Source signals 可能来自两处：

```text
SDK stream:
method = item/completed
payload.item.type = contextCompaction

rollout JSONL:
type = response_item
payload.item.type = compacted
payload.message = <summary>
payload.replacement_history = [...]
```

Adapter facts：

```text
CodexFact
  kind = codex.thread.compacted
  source = sdk_stream
  thread_id = thread_abc
  turn_id = turn_123
  item_id = item_compact

CodexFact
  kind = codex.compaction.snapshot
  source = rollout
  payload.message = <summary>
  payload.message_sha256 = ...
  payload.replacement_history_len = ...
```

Tape events：

```text
bub.anchor.creation.started
  payload.method = compact
  payload.active_thread_id_before = thread_abc
  payload.source_fact_id = <codex.thread.compacted fact>

codex.thread.compacted
  payload.snapshot_ref = <codex.compaction.snapshot fact>
  payload.source_fact_id = <codex.thread.compacted fact>

codex.compaction.snapshot
  payload.message_sha256 = ...
  payload.replacement_history_len = ...
  payload.source_fact_id = <snapshot fact>

bub.anchor.created
  payload.method = compact
  payload.state.summary = <summary>
  payload.refs.source_fact_id = <codex.thread.compacted fact>
  payload.refs.snapshot_fact_id = <snapshot fact>
```

这个例子里 tape event 明显不只是 Codex signal 的转写。`bub.anchor.created` 是 Bub domain event，它表达的是：

```text
一次 compact 已经被 Bub 接受为新的 LLM context materialization boundary。
```

`codex.thread.compacted` 仍保留 Codex runtime 事实，方便以后投影、调试和追溯。

## 示例四：Anchor + new thread materialization

这条路径更能说明 source signal 和 Bub event 的差异。

先由 Bub 创建 Anchor：

```text
bub.anchor.creation.started
bub.anchor.created
```

然后 Bub 构造初始上下文：

```text
bub.context.materialized
  payload.strategy = anchor_state_plus_selected_tape_refs
  payload.selected_fact_refs = [...]
  payload.input_sha256 = ...
```

随后 Codex adapter 执行物理 materialization：

```text
thread_start
turn_start(initial materialization prompt)
wait turn/completed
thread_read(include_turns=True)
```

Codex source signals：

```text
turn/started
turn/completed
可能还有 item/started、item/completed、token usage 等
```

Adapter facts：

```text
codex.turn.started
codex.turn.completed
codex.item.started
codex.item.completed
```

Tape events：

```text
codex.turn.materialization.started
codex.turn.materialization.completed
codex.thread.bound
```

这里 `codex.thread.bound` 不是 `thread_start` 成功的转写。真实 SDK spike 已经证明：

```text
thread_start success
  != resumable Codex thread

thread_start + first turn completed
  == rollout materialized enough for resume
```

所以 `codex.thread.bound` 是 Bub 对 Codex thread 可恢复性的承诺事件。它依赖 Codex source signals，但语义高于单个 source signal。

## 为什么不能直接用 source signal 当 tape event

直接使用 source signal 会带来几个问题：

1. Bub tape 会被 Codex SDK / app-server / rollout 内部格式牵引。
2. 同一个事实可能来自 stream、read、rollout 多条路径，难以去重和比较。
3. Bub domain event，例如 `bub.anchor.created`，无法自然表达。
4. 未来接入其他 agent core 时，会把 Codex 的事件形状误当成通用模型。
5. Projection 层容易反向污染 canonical history，例如把 Langfuse 的 `generation/tool/agent` 当成 tape ontology。

## 当前最小原则

v0 可以先遵循这些原则：

1. `source signal` 保留原始来源和 payload，用于调试与追溯。
2. `CodexFact` 只做 Codex-specific 归一化，不假装通用。
3. `TapeEvent` 是 Bub 接受的事实，必须有稳定 type、identity refs 和来源 refs。
4. Bub domain event 只在语义已经清楚时引入，例如 `bub.anchor.created`、`bub.context.materialized`。
5. runtime-specific event 可以进入 tape，例如 `codex.turn.started`、`codex.thread.bound`。
6. 不急于定义跨所有 Agent core 的通用事件 vocabulary。
7. Langfuse / OTel / UI 都是 projection，可以聚合、截断、脱敏，不反向定义 tape。

## 对 ADR 0006 的含义

ADR 0006 应该讨论的是事件合同分层和 namespace，而不是过早统一所有 agent event。

推荐表达：

```text
Allowed:
  bub.*
  codex.*
  future_runtime.*

Not required in v0:
  universal agent event ontology
  every CodexFact mapped one-to-one into tape
  Langfuse observation type as Bub event type
```

更准确的方向是：

```text
先允许不同 runtime 暴露自己的 source-attributed facts。
当多个 runtime 的重复语义稳定后，再提炼 bub.* canonical event。
```
