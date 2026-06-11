# bub-codex Context

## Purpose

`bub-codex` exists to explore and implement Codex as a Bub-native coding runtime.

The project should avoid treating Codex as an opaque command invoked through `codex e`. Bub should be able to participate in the runtime lifecycle directly: context assembly, hook execution, tool mediation, tape recording, observability, and long-running session continuity.

## Background

Bub is a hook-first runtime for collaborative agents. Its model emphasizes explicit runtime stages, shared environments, and recorded context. `bub-contrib` already contains a Codex-related package, but that package delegates model execution to the Codex CLI. This repo is for the deeper integration path.

Tape Systems contributes the context model: durable, append-only facts that can support observability, evaluation, and training. Obelisk contributes a related agent-history lens: sessions, messages, tool calls, subagents, workflows, file history, failures, and parent chains should be queryable by agents. Langfuse's Codex observability plugin is a useful reference for structured tracing of Codex turns and tools. Multica's Codex daemon integration is a useful reference for production runtime hardening: per-task execution environments, `CODEX_HOME` management, Codex config blocks, liveness diagnostics, and session pinning. It should not replace the tape-first event model because it records a narrower task-message stream rather than a canonical event log.

## Ubiquitous Language

- **Bub**: The host runtime for agents, hooks, channels, tapes, tools, and shared execution.
- **Codex**: The coding agent runtime being embedded.
- **Bub-native coding runtime**: A Codex integration where Bub owns or participates in runtime stages directly, rather than supervising a CLI subprocess from the outside.
- **Hook**: A Bub extension point that can observe or reshape a turn stage.
- **Tape**: Durable record of facts and events from a session.
- **Anchor**: A tape event that declares a committed LLM context materialization boundary. Codex threads are bound to Anchors.
- **Anchor creation**: An attempt/transaction to create an Anchor. Failed attempts are recorded but do not produce an `anchor_id`.
- **Handoff**: A transition that creates an Anchor by compacting the active Codex thread or binding a new Codex thread.
- **Codex thread**: Codex runtime context instance. It is related to a Bub session but is not the canonical session identity.
- **Turn**: One agent interaction cycle, including context assembly, model execution, tool calls, edits, and final response.
- **Tool call**: A structured request by the agent to perform an external action.
- **Observability event**: Structured telemetry about runtime behavior, suitable for tracing, debugging, evaluation, or replay.
- **Session history**: The durable, queryable record of past work.
- **Translator**: A Module that converts one runtime representation into another while preserving source attribution and ordering. In `bub-codex`, the Codex turn Translator owns the source-signal-to-tape/stream interpretation so callers do not need to know raw Codex notification shape.

## Product Boundary

In scope:

- Define a Bub-native interface for Codex turn execution.
- Represent Codex events as Bub runtime events and tape entries.
- Preserve tool calls, file edits, and subagent activity as structured data.
- Provide observability integration points.
- Make past coding sessions queryable by agents.

Out of scope for the initial scaffold:

- Reimplementing Codex model behavior.
- Building a generic terminal wrapper around `codex e`.
- Locking the project to one observability backend before the runtime boundary is clear.
- Importing all of `bub-contrib` into this repository.
- Designing a full approval UX or policy engine. The initial runtime should assume maximum local permissions.

## Current Spike Status

The current implementation is a validated spike prototype, not an MVP.

Validated:

- Real Codex SDK turns can be run from the Bub plugin/runtime path.
- Codex raw notifications can be normalized into `CodexFact`.
- `CodexFact` can be projected into Bub tape events for assistant messages, command executions, command failures, file changes, Anchor bootstrap, thread materialization, and turn lifecycle.
- Tape event ordering now follows Codex notification source order.
- Real smoke artifacts exist for simple final-answer turns, command/tool turns, Fibonacci file creation, and raw-notification mapping.

Still spike-shaped:

