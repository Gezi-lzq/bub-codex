# Codex SDK tool event 感知 spike

日期：2026-06-10

## 问题

需要确认使用 Codex SDK / app-server stream 时，Bub 是否能感知 Codex runtime 内部发生的工具调用。

如果只能看到最终 assistant message，Bub tape 会漏掉代码 agent 最关键的执行事实：命令、文件变更、MCP 调用、协作工具、搜索与结果。

## app-server 文档观察

`/tmp/bub-codex-sources/openai-codex/codex-rs/app-server/README.md` 明确说明：

- item 表示 turn 中持久化并用于未来上下文的单位，示例包括 shell command、file edit 等。
- turn stream 会发出 `item/started`、`item/completed`、agent message delta、tool progress 等。
- `ThreadItem` 支持：
  - `commandExecution`
  - `fileChange`
  - `mcpToolCall`
  - `collabToolCall`
  - `webSearch`
  - `imageView`
  - 以及 message / reasoning / compaction 等非工具 item
- `item/completed` 是工具执行/result state 的 authoritative lifecycle event。

这说明 SDK/app-server 的正确感知点是 item lifecycle，而不是最终自然语言回复，也不是 rollout JSONL。

## Artifact 观察

在现有 spike artifact：

```text
artifacts/spikes/codex-sdk-harness-20260610-030158/normalized-facts.jsonl
```

统计结果：

```text
commandExecution item: 6
```

这些来自 3 个命令，每个命令都有 started/completed 两个 lifecycle facts。

样本 item 字段包括：

```text
type=commandExecution
id
command
cwd
status
source
commandActions
aggregatedOutput
exitCode
durationMs
```

## Spike 实现

新增：

```text
src/bub_codex/tool_projection.py
scripts/spikes/project_tool_events_spike.py
```

当前先投影 tool-like item：

```text
commandExecution
mcpToolCall
dynamicToolCall
collabAgentToolCall
webSearch
imageView
```

并把 `fileChange` 作为 side-effect item 保留入口。

输出事件：

```text
bub.tool.call.started
bub.tool.call.completed
bub.tool.call.failed
bub.side_effect.started
bub.side_effect.completed
bub.side_effect.failed
```

当前样本输出：

```text
bub.tool.call.started: 3
bub.tool.call.completed: 2
bub.tool.call.failed: 1
```

全部来自 `commandExecution`。

## FileChange 实测

继续跑了一个真实 Codex SDK turn，隔离 workspace：

```text
/tmp/bub-codex-filechange-workspace
```

任务是创建：

```text
hello.txt
```

内容为：

```text
hello from codex filechange spike.
```

生成 artifact：

```text
artifacts/spikes/codex-sdk-harness-20260610-113011/
```

结果确认 stream 中出现：

```text
fileChange item: 2
turn/diff/updated: 4
item/commandExecution/outputDelta: 5
```

`fileChange` 的 started/completed item 都包含：

```text
changes:
  - path: /tmp/bub-codex-filechange-workspace/hello.txt
    kind:
      type: add
    diff: "hello from codex filechange spike.\n"
status: inProgress | completed
```

投影结果：

```text
bub.tool.call.started: 2
bub.tool.call.completed: 2
bub.side_effect.started: 1
bub.side_effect.completed: 1
```

其中 side effect 来自 `fileChange`。

这个样本还观察到一次可重试的 stream reconnect error：

```text
method=error
willRetry=true
```

turn 最终成功完成，因此这类 error 应作为 observability / resilience fact 记录，不应等同于 turn failure。

## 结论

需要这个 spike，而且结论是正向的：Codex SDK/app-server stream 可以通过 `item/started` / `item/completed` 感知工具调用与文件变更生命周期。

对 `bub-codex` 来说，下一步不是重新发明工具执行系统，而是把 Codex item lifecycle 投影成 Bub tape facts：

```text
Codex item lifecycle
  -> CodexFact
  -> Bub tool / side-effect tape events
```

这样 Bub tape 仍然是 canonical fact record；Codex thread 只是 runtime context handle。

## 暂不决定

- 不决定最终 tool schema。
- 不接管 Codex 工具执行。
- 不实现 approval policy；v0 仍按最大权限策略。
- 不把所有 output 全量塞进 event；当前 spike 使用 hash + preview，后续应切到 `input_ref` / `output_ref`。

## 后续

- 构造 dynamic tool / MCP tool 样本，验证 `mcpToolCall` 与 server request 的关系。
- 将 tool events 纳入 context materialization 的 `selected_fact_refs` 候选。

## Tool refs 与 Handoff Anchor

继续新增：

```text
scripts/spikes/handoff_with_tool_refs_spike.py
```

该 spike 从 tool projection 输出中选择 completed/failed tool 与 side-effect events：

```text
bub.tool.call.completed
bub.tool.call.failed
bub.side_effect.completed
bub.side_effect.failed
codex.turn.diff.updated
```

并把它们写入 new-thread Anchor：

```text
bub.anchor.created.refs.source_event_refs
```

