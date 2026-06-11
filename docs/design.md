# Design

`bub-codex` is a Bub plugin that uses Codex as the model runtime while keeping
Bub in charge of the conversation pipeline.

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
  -> consume Codex SDK notifications
  -> append selected runtime events to Bub tape
  -> emit Bub stream text/final output
```

The plugin starts and initializes the Codex SDK runtime lazily, then reuses it
while the workspace, tape store, and Codex configuration stay the same.

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
  -> materialize and bind a new Codex thread

no Anchor exists
  -> create a bootstrap Anchor, then materialize and bind a Codex thread
```

If a bound Codex thread cannot be resumed, the plugin surfaces the error instead
of silently creating a replacement thread.

## Codex SDK Integration

The plugin integrates with the Codex Python SDK, not the `codex e` subprocess
interface. At runtime it creates a Codex client from Bub configuration, starts
the Codex app-server client, and runs Codex turns through the SDK.

For new sessions, the plugin first performs a short materialization turn so the
Codex thread has resumable context. User prompts are sent as separate user turns.

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
