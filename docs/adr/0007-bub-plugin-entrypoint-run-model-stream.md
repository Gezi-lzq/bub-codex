# ADR 0007: Bub Plugin Entry Point Uses run_model_stream

日期：2026-06-10

## 状态

Accepted

## 背景

`bub-codex` 的目标是把 Codex 作为 Bub-native coding runtime，而不是通过 `codex e` 子进程包装。

前序 spike 已经验证：

- Codex SDK/app-server 暴露 turn notification stream。
- Codex thread materialization / resume 需要通过真实 turn 完成后才算可绑定。
- Tool lifecycle、side effect、dynamic tool request、token usage、errors 都更适合从 stream 捕获。
- Bub `HookRuntime` 支持 `run_model` / `run_model_stream` 互相 fallback。
- Bub 插件注册在 builtin 之后，可以覆盖 builtin model execution hook。

因此需要确定 `bub-codex` 在 Bub hook pipeline 中的第一入口。

## 决策

v0 Bub 插件只实现：

```text
run_model_stream(prompt, session_id, state) -> AsyncStreamEvents
```

不同时实现独立的 `run_model`。

Bub 非 streaming 调用由 `HookRuntime.run_model()` 消费 `run_model_stream` 的 `text` events 聚合为 plain output。

## 设计约束

### 不覆盖非必要 hooks

v0 不覆盖：

```text
load_state
build_prompt
system_prompt
render_outbound
dispatch_outbound
```

这些仍由 Bub builtin 或其他插件负责。`bub-codex` 第一阶段只替换 model execution boundary。

理由：

- `load_state` 已负责提供 `_runtime_workspace`、`_runtime_steering`，builtin 还提供 `session_id` 与 `_runtime_agent`。
- `build_prompt` 已处理 channel message、comma command、media prompt shape。
- outbound hooks 属于 channel/output surface，不属于 Codex runtime adapter 的最小边界。

### Comma commands 回退给 builtin

如果 prompt 是以 `,` 开头的 operator comma command，`bub-codex` 不交给 Codex runtime，而是回退给 builtin agent：

```text
if prompt.strip().startswith(","):
  await state["_runtime_agent"].run(session_id=session_id, prompt=prompt, state=state)
```

Comma command 是 operator surface，不是模型执行 surface。Codex 插件替换 model execution，不应接管 Bub builtin command semantics。

### Stream 分层

Codex notification stream 应同时服务两个出口：

```text
Bub AsyncStreamEvents
  user/channel visible text/error/final events

Bub tape events
  canonical runtime facts: turn, tool, side-effect, token usage, anchor/thread refs
```

不是每个 Codex notification 都应成为 Bub stream event。细粒度事实写 tape；用户可见文本走 `StreamEvent("text", ...)`。

## 后果

- Codex SDK/app-server stream 与 Bub model execution hook 对齐。
- v0 避免维护 `run_model` / `run_model_stream` 两套逻辑。
- Bub non-streaming 与 streaming 两种调用模式都可用。
- Bub builtin 的 state、prompt、command、outbound 语义尽量保留。
- 后续可以把 `BubCodexRuntime.run_turn()` 包装成 `AsyncStreamEvents`，而不改变插件入口。

## 非目标

- 不在本 ADR 定义真实 Bub tape service 接入方式。
- 不在本 ADR 解决 system prompt / skills 如何注入 Codex。
- 不在本 ADR 替换 Bub channel 或 outbound behavior。
- 不在本 ADR 设计 approval policy。
- 不要求 `run_model_stream` 输出所有 Codex notification；notification 到 stream/tape 的映射由 adapter/projection 层处理。

## 已验证

Research spike：

```text
scripts/spikes/bub_hook_strategy_spike.py
```

使用 fake HookRuntime 验证：

```text
run_model_stream only
  -> non-streaming can aggregate text
  -> streaming can pass through text/final
  -> comma command can delegate to state["_runtime_agent"]
```

安装 Bub/Republic 依赖后，继续用真实 Bub framework 验证：

```text
scripts/spikes/bub_real_hook_strategy_spike.py
```

该 spike 已证明同一策略在真实 `BubFramework` / `HookRuntime` / `AsyncStreamEvents` 上成立：

```text
non-streaming process_inbound(...)
  -> plugin run_model_stream
  -> HookRuntime aggregates text

streaming process_inbound(..., stream_output=True)
  -> plugin run_model_stream
  -> framework consumes text/final stream

comma command
  -> builtin build_prompt preserves ",..."
  -> plugin delegates to state["_runtime_agent"].run(...)
```

真实验证还暴露一个重要细节：builtin `build_prompt` 会给普通 channel message 加上 channel/chat/date 前缀，但 comma command prompt 保持原始 `,command`。

## 后续

