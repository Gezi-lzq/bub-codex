from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .runtime_adapter import facts_from_notification_record
from .tape_events import JsonObject, TapeEvent
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
    _final_texts: list[str] | None = None
    _fallback_text: str | None = None

    def __post_init__(self) -> None:
        if self._final_texts is None:
            self._final_texts = []

    def accept(self, record: JsonObject) -> TranslationResult:
        facts = facts_from_notification_record(record, source=self.source)
        tape_events = project_user_turn_events(
            facts,
            session_id=self.session_id,
            tape_id=self.tape_id,
            anchor_id=self.anchor_id,
        )
        return TranslationResult(
            tape_events=tuple(tape_events),
            stream_decisions=tuple(self._stream_decisions_for(tape_events)),
        )

    def finish(self) -> TranslationResult:
        assert self._final_texts is not None
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
        assert self._final_texts is not None
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
                decisions.append(StreamDecision("text", {"delta": text}))
        return decisions


def stream_success_decisions_from_tape_events(events: tuple[TapeEvent, ...]) -> tuple[StreamDecision, ...]:
    final_texts: list[str] = []
    fallback_text = ""
    for event in events:
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
        turn_id = _last_turn_id(events)
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


def _last_turn_id(events: tuple[TapeEvent, ...]) -> str | None:
    for event in reversed(events):
        turn_id = event.payload.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            return turn_id
    return None
