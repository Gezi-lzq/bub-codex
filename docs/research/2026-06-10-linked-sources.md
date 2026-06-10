# 链接资料探索

日期：2026-06-10

本文记录对 `bub-codex` 初始参考链接的源码级观察。重点不是复述各项目介绍，而是判断它们应如何影响 Bub-native Codex runtime 的设计，尤其是如何避免把 `codex e` 当作一个终端子进程来包装。

## 资料来源

- Bub: https://github.com/bubbuild/bub
- Bub contrib: https://github.com/bubbuild/bub-contrib
- Tape Systems: https://tape.systems/
- Obelisk: https://github.com/tommy0103/obelisk
- Langfuse Codex observability plugin: https://github.com/langfuse/codex-observability-plugin

## Bub Core

观察到的事实：

- Bub 将自己定位为 hook-first runtime，让 agent 与人一起工作。
- 公开的 turn pipeline 是：
  `resolve_session -> load_state -> build_prompt -> run_model -> save_state -> render_outbound -> dispatch_outbound`。
- 实际 hook surface 比这个简化 pipeline 更宽。`hookspecs.py` 还包含 `run_model_stream`、`provide_tape_store`、`build_tape_context`、`admit_message`、`on_error`、`provide_channels`、`system_prompt`。
- `BubFramework.process_inbound()` 会构造包含 `_runtime_workspace` 和 `_runtime_steering` 的 runtime state，收集 `load_state` 结果，构建 prompt，运行 model，在 `finally` 中保存 state，然后渲染并派发 outbound messages。

设计含义：

- Bub-native Codex runtime 不应只被建模为一个 `run_model` override。
- 更有价值的集成边界可能是 runtime event interface：它能把 Codex 活动映射到 `run_model_stream`、`save_state`、tape context、admission/steering 和 error hooks。
- Streaming 很重要。Codex tool calls、file changes 和 subagent events 需要结构化 stream 或事件捕获，而不只是最终 stdout 文本。approval 可作为后续治理层处理，不进入 v0 主路径。

## 现有 bub-contrib Codex Plugin

观察到的事实：

- `packages/bub-codex` 明确是 Bub 的 Codex model plugin。
- 它提供 Bub plugin entry point 和一个 `run_model` hook implementation。
- 它调用 `codex` CLI。
- 它通过 `codex e resume <session_id>` 做 session continuation。
- 它支持把 Bub `skills` 临时接入 workspace `.agents/skills`。
- 代码中使用 workspace-local `.bub-codex-threads.json`，把 Bub `session_id` 映射到 Codex thread id。
- 如果 prompt 以 `,` 开头，它会尝试把 prompt 回退给 Bub builtin agent 作为 internal command 执行。
- 普通 prompt 会被组装成 `codex e ...` 命令，并在 workspace 中作为 subprocess 执行。

设计含义：

- 这个 plugin 是要超越的 baseline，不是目标架构。
- thread mapping 仍有迁移价值：Bub session identity 和 Codex thread identity 是不同身份，需要显式绑定。
- 逗号命令 fallback 很有价值，因为它保留了 Bub-native control messages。native runtime 应保留这个语义分割，但应让它成为一等能力，而不是 subprocess wrapper 里的例外分支。
- 临时 skill symlink 暴露了一个更深的边界问题：Codex 需要访问 Bub skills，但 native runtime 应通过 runtime/tool model 呈现它们，而不是修改 workspace 文件系统。

## Bub Tape Store Plugins

观察到的事实：

- `bub-contrib` 包含 SQLite、SQLAlchemy、Redis 和 OTel tape store backends。
- `bub-tapestore-sqlite` 通过 `provide_tape_store` 覆盖 Bub builtin file tape store。
- `bub-tapestore-otel` 是透明 tape-store decorator。它包装 active tape store，并在 tape write 成功提交后把 committed tape writes 投影到 OpenTelemetry。
- OTel plugin 将真实 tape backend 与 telemetry export 分离，并把 export failure 视为非致命错误。

设计含义：

