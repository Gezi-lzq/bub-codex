# Codex SDK 封装与 Bub 集成组织设计

本文定义 `bub-codex` 围绕 Codex SDK 封装层与 Bub 集成点的代码组织方向。

`docs/design.md` 和 `docs/integration-contracts.md` 已经描述了当前运行契约和行为。
本文不重新定义这些契约，而是把已经存在的实现重新整理为更清晰的组件边界。
如果本文和那两份契约或当前已验证实现发生冲突，应先修正本文；不要用本文绕过已经验证过的
turn lifecycle、输入转发、stream、trace 或 continuity 行为。

## 原始目的

这次重构的目的不是改变 `bub-codex` 的行为，而是重新梳理代码组织，使 Codex SDK
封装层和 Bub 集成层的职责更清楚。

原始动机：

- 目前围绕 Codex 的实现已经覆盖主要契约，但模块命名和中间抽象容易让人误解；
- `CodexFact` 容易被理解成第三套领域模型，但实际需求只是
  `CodexNotification -> TapeEvent / StreamEvent` 的 filter-and-map；
- `StreamDecision` 曾经只是过渡结构；本次重构已让 translator 直接产出 Republic
  `StreamEvent`；
- `RuntimeEvent` 这类泛化控制信号目前没有必要，turn loop、异常和 continuity 都能由
  Codex stream 生命周期、异常处理和 tape events 表达；
- Codex SDK 本身已经有 thread、turn、turn handle、notification stream 等抽象，
  `bub-codex` 不应该重新发明一套 Codex runtime 模型；
- 但 `bub-codex` 有 custom tools、approval handling、Bub runtime context injection
  的需求，因此需要一个薄的 Codex SDK wrapper。

目标结果：

- 保留现有行为和集成契约；
- 将 Codex SDK 封装层变薄、变明确；
- 将 Bub/Codex 之间的三条集成面分开；
- 让 notification 转换逻辑表现为清晰的 filter-and-map；
- 让 tape continuity 规则继续保持 tape-first。
- 给后续重构留下可审查的所有权边界，而不是规定必须出现某个文件名或类名。

## 方法论

这次重构按现有行为反推组件，而不是先设计一套理想架构再让代码迁就它。

方法：

1. 先承认 Codex SDK 的原生抽象。
   `bub-codex` 应该直接依赖 Codex thread、turn、notification stream、steer 等能力。
   自定义封装只覆盖 Bub 集成产生的边界摩擦。

2. 再识别 Bub/Codex 的真实集成面。
   当前明确有三条线：tool 集成、Bub input 进入 Codex turn/steer、Codex notification
   转换成 Bub tape/stream。

3. 只为真实差异建抽象。
   Bub 和 Codex identity 不同，所以需要 `CodexThreadBindingResolver`。
   Codex server request 和 Bub tool context 不同，所以需要 `BubToolBridge`。
   CodexNotification 和 Bub output 不同，所以需要 `BubCodexNotificationTranslator`。

4. 不为中间步骤制造领域模型。
   如果一个数据结构既不是 Codex SDK 对象，也不是 Bub 对象，而且没有独立生命周期，
   它不应该成为公开设计语言。

5. 让 IO 边界保持单向。
   translator 不 append tape、不 yield stream、不调用 Codex、不执行 tools。runner
   才执行这些 IO。

6. 用具体 notification 样例验证设计。
   final-answer delta、assistant completed、commandExecution、contextCompaction、error
   等真实 notification 必须能直接解释成 tape/stream 结果。

7. 把“当前模块”和“目标角色”分开。
   目标角色是审查边界的语言，不要求把当前实现合并成大类。当前多个小模块只要各自拥有清楚的
   side-effect boundary，就可以保留。

## 架构评判原则

后续重构不应该先问“应该抽出什么类”，而应该先问“这里是否真的存在一个独立变化的边界”。
一个抽象是否成立，取决于它能不能稳定回答下面几类问题：

- **它保护了什么边界**：系统边界、生命周期边界、IO 边界、持久化边界、实时输出边界，
  还是只是把几行代码换了一个名字？
- **它拥有哪类变化**：协议变化、状态变化、执行策略变化、恢复策略变化、展示策略变化，
  还是把多个变化来源混在一起？
