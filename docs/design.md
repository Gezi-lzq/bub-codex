# Design

`bub-codex` is a Bub plugin that uses Codex as the model runtime while keeping
Bub in charge of the conversation pipeline.

The exact Bub, Republic, and Codex SDK surfaces this package relies on are
listed in [integration-contracts.md](integration-contracts.md).

## Pipeline Position

`bub-codex` does not replace Bub's turn pipeline. It only handles the model
execution stage:

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
saving, outbound rendering, and outbound dispatch. Comma commands such as
`,help` are delegated back to Bub's builtin agent.

## Runtime Flow

For a normal chat prompt, the flow is:

```text
Bub run_model_stream
  -> resolve workspace and tape id
  -> find or create a Codex thread for the Bub session
  -> start a Codex turn through the Codex SDK
  -> consume Codex SDK notifications and Bub steering messages
  -> append selected runtime events to Bub tape
  -> emit Bub stream text/final output
```

The plugin starts and initializes the Codex SDK runtime lazily, then reuses it
while the workspace, tape store, and Codex configuration stay the same.

## Module Boundaries

The package intentionally stays flat under `src/bub_codex/`. The file count is
accepted because each file owns a concrete side-effect boundary or invariant:

| File | Owns |
| --- | --- |
| `plugin.py` | Bub hook surface, turn admission, and comma-command delegation |
| `runtime_services.py` | dependency assembly and lazy runtime lifecycle |
| `runtime_context.py` | tape-backed create/resume state machine |
| `new_thread_materialization.py` | startup context evidence and thread-binding events |
| `startup_context.py` | first-turn prompt wrapper |
| `live_stream.py` | Bub streaming side effects |
| `codex_client.py` | Codex app-server params and dynamic-tool request dispatch |
| `codex_thread_service.py` | Codex thread/turn SDK calls and notification collection |
| `runtime_adapter.py` | private raw SDK notification record helpers |
| `notification_translator.py` | Codex notification records to `TapeEvent` and `StreamEvent` translation |
| `turn_projection.py` | user-turn notification to tape-event projection |
| `tool_projection.py` | tool/file side-effect tape events |
| `compact_projection.py` | compaction continuity events |
| `bub_tools.py` | configured Bub tool allowlist and Codex dynamic-tool bridge |
| `tape_events.py` | internal event shape and deterministic ids |
| `tape_store.py` | narrow async internal tape-store port |
| `republic_tape_store.py` | sync/async Republic tape adapter |
| `runtime_diagnostics.py` | standardized runtime error events |
| `stream_utils.py` | stream/prompt/tape-id helpers |
| `json_utils.py` | canonical JSON, hashing, previews |

Do not add subpackages only to reduce the apparent file count. Add hierarchy
only when imports or ownership become hard to trace.

## Notification Mapping

Notification projection is intentionally not one-to-one:

```text
Codex SDK notification
  -> NotificationTranslation
     -> selected TapeEvent
     -> optional StreamEvent
```

The translator maps JSON-like Codex notification records directly to Bub output.
Projection helpers may inspect one record in multiple ways, such as item
lifecycle, assistant-message completion, or compaction continuity, but they do
not create a public intermediate notification model. The public boundary remains
`CodexNotification -> TapeEvent / StreamEvent`.

Durable projection rules:

- assistant deltas are stream-only and are not written to tape;
- completed assistant messages are written to tape;
- selected tool and file-change items become Bub lifecycle events with hashes
  and previews;
- compaction notifications become multiple continuity events because they update Bub
  Anchor/thread state;
- SDK error notifications are written as `codex.error.observed` with the raw SDK
  payload preserved;
- token usage, command output deltas, patch updates, turn diff updates, unknown
  notifications, and non-tool item lifecycle updates remain filtered until a
  concrete consumer exists.

`TapeEvent.type` is the internal bub-codex event name. The Republic adapter still
uses native `TapeEntry.kind` when the semantics match:

