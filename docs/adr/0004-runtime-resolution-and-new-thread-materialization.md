# ADR 0004: Runtime Resolution 与 New-thread Materialization

日期：2026-06-10

## 状态

Accepted

## 背景

ADR 0002 已确定：

```text
tape owns history.
Anchor owns committed context materialization boundaries.
Codex thread owns executable model context.
```

后续 spike 进一步验证了两条 Anchor materialization path：

- `compact`：Codex thread 先完成 compact，再创建 Bub Anchor。
- `new_thread`：Bub 先创建 Anchor，再从 Anchor materialize context，并绑定 Codex thread。

本 ADR 固化 runtime 启动恢复、new-thread materialization、binding failure 的 tape-first 规则。真实 Codex SDK thread creation 属于 adapter 接入工作；不改变这里的 domain event 顺序和 resolution 规则。

## 决策

### Runtime Resolution

v0 不读取 `.bub-codex-threads.json` 之类的 binding file。启动时只从 tape events 推导：

```text
active_anchor_id = latest bub.anchor.created
active_thread_id = latest codex.thread.bound for active_anchor_id
```

启动动作：

```text
no committed Anchor
  -> bootstrap

latest Anchor exists, but no codex.thread.bound for that Anchor
  -> materialize_thread

latest Anchor has codex.thread.bound
  -> resume_thread
```

如果 resume 已绑定的 Codex thread 失败，v0 暴露异常，不自动创建替代 thread。自动替换会掩盖 runtime continuity 的断裂，应该由后续显式恢复策略处理。

### New-thread Path

Bootstrap 和 fresh handoff 共用同一套 new-thread materialization 机制。new-thread path 的不变量是：

```text
先创建 Anchor
再从 Anchor materialize initial context
最后绑定 Codex thread
```

事件序列：

```text
bub.anchor.creation.started
bub.anchor.created
bub.context.materialized
codex.turn.materialization.started
codex.turn.materialization.completed
codex.thread.bound
```

真实 Codex SDK 测试补充了 `codex.thread.bound` 的成立条件：`thread/start` 成功只表示 thread id 已分配，不表示 Codex rollout 已经可恢复。Codex thread 需要完成首个 materialization turn 后，`thread_resume` 才能成功。因此真实 adapter 不应仅凭 `thread/start` 成功写入 `codex.thread.bound`；它应在 initial materialization turn 完成并形成可恢复 rollout 后再写入 binding。

Bootstrap 与 fresh handoff 的差异只在字段：

- `reason`
- `previous_anchor_id`
- `previous_thread_id`
- Anchor `state`
- selected tape refs

### Materialization Audit

`bub.context.materialized` 记录新 Codex thread 初始上下文由什么材料组装而来。最小字段：

```text
bub.context.materialized
  materialization_id
  anchor_id
  strategy
  selected_fact_refs
  input_sha256
  input_ref?
  refs.materialization_turn_id?
  token_estimate?
  workspace_metadata?
```

Spike 暂时保留 `input_preview` 方便检查；长期 schema 应更偏向 `input_ref` / `input_sha256`，避免把完整上下文重复塞入每条 tape event。

New-thread Anchor 可以携带用于 handoff 的事实引用：

```text
bub.anchor.created.refs.source_event_refs
```

这些 refs 应进入 `bub.context.materialized.selected_fact_refs`。这样 handoff summary 是人可读的压缩状态，而 `source_event_refs` 保留底层执行事实的可追溯性，例如：

```text
bub.tool.call.completed
bub.tool.call.failed
bub.side_effect.completed
bub.side_effect.failed
codex.turn.diff.updated
```

### Binding Failure

如果 new-thread Anchor 已创建，但 Codex thread binding 失败：

```text
bub.anchor.creation.started
bub.anchor.created
bub.context.materialized
codex.thread.bind.failed
```

Anchor 仍然成立。失败的是 runtime binding，不是 context materialization boundary。

后续 runtime resolution 会看到：

```text
latest Anchor exists, but no codex.thread.bound for that Anchor
  -> materialize_thread
```

因此可以从同一个 Anchor 重试 materialization / binding。

### Compact Path

Compact path 的顺序不同：

```text
bub.anchor.creation.started
codex.thread.compacted
codex.compaction.snapshot
bub.anchor.created
```

也就是先确认 Codex compact 成功，再创建 Bub Anchor。

这个差异是刻意的：compact Anchor 依赖已有 thread 的物理上下文压缩成功；new-thread Anchor 是 Bub tape 上的逻辑 materialization boundary，随后才把这个边界 materialize 到一个新的 Codex runtime context。

## 已验证路径

已实现最小 spike：

```text
src/bub_codex/context_materialization.py
src/bub_codex/runtime_resolution.py
src/bub_codex/tape_store.py
scripts/spikes/bootstrap_new_thread_spike.py
scripts/spikes/materialize_new_thread_spike.py
scripts/spikes/resolve_runtime_context_spike.py
scripts/spikes/in_memory_tape_store_spike.py
scripts/spikes/handoff_with_tool_refs_spike.py
```

