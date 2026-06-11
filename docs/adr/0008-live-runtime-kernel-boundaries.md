# ADR 0008: Live Runtime Kernel Boundaries

日期：2026-06-11

## 状态

Accepted

## 背景

`bub-codex` 的 live runtime 不只是 `run_model_stream` 的实现细节。它是 Bub tape、Anchor、Codex thread、Codex turn notification、Bub stream output 之间的 runtime kernel。

前序实现已经形成基本分层：

```text
plugin.py
  Bub hook entrypoint

runtime_services.py
  composition root, runtime cache, SDK client construction

runtime.py
  tape-first Anchor/thread resolution

live_stream.py
  live stream orchestration

turn_translator.py
  Codex notification -> tape/stream semantic translation
```

继续推进时，不能把 compact continuity、async tape store、stream cancellation、materialization error propagation 当成孤立补丁。它们暴露的是同一组 runtime kernel 边界还不够显式。

## 决策

### Runtime Context

Runtime context resolution 返回的是能力，而不是普通启动状态。

```text
RuntimeContext
  -> ExecutableContext
  -> ContextUnavailable
```

`ExecutableContext` 表示当前 Bub tape 已经能提供一个可执行 Codex context：

```text
session_id
tape_id
anchor_id
thread_id
cwd
source: resumed | bootstrapped | materialized
appended_events
```

`ContextUnavailable` 表示 runtime 已经尝试从 tape/Anchor 得到可执行 context，但失败原因必须显式可见：

```text
session_id
tape_id
anchor_id?
cwd
reason
error
appended_events
```

Live stream 不应该通过 `thread_id is None` 推断失败。它应该消费 `ExecutableContext | ContextUnavailable`，并把 `ContextUnavailable.error` 原样映射到 Bub stream error/final。

### Binding Makes an Anchor Executable

`codex.thread.bound` 是 Anchor 到可恢复 Codex thread 的 canonical signal。

任何 active Anchor 只要可继续执行，就必须有显式 binding，包括 compact Anchor。

Compact path 的事件顺序为：

```text
codex.thread.compacted
codex.compaction.snapshot?
bub.anchor.created(method=compact)
codex.thread.bound(reason=compact_continuity, thread_id=same_thread_id)
```

这样 runtime resolver 不需要为 compact Anchor 偷看 `bub.anchor.created.refs.thread_id`。统一规则仍然是：

```text
latest Anchor
  -> latest codex.thread.bound for that Anchor
  -> resume thread
```

### Codex Turn Is a Resource

Codex turn stream 不是裸 iterator，而是有生命周期的 runtime resource：

```text
turn_start
notification subscription
records
unregister
cancel / interrupt
```

引入显式 turn session seam：

```text
CodexTurnSession
  records()
  close()
```

Live orchestration 必须保证：

```text
session = codex_execution.start_turn_stream(...)
try:
  for record in session.records():
    ...
finally:
  session.close()
```

这样 stream 正常结束、异常、consumer cancellation 都能释放 Codex notification subscription。

### Async Shell, Sync Pure Core

Live runtime 位于三个异步边界交汇处：

```text
Bub async stream hook
Codex notification stream
Tape / observability IO
```

因此 live orchestration 应保持 async interface。

但同步纯核心应保持同步：

```text
Translator
Projection functions
Context materialization helpers
Schema/hash/id helpers
```

最终 `RuntimeTape` 应是 async-first port：

```text
RuntimeTape
  async events(...)
  async append(...)
  async append_many(...)
  async resolve_context(...)
```

v0 在完成 async-first migration 前，不假装支持 running event loop 内的 async-only Republic tape store。遇到 async-only store 应 fail fast，并给出明确错误。

### Live Stream Ordering

用户 turn 的 live ordering 不变：

```text
Codex notification
  -> CodexFact
  -> Bub tape events append
  -> Bub stream decisions emit
```

Tape append 必须先于对应 stream emit。Delta text 是 stream optimization，不是 canonical tape；completed assistant message 仍写 tape。

## 后果

- `BubCodexLiveRuntimeStreamService` 应逐步变成薄 orchestration shell。
- `RuntimeContextKernel.ensure_executable_context()` 返回 `ExecutableContext | ContextUnavailable`，live runtime 不再通过 `BubCodexRuntime` facade 获取 context。
- `MaterializingCodexThreadService` 提供显式 turn session，而不是只暴露 generator。
- Compact Anchor 创建时必须追加 `codex.thread.bound(reason=compact_continuity)`。
- Async tape store 支持是单独的 runtime tape port 迁移，不用局部 `asyncio.run()` 补丁解决。

## 非目标

- 不在本 ADR 一次性完成 async-first `RuntimeTape` 迁移。
- 不把 `CodexTurnTranslator` 改成 async。
- 不让 Translator 负责 tape append、thread filtering、notification unregister。
- 不把 compact Anchor 的 thread continuity 隐式藏在 resolver 特判里。

## 分阶段落地

第一阶段：

```text
compact_continuity binding
ExecutableContext / ContextUnavailable result model
CodexTurnSession lifecycle seam
bind failure root-cause stream propagation
async tape store fail-fast boundary
```

第二阶段：

```text
RuntimeTape async-first port
live runtime async tape read/append
batch/reference path adapter 化
删除 sync-over-async tape 调用
```
