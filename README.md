# bub-codex

Codex runtime plugin for `bub`.

`bub-codex` lets Bub use Codex as the model runtime through the Codex SDK. It
does not wrap Bub around `codex e`; Bub still owns the turn pipeline, session
state, tape storage, and outbound rendering.

This package is different from the historical `bub-contrib` `bub-codex` plugin:
that plugin shells out to `codex e resume ...`, while this package implements
live `run_model_stream` through the Codex SDK and stores continuity in Bub tape.

## What It Provides

- Bub plugin entry point: `codex`
- `run_model_stream` backed by the Codex SDK
- Codex thread resume from Bub tape history
- Codex compaction recorded as Bub Anchors
- Bub comma-command delegation back to the builtin Bub agent
- Bub tape tools exposed to Codex as dynamic tools

## Installation

Install into the same Python environment that runs Bub:

```bash
uv pip install "git+https://github.com/Gezi-lzq/bub-codex.git"
```

For local development:

```bash
uv pip install -e .
```

Bub loads plugins through the `bub` entry point group:

```toml
[project.entry-points."bub"]
codex = "bub_codex.plugin:create_plugin"
```

## Prerequisites

- Python 3.12+
- Bub
- Codex CLI installed and authenticated
- Codex Python SDK importable as `openai_codex`

## Configuration

The plugin reads Bub config under the `codex` section and environment variables
with the `BUB_CODEX_` prefix.

Common local setup:

```bash
export BUB_CODEX_ENABLED=true
export BUB_CODEX_CODEX_BIN="$(command -v codex)"
export BUB_CODEX_APPROVAL_POLICY=never
export BUB_CODEX_SANDBOX=danger-full-access
```

Equivalent Bub config shape:

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

## Usage

Start Bub normally after the plugin is installed:

```bash
uv run bub --workspace /path/to/workspace chat --session-id my-session
```

Normal chat turns are handled by Codex. Bub comma commands, such as `,help`, are
still handled by Bub.

`tape.handoff` is supported from both sides:

- user command: `,tape.handoff name=handoff summary="new context"`
- Codex dynamic tool: `bub.tape_handoff`

Both paths create a Bub Anchor when Bub has an active tape store. The next
normal chat turn materializes a new Codex thread from that Anchor and binds it
in tape.

## Behavior

`bub-codex` only handles Bub's `run_model_stream` stage. Bub still handles
inbound messages, session resolution, state loading, prompt construction, state
saving, outbound rendering, and dispatch.

At runtime, the plugin:

- resolves the Bub workspace and tape id
- resumes the Codex thread bound to the current Bub Anchor
- materializes a new Codex thread when no binding exists
- writes selected Codex runtime events to Bub tape
- emits final-answer text to Bub's stream output

See [docs/design.md](docs/design.md) for the runtime flow and
[docs/integration-contracts.md](docs/integration-contracts.md) for the Bub,
Republic, and Codex SDK contracts this package depends on.

## Verification

Check that Bub loaded the plugin:

```bash
uv run bub hooks
```

The report should include `codex` under `run_model_stream`.

Run one real chat turn:

```bash
uv run bub --workspace "$PWD" chat --session-id bub-codex-smoke
```

Local repository checks:

```bash
.venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -m py_compile src/bub_codex/*.py tests/*.py scripts/*.py
```
