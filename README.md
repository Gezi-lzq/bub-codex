# bub-codex

Codex runtime plugin for `bub`.

This package is a Bub-native Codex runtime. It uses the Codex SDK/app-server
path directly instead of wrapping Bub around `codex e` as an opaque subprocess.

It is intentionally different from the historical `bub-contrib` `bub-codex`
plugin:

- the contrib plugin implements `run_model` by invoking `codex e resume ...`;
- this package implements live `run_model_stream` through the Codex SDK;
- runtime continuity is derived from Bub tape events and Anchors, not from a
  workspace JSON file.

## What It Provides

- Bub plugin entry point: `codex`
- A `run_model_stream` hook implementation backed by the Codex SDK
- Tape-first session/thread resume
- Codex compaction projection into Bub Anchors
- Current-thread notification filtering
- Bub comma-command delegation back to the builtin Bub agent
- Explicit runtime errors when Codex cannot be configured or resumed

## Installation

Install from this repository into the same Python environment that runs Bub:

```bash
uv pip install "git+https://github.com/Gezi-lzq/bub-codex.git"
```

For local development:

```bash
uv pip install -e .
```

When published through the Bub plugin catalog, it can also be installed with
Bub:

```bash
bub install bub-codex@main
```

Bub loads plugins from the current Python environment through the `bub` entry
point group. For this package, the entry point is:

```toml
[project.entry-points."bub"]
codex = "bub_codex.plugin:create_plugin"
```

## Prerequisites

- Python 3.12+
- Bub
- Codex CLI installed and authenticated
- The Codex Python SDK importable as `openai_codex`

The project currently depends on the Codex SDK from the Codex repository:

```toml
openai-codex @ git+https://github.com/openai/codex.git#subdirectory=sdk/python
```

## Configuration

The plugin reads Bub config under the `codex` section and environment variables
with the `BUB_CODEX_` prefix.

Environment variables:

- `BUB_CODEX_ENABLED` (optional, default: `true`): enable the plugin runtime
- `BUB_CODEX_CODEX_BIN` (optional): path to the `codex` binary
- `BUB_CODEX_SDK_PYTHON_PATH` (optional): local Codex SDK source path
- `BUB_CODEX_WORKSPACE` (optional): workspace override
- `BUB_CODEX_APPROVAL_POLICY` (optional, default: `never`)
- `BUB_CODEX_SANDBOX` (optional, default: `danger-full-access`)
- `BUB_CODEX_CONFIG_OVERRIDES` (optional): extra Codex config overrides
- `BUB_CODEX_USE_BUB_TAPE_STORE` (optional, default: `true`)

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

Common local setup:

```bash
export BUB_CODEX_ENABLED=true
export BUB_CODEX_CODEX_BIN="$(command -v codex)"
export BUB_CODEX_APPROVAL_POLICY=never
export BUB_CODEX_SANDBOX=danger-full-access
```

## Usage

Start Bub normally after the plugin is installed:

```bash
python -m bub --workspace /path/to/workspace chat --session-id my-session
```

Normal chat turns are handled by Codex through `run_model_stream`. Bub comma
commands, such as `,help`, are delegated back to the builtin Bub agent.

## Runtime Behavior

`bub-codex` does not replace Bub's full turn pipeline. Bub still handles
inbound messages, session resolution, state loading, prompt construction, state
saving, outbound rendering, and dispatch. The plugin handles the
`run_model_stream` stage for normal chat prompts.

- Workspace resolution:
  - Uses `codex.workspace` config when set
  - Otherwise uses Bub's framework workspace
  - Per turn, uses `state["_runtime_workspace"]` when Bub provides it
- Codex runtime:
  - Builds a Codex SDK client with `experimental_api=True`
  - Starts and initializes the Codex app-server client lazily inside Bub's turn
    lifecycle
  - Reuses the runtime while the workspace/config/tape-store cache key stays
    stable
- Tape behavior:
  - Uses Bub's active tape store when available
  - Falls back to an in-memory tape store when Bub does not expose one
  - Fails fast for async-only Republic tape stores in the current v0 runtime
- Resume behavior:
  - Resumes the Codex thread bound to the latest Bub Anchor
  - Materializes a new Codex thread when the latest Anchor has no binding
  - Surfaces resume failure instead of silently creating a replacement thread
- Stream behavior:
  - Writes commentary and tool lifecycle events to tape
  - Emits final-answer text to Bub's stream output
  - Projects Codex compaction notifications into Bub Anchors

See [docs/design.md](docs/design.md) for the architecture and event model.

## Verification

Verify that Bub loaded the plugin by checking the hook report:

```bash
uv run bub hooks
```

The report should include `codex` under `run_model_stream`.

This repository also includes a focused discovery check:

```bash
BUB_CODEX_ENABLED=false python scripts/verify_installed_plugin.py
```

Expected output:

```text
OK: Bub discovered installed bub-codex plugin (run_model_stream: builtin, codex).
```

Run local checks:

```bash
.venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -m py_compile src/bub_codex/*.py tests/*.py scripts/*.py
```
