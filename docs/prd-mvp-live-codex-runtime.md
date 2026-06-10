# PRD: Bub-native Codex Runtime MVP

## Problem Statement

`bub-codex` has validated that Codex SDK notifications can be mapped into Bub runtime facts, but the current implementation is still spike-shaped. The project needs a first MVP that turns the validated spike into a usable Bub-native Codex runtime without carrying forward temporary batch/fallback paths.

From the user's perspective, Codex should run inside Bub as a native coding runtime: turns should write structured tape events as they happen, preserve Anchor/thread context semantics, resume existing Codex threads when possible, and expose final answers through Bub's `run_model_stream` contract.

## Solution

Build an MVP Bub plugin/runtime path whose only production execution path is a live Codex notification bridge.

The runtime will:

- Package as a normal Bub plugin Python distribution.
- Depend on the real `openai-codex` Python SDK package.
- Register a `[project.entry-points."bub"]` entry point.
- Load plugin config through `@bub.config(name="codex")` and `bub.ensure_config(...)`.
- Start from Bub's `run_model_stream` hook.
- Resolve or materialize a Codex thread from Bub tape state.
- Resume existing Codex threads when a valid active binding exists.
- Consume Codex SDK notifications as they arrive.
- Normalize notifications into `CodexFact`.
- Project accepted facts into Bub tape events in source order.
- Emit Bub stream text/final only from `phase=final_answer` assistant messages.
- Preserve `phase=commentary` assistant messages in tape without sending them as `text`.
- Record successful Codex auto compaction as a Bub Anchor.
- Use real Bub TapeService/TapeStore as the canonical store.

This MVP intentionally does not include Bub dynamic tool hosting, token-level assistant streaming, approval UX, observability backend projection, or主动 compact policy.

## User Stories

1. As a Bub operator, I want Codex to run through Bub's `run_model_stream`, so that Codex participates in Bub's normal model execution pipeline.
2. As a Bub operator, I want comma commands to remain Bub-native, so that operator commands are not sent to Codex as model prompts.
3. As a Bub operator, I want Codex to resume an existing bound thread, so that coding work continues in the same physical Codex context.
4. As a Bub operator, I want resume failures to be surfaced, so that I do not unknowingly continue in a replacement thread.
5. As a Bub operator, I want new sessions to create a bootstrap Anchor, so that every Codex thread binding has a committed context boundary.
6. As a Bub operator, I want a latest Anchor without a thread binding to materialize a new Codex thread, so that handoff/new-thread flows can restart from tape state.
7. As a Bub operator, I want Codex `thread.bound` written only after materialization succeeds, so that tape does not claim a resumable thread before Codex has a rollout.
8. As a Bub operator, I want live Codex notifications written to tape as they arrive, so that the tape reflects the runtime timeline.
9. As a Bub operator, I want command executions recorded as started/completed/failed, so that I can inspect what Codex ran and how it ended.
10. As a Bub operator, I want file changes recorded as side effects, so that code edits are durable facts rather than terminal text.
11. As a Bub operator, I want failed command attempts recorded, so that retry behavior is visible.
12. As a Bub operator, I want assistant commentary preserved in tape, so that the reasoning-facing progress narrative is auditable.
13. As a Bub operator, I want only final answers emitted as Bub final text, so that user-visible model output is not polluted by progress commentary.
14. As a Bub operator, I want Codex auto compaction to create a Bub Anchor, so that Bub's context boundary follows Codex's physical context boundary.
15. As a Bub operator, I want compact summaries to be optional, so that Anchor creation does not depend on parsing private rollout details.
16. As a developer, I want a single production runtime path, so that live and batch behavior do not diverge.
17. As a developer, I want batch behavior kept out of the MVP contract, so that fallback paths do not腐化 the runtime semantics.
18. As a developer, I want real Bub TapeService integration, so that the MVP is Bub-native rather than an in-memory spike.
19. As a developer, I want explicit Codex runtime config, so that binary path, workspace, sandbox, approval policy, and overrides are controlled.
20. As a developer, I want the runtime to inherit Bub host env/PATH by default, so that workspace instructions match the actual command environment.
21. As a developer, I want env overrides, so that deployments can repair PATH or inject runtime-specific variables.
22. As a developer, I want `approval_policy=never` and `sandbox=danger-full-access` for MVP, so that approval UX does not block the first integration.
23. As a developer, I want `agentMessage` delta events excluded from canonical tape, so that tape records stable message boundaries rather than token chunks.
24. As a developer, I want `agentMessage` completed events recorded with phase, so that commentary and final answer remain distinguishable.
25. As a developer, I want MVP scope to exclude Bub dynamic tools, so that runtime/tape integration ships before tool-hosting complexity.
26. As a Bub operator, I want `bub-codex` installed as a normal Bub plugin package, so that Bub discovers it through the same entry point mechanism as other plugins.
27. As a developer, I want plugin settings registered with Bub config, so that runtime configuration lives in Bub's normal config/env system.
28. As a developer, I want the MVP entry point to construct the live runtime service directly, so that the installed package exercises the same production path as tests.
29. As a developer, I want missing Codex SDK/runtime configuration to surface as an explicit stream error, so that the plugin does not silently fall back to another execution path.

## Implementation Decisions