- `bub.anchor.created` -> `anchor`;
- completed assistant messages -> `message`;
- runtime errors and SDK error observations -> tape `error`;
- tool started/completed events -> `tool_call` / `tool_result`;
- lifecycle, binding, and audit events stay `event`.

Every bub-codex entry carries the full `TapeEvent` in metadata so the runtime can
read back old and new storage shapes without losing state.

## Session Continuity

Bub and Codex use different identities:

- Bub uses `session_id` and tape history.
- Codex uses `thread_id` and `turn_id`.
- `bub-codex` connects them through Bub Anchors and `codex.thread.bound` tape
  events.

Startup resolution is tape-first:

```text
latest Anchor has a bound Codex thread
  -> resume that Codex thread

latest Anchor has no bound Codex thread
  -> prepare startup context, create a Codex thread, and bind it

no Anchor exists
  -> create a bootstrap Anchor
  -> prepare startup context, create a Codex thread, and bind it
```

If a bound Codex thread cannot be resumed, the plugin surfaces the error instead
of silently creating a replacement thread.

## Codex SDK Integration

The plugin integrates with the Codex Python SDK, not the `codex e` subprocess
interface. At runtime it creates a Codex client from Bub configuration, starts
the Codex app-server client, and runs Codex turns through the SDK.

For new sessions, the plugin creates a Codex thread and records the prepared
startup context in tape. It does not run a hidden initialization model turn.
The startup context is wrapped into the first real user turn; resumed turns send
the raw user prompt.

When Bub supports turn admission, messages received during an active turn are
admitted as `steer` decisions. Bub queues them in `state["_runtime_steering"]`;
`live_stream.py` drains that buffer during the current turn and
`codex_thread_service.py` forwards the text through Codex SDK `turn_steer`.
This does not create a new Bub turn, Codex thread, or Codex turn.

## Tape Projection

Codex SDK notifications are converted into Bub tape events for the parts of the
runtime users need to audit or resume:

- turn start and completion
- assistant messages
- tool call lifecycle
- file-change side effects
- compaction boundaries
- runtime errors

Assistant commentary is preserved in tape but is not shown as user-facing text.
Final-answer text is emitted to Bub's stream output.

When Codex compacts context, `bub-codex` records a Bub Anchor and binds it to the
same Codex thread. This keeps future resume behavior consistent with normal
Anchor/thread resolution.

## Bub Tools

`bub-codex` exposes configured Bub tools to Codex as dynamic tools. The default
allowlist is intentionally small:

- `bub.tape_info`
- `bub.tape_search`
- `bub.tape_anchors`
- `bub.tape_handoff`

Additional Bub tools, such as `schedule.add`, should be added through
configuration only when the plugin that registers them is installed and the
runtime side effects are understood.

`tape.handoff` is the context-switching tool. When the user runs
`,tape.handoff` or Codex calls `bub.tape_handoff` and Bub has an active tape
store, Bub records a new Anchor. On the next normal chat turn, `bub-codex`
resolves that latest Anchor, prepares startup context, creates a new Codex
thread, and writes the new `codex.thread.bound` event to tape.

Codex dynamic tool calls are scoped by app-server `threadId` and `turnId`.
`bub-codex` registers Bub tool context when a Codex turn starts and clears it
when the turn finishes. A dynamic tool request with missing or unknown ids fails
instead of using the most recent Bub turn state.

## Review Rules

Before large changes, check these invariants:

- only `runtime_context.py` decides create-vs-resume state;
- only `codex_thread_service.py` calls Codex thread/turn methods;
- all Bub/Republic tape I/O is awaited through the internal `TapeStore` port;
- `notification_translator.py` consumes JSON-like notification records and emits Bub `TapeEvent`
  plus Republic `StreamEvent` objects;
- projection files consume JSON-like notification records through helper functions, not raw SDK model payloads;
- second and later turns in the same session do not receive startup context;
- no hidden initialization model turn is introduced;
- steering input targets the active Codex turn and does not create a new turn;
- projection remains value-based, not a mirror of Codex SDK notifications;
- new files must earn their boundary by owning a distinct invariant or side
  effect.
