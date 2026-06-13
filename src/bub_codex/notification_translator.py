"""Codex notification to Bub output translation.

This module owns the lossy mapping from Codex notification records to Bub tape
events and Republic stream events. It does not append tape, yield streams,
control Codex turns, or execute Bub tools.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from republic import StreamEvent

from .json_utils import JsonObject
from .runtime_adapter import record_item_id, record_payload
from .tape_events import TapeEvent
from .turn_projection import project_user_turn_events


@dataclass(frozen=True, slots=True)
class NotificationTranslation:
    tape_events: tuple[TapeEvent, ...]
    stream_events: tuple[StreamEvent, ...] = ()


@dataclass(slots=True)
class BubCodexNotificationTranslator:
    session_id: str
    tape_id: str
    anchor_id: str | None
    source: str = "sdk_stream:user_turn"
    _final_texts: list[str] = field(default_factory=list)
    _fallback_text: str | None = None
    _streamed_final_delta_item_ids: set[str] = field(default_factory=set)

    def translate(self, record: JsonObject) -> NotificationTranslation:
        stream_events: list[StreamEvent] = []
        if record.get("method") == "item/agentMessage/delta":
            payload = record_payload(record)
            delta = payload.get("delta")
            if not isinstance(delta, str) or not delta:
                return NotificationTranslation(tape_events=(), stream_events=())
            if payload.get("phase") == "final_answer":
                item_id = record_item_id(record)
                if item_id:
                    self._streamed_final_delta_item_ids.add(item_id)
                stream_events.append(StreamEvent("text", {"delta": delta}))
            return NotificationTranslation(tape_events=(), stream_events=tuple(stream_events))

        tape_events = project_user_turn_events(
            [record],
            session_id=self.session_id,
            tape_id=self.tape_id,
            anchor_id=self.anchor_id,
            source=self.source,
        )
        stream_events.extend(self._stream_events_for(tape_events))
        return NotificationTranslation(
            tape_events=tuple(tape_events),
            stream_events=tuple(stream_events),
        )

    def finish(self) -> NotificationTranslation:
        text = "\n".join(self._final_texts) if self._final_texts else self._fallback_text or ""
        events: tuple[StreamEvent, ...]
        if text and not self._final_texts:
            events = (
                StreamEvent("text", {"delta": text}),
                StreamEvent("final", {"text": text, "ok": True}),
            )
        else:
            events = (StreamEvent("final", {"text": text, "ok": True}),)
        return NotificationTranslation(tape_events=(), stream_events=events)

    def _stream_events_for(self, events: list[TapeEvent]) -> list[StreamEvent]:
        stream_events: list[StreamEvent] = []
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
                    stream_events.append(StreamEvent("text", {"delta": text}))
        return stream_events


def stream_success_events_from_tape_events(events: Iterable[TapeEvent]) -> tuple[StreamEvent, ...]:
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
        StreamEvent("text", {"delta": text}),
        StreamEvent("final", {"text": text, "ok": True}),
    )


def stream_error_events(exc: Exception) -> tuple[StreamEvent, ...]:
    text = f"{type(exc).__name__}: {exc}"
    return (
        StreamEvent("error", {"kind": "unknown", "message": str(exc)}),
        StreamEvent("text", {"delta": text}),
        StreamEvent("final", {"text": text, "ok": False}),
    )


def _last_turn_id(events: list[TapeEvent]) -> str | None:
    for event in reversed(events):
        if event.turn_id:
            return event.turn_id
    return None