- `bub-codex` 应保持 runtime facts tape-first，并把 observability 作为 projection。
- 第一版设计应区分这些层：
  - runtime event capture
  - durable tape entries
  - derived observability spans
  - query/replay views
- Langfuse 或 OTel 不应拥有 canonical runtime model。

## Tape Systems

观察到的事实：

- Tape Systems 将 context 建模为带 anchors 和 assembled views 的 append-only timelines。
- compacting 不删除历史，只缩小默认 read set。
- summaries 应引用来源，并只作为 hints。
- fork/merge 只追加 deltas，不重写 mainline。
- shared tapes 保留 origin；cross-tape views 是组装出来的，tapes 本身保持隔离。
- observability 部分明确把 sessions、tool calls 和 runtime events 视为 append-only facts，可用于 replay、UI timelines、QA 和 agent explanation。

设计含义：

- Codex turn history 应记录为 append-only facts，而不是从 terminal output 里重建。
- handoff、compaction 和 resume 应用 anchors、source ids 和 default read-set changes 建模。
- subagents 和 parallel explorations 应映射到 forked 或 linked tapes，并带有显式 lineage。
- native runtime 应让 Bub 能从同一组 facts 回答 “what happened?”，而这些 facts 同时也用于 context assembly。

## Obelisk

观察到的事实：

- Obelisk 不是给人用的 session browser，而是给 agents 查询过去工作的系统。
- 它索引 sessions、messages、tool calls、subagents、workflows、file history、failures 和 parent chains。
- 它的核心查询表面很小：full-text search、context lookup、raw SQL。
- 它使用 progressive disclosure：先暴露核心 API，只有需要时才读取 schema 和 query recipes。

设计含义：

- Bub-native Codex 应产生 agent 可查询的历史，而不是让 agent scrape 非结构化 chat logs。
- event schema 应保留 parent chains、tool inputs/outputs、touched file paths、subagent linkage、workflow boundaries 和 failures。
- 面向 tape/session data 的小型 query surface，可能比一个很宽的 bespoke API 更有用。

## Langfuse Codex Observability Plugin

观察到的事实：

- 该 plugin 会把 Codex turns、model calls、tool executions、token usage 和 subagent threads trace 到 Langfuse。
- 它在每个 Codex turn 后读取 session rollout transcript，并重建 model steps、tool calls、usage 和 subagents。
- 它用 Codex thread id 将同一 Codex session 的所有 turns 分组。
- 它用 sidecar file 记录已上传的 turn ids，避免重复上传。
- 它 fail open：tracing errors 会被记录/吞掉，不阻塞 Codex session。
- 启用后，它会发送 prompts、assistant messages、reasoning summaries、tool-call inputs/outputs、model metadata 和 token usage。

设计含义：

- rollout transcript parsing 是有用参考，但它本质上是 Codex-as-black-box 之后的 workaround。
- Bub native runtime 应在这些事件变成 post-hoc transcript 之前捕获等价事件。
- Langfuse-style traces 是 output projection。canonical data 应保持为 Bub runtime events / tape entries。
- deduplication 和 idempotency 需要显式 event ids 或 turn ids；sidecars 是 plugin 层 workaround。
- privacy 和 truncation policy 应属于 observability export，而不是底层 tape schema。

## Codex Python SDK

资料来源：

- Codex manual: `Codex SDK` / `Use Codex with the Agents SDK`
- 本地源码：`/tmp/bub-codex-sources/openai-codex/sdk/python`
- 官方仓库路径：https://github.com/openai/codex/tree/main/sdk/python

观察到的事实：