实测 fileChange artifact 中选出的 refs 是：

```text
bub.tool.call.completed shell_command
bub.side_effect.completed fileChange
bub.tool.call.completed shell_command
```

随后 `bub.context.materialized.selected_fact_refs` 会包含：

```text
anchor event id
source_event_refs...
```

这个关系很重要：handoff summary 可以是人读的压缩状态，但 Anchor refs 让新 thread 的 materialization audit 仍能追溯到底层执行事实。

## Dynamic / MCP / Collab 工具

源码和 app-server 文档显示：

- `mcpToolCall` 是普通 `ThreadItem`，字段包含 `server`、`tool`、`arguments`、`result`、`error`、`durationMs`。
- `dynamicToolCall` 也是 `ThreadItem`，字段包含 `namespace`、`tool`、`arguments`、`contentItems`、`success`、`durationMs`。
- dynamic tool live flow 还包含一个 server-initiated JSON-RPC request：`item/tool/call`。app-server 文档给出的顺序是：

```text
item/started dynamicToolCall
item/tool/call request to client
client response
item/completed dynamicToolCall
```

当前 Python SDK 高层 `AsyncCodex.thread_start()` 没有暴露 `dynamicTools` 参数，generated `ThreadStartParams` 里也没有该字段。不过底层 `CodexClient.thread_start()` 接受 raw dict，并会原样发送 `thread/start` params；SDK reader thread 也会把 server-initiated JSON-RPC request 交给 `approval_handler`。

为先验证 Bub 投影边界，新增 synthetic spike：

```text
scripts/spikes/synthetic_tool_projection_spike.py
```

它覆盖：

```text
mcpToolCall started/completed
dynamicToolCall started/completed
collabAgentToolCall completed
```

结论：这些 item 都可以投影为同一种 Bub tool call event family；区别主要在 `tool_kind`、`tool_name`、`executor`、input/output payload shape。

synthetic 输出：

```text
bub.tool.call.started mcpToolCall docs/search client_or_plugin_tool
bub.tool.call.completed mcpToolCall docs/search client_or_plugin_tool
bub.tool.call.started dynamicToolCall bub/lookup_anchor client_dynamic_tool
bub.tool.call.completed dynamicToolCall bub/lookup_anchor client_dynamic_tool
bub.tool.call.completed collabAgentToolCall spawn_agent client_or_plugin_tool
```

因此 Bub tape 层可以先统一为 tool call event family；dynamic tool 的 live 执行则需要后续低层 app-server spike，重点处理 `item/tool/call` server request / response。

## Live dynamic tool spike

继续新增：

```text
scripts/spikes/dynamic_tool_low_level_spike.py
```

该 spike 使用底层 `CodexClient`：

```text
thread/start raw params:
  dynamicTools:
    - namespace: bub
      name: echo

approval_handler:
  item/tool/call -> DynamicToolCallResponse
```

实测 artifact：

```text
artifacts/spikes/dynamic-tool-low-level-20260610-123754/
```

server request：

```text
method=item/tool/call
params:
  namespace=bub
  tool=echo
  arguments:
    message=hello dynamic tool
```

turn stream：

```text
item/started dynamicToolCall status=inProgress
item/completed dynamicToolCall status=completed success=true
```

投影结果：

```text
bub.tool.call.started dynamicToolCall bub/echo
bub.tool.call.completed dynamicToolCall bub/echo
```

归一化 facts 中也新增：

```text
codex.dynamic_tool.requested
```

结论：Bub 可以通过底层 Codex SDK/app-server path 作为 dynamic tool provider。高层 `AsyncCodex` API 暂时不够，但不需要重写 app-server transport；可以包装底层 `CodexClient` / `AsyncCodexClient._sync` 这一层。

随后新增 adapter：

```text
src/bub_codex/codex_client.py
```

最小边界：

```text
DynamicToolSpec
DynamicToolCall
DynamicToolResult
DynamicToolDispatcher
ThreadStartOptions
```

并把 live spike 改为使用该 adapter，而不是在脚本里手写 raw `dynamicTools` 和 `item/tool/call` handler。重跑 artifact：

```text
artifacts/spikes/dynamic-tool-low-level-20260610-131255/
```

结果仍然成立：

```text
server request: item/tool/call bub/echo
normalized fact: codex.dynamic_tool.requested
projection: bub.tool.call.started/completed dynamicToolCall bub/echo
```

这说明我们可以把 raw app-server dynamic tool 细节压在 adapter 层，不泄露到 runtime domain model。

## Bub tool registry adapter spike

继续新增：

```text
src/bub_codex/bub_tools.py
scripts/spikes/bub_tool_registry_adapter_spike.py
```

目标是验证 Bub registry 到 Codex dynamic tool provider 的最小桥接，而不是直接依赖 Bub 包。Bub 当前内置工具形状来自：

```text
/tmp/bub-codex-sources/bub/src/bub/tools.py
```

关键观察：