- Production runtime uses the live notification bridge as the only MVP execution path.
- Batch `run_turn` behavior may remain as spike/reference code, but it is not an MVP fallback path.
- MVP is an installable Bub plugin package with `pyproject.toml` and `[project.entry-points."bub"]`.
- MVP declares a real `openai-codex` dependency; `sdk_python_path` is only a local-development override.
- The entry point target is a callable factory that receives Bub `BubFramework` and returns `BubCodexPlugin`.
- Plugin config is registered as `codex` via `@bub.config(name="codex")`.
- Runtime code calls `bub.ensure_config(BubCodexSettings)` to read config; it does not instantiate config at module import time.
- The package must be installed into the same Python environment as Bub.
- Codex notifications are parsed as source signals. Bub tape event names are Bub domain events, not raw Codex `method` strings.
- Projection uses `method + payload.item.type + payload.item.status + payload.item.phase`.
- MVP tape scope includes:
  - Anchor/thread/materialization events
  - turn started/completed
  - `agentMessage` completed with `phase=commentary|final_answer|null`
  - `commandExecution` started/completed/failed
  - `fileChange` started/completed/failed
  - Codex successful compaction to Anchor
- MVP tape scope excludes:
  - `agentMessage` delta as canonical tape event
  - token-level streaming
  - reasoning item schema
  - token usage schema
  - hook event schema
  - Bub dynamic tool hosting
  - observability backend projections
- `phase=commentary` writes tape only.
- `phase=final_answer` writes tape and drives Bub `text` / `final.text`.
- If no `phase=final_answer` exists, final text falls back to the last completed assistant message.
- Codex auto compaction observed in stream creates a Bub Anchor with `method=compact`, `reason=auto_compact`, and `initiator=codex_runtime`.
- MVP does not主动 call `thread.compact()` and does not implement context overflow policy.
- Resume existing Codex thread is required.
- Resume failure is surfaced and does not silently create a new thread.
- Real Bub TapeService/TapeStore is required for the production path.
- `InMemoryTapeStore` remains available for tests/spikes only.
- MVP runtime config is explicit and includes Codex binary, workspace, config overrides, approval policy, sandbox, and env overrides.
- MVP defaults to maximum local permission: `approval_policy=never`, `sandbox=danger-full-access`.
- Runtime inherits Bub host env/PATH by default.
- The formal package entry point constructs the live runtime bridge and explicitly errors if the Codex SDK/runtime cannot be imported or configured.

## Testing Decisions

- Tests should cover behavior at the runtime stream boundary rather than private helper internals.
- Existing live bridge tests are the closest prior art: fake notification streams should assert resulting stream events and tape event ordering.
- Add tests for:
  - Bub entry point target loads
  - configured plugin factory returns a `BubCodexPlugin`
  - plugin factory constructs live runtime path, not batch fallback
  - installed package entry point is visible to `importlib.metadata`
  - `bub hooks` reports `run_model_stream: builtin, codex`
  - real BubFramework can load the installed plugin and route `run_model_stream` through the live bridge
  - commentary writes tape but does not emit `text`
  - final answer emits `text` and `final`
  - command started/completed/failed ordering
  - fileChange started/completed ordering
  - resume success path
  - resume failure surfaced
  - latest Anchor with no binding materializes a new thread
  - Codex compaction notification creates Anchor
  - real TapeService adapter append/load behavior
  - Republic `FileTapeStore` persists and reloads `bub-codex` events
  - runtime context can be derived from persisted tape events only
- Keep real Codex SDK smoke tests explicit/manual because they depend on external runtime and model execution.
- Preserve mapping artifacts from real smoke tests as evidence, not as required unit fixtures.

## Packaging Acceptance Criteria

- `pyproject.toml` declares the project as a Python package.
- `[project.entry-points."bub"]` registers the plugin.
- The entry point follows Bub's plugin callable convention and accepts `BubFramework`.
- Settings subclass uses `@bub.config(name="codex")`.
- Runtime config uses `bub.ensure_config(...)`.
- README documents editable install, `bub hooks` verification, and minimum config.
- Unit tests verify `openai_codex` importability from the installed dependency.
- Plugin runs in Bub's Python environment and uses the live bridge as the only production path.
- Editable install plus `bub hooks` confirms Bub discovers the package entry point.
- Real BubFramework smoke confirms the installed plugin can run one live Codex turn through `run_model_stream`.
- Republic tape adapter test confirms persisted tape can drive `resume_thread` resolution.
- Live bridge resume test confirms existing thread bindings are resumed instead of replaced.
- Live bridge compact test confirms Codex compaction notifications create Bub Anchors.

## MVP Hardening Still Remaining

- Real SDK resume smoke across two framework turns should be added once test runtime cost is acceptable.
- Real SDK compact smoke should remain explicit/manual until compact triggering is deterministic enough for local CI.
- Async Republic tape stores are detected but not yet supported from an already-running event loop by `RepublicTapeStoreAdapter`.

## Out of Scope

- Bub dynamic tool hosting and ToolContext production contract.
- Token-level assistant streaming from `item/agentMessage/delta`.
- Active/manual compact triggering.
- Context overflow policy.
- Approval UX or policy engine.
- Langfuse, OTel, or Obelisk projections.
- Replay engine or UI timeline.
- Full schema for reasoning, token usage, hook events, MCP tools, collab agents, web search, and image items.

## Further Notes

Current validated spike artifacts include:

- `artifacts/spikes/real-codex-plugin-fibonacci-stream-20260610-225755/result.json`
- `artifacts/spikes/real-codex-notification-mapping-20260611-011823/mapping.json`
- `artifacts/spikes/real-codex-live-stream-20260611-015408/result.json`

The project is currently a validated spike prototype, not an MVP. This PRD defines the first MVP boundary for converting the live runtime path into a Bub-native production integration.