- 官方 manual 明确存在 Python SDK，包名是 `openai-codex`。
- Python SDK 通过本地 Codex `app-server` 的 JSON-RPC 控制 Codex，而不是直接调用一次性 CLI 命令。
- SDK 处于 beta；公开 API 可能在 `1.0` 前变化。
- Published SDK builds 会携带 pinned Codex CLI runtime dependency。当前源码 `pyproject.toml` 中依赖 `openai-codex-cli-bin==0.137.0a4`。
- SDK 同时提供 sync 和 async API：`Codex` / `AsyncCodex`。
- `CodexConfig` 可以指定 `codex_bin`、`launch_args_override`、`config_overrides`、`cwd`、`env`、client metadata，并默认通过 `codex app-server --listen stdio://` 启动 runtime。
- SDK 支持多种登录方式：复用已有 Codex auth、ChatGPT browser login、device-code login、API key login。
- thread lifecycle 是一等 API：
  - `thread_start`
  - `thread_list`
  - `thread_resume`
  - `thread_fork`
  - `thread_archive`
  - `thread_unarchive`
  - `thread.read(include_turns=True)`
  - `thread.compact()`
- turn lifecycle 也是一等 API：
  - `thread.run(...)`：便利方法，启动 turn、消费通知、返回 `TurnResult`
  - `thread.turn(...)`：低层方法，返回 `TurnHandle`
  - `TurnHandle.stream()`：消费该 turn 的 notification stream
  - `TurnHandle.steer(...)`：给 active turn 注入 steering input
  - `TurnHandle.interrupt()`：中断 active turn
- SDK 文档说明 turn streams 按 turn ID 路由，因此一个 client 可以同时消费多个 active turns。
- `TurnResult` 返回：
  - `id`
  - `status`
  - `error`
  - `started_at`
  - `completed_at`
  - `duration_ms`
  - `final_response`
  - `items`
  - `usage`
- 输入支持纯文本和多模态/结构化输入：
  - `TextInput`
  - `ImageInput`
  - `LocalImageInput`
  - `SkillInput`
  - `MentionInput`
- thread 和 turn 都支持 sandbox/approval/model 等控制：
  - `ApprovalMode.auto_review`
  - `ApprovalMode.deny_all`
  - `Sandbox.read_only`
  - `Sandbox.workspace_write`
  - `Sandbox.full_access`
  - `model`
  - `effort`
  - `output_schema`
  - `personality`
  - `summary`
  - `service_tier`
- SDK 的 public protocol types 暴露了比较丰富的 notification 和 thread item model，包括：
  - `item/agentMessage/delta`
  - `item/plan/delta`
  - `item/commandExecution/outputDelta`
  - `item/fileChange/outputDelta`
  - `item/mcpToolCall/progress`
  - `item/reasoning/summaryTextDelta`
  - `item/reasoning/summaryPartAdded`
  - `item/reasoning/textDelta`
  - `thread/compacted`
  - `fs/changed`
  - `guardianWarning`
  - `model/rerouted`
  - `turn/completed`
- completed `ThreadItem` types 包含：
  - `agentMessage`
  - `plan`
  - `reasoning`
  - `commandExecution`
  - `mcpToolCall`
  - `dynamicToolCall`
  - `imageView`
  - `imageGeneration`
  - `contextCompaction`
- `CommandExecutionThreadItem` 包含 `command`、`commandActions`、`cwd`、`durationMs`、`exitCode`、`processId`、`source`、`status`、`aggregatedOutput`。
- `McpToolCallThreadItem` 包含 `server`、`tool`、`arguments`、`status`、`durationMs`、`result`、`error`、`pluginId`。
- `TurnStatus` 包含 `completed`、`interrupted`、`failed`、`inProgress`。

设计含义：

