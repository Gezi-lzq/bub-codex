# ADR 0003: Codex Runtime Adapter Facts

日期：2026-06-10

## 状态

Accepted

## 背景

SDK harness spike 证明：

- `TurnHandle.stream()` 能捕获 regular turn notifications。
- `thread.read(include_turns=True)` 能提供 completed item projection。
- `thread.compact()` 高层 API 不返回 compact turn handle；compact stream 需要底层 adapter 或 SDK-private route。
- rollout `CompactedItem` 包含 `message` 与 `replacement_history`，可作为 compaction Anchor enrichment。

因此需要一个小 adapter boundary，把 SDK/generated types、private stream workaround 和 rollout JSONL 都归一化成 Bub 可消费的 facts。

## 决策

引入 `CodexRuntimeAdapter` 方向的 normalized fact model。v0 spike 先实现最小代码：

```text
src/bub_codex/runtime_adapter.py
scripts/spikes/normalize_codex_spike.py
```

normalized fact 最小结构：

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

当前 fact vocabulary：

```text
codex.turn.started
codex.turn.completed
codex.item.started
codex.item.completed
codex.thread.compacted
codex.token_usage.updated
codex.command_output.delta
codex.file_change.patch_updated
codex.turn.diff.updated
codex.error.observed
codex.dynamic_tool.requested
codex.server_request.observed
codex.notification.observed
codex.compaction.snapshot
```

## 边界

Adapter fact 不是最终 Bub tape schema。它是中间边界：

```text
Codex SDK / app-server / rollout
  -> CodexRuntimeAdapter facts
  -> Bub tape entries
  -> projections
```

这样可以避免 Bub domain model 直接依赖：

- SDK generated Pydantic types
- app-server private notification routing details
- rollout JSONL internal shape

## 后续

- 用 adapter facts 定义 `bub.anchor.created`、`codex.thread.bound` 等 tape entries 的映射。
- 增加 `thread.read(include_turns=True)` reconciliation path。
- 对 compact turn 捕获改用低层 app-server request，避免长期依赖 SDK private router fields。
- 为 dynamic tool server request / response 增加更完整的 adapter boundary；当前 spike 已能记录 `codex.dynamic_tool.requested`。