- Most executable proof lives under `scripts/spikes/*`.
- The tape store is still `InMemoryTapeStore`.
- `BubCodexRuntime.run_turn()` still batches notifications until `turn/completed`; this is a reference/spike path for validating runtime and projection rules, not the MVP production stream path.
- Real SDK smoke tests are manual and external-runtime dependent.
- There is no installable/configurable Bub plugin MVP yet.

Representative research summary:

- [Validated Spike Summary](docs/research/2026-06-11-validated-spike-summary.md)

## Current Decisions

- The canonical record is Bub tape facts, not Codex rollout JSONL, Langfuse traces, OTel spans, or Obelisk query tables.
- Codex source signals, `CodexFact` adapter facts, and Bub tape events are separate layers. Source signals are Codex-defined input material; `CodexFact` is a Codex-specific anti-corruption boundary; tape events are Bub-accepted canonical facts. Langfuse, OTel, UI timelines, and query tables are projections from those facts.
- `session_id`, tape name, Codex `thread_id`, turn id, and Anchor id are related but distinct identities.
- `Anchor` owns committed LLM context materialization boundaries. Codex `thread_id` owns runtime context continuity.
- Anchor `summary` is optional enrichment; Anchor existence does not depend on whether a Codex thread is already bound.
- Anchor event schema keeps minimal top-level fields: `anchor_id`, `method`, `reason`, `created_at`, `state`, and `refs`; optional details live under `state` or `refs`.
- v0 does not support soft Anchor. Ordinary checkpoints, phase markers, notes, and UI timeline boundaries are not Anchors unless they are used as compact or new-thread materialization boundaries.
- `Anchor + compact` is allowed: Bub may use Codex `thread.compact()` to compress the same thread's physical context, then create an Anchor from the successful compaction.
- Successful Codex auto compaction also creates a Bub Anchor with `reason=auto_compact` and `initiator=codex_runtime`.
- A Codex turn may cross Anchors. `anchor_id` is event-level attribution, not a turn-level invariant.
- `active_anchor_id` is derived from the latest committed `bub.anchor.created` tape event, not stored as canonical mutable state.
- `active_thread_id` is also derived only from tape `codex.thread.bound` events; v0 does not use `.bub-codex-threads.json` as state.
- If multiple thread bindings exist for one Anchor, v0 uses latest-bound-wins and reserves explicit unbound/archive events for later.
- `codex.thread.bound` must carry `anchor_id`; new thread binding always attaches to an existing Anchor.
- `Anchor + new_thread` does not automatically archive the previous Codex thread in v0; it only records the active binding switch.
- New Codex thread initial context is materialized from Bub tape facts, Anchor state, current intent, and runtime metadata; it does not directly copy old Codex thread history.
- `bub.context.materialized` is required for new-thread materialization to audit selected tape facts and initial input.
- Codex SDK/app-server tool calls are observable through item lifecycle events. v0 should project `commandExecution`, `mcpToolCall`, `dynamicToolCall`, `collabAgentToolCall`, `webSearch`, `imageView`, and `fileChange` into Bub tape facts instead of relying only on final assistant text.
- `dynamicToolCall` can be projected from Codex item lifecycle into Bub tool facts. Live dynamic tool execution works through the lower-level `CodexClient.thread_start(raw dict dynamicTools=...)` path plus `approval_handler` handling `item/tool/call`; the high-level `AsyncCodex.thread_start()` still does not expose `dynamicTools`.
- Bub tools should be exposed to Codex through a dynamic tool provider adapter, not by making runtime/domain code depend on raw app-server JSON-RPC. The v0 mapping uses `namespace=bub`, converts Bub registry names to Codex-safe names by replacing non `[a-zA-Z0-9_-]` characters with `_`, preserves Codex-name-to-Bub-name mapping, and fails fast on collisions.
- Bub `ToolContext` is host-runtime injected context, not model-provided tool input. Codex dynamic tool schema should expose only model arguments. `bub-codex` should prefer constructing Bub/Republic-compatible `ToolContext(tape, run_id, state)` from `DynamicToolCall` plus session/tape/anchor runtime state instead of inventing a new context type.
- Bub dynamic tool handler failure should return `success=false` to Codex instead of breaking app-server transport, so the failed dynamic tool remains observable through Codex item lifecycle and Bub tape projection.
- Bub dynamic tool execution has a host-side audit path separate from Codex item lifecycle: `BubToolInvocationAuditRecord` can be projected into `bub.tool.invocation.started/completed/failed`, while Codex `dynamicToolCall` item lifecycle still projects the model/runtime-facing call as `bub.tool.call.*`.
- `BubDynamicToolProvider` should execute real Bub/Republic tools through `Tool.run()` when available, falling back to raw `handler` only for lightweight spike/test tool shapes.
- A new Bub session with no Anchor first creates a bootstrap Anchor with `reason=session_start`, keeping Bub builtin-compatible state such as `owner=human`, then binds a Codex thread to it.
- If the latest committed Anchor has no Codex thread binding, v0 may automatically materialize and bind a new thread from that Anchor.
- If new-thread binding fails after Anchor creation, the Anchor remains and later startup can retry materializing a thread from it.
- If a bound Codex thread fails to resume, v0 exposes the failure instead of automatically binding a replacement thread.
- The first runtime facade boundary should only ensure a usable Codex thread context. Turn execution, stream normalization, and tape projection remain separate layers.
- `BubCodexRuntime.ensure_thread_context()` composes `TapeStore`, `resolve_runtime_context`, Anchor creation, context materialization, Codex thread materialize/resume, and binding/failure tape events.
- Real Codex SDK testing shows `thread/start` only allocates a thread id; before the first user message / materialization turn, `thread_read(include_turns=True)` and `thread_resume` fail because no rollout exists. Therefore real `codex.thread.bound` should be written only after the initial materialization turn completes and the thread is resumable.
- `CodexThreadService.materialize_thread()` is the runtime facade boundary. It should return only after the Codex thread has a resumable rollout. `LowLevelCodexThreadService.create_thread()` remains a low-level allocation helper, not a production binding condition.
- `ThreadMaterialization` carries `thread_id` and `turn_id`; `bub.context.materialized` and `codex.thread.bound` record `refs.materialization_turn_id` so the initial materialization turn can be audited separately from ordinary user turns.
- Initial materialization turn notifications are normalized into `CodexFact` and projected into tape as `codex.turn.materialization.started/completed`; tool-like items from that turn can reuse the normal tool/side-effect projection path.
- `BubCodexRuntime.run_turn()` is the first ordinary user-turn facade. It reuses `ensure_thread_context()`, runs a Codex turn, normalizes notifications into `CodexFact`, projects `codex.turn.started/completed` with `purpose=user_turn`, and reuses tool/side-effect projection.
- The first Bub plugin integration should implement `run_model_stream` only, delegate comma commands back to the builtin agent, avoid overriding `load_state` / `build_prompt` / outbound hooks, use Bub `REGISTRY` as the tool source, and let HookRuntime consume the stream for non-streaming calls.
- `src/bub_codex/plugin.py` now provides the minimal `BubCodexPlugin` skeleton. It exposes only `run_model_stream`, delegates comma commands to `state["_runtime_agent"].run(...)`, and delegates normal prompts to an injected `RuntimeStreamService`.
- `BubCodexRuntimeStreamService` connects `BubCodexPlugin` to `BubCodexRuntime.run_turn()` for reference/spike tests. It is not the MVP production runtime path; when it emits Bub stream events, it reuses the Codex turn Translator's final-answer semantics so batch/reference behavior does not diverge from the live bridge.
- Codex SDK supports live turn notification streaming; the current `BubCodexRuntime.run_turn()` path intentionally batches notifications until `turn/completed` only as a temporary adapter shape for validating projection rules. A Bub-native coding runtime should grow a live notification bridge that consumes Codex events as they arrive, appends tape events in source order, and yields Bub stream/progress events without waiting for the whole turn to finish.
- `agentMessage` items carry `phase=commentary | final_answer | null` from Codex. `phase=final_answer` is a Codex-provided semantic marker, not inferred by Bub. Tape should preserve all assistant message completions in order, while Bub `final.text` should prefer `phase=final_answer` and fall back to the last assistant message if no final-answer phase is available.
- `BubCodexLiveRuntimeStreamService` is the first live notification bridge spike. It reuses `ensure_thread_context()`, consumes ordinary user-turn notification records as they arrive, appends projected tape events immediately in source order, keeps `phase=commentary` out of `StreamEvent("text")`, and emits `phase=final_answer` as Bub `text` and `final.text`. This validates the live bridge direction but does not yet replace the default batch service as production runtime.
- Current-thread notification filtering 属于 Codex stream 输入边界，而不是 Translator 语义投影层。`MaterializingCodexThreadService` 会忽略 `threadId` 不等于当前 thread 的 notification，避免 foreign `turn/completed` 结束当前 turn；`BubCodexLiveRuntimeStreamService` 也会防御性过滤 foreign-thread records，确保背景 thread / subagent notification 不进入当前 Bub tape。缺失 `threadId` 的 notification 暂时保留给 Translator 处理。
- MVP 使用最小 tape-side runtime diagnostic event：`bub.runtime.error`。它记录 Bub/Codex runtime 边界失败，而不是模型语义事件；v0 payload 只包含 `stage`、`error_type`、`message` 和 tape event 顶层的 session/tape/anchor/thread/turn refs。`thread_resume` failure 和 live `turn_stream` failure 会写该事件并继续通过 Bub stream 返回 `error/text/final`。`codex_version`、`stderr_tail`、`last_semantic_activity`、环境快照等诊断字段推迟到 runtime hardening 阶段。
- MVP 必须按 Bub plugin package 规范收敛：`pyproject.toml` 注册 `[project.entry-points."bub"]`，入口为可调用工厂 `bub_codex.plugin:create_plugin`，插件安装到运行 Bub 的同一 Python 环境中，由 Bub entry point loader 加载。
- Bub Codex 配置属于 Bub plugin config：`BubCodexSettings` 使用 `@bub.config(name="codex")` 注册，运行时通过 `bub.ensure_config(BubCodexSettings)` 读取，不在模块 import 时创建全局配置实例。
- MVP 插件入口只实现 `run_model_stream`，保留逗号命令委托给 Bub builtin agent，不覆盖 `load_state`、`build_prompt`、`save_state`、`render_outbound` 或 channel hooks。
- MVP 正式入口使用 live notification bridge；配置缺失或 Codex SDK 不可导入时暴露明确错误，不切换到 batch fallback。
- `openai-codex` 是项目依赖，提供 `openai_codex` SDK import；`sdk_python_path` 仅作为开发期覆盖本地 SDK checkout 的 escape hatch。
- Editable install 后，Bub entry point discovery 应报告 `codex bub_codex.plugin:create_plugin`，`bub hooks` 应报告 `run_model_stream: builtin, codex`。
- 真实 BubFramework smoke 已验证 installed plugin 可被加载，并通过 `run_model_stream` live bridge 完成一轮 Codex turn。
- MVP runtime 优先接入 Bub/Republic tape store，并通过 `RepublicTapeStoreAdapter` 只还原 `bub-codex` 自己写入的事件；`InMemoryTapeStore` 仅用于测试、spike 或显式禁用 Bub tape store 的开发场景。
- `RepublicTapeStoreAdapter` 已通过 Bub `FileTapeStore` 持久化读回测试，可从真实 Republic tape entries 还原 `bub-codex` 事件并仅从 tape 推导 `resume_thread` runtime context。
- Resume existing Codex thread 的 MVP 语义已有 live bridge 测试：当 tape 中存在 latest Anchor 与 `codex.thread.bound`，runtime 不 materialize 新 thread，而是调用 `resume_thread(thread_id)` 后继续 ordinary turn。
- Codex stream 中的 `contextCompaction` completed item 会投影为 `codex.thread.compacted` 与新的 `bub.anchor.created(method=compact, reason=auto_compact, initiator=codex_runtime)`。
- Codex turn Translator 的外部 Interface 应输入 raw Codex notification record，并在 Implementation 内部拥有 `raw notification -> CodexFact -> TapeEvent -> Bub stream decision` 的解释链。这样 `live_stream` 不需要知道 Codex raw shape、adapter fact 中间形态或 final-answer collection 规则，保持高内聚。
- Codex turn Translator 的输出 Interface 应是 `TapeEvent[] + StreamDecision[]`，而不是直接输出 Republic `StreamEvent`。Translator 负责语义决策，`live_stream` 负责把 `StreamDecision` 转成 Bub/Republic transport object。
- Codex turn Translator 应是 per-turn 有状态 Module，生命周期为 `accept(record)` 多次加 `finish()` 一次。Translator 内部持有 final-answer texts 与 fallback text，`live_stream` 不再知道 `phase=final_answer`、fallback text 或 final aggregation 规则。
- Package root `bub_codex` should stay narrow and expose plugin-facing entrypoints, not projection helpers, tape internals, runtime adapters, or spike-only utilities. Tests and spike scripts should import internal Modules directly when they need internal surfaces.
- Multica's Codex integration suggests a future `CodexEnvironment` Module, but not for the first MVP. Its strongest lessons are platform-aware sandbox/config handling, managed `config.toml` blocks, current-thread notification filtering, semantic inactivity diagnostics, and early session/thread pinning. `bub-codex` should absorb those as hardening inputs while keeping Bub tape as the canonical record.
- v0 may parse Codex internal rollout format to enrich compaction Anchors with Codex compact summaries, but this must live behind a versioned adapter and fail open.
- If compaction snapshot parsing fails, v0 records summary failure status and does not automatically run a Bub-generated second summary.
- Maximum local permission is acceptable for v0; approval UX and policy governance are later layers.

