# Runtime facade spike

日期：2026-06-10

## 问题

ADR 0004 和 ADR 0005 已经分别收敛：

- runtime resolution / new-thread materialization
- Bub tools 到 Codex dynamic tools 的 provider boundary

下一步需要验证这些独立模块能否组成一个最小 runtime 主入口，而不是继续停留在分散函数和脚本。

## Spike

新增：

```text
src/bub_codex/runtime.py
scripts/spikes/runtime_facade_spike.py
```

当前 facade 只负责确保有可用 Codex thread context，不执行 turn：

```text
BubCodexRuntime.ensure_thread_context()
  -> TapeStore.resolve_runtime_context()
  -> bootstrap | materialize_thread | resume_thread
  -> CodexThreadService.materialize_thread/resume_thread
  -> append tape events
```

最小 adapter protocol：

```text
CodexThreadService.materialize_thread(cwd, anchor_id, intent) -> thread_id
CodexThreadService.resume_thread(thread_id) -> None
```

## 验证路径

spike 使用 fake Codex thread service，覆盖：

```text
empty tape
  -> bootstrapped
  -> bub.anchor.creation.started
  -> bub.anchor.created
  -> bub.context.materialized
  -> codex.thread.bound

existing bound thread
  -> resumed
  -> no appended events

thread create failure
  -> bind_failed
  -> bub.anchor.creation.started
  -> bub.anchor.created
  -> bub.context.materialized
  -> codex.thread.bind.failed

retry after bind failure
  -> materialized
  -> same anchor_id
  -> bub.context.materialized
  -> codex.thread.bound
```

## 结论

runtime facade 的第一版边界应该保持窄：

```text
ensure thread context only
```

它不应该同时承担 turn execution、Codex stream normalization、tool projection 或 observability export。这样可以保持 ADR 0004 的 runtime continuity 语义清晰，也避免把 ADR 0005 的 tool provider 细节过早耦合进启动恢复逻辑。

## 暴露的问题

当前 `materialize_thread_binding_events()` 需要 `thread_id` 来生成 `materialization_id` 和 `codex.thread.bound`。真实 Codex SDK 可能需要先 `thread/start` 得到 thread id，再把 materialized context 作为首个 turn 输入。这不会改变 tape event 顺序，但会影响 adapter 内部调用顺序，需要在真实 SDK 接入时明确。

`resume_thread()` 失败目前按 ADR 0004 直接暴露异常，不记录错误 event。后续需要定义 resume failure 的 tape event shape。

## 真实 Codex SDK thread service spike

继续新增：

```text
src/bub_codex/codex_thread_service.py
scripts/spikes/real_codex_thread_service_spike.py
```

`LowLevelCodexThreadService` 接收已经启动的低层 `CodexClient`，实现 runtime facade 需要的最小 thread lifecycle：

```text
create_thread(cwd, anchor_id, intent) -> thread_start(...) -> thread_id
resume_thread(thread_id) -> thread_resume(...)
```

后续修正：runtime facade 的正式 protocol 已从 `create_thread()` 改为 `materialize_thread()`。`LowLevelCodexThreadService` 只保留为低层 thread id allocation / 失败对照工具；真实可绑定路径应使用 `MaterializingCodexThreadService`：

```text
materialize_thread(cwd, anchor_id, intent)
  -> thread_start
  -> turn_start(initial materialization prompt)
  -> wait turn/completed
  -> thread_read(include_turns=True)
  -> thread_id
```

第一次真实运行暴露了一个 SDK 细节：

```text
thread_read(include_turns=True)
  -> InvalidRequestError:
     thread ... is not materialized yet;
     includeTurns is unavailable before first user message
```

这说明 Codex `thread/start` 可以先返回 thread id，但在 first user message / first turn 之前，thread turns 还不可读取。runtime facade 的 `codex.thread.bound` 可以仍然记录 thread id；但如果要读取 turns 或验证完整 materialized thread history，需要等首个 turn 之后。

第二次真实运行继续暴露更强约束：

```text
thread_resume(thread_id)
  -> InvalidRequestError:
     no rollout found for thread id ...
```

也就是说，仅 `thread/start` 得到的 `thread_id` 还不是一个可 resume 的 Codex rollout。Codex thread 的可恢复物理上下文需要首个 user message / turn 之后才成立。

这对 runtime facade 的影响很大：

```text
Codex thread id allocated
  != Codex thread materialized/resumable
```

ADR 0004 的 tape event 顺序仍然可以成立，但 `codex.thread.bound` 的成立条件需要更精确：不能只以 `thread/start` 成功为准，还需要 Codex runtime 已经形成可恢复 rollout。下一步应测试“thread_start + initial turn”后再 `thread_resume`。

第三次真实运行加入首个最小 turn：

```text
prompt: Reply exactly with: bub-codex-thread-materialized
```

结果：

```text
artifact:
  artifacts/spikes/real-codex-thread-service-20260610-150824/

first:
  status: bootstrapped
  thread_id: 019eb05c-753e-7412-9507-525ba8457dcd

turn:
  turn_id: 019eb05c-7715-7ee1-a253-65c4c0f4594d
  completed: true
  assistant text: bub-codex-thread-materialized

second:
  status: resumed
  same thread_id: true
  appended_events: []

thread_read(include_turns=True):
  turns: 1
```

