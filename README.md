# bub-codex

`bub-codex` explores embedding Codex as a native Bub coding runtime.

The goal is not to wrap Bub around `codex e` as an external subprocess. The goal is to make Codex participate in Bub's runtime model directly: hooks, tapes, channels, tools, observability, and session context should be first-class runtime concerns rather than incidental CLI IO.

## Context

- [bubbuild/bub](https://github.com/bubbuild/bub) is the core Bub runtime: a hook-first runtime for agents that live alongside people.
- [bubbuild/bub-contrib](https://github.com/bubbuild/bub-contrib) contains ecosystem packages, including an existing `packages/bub-codex` plugin that delegates model execution to the Codex CLI.
- [tape.systems](https://tape.systems/) frames context as an append-only fact model for long-running work.
- [tommy0103/obelisk](https://github.com/tommy0103/obelisk) exposes past agent work as structured local data for agent queries.
- [langfuse/codex-observability-plugin](https://github.com/langfuse/codex-observability-plugin) traces Codex agent turns, tool calls, and subagents to Langfuse.

## Design Intent

This repository starts from a narrower claim than the existing `bub-contrib` Codex integration:

> Codex should run as a Bub-native coding runtime, not as an opaque CLI child process.

That implies:

- Bub can observe and shape Codex turn stages through hooks.
- Codex sessions can be represented in Bub tapes rather than only terminal transcripts.
- Tool calls, subagents, approvals, and file edits can become structured runtime events.
- Observability can be attached at the runtime boundary, not scraped from process output.
- Past work can be queried through systems like Obelisk without depending on ad-hoc chat history.

## Initial Questions

- Which Codex runtime APIs are stable enough to embed directly?
- What is the minimal Bub hook surface needed for a coding runtime?
- How should Codex session state map onto Bub tapes?
- Which events belong in Bub's domain model versus an observability adapter?
- How should approval, sandboxing, and tool execution be represented when Codex is not just a subprocess?

## Repository Status

This repository currently contains an MVP candidate skeleton, not a
release-ready MVP.

The package now has a Bub plugin entry point, Bub config wiring, a live
`run_model_stream` bridge, Bub/Republic tape store integration, current-thread
notification filtering, minimal runtime diagnostic events, and tests for Anchor
bootstrap, thread materialization, resume, compaction Anchor projection, and
stream final-answer behavior.

Real Codex SDK smoke tests have covered assistant messages, command execution,
command failure/retry, file changes, Anchor bootstrap, thread materialization,
turn completion, and two-turn live resume from the same tape-derived thread
binding. These remain manual checks because they depend on the external Codex
runtime and model behavior.

Current release readiness is tracked in
[MVP Readiness Review](docs/research/2026-06-11-mvp-readiness-review.md).

## Bub Plugin Package

MVP 必须按 Bub plugin package 规范收敛，而不是只保留可手动运行的
spike 模块。

当前最小入口：

- `pyproject.toml` 声明 Python package。
- `openai-codex` 是项目依赖，import 名为 `openai_codex`。
- `[project.entry-points."bub"]` 注册 `codex = "bub_codex.plugin:create_plugin"`。
- `create_plugin(framework)` 捕获 Bub `BubFramework`，构造只实现
  `run_model_stream` 的 `BubCodexPlugin`。
- `BubCodexSettings` 使用 `@bub.config(name="codex")` 注册配置，并通过
  `bub.ensure_config(BubCodexSettings)` 读取。
- 插件必须安装到运行 Bub 的同一个 Python 环境中。

开发期安装：

```bash
uv pip install -e .
BUB_CODEX_ENABLED=false python scripts/verify_installed_plugin.py
```

这个检查只验证 Bub 能从已安装的 package entry point 发现 `codex` 插件；
`BUB_CODEX_ENABLED=false` 会避免启动真实 Codex runtime。脚本内部运行
`python -m bub hooks`，并断言输出包含：

```text
run_model_stream: builtin, codex
```

当前 MVP 骨架已覆盖：

- installed package entry point discovery
- live `run_model_stream` bridge
- Bub/Republic `FileTapeStore` 持久化读回
- 从 tape 推导 existing Codex thread resume
- Codex compaction notification 创建 Bub Anchor

最小配置可以放在 Bub config 的 `codex:` 段，也可以用 `BUB_CODEX_*`
环境变量覆盖：

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

通常不需要设置 `sdk_python_path`；它只用于开发期临时指向本地
`openai-codex/sdk/python/src` checkout。如果 `openai_codex` SDK 或 Codex
runtime 不可用，插件会返回明确的 `bub-codex runtime is not configured`
错误。MVP 生产路径不引入 batch fallback；逗号命令仍委托给 Bub builtin
agent。

真实 Codex SDK resume smoke 保持为手动检查，不进入默认单元测试：

```bash
python scripts/spikes/real_codex_resume_smoke.py
```

该脚本会运行两轮 live bridge turn，复用同一个 tape store，并断言第二轮
resume 第一轮绑定的 Codex thread，没有创建 replacement thread。结果写入
`artifacts/spikes/real-codex-resume-smoke-*/result.json`。
