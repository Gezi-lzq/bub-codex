# bub-codex

`bub-codex` is a Bub plugin that runs Codex as a Bub-native coding runtime.

The plugin does not wrap Bub around `codex e` as an opaque subprocess. It uses
the Codex SDK/app-server path directly, projects Codex notifications into Bub
tape events, and lets Bub own session identity, tape storage, command routing,
and stream delivery.

## What It Provides

- A Bub plugin entry point: `bub_codex.plugin:create_plugin`.
- A live `run_model_stream` implementation backed by the Codex SDK.
- Tape-first runtime context resolution for Bub `session_id` / tape id /
  Codex `thread_id` / Anchor continuity.
- Existing Codex thread resume from tape-derived bindings.
- Codex compaction projection into Bub Anchors.
- Current-thread notification filtering.
- Minimal runtime diagnostic events for resume and stream failures.
- Bub comma-command delegation back to the builtin Bub agent.

## Install

Install the package into the same Python environment that runs Bub:

```bash
uv pip install -e .
```

The project depends on `bub`, `republic`, and the Codex Python SDK:

```toml
openai-codex @ git+https://github.com/openai/codex.git#subdirectory=sdk/python
```

Verify that Bub can discover the plugin:

```bash
BUB_CODEX_ENABLED=false python scripts/verify_installed_plugin.py
```

Expected output:

```text
OK: Bub discovered installed bub-codex plugin (run_model_stream: builtin, codex).
```

## Configure

`bub-codex` registers a Bub config section named `codex`.

```yaml
codex:
  enabled: true
  codex_bin: /path/to/codex
  sdk_python_path: null
  workspace: null
  approval_policy: never
  sandbox: danger-full-access
  config_overrides: []
  env: {}
  use_bub_tape_store: true
```

The same settings can be supplied with `BUB_CODEX_*` environment variables.

Common local settings:

```bash
export BUB_CODEX_ENABLED=true
export BUB_CODEX_CODEX_BIN="$(command -v codex)"
export BUB_CODEX_APPROVAL_POLICY=never
export BUB_CODEX_SANDBOX=danger-full-access
```

Notes:

- `approval_policy=never` and `sandbox=danger-full-access` are the default v0
  local runtime posture.
- `sdk_python_path` is only a development escape hatch for a local Codex SDK
  checkout.
- If the runtime cannot be configured, the plugin returns an explicit Bub stream
  error instead of silently falling back to another runtime.

## Use With Bub

After installing the package into Bub's Python environment, start Bub normally:

```bash
python -m bub --workspace /path/to/workspace chat --session-id my-session
```

When enabled, `bub-codex` participates as a Bub `run_model_stream` plugin. Normal
prompts are handled by Codex. Bub comma commands, such as `,help`, are delegated
back to Bub's builtin agent behavior.

## Runtime Semantics

The live path is:

```text
Bub run_model_stream
  -> bub-codex plugin
  -> RuntimeContextKernel
  -> Codex SDK turn session
  -> CodexTurnTranslator
  -> Bub tape events
  -> Bub stream text/final
```

Key rules:

- Bub tape is the canonical runtime history.
- A Bub Anchor is the context boundary used to materialize or resume a Codex
  thread.
- A Codex `thread_id` is executable only after it is bound to a committed Anchor.
- If a bound Codex thread cannot be resumed, the failure is surfaced; the runtime
  does not create a replacement thread behind the user's back.
- Codex `phase=commentary` assistant messages are written to tape but are not
  emitted as Bub text.
- Codex `phase=final_answer` assistant messages drive Bub `text` and
  `final.text`.
- Codex compaction creates a Bub Anchor and preserves continuity by binding that
  Anchor to the same Codex thread.

See [docs/design.md](docs/design.md) for the architecture and event model.

## Verify

Run the default local checks:

```bash
.venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -m py_compile src/bub_codex/*.py tests/*.py scripts/*.py
python scripts/verify_installed_plugin.py
```

## Important Files

- `src/bub_codex/plugin.py` - Bub plugin entry point.
- `src/bub_codex/config.py` - Bub config model.
- `src/bub_codex/runtime_services.py` - runtime construction, caching, and
  tape-store selection.
- `src/bub_codex/runtime_context.py` - Anchor/thread lifecycle kernel.
- `src/bub_codex/live_stream.py` - live Codex notification bridge.
- `src/bub_codex/codex_thread_service.py` - Codex SDK thread and turn adapter.
- `src/bub_codex/turn_translator.py` - notification-to-tape/stream translator.
- `src/bub_codex/republic_tape_store.py` - Bub/Republic tape-store adapter.
- `scripts/verify_installed_plugin.py` - installed plugin discovery check.