- **它服务哪个时间尺度**：当前 turn、当前 stream、长期 tape history、跨 session continuity，
  还是没有清楚的时间归属？
- **它有没有明确 consumer**：谁会读取它、依赖它、恢复它、调试它，或者基于它做决策？
- **它是否减少了认知负担**：读代码的人能更快判断 owner、输入、输出和副作用，还是需要理解
  更多中间语言？

更抽象地说，这次重构使用以下判断原则：

- **边界先于对象**：先识别系统之间的责任边界，再命名对象。对象应该是边界的表达，不是为了
  整理代码形状而产生的容器。
- **生命周期先于数据结构**：一个概念如果没有独立生命周期、状态演进或错误语义，通常不应该
  成为公开抽象。
- **所有权先于复用**：复用不是抽象成立的充分理由。抽象首先要说明谁拥有决策、谁拥有执行、
  谁拥有状态，之后才讨论能否复用。
- **事实先于事件**：外部事件只是观察到的输入；系统事实是未来恢复、审计、debug 或决策仍然
  需要的信息。持久化应该保存事实，不应该镜像所有事件。
- **消费者先于完整性**：集成层不追求完整复制外部协议，而是围绕明确 consumer 做有损转换。
  没有 consumer 的信息应留在边界之外。
- **时间尺度不能混用**：stream 是当下体验，tape 是未来可恢复历史，runtime state 是执行过程。
  一个抽象如果同时承担这些时间尺度，通常会变得含混。
- **控制面和观察面分离**：执行 tool、approval、steer、resume 属于控制面；记录 notification
  和生成输出属于观察面。观察到一个事件，不等于获得执行某个动作的授权。
- **映射不拥有副作用**：map/translate/project 这类组件只应产生值。append tape、yield stream、
  调 SDK、执行 tool 这些副作用必须由 runner/kernel/manager 这类执行者承担。
- **错误是边界的一部分**：错误不能被隐藏在“自动恢复”里。无法确认 continuity 或 resume 失败时，
  应暴露清楚的失败事实，而不是静默创造替代状态。
- **抽象深度跟随差异强度**：只有两个系统在 identity、lifecycle、IO、错误语义或消费需求上
  真的不一致时，才增加一层抽象。差异越弱，封装越薄。
- **命名要暴露变化原因**：好的名字应该说明这个组件为什么会变化，例如 thread binding、
  notification translation、tool bridge。只描述技术动作的名字容易掩盖 owner。
- **行为契约高于结构美感**：重构可以改变模块和命名，但不能改变已经验证过的 turn lifecycle、
  输入转发、流式输出、trace 记录和 continuity 行为。

落到本项目，这些原则对应为：

- Codex SDK 封装层保持薄，只处理 dynamic tools、approval、turn-scoped context binding
  和 payload 适配。
- Bub 的持久输出是 `TapeEvent`，实时输出是 `StreamEvent`，不引入第三套公开事件语言。
- CodexNotification 转换是 filter-and-map，不是 notification mirror。
- session continuity 从 tape、Anchor、`codex.thread.bound` 推导；resume 失败不自动换 thread。
- translator 只转换，不做 IO；runner 执行 append tape、yield stream、steer drain 和错误处理。
- dynamic tool server request 和 notification projection 是两条不同平面：前者执行 Bub tool，
  后者只记录已经发生的 trace。

## 集成面

`bub-codex` 和 Codex 的集成主要有三条线。

### 1. Tool 集成

Bub tools 通过 Codex dynamic tools 暴露给模型。

这条线包含：

- 将配置过的 Bub tool allowlist 转成 Codex `dynamicTools` specs；
- 在 Codex thread start 时挂载 dynamic tools；
- 处理 Codex server request，例如 `item/tool/call`；
- 为 Bub tool call 注入精确的 `(thread_id, turn_id)` runtime context；
- 处理 command/file approvals。

这条线应该集中在 `BubToolBridge` 及其 Codex SDK wrapper 连接处，不应该进入
notification 到 tape/stream 的转换逻辑。

Codex notification 中可能出现 `dynamicToolCall` item，但那只是已经发生的 tool trace；
真正的 Bub tool 执行来自 `item/tool/call` server request。不要在 notification
translator 中重新 dispatch Bub tool，也不要因为看见 tool item 就推导 runtime context。

### 2. Bub 输入进入 Codex turn

