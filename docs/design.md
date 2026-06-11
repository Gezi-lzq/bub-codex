# Design

`bub-codex` embeds Codex into Bub as a native runtime. Bub remains responsible
for workspace selection, session identity, tape storage, and stream delivery.
Codex is responsible for model turns, tool execution through its SDK runtime,
and Codex thread continuity.

## Bub Turn Pipeline Integration

`bub-codex` does not replace Bub's turn pipeline. It participates at the model
execution hook:

```text
inbound
  -> resolve_session
  -> load_state
  -> build_prompt
  -> run_model_stream        # handled by bub-codex
  -> save_state
  -> render_outbound
  -> dispatch_outbound
```

Bub still owns session resolution, state loading, prompt construction, state
saving, outbound rendering, and outbound dispatch. `bub-codex` only changes what
happens inside `run_model_stream` for normal chat prompts.

Comma commands remain Bub-native. When the prompt is a comma command such as
`,help`, `BubCodexPlugin` delegates back to the builtin Bub agent instead of
sending the command to Codex.

## Runtime Flow

```text
Bub run_model_stream hook
  -> BubCodexPlugin
  -> LazyRuntimeStreamService
  -> BubCodexLiveRuntimeStreamService
  -> RuntimeContextKernel.ensure_executable_context
  -> MaterializingCodexThreadService.start_turn_stream
  -> CodexTurnTranslator
  -> TapeStore.append_many
  -> Bub AsyncStreamEvents
```

The live path is the production path. `BubCodexRuntime.run_turn()` remains a
batch/reference facade for tests and projection checks.

Inside `run_model_stream`, the runtime flow is:

```text
prompt + session_id + Bub state
  -> resolve workspace and tape id
  -> ensure executable Codex thread context from Bub tape
  -> start a Codex turn session
  -> consume Codex notification records
  -> translate records into Bub tape events and stream decisions
  -> append tape events before emitting corresponding stream output
  -> close the Codex turn session
```

## Codex SDK Boundary

`bub-codex` integrates with the Codex Python SDK through
`openai_codex.client.CodexConfig` and `CodexClient`.

Runtime construction passes the following values into `CodexConfig`:

- `codex_bin`: optional path to the Codex CLI binary
- `cwd`: resolved Bub workspace
- `config_overrides`: approval policy, sandbox mode, and user overrides
- `env`: optional environment overrides
- `experimental_api=True`

The runtime then constructs `CodexClient(config=...)`, calls `client.start()`,
and calls `client.initialize()`. This happens lazily inside Bub's turn lifecycle,
not at Python import time.

Thread continuity is handled by `MaterializingCodexThreadService`:

```text
new thread materialization
  -> client.thread_start(...)
  -> client.turn_start(thread_id, materialization_prompt, {"cwd": cwd})
  -> client.next_turn_notification(turn_id) until turn/completed
  -> client.unregister_turn_notifications(turn_id)
  -> client.thread_read(thread_id, include_turns=True)
  -> write codex.thread.bound

existing thread resume
  -> client.thread_resume(thread_id, {"cwd": cwd, "approvalPolicy": ..., "sandbox": ...})

user turn streaming
  -> client.turn_start(thread_id, prompt, {"cwd": cwd})
  -> client.next_turn_notification(turn_id) until turn/completed
  -> client.unregister_turn_notifications(turn_id)
```

The materialization turn is separate from the user turn. It exists only to make
the new Codex thread resumable and to bind it to a Bub Anchor. The user's prompt
is sent in the later user turn.

## Identity Model

The runtime keeps these identities separate:

- `session_id`: Bub conversation/session identity.
- `tape_id`: Bub tape identity for persisted runtime history.
- `anchor_id`: Bub context boundary identity.
- `thread_id`: Codex thread identity.
- `turn_id`: Codex turn identity.

The tape is the source of truth. A Codex thread is not treated as the active
thread for a Bub session unless the tape contains a valid `codex.thread.bound`
event for the active Anchor.

## Anchors And Threads

An Anchor is a committed Bub context boundary. It can be created when a session
starts, when a thread must be materialized from an existing Anchor, or when Codex
compaction produces a new context boundary.

Startup resolution follows the tape:

```text
tape has latest Anchor + thread binding
  -> resume that Codex thread

tape has latest Anchor but no thread binding
  -> materialize a new Codex thread from that Anchor
  -> write bub.context.materialized
  -> write codex.thread.bound

tape has no Anchor
  -> create bootstrap Anchor
  -> materialize a new Codex thread
  -> write codex.thread.bound
```

If materialization fails, the Anchor remains committed and the failure is
recorded as `codex.thread.bind.failed`. A later startup can retry from the same
Anchor.

If resume fails, the error is surfaced and recorded as `bub.runtime.error`. The
runtime does not automatically bind a replacement thread, because that would
hide a continuity break.

## Live Turn Sessions

Codex turn streaming is modeled as an explicit resource:

```text
start_turn_stream() -> CodexTurnSession
CodexTurnSession.records() -> notification records
CodexTurnSession.close() -> unregister Codex notifications
```

The live bridge closes the session on normal completion, stream failure, or
consumer cancellation. Notification records for unrelated Codex threads are
filtered before translation.

## Tape Events

Tape events are projected in three steps:

