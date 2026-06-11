# bub-codex MVP Candidate Checkpoint

日期：2026-06-11

## 结论

当前仓库已经整理到 **MVP candidate skeleton, not release-ready MVP**。

主链路已经跑通：

```text
Bub plugin entry point
  -> Bub config
  -> live run_model_stream bridge
  -> Codex SDK app-server client
  -> tape-first Anchor/thread resolution
  -> Translator
  -> Bub tape events + Bub stream final answer
```

当前目标不是宣称可发布，而是把开发过程、代码产物、文档产物和验证标准固定下来，作为后续 release-ready 工作的基线。

## 当前代码产物

### Package / Plugin

- `pyproject.toml`
  - 声明 `bub-codex` Python package。
  - 依赖 `bub`、`republic`、`openai-codex`、`pydantic`。
  - 注册 `[project.entry-points."bub"]`：

    ```text
    codex = "bub_codex.plugin:create_plugin"
    ```

- `src/bub_codex/plugin.py`
  - Bub plugin entry point。
  - 只实现 MVP 需要的 `run_model_stream`。
  - 逗号命令委托给 Bub builtin agent。
  - 正式 runtime service 构造 live bridge，不走 batch fallback。
  - Codex SDK/runtime 不可用时返回明确 stream error。

- `src/bub_codex/config.py`
  - `@bub.config(name="codex")` 配置。
  - 通过 `bub.ensure_config(...)` 读取。
  - 支持 `codex_bin`、`sdk_python_path`、`approval_policy`、`sandbox`、`config_overrides`、`env`。

### Runtime / Thread

- `src/bub_codex/runtime.py`
  - `BubCodexRuntime.ensure_thread_context()` 是 tape-first runtime context 边界。
  - 支持 bootstrap Anchor、latest Anchor materialization、existing thread resume。
  - resume failure 先写 `bub.runtime.error`，再暴露异常，不自动创建 replacement thread。

- `src/bub_codex/codex_thread_service.py`
  - `MaterializingCodexThreadService` 封装真实 Codex SDK thread lifecycle。
  - `codex.thread.bound` 的成立条件是 materialization turn 完成并形成可 resume rollout。
  - 读取 Codex notifications 时过滤 foreign thread records，避免背景 thread 干扰当前 turn。

- `src/bub_codex/live_stream.py`
  - MVP live notification bridge。
  - 消费 Codex notification records as they arrive。
  - 投影 tape events 后再输出 Bub stream decisions。
  - commentary 只写 tape，不输出为 `text`。
  - final answer 输出为 Bub `text` 和 `final.text`。
  - turn stream 异常会写 `bub.runtime.error` 并返回 stream error。

### Translation / Projection

- `src/bub_codex/turn_translator.py`
  - per-turn 有状态 Translator。
  - 负责 `raw notification -> CodexFact -> TapeEvent -> StreamDecision`。
  - 管理 final-answer collection 和 fallback final text。

- `src/bub_codex/runtime_adapter.py`
  - Codex raw notification 到 `CodexFact` 的 anti-corruption layer。

- `src/bub_codex/turn_projection.py`
  - user turn facts 到 tape events。

- `src/bub_codex/materialization_projection.py`
  - materialization turn facts 到 tape events。

- `src/bub_codex/tool_projection.py`
  - Codex item lifecycle 到 Bub tool / side-effect event。

- `src/bub_codex/context_materialization.py`
  - Anchor creation、context materialization、thread binding 和 bind failure events。

- `src/bub_codex/tape_events.py`
  - minimal tape event shape 和 compact Anchor projection。

### Tape / Diagnostics

- `src/bub_codex/republic_tape_store.py`
  - Bub/Republic tape store adapter。
  - 只还原 `bub-codex` 自己写入的 events。

- `src/bub_codex/tape_store.py`
  - `TapeStore` protocol。
  - `InMemoryTapeStore` 仅用于测试、spike 或显式禁用 Bub tape store 的开发场景。

- `src/bub_codex/notification_filter.py`
  - 当前 thread notification filtering。

- `src/bub_codex/runtime_diagnostics.py`
  - 最小 `bub.runtime.error` event factory。

### Manual / Smoke Scripts

- `scripts/verify_installed_plugin.py`
  - 本地验证已安装插件能被 Bub 发现。
  - 强制 `BUB_CODEX_ENABLED=false`，不启动真实 Codex runtime。
  - 断言 `bub hooks` 输出包含：

    ```text
    run_model_stream: builtin, codex
    ```

- `scripts/spikes/real_codex_resume_smoke.py`
  - 真实 Codex SDK live resume smoke。
  - 第一轮创建 Anchor/thread binding。
  - 第二轮用新 `CodexClient` 和同一 tape store resume 同一 Codex thread。
  - 断言第二轮没有新的 materialization / `codex.thread.bound`。

## 当前测试标准

默认必须通过：

```bash
.venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -m py_compile src/bub_codex/*.py tests/*.py scripts/*.py scripts/spikes/*.py
```

当前结果：

```text
20 tests OK
py_compile OK
```

本地安装发现验证：

