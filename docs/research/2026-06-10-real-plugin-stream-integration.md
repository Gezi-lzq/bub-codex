# Real Plugin Stream Integration

## 背景

前面的 spike 已验证：

- `BubCodexRuntime.ensure_thread_context()` 可以创建 bootstrap Anchor、materialize Codex thread，并在真实 SDK 下等到 initial materialization turn 完成后再写入 `codex.thread.bound`。
- `BubCodexRuntime.run_turn()` 可以消费真实 Codex SDK turn notifications。
- 真实 Codex `item/completed` 中的 `agentMessage` item 形状包含：

```text
item.type = agentMessage
item.text = <assistant text>
item.phase = final_answer | commentary | null
```

因此 v0 可以从 `item/completed` 投影：

```text
codex.assistant_message.completed
  payload.assistant_text
  payload.phase
```

再由 Bub plugin stream 输出：

```text
StreamEvent("text", {"delta": assistant_text})
StreamEvent("final", {"text": assistant_text, "ok": True})
```

## 收敛后的入口

新增正式 helper：

```text
src/bub_codex/plugin_stream_integration.py
```

核心函数：

```text
run_plugin_stream_once(runtime_stream, prompt, session_id, state, tape_store=None)
```

它只负责：

1. 调用 `RuntimeStreamService.run_stream()`。
2. 收集 Republic `StreamEvent.kind/data`。
3. 可选收集 `TapeStore.events()`。
4. 返回 `PluginStreamIntegrationResult`，提供 `text` 与 `final_text` 便于断言。

这个 helper 不 import Codex SDK，也不启动 `codex` binary。真实 SDK 构造仍留在手动 smoke test 脚本里。

## 测试分层

普通正式测试：

```text
tests/test_plugin_stream_integration.py
```

该测试使用 fake `CodexThreadService`，验证：

- `run_plugin_stream_once()` 输出 assistant text。
- tape 中存在 `codex.assistant_message.completed`。
- bootstrap Anchor、context materialization、thread binding、user turn event 顺序保持稳定。

手动真实 smoke test：

```text
scripts/spikes/real_codex_plugin_stream_spike.py
```

该脚本使用真实 Codex SDK 与 `codex` binary，验证同一个正式 helper 能在真实 runtime 下输出：

```text
text:  bub-codex-plugin-stream
final: bub-codex-plugin-stream
```

并写出 artifact：

```text
artifacts/spikes/real-codex-plugin-stream-<timestamp>/result.json
```

## 当前判断

这次收敛没有改变核心架构，只把已验证的真实 plugin stream 路径变成可复用入口：

```text
BubCodexRuntimeStreamService
  -> BubCodexRuntime.run_turn()
  -> Codex SDK notifications
  -> CodexFact
  -> Bub tape events
  -> Republic StreamEvent
```

默认测试不依赖真实 Codex SDK，是有意的。真实 SDK smoke test 会启动外部 runtime，并可能消耗模型调用，应保留为显式运行。
