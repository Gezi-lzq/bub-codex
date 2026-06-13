# Integration Contracts

This plugin depends on two external systems: Bub owns the conversation runtime,
and the Codex Python SDK owns Codex threads and turns. The code should only
depend on the parts listed here. Everything else is an implementation detail.

## Bub Contract

The supported baseline is Bub 0.3.8 or newer. The plugin is packaged as a
normal Bub entry point:

```toml
[project.entry-points."bub"]
codex = "bub_codex.plugin:create_plugin"
```

Bub calls the entry point as a framework-aware factory and registers the
returned plugin object. This is the documented callable plugin shape; it lets
the plugin read the active workspace and tape store after Bub has processed CLI
configuration.

`bub-codex` implements Bub's `run_model_stream` hook:

```python
run_model_stream(prompt: str | list[dict], session_id: str, state: State) -> AsyncStreamEvents
```

The plugin assumes Bub has already resolved the session, loaded state, and built
the prompt. Bub remains responsible for saving state, rendering outbound
messages, and dispatching them.

On Bub versions that include turn admission, `bub-codex` also implements the
optional hook:

```python
admit_message(session_id: str, message: Envelope, turn: TurnSnapshot) -> AdmitDecision | None
```

The plugin returns `AdmitDecision(action="steer")` only when Bub reports an
active turn for the same session. Otherwise it returns `None` so Bub keeps its
default scheduling behavior. The hook is registered as optional so older Bub
installations that do not define `admit_message` can still load the plugin.

Runtime state used by this plugin:

- `state["_runtime_workspace"]`: optional workspace path. If absent, the live
  runtime uses `"."`.
- `state["_runtime_agent"]`: required only for comma-command delegation. It must
  expose `run(session_id=..., prompt=..., state=...)`; the result may be sync or
  awaitable.
- `state["_runtime_steering"]`: optional Bub `SteeringBuffer`. When present, the
  live runtime consumes messages with `get_nowait()` and sends their textual
  `content` to the active Codex turn.

The plugin exposes configuration through Bub's config registry:

```python
@bub.config(name="codex")
class BubCodexSettings(...)
```

Settings are loaded with `bub.ensure_config(BubCodexSettings)` and may also come
from `BUB_CODEX_` environment variables.

## Bub Stream Contract

Bub stream output is Republic `AsyncStreamEvents`:

```python
AsyncStreamEvents(iterator: AsyncIterator[StreamEvent], *, state: StreamState | None = None)
StreamEvent(kind: "text" | "tool_call" | "tool_result" | "usage" | "error" | "final", data: dict)
StreamState(error=None, usage=None)
```

`bub-codex` emits only:

- `text`: user-visible assistant text deltas.
- `error`: runtime failure details.
- `final`: terminal result with `{ "text": str, "ok": bool }`.

Assistant commentary is written to tape but is not emitted as user-visible text.

## Bub Tape Contract

The internal runtime uses a narrow append-only `TapeStore` port:

```python
async append(event: TapeEvent) -> None
async append_many(events: Iterable[TapeEvent]) -> None
async events(session_id: str | None = None, tape_id: str | None = None) -> list[TapeEvent]
```

`RepublicTapeStoreAdapter` is the only place that translates between this port
and Republic tape storage. The adapter accepts both sync Republic `TapeStore`
implementations and async Republic `AsyncTapeStore` implementations. Async
stores are awaited directly inside Bub's async runtime; the adapter does not
start a nested event loop or move async stores to a separate thread.

The adapter depends on:

- `TapeEntry.event(name, data, **meta)` for bub-codex events.
- `TapeEntry.anchor(name, state, **meta)` for native Bub anchors.
- `TapeQuery(tape=..., store=...)` plus store `fetch_all(query)` when the store
  does not expose `read(tape_id)`.

If the active Bub tape store is async, all bub-codex tape reads and writes must
stay on the current event loop. This matches Republic's async tape contract and
keeps SQLite-backed stores from being shared across hidden event loops.

## Bub Tool Contract

The plugin exposes a configured allowlist of Bub tools to Codex. The default
allowlist is:

