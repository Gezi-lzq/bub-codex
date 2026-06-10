# Bub runtime hook 接入策略

日期：2026-06-10

## 问题

`bub-codex` 的目标不是把 Bub 包在 `codex e` 子进程外，而是把 Codex 作为 Bub-native coding runtime。前面已经验证了 Codex thread、turn、tool、Anchor、ToolContext 等局部边界。下一步需要判断：

```text
bub-codex 作为 Bub 插件时应该插在哪个 hook？
```

## Bub hook pipeline 观察

Bub framework 的一轮 inbound message 大致是：

```text
resolve_session
load_state
build_prompt
run_model / run_model_stream
save_state
render_outbound
dispatch_outbound
```

`BubFramework.process_inbound()` 先构造 base state：

```text
_runtime_workspace
_runtime_steering
```

然后合并所有 `load_state` 结果，再调用 `build_prompt`，最后进入 model execution hook。

Builtin implementation 提供：

```text
load_state
build_prompt
run_model
run_model_stream
system_prompt
provide_channels
...
```

其中 builtin `run_model` / `run_model_stream` 委托给 `Agent.run()` / `Agent.run_stream()`。Builtin Agent 再使用 Republic tape、tools、skills、auto-handoff。

## Hook precedence

Bub 先注册 builtin，后注册 entry-point plugins。`HookRuntime` 在执行 first-result hook 时反向迭代 hook implementations。

因此后注册的 plugin 可以覆盖 builtin 的：

```text
run_model
run_model_stream
build_prompt
...
```

同时 `HookRuntime.run_model()` / `run_model_stream()` 有互相 fallback：

```text
non-streaming request:
  if selected plugin has run_model -> call run_model
  else if selected plugin has run_model_stream -> consume stream text

streaming request:
  if selected plugin has run_model_stream -> return stream
  else if selected plugin has run_model -> wrap result as one text event
```

这意味着 `bub-codex` 如果只实现 `run_model_stream`，也能服务非 streaming 调用；框架会把 stream text 聚合为 model_output。

## 现有 bub-contrib/bub-codex

`bub-contrib/packages/bub-codex` 当前只实现 `run_model`：

```text
run_model(prompt, session_id, state)
  -> if comma command: delegate to builtin Agent.run(...)
  -> workspace = _runtime_workspace
  -> thread_id = .bub-codex-threads.json[session_id]
  -> subprocess: codex e [resume thread_id] [--model ...] prompt
  -> parse stderr "session id:"
  -> save .bub-codex-threads.json
  -> return stdout
```

这个实现验证了 Bub 插件边界，但与本项目目标相反：

- 它把 Codex 当 CLI 子进程。
- 它用 `.bub-codex-threads.json` 保存 thread binding。
- 它不能捕获 Codex SDK/app-server item lifecycle。
- 它不能把 Bub tools 作为 Codex dynamic tools 暴露。
- 它不能让 Bub tape 成为 canonical runtime fact record。

本项目应保留它的 hook 位置经验，但替换实现方式。

## 推荐 v0 hook strategy

### 第一入口：run_model_stream

推荐第一版 Bub 插件只实现：

```text
run_model_stream(prompt, session_id, state) -> AsyncStreamEvents
```

理由：

1. Codex SDK/app-server 本身是 notification stream。
2. Tool lifecycle、turn lifecycle、error、token usage 都在 stream 上更自然。
3. Bub 非 streaming 调用可以由 HookRuntime 消费 text events 得到 plain output。
4. 避免同时维护 `run_model` 和 `run_model_stream` 两套路径。

### 暂不覆盖 build_prompt / load_state

v0 不应急着替换：

```text
load_state
build_prompt
system_prompt
render_outbound
dispatch_outbound
```

理由：

- `load_state` 已经注入 `_runtime_workspace`、`_runtime_steering`，builtin 还注入 `session_id`、`_runtime_agent`。
- `build_prompt` 负责 channel message、comma command、media 的基础处理。
- `system_prompt` / skills / tools 的长期位置仍需要和 Bub builtin 对齐。
- 过早覆盖会把问题扩大到 channel、prompt、skills 和 outbound 语义。

### Comma command fallback

现有 contrib 插件对 comma command 做了 fallback：

```text
if prompt starts with ",":
  delegate to builtin Agent.run(...)
```

这个设计应保留。

原因：comma command 是人类/operator surface，不是模型调用 surface。Codex runtime 插件替换的是 model execution，不应夺走 builtin command handling。

在 `run_model_stream` 中可以采用：