- 将当前 batch-style `BubCodexRuntime.run_turn()` bridge 升级为 live notification bridge。
- 决定真实 Bub `TapeService` / `TapeStore` 如何注入 `BubCodexRuntime`。
- 单独 spike system prompt / skills / AGENTS.md 与 Codex runtime 的组合方式。
- 明确 `phase=commentary` 与 `phase=final_answer` 在 Bub stream 中的呈现策略。

## 实现状态

已新增最小 plugin skeleton：

```text
src/bub_codex/plugin.py
  BubCodexPlugin
  create_plugin

src/bub_codex/runtime_services.py
  BubCodexRuntimeStreamService
  RuntimeStreamService
  UnconfiguredRuntimeStreamService

src/bub_codex/stream_utils.py
  stream_text(...)
```

当前 skeleton 只实现：

```text
run_model_stream(prompt, session_id, state)
```

行为：

```text
comma command:
  delegate to state["_runtime_agent"].run(...)

normal prompt:
  delegate to injected RuntimeStreamService.run_stream(...)
```

已用真实 Bub framework 验证：

```text
scripts/spikes/bub_plugin_skeleton_spike.py
```

该 spike 证明 `BubCodexPlugin` 在真实 `BubFramework` 中能覆盖 builtin model execution，同时不接管 `load_state` / `build_prompt` / outbound hooks。

随后新增：

```text
scripts/spikes/bub_plugin_runtime_stream_spike.py
```

该 spike 使用真实 `BubFramework`、`BubCodexPlugin`、`BubCodexRuntimeStreamService`、`BubCodexRuntime` 和 fake `CodexThreadService`，验证离线端到端链路：

```text
BubFramework.process_inbound
  -> builtin load_state/build_prompt
  -> BubCodexPlugin.run_model_stream
  -> BubCodexRuntimeStreamService.run_stream
  -> BubCodexRuntime.run_turn
  -> InMemoryTapeStore append events
  -> AsyncStreamEvents back to Bub
```

验证事件顺序：

```text
bub.anchor.creation.started
bub.anchor.created
bub.context.materialized
codex.thread.bound
codex.turn.started
codex.turn.completed
codex.turn.started
codex.turn.completed
```

随后已补齐 assistant message completed projection：

```text
item/completed agentMessage
  -> codex.assistant_message.completed
```

真实 Codex SDK smoke test 已验证：

```text
scripts/spikes/real_codex_plugin_stream_spike.py
scripts/spikes/real_codex_plugin_tool_stream_spike.py
scripts/spikes/real_codex_plugin_fibonacci_stream_spike.py
scripts/spikes/real_codex_notification_mapping_spike.py
```

这些测试确认：

- Codex SDK 原始 notification stream 包含 `item/agentMessage/delta`、`item/completed agentMessage`、`item/started/completed commandExecution`、`item/started/completed fileChange`。
- `agentMessage.phase` 由 Codex 原始 payload 提供，可为 `commentary` 或 `final_answer`。
- `item/completed agentMessage` 的 `item.text` 是该 assistant message 的完整聚合文本；此前对应文本已通过 `item/agentMessage/delta` 分片出现。
- `item/completed agentMessage phase=final_answer` 表示最终回答 item 完成，`turn/completed` 表示整个 turn lifecycle 完成，二者不是同一层级。
- Bub tape projection 必须按 Codex notification source order 单 pass 产生事件，不能先聚合 assistant 再追加 tool events。

当前 `BubCodexRuntimeStreamService` 仍是 batch bridge：

```text
Codex notification stream
  -> collect until turn/completed
  -> RuntimeTurnResult
  -> AsyncStreamEvents
```

这只是临时实现，用于稳定 adapter/projection 规则。它不代表 Codex SDK 不支持流。Bub-native runtime 的目标形态应是 live bridge：

```text
Codex notification arrives
  -> normalize to CodexFact
  -> project/append Bub tape event in source order
  -> yield Bub StreamEvent/progress as appropriate
```

在 batch bridge 中，Bub `final.text` 应优先来自 `phase=final_answer` 的 assistant message；如果缺失，再 fallback 到最后一个 assistant message。`phase=commentary` 应保留在 tape 中，未来 live bridge 可作为 progress/commentary stream 输出。

随后新增最小 live bridge spike：

```text
src/bub_codex/live_stream.py
  BubCodexLiveRuntimeStreamService

tests/test_live_stream.py
scripts/spikes/real_codex_live_stream_spike.py
```

验证结果：

```text
artifacts/spikes/real-codex-live-stream-20260611-015408/result.json
```

该 spike 证明：

- 普通 user turn notifications 可以边到达边归一化、边投影、边 append tape。
- Tape event 仍保持 Codex source order。
- `phase=commentary` 可以只写 tape，不作为 `StreamEvent("text")`。
- `phase=final_answer` 可以作为 Bub `StreamEvent("text")` 与 `final.text`。
- `turn/completed` 后发出最终 `final` event。

这验证了 live bridge 的方向，但当前默认 `BubCodexRuntimeStreamService` 仍是 batch bridge；是否切换默认 runtime 属于后续 MVP/PRD 决策。
