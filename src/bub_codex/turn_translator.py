from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from .json_utils import JsonObject
from .runtime_adapter import facts_from_notification_record
from .tape_events import TapeEvent
from .turn_projection import project_user_turn_events


StreamDecisionKind = Literal["text", "final", "error"]


@dataclass(frozen=True, slots=True)
class StreamDecision:
    kind: StreamDecisionKind
    data: JsonObject


@dataclass(frozen=True, slots=True)
class TranslationResult:
    tape_events: tuple[TapeEvent, ...]
    stream_decisions: tuple[StreamDecision, ...] = ()


@dataclass(slots=True)
class CodexTurnTranslator:
    session_id: str
    tape_id: str
    anchor_id: str | None
    source: str = "sdk_stream:user_turn"
    _final_texts: list[str] = field(default_factory=list)
    _fallback_text: str | None = None
    _streamed_final_delta_item_ids: set[str] = field(default_factory=set)

    def accept(self, record: JsonObject) -> TranslationResult:
        facts = facts_from_notification_record(record, source=self.source)
        stream_decisions: list[StreamDecision] = []
        for fact in facts:
            if fact.kind != "codex.assistant_message.delta":
                continue
            delta = fact.payload.get("delta")
            if not isinstance(delta, str) or not delta:
                continue
            if fact.payload.get("phase") == "final_answer":
                if fact.item_id:
                    self._streamed_final_delta_item_ids.add(fact.item_id)
                stream_decisions.append(StreamDecision("text", {"delta": delta}))
        tape_events = project_user_turn_events(
            [fact for fact in facts if fact.kind != "codex.assistant_message.delta"],
            session_id=self.session_id,
            tape_id=self.tape_id,
            anchor_id=self.anchor_id,
        )
        stream_decisions.extend(self._stream_decisions_for(tape_events))
        return TranslationResult(
            tape_events=tuple(tape_events),
            stream_decisions=tuple(stream_decisions),
        )

    def finish(self) -> TranslationResult:
        text = "\n".join(self._final_texts) if self._final_texts else self._fallback_text or ""
        decisions: tuple[StreamDecision, ...]
        if text and not self._final_texts:
            decisions = (
                StreamDecision("text", {"delta": text}),
                StreamDecision("final", {"text": text, "ok": True}),
            )
        else:
            decisions = (StreamDecision("final", {"text": text, "ok": True}),)
        return TranslationResult(tape_events=(), stream_decisions=decisions)

    def _stream_decisions_for(self, events: list[TapeEvent]) -> list[StreamDecision]:
        decisions: list[StreamDecision] = []
        for event in events:
            if event.type != "codex.assistant_message.completed":
                continue
            text = event.payload.get("assistant_text")
            if not isinstance(text, str) or not text:
                continue
            self._fallback_text = text
            if event.payload.get("phase") == "final_answer":
                self._final_texts.append(text)
                source_item_id = event.payload.get("source_item_id")
                if not isinstance(source_item_id, str) or source_item_id not in self._streamed_final_delta_item_ids:
                    decisions.append(StreamDecision("text", {"delta": text}))
        return decisions


def stream_success_decisions_from_tape_events(events: Iterable[TapeEvent]) -> tuple[StreamDecision, ...]:
    event_list = list(events)
    final_texts: list[str] = []
    fallback_text = ""
    for event in event_list:
        if event.type != "codex.assistant_message.completed":
            continue
        text = event.payload.get("assistant_text")
        if not isinstance(text, str) or not text:
            continue
        fallback_text = text
        if event.payload.get("phase") == "final_answer":
            final_texts.append(text)

    text = "\n".join(final_texts) if final_texts else fallback_text
    if not text:
        turn_id = _last_turn_id(event_list)
        text = f"codex turn completed: {turn_id}" if turn_id else "codex turn completed"
    return (
        StreamDecision("text", {"delta": text}),
        StreamDecision("final", {"text": text, "ok": True}),
    )


def stream_error_decisions(exc: Exception) -> tuple[StreamDecision, ...]:
    text = f"{type(exc).__name__}: {exc}"
    return (
        StreamDecision("error", {"kind": "unknown", "message": str(exc)}),
        StreamDecision("text", {"delta": text}),
        StreamDecision("final", {"text": text, "ok": False}),
    )


def _last_turn_id(events: list[TapeEvent]) -> str | None:
    for event in reversed(events):
        if event.turn_id:
            return event.turn_id
    return None