- Bub `REGISTRY` 保存 `dict[str, Tool]`。
- `Tool` 来自 `republic`，至少包含 `name`、`description`、`parameters`、`handler`、`context`。
- Bub 自己的 `model_tools()` 已经把 `.` 映射成 `_`，例如 `web.search -> web_search`。

因此 adapter 采用协议式接入：

```text
BubToolLike:
  name
  description
  parameters
  handler
  context
```

而不是 import Bub runtime。

映射规则：

```text
namespace = bub
Bub registry name = demo.echo
Codex dynamic tool name = demo_echo
reverse mapping = demo_echo -> demo.echo
```

需要启动时检测 collision，例如：

```text
a.b -> a_b
a_b -> a_b
```

实测 spike 输出：

```text
spec:
  namespace: bub
  name: demo_echo
  inputSchema: 原 Bub parameters

response:
  contentItems:
    - type: inputText
      text: "{\"echo\": \"hello\"}"
  success: true
```

随后把 spike 扩展为 executable spec，覆盖：

```text
demo.echo      -> sync handler success
demo.async     -> async handler success
demo.context   -> context=True receives thread_id/turn_id context
demo.fail      -> handler exception returns success=false
demo.collision + demo_collision -> startup collision error
```

输出确认：

```text
codex_to_bub_name:
  demo_echo: demo.echo
  demo_async: demo.async
  demo_context: demo.context
  demo_fail: demo.fail

failure:
  success: false
  text: "ValueError: bad message: hello failure"

collision_error:
  both map to 'demo_collision'
```

`ToolContext` 的当前 spike 结论：context 不应该进入 Codex tool schema。Codex 只提供 model-generated arguments；Bub host runtime 用 `DynamicToolCall` 的 `thread_id`、`turn_id`、以及 session/tape/anchor 信息构造 Bub/Republic-compatible `ToolContext(tape, run_id, state)`，再注入 Bub handler。

async handler 的当前 spike 结论：在低层同步 `approval_handler` 路径中可以桥接 awaitable handler；但如果未来使用 async transport，需要一个 async dispatcher，而不是复用当前同步 dispatcher。

结论：Bub 可以作为 Codex dynamic tool provider，而 `bub-codex` 不需要接管 Bub tool registry 的定义权。边界应保持为：

```text
Bub registry Tool
  -> BubDynamicToolProvider
  -> DynamicToolSpec + DynamicToolDispatcher
  -> Codex app-server dynamicTools / item/tool/call
```

随后新增 `scripts/spikes/bub_republic_tool_context_adapter_spike.py`，验证：

```text
FakeRepublicTool.run(...)
  receives context.tape = tape_1
  receives context.run_id = turn_1
  receives context.state.session_id = session_1
  receives context.state._runtime_workspace = /workspace
  receives context.state._runtime_anchor_id = anchor_1
  receives context.state._runtime_thread_id = thread_1
  receives context.state._runtime_turn_id = turn_1
  receives context.state._runtime_tool_call_id = call_1
```

这说明 `bub-codex` 不应该优先发明新的 `BubToolContext` 类型。更好的方向是兼容 Bub builtin 已经使用的 Republic `ToolContext(tape, run_id, state)`。当前实现中，如果环境没有安装 `republic`，`make_bub_tool_context()` 会返回同字段的 `ToolContextLike` stand-in，便于 spike；真实 Bub runtime 环境中应返回 Republic `ToolContext`。

暂未解决：

- `state` 中哪些 `_runtime_*` 字段应成为长期稳定契约。
- handler timeout / cancellation。

## Bub dynamic tool host-side audit

ADR 0006 之后，dynamic tool 的 Bub host-side 执行需要和 Codex item lifecycle 分层：

```text
Codex item lifecycle:
  dynamicToolCall item
  -> CodexFact codex.item.started/completed
  -> bub.tool.call.started/completed/failed

Bub host-side handler execution:
  DynamicToolDispatcher -> Bub tool handler
  -> BubToolInvocationAuditRecord
  -> bub.tool.invocation.started/completed/failed
```

两者表达的不是同一层事实：

- `bub.tool.call.*` 表达 Codex 运行时 / 模型视角的工具调用生命周期。
- `bub.tool.invocation.*` 表达 Bub host runtime 实际执行 Bub handler 的审计事实。

已扩展 `scripts/spikes/bub_tool_registry_adapter_spike.py` 验证：

```text
demo.echo    -> bub.tool.invocation.started/completed
demo.async   -> bub.tool.invocation.started/completed
demo.context -> bub.tool.invocation.started/completed
demo.fail    -> bub.tool.invocation.started/failed
```

投影后的 tape event payload 使用 hash/preview 策略记录 arguments/output，并保留：

```text
tool_call_id
namespace
codex_tool_name
bub_tool_name
thread_id
turn_id
error_type
error_message
```

这使 dynamic tool request/response 不再是“只依赖 Codex item lifecycle”或“另起一套替代事件”的二选一。v0 采用双层记录：

```text
Codex lifecycle for model/runtime observation
Bub invocation audit for host execution accountability
```
