# MVP Readiness Review

## 结论

当前状态已经不是单纯 spike scaffold，而是接近 **MVP candidate skeleton**：

```text
package entry point
  -> Bub plugin factory
  -> live Codex SDK bridge
  -> Bub/Republic tape adapter
  -> Anchor/thread resolution
  -> Translator-driven stream semantics
```

已经跑通。

但它还不应标记为 MVP ready。原因不是主链路缺失，而是以下能力还没有足够稳定：

- resume failure 的 live boundary 测试不足。
- latest Anchor without binding 的 materialization 测试不足。
- live stream 中当前 thread filtering / foreign thread handling 未定义。
- error/diagnostic tape 语义不足。
- installed `bub hooks` / installed real plugin smoke 还没有形成可重复检查。
- README / CONTEXT 中部分状态描述滞后于代码。

因此当前推荐状态：

```text
MVP candidate skeleton, not release-ready MVP
```

## 已满足的 MVP 条件

### Packaging / entry point

已满足：

- `pyproject.toml` 声明 Python package。
- `[project.entry-points."bub"]` 注册：

  ```text
  codex = "bub_codex.plugin:create_plugin"
  ```

- `create_plugin(framework)` 返回 `BubCodexPlugin`。
- package root `bub_codex` 已收窄为插件入口相关 Interface。
- `openai-codex` 已是项目依赖。
- `BubCodexSettings` 使用 `@bub.config(name="codex")` 注册。
- 配置通过 `bub.ensure_config(BubCodexSettings)` 读取。

已有测试：

```text
tests/test_bub_plugin_package.py
```

覆盖：

- entry point target loads plugin factory
- SDK dependency importable
- BubFramework loads package entry point
- `run_model_stream` hook route works with fake runtime
- runtime can use Bub tape store
- runtime can explicitly disable Bub tape store for tests

### Production path uses live bridge

已满足：

- `build_runtime_stream_service()` 构造 `BubCodexLiveRuntimeStreamService`。
- 配置缺失 / SDK 不可 import 时返回明确 unconfigured stream error。
- 不从正式入口 fallback 到 batch `BubCodexRuntimeStreamService`。

当前 batch path 仍存在，但已经明确为 reference/spike path：

```text
BubCodexRuntimeStreamService
  -> BubCodexRuntime.run_turn()
  -> stream_runtime_turn_result()
```

并且它的 final-answer 语义复用 Translator，降低行为漂移风险。

### Tape-first runtime context

已满足：

- `BubCodexRuntime.ensure_thread_context()` 只从 `TapeStore` 推导 runtime context。
- 新 session 创建 bootstrap Anchor。
- latest Anchor + thread binding 进入 `resume_thread`。
- latest Anchor without binding 可 materialize thread。
- new thread binding 在 materialization 成功后写入。
- thread materialization turn id 会写入 `bub.context.materialized` / `codex.thread.bound` refs。

已有测试覆盖部分：

```text
tests/test_republic_tape_store.py
tests/test_live_stream.py
```

### Live notification projection

已满足：

- live bridge 消费 Codex notifications as they arrive。
- `CodexTurnTranslator` 拥有：

  ```text
  raw notification -> CodexFact -> TapeEvent -> StreamDecision
  ```

- `phase=commentary` 写 tape，不出 `StreamEvent("text")`。
- `phase=final_answer` 写 tape，并驱动 `text` / `final.text`。
- 无 final_answer 时 fallback 到最后一条 assistant message。
- context compaction notification 创建 compact Anchor。
- batch/reference stream 也复用 Translator 的 final-answer 语义。

已有测试：

```text
tests/test_turn_translator.py
tests/test_live_stream.py
tests/test_plugin_stream_integration.py
```

### Real smoke evidence

真实 Codex SDK live smoke 已多次通过。最近一次：

```text
artifacts/spikes/real-codex-live-stream-20260611-143207/result.json
```

关键结果：

```text
stream_kinds = ["text", "final"]
text_equals_final = True
assistant phases = commentary...final_answer
tool started/completed/failed events present
side_effect started/completed events present
```

## 部分满足但需要补强

### Resume existing thread

当前已有 fake live bridge resume 测试：

```text
tests/test_live_stream.py
  test_live_bridge_resumes_thread_from_tape_binding
  test_live_bridge_surfaces_resume_failure_without_materializing_replacement
```

已补：

- real SDK two-turn resume smoke：`scripts/spikes/real_codex_resume_smoke.py`。
- resume failure 的 stream error payload / tape error semantics：`bub.runtime.error`。

PRD 要求 resume failure 不自动创建 replacement thread。实现层 `BubCodexRuntime.ensure_thread_context()` 会记录 `bub.runtime.error` 后继续抛出 `resume_thread()` 异常，live bridge 会转成 stream error。这条主语义已有 fake live bridge 测试覆盖。

### Latest Anchor without binding materializes new thread

runtime facade 支持该路径，live bridge 层已补测试：

```text
tests/test_live_stream.py
  test_live_bridge_materializes_thread_from_latest_anchor_without_binding
```