验证结果：

```text
bootstrap:
bub.anchor.creation.started -> bub.anchor.created -> bub.context.materialized -> codex.thread.bound

compact:
bub.anchor.creation.started -> codex.thread.compacted -> codex.compaction.snapshot -> bub.anchor.created

fresh handoff:
bub.anchor.creation.started -> bub.anchor.created -> bub.context.materialized -> codex.thread.bound

bind failed:
bub.anchor.creation.started -> bub.anchor.created -> bub.context.materialized -> codex.thread.bind.failed
```

runtime resolution 验证结果：

```text
empty tape -> bootstrap
compact Anchor without binding -> materialize_thread
bootstrap with binding -> resume_thread
new-thread bind failure -> materialize_thread
new-thread bind success -> resume_thread
```

in-memory tape store 验证结果：

```text
empty store -> bootstrap
append session_start Anchor -> materialize_thread
append codex.thread.bound -> resume_thread
```

tool refs handoff 验证结果：

```text
bub.anchor.created.refs.source_event_refs
  -> bub.context.materialized.selected_fact_refs
```

真实 Codex SDK thread service 验证结果：

```text
thread_start only
  -> thread id allocated
  -> thread_read(include_turns=False) works
  -> thread_read(include_turns=True) fails before first user message
  -> thread_resume fails: no rollout found for thread id

thread_start + first turn completed
  -> thread_read(include_turns=True) has 1 turn
  -> thread_resume succeeds from a new app-server client
```

## 理由

这个模型让 Codex thread binding 变成 tape projection，而不是外部状态文件。`.bub-codex-threads.json` 这类文件如果存在，只能是 cache/projection；一旦它和 tape 不一致，tape 必须胜出。

Anchor 与 thread creation failure 解耦也很重要：Anchor 可以已经提交，但 Codex runtime binding 仍然失败并等待重试。

`bub.context.materialized` 给 new-thread handoff 留下审计边界：未来可以追踪 initial context 来自哪些 Anchor state、source refs、workspace metadata 和当前 intent，而不需要相信自然语言 summary。

resume failure 不自动 fallback 到 new thread，是为了保护 continuity 语义。一个 thread resume 失败可能意味着 Codex runtime 状态损坏、thread id 失效、版本不兼容或存储缺失；自动换 thread 会把异常变成隐式 handoff，破坏 tape 上的因果可见性。

## 后果

- Runtime 主入口可以先实现为纯 tape resolver，再接真实 Codex SDK adapter。
- 第一版 runtime facade 只负责确保 Codex thread context：`bootstrap`、`materialize_thread`、`resume_thread`。普通 turn execution、stream normalization、tool projection 是后续层。
- Initial materialization turn 是建立真实 Codex physical context 的一部分，不应被当成普通 user turn。
- `bub.context.materialized` 和 `codex.thread.bound` 应通过 `refs.materialization_turn_id` 指向 initial materialization turn，形成可审计链。
- Initial materialization turn 的 started/completed facts 应进入 tape；如果该 turn 中出现 tool-like items，也按普通 Codex item lifecycle 投影为 tool/side-effect events。
- 普通 user turn 可在 `codex.thread.bound` 之后运行，投影为 `codex.turn.started/completed` 且 `purpose=user_turn`；这不改变 startup/materialization 边界。
- 新 session bootstrap 与 fresh handoff 共享同一套 new-thread materialization。
- Anchor 创建成功与 Codex thread binding 成功解耦。
- `codex.thread.bind.failed` 是可恢复状态；后续启动会从 latest Anchor 重新进入 `materialize_thread`。
- `active_anchor_id` 和 `active_thread_id` 都是 projection，不是 canonical mutable state。
- `input_preview` 只是 spike 便利字段；长期 schema 应使用 `input_ref` / `input_sha256`。
- Runtime facade 的 Codex thread lifecycle boundary 是 `materialize_thread()`，不是低层 `thread_start()`。它应只在 Codex rollout 已可恢复时返回 thread id。

## 非目标

- 不在本 ADR 决定最终持久化后端。
- 不在本 ADR 决定完整 context assembler 策略。
- 不要求 `input_preview` 成为最终 schema。
- 不处理 Codex thread resume 失败后的自动替换；resume 失败仍应先暴露为异常。
- 不定义 thread archive/unbound 语义；v0 使用 latest-bound-wins。

## 后续

- 将 context materialization 的 `input_preview` 替换为更明确的 `input_ref` 策略。
- 将 initial materialization turn 的 stream facts 纳入 tape projection，并关联到 `bub.context.materialized` / `codex.thread.bound`。
- 定义真实 runtime resume failure 的错误 event shape。