Bub 的用户输入有两种进入 Codex 的方式：

- 普通 Bub model turn：启动一个 Codex turn；
- Bub turn 正在运行时的新消息：作为 steer 输入进入当前 Codex turn。

这条线包含：

- Bub session/tape/Anchor/thread binding resolution；
- first real user turn 的 startup context wrapping；
- resumed thread 上的普通 prompt 转发；
- active turn 上的 steering buffer drain；
- 将 Bub input 转成 Codex turn input 或 steer input。

这条线应该由 `BubTurnRunner` 编排。`CodexManager` 只负责调用 Codex SDK 的
thread/turn/steer 能力。

Bub comma commands 不进入这条线。`,help`、`,tape.handoff` 等仍委托给 Bub builtin agent；
只有 handoff 这类会改变 continuity 的命令，在 Bub 有 active tape store 时由
`bub-codex` 记录 Anchor，下一轮普通 chat turn 再按 tape-first 规则创建和绑定新 Codex
thread。

### 3. Codex notification 转成 Bub 输出

Codex turn 运行过程中产出的 notifications 被转换为 Bub tape 和 stream events。

CodexNotification 的类别很宽，不应该整体镜像到 Bub：

- 有些 notification 只服务流式输出，例如 final-answer 的 assistant message delta；
- 有些 notification 是工具调用、文件修改或其他 side-effect trace；
- 有些 notification 是模型输出的完成态，可能与前面的 delta 内容重复；
- 有些 notification 是 token usage、diff、patch update、command output delta 或未知事件；
- 有些 notification 会改变 continuity，例如 context compaction。

因此 translator 的职责不是保存全部 CodexNotification，而是 **filter and map**：
只把 Bub 需要的关键 trace 写入 tape，只把用户可见的输出写入 stream。

核心关系是：

```text
CodexNotification + BubTurnContext
  -> TapeEvent[]
  -> StreamEvent[]
```

`CodexNotification` 是 Codex 侧的输入协议。`TapeEvent` 和 `StreamEvent`
是 Bub 侧的输出对象。集成层不应该把 `CodexFact` 引入为公开概念或架构核心。

这条线应该由 `BubCodexNotificationTranslator` 承担。translator 只做转换，不执行
append tape、yield stream、调用 Codex 或执行 Bub tools。

`TapeEvent` 和 `StreamEvent` 的用途不同：

- `TapeEvent` 是持久 trace：用于审计、resume、debug、handoff/Anchor continuity；
- `StreamEvent` 是 Bub stream pipeline 的实时输出：当前 `bub-codex` 只用它向用户输出
  text delta、error 和 terminal final result。

Bub stream contract 支持 `text`、`tool_call`、`tool_result`、`usage`、`error`、`final`
等 kind，但当前 `bub-codex` 只 emit：

- `text`：用户可见 assistant 文本增量；
- `error`：需要即时暴露给用户的运行失败；
- `final`：一轮输出的 terminal result。

工具调用 trace 不通过 stream 暴露；它们被记录为 `bub.tool.call.*` tape events。
assistant commentary 通常不通过 stream 暴露；它只作为 completed assistant message 写入 tape。
唯一例外是 Codex 没有产出 final-answer 时，turn 结束的 fallback 可以用最后一条 completed
assistant message 作为 terminal text，保证 Bub stream contract 仍有可消费的 `final`。

`final` 的作用不是再输出一段 delta，而是给 stream consumer 一个完整的终止快照。
Republic 的 `final` 通常携带完整 `text`、tool calls/results、usage 和 `ok` 状态。Bub
framework 主路径目前用 `text` delta 拼接 `model_output`，并把 `error` 作为运行错误通知；
channel wrappers 仍会看到 `final`，例如 CLI channel 用它收尾换行。Bub builtin agent
的多步循环会读取 `final`，判断这一轮是产出文本、继续 tool loop，还是失败。

因此在 `bub-codex` 中：

- `text` stream event 承担用户可见增量输出；
- `final` stream event 承担 turn-level 完整结果和终止状态；
- 成功时，`final.data["text"]` 应该等于最终 final-answer 文本，或没有 final-answer 时的
  fallback assistant text；
- `final.data["ok"]` 应该表达这一轮是否成功，运行异常由 runner 产出 `ok=false`；
- `final` 不应写入 tape，tape 已经由 `TapeEvent` 表达持久 trace。