### Command / side-effect coverage

已有 command started/completed 与 real smoke 覆盖 command failed。

还缺正式单元测试：

- `commandExecution` failed ordering。
- `fileChange` failed projection。
- fileChange started/completed ordering 的独立 fake stream 测试。

### Republic tape integration

已有 `FileTapeStore` round-trip 测试。

但 PRD 已记录：

```text
Async Republic tape stores are detected but not yet supported from an already-running event loop.
```

这可以留在 post-MVP hardening，但需要明确为接受的限制。

### README / CONTEXT 状态描述

已更新。

当前 README 和 CONTEXT 使用：

```text
MVP candidate skeleton, not release-ready MVP
```

最终交付检查点集中记录在 `docs/release/mvp-candidate-checkpoint.md`。原因是 package / live bridge 已经存在，但 hardening 和 checklist 还未完成。

## 明确缺口

### P0: live resume failure test

已补。

价值：

- 保护“resume failure 先暴露，不自动 new thread”的核心决策。
- 防止未来引入 Multica-style fallback fresh thread。

```text
tests/test_live_stream.py
  test_live_bridge_surfaces_resume_failure_without_materializing_replacement
```

### P0: latest Anchor without binding live test

已补。

价值：

- 覆盖 handoff/new-thread materialization 直觉路径。
- 保护“先 Anchor，后 thread binding”的设计。

```text
tests/test_live_stream.py
  test_live_bridge_materializes_thread_from_latest_anchor_without_binding
```

### P0: installed plugin verification command

当前测试 patch 了 `importlib.metadata.entry_points`，但没有自动跑真实 installed discovery。

应增加一个 explicit local check 文档或脚本：

```text
uv pip install -e .
BUB_CODEX_ENABLED=false uv run bub hooks
```

期望：

```text
run_model_stream: builtin, codex
```

是否进入单元测试要谨慎，因为它依赖当前环境安装状态。

### P1: current-thread notification filtering

Multica 调研显示同一 Codex app-server pipe 可能出现非当前 thread events。

当前 `CodexTurnTranslator` 不过滤 thread_id；它信任 `CodexTurnStreamService` 只返回当前 turn records。

需要决策：

```text
filter in CodexTurnStreamService?
filter in CodexTurnTranslator?
project as background thread event?
fail fast if record threadId mismatches expected thread?
```

MVP 推荐：

```text
CodexTurnTranslator 接收 expected_thread_id，可忽略或记录 foreign-thread notification。
```

但这会扩大 Translator Interface。也可以先在 `MaterializingCodexThreadService.run_turn_stream_records()` 过滤。

### P1: diagnostic/error event schema

当前错误主要转成 stream error：

```text
StreamEvent("error", {"kind": "unknown", "message": ...})
```

缺少 tape-side diagnostic event。

应设计最小：

```text
bub.runtime.error
或 codex.runtime.error
```

payload 可包含：

```text
stage
error_type
message
thread_id
turn_id
codex_version?
stderr_tail?
last_semantic_activity?
```

MVP 可以先只补 stream boundary 测试，不强制 tape error schema；但 release 前最好至少有一条结构化 diagnostic path。

### P1: real SDK resume smoke

已补手动 smoke：

```text
scripts/spikes/real_codex_resume_smoke.py
```

脚本验证：

```text
turn 1 creates thread and writes tape
turn 2 reuses same tape store
live bridge resumes existing Codex thread
assert no new materialization
```

这对 MVP 信心很关键，但因为真实 SDK/model 成本，不建议进默认 unit suite。

最近 artifact：

```text
artifacts/spikes/real-codex-resume-smoke-20260611-160011/result.json
```

### P2: README status update

README 应从“还没有 formal package entrypoint/live bridge”更新为当前状态。

### P2: issue backlog

将上述缺口转为 GitHub Issues，便于后续执行。

## 不进入 MVP

保持不进入：

- Bub dynamic tool hosting production contract。
- token-level streaming。
- active/manual compact triggering。
- context overflow policy。
- approval UX / policy engine。
- Langfuse / OTel / Obelisk projection。
- replay engine / UI timeline。
- full schema for reasoning、usage、MCP tools、collab agents、web search、image items。
- full `CodexEnvironment` Module。

`CodexEnvironment` 作为 post-MVP hardening candidate 保留。

## 推荐下一步

按顺序：

1. 补 P0 测试：

   ```text
   live resume failure surfaces error and does not materialize replacement
   latest Anchor without binding materializes thread from Anchor
   ```

2. 更新 README / CONTEXT 状态描述，改成：

   ```text
   MVP candidate skeleton, not release-ready MVP
   ```

3. 创建 GitHub Issues：

   ```text
   P0 live resume failure coverage
   P0 latest Anchor materialization coverage
   P0 installed plugin verification
   P1 current-thread notification filtering
   P1 diagnostic/error event schema
   P1 real SDK resume smoke
   P2 README status update
   P2 CodexEnvironment post-MVP design
   ```

4. 再选一个 P0 issue 实现并提交。
