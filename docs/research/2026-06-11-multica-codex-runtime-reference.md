# Multica Codex Runtime Reference

## 背景

本次调研参考固定 commit：

```text
multica-ai/multica@8151f60c6cbe40b763145f4e01a0832af7bde92b
```

本地 checkout：

```text
/tmp/bub-codex-reference/multica
```

重点文件：

```text
server/pkg/agent/codex.go
server/internal/daemon/execenv/codex_sandbox.go
server/internal/daemon/execenv/codex_home.go
server/internal/daemon/execenv/execenv.go
server/internal/daemon/daemon.go
server/internal/daemon/client.go
```

## Multica 如何与 Codex 交互

Multica 的 Codex backend 仍然是 subprocess/app-server 模式，不是 Python SDK native embedding。

执行入口：

```text
server/pkg/agent/codex.go
  codexBackend.Execute()
```

它启动：

```text
codex app-server --listen stdio://
```

然后通过 stdin/stdout 进行 JSON-RPC 通信：

```text
initialize
initialized
thread/resume 或 thread/start
turn/start
```

stdout reader 按行读取 app-server 输出，解析 JSON-RPC response、server request 和 notification。

Multica 支持两种 Codex notification 协议：

```text
legacy:
  codex/event

raw v2:
  turn/started
  turn/completed
  thread/started
  item/started
  item/completed
  error
  thread/status/changed
```

raw v2 handler 会过滤非当前 `threadId` 的通知，避免同一 app-server pipe 上的 subagent、memory consolidation 或其他后台 thread 事件污染当前 task。

## Event 映射

Multica 将 Codex notifications 降级为统一的 `agent.Message` stream：

```text
turn/started
  -> MessageStatus(status=running, session_id=thread_id)

item/started commandExecution
  -> MessageToolUse(tool=exec_command)

item/completed commandExecution
  -> MessageToolResult(tool=exec_command)

item/started fileChange
  -> MessageToolUse(tool=patch_apply)

item/completed fileChange
  -> MessageToolResult(tool=patch_apply)

item/completed agentMessage
  -> MessageText(content=text)

turn/completed
  -> turnDone
```

`agentMessage.phase == final_answer` 会触发 turn done。这一点说明 Codex `phase` 是可用的 runtime semantic signal。

## Approval

Multica daemon 模式自动接受 Codex approval request：

```text
item/commandExecution/requestApproval -> decision=accept
item/fileChange/requestApproval       -> decision=accept
mcpServer/elicitation/request         -> action=accept
```

这与 `bub-codex` v0 最大权限方向一致，但实现层不同：

```text
Multica:
  进入 approval request 后自动 accept

bub-codex v0:
  approval_policy=never
  sandbox=danger-full-access
  尽量不进入 approval 分支
```

## 执行细节如何记录

Multica 没有把 Codex raw notifications 持久化为 canonical event log。它记录的是几类派生信息。

### Daemon logs

Codex backend 和 daemon 会记录：

```text
agent command
codex started app-server
codex turn/start sent
codex turn/completed received
tool #n
tool_result observed
semantic inactivity timeout
first-turn no-progress timeout
stderr tail
```

### Task messages

daemon drain `Session.Messages` 后批量上报：

```text
POST /api/daemon/tasks/{taskID}/messages
```

schema：

```go
type TaskMessageData struct {
    Seq     int
    Type    string
    Tool    string
    Content string
    Input   map[string]any
    Output  string
}
```

消息类型包括：

```text
text
thinking
tool_use
tool_result
error
```

tool output 会被截断到 8192 bytes。

### Session pinning

当 daemon 收到带 `SessionID` 的 `MessageStatus`，会尽早上报：

```text
POST /api/daemon/tasks/{taskID}/session
```

保存：

```text
session_id = Codex thread id
work_dir   = task workdir
```

这样 daemon crash 后仍有 resume pointer。

### Final result

完成时：

```text
POST /api/daemon/tasks/{taskID}/complete
```

失败时：

```text
POST /api/daemon/tasks/{taskID}/fail
```

上报内容包括：

```text
output / error
session_id
work_dir
failure_reason
usage
```

### Usage

Multica 优先从 Codex notification 中提取 usage。如果 JSON-RPC notification 没有 usage，则 fallback 扫描 Codex session JSONL。

这说明 Codex runtime usage 信号在不同版本/路径下可能不稳定，后续 `bub-codex` 不应假设单一来源永远可用。

## Execution Environment

Multica 的 execution environment 设计是本次调研最有参考价值的部分。