## 当前实现

当前实现已经覆盖以上三条集成面。notification 转换链路现在直接从 translator 产出
持久 `TapeEvent` 和实时 `StreamEvent`：

```text
Codex SDK notification
  -> codex_thread_service JSON-like record
  -> notification_translator.NotificationTranslation
     -> TapeEvent
     -> Republic StreamEvent
```

`runtime_adapter.py` 只保留私有 notification parsing helper，作为 SDK payload shape
到 projection helpers 之间的解析细节。当前代码不再定义 `CodexFact` 类型，避免把
notification decoding 误读为第三套领域模型。

相关模块：

| 模块 | 当前职责 |
| --- | --- |
| `codex_thread_service.py` | 读取 SDK notifications，将 SDK payload model 转成 JSON-like record，并过滤 foreign thread 与其他 turn。 |
| `runtime_adapter.py` | 私有 notification record 解析 helper。 |
| `notification_translator.py` | 维护 turn-local stream 状态，返回 tape events 和 Republic stream events。 |
| `turn_projection.py` | 将 turn 与 assistant-message notification records 映射成 tape events。 |
| `tool_projection.py` | 将 Codex tool 与 file-change items 映射成 Bub tool 或 side-effect tape events。 |
| `compact_projection.py` | 将 Codex compaction notification 映射成 Bub Anchor continuity events。 |
| `live_stream.py` | 运行 live turn loop，追加 tape events，并发出 stream events。 |

其他集成面当前分布如下：

| 集成面 | 当前相关模块 |
| --- | --- |
| Codex SDK thread/turn 封装 | `codex_thread_service.py`, `codex_client.py`, `runtime_services.py` |
| Bub tool dynamic tool bridge | `bub_tools.py`, `codex_client.py`, `runtime_services.py` |
| Bub input 到 Codex turn/steer | `plugin.py`, `live_stream.py`, `runtime_context.py`, `startup_context.py` |
| Codex notification 到 tape/stream | `notification_translator.py`, `turn_projection.py`, `tool_projection.py`, `compact_projection.py`, `live_stream.py` |

`runtime_adapter.py` 只是 JSON-like notification record helper 集合，不再产出中间对象。
如果以后 projection helpers 不再需要它，可以继续折叠进 translator 的私有 parsing helpers。
`StreamDecision` 已被删除；translator 直接返回 `StreamEvent`，不再有额外的 stream 输出层。

## 目标组件

这个设计的目标不是重新定义 Codex SDK。`bub-codex` 应该尽量直接依赖 Codex
SDK 的原生抽象，例如 client、thread、turn handle、notification stream。自定义封装层
只用于承接 Bub 集成需求，尤其是 dynamic tools、approval handler、Bub tool context
绑定，以及少量 SDK/app-server payload 适配。

目标组件集合：

```text
CodexManager
  -> 产出 CodexNotification
  -> 挂载 dynamic tools / approval handler

CodexThreadBindingResolver
  -> 读取 Bub tape history / Anchor / codex.thread.bound
  -> 决定 create_anchor / materialize_thread / resume_thread

RuntimeContextKernel
  -> 执行 binding resolution
  -> append Anchor / context materialization / thread binding events
  -> resume bound Codex thread

BubTurnRunner
  -> 接收 Bub input / steer input
  -> 从 RuntimeContextKernel 获取 executable context
  -> 消费 CodexNotification
  -> 调 BubCodexNotificationTranslator
  -> append tape / yield stream

BubCodexNotificationTranslator
  -> 只做 translate/map，不做 IO

BubToolBridge
  -> Bub tools <-> Codex dynamic tools / server requests
```

### CodexManager

负责 Codex SDK 封装层。

`CodexManager` 应该是薄封装，而不是新的 runtime 抽象体系。它的输入输出应尽量贴近
Codex SDK：thread、turn、turn-scoped notification stream，以及 steer/close
这类 SDK control 操作。

职责：

- start 或 resume Codex thread；
- start Codex turn；
- 暴露 turn-scoped notification stream；
- steer 当前 active turn；
- 关闭 turn notification subscription 和 runtime resources；
- 在 thread start 时挂载 Bub custom tools 对应的 Codex dynamic tools；
- 将 Codex server requests 转发给 BubToolBridge。