- `tape.info`
- `tape.search`
- `tape.anchors`
- `tape.handoff`

The allowlist is configured by `codex.bub_tools` or `BUB_CODEX_BUB_TOOLS`.
Environment values may be a comma-separated list or a JSON list.

Tools come from `bub.tools.REGISTRY`. `bub-codex` imports `bub.builtin.tools`
before resolving the allowlist; tools from other plugins must have been
registered by their plugin initialization. Unknown configured tool names fail
runtime construction instead of being ignored.

Do not expose all Bub tools by default. Some tools have broad side effects or
runtime assumptions. Add tools such as `schedule.add`, `schedule.list`,
`schedule.remove`, and `schedule.trigger` only for deployments that install and
enable `bub-schedule`.

The adapter depends on Bub/Republic tool objects having:

- `name: str`
- `description: str | None`
- `parameters: dict`
- `context: bool`
- `run(**kwargs)` or `handler(**kwargs)`

Tool names are converted for Codex by replacing non `[a-zA-Z0-9_-]` characters
with `_`. For example, `tape.handoff` becomes Codex dynamic tool name
`tape_handoff` in namespace `bub`.

Context-aware tools receive a `ToolContext` with:

- `tape`: current tape id
- `run_id`: Codex turn id, tool call id, or fallback id
- `state`: Bub state plus runtime ids

The Codex SDK calls dynamic tools through a synchronous server-request handler.
When a Bub tool returns an awaitable, live runtime schedules it back onto Bub's
active event loop. This keeps async tape tools on the same loop as async
Republic tape stores such as SQLite.

## Codex SDK Contract

The plugin integrates with the Codex Python SDK through
`openai_codex.client.CodexClient` and `CodexConfig`.

Client construction:

```python
CodexConfig(
    codex_bin: str | None,
    cwd: str,
    config_overrides: tuple[str, ...],
    env: dict[str, str] | None,
    experimental_api: True,
)
CodexClient(config=..., approval_handler=...)
```

Lifecycle:

```python
client.start()
client.initialize()
client.close()  # when available
```

Thread and turn operations:

```python
thread_start(params: dict) -> response.thread.id
thread_resume(thread_id: str, params: dict)
turn_start(thread_id: str, input_items: str | list[dict] | dict, params: dict) -> response.turn.id
turn_steer(thread_id: str, expected_turn_id: str, input_items: str | list[dict] | dict)
next_turn_notification(turn_id: str) -> Notification
unregister_turn_notifications(turn_id: str)
thread_read(thread_id: str, include_turns: bool = False)
```

`codex_thread_service.CodexClientPort` is the internal port for these methods.
Code outside `codex_thread_service` should not call Codex SDK thread or turn
methods directly.

New Codex threads receive one short `developerInstructions` string:

```text
Inside Bub, if the prompt includes external channel context, use the matching installed channel skill for user-visible replies; direct final answers may not be delivered.
```

This is a stable thread-level contract only. Per-message fields such as
`chat_id` and `message_id` stay in Bub's built prompt. `thread_resume` does not
send `developerInstructions`.

The plugin treats `next_turn_notification` as a blocking iterator source for one
turn. It filters notifications by thread id and stops only on the current
thread's `turn/completed`.

While waiting for blocking turn notifications, the live runtime also drains
Bub's steering buffer and calls `turn_steer` on the same Codex thread and turn.
The current contract is intentionally narrow: only textual Bub message content
is steered. Rich envelope fields stay out of the Codex SDK boundary until a
concrete consumer requires them.

Notification payloads may be SDK models. `codex_thread_service` is the only
place that converts them with `model_dump(mode="json", by_alias=True,
exclude_none=False)`. Downstream code receives plain JSON-like dictionaries.

## Codex Dynamic Tool Contract

`dynamicTools` is an experimental app-server `thread/start` field. It is present
in the Codex app-server experimental schema (`codex app-server
generate-json-schema --experimental`) and omitted from the default stable schema.
The pinned Codex Python SDK generated `ThreadStartParams` model also does not
define `dynamicTools`. `bub-codex` therefore sets `experimental_api=True` during
SDK initialization and sends this one field through raw JSON params.