```text
if prompt is string and prompt.strip().startswith(","):
  agent = state["_runtime_agent"]
  result = await agent.run(session_id=session_id, prompt=prompt, state=state)
  return text/final stream events
```

## v0 run_model_stream 内部结构

第一版可以组合现有模块：

```text
run_model_stream(prompt, session_id, state)
  -> workspace = state["_runtime_workspace"]
  -> tape_id = derive from session_id / Bub tape name
  -> ensure_thread_context()
       resolve_runtime_context from tape
       create/bootstrap Anchor if needed
       materialize/resume Codex thread
  -> build dynamic tool provider from Bub REGISTRY
       model-facing names use Bub dotted-name rewrite
       context_factory -> make_bub_tool_context(...)
       invocation_observer -> collect BubToolInvocationAuditRecord
  -> start Codex turn
       stream notifications
       normalize to CodexFact
       project turn/tool/side-effect events
       project Bub invocation audit events
       append to tape
       yield Bub StreamEvent("text", ...)
  -> final StreamEvent("final", ...)
```

## 关键设计点

### Tape identity

Bub builtin `Agent` uses:

```text
tape = agent.tapes.session_tape(session_id, workspace)
```

`bub-codex` 目前 spike 里用 `session_id` / `tape_id` 显式传入。真实插件接入时需要决定：

```text
tape_id = session tape name?
tape_id = session_id?
or use Bub TapeService directly?
```

倾向：v0 插件应尽量使用 Bub 提供的 tape service / tape store，而不是自建平行 tape identity。但在真正依赖 Bub package 前，可以继续用 `TapeStore` protocol 做中间层。

### Tools

Bub builtin 的 tool source of truth 是：

```text
bub.tools.REGISTRY
```

`bub-codex` 不应定义自己的 registry。它应把 `REGISTRY.values()` 转换成 Codex dynamic tools，并通过 `Tool.run()` 执行。

### Skills / system prompt

Builtin Agent 将 tools prompt、skills prompt、system prompt 合并进 Republic LLM call。

Codex runtime 本身也有 AGENTS.md / skills 机制。这里还没完全收敛。v0 不应先覆盖 `build_prompt`，但 `run_model_stream` 需要明确是否：

- 直接使用 Bub `prompt`，让 Codex 自己读 AGENTS.md。
- 在 Codex initial/materialization prompt 中加入 Bub system prompt / tools prompt / skills prompt。
- 复用 contrib 的 `with_bub_skills()` 把 Bub builtin skills 暴露到 `.agents/skills`。

这需要单独 spike。不要混入第一版 hook strategy。

### Streaming semantics

Bub `run_model_stream` 期望返回 Republic `AsyncStreamEvents`，其中 framework 主要消费：

```text
StreamEvent("text", {"delta": ...})
StreamEvent("error", {...})
StreamEvent("final", {"text": ..., "ok": ...})
```

Codex notification stream 需要转换为 Bub stream events。不是每个 Codex notification 都应该作为 Bub stream event；细粒度事实写 tape，用户可见文本走 `text` events。

## 当前结论

v0 插件策略：

```text
implement run_model_stream only
delegate comma commands to builtin Agent
do not override load_state/build_prompt/render_outbound
use Bub REGISTRY as tool source
construct Republic-compatible ToolContext for context=True tools
derive runtime continuity only from tape events
```

这样能把 Codex 放在 Bub model execution boundary，同时尽量保留 Bub builtin 的 channel、prompt、state、command、outbound 语义。

## 后续 spike

建议下一步做一个不依赖真实 Codex SDK 的 fake plugin spike：

```text
scripts/spikes/bub_hook_strategy_spike.py
```

验证：

1. plugin 只实现 `run_model_stream` 时，非 streaming `process_inbound()` 能拿到聚合文本。
2. streaming `process_inbound(..., stream_output=True)` 能透传 text/final events。
3. comma command fallback 会调用 state 中的 `_runtime_agent`。
4. state 中 `_runtime_workspace` 可用于 runtime workspace。
5. hook precedence 确认 plugin 覆盖 builtin run_model/run_model_stream。

通过后，再把 fake Codex runtime 换成 `BubCodexRuntime.run_turn()`。

## Spike 结果

已新增并运行：

```text
scripts/spikes/bub_hook_strategy_spike.py
```

由于当前 `bub-codex` 工作区还没有安装 `republic` / `bub` 依赖，该 spike 使用本地 fake HookRuntime 复刻 Bub 的关键语义：