它不应该知道 Bub tape、Bub Anchor，或者用户可见 stream 语义。

这层存在的理由不是为了隔离 Codex SDK 本身，而是因为 `bub-codex` 有 SDK 之外的集成职责：

- Bub tools 需要以 Codex dynamic tools 的形式暴露给模型；
- dynamic tool call 需要绑定精确的 `(thread_id, turn_id)` Bub runtime context；
- approval requests 需要统一接入 Bub/Codex 的运行策略；
- SDK notification payload 可能需要转成 JSON-like shape，供 Bub 侧 mapper 稳定消费。

因此，`CodexManager` 应该封装这些边界摩擦，但不应该把 Codex SDK 的 thread/turn
模型改造成另一套领域模型。

### CodexThreadBindingResolver

负责 Bub session/tape history 与 Codex thread id 之间的绑定解析。

Bub 和 Codex 使用不同 identity：

- Bub 使用 `session_id` 和 tape history；
- Codex 使用 `thread_id` 和 `turn_id`；
- `bub-codex` 通过 Bub Anchor 和 `codex.thread.bound` tape event 连接两者。

resolver 的输入应该是 Bub tape history，输出应该是一个明确的 binding resolution：

```python
RuntimeAction = Literal[
    "create_anchor",
    "materialize_thread",
    "resume_thread",
]

@dataclass
class CodexThreadBindingResolution:
    action: RuntimeAction
    anchor_id: str | None
    thread_id: str | None
    reason: str
```

解析规则保持 tape-first：

```text
latest Anchor has a bound Codex thread
  -> resume that Codex thread

latest Anchor has no bound Codex thread
  -> prepare startup context, create a Codex thread, and bind it

no Anchor exists
  -> create a bootstrap Anchor
  -> prepare startup context, create a Codex thread, and bind it
```

`CodexThreadBindingResolver` 只做解析，不调用 Codex SDK，也不 append tape。当前代码中，
`resolve_codex_thread_binding()` 和 `CodexThreadBindingResolver.resolve()` 承担这个
resolver 角色；`resolve_runtime_context()` 只作为兼容别名保留。

创建 bootstrap Anchor、materialize startup context、调用 Codex thread service 创建或
resume thread、写入 `codex.thread.bound`，都属于 `RuntimeContextKernel` 这一类
continuity executor 的职责。不要把这些 side effects 搬进 live stream runner，否则 batch
和 live paths 会分叉。

如果已有 bound Codex thread 无法 resume，resolver 不做 fallback。错误应该由执行
resume 的上层暴露；不要静默创建替代 thread。

### RuntimeContextKernel

负责按 binding resolution 执行 tape-backed continuity state machine。

职责：

- 读取 Bub tape history；
- 创建 bootstrap Anchor 或使用 latest Anchor；
- 准备 startup context，并记录 `bub.context.materialized`；
- 创建新 Codex thread 或 resume 旧 Codex thread；
- 写入 `codex.thread.bound` 或 `codex.thread.bind.failed`；
- resume 失败时记录 `bub.runtime.error` 并向上抛错，不静默创建替代 thread。

这个角色是 continuity side effects 的 owner。它可以调用 Codex thread service 和
TapeStore，但不运行 user turn，不消费 notification，也不产出 stream events。

### BubTurnRunner

负责一轮 Bub model turn 的编排。

职责：

- 在 turn 开始前解析 Bub session、tape id、workspace；
- 调用 `RuntimeContextKernel` 得到 executable context 或 context-unavailable error；
- 通过 `CodexManager` 启动 Codex turn；
- 将 steering messages 送入 active Codex turn；
- 将每个 Codex notification 交给 `BubCodexNotificationTranslator`；
- 追加 translator 返回的 tape events；
- 发出 translator 返回的 stream events。

runner 可以执行 append/yield 这些 turn-local IO，但不拥有 create-vs-resume 决策，也不直接
写 Anchor/thread binding 事件。

### BubCodexNotificationTranslator

负责从 Codex notifications 到 Bub 集成输出的纯映射。

translator 不执行 IO。它不 append tape，不 yield stream event，不调用 Codex，也不执行
Bub tools。

推荐接口：

```python
class BubCodexNotificationTranslator:
    def translate(
        self,
        notification: CodexNotification,
    ) -> NotificationTranslation:
        ...

    def finish(self) -> NotificationTranslation:
        ...
```