对 Codex provider，它会准备 per-task `CODEX_HOME`：

```text
envRoot/codex-home
```

并处理：

```text
auth.json       -> symlink from shared ~/.codex
sessions        -> symlink from shared ~/.codex
config.json     -> copied
config.toml     -> copied then sanitized/managed
instructions.md -> copied
plugin cache    -> exposed from shared ~/.codex
skills          -> hydrated into per-task CODEX_HOME/skills
```

它还会通过 managed config blocks 控制：

```text
sandbox
multi-agent
memory
mcp_servers
```

### Sandbox policy

`codex_sandbox.go` 根据平台和 Codex version 选择 sandbox policy：

```text
non-darwin:
  sandbox_mode = "workspace-write"
  sandbox_workspace_write.network_access = true

darwin with known fixed Codex version:
  workspace-write + network_access

darwin otherwise:
  danger-full-access
```

原因是 Codex macOS Seatbelt sandbox 曾存在 `network_access=true` 不生效的问题。

### Managed TOML block

Multica 写 `config.toml` 的方式值得直接吸收：

```text
# BEGIN multica-managed ...
...
# END multica-managed
```

关键经验：

- 幂等 upsert。
- managed block 放在文件顶部。
- 用 dotted key，例如 `sandbox_workspace_write.network_access = true`。
- 不随意追加 TOML table，避免用户文件当前处于 `[permissions.foo]` 等 table 下时产生 scope 污染。
- 需要迁移旧 inline sandbox 配置时，先 strip legacy directives。

如果 `bub-codex` 未来写 Codex `config.toml`，应采用同类策略，不能简单 append。

## 对 bub-codex 的启发

### 应该吸收

1. **CodexEnvironment Module**

   后续可引入独立 Module 管理：

   ```text
   CodexConfig / config overrides
   CODEX_HOME 是否隔离
   host env / PATH parity
   sandbox policy
   MCP / dynamic tool config 注入
   Codex native memory / multi-agent 开关
   runtime diagnostic metadata
   ```

2. **thread event filtering**

   live bridge / Translator 应明确是否接收混合 thread notifications。如果 Codex stream 可能出现非当前 thread 事件，应过滤、隔离或显式投影为后台事件。

3. **liveness diagnostics**

   后续 runtime hardening 需要区分：

   ```text
   process alive but no semantic progress
   turn started but no item
   tool in flight so silence is expected
   stream reader stuck
   ```

4. **diagnostic payload**

   失败事件应考虑记录：

   ```text
   Codex version
   stderr tail
   last semantic activity
   thread_id
   turn_id
   model
   timeout kind
   ```

5. **mid-flight session/thread persistence**

   Multica 一收到 session id 就 pin task session。`bub-codex` 当前要求 `codex.thread.bound` 在 materialization turn 成功后写入，这是正确的；未来若有长 turn crash recovery，也需要类似 mid-flight 可恢复标记，但必须保持 tape-first 语义。

### 不应该照搬

1. **不要把 `agent.Message` 作为 canonical record**

   Multica 的 `text/tool_use/tool_result/status` stream 对 UI 足够，但对 Bub tape、Anchor、Handoff、replay、eval 和 observability 不够。

   `bub-codex` 应继续坚持：

   ```text
   Codex raw notification
     -> CodexFact
     -> Bub TapeEvent
     -> UI / Stream / Observability projections
   ```

2. **不要在 v0 引入完整 per-task CODEX_HOME**

   这会显著扩大状态面，并影响 thread/session resume 可解释性。当前 MVP 继续使用 Bub plugin config + SDK config overrides 更合适。

3. **不要自动 fallback fresh thread**

   Multica 在 resume 失败时 fallback 到 `thread/start`，以保证 task 继续执行。`bub-codex` 当前决策是 resume failure 先暴露，因为 tape/Anchor/thread 语义需要可解释，不应静默换上下文。

## 结论

Multica 验证了 Codex app-server/raw notification 可以作为 runtime integration surface 使用，也展示了生产环境中需要关注的 execution environment、liveness、diagnostic 和 resume pointer 问题。

但 Multica 的记录模型是：

```text
Codex raw notifications
  -> agent.Message
  -> task messages / logs / final task result / session pointer
```

`bub-codex` 的核心价值应保持为：

```text
Codex raw notifications
  -> CodexFact
  -> Bub TapeEvent canonical log
  -> stream / UI / observability projections
```

因此，Multica 值得借鉴 runtime hardening 和 execution environment，不应借鉴其事件记录深度作为上限。