- 之前 “可能需要解析 rollout JSONL 才能获得事件” 的假设应降级。Python SDK 已经暴露了 thread/turn/control/stream/items/usage 等一等 API，应该优先作为 Bub-native integration 的底层通道。
- `run_model_stream` 的可行性显著提高：Bub 可以从 `TurnHandle.stream()` 消费 Codex notifications，并转换成 Republic `StreamEvent` 与 Bub tape events。
- `TurnResult.items` 和 `ThreadItem` 类型很适合作为 canonical event vocabulary 的初始来源。相比 Langfuse parser 的 post-hoc reconstruction，SDK stream 更接近 runtime-time capture。
- Bub `admit_message` / `_runtime_steering` 与 SDK `steer()` / `interrupt()` 有天然映射关系。这个方向比在 prompt 或 stdout 中编码控制块更干净。
- Bub v0 可以先假设最大权限：SDK 层使用 `Sandbox.full_access`，wire 层对应 `danger-full-access`。approval policy 不作为第一版核心能力；若需要抑制 approval prompts，再验证 `ApprovalMode.deny_all` 映射到 wire `approvalPolicy: "never"` 后的实际语义。
- `thread_fork`、`thread_resume`、`thread.compact()` 能直接支撑 Bub 的 handoff、fork 和 compaction 语义，但需要明确 Bub tape anchor 与 Codex thread operation 的对应关系。
- `SkillInput` / `MentionInput` 可能是替代 `.agents/skills` 临时 symlink 的关键路径，应继续验证它们在 app-server 中如何影响 Codex context。
- `thread.read(include_turns=True)` 可能提供 replay/query projection 的补数据通道，但 canonical capture 仍应优先来自 live stream。

仍需验证的问题：

- SDK stream 的 notification 是否覆盖所有需要的 coding runtime events，尤其是 patch apply request/result、subagent spawn/result。
- `SkillInput`、`MentionInput` 是否可以表达 Bub skills，还是仍依赖 Codex 自己的 skill discovery 机制。
- SDK 的 `items` 与 Codex rollout JSONL 是否一一对应；如果不对应，哪些字段会丢失。
- `thread.compact()` 的结果如何与 Bub tape anchors/default read set 对齐。
- SDK beta API 的变动风险如何隔离：`bub-codex` 可能需要自己的 adapter layer，不应让 Bub domain model 直接依赖 SDK generated types。

### 继续下钻后的修正

进一步阅读 `openai-codex` 的 Python SDK、generated protocol models 和 `codex-rs/app-server/README.md` 后，结论需要更具体：

- `TurnHandle.stream()` 确实会 yield 该 turn 的所有 routed notifications，而不是只 yield 文本 delta。`MessageRouter` 通过 generated `notification_turn_id()` 抽取 `turnId`，并把相关 notifications 路由到对应 turn queue。
- generated `notification_registry.py` 比 `models.py` 的 public `NotificationPayload` union 更完整。实际 `_coerce_notification()` 通过 `NOTIFICATION_MODELS` 解析，能识别更多通知，包括：
  - `item/started`
  - `item/completed`
  - `item/autoApprovalReview/started`
  - `item/autoApprovalReview/completed`
  - `item/fileChange/patchUpdated`
  - `turn/diff/updated`
  - `thread/tokenUsage/updated`
  - `hook/started`
  - `hook/completed`
  - `serverRequest/resolved`
- approval 不是普通 stream notification，而是 app-server 发给 client 的 server-initiated JSON-RPC request。Python SDK 底层 `CodexClient` 有 `approval_handler`，默认对 `item/commandExecution/requestApproval` 和 `item/fileChange/requestApproval` 返回 `{ "decision": "accept" }`。但当前项目假设 v0 不设计 approval flow，而是以最大权限运行，尽量避免进入审批分支。
- `Codex` / `AsyncCodex` 高层 wrapper 目前没有把 `approval_handler` 作为 public constructor 参数暴露出来。若 Bub 要接管 approval，需要：
  - 修改/包装 SDK；
  - 或直接使用底层 `CodexClient` / `AsyncCodexClient`；
  - 或等待 SDK public API 暴露 approval handler。
- app-server approval flow 是明确的：
  - command approval: `item/started` -> `item/commandExecution/requestApproval` request -> client decision -> `serverRequest/resolved` -> `item/completed`
  - file change approval: `item/started` -> `item/fileChange/requestApproval` request -> client decision -> `serverRequest/resolved` -> `item/completed`
