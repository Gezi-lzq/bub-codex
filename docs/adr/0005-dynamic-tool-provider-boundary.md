# ADR 0005: Dynamic Tool Provider Boundary

日期：2026-06-10

## 状态

Accepted

## 背景

`bub-codex` 的目标是让 Codex 作为 Bub-native coding runtime，而不是只让 Bub 观察 Codex 最终文本。工具调用边界是关键：Bub 需要能观察 Codex 内置工具，也需要有能力把 Bub tools 暴露给 Codex。

前序 spike 已验证：

- `commandExecution` 和 `fileChange` 可通过 SDK/app-server item lifecycle 感知。
- `mcpToolCall`、`dynamicToolCall`、`collabAgentToolCall` 可以统一投影到 `bub.tool.call.*`。
- live dynamic tool 调用可通过低层 `CodexClient` 实现。

## 决策

引入 `bub_codex.codex_client` 和 `bub_codex.bub_tools` 两层 adapter boundary：

- `codex_client` 隔离 Codex app-server dynamic tool 协议细节。
- `bub_tools` 把 Bub registry tools materialize 成 Codex dynamic tools。

Domain/runtime 上层不直接构造 raw app-server JSON：

```text
dynamicTools
item/tool/call
contentItems
approval_handler
```

这些细节由 adapter 负责转换。

当前最小类型：

```text
DynamicToolSpec
DynamicToolCall
DynamicToolResult
DynamicToolDispatcher
ThreadStartOptions
```

当前 Bub tool provider 最小协议：

```text
name
description
parameters
handler
context
```

不在 `bub-codex` 中重新定义 Bub tool registry。Bub registry 是工具定义源；`bub-codex` 只负责暴露、调度和记录。

命名规则：

```text
namespace = bub
Bub registry name = demo.echo
Codex dynamic tool name = demo_echo
reverse mapping = demo_echo -> demo.echo
```

`bub-codex` 必须保留 Codex name 到 Bub registry name 的映射，并在启动时检测映射冲突。

`ToolContext` 不进入 Codex tool schema。Codex dynamic tool schema 只暴露模型可生成的 arguments；Bub host runtime 从 `DynamicToolCall` 和 session/tape/thread/anchor 状态构造 context，并在 `context=True` 时注入 Bub handler。

handler failure 不抛穿到 app-server transport。v0 返回：

```text
success=false
contentItems[0].type=inputText
contentItems[0].text="<ExceptionType>: <message>"
```

这样 Codex runtime 可以继续把失败记录为 `dynamicToolCall` item lifecycle，Bub tape 再投影为 failed tool event。

## 已验证路径

低层 live spike：

```text
scripts/spikes/dynamic_tool_low_level_spike.py
```

运行路径：

```text
ThreadStartOptions(dynamic_tools=(DynamicToolSpec(...),))
  -> CodexClient.thread_start(raw dict)
  -> app-server thread/start dynamicTools

item/tool/call server request
  -> DynamicToolDispatcher.handle_server_request
  -> DynamicToolResult
  -> app-server response

item/started dynamicToolCall
item/completed dynamicToolCall
  -> CodexFact
  -> bub.tool.call.started/completed
```

实测 artifact：

```text
artifacts/spikes/dynamic-tool-low-level-20260610-131255/
```

结果：

```text
codex.dynamic_tool.requested: 1
bub.tool.call.started dynamicToolCall bub/echo
bub.tool.call.completed dynamicToolCall bub/echo
```

Bub registry adapter spike：

```text
scripts/spikes/bub_tool_registry_adapter_spike.py
```

该 spike 不引入真实 Bub 依赖，只使用 Bub `Tool` 的最小协议形状：

```text
name
description
parameters
handler
context
```

映射规则：

```text
Bub tool name: demo.echo
Codex namespace/tool: bub/demo_echo
reverse mapping: demo_echo -> demo.echo
```

实测返回：

```text
contentItems:
  - type: inputText
    text: "{\"echo\": \"hello\"}"
success: true
```

这说明 dynamic tool provider boundary 可以继续保持 Bub-native：Bub registry 负责定义工具，`bub-codex` 只负责把它们 materialize 成 Codex dynamicTools，并在 server request 到达时调回 Bub handler。

随后该 spike 扩展为 executable spec，覆盖：

```text
sync handler -> success=true
async handler -> success=true
context=True + context_factory -> success=true
handler exception -> success=false + inputText(error)
name collision -> startup ValueError
```

其中 `context=True` 的当前语义是：adapter 从 `DynamicToolCall` 构造 Bub-side context，并以 `context=` 传给 Bub handler。v0 不把 `ToolContext` 设计进 Codex schema；Codex 只负责发起 tool arguments，Bub context 是 host runtime 注入。

async handler 当前通过同步 dispatcher 桥接：如果 handler 返回 awaitable，且当前线程没有 running event loop，则用 `asyncio.run()` 执行。这与低层 `CodexClient` 的同步 `approval_handler` 路径匹配；如果未来改成 async transport，需要单独引入 async dispatcher，而不是在 running event loop 内阻塞执行。

## 理由

这个边界让 Bub tools 可以作为 Codex dynamic tools 暴露，同时避免 Bub domain model 依赖 raw JSON-RPC method names。

`namespace=bub` 比把 `bub_` 拼进 tool name 更清晰：namespace 表达 provider，tool name 保留 Bub model-facing alias。反向映射让 tape 和 runtime 仍能回到 Bub registry 原名。

failure-as-result 更符合 tape 价值：失败是 runtime fact，应该可观察、可投影、可回放，而不是变成 transport exception 后丢失在边界外。

context host-injected 保持了 tool schema 的纯度：模型只负责业务参数，runtime context 由 Bub 控制，不暴露给模型伪造。

## 后果

- Bub 可以作为 Codex dynamic tool provider。
- Codex dynamic tool request/response 细节被压在 adapter 层。
- Bub domain model 不依赖 raw app-server JSON-RPC method names。
- `dynamicToolCall` item lifecycle 仍是 Bub tape projection 的主感知点。
- v0 仍采用最大权限运行；approval UX / policy engine 不进入本 ADR。
- 同步 dispatcher 只支持无 running event loop 的 awaitable 桥接；未来 async client 需要独立 async dispatcher。

## 非目标

- 不在本 ADR 设计完整 Bub tool registry。
- 不在本 ADR 设计 approval policy。
- 不把 high-level `AsyncCodex.thread_start()` 当作 dynamicTools 主路径；当前高层 API 没有暴露该参数。
- 不要求所有 dynamic tool 输出都内联进 tape；tool projection 仍应使用 hash/ref/preview 策略。
- 不在同步 dispatcher 中解决 async event loop re-entry；这是未来 async client 边界的问题。

## 后续

- 为 dynamic tool request / response 增加更完整的 tape projection。
- 定义 dynamic tool failure / timeout 的 Bub event shape。
- 决定真实 Bub `ToolContext` 需要包含哪些 session/tape/thread/anchor 字段。
- 决定是否给 Bub dynamic tool handler 增加统一 timeout/cancellation 包装。
