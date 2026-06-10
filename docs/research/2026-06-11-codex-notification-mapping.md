# Codex Notification Mapping

## 目的

为了确认 `bub-codex` 的解析规则，需要同时查看三层信息：

```text
Codex SDK raw notification
  -> CodexFact adapter fact
  -> Bub tape event
```

新增脚本：

```text
scripts/spikes/real_codex_notification_mapping_spike.py
```

它会运行真实 Codex SDK Fibonacci 任务，并写出：

```text
artifacts/spikes/real-codex-notification-mapping-<timestamp>/mapping.json
```

当前样本：

```text
artifacts/spikes/real-codex-notification-mapping-20260611-011823/mapping.json
```

## 核心观察

Codex 原始 notification 的 `method` 是解析入口，但不是 Bub tape 的 canonical event name。

例如同一个：

```text
method = item/completed
```

需要继续看：

```text
payload.item.type
payload.item.status
payload.item.phase
```

才能区分：

```text
agentMessage      -> codex.assistant_message.completed
commandExecution  -> bub.tool.call.completed / failed
fileChange        -> bub.side_effect.completed
contextCompaction -> codex.thread.compacted / Anchor path
```

## Representative Rules

### Agent message

```text
raw:
  method = item/completed
  item.type = agentMessage
  item.phase = commentary | final_answer
  item.text = complete aggregated text

CodexFact:
  codex.item.completed
  codex.assistant_message.completed

Bub tape:
  codex.assistant_message.completed
    assistant_text
    phase
```

`item/agentMessage/delta` 先给出文本分片，`item/completed agentMessage` 再给出完整聚合文本和 message boundary。

### Command execution

```text
raw:
  method = item/started
  item.type = commandExecution
  item.status = inProgress

Bub tape:
  bub.tool.call.started
    tool_kind = commandExecution
    tool_name = shell_command
    input_preview = command/cwd/commandActions
```

```text
raw:
  method = item/completed
  item.type = commandExecution
  item.status = completed | failed
  item.exitCode
  item.aggregatedOutput

Bub tape:
  bub.tool.call.completed | bub.tool.call.failed
    output_preview = exitCode/aggregatedOutput/durationMs
```

### File change

```text
raw:
  method = item/started | item/completed
  item.type = fileChange
  item.status = inProgress | completed | failed
  item.changes

Bub tape:
  bub.side_effect.started | completed | failed
    side_effect_kind = fileChange
    changes_preview
```

## 顺序要求

Projection 必须按 Codex notification source order 单 pass 进行。

已修复的问题：

```text
bad:
  assistant messages
  turn completed
  tool events appended later

good:
  assistant commentary
  tool started/completed
  fileChange started/completed
  assistant final_answer
  turn completed
```

这个顺序是 Bub tape 作为 canonical fact timeline 的基本要求。

## Live Bridge 结论

Codex SDK 已经提供 live notification stream。当前 `BubCodexRuntime.run_turn()` 把 notifications 收集到 `turn/completed` 后批量返回，是 adapter 的临时实现，不是 SDK 限制。

后续 Bub-native runtime 应提供 live bridge：

```text
notification arrives
  -> CodexFact
  -> append tape event
  -> yield stream/progress event when appropriate
```

其中：

```text
phase=commentary
  -> tape first; future live UI may show as progress/commentary

phase=final_answer
  -> tape + Bub final.text source
```