`finish` 是必要的，因为这个映射不是完全 stateless，而是 turn-local 的。translator
需要在 turn 结束时发出 terminal `final` stream event，并在 Codex 没有产出
final-answer delta 时处理 fallback text。

当前实现把 `BubTurnContext` 的字段放在 translator 构造参数中，而不是每次 `translate()`
都传入 context。这不改变所有权：这些字段只是转换所需上下文，不赋予 translator 任何 IO
能力。

```python
@dataclass
class BubTurnContext:
    session_id: str
    tape_id: str
    anchor_id: str | None
    thread_id: str
    turn_id: str | None
    cwd: str
```

```python
@dataclass
class NotificationTranslation:
    tape_events: tuple[TapeEvent, ...]
    stream_events: tuple[StreamEvent, ...]
```

当前设计不引入 `RuntimeEvent`。

原因是现有行为都能由更明确的机制表达：

- 需要持久化、审计、恢复的事实，用 `TapeEvent` 表达；
- 需要发给用户或 Bub stream pipeline 的输出，用 `StreamEvent` 表达；
- turn loop 何时结束，由 Codex turn notification stream 的生命周期表达；
- turn stream 异常，由 `BubTurnRunner` 的异常处理表达；
- compaction continuity，由 translator 返回的 Anchor/thread binding tape events 表达。

如果未来出现无法用 tape/stream/exception 表达的 runner-only 控制需求，再引入一个具体命名的
内部信号类型。不要提前引入泛化的 `RuntimeEvent`。

### BubToolBridge

负责 Codex server-request plane，包括 approvals 和 dynamic tools。

这条链路独立于 notification mapping。dynamic tool calls 通过
`item/tool/call` 这类 server request 到达，而不是通过普通 notification projection
路径到达。bridge 将配置过的 Bub tools 映射为 Codex dynamic tool specs，并使用精确的
`(thread_id, turn_id)` runtime context 分发调用。

这是 `bub-codex` 需要自定义 Codex SDK wrapper 的主要原因。普通 turn execution
应尽量保持 Codex SDK 原生；custom tools、approval handling、Bub context injection
才是 wrapper 层真正增加的能力。

## 映射规则

translator 应该保持规则显式、value based：

| Codex notification | Bub 映射 |
| --- | --- |
| `turn/started` | `TapeEvent("codex.turn.started")` |
| `turn/completed` | `TapeEvent("codex.turn.completed")` |
| `item/agentMessage/delta`, `phase=final_answer` | `StreamEvent("text")`；不写 tape |
| `item/agentMessage/delta`, commentary 或其他非 final phase | 忽略 |
| `item/completed`, `item.type=agentMessage`, commentary | `TapeEvent("codex.assistant_message.completed")`；不产生用户可见 stream event |
| `item/completed`, `item.type=agentMessage`, `phase=final_answer` | assistant-message tape event；只有同一 item 没有 stream 过 final-answer delta 时才补 stream text |
| `item/started` 或 `item/completed`, tool item types | `TapeEvent("bub.tool.call.started")`、`TapeEvent("bub.tool.call.completed")` 或 failed lifecycle variant |
| `item/started` 或 `item/completed`, `item.type=fileChange` | `TapeEvent("bub.side_effect.started")` 或 terminal side-effect event |
| `item/completed`, `item.type=contextCompaction` | compact continuity events：`bub.anchor.creation.started`、`codex.thread.compacted`、`bub.anchor.created`、`codex.thread.bound` |
| `error` | `TapeEvent("codex.error.observed")`；不因观察到 SDK error notification 本身产出 stream error |
| token usage updates、command output deltas、patch updates、diff updates、unknown notifications | 在存在明确 Bub consumer 之前不落 tape/stream |

## 具体 CodexNotification 样例

以下样例使用当前测试和实现里实际消费的 JSON-like notification shape。真实 SDK payload
进入 `codex_thread_service.py` 后会先被转成这种 `{method, payload}` 结构。

### Turn lifecycle

```json
{
  "method": "turn/started",
  "payload": {
    "threadId": "thread-1",
    "turn": {"id": "turn-1"}
  }
}
```

映射结果：

```text
TapeEvent("codex.turn.started")
StreamEvent: none
```