```bash
uv pip install -e .
BUB_CODEX_ENABLED=false python scripts/verify_installed_plugin.py
```

期望输出：

```text
OK: Bub discovered installed bub-codex plugin (run_model_stream: builtin, codex).
```

真实 Codex SDK resume smoke：

```bash
python scripts/spikes/real_codex_resume_smoke.py
```

最近 artifact：

```text
artifacts/spikes/real-codex-resume-smoke-20260611-160011/result.json
```

关键结论：

```text
first_thread_id: 019eb5b2-434d-7cd2-9e1d-5260502de44d
second_resumed_thread_ids: [019eb5b2-434d-7cd2-9e1d-5260502de44d]
second_materialization_count: 0
second_event_types:
  codex.turn.started
  codex.assistant_message.completed
  codex.turn.completed
```

## 测试覆盖索引

- `tests/test_bub_plugin_package.py`
  - entry point target。
  - plugin factory。
  - SDK dependency importability。
  - BubFramework hook loading。
  - Bub tape store adapter selection。

- `tests/test_live_stream.py`
  - commentary 写 tape 但不输出 text。
  - final answer 输出 text/final。
  - existing thread resume。
  - resume failure 不 materialize replacement，并写 `bub.runtime.error`。
  - latest Anchor without binding materializes thread。
  - compaction notification creates compact Anchor。
  - foreign thread notification filtering。
  - turn stream failure writes `bub.runtime.error`。

- `tests/test_codex_thread_service.py`
  - foreign `turn/completed` 不会结束当前 thread turn。

- `tests/test_turn_translator.py`
  - Translator final-answer / fallback / compact projection semantics。

- `tests/test_republic_tape_store.py`
  - Republic `FileTapeStore` round trip。
  - 从 persisted tape 推导 active runtime thread。

- `tests/test_plugin_stream_integration.py`
  - plugin stream integration fixture。

## 文档产物

- `README.md`
  - 当前状态、安装验证、最小配置、手动真实 resume smoke。

- `CONTEXT.md`
  - 领域语言、当前状态、核心决策、open design questions、ADR index。

- `docs/prd-mvp-live-codex-runtime.md`
  - MVP product boundary 和验收标准。

- `docs/research/2026-06-11-mvp-readiness-review.md`
  - readiness review、已满足项、剩余 hardening。

- `docs/research/2026-06-11-validated-spike-summary.md`
  - spike 过程和真实验证总结。

- `docs/research/2026-06-11-multica-codex-runtime-reference.md`
  - Multica 调研及对 `CodexEnvironment` 的启发。

- `docs/adr/0001-codex-compaction-summary-source.md`
- `docs/adr/0002-tape-anchor-thread-semantics.md`
- `docs/adr/0003-codex-runtime-adapter-facts.md`
- `docs/adr/0004-runtime-resolution-and-new-thread-materialization.md`
- `docs/adr/0005-dynamic-tool-provider-boundary.md`
- `docs/adr/0006-event-contract-layers-and-namespaces.md`
- `docs/adr/0007-bub-plugin-entrypoint-run-model-stream.md`

## 开发过程提交线

```text
1ea0b0e Initial bub-codex MVP skeleton
372700c Introduce Codex turn translator
eddcb31 Add MVP readiness review
8082448 Cover live thread resume edge cases
ea81b38 Add installed plugin verification check
0db2262 Filter Codex notifications to current thread
047922f Record runtime diagnostic errors on tape
7d38269 Add real Codex resume smoke
dcf5a8e Refresh MVP candidate status docs
```

## GitHub Issues 状态

已关闭：

- #1 `P0: Add live resume failure coverage`
- #2 `P0: Cover latest Anchor without thread binding materialization`
- #3 `P0: Add installed plugin verification check`
- #4 `P1: Define current-thread notification filtering`
- #5 `P1: Add minimal runtime diagnostic error event strategy`
- #6 `P1: Add real SDK resume smoke`
- #7 `P2: Refresh README and CONTEXT status wording`

仍打开：

- #8 `P2: Design post-MVP CodexEnvironment module`

#8 不阻塞当前 MVP candidate skeleton；它属于 post-MVP runtime hardening。

## 当前明确不进入 MVP

- Bub dynamic tool hosting production contract。
- token-level assistant streaming。
- 主动/manual compact triggering。
- context overflow policy。
- approval UX / policy engine。
- Langfuse / OTel / Obelisk projection。
- replay engine / UI timeline。
- full schema for reasoning、usage、MCP tools、collab agents、web search、image items。
- full `CodexEnvironment` Module。

## Release-ready 前建议补齐

1. 建立真正的 release checklist issue，把默认验证命令和手动 smoke 分层。
2. 决定 real compact smoke 是否需要成为手动 release gate。
3. 决定 async Republic tape store 的处理策略：明确不支持、同步 wrapper、或 async runtime path。
4. 完成 #8 `CodexEnvironment` post-MVP 设计，避免 app-server lifecycle 和 runtime config 继续散落。
5. 在真实 Bub 环境中再跑一次 installed plugin discovery + one live turn + resume smoke。