结论更新：

```text
thread_start success
  -> thread id allocated
  -> not enough for resumable Codex context

thread_start + first turn completed
  -> rollout materialized
  -> thread_resume succeeds
```

因此 `codex.thread.bound` 在真实 adapter 中不应只表示 `thread/start` 成功。更准确的 v0 条件应是：

```text
Codex thread id allocated
and initial materialization turn completed
and thread_resume/read validation succeeds or is expected to succeed
```

这意味着 runtime facade 的下一版需要把“initial materialization turn”视为 new-thread materialization 的一部分，而不是普通 user turn。普通 turn execution 仍是后续层，但第一个 materialization turn 是建立 physical Codex context 的必要步骤。

## 修正后的 materializing adapter

新增并验证：

```text
MaterializingCodexThreadService
artifacts/spikes/real-codex-thread-service-20260610-163513/
```

结果：

```text
first_status: bootstrapped
thread_id: 019eb0ab-f759-7023-b6de-6652323abd09
turn_stream includes: turn/completed
second_status: resumed
same_thread: true
second_appended_events: []
thread_read turns: 1
```

这确认修正后的边界可行：`ensure_thread_context()` 现在可以依赖 `materialize_thread()` 返回一个已经形成 rollout、可 resume 的 Codex thread；随后才写入 `codex.thread.bound`。

## Materialization turn ref

继续把 `materialize_thread()` 的返回值从裸 `thread_id` 提升为：

```text
ThreadMaterialization
  thread_id
  turn_id
  notification_records
```

当前 runtime 先使用 `turn_id`，把 initial materialization turn ref 写入：

```text
bub.context.materialized.payload.refs.materialization_turn_id
codex.thread.bound.payload.refs.materialization_turn_id
```

fake spike 已验证：

```text
materialization_turn_id: codex-turn-1
```

真实 SDK spike 已验证：

```text
artifact:
  artifacts/spikes/real-codex-thread-service-20260610-165615/

thread_id:
  019eb0bf-36eb-7842-b652-088646798fcf

materialization_turn_id:
  019eb0bf-3b95-7472-8e06-39347c31de3d

turn_completed:
  true

resume:
  status: resumed
  same_thread: true
```

这让审计链变成：

```text
bub.anchor.created
  -> bub.context.materialized(refs.materialization_turn_id)
  -> Codex materialization turn
  -> codex.thread.bound(refs.materialization_turn_id)
```

下一步不是把整个 turn stream 塞进 binding event，而是把 materialization turn notifications 走现有 `CodexFact` normalization，再投影为 tape events，并通过 refs 关联。

## Materialization turn projection

继续新增：

```text
src/bub_codex/materialization_projection.py
```

runtime 现在会把 `ThreadMaterialization.notification_records` 归一化为 `CodexFact`，再投影成 materialization turn tape events。当前最小投影：

```text
codex.turn.materialization.started
codex.turn.materialization.completed
```

同时复用 `project_tool_events()`，如果 initial materialization turn 中出现 tool-like item，会继续投影为：

```text
bub.tool.call.*
bub.side_effect.*
```

真实 SDK spike 已验证：

```text
artifact:
  artifacts/spikes/real-codex-thread-service-20260610-171737/

event sequence:
  bub.anchor.creation.started
  bub.anchor.created
  bub.context.materialized
  codex.turn.materialization.started
  codex.turn.materialization.completed
  codex.thread.bound

refs.materialization_turn_id:
  019eb0d2-dda5-7dd0-a834-e9f657c9cdc4

resume:
  status: resumed
```

这让 new-thread materialization 的审计链进一步完整：不仅有 turn id ref，也有 materialization turn 的 started/completed facts 进入 Bub tape。

## User turn runtime spike

继续新增：

```text
src/bub_codex/turn_projection.py
scripts/spikes/real_codex_run_turn_spike.py
```

`BubCodexRuntime.run_turn()` 的最小职责：

```text
ensure_thread_context()
  -> CodexThreadService.run_turn()
  -> notification records
  -> CodexFact
  -> codex.turn.started/completed
  -> project_tool_events()
  -> append tape events
```

普通 user turn 与 materialization turn 区分：

```text
codex.turn.materialization.started/completed
  purpose: thread_materialization

codex.turn.started/completed
  purpose: user_turn
```

fake runtime spike 已验证普通 turn 投影：

```text
codex.turn.started
codex.turn.completed
```

真实 SDK smoke test：

```text
artifact:
  artifacts/spikes/real-codex-run-turn-20260610-173141/

event sequence:
  bub.anchor.creation.started
  bub.anchor.created
  bub.context.materialized
  codex.turn.materialization.started
  codex.turn.materialization.completed
  codex.thread.bound
  codex.turn.started
  codex.turn.completed

assistant texts:
  materialized
  bub-codex-user-turn
```

结论：普通 turn execution 可以在不破坏 startup/materialization 边界的前提下接入 runtime facade。下一步应在普通 user turn 中验证 tool-like items 的投影，尤其是 `fileChange` 和 `commandExecution`。
