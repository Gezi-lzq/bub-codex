# Bub builtin agent 与 plugin lifecycle 调研

日期：2026-06-11

## 目标

结合 Bub 源码、README/build docs 指向和 builtin agent 实现，解释真实 Bub CLI 模式下 `bub-codex` 为什么没有持久化 tape events，并给出符合 Bub 设计的修复方向。

调研来源：

- `/tmp/bubbuild-bub` clone of `https://github.com/bubbuild/bub`
- 当前环境安装包：
  - `bub 0.3.8`
  - `republic 0.5.8`
- Bub README 的 design statement：
  - hook-first runtime
  - builtins are included but replaceable
  - external plugins load after builtins
  - later plugins take precedence at runtime
  - tape context is rebuilt from append-only records

## Bub runtime lifecycle

Bub CLI bootstrap：

```text
python -m bub
  -> create_cli_app()
  -> framework = BubFramework()
  -> framework.load_hooks()
  -> app = framework.create_cli_app()
```

`BubFramework.load_hooks()` 顺序：

```text
_load_builtin_hooks()
for entry_point in importlib.metadata.entry_points(group="bub"):
  plugin = entry_point.load()
  if callable(plugin):
    plugin = plugin(framework)
  plugin_manager.register(plugin, name=entry_point.name)
```

因此 Bub plugin factory 确实会在 CLI command 执行前运行。

但 Bub tape store 生命周期不在 `load_hooks()` 阶段建立。真实 tape store 在 `framework.running()` 中进入：

```text
async with framework.running():
  tape_store = hook.provide_tape_store()
  framework._tape_store = tape_store
  ... process inbound ...
  framework._tape_store = None
```

`bub run` 的实现正是：

```text
async with framework.running():
  return await framework.process_inbound(inbound)
```

Bub 自己的 `test_run_command_processes_inbound_inside_framework_runtime` 验证了：

```text
run_model hook 执行时 framework.get_tape_store() is tape_store
provide_tape_store enter_count == 1
provide_tape_store exit_count == 1
```

这说明：插件可以在 `run_model` / `run_model_stream` 调用期读取 `framework.get_tape_store()`，但不能在 plugin factory 初始化期读取。

## Hook precedence

Bub README 说明：

```text
Builtins are registered first. External plugins load after them.
At runtime, later plugins take precedence.
```

`HookRuntime` 实现上通过 reversed plugin order 选择 hook：

```text
run_model_stream:
  for plugin in reversed(plugin_manager.list_name_plugin()):
    if hasattr(plugin, "run_model_stream"):
      return call_first("run_model_stream", ...)
    elif hasattr(plugin, "run_model"):
      fallback run_model -> stream
```

因此 `bub-codex` 只实现 `run_model_stream` 是符合 Bub runtime 设计的；它会覆盖 builtin agent 的 model execution，同时仍可保留 builtin 的其他 hook：

- `load_state`
- `build_prompt`
- `provide_tape_store`
- `render_outbound`
- `dispatch_outbound`
- `register_cli_commands`

这正是当前 `bub-codex` 的插件边界。

## Builtin Agent 的 tape store 模式

Builtin agent 没有在插件初始化时固定 tape store。

`BuiltinImpl.load_state()` 将 `_runtime_agent` 放入 state：

```text
state = {
  "session_id": session_id,
  "_runtime_agent": self._get_agent(),
}
```

`Agent` 持有 framework。

`Agent.tapes` 是 `cached_property`，第一次实际使用时才读：

```text
tape_store = self.framework.get_tape_store()
if tape_store is None:
  tape_store = InMemoryTapeStore()
tape_store = ForkTapeStore(tape_store)
llm = _build_llm(settings, tape_store, self.framework.build_tape_context())
return TapeService(llm, bub.home / "tapes", tape_store)
```

因为 builtin agent 的 `run()` / `run_stream()` 在 `framework.running()` 内执行，所以这个 lazy property 通常能拿到真实 `FileTapeStore`。

同时 builtin agent 用 `ForkTapeStore` 包住 parent store：

```text
async with self.tapes.fork_tape(tape.name, merge_back=merge_back):
  ...
```

turn 内 append 到 forked in-memory store，turn 完成后 merge back 到 parent FileTapeStore。这解释了 Bub 的 tape 写入不是直接在每个 hook 阶段刷盘，而是 turn-scoped merge。

## bub-codex 当前问题

`bub-codex.create_plugin(framework)` 当前做了太多事：

```text
create_plugin(framework)
  -> load_settings()
  -> build_runtime_stream_service(framework, settings)
  -> build_runtime_stream_service:
       workspace = settings.workspace or framework.workspace
       CodexClient.start()
       CodexClient.initialize()
       tape_store = _runtime_tape_store(framework, settings)
       runtime = BubCodexRuntime(tape_store, codex_threads)
       return BubCodexLiveRuntimeStreamService(...)
```