- command approval request 可包含 `command`、`cwd`、`commandActions`、`reason`、`additionalPermissions`、`availableDecisions`、policy amendment hints。
- file change approval request 可包含 `itemId`、`threadId`、`turnId`、`reason`，以及不稳定的 `grantRoot`。
- permission requests 也是 server request：`item/permissions/requestApproval`。这对 Bub sandbox/permission profile 映射很重要，但 Python SDK 默认 approval handler 当前只特殊处理 command/file change 两种 request。
- `ThreadItem` union 已包含更完整的 completed facts：
  - `fileChange`
  - `collabAgentToolCall`
  - `webSearch`
  - `commandExecution`
  - `mcpToolCall`
  - `dynamicToolCall`
  - `contextCompaction`
- `FileChangeThreadItem` 包含 `changes` 和 `status`；每个 `FileUpdateChange` 有 `path`、`kind`、`diff`。这足以作为 Bub file edit fact 的核心 payload。
- `CollabAgentToolCallThreadItem` 包含 `senderThreadId`、`receiverThreadIds`、`tool`、`status`、`prompt`、`model`、`reasoningEffort`、`agentsStates`。这足以保存 subagent spawn/send/resume/wait/close 的 lineage。
- app-server protocol 支持 experimental `dynamicTools` 和 `item/tool/call` server request。这个机制可以让 Codex 调用 client-provided tools，并通过 `dynamicToolCall` item 记录生命周期。
- 但 Python SDK generated `ThreadStartParams` 当前没有暴露 `dynamicTools` 字段，`api.py` 高层 wrapper 也无法直接注册 dynamic tools。若 Bub 要把 Bub tools 原生暴露给 Codex，可能需要下探 app-server JSON-RPC raw API 或等待 SDK 更新。
- app-server 已支持 skills native input：在 turn input 中同时传 `$<skill-name>` 文本和 `skill` input item，可以让 backend 注入完整 skill instructions。也支持 `skills/list`、`skills/extraRoots/set` 和 `skills/changed`。这比现有 `bub-contrib` 的 `.agents/skills` 临时 symlink 更接近正确边界。

新的设计判断：

- **主路径**：`AsyncCodex` / app-server JSON-RPC + `TurnHandle.stream()`，而不是 `codex e` 或 rollout parser。
- **permission 主路径**：v0 使用最大权限，不实现 approval UX/policy engine。`approval_handler` 只作为后续治理层预留，不进入第一版 runtime slice。
- **tool 主路径**：短期可以先只观察 Codex built-in command/MCP/fileChange items；中期应验证 `dynamicTools` 是否可通过低层 JSON-RPC 注册 Bub tools。
- **skill 主路径**：优先研究 `SkillInput` / `skills/extraRoots/set`，不要继续依赖 workspace symlink mutation。
- **subagent 主路径**：用 `collabAgentToolCall` 的 `senderThreadId` / `receiverThreadIds` 建立 Bub tape lineage；rollout parser 只作为校验或补历史。

因此，下一步最有价值的 spike 不是写完整 runtime，而是写一个最小 app-server harness：

1. 启动 `AsyncCodex` 或底层 `CodexClient`。
2. 以 `Sandbox.full_access` 创建 thread，运行一个会触发 command/file change 的 turn。
3. 消费 `TurnHandle.stream()`，把每个 notification dump 成 JSONL。
4. turn 完成后调用 `thread.read(include_turns=True)`，对比 live notifications、completed `ThreadItem`、本地 rollout JSONL 三者是否丢字段。
5. 只有当最大权限仍触发 approval request 时，才加入最小 `approval_handler` 来记录 payload 并自动返回 accept。

## 初始架构假设

第一个有价值的设计目标不是 “把 `run_model` 换成另一个 subprocess command”。在 Codex Python SDK 存在后，更合理的目标是：

1. 定义 Codex runtime event vocabulary。
2. 显式绑定 Bub `session_id`、Codex thread id、turn id 和 tape id。
3. 通过 SDK `TurnHandle.stream()` 捕获 model steps、tool calls、edits、subagents、errors、interruptions 和 final responses 的结构化事件；只有 SDK 覆盖不到时才考虑 rollout parser 作为补充。
4. 将这些事件持久化为 tape entries 或 tape-derived facts。
5. 将同一组 facts 投影到 OTel/Langfuse 等 observability backends。
6. 暴露一个小型 query surface，让 agents 可以调查过去的工作。