```text
plugins registered as:
  builtin
  bub-codex

execution order:
  reversed plugin order

run_model fallback:
  run_model_stream -> aggregate text

run_model_stream fallback:
  run_model -> single text stream
```

验证结果：

```text
non-streaming inbound:
  model_output = codex:hello

streaming inbound:
  model_output = codex:stream

comma command:
  prompt = ,tape.info
  delegate to state["_runtime_agent"].run(...)
  model_output = command:,tape.info
```

同时确认 `run_model_stream` 能看到：

```text
state["_runtime_workspace"]
state["_runtime_agent"]
```

这个 spike 不是 Bub framework integration test，但足以验证 hook strategy 的核心假设：只实现 `run_model_stream` 可以覆盖 streaming 与 non-streaming 两种入口，并且 comma command 可以回退给 builtin agent。

## 真实 Bub framework 验证

用户同意安装 `republic` / `bub` 依赖后，在本仓库 `.venv` 中安装本地 Bub clone：

```text
uv venv --python 3.12
uv pip install -e /tmp/bub-codex-sources/bub
```

随后新增并运行：

```text
scripts/spikes/bub_real_hook_strategy_spike.py
```

验证结果：

```text
non-streaming:
  process_inbound(...)
  -> run_model_stream only plugin
  -> HookRuntime consumes text events
  -> model_output starts with codex:

streaming:
  process_inbound(..., stream_output=True)
  -> run_model_stream only plugin
  -> framework consumes text/final stream
  -> model_output starts with codex:

comma command:
  prompt = ,tape.info
  -> plugin delegates to state["_runtime_agent"].run(...)
  -> model_output = command:,tape.info
```

真实 Bub 行为还确认：

```text
普通 ChannelMessage 经 builtin build_prompt 后会变成：
  channel=$cli|chat_id=<id>
  ---Date: <iso datetime>---
  <content>

comma command 经 builtin build_prompt 后保持：
  ,tape.info
```

这强化了 v0 不覆盖 `build_prompt` 的判断：`bub-codex` 的 `run_model_stream` 应消费 Bub 已构造好的 prompt，而不是重新解析 channel message。

## Plugin skeleton

继续新增：

```text
src/bub_codex/plugin.py
scripts/spikes/bub_plugin_skeleton_spike.py
```

`BubCodexPlugin` 当前只实现：

```text
run_model_stream(prompt, session_id, state)
```

它通过注入的 `RuntimeStreamService` 处理普通 prompt，通过 `state["_runtime_agent"].run(...)` 回退处理 comma command。

真实 Bub framework spike 验证：

```text
normal prompt:
  Bub builtin build_prompt
  -> BubCodexPlugin.run_model_stream
  -> fake RuntimeStreamService

comma command:
  Bub builtin build_prompt keeps ",tape.info"
  -> BubCodexPlugin.run_model_stream
  -> state["_runtime_agent"].run(...)
```

这把 hook strategy 从 research 结论推进成了可导入的插件形状。下一步可以把 fake `RuntimeStreamService` 替换为 `BubCodexRuntime.run_turn()` 的 stream wrapper。

## Plugin 到 Runtime facade 的离线闭环

继续新增：

```text
BubCodexRuntimeStreamService
scripts/spikes/bub_plugin_runtime_stream_spike.py
```

该 spike 不接真实 Codex SDK，而是使用 fake `CodexThreadService` 验证 Bub 真实 hook pipeline 可以驱动 `BubCodexRuntime.run_turn()`：

```text
BubFramework.process_inbound
  -> builtin load_state/build_prompt
  -> BubCodexPlugin.run_model_stream
  -> BubCodexRuntimeStreamService.run_stream
  -> BubCodexRuntime.run_turn
  -> InMemoryTapeStore
  -> AsyncStreamEvents
```

验证通过：

```text
first prompt:
  creates Anchor
  materializes thread
  records codex.thread.bound
  records codex.turn.started/completed

second prompt, same session:
  resumes same thread
  records codex.turn.started/completed

comma command:
  delegates to state["_runtime_agent"].run(...)
  does not call runtime.run_turn
```

当前 event sequence：

```text
bub.anchor.creation.started
bub.anchor.created
bub.context.materialized
codex.thread.bound
codex.turn.started
codex.turn.completed
codex.turn.started
codex.turn.completed
```

这完成了第一个离线端到端 slice。下一步应解决 assistant text extraction，使 `run_model_stream` 的 text/final 输出来自 Codex assistant message，而不是 fallback `codex turn completed: <turn_id>`。