问题点：

1. plugin factory 在 `framework.running()` 之前执行。
2. 此时 `framework.get_tape_store()` 是 `None`。
3. `_runtime_tape_store()` 退回 `InMemoryTapeStore`。
4. `BubCodexRuntime` 固定持有这个 in-memory store。
5. 后续真实 `bub run/chat` 即使进入 `framework.running()`，runtime 也不会重新绑定 Bub `FileTapeStore`。

这与真实 CLI research 完全吻合：

```text
bub run/chat 任务成功
workspace 文件副作用成功
但 ~/.bub/tapes/*.jsonl 无变化
```

## 与 Bub 文档/设计的关系

Bub 的“builtins are replaceable”并不意味着插件应复制 builtin agent 的初始化方式；更准确的模式是：

- plugin factory 只建立轻量 hook object。
- runtime turn 资源在 hook 调用期绑定。
- `framework.running()` 内的 resources，比如 tape store，必须在 hook 调用期读取。

Bub 的“same runtime across CLI/Telegram/custom channels”也意味着 `bub-codex` 不能针对 CLI 做特殊路径；修复应落在 plugin/runtime lifecycle，而不是 CLI harness。

## 修复设计建议

### 方案 A：Lazy runtime service

让 `BubCodexPlugin` 持有：

```text
framework
settings
runtime_service: optional cached service
```

`create_plugin(framework)`：

```text
settings = load_settings()
return BubCodexPlugin(framework=framework, settings=settings)
```

`BubCodexPlugin.run_model_stream(...)`：

```text
if not settings.enabled:
  return unconfigured/disabled stream
service = self._runtime_service()
return await service.run_stream(...)
```

`_runtime_service()` 在第一次 turn 时构造 live runtime service。此时 `framework.running()` 已经设置 `framework.get_tape_store()`，因此 `RepublicTapeStoreAdapter(FileTapeStore)` 可用。

风险：

- CLI `run` 每次都是新进程，service cache 只在单进程内有效。
- `bub chat` / gateway 长进程可复用 CodexClient。
- 需要明确 shutdown；Bub plugin 当前没有 lifecycle hook，因此 CodexClient 可能仍需跟随进程退出。

### 方案 B：每 turn 重新绑定 TapeStore

保留 CodexClient / CodexThreadService cache，但每次 `run_model_stream` 都用当前 `framework.get_tape_store()` 构造新的 `BubCodexRuntime` 和 live service：

```text
codex_threads = cached
tape_store = current framework tape store or InMemory fallback
runtime = BubCodexRuntime(tape_store, codex_threads)
live = BubCodexLiveRuntimeStreamService(runtime, codex_threads)
```

优点：

- 每轮都能使用当前 active tape store。
- 避免 stale store。

缺点：

- runtime service 不再是稳定对象。
- CodexClient lifecycle 仍需单独管理。

### 方案 C：Builtin-style runtime facade

仿照 builtin `Agent`：

```text
class BubCodexPlugin:
  def __init__(framework):
    self.framework = framework
    self.settings = load_settings()
    self.codex_threads = cached_property(...)

  def tape_store(self):
    store = framework.get_tape_store()
    if store is None:
      return InMemoryTapeStore()
    return RepublicTapeStoreAdapter(store)
```

在 `run_model_stream` 中组合：

```text
runtime = BubCodexRuntime(self.tape_store(), self.codex_threads)
live = BubCodexLiveRuntimeStreamService(runtime, self.codex_threads)
```

这是最贴近 Bub builtin agent 的形态。

## 推荐

推荐采用方案 C。

理由：

- 与 builtin Agent lifecycle 一致。
- plugin factory 保持轻量。
- turn 调用期读取 `framework.get_tape_store()`。
- Codex thread service / client 可以作为 plugin 级 cached resource。
- `BubCodexRuntime` 保持 per-turn/lightweight facade，避免固定旧 tape store。

## 测试建议

需要补一个不依赖真实 Codex 的单元测试：

```text
create_plugin(framework)
  framework.get_tape_store() initially None
  run_model_stream called later when framework.get_tape_store() returns fake store
  assert runtime uses RepublicTapeStoreAdapter(fake_store)
```

还需要补真实 CLI smoke：

```text
scripts/spikes/real_bub_cli_research.py
```

通过标准：

```json
{
  "all_runs_succeeded": true,
  "tape_persisted_bub_codex_events": true,
  "unique_thread_ids": ["..."]
}
```

并且第二轮 `bub run --session-id same` 不应产生新的 `codex.thread.bound`。

## 结论

Bub builtin agent 的实现验证了 #9 的修复方向：`bub-codex` 应该延迟绑定 runtime resources，尤其是 tape store。当前真实 CLI 缺陷不是 Bub CLI 的问题，而是 `bub-codex` 在 plugin initialization 阶段过早构造 runtime service，错过了 `framework.running()` 提供的 FileTapeStore。
