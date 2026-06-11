# 真实 Bub CLI + bub-codex 插件研究

日期：2026-06-11

## 目标

验证 `bub-codex` 在真实 Bub CLI 模式下的行为，而不是只验证 SDK harness 或 fake framework：

- 使用真实 `python -m bub run`。
- 使用真实 `python -m bub chat`。
- 通过已安装的 Bub entry point 加载 `bub-codex` 插件。
- 使用真实 `openai_codex` SDK 和本机 `codex` binary。
- 执行多轮较复杂任务与聊天。
- 检查 stdout/stderr、workspace 文件副作用、Bub tape 持久化和 resume 语义。

## 运行入口

新增研究脚本：

```text
scripts/spikes/real_bub_cli_research.py
```

脚本会：

1. 创建隔离 workspace。
2. 用固定 `session_id` 连续运行 3 次真实 `bub run`。
3. 记录每轮 CLI command、stdout、stderr、return code、耗时。
4. 收集 workspace 文件内容。
5. 比较 `~/.bub/tapes/*.jsonl` 前后变化。
6. 输出 artifact。

最近 artifact：

```text
artifacts/spikes/real-bub-cli-research-20260611-170333/result.json
```

## `bub run` 结果

命令形态：

```text
.venv/bin/python -m bub --workspace <workspace> run --session-id <session> --chat-id research <message>
```

环境：

```text
BUB_CODEX_ENABLED=true
BUB_CODEX_WORKSPACE=<workspace>
BUB_CODEX_APPROVAL_POLICY=never
BUB_CODEX_SANDBOX=danger-full-access
BUB_CODEX_CODEX_BIN=/opt/homebrew/bin/codex
```

三轮任务：

1. 创建 `weather_stats` Python 小项目，实现 `mean`、`median`、`summarize`，并运行 unittest。
2. 继续同一 session，新增 `standard_deviation`，更新 tests，再运行 unittest。
3. 只聊天总结 public functions，不编辑文件。

执行结果：

```text
turn-1-project       returncode=0 elapsed=77.946s
turn-2-resume-modify returncode=0 elapsed=120.821s
turn-3-chat          returncode=0 elapsed=54.530s
```

生成文件：

```text
weather_stats/weather_stats.py
weather_stats/test_weather_stats.py
```

文件内容符合任务要求，第二轮成功修改并测试通过。第三轮正确读取 workspace 状态并给出总结。

## `bub chat` 结果

第一次直接运行 chat 时，workspace 目录不存在：

```text
BUB_CODEX_WORKSPACE=/tmp/bub-codex-real-chat-workspace-20260611
```

结果：

```text
bub-codex runtime is not configured:
[Errno 2] No such file or directory: '/tmp/bub-codex-real-chat-workspace-20260611'
```

创建 workspace 目录后重跑 `bub chat` 成功：

1. 第一轮创建 `chat_notes.md`。
2. 第二轮读取该文件并追加第四条 observation。

最终文件：

```text
- Bub-Codex integration tests should verify anchor materialization preserves intent, timestamp, and channel metadata.
- Test fixtures need to cover both CLI-originated context and chat-thread continuation behavior.
- Assertions should check the created workspace artifacts and the final assistant acknowledgement, not only command success.
- Real Bub CLI mode should be exercised end to end to catch differences from mocked transport or replayed anchor flows.
```

## 重要发现：真实 CLI 没有持久化 bub-codex tape events

虽然 `bub run` 和 `bub chat` 的任务执行成功，但 `~/.bub/tapes/*.jsonl` 没有新增或变化。

研究脚本分析结果：

```json
{
  "all_runs_succeeded": true,
  "changed_tapes": [],
  "event_types": [],
  "unique_thread_ids": [],
  "runtime_error_count": 0
}
```

这说明真实 Bub CLI 模式下：

- Codex SDK 确实被调用。
- 文件副作用确实发生。
- Bub CLI 确实通过插件得到最终回答。
- 但 `bub-codex` tape events 没有进入 Bub builtin `FileTapeStore`。

最可能原因：

1. Bub CLI 在 `BubFramework.load_hooks()` 阶段加载插件。
2. `bub-codex.create_plugin()` 当前在插件初始化时立即调用 `build_runtime_stream_service()`。
3. `build_runtime_stream_service()` 此时调用 `_runtime_tape_store(framework, settings)`。
4. 真实 Bub `FileTapeStore` 只在 `async with framework.running()` 期间通过 `framework.get_tape_store()` 暴露。
5. 插件初始化发生在 `framework.running()` 之前，因此 `framework.get_tape_store()` 返回 `None`。
6. `bub-codex` 退回 `InMemoryTapeStore`。
7. 每次 `bub run` 都是新进程，因此 tape-derived resume 不可能跨进程成立。

这解释了一个表象：

```text
第二轮 assistant 说 resumed existing context
```

但从 Bub tape 角度看，这不是已验证的 tape-derived resume。它可能来自 Codex 自身本地 thread state、workspace 文件、或模型推断，而不是 Bub tape canonical state。

## 对当前方案的影响

这个发现不推翻已有 SDK/live bridge spike，但会改变真实 CLI readiness 判断：

- 单元测试中的 `BubFramework` fake path 可以验证 adapter 行为。
- 真实 SDK resume smoke 可以验证 runtime 在同一进程、同一 tape store 下的 resume 语义。
- 真实 Bub CLI path 暴露了 production integration 差异：runtime service 不能在插件初始化时固定 tape store。

因此，真实 CLI 模式不能只看 final text 或 workspace 文件是否正确。release gate 必须同时检查：

```text
任务成功
workspace 副作用正确
Bub FileTapeStore 持久化了 bub-codex events
第二轮从 persisted tape 推导出 same thread_id resume
```

## 建议修复方向

需要把 runtime service 构造从 plugin initialization 推迟到 `run_model_stream` 调用期，或让 live stream service 在每轮开始时重新解析当前 `framework.get_tape_store()`：

```text
create_plugin(framework)
  -> 保存 framework + settings
  -> 不启动 CodexClient / 不固定 TapeStore

run_model_stream(...)
  -> 此时处于 framework.running()
  -> framework.get_tape_store() 可用
  -> 构造或刷新 runtime service
  -> 使用 RepublicTapeStoreAdapter(FileTapeStore)
```

同时要处理 CodexClient lifecycle：

- 每个 plugin process 一个 client。
- 或按 turn lazy start client。
- 需要明确关闭策略，避免 app-server 泄露。

## 建议新增 issue

建议新增 P0/P1 issue：

```text
Fix real Bub CLI tape persistence by lazy-binding runtime store
```

验收标准：

- 真实 `python -m bub run` 产生 `~/.bub/tapes` 变化。
- tape 中有 `bub-codex` meta events。
- 第二次 `bub run --session-id same` 从 persisted tape 推导 resume。
- 研究脚本 `scripts/spikes/real_bub_cli_research.py` 的 `tape_persisted_bub_codex_events=true`。
- 不引入 batch fallback。

## 结论

真实 Bub CLI + bub-codex 插件可以完成复杂任务和多轮聊天，Codex SDK、文件编辑、命令执行、最终回答都工作。

但真实 CLI 模式目前没有持久化 `bub-codex` tape events，因此还不能宣称 CLI production path 满足 tape-first resume 语义。这个问题应作为 release-ready 前的核心修复项。