## Open Design Questions

- What direct Codex APIs or libraries should this project depend on?
- When should the direct Git dependency on `openai-codex` be replaced by a pinned package index version?
- What is the smallest executable vertical slice after the Bub package entry point exists?
- Should real SDK resume and real SDK compact smoke tests become mandatory local checks, or remain manual because they depend on external model/runtime behavior?
- What tape schema is needed for coding sessions?
- What should be replayable versus merely observable?
- How should user-interruption semantics map into Bub?
- If approval support is added later, how should it layer over the maximum-permission runtime without changing the canonical event model?
- What normalized schema should `CodexRolloutCompactionReader` produce for local, remote, and auto compaction?
- What is the minimal stable Bub tool / side-effect event schema for Codex item lifecycle projection?
- Which `ToolContext.state` keys should become stable for Codex dynamic tool calls?
- Should Bub dynamic tool handlers have a host-level timeout/cancellation wrapper in v0?
- What is the exact live bridge contract from Codex notifications to Bub `AsyncStreamEvents`, especially commentary/progress versus final answer text?
- When should `bub-codex` introduce a `CodexEnvironment` Module, and which responsibilities belong there versus Bub plugin settings?

## ADR Index

Architectural decisions should live under `docs/adr/`.

- [ADR 0001: Codex compact summary 的来源与边界](docs/adr/0001-codex-compaction-summary-source.md)
- [ADR 0002: Tape、Anchor、Handoff 与 Codex thread 的语义关系](docs/adr/0002-tape-anchor-thread-semantics.md)
- [ADR 0003: Codex Runtime Adapter Facts](docs/adr/0003-codex-runtime-adapter-facts.md)
- [ADR 0004: Runtime Resolution 与 New-thread Materialization](docs/adr/0004-runtime-resolution-and-new-thread-materialization.md)
- [ADR 0005: Dynamic Tool Provider Boundary](docs/adr/0005-dynamic-tool-provider-boundary.md)
- [ADR 0006: Event Contract Layers and Namespaces](docs/adr/0006-event-contract-layers-and-namespaces.md)
- [ADR 0007: Bub Plugin Entry Point Uses run_model_stream](docs/adr/0007-bub-plugin-entrypoint-run-model-stream.md)
