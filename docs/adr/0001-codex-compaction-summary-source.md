# ADR 0001: Codex compact summary 的来源与边界

日期：2026-06-10

## 状态

Accepted

## 背景

`bub-codex` 需要把 Bub `Anchor` / `Handoff` 与 Codex thread 的物理上下文控制连接起来。v0 中，Anchor 是 LLM context materialization 的边界；Codex thread 绑定到 Anchor。

目前支持两种 Anchor creation method：

- `compact`：触发 Codex `thread.compact()`，让同一个 thread 的历史被压缩，并用 compact 结果创建或丰富 Anchor。
- `new_thread`：先创建 Anchor，再用 Bub 从该 Anchor 组装的新上下文启动或绑定新的 Codex thread。

在 `Anchor + compact` 中，一个自然问题是：Bub Anchor 的 `summary` 是否可以来自 Codex compact 生成的 summary。

源码观察显示：

- Python SDK 的 `thread.compact()` 只调用 app-server 的 `thread/compact/start`。
- SDK 高层 API 和 `contextCompaction` item 只暴露 compact 发生了，不直接返回 summary。
- Codex 内部 rollout JSONL 中的 `CompactedItem` 包含 `message` 和 `replacement_history`。
- local inline compact 会把 summary 写入 `CompactedItem.message`，并把后续使用的压缩历史写入 `replacement_history`。
- remote / v2 compact 可能更依赖 `replacement_history`，`message` 可能为空。

因此，公开 SDK API 不足以直接获得 compact summary；但 internal rollout format 可以提供 summary 或压缩后的 replacement history。

## 决策

v0 接受读取 Codex internal rollout format 作为 **compaction summary enrichment source**。

但 rollout parser 不进入 Bub 的核心领域模型。它必须被隔离在 adapter 后面，并输出一个 Bub 自己的 normalized fact：

```text
codex.compaction.snapshot
  thread_id
  turn_id
  anchor_id?
  summary_text?
  replacement_history_hash?
  replacement_history_ref?
  rollout_path
  parser_version
  parse_status: ok | summary_missing | replacement_history_missing | failed
```

Bub tape 中仍必须记录 compact 的事实，即使 rollout 解析失败。无论 compact 是 user/manual、Bub runtime context-overflow，还是 Codex auto compact，只要 compact 成功改变了物理上下文，就应创建 Anchor：

```text
codex.thread.compacted
  session_id
  tape_id
  thread_id
  turn_id
  anchor_id?
  trigger: manual | auto | bub_anchor_compact
  initiator: human | bub_runtime | codex_runtime
  reason: user_requested | context_overflow | auto_compact | handoff
```

如果解析成功，Bub 可以用 Codex compact summary 创建 Anchor state：

```text
bub.anchor.created
  anchor_id
  reason: codex_compact
  source: codex_rollout_compacted_item
  summary: ...
  thread_id
  turn_id
```

如果 compact 成功但 summary 解析失败，Anchor 仍然可以创建，但必须显式标记 summary 不可用：

```text
bub.anchor.created
  anchor_id
  reason: codex_compact
  source: codex_rollout_compacted_item
  summary_status: parse_failed
  thread_id
  turn_id
```

## 设计约束

- Codex rollout JSONL 是 internal format，不承诺稳定。
- Bub domain model 不直接依赖 rollout JSON shape。
- Rollout parser 必须版本化。
- 解析失败不能让 runtime turn 失败。
- `CompactedItem.message` 不能被假设总是存在；需要支持从 `replacement_history` 中提取 summary-like message。
- Bub Anchor 的语义仍由 Bub 定义；Codex compact summary 只是 Anchor state 的来源之一。
- v0 中 compaction snapshot 解析失败时，不自动触发 Bub 自己再总结一次；只记录 `summary_status=parse_failed` 或 `summary_status=unavailable`。

## 影响

这个决定让 `Anchor + compact` 可以成为一个实用模式：

```text
触发 Codex compact
  -> 监听 contextCompaction completed
  -> 读取 rollout CompactedItem
  -> 提取 summary / replacement_history
  -> 写入 compaction snapshot
  -> 创建 Bub Anchor
```

同时，它保留了 fallback：

```text
compact 发生了，但 summary 不可得
  -> 仍记录 codex.thread.compacted
  -> Anchor summary_status=parse_failed
  -> v0 不自动二次总结；后续可由人工或显式命令基于 tape 生成 summary
```

## 非目标

- 不把 Codex rollout JSONL 作为 canonical Bub tape。
- 不要求所有 compact 都有可读 summary。
- 不要求 Python SDK 高层 API 修改后才能实现 v0。
- 不把 Codex compact summary 当作唯一 Anchor state 来源。

## 后续工作

- 实现 `CodexRolloutCompactionReader` spike。
- 对比 local inline compact、remote compact、auto compact 三种 rollout 形状。
- 定义从 `replacement_history` 提取 summary-like message 的规则。
- 在 tape event schema 中固化 `codex.thread.compacted` 和 `codex.compaction.snapshot`。
