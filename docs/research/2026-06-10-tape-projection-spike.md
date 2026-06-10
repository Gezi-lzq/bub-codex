# Codex facts 到 Bub tape event 的投影 spike

日期：2026-06-10

## 背景

前一轮 SDK harness 已经能把 Codex SDK stream、private compact stream 与 rollout compacted item 归一化成 `CodexFact`。下一步需要验证：这些 facts 是否足够投影成 Bub tape 上的最小事件，而不让 Bub domain model 直接依赖 Codex SDK/generated models 或 rollout JSONL。

本 spike 新增：

```text
src/bub_codex/tape_events.py
scripts/spikes/project_codex_facts_to_tape.py
```

输入：

```text
artifacts/spikes/codex-sdk-harness-20260610-030158/normalized-facts.jsonl
```

输出：

```text
artifacts/spikes/codex-sdk-harness-20260610-030158/projected-tape-events.jsonl
```

## 当前投影范围

v0 先只投影 compact path，因为这是当前 spike 已经证明的能力：

```text
codex.thread.compacted
codex.compaction.snapshot
```

投影成：

```text
bub.anchor.creation.started
codex.thread.compacted
codex.compaction.snapshot
bub.anchor.created
```

这不是完整 Bub tape schema，而是 tape-like event slice，用来检验事件因果顺序与字段边界。

## 关键观察

compact path 的核心不变量成立：

```text
先有 Codex compact 成功事实，再创建 Bub Anchor。
```

`codex.compaction.snapshot` 只是 enrichment。snapshot 存在且 `parse_status=ok` 时，`bub.anchor.created.state.summary_status=ok`，并可把 Codex compact summary 放入 Anchor state。若 snapshot 缺失或解析失败，投影层仍应创建 Anchor，只把 `summary_status` 标记为 `unavailable` 或解析失败原因。

当前样本生成 4 条事件：

```text
bub.anchor.creation.started
codex.thread.compacted
codex.compaction.snapshot
bub.anchor.created
```

这说明 `CodexFact` 的最小结构已经足够支撑 compact Anchor 的第一版 tape 投影。

## 初始 compact 投影阶段暂不做的事

- 不把所有 Codex item 都投影成 Bub tape event。
- 不在 compact fact 投影器里处理 new-thread materialization；new-thread 由单独的 context materialization 层负责。
- 不引入 `.bub-codex-threads.json` 或其他 binding cache。
- 不把 rollout snapshot 当作 canonical state。
- 不决定最终持久化后端。

## 暴露的问题

ADR 0002 里 bootstrap 示例曾把 `bub.context.materialized` 放在 `bub.anchor.created` 前面，这与后续确定的 new-thread 不变量不一致。已修正为：

```text
bub.anchor.creation.started
bub.anchor.created
bub.context.materialized
codex.thread.bound
```

这个顺序更符合当前模型：new-thread path 是先有 Anchor，再从 Anchor materialize context 并绑定 Codex thread；compact path 则是先 compact，再 Anchor。

## 下一步

更值得继续验证的是 new-thread path：

```text
bub.anchor.created
  -> bub.context.materialized
  -> codex.thread.bound
```

这会迫使我们定义 context assembler 的最小输入：Anchor state、selected tape facts、当前用户意图、workspace/runtime metadata，以及 materialization audit refs。

## New-thread materialization spike

继续新增：

```text
src/bub_codex/context_materialization.py
scripts/spikes/materialize_new_thread_spike.py
```

该 spike 从已经生成的 tape-like events 中读取最近的 committed Anchor，再创建一个新的 `method=new_thread` Anchor，并从这个 Anchor 生成 context materialization 与 thread binding：

```text
bub.anchor.creation.started
bub.anchor.created
bub.context.materialized
codex.thread.bound
```

这验证了 new-thread path 的核心不变量：

```text
先有 Anchor，再从 Anchor materialize context，最后绑定 Codex thread。
```

`bub.context.materialized` 记录的是 audit refs：

```text
materialization_id
anchor_id
strategy
selected_fact_refs
input_sha256
input_preview
token_estimate
workspace_metadata
```

其中 `input_preview` 只是 spike 期间便于检查的短预览；长期 schema 更应偏向 `input_ref` / `input_sha256`，避免把完整上下文重复写进 tape event。

脚本也支持模拟 bind 失败：

```text
bub.anchor.creation.started
bub.anchor.created
bub.context.materialized
codex.thread.bind.failed
```

这对应当前设计判断：new-thread binding 失败不会撤销 Anchor。Anchor 已经是 committed materialization boundary，失败的是 Codex runtime binding；后续启动可以从该 Anchor 重试 materialization / binding。

## 新暴露的约束

仅从 compact spike 输出继续 fresh handoff 时，`previous_thread_id` 会是 `null`，因为 compact path 没有产生 `codex.thread.bound`。这符合当前输入事实，但提醒我们真实 runtime 启动顺序必须先有 bootstrap/new-thread binding：

```text
session_start Anchor
  -> context.materialized
  -> codex.thread.bound
  -> later compact Anchors
```

compact Anchor 表示同一个 Codex thread 的物理上下文被压缩；它不负责首次建立 thread identity。

## Bootstrap spike

为验证真实启动路径，继续新增：

```text
scripts/spikes/bootstrap_new_thread_spike.py
```

它从空 tape facts 开始生成：

```text
bub.anchor.creation.started
bub.anchor.created
bub.context.materialized
codex.thread.bound
```

其中 Anchor：

```text
method=new_thread
reason=session_start
state.owner=human
state.summary_status=unavailable
```

这确认了一个重要约束：bootstrap 与 fresh handoff 可以共用 new-thread materialization 机制；差异只是 `reason`、`previous_anchor_id`、`previous_thread_id` 与 Anchor state。

## Runtime resolution spike

继续新增：

```text
src/bub_codex/runtime_resolution.py
scripts/spikes/resolve_runtime_context_spike.py
```

该 spike 只从 tape events 推导 runtime 启动动作：

```text
no committed Anchor
  -> bootstrap

latest Anchor exists, but no codex.thread.bound for that Anchor
  -> materialize_thread

latest Anchor has codex.thread.bound
  -> resume_thread
```

这保持了当前原则：

```text
active_anchor_id = latest bub.anchor.created
active_thread_id = latest codex.thread.bound for active_anchor_id
```

因此 v0 不需要 `.bub-codex-threads.json`。如果未来引入缓存，它也只能是从 tape 重建出来的 projection。

## In-memory tape store spike

继续新增：

```text
src/bub_codex/tape_store.py
scripts/spikes/in_memory_tape_store_spike.py
```

`InMemoryTapeStore` 只提供最小 append-only 边界：

```text
append(event)
append_many(events)
events(session_id?, tape_id?)
latest_anchor(session_id, tape_id)
active_thread_id(session_id, tape_id)
resolve_runtime_context(session_id, tape_id)
```

这个 spike 的目的不是决定最终持久化后端，而是验证 domain core 不需要读取 JSONL 文件或 workspace binding cache 才能推导 runtime 状态。验证序列：

```text
empty store
  -> bootstrap

append session_start Anchor
  -> materialize_thread

append codex.thread.bound
  -> resume_thread
```

这说明下一层真实 runtime 可以依赖一个很小的 TapeStore protocol：只要它能 append events 并按 session/tape 读回 ordered events，runtime resolution 就能保持 tape-first。