`bub-codex` keeps that app-server extension isolated in `ThreadStartOptions`; no
other module should build this payload directly.

The plugin registers Codex dynamic tools through raw `thread_start` params:

```json
{
  "dynamicTools": [
    {
      "namespace": "bub",
      "name": "tape_handoff",
      "description": "...",
      "inputSchema": {"type": "object", "properties": {...}}
    }
  ]
}
```

The SDK calls the approval handler with server-request methods. The plugin
handles:

- `item/tool/call`: dispatch to a registered Bub dynamic tool. App-server
  `DynamicToolCallParams` requires `threadId` and `turnId`; Bub runtime context
  is selected by the exact `(threadId, turnId)` pair. Missing or unknown ids
  fail the tool call instead of falling back to the most recent Bub turn.
- `item/commandExecution/requestApproval`: accept.
- `item/fileChange/requestApproval`: accept.

Unknown methods return `{}`.

`thread_resume` is called only with documented resume fields today: `cwd`,
`approvalPolicy`, and `sandbox`. Do not add `developerInstructions` or
`dynamicTools` to resume unless the SDK or app-server contract is verified and
covered by tests.

## Runtime State Machine

Terms used by the runtime:

- **Anchor**: Bub-side continuity marker recorded in tape.
- **Bound Codex thread**: a Codex thread id recorded by `codex.thread.bound` for
  the latest Anchor.
- **Startup context**: model-visible JSON text prepared from workspace metadata
  and optional handoff summary. It is wrapped into the first real user turn
  only.
- **First real user turn**: the first Codex `turn_start` caused by a user chat
  message after a new thread is bound. There is no hidden initialization turn
  before it.
- **Materialization**: retained event terminology meaning that startup context
  was prepared and recorded as tape evidence. It does not mean `thread_start`
  received that text or that a hidden LLM turn ran.

The state machine is tape-first:

```text
no committed Anchor
  -> create session_start Anchor
  -> prepare startup context
  -> create Codex thread
  -> bind thread to Anchor

latest Anchor has no codex.thread.bound
  -> prepare startup context
  -> create Codex thread
  -> bind thread to Anchor

latest Anchor has codex.thread.bound
  -> resume that Codex thread
```

Resume failure is surfaced as an error. It does not silently create a replacement
thread.

The context-binding path prepares `MaterializedContextInput` once and records
the startup context hash in `bub.context.materialized.input_sha256`. The Codex
thread creation call does not receive this text. The runtime wraps it into the
first real user turn only; resumed turns send the raw user prompt.

`RuntimeStreamService.current_tape_store()` is the plugin-facing port for
comma-command handoff recording. `LazyRuntimeStreamService` resolves the active
Bub tape store directly for comma handoff and does not start a Codex SDK runtime
for that path. Normal model turns build a Codex runtime per Bub turn and close
it after the stream is consumed. Continuity comes from tape Anchors and
`codex.thread.bound`, not from an in-process Codex client cache. If Bub has no
active tape store, comma handoff delegation still runs, but no bub-codex Anchor
is recorded.

## Extension Boundaries

Future deeper integration should extend one of these ports instead of spreading
SDK or Bub details through the codebase:

- `TapeStore` for richer or async tape persistence.
- `CodexThreadContextAdapter` / `CodexThreadService` for new Codex thread or turn
  capabilities.
- `runtime_adapter.py` for shared raw notification record helper functions.
- `notification_translator.py` for changing which Codex notifications produce
  Bub tape or stream output.
- `tool_projection` for new tool item types.
- `compact_projection` only when Codex produces a real compaction notification
  that the current runtime uses.
- `BubToolRuntimeContext` for new fields passed to Bub tools.

Do not add speculative event fields or modules for SDK behavior that is not
produced by the current adapter or required by a current workflow.

SDK `error` notifications are observed Codex payloads and are projected to tape
as `codex.error.observed`. User-visible stream `error` events and failed
`final` results come from runtime exceptions or unavailable context, not from
the observed SDK notification alone.
