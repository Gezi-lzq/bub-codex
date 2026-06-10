# Codex SDK Harness Spike

日期：2026-06-10

## 目的

验证 `bub-codex` v0 是否可以通过 Codex Python SDK / app-server 捕获我们需要的 runtime facts，尤其是：

- regular turn stream notifications
- `thread.read(include_turns=True)` completed item projection
- manual compact 的 stream/read/rollout 形状
- rollout `CompactedItem.message` / `replacement_history` 是否可用于 Anchor enrichment

## 产物

脚本：

```text
scripts/spikes/codex_sdk_harness.py
```

成功运行产物：

```text
artifacts/spikes/codex-sdk-harness-20260610-030158/
```

运行命令：

```text
rtk python3 scripts/spikes/codex_sdk_harness.py \
  --config-override sandbox_mode='"danger-full-access"' \
  --config-override approval_policy='"never"' \
  --compact-wait-s 15
```

环境事实：

```text
codex_bin: /opt/homebrew/bin/codex
codex version: codex-cli 0.137.0
SDK source: /tmp/bub-codex-sources/openai-codex/sdk/python
workspace: /Users/gezi/Dev/bub-codex
```

## 观察

### Regular turn stream

`TurnHandle.stream()` 成功捕获了普通 turn 的 routed notifications。

本次 stream 方法计数：

```text
turn/started: 1
item/started: 8
item/completed: 8
item/agentMessage/delta: 81
thread/tokenUsage/updated: 2
hook/started: 2
hook/completed: 2
turn/completed: 1
```

item 类型包括：

```text
userMessage
reasoning
agentMessage
commandExecution
```

这确认了 v0 可以把 regular turn stream 作为主要 runtime capture path。

### thread.read(include_turns=True)

compact 前：

```text
turns: 1
turn status: completed
items: userMessage, agentMessage, agentMessage
```

compact 后：

```text
turns: 2
regular turn: completed
compact turn: completed
compact turn items: contextCompaction
```

这说明 `thread.read(include_turns=True)` 可以作为 completed item projection 和 backfill/checkpoint 通道。

### Manual compact stream

Python SDK high-level `thread.compact()` 只返回 `ThreadCompactStartResponse`，不返回 compact turn handle。

但 app-server 仍会生成一个 compact turn。因为没有 public handle，SDK router 会把 turn-scoped notifications 暂存在 private pending queue。Spike 通过私有字段发现 compact `turn_id`，再注册该 turn 并消费到 completed。

本次 compact turn stream：

```text
turn/started: 1
item/started: 1      # contextCompaction
thread/tokenUsage/updated: 3
item/completed: 1    # contextCompaction
turn/completed: 1
```

结论：

- public SDK 可以触发 compact；
- public SDK 不方便直接 stream compact turn；
- 若 v0 需要稳定捕获 compact stream，应考虑更低层 app-server adapter 或要求 SDK 暴露 compact turn handle；
- 作为 spike，私有 router introspection 可证明 app-server 事件确实存在。

### Rollout compacted item

找到 rollout：

```text
/Users/gezi/.codex/sessions/2026/06/10/rollout-2026-06-10T03-02-04-019eadc3-66e4-7c11-ad79-5ef926a2c0eb.jsonl
```

rollout 中存在：

```text
type: compacted
payload.message
payload.replacement_history
```

本次数据：

```text
message length: 2553
replacement_history length: 2
replacement_history item types:
  message:user
  message:user
```

这确认 ADR 0001 的判断可行：internal rollout format 可以作为 compaction summary enrichment source。

## 设计影响

1. `TurnHandle.stream()` 应作为 regular turn canonical capture path。
2. `thread.read(include_turns=True)` 适合作为 completed projection / reconciliation path。
3. `thread.compact()` 当前高层 API 不足以优雅捕获 compact turn stream；v0 adapter 需要：
   - 使用 SDK-private router introspection；或
   - 使用底层 app-server JSON-RPC；或
   - 等待/贡献 SDK 暴露 compact turn handle。
4. `contextCompaction` item 本身不携带 summary。summary 需要从 rollout `CompactedItem` 解析。
5. rollout parser 应保持 fail-open：compact event 是 must-have，summary/replacement history 是 enrichment。

## 对 Anchor 模型的校验

本次 spike 支持当前 Anchor 决策：

- manual compact 成功后，`thread.read` 出现 completed `contextCompaction` turn；
- rollout 中出现 `CompactedItem.message` 和 `replacement_history`；
- 因此 compact 成功后创建 Anchor，并用 rollout summary enrich Anchor 是可实现的。

同时也暴露实现约束：

- compact 的 public stream capture 需要 adapter 层处理；
- `contextCompaction` item 只适合作为 “compact happened” fact；
- `CompactedItem` 才是 summary/replacement history 的来源。

## 后续

下一步建议实现一个更窄的 adapter spike：

```text
CodexRuntimeAdapter
  run_turn()
  compact_thread()
  read_thread()
  find_rollout()
  extract_compaction_snapshot()
```

并输出 normalized facts：

```text
codex.turn.started
codex.item.started
codex.item.completed
codex.thread.compaction.started
codex.thread.compacted
codex.compaction.snapshot
```

## Adapter normalization follow-up

已实现最小 adapter spike：

```text
src/bub_codex/runtime_adapter.py
scripts/spikes/normalize_codex_spike.py
docs/adr/0003-codex-runtime-adapter-facts.md
```

对成功运行产物执行：

```text
rtk python3 scripts/spikes/normalize_codex_spike.py artifacts/spikes/codex-sdk-harness-20260610-030158
```

输出：

```text
artifacts/spikes/codex-sdk-harness-20260610-030158/normalized-facts.jsonl
```

fact 计数：

```text
codex.turn.started: 2
codex.item.started: 9
codex.item.completed: 9
codex.notification.observed: 85
codex.token_usage.updated: 5
codex.turn.completed: 2
codex.thread.compacted: 1
codex.compaction.snapshot: 1
```

这验证了 adapter boundary 可以把 SDK stream 和 rollout compacted item 合并到统一 fact stream 中。
