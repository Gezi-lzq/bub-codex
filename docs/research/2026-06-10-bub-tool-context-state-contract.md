# Bub ToolContext state 最小契约

日期：2026-06-10

## 背景

`bub-codex` 需要把 Bub tools 暴露给 Codex dynamic tools。Codex 模型只应该看到工具业务参数；Bub runtime 需要在实际执行工具时补充可信运行时上下文。

前一轮实现曾考虑引入新的 `BubToolContext`。调研 Bub builtin 后，方向需要修正：Bub 已经通过 Republic `ToolContext(tape, run_id, state)` 给 `context=True` 工具注入上下文。`bub-codex` 应优先兼容这个形状，而不是发明新 context 类型。

## Bub builtin 观察

Bub tool 的已有路径是：

```text
@tool(...)
  -> republic.Tool
  -> bub.tools.REGISTRY
  -> model_tools() 将 dotted registry name 转为 model-facing name
  -> Republic ToolExecutor / Tool.run(...)
  -> context=True 时注入 ToolContext
```

`ToolContext` 的形状是：

```text
ToolContext
  tape
  run_id
  state
```

Builtin tools 已经使用：

```text
context.tape
context.run_id
context.state["_runtime_workspace"]
context.state["session_id"]
context.state["_runtime_agent"]
context.state["allowed_skills"]
context.state["allowed_tools"]
```

这说明 `state` 是 per-turn runtime dict，允许 runtime 注入 `_runtime_*` 字段。

## v0 候选字段

`bub-codex` 为 Codex dynamic tool call 构造 ToolContext 时，v0 候选字段如下：

```text
context.tape
  = tape_id / tape name

context.run_id
  = turn_id if available else tool_call_id else "codex_dynamic_tool"

context.state.session_id
  = Bub session identity

context.state._runtime_workspace
  = cwd / workspace root

context.state._runtime_anchor_id
  = active Anchor attribution for this event, if known

context.state._runtime_thread_id
  = Codex thread id from DynamicToolCall, if known

context.state._runtime_turn_id
  = Codex turn id from DynamicToolCall, if known

context.state._runtime_tool_call_id
  = Codex dynamic tool call id
```

当前实现：

```text
src/bub_codex/bub_tools.py
  make_bub_tool_context(...)
```

如果环境中有 `republic`，返回真实 `ToolContext`；否则返回同字段的 `ToolContextLike`，用于 spike。

## 字段分层

### 已有 Bub 语义

这些字段沿用 Bub / Republic：

```text
context.tape
context.run_id
context.state
context.state.session_id
context.state._runtime_workspace
```

### Codex runtime attribution

这些字段是 `bub-codex` 特有的 runtime attribution：

```text
_runtime_anchor_id
_runtime_thread_id
_runtime_turn_id
_runtime_tool_call_id
```

它们不应该进入 Codex dynamic tool schema，也不应该由模型生成。它们只由 Bub host runtime 基于当前 tape/runtime state 和 `DynamicToolCall` 注入。

### 暂不稳定化

这些不应在 v0 直接承诺：

```text
_runtime_agent
allowed_skills
allowed_tools
tape append/read capabilities
cancellation token
timeout controller
approval policy
```

原因：

- `_runtime_agent` 是 Bub builtin 内部用法，`bub-codex` 当前还没有真实 Bub Agent 实例。
- `allowed_skills` / `allowed_tools` 属于 turn admission / policy 层，v0 还没有收敛。
- tape append/read 能力不应作为随手塞进 context 的 capability object；已有 Bub builtin 工具通过 `context.tape` 和 runtime agent/service 访问 tape。
- timeout、cancellation、approval 是 runtime control plane，不应混入第一版 context data contract。

## 与 tool audit 的关系

ToolContext 是给工具执行用的可信上下文。

`BubToolInvocationAuditRecord` 是工具执行后的审计事实。

二者分工不同：

```text
ToolContext:
  handler input, not tape event

BubToolInvocationAuditRecord:
  host-side execution fact, can project to tape
```

当前已验证：

```text
Codex DynamicToolCall
  -> make_bub_tool_context(...)
  -> Tool.run(..., context=context)
  -> BubToolInvocationAuditRecord
  -> bub.tool.invocation.started/completed/failed
```

## Spike 验证

新增：

```text
scripts/spikes/bub_republic_tool_context_adapter_spike.py
```

该 spike 用一个 fake Republic-like tool 验证 provider 会优先调用 `Tool.run()`，并且 `context=True` 工具能收到：

```text
context.tape = tape_1
context.run_id = turn_1
context.state.session_id = session_1
context.state._runtime_workspace = /workspace
context.state._runtime_anchor_id = anchor_1
context.state._runtime_thread_id = thread_1
context.state._runtime_turn_id = turn_1
context.state._runtime_tool_call_id = call_1
```

同时该调用仍然产生：

```text
bub.tool.invocation.started
bub.tool.invocation.completed
```

## 当前结论

v0 先采用：

```text
Republic-compatible ToolContext
  + minimal runtime attribution in state
  + no new BubToolContext type
  + no capability object in context
```

后续接入真实 Bub runtime 后，再决定哪些 `_runtime_*` 字段进入正式 ADR。

