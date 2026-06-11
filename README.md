# bub-codex

`bub-codex` is a Bub plugin that embeds Codex as a Bub-native coding runtime.

It does not wrap Bub around `codex e` as an opaque subprocess. Instead, Codex
participates in Bub's runtime model directly: hooks, tapes, channels, runtime
configuration, context continuity, structured events, and session recovery are
first-class concerns.

## Status

Current status:

```text
MVP candidate skeleton, not release-ready MVP
```

The current package includes:

- A Bub plugin entry point: `bub_codex.plugin:create_plugin`.
- Bub config wiring via `@bub.config(name="codex")`.
- A live `run_model_stream` bridge backed by the real `openai_codex` SDK.
- Bub/Republic tape store integration.
- Tape-first Anchor/thread resolution.
- Existing Codex thread resume from tape-derived bindings.
- Current-thread notification filtering.
- Minimal runtime diagnostic tape events.
- Codex compaction notification projection into Bub Anchors.
- Unit tests and manual real Codex SDK smoke checks.

Current release readiness is tracked in:

- [MVP Candidate Checkpoint](docs/release/mvp-candidate-checkpoint.md)
- [MVP Readiness Review](docs/research/2026-06-11-mvp-readiness-review.md)
- [MVP PRD](docs/prd-mvp-live-codex-runtime.md)

## Runtime Model

The production MVP path is:

```text
Bub run_model_stream
  -> bub-codex plugin
  -> live Codex SDK notification stream
  -> CodexTurnTranslator
  -> Bub tape events
  -> Bub stream text/final
```

Key semantics:

- Bub tape is the canonical record, not Codex rollout JSONL or observability
  traces.
- `session_id`, tape id, Codex `thread_id`, turn id, and Anchor id are distinct
  identities.
- A Codex thread is bound to a committed Bub Anchor only after materialization
  succeeds.
- If a bound Codex thread fails to resume, the failure is surfaced; the runtime
  does not silently create a replacement thread.
- `phase=commentary` assistant messages are preserved in tape but are not
  emitted as Bub `text`.
- `phase=final_answer` assistant messages drive Bub `text` and `final.text`.
- Codex auto compaction creates a Bub Anchor with `method=compact`.

## Installation

Install the package into the same Python environment that runs Bub:

```bash
uv pip install -e .
```

Verify that Bub can discover the installed plugin without starting a real Codex
runtime:

```bash
BUB_CODEX_ENABLED=false python scripts/verify_installed_plugin.py
```

Expected output:

```text
OK: Bub discovered installed bub-codex plugin (run_model_stream: builtin, codex).
```

The underlying Bub hook report should include:

```text
run_model_stream: builtin, codex
```

## Configuration

Minimal Bub config:

```yaml
codex:
  enabled: true
  codex_bin: /path/to/codex
  sdk_python_path: null
  approval_policy: never
  sandbox: danger-full-access
  config_overrides: []
  env: {}
```

The same settings can be overridden with `BUB_CODEX_*` environment variables.

Notes:

- `openai-codex` is a project dependency; the import name is `openai_codex`.
- `sdk_python_path` is only a development escape hatch for pointing at a local
  `openai-codex/sdk/python/src` checkout.
- v0 intentionally uses maximum local permission:
  `approval_policy=never`, `sandbox=danger-full-access`.
- If the Codex SDK or runtime cannot be configured, the plugin returns an
  explicit `bub-codex runtime is not configured` stream error.
- The MVP production path does not use a batch fallback.
- Bub comma commands are delegated back to Bub builtin agent behavior.

## Verification

Default local checks:

```bash
.venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -m py_compile src/bub_codex/*.py tests/*.py scripts/*.py scripts/spikes/*.py
```

Installed plugin discovery:

```bash
BUB_CODEX_ENABLED=false python scripts/verify_installed_plugin.py
```

Manual real Codex SDK resume smoke:

```bash
python scripts/spikes/real_codex_resume_smoke.py
```

The resume smoke runs two live bridge turns against the real Codex SDK, reuses
the same tape store, and asserts that the second turn resumes the first turn's
bound Codex thread without creating a replacement thread. Results are written to:

```text
artifacts/spikes/real-codex-resume-smoke-*/result.json
```

## Important Files

- `src/bub_codex/plugin.py` - Bub plugin entry point.
- `src/bub_codex/config.py` - Bub config model.
- `src/bub_codex/live_stream.py` - MVP live notification bridge.
- `src/bub_codex/runtime.py` - tape-first runtime context facade.
- `src/bub_codex/codex_thread_service.py` - Codex SDK thread lifecycle adapter.
- `src/bub_codex/turn_translator.py` - raw notification to tape/stream translator.
- `src/bub_codex/republic_tape_store.py` - Bub/Republic tape adapter.
- `scripts/verify_installed_plugin.py` - installed plugin discovery check.
- `scripts/spikes/real_codex_resume_smoke.py` - manual real SDK resume smoke.

## Documentation

- [CONTEXT.md](CONTEXT.md) - domain language, current decisions, and open
  questions.
- [MVP Candidate Checkpoint](docs/release/mvp-candidate-checkpoint.md) - current
  delivery baseline.
- [MVP PRD](docs/prd-mvp-live-codex-runtime.md) - product boundary and acceptance
  criteria.
- [MVP Readiness Review](docs/research/2026-06-11-mvp-readiness-review.md) -
  readiness status and remaining hardening.
- [ADR index](CONTEXT.md#adr-index) - architecture decision records.

## Current Non-Goals

The current MVP candidate intentionally excludes:

- Bub dynamic tool hosting production contract.
- Token-level assistant streaming from `item/agentMessage/delta`.
- Active/manual compact triggering.
- Context overflow policy.
- Approval UX or policy engine.
- Langfuse, OTel, or Obelisk projections.
- Replay engine or UI timeline.
- Full schema for reasoning, token usage, MCP tools, collab agents, web search,
  and image items.
- Full `CodexEnvironment` module.

The remaining post-MVP hardening track is captured in GitHub issue #8:
`P2: Design post-MVP CodexEnvironment module`.