```json
{
  "method": "turn/completed",
  "payload": {
    "threadId": "thread-1",
    "turn": {"id": "turn-1"}
  }
}
```

映射结果：

```text
TapeEvent("codex.turn.completed")
StreamEvent: none
```

turn loop 的结束不需要额外 `RuntimeEvent` 表达。Codex turn notification stream
已经以当前 turn 的 `turn/completed` 作为结束条件。

### Final answer delta

```json
{
  "method": "item/agentMessage/delta",
  "payload": {
    "threadId": "thread-1",
    "turnId": "turn-1",
    "itemId": "message-final",
    "delta": "Hel",
    "phase": "final_answer"
  }
}
```

映射结果：

```text
TapeEvent: none
StreamEvent("text", {"delta": "Hel"})
```

这类 notification 是专门服务流式用户输出的，不写 tape。tape 只保留 completed
assistant message，避免把每个 token/delta 都变成持久 trace。

### Commentary delta

```json
{
  "method": "item/agentMessage/delta",
  "payload": {
    "threadId": "thread-1",
    "turnId": "turn-1",
    "itemId": "message-commentary",
    "delta": "I will inspect.",
    "phase": "commentary"
  }
}
```

映射结果：

```text
TapeEvent: none
StreamEvent: none
```

commentary delta 既不是用户可见输出，也不是有审计价值的完整 trace，因此忽略。
完整 commentary message 会在 completed notification 中写入 tape。

### Assistant message completed

```json
{
  "method": "item/completed",
  "payload": {
    "threadId": "thread-1",
    "turnId": "turn-1",
    "item": {
      "type": "agentMessage",
      "id": "message-commentary",
      "text": "I will inspect the workspace.",
      "phase": "commentary",
      "memoryCitation": null
    }
  }
}
```

映射结果：

```text
TapeEvent("codex.assistant_message.completed")
StreamEvent: none
```

```json
{
  "method": "item/completed",
  "payload": {
    "threadId": "thread-1",
    "turnId": "turn-1",
    "item": {
      "type": "agentMessage",
      "id": "message-final",
      "text": "Hello.",
      "phase": "final_answer",
      "memoryCitation": null
    }
  }
}
```

映射结果：

```text
TapeEvent("codex.assistant_message.completed")
StreamEvent("text", {"delta": "Hello."}) only if no final-answer delta for this item was streamed
```

这解释了为什么 translator 需要 turn-local 状态：final answer 的 delta 和 completed
message 可能表达同一段文本。delta 已经实时输出过时，completed message 只写 tape，
不重复 stream。

### Command execution item

```json
{
  "method": "item/started",
  "payload": {
    "threadId": "thread-1",
    "turnId": "turn-1",
    "item": {
      "type": "commandExecution",
      "id": "command-1",
      "command": "pwd",
      "cwd": "/workspace",
      "status": "inProgress",
      "source": "model",
      "commandActions": [{"type": "unknown", "command": "pwd"}],
      "aggregatedOutput": null,
      "exitCode": null,
      "durationMs": null
    }
  }
}
```

映射结果：

```text
TapeEvent("bub.tool.call.started")
StreamEvent: none
```

```json
{
  "method": "item/completed",
  "payload": {
    "threadId": "thread-1",
    "turnId": "turn-1",
    "item": {
      "type": "commandExecution",
      "id": "command-1",
      "command": "pwd",
      "cwd": "/workspace",
      "status": "completed",
      "source": "model",
      "commandActions": [{"type": "unknown", "command": "pwd"}],
      "aggregatedOutput": "/workspace\n",
      "exitCode": 0,
      "durationMs": 1
    }
  }
}
```

映射结果：

```text
TapeEvent("bub.tool.call.completed")
StreamEvent: none
```

工具调用是关键 trace，应该进入 tape。当前 `bub-codex` 不把工具调用通过
`StreamEvent("tool_call")` / `StreamEvent("tool_result")` 暴露给 Bub stream pipeline。

### Context compaction item

```json
{
  "method": "item/completed",
  "payload": {
    "threadId": "thread-1",
    "turnId": "turn-1",
    "item": {
      "type": "contextCompaction",
      "id": "compact-1",
      "status": "completed"
    }
  }
}
```

映射结果：

