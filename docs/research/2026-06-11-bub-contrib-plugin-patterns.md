# Bub contrib plugin 模式对照

日期：2026-06-11

## 目标

对照 `https://github.com/bubbuild/bub-contrib` 中已有插件，检查 `bub-codex` 当前实现是否符合 Bub 插件生态，并提炼后续架构演进方向。

本次对照仓库：

```text
/tmp/bub-contrib
```

重点样本：

```text
packages/bub-codex
packages/bub-kimi
packages/bub-extism
packages/bub-mcp-server
packages/bub-mcp
packages/bub-tapestore-sqlite
packages/bub-tapestore-redis
packages/bub-tapestore-sqlalchemy
packages/bub-dingtalk / bub-discord / bub-feishu
```

## Bub contrib 的通用插件形态

已有插件基本遵循同一组约定：

```text
pyproject.toml
└─ [project.entry-points.bub]
   └─ <plugin-name> = "<python_package>.plugin"

plugin.py
├─ @bub.config(name="<plugin-name>")
├─ bub.ensure_config(...)
├─ @hookimpl
└─ thin exported hook surface
```

这与 `bub-codex` 当前包形态一致：

```text
[project.entry-points."bub"]
codex = "bub_codex.plugin:create_plugin"
```

并且配置已通过：

```python
@bub.config(name="codex")
class BubCodexSettings(bub.Settings):
    ...
```

对照结论：

```text
package / entry point / config 注册方式符合 contrib 规范。
```

## 旧 bub-contrib/bub-codex 与当前 bub-codex 的差异

`bub-contrib/packages/bub-codex` 是薄的 CLI wrapper：

```text
run_model()
├─ comma command -> delegate to builtin agent
├─ read .bub-codex-threads.json
├─ command = ["codex", "e"]
├─ optional "resume <thread_id>"
├─ spawn subprocess
├─ parse stderr "session id:"
└─ return stdout
```

它的 thread state 存在 workspace 文件：

```text
.bub-codex-threads.json
```

当前 `bub-codex` 的目标不同：

```text
run_model_stream()
├─ use real openai_codex SDK / app-server
├─ resolve Anchor/thread state from Bub tape
├─ materialize/resume Codex thread
├─ consume SDK notifications
├─ project CodexFact -> TapeEvent
└─ emit Republic StreamEvent
```

因此当前实现不应回退成旧 contrib `bub-codex` 的单文件形态。旧实现证明了最小 hook shape，但它的状态模型与当前目标冲突：

```text
旧实现: session_id -> JSON file -> codex CLI resume id
当前实现: tape -> Anchor -> codex.thread.bound -> thread_id
```

## bub-kimi 的参考价值

`bub-kimi` 与旧 `bub-codex` 类似，也是 CLI runtime plugin：

```text
run_model()
├─ comma command -> builtin agent
├─ session_id -> .bub-kimi-threads.json
├─ spawn kimi CLI
├─ parse resume line
└─ return stdout or process error
```

可借鉴点：

- internal command 直接交回 `_runtime_agent`。
- error output 不吞掉，返回可读错误。
- tests 覆盖 subprocess 参数、env forwarding、resume id persistence。

不应照搬点：

- thread state 文件不应替代 Bub tape。
- `run_model` 不适合作为当前 MVP 主入口；当前需要 `run_model_stream` 来承载 live bridge。

## bub-extism 的参考价值

`bub-extism` 是 hook bridge，可以暴露：

```text
run_model
run_model_stream
provide_tape_store
provide_channels
```

其 `run_model_stream` 返回 Republic `AsyncStreamEvents`：

```text
value
└─ stream_events_from_value()
   ├─ validate event list
   ├─ StreamEvent(kind, data)
   └─ optional usage -> StreamState
```

可借鉴点：

- `run_model_stream` 是 Bub contrib 中的一等 hook，不是边缘能力。
- stream event 边界保持很窄：`kind + data + optional state.usage`。
- hook adapter 层负责转换，业务语义不泄漏到 hook registration。

对当前 `bub-codex` 的确认：

```text
只实现 run_model_stream 是合理的。
turn_translator 作为 Codex notification -> StreamDecision 的边界是合理的。
```

## tapestore 插件的参考价值

`bub-tapestore-sqlite` / `redis` / `sqlalchemy` 都是 resource provider：

```text
@hookimpl
def provide_tape_store() -> TapeStore:
    return singleton_store
```

SQLite 插件使用：

```python
@lru_cache(maxsize=1)
def _store() -> SQLiteTapeStore:
    return _build_store()
```

可借鉴点：

- singleton resource cache 是 contrib 中已有模式。
- cache 必须有明确边界和测试。
- `tape_store_from_env()` 返回 fresh store，用于测试或外部脚本；hook path 返回 singleton。

对当前 `bub-codex` 的确认：

```text
LazyRuntimeStreamService cache 是合理的。
cache key 必须包含 active Bub tape store identity，因为 Codex runtime 内部绑定了 tape adapter。
bub-codex 不应自己 provide_tape_store；tape backend 属于 tapestore plugin。
```

## bub-mcp-server 的参考价值

`bub-mcp-server` 不是 model runtime plugin，而是 channel provider：

```text
MCPServerPlugin
└─ provide_channels()
   └─ MCPServerChannel
      ├─ start()
      │  └─ start FastMCP SSE server task
      ├─ stop()
      │  └─ cancel server task
      └─ run_model tool
         └─ framework.process_inbound(ChannelMessage)
```

