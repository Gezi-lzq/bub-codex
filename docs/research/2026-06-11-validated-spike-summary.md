# Validated Spike Summary

## 状态判断

当前项目状态应表述为：

```text
validated spike prototype
```

而不是：

```text
MVP
```

原因是核心路径已经通过真实 Codex SDK 验证，但实现仍主要分布在：

```text
scripts/spikes/*
InMemoryTapeStore
fake / recording services
manual smoke artifacts
```

还没有形成可安装、可配置、可重复运行的 Bub plugin 产品形态。

## 已验证结论

### Bub plugin entrypoint

`bub-codex` 第一入口使用：

```text
run_model_stream(prompt, session_id, state) -> AsyncStreamEvents
```

并保留 Bub builtin 语义：

```text
comma command -> state["_runtime_agent"].run(...)
normal prompt -> BubCodexRuntimeStreamService
```

### Codex thread materialization

真实 Codex SDK 测试确认：

```text
thread_start
  -> only allocates thread id
  -> not yet resumable

initial materialization turn completed
  -> rollout exists
  -> thread_read(include_turns=True) works
  -> thread_resume works
```

因此：

```text
codex.thread.bound
```

必须在 materialization turn 完成之后写入 tape。

### Anchor / thread / tape

当前规则成立：

```text
Anchor = committed LLM context materialization boundary
Codex thread_id = executable runtime context handle
Bub tape = canonical fact record
```

`active_anchor_id` 与 `active_thread_id` 仅从 tape 推导，不使用额外 mutable thread registry。

### Source signal -> adapter fact -> tape event

三层边界已验证：

```text
Codex raw notification
  -> CodexFact
  -> Bub tape event
```

`method` 是解析入口，但不是 Bub tape canonical event name。需要结合：

```text
method
payload.item.type
payload.item.status
payload.item.phase
```

进行投影。

代表性映射：

```text
item/completed agentMessage
  -> codex.assistant_message.completed

item/started commandExecution
  -> bub.tool.call.started

item/completed commandExecution status=completed
  -> bub.tool.call.completed

item/completed commandExecution status=failed
  -> bub.tool.call.failed

item/started|completed fileChange
  -> bub.side_effect.started|completed
```

### Real coding task

真实 Fibonacci 任务验证了 coding runtime 价值：

```text
assistant commentary
commandExecution started/completed/failed
fileChange started/completed
assistant final_answer
turn completed
```

artifact 示例：

```text
artifacts/spikes/real-codex-plugin-fibonacci-stream-20260610-225755/result.json
artifacts/spikes/real-codex-notification-mapping-20260611-011823/mapping.json
```

生成文件：

```text
fibonacci.py
```

验证输出：

```text
[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
```

### Tape ordering

发现并修复了一个严重问题：

```text
bad:
  assistant messages
  turn completed
  tool events appended later
```

原因是 `project_user_turn_events()` 先投影 assistant/turn，再批量追加 tool events。

修复后改为 single-pass projection：

```text
CodexFact source order
  -> tape event source order
```

真实回归确认顺序已正确：

```text
codex.assistant_message.completed
bub.tool.call.started
bub.tool.call.completed
codex.assistant_message.completed
bub.side_effect.started
bub.side_effect.completed
codex.assistant_message.completed
bub.tool.call.started
bub.tool.call.failed
codex.assistant_message.completed
bub.tool.call.started
bub.tool.call.completed
codex.assistant_message.completed
codex.turn.completed
```

## 发现的问题

### Live bridge 已完成最小 spike

Codex SDK 支持 live notification stream。此前 `BubCodexRuntime.run_turn()` 是 batch bridge：

```text
BubCodexRuntime.run_turn()
  -> collect notifications until turn/completed
  -> return RuntimeTurnResult
  -> convert to AsyncStreamEvents
```

这意味着 Bub 侧还看不到真实流式过程，只能在 turn 结束后一次性输出。

现在新增了最小 live bridge spike：

```text
src/bub_codex/live_stream.py
  BubCodexLiveRuntimeStreamService

scripts/spikes/real_codex_live_stream_spike.py
```

真实 smoke artifact：

```text
artifacts/spikes/real-codex-live-stream-20260611-015408/result.json
```

该 spike 已验证：

```text
notification arrives
  -> CodexFact
  -> project/append tape event in source order during the turn
  -> phase=commentary only writes tape
  -> phase=final_answer yields Bub text and final.text
```

这仍是 spike，不是默认 production runtime，但已经证明 live notification bridge 可行。

### final.text 语义已在 live spike 中验证

Codex `agentMessage` 带有：

```text
phase = commentary | final_answer | null
```

目标策略：

```text
tape:
  preserve all assistant message completions in order

Bub final.text:
  prefer phase=final_answer
  fallback to last assistant message if no final_answer

commentary:
  tape first
  future live UI may expose as progress/commentary
```

真实 live smoke 中：

```text
stream text/final:
  only final_answer

tape:
  commentary + tool + fileChange + final_answer + turn completed
```

batch bridge 仍需同步该策略；live spike 已验证策略可行。

### Environment parity

真实 Codex runtime 读取到了全局 `RTK.md` 指令并尝试：

```text
rtk python fibonacci.py
```

但该 Codex execution environment 中没有 `rtk`，导致：

```text
bub.tool.call.failed
exitCode = 127
```

随后 fallback 到 `python3` 成功。

这个不是 tape 错误，而是 runtime environment parity 问题。后续需要明确：

```text
Codex runtime PATH / shell environment
global instructions
workspace instructions
```

三者如何一致。

### Not MVP yet

尚未达到 MVP，因为缺少：

```text
formal package / entrypoint
configurable Codex client
real Bub TapeService integration
stable tests beyond smoke scripts
README runnable instructions
issue-tracked MVP scope
```

## 下一步建议

优先顺序：

1. 将 batch bridge 的 `final.text` 同步为优先使用 `phase=final_answer`。
2. 将 stable spike helpers 收敛为正式模块和测试。
3. 接入真实 Bub TapeService。
4. 定义 MVP issue 切片。