```text
TapeEvent("bub.anchor.creation.started")
TapeEvent("codex.thread.compacted")
TapeEvent("bub.anchor.created")
TapeEvent("codex.thread.bound")
StreamEvent: none
```

compaction 是 continuity 事件。它不产生用户可见 stream，但会改变后续 resume
时选择的 active Anchor/thread binding。

### Error notification

```json
{
  "method": "error",
  "payload": {
    "threadId": "thread-1",
    "turnId": "turn-1",
    "type": "RuntimeError",
    "message": "codex failed",
    "code": "internal_error"
  }
}
```

映射结果：

```text
TapeEvent("codex.error.observed")
StreamEvent: none from translator
```

观察到 SDK `error` notification 只表示 Codex payload 中出现错误事实；translator 记录
`codex.error.observed`，不把它升级为用户可见失败。当前 live path 只有在 runtime exception、
resume failure 或 context unavailable 这类不可继续情况中，才 append `bub.runtime.error`
或相关失败事件，并输出 `error`、`text`、失败 `final` stream events。

### Filtered notifications

以下类型当前不会进入 tape，也不会产生 stream，除非未来有明确 Bub consumer。部分类型会先被
当前 projection 会丢弃这些 notification：

```text
thread/tokenUsage/updated
item/commandExecution/outputDelta
item/fileChange/patchUpdated
turn/diff/updated
unknown methods
```

它们不是无意义，而是当前没有被 Bub 用来做 resume、audit、handoff continuity 或用户可见
输出。保留这些事件会让 tape 退化成 Codex notification mirror。

## Turn-Local 状态

translator 只拥有保证 stream 语义正确所需的 turn-local 状态：

- 已经 stream 过 final-answer delta 的 item ids；
- 用于 final stream event 的 final-answer completed texts；
- Codex 没有 final answer 时使用的 fallback assistant text；
- `finish` 所需的 terminal final text。

这些状态不能变成持久 runtime state。持久化应该通过 tape events 完成。

## 设计规则

- 不要在核心接口中暴露 `CodexFact` 或同义的事实模型；notification decoding 只能作为私有实现细节。
- 不要让 `BubCodexNotificationTranslator` 执行 IO。
- 不要让 `CodexManager` 产出 Bub 对象。
- 不要让 Bub tape 或 Anchor 逻辑泄漏进 Codex control plane。
- dynamic tools 和 approval handling 放在 `BubToolBridge` 中，不放进 notification translator。
- 尽可能在 mapping 前过滤 foreign thread 或 foreign turn notifications。
- projection 保持 value based。只有存在真实 Bub consumer 时，translator 才应该产出 Bub 输出。
- 不要把 observed SDK error notification 直接当作 stream failure；stream failure 来自 runner
  捕获的 runtime exception 或 context unavailable。

## 本次重构结果

本次代码重构已经完成的部分：

1. 新增 `notification_translator.py`，让目标角色在代码中有明确 owner。
2. 删除 `turn_translator.py` 和 `StreamDecision`，translator 直接返回 Republic `StreamEvent`。
3. `live_stream.py` 只执行 append tape / yield stream，不再把中间 decision 转成 stream event。
4. `runtime_adapter.py` 仅保留 notification record helper，不再定义或返回中间 notification 模型。
5. 保留 final-answer deltas 与 completed assistant messages 之间的 turn-local 去重行为。

仍可后续处理但不应混入本次重构的方向：

1. 如果 record helper 不再提供足够收益，可以把 `runtime_adapter` 和 projection helpers
   继续折叠进 `notification_translator.py` 私有函数。
2. `RuntimeContextResolution` 已作为兼容别名保留；新代码应使用 `CodexThreadBindingResolution`
   和 `CodexThreadBindingResolver`，且不改变
   `runtime_context.py` 对 continuity decision/execution 的所有权。
3. 可以进一步把 `codex_thread_service.py` / `codex_client.py` 的命名对齐为薄 Codex manager
   边界，但不能新增 SDK contract 未验证的 control 操作。

期望的公开叙述应该很简单：

```text
CodexManager 产出 CodexNotification。
RuntimeContextKernel 负责 tape-backed continuity。
BubTurnRunner 将 CodexNotification 交给 BubCodexNotificationTranslator。
BubCodexNotificationTranslator 返回 TapeEvent 和 StreamEvent。
BubTurnRunner 执行对应 IO。
```