```text
Codex SDK notification record
  -> CodexFact
  -> Bub TapeEvent
  -> optional Bub stream decision
```

`runtime_adapter.py` normalizes SDK notification records into `CodexFact`
objects. Projection modules then decide which facts become durable tape events.
`CodexTurnTranslator` also derives Bub stream decisions from projected events.

Important event families include:

- `bub.anchor.creation.started`
- `bub.anchor.created`
- `bub.context.materialized`
- `codex.thread.bound`
- `codex.thread.bind.failed`
- `codex.turn.started`
- `codex.turn.completed`
- `codex.assistant_message.completed`
- `bub.tool.call.started`
- `bub.tool.call.completed`
- `bub.tool.call.failed`
- `bub.side_effect.started`
- `bub.side_effect.completed`
- `bub.side_effect.failed`
- `codex.thread.compacted`
- `codex.compaction.snapshot`
- `bub.runtime.error`

Tape append happens before the corresponding Bub stream event is emitted. The
tape preserves commentary, final answers, tool calls, runtime diagnostics, and
compaction boundaries even when only final-answer text is displayed to the user.

## Notification Projection

The current user-turn projection is intentionally conservative. It persists
turn lifecycle, completed assistant messages, tool/side-effect lifecycle, and
compaction boundaries. Some SDK notifications are normalized for future use but
are not yet persisted as user-turn tape events.

| Codex SDK notification | Normalized fact | Bub tape / stream result |
| --- | --- | --- |
| `turn/started` | `codex.turn.started` | `codex.turn.started` |
| `turn/completed` | `codex.turn.completed` | `codex.turn.completed`, then final Bub stream event |
| `item/started` for tool-like items | `codex.item.started` | `bub.tool.call.started` |
| `item/completed` for tool-like items | `codex.item.completed` | `bub.tool.call.completed` or `bub.tool.call.failed` |
| `item/started` / `item/completed` for `fileChange` | `codex.item.*` | `bub.side_effect.started`, `bub.side_effect.completed`, or `bub.side_effect.failed` |
| `item/completed` for `agentMessage` | `codex.assistant_message.completed` | `codex.assistant_message.completed`; final-answer text may be emitted to Bub |
| `item/agentMessage/delta` | `codex.assistant_message.delta` | Bub `text` delta only when `phase=final_answer`; not persisted as a tape event |
| `item/completed` for `contextCompaction` | `codex.thread.compacted` | compact Anchor, `codex.thread.compacted`, optional `codex.compaction.snapshot`, and `codex.thread.bound(reason=compact_continuity)` |
| `thread/tokenUsage/updated` | `codex.token_usage.updated` | normalized only; not persisted by current user-turn projection |
| `item/commandExecution/outputDelta` | `codex.command_output.delta` | normalized only; not persisted by current user-turn projection |
| `item/fileChange/patchUpdated` | `codex.file_change.patch_updated` | normalized only; not persisted by current user-turn projection |
| `turn/diff/updated` | `codex.turn.diff.updated` | normalized only; not persisted by current user-turn projection |
| `error` | `codex.error.observed` | normalized only; runtime boundary errors are persisted as `bub.runtime.error` |
| unrecognized method | `codex.notification.observed` | normalized only; not persisted by current user-turn projection |

Tool-like item types currently include `commandExecution`, `mcpToolCall`,
`dynamicToolCall`, `collabAgentToolCall`, `webSearch`, and `imageView`.
`fileChange` is treated as a side effect rather than a tool call.

## Assistant Message Phases

Codex assistant message items may carry `phase=commentary` or
`phase=final_answer`.

- `commentary` is written to tape for audit and replay, but is not emitted as
  user-facing Bub text.
- `final_answer` is written to tape and drives Bub `text` deltas plus
  `final.text`.

When final-answer deltas are available, the live bridge can stream them. When
only completed messages are available, the completed final-answer text is used.

## Compaction

When Codex emits a compaction notification, `bub-codex` projects it into a Bub
Anchor with `method=compact`. The compact Anchor is bound to the same Codex
thread with:

```text
codex.thread.bound(reason=compact_continuity)
```

This keeps the active-thread lookup uniform: latest Anchor plus latest
`codex.thread.bound` for that Anchor.

## Configuration And Runtime Lifetime

`LazyRuntimeStreamService` builds the real Codex runtime inside Bub's turn
lifecycle and caches it by a typed runtime cache key:

- Bub tape-store identity
- workspace
- Codex binary path
- SDK Python path
- approval policy
- sandbox mode
- config overrides
- environment overrides

When the key changes, the old runtime is closed and a new one is initialized.

## Boundaries

Primary modules:

- `plugin.py`: Bub hook entry point.
- `runtime_services.py`: composition root, runtime cache, tape-store selection.
- `runtime_context.py`: Anchor/thread lifecycle kernel.
- `codex_thread_service.py`: Codex SDK thread and turn adapter.
- `live_stream.py`: live orchestration from Codex notifications to Bub stream.
- `turn_translator.py`: notification interpretation into tape and stream
  decisions.
- `compact_projection.py`: Codex compaction to Bub Anchor projection.
- `new_thread_materialization.py`: new-thread materialization and binding
  projection.
- `tape_events.py`: primitive tape event model.