## 后续具体问题

- Codex Python SDK 的 notification stream 是否足以在不解析 rollout files 的情况下覆盖 turn/tool/subagent/file-change events？
- 第一个 executable slice 是否应直接实现 `run_model_stream`，将 `TurnHandle.stream()` 转成 Bub stream events 与 tape events？
- 最小 event schema 应如何表示：
  - model generation
  - tool call request
  - tool result
  - file edit
  - subagent spawn/result
  - interruption
  - final answer
- 最大权限配置下是否还能稳定触发所有需要的 command/file-change events？
- 是否可以把 `bub-contrib/packages/bub-codex` 作为 compatibility adapter，而本 repo 专注构建 native runtime boundary？

## 本地 Clone 后续探索

GitHub 仓库也已 shallow clone 到 `/tmp/bub-codex-sources/`，用于源码级探索：

```text
/tmp/bub-codex-sources/
├── bub
├── bub-contrib
├── codex-observability-plugin
└── obelisk
```

补充源码观察：

- Bub builtin `Agent` 已经使用 `TapeService` 作为 durable event layer。它会写入 `loop.start`、`loop.step.start`、`loop.step`、`command` 等 events，并使用 `session/start`、`auto_handoff/context_overflow` 等 anchors。
- Bub 的 `Agent.run_stream()` 返回 Republic `AsyncStreamEvents`；stream path 仍会追加 tape events，并用 usage/errors 更新 stream state。
- Bub 当前 tape naming 会 hash workspace path 和 session id，因此 native Codex runtime 应把 `session_id`、tape name、Codex thread id 当成相关但不同的 identities。
- `bub-contrib/packages/bub-codex/src/bub_codex/utils.py` 会通过临时 symlink Bub skills 到 `.agents/skills` 来修改 workspace。这确认了当前 skill exposure 是 filesystem-mediated，native runtime/tool boundary 需要重新设计。
- Langfuse 的 Codex parser 已经从 rollout files 中暴露出一组实用 event vocabulary：
  - line types: `session_meta`、`turn_context`、`response_item`、`event_msg`
  - response item types: `message`、`reasoning`、`function_call`、`function_call_output`、`custom_tool_call`、`custom_tool_call_output`
  - 值得关注的 event message types: `task_started`、`user_message`、`agent_message`、`token_count`、`task_complete`、`turn_aborted`、`collab_agent_spawn_end`，以及 `exec_command_end`、`patch_apply_end`、`mcp_tool_call_end` 这类 `*_end` tool lifecycle events
- Langfuse 组装后的模型包含 `SessionMeta`、`Turn`、`ModelStep`、`ToolCall`、`TokenUsage`。这很接近 Bub Codex event projection 的最小模型，但它是从 rollout JSONL 事后重建出来的。
- Langfuse trace emission 会通过 child thread ids 反查 subagent rollout files，把 subagent rollouts 嵌套到 spawning turn 下面。Bub native 版本应在 subagent spawn 时直接保存这个 parent-child edge。
- Obelisk 的 SQLite schema 将可查询历史拆成 `sessions`、`messages`、`tool_calls`、`tool_results`、`subagents`、`workflows`、`workflow_agents`。这适合作为 tape/runtime facts 之上的 query projection，而不一定是 canonical write model。

更新后的设计含义：

下一份 artifact 很可能应是一篇 ADR，用来定义 two-layer model：

1. **Canonical runtime/tape facts**：append-only Bub events，带 stable ids、parent ids、timestamps、session/tape/thread bindings 和 typed payloads。
2. **Derived projections**：Langfuse/OTel traces、Obelisk-style SQLite query tables、replay/UI timelines。

canonical layer 应足够丰富，使任何 projection 都不需要解析 terminal output、scrape rollout JSONL，或事后推断 parent-child lineage。
