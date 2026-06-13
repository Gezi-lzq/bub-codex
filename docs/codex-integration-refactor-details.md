# Codex Integration Refactor Details

本文记录本次围绕 Codex notification 转换边界的实际代码重构。它补充
`docs/codex-integration-organization.md`，用于说明哪些设计思想已经落到代码，哪些文档表述
被当前实现和契约约束。

## 重构目标

本次重构选择最小但真实的行为保持型切口：

- 去掉 `StreamDecision` 中间层；
- 让 notification translator 直接返回 Bub 持久输出 `TapeEvent` 和 Republic 实时输出
  `StreamEvent`；
- 让 live runner 只负责 append tape / yield stream，不再做 stream decision 到 stream event
  的二次转换；
- 保留当前 tape-first continuity、startup context wrapping、steer drain、tool trace、compaction
  和 SDK error notification 行为。

## 代码变化

已完成：

- 新增 `src/bub_codex/notification_translator.py`。
  - `BubCodexNotificationTranslator.translate()` 接收 JSON-like Codex notification record。
  - 返回 `NotificationTranslation(tape_events=..., stream_events=...)`。
  - 仍拥有 turn-local 状态：final-answer delta 去重、final text 累积、无 final-answer 时的
    fallback assistant text。
- 删除 `src/bub_codex/turn_translator.py`。
- 删除 `StreamDecision` 概念。
- 更新 `src/bub_codex/live_stream.py`，直接 yield translator 返回的 `StreamEvent`。
- 更新测试 helper 和 translator 测试，使用 `stream_events` / `stream_success_events_from_tape_events`。
- 更新 `docs/design.md` 和 `docs/integration-contracts.md`，让模块边界与当前代码一致。

保留：

- `runtime_adapter.py` 仍作为私有 notification decoder 使用。
  它现在的收益是隔离 SDK payload shape，并让现有 `turn_projection.py`、`tool_projection.py`、
  `compact_projection.py` 继续复用；代码不再定义 `CodexFact` 类型。
- `runtime_context.py` 继续拥有 tape-backed create/resume state machine。
  `live_stream.py` 不接管 Anchor creation、startup context materialization 或 thread binding。

## 文档偏差修正

`docs/codex-integration-organization.md` 的主要思想可用，但有几处需要被代码事实约束：

- Codex SDK contract 当前没有 interrupt API，所以 Codex wrapper 只记录 start/resume/turn/steer/
  notification unregister/close 这些已验证能力。
- Continuity 的 owner 是 `RuntimeContextKernel`，不是 live turn runner。runner 获取 executable
  context 后才启动 turn。
- SDK `error` notification 是 observed payload，translator 只写 `codex.error.observed` tape event。
  用户可见 `StreamEvent("error")` 和失败 `final` 来自 runtime exception 或 context unavailable。

## 行为保持点

本次重构不改变以下行为：

- first real user turn 包含 startup context；resumed turn 只发送原始 prompt；
- active Bub turn 内的新消息通过 steer 进入当前 Codex turn；
- final-answer delta stream-only，不写 tape；
- completed assistant message 写 tape；
- final-answer completed 只在同一 item 没有 stream 过 delta 时补 text stream；
- 没有 final-answer 时，`finish()` 使用最后一条 completed assistant message 作为 fallback；
- tool/file side-effect item 写 tape，不通过 Bub stream 暴露；
- context compaction 写 Anchor/thread binding continuity events；
- SDK error notification 写 `codex.error.observed`，不直接产出 stream failure；
- runtime exception 写 runtime error，并产出 `error`、`text`、失败 `final`。

## 验证

本次重构后运行：

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m py_compile src/bub_codex/*.py tests/*.py
```

两者均通过。