MCP tool 并不直接调用 model hook，而是构造 Bub inbound message：

```text
ChannelMessage(
  session_id=session_id,
  channel="mcp-server",
  chat_id=session_id,
  content=prompt,
  is_active=True,
)
```

然后：

```text
framework.process_inbound(inbound)
└─ result.model_output
```

重要原则：

```text
外部入口进入 Bub 时，应走 Bub inbound pipeline，不绕过 framework.process_inbound。
```

对 `bub-codex` 的影响：

- `bub-codex` 本身已经位于 `run_model_stream` hook 内，不能再调用 `process_inbound()`，否则会递归。
- 但 `bub-mcp-server` 的 lifecycle 值得借鉴：重资源在 `start()` 或 hook 调用期启动，`stop()` 负责取消任务。

## bub-mcp 的参考价值

`bub-mcp` 是更完整的 manager/channel 模式：

```text
MCPPlugin
├─ load_state()
│  └─ state["mcp"] = MCPChannel
├─ provide_channels()
│  └─ return [MCPChannel]
└─ register_cli_commands()
   └─ bub mcp ...
```

`MCPChannel` 维护显式状态：

```text
MCPServerState
├─ client
├─ tools
├─ connected
└─ error
```

并负责：

```text
start()
└─ bootstrap remote MCP clients
   ├─ read config
   ├─ connect
   ├─ list_tools
   └─ register Bub tools

stop()
└─ close clients
```

对 `bub-codex` 的启发：

当前：

```text
LazyRuntimeStreamService
├─ _cached_runtime
└─ _cached_key
```

2026-06-11 后续清理已将 `_cached_key` 从裸 tuple 改为类型化 cache key，并补了 close 传递边界：

```text
RuntimeCacheKey
├─ tape_store_id
├─ workspace
├─ codex_bin
├─ sdk_python_path
├─ approval_policy
├─ sandbox
├─ config_overrides
├─ env
└─ use_bub_tape_store

LazyRuntimeStreamService.close()
└─ cached runtime close()
   └─ BubCodexLiveRuntimeStreamService.close()
      └─ MaterializingCodexThreadService.close()
         └─ CodexClient.close()
```

未来仍可进一步演进成：

```text
CodexRuntimeManager
├─ get_or_start(framework, settings)
├─ close()
├─ reset()
├─ status()
└─ last_error / health
```

这会让 diagnostics、CLI commands 和 shutdown 更自然。

## 当前实现对照结论

当前 `bub-codex` 与 contrib 模式对齐的部分：

- 使用 Bub entry point。
- 使用 `@bub.config` 注册配置。
- 通过 `bub.ensure_config` 读取设置。
- 导出的 plugin entry module 保持相对薄。
- 重资源不在 module import 时启动。
- comma command 交回 builtin `_runtime_agent`。
- `run_model_stream` 作为主入口是可接受的 contrib hook 形态。
- runtime cache 作为 singleton-like resource 是合理的，但必须带 key 和测试。
- 不提供 tape store，只消费当前 active Bub tape store。

当前实现故意不同于 contrib 旧 Codex/Kimi 的部分：

- 不用 subprocess stdout/stderr 作为主集成边界。
- 不用 workspace JSON 文件保存 thread id。
- 不把 `session_id -> thread_id` 作为根语义。
- 不只返回最终 stdout；需要写 Bub tape events 和 stream events。

## 后续建议

### 1. 保持当前模块边界

不要把当前实现压回单文件 plugin。更合适的边界是：

```text
plugin.py                Bub hook / config / lifecycle
runtime.py               Bub runtime orchestration
runtime_resolution.py    tape -> Anchor/thread decision
context_materialization.py Anchor/thread binding events
codex_thread_service.py  Codex SDK thread/turn operations
runtime_adapter.py       Codex raw notification -> CodexFact
turn_projection.py       CodexFact -> TapeEvent
turn_translator.py       CodexFact/TapeEvent -> StreamDecision
republic_tape_store.py   Republic tape adapter
```

### 2. 引入显式 CodexRuntimeManager

不是立即 P0，但它是对齐 `bub-mcp` manager/channel 模式的自然演进。当前已先完成较小的规范化：typed `RuntimeCacheKey` 与 close propagation。

目标：

```text
LazyRuntimeStreamService
└─ delegate to CodexRuntimeManager
   ├─ get_or_start()
   ├─ close()
   ├─ reset()
   └─ status()
```

### 3. 设计 shutdown/close

`CodexClient/app-server` 是长期进程资源。当前跟随 Python 进程退出，对 MVP 可接受，但长期应补：

```text
CodexRuntimeManager.close()
└─ CodexClient.close()
```

当前已补内部 close 边界，但仍需要进一步确认 Bub 是否有适合 model runtime plugin 的 shutdown hook。如果没有，可以先提供显式 CLI/diagnostic reset。

### 4. 不引入 provide_channels

`bub-codex` 是 model runtime provider，不是外部消息入口。照搬 `bub-mcp-server` 的 channel provider 会让职责变混。

### 5. 不引入 provide_tape_store

Tape backend 应由 `bub-tapestore-*` 插件提供。`bub-codex` 只通过 `framework.get_tape_store()` 使用当前 active store。
