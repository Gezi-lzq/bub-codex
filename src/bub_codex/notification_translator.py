"""Codex notification to Bub output translation.

This module owns the lossy mapping from Codex notification records to Bub tape
events and Republic stream events. It does not append tape, yield streams,
control Codex turns, or execute Bub tools.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from republic import StreamEvent

from .compact_projection import project_compaction_record
from .json_utils import JsonObject
from .runtime_adapter import record_item, record_item_id, record_payload
from .tape_events import TapeEvent
from .tool_projection import SIDE_EFFECT_ITEM_TYPES, TOOL_ITEM_TYPES, project_tool_event
from .turn_projection import (
    is_completed_assistant_message,
    project_assistant_message_record,
    project_codex_error_record,
    project_turn_lifecycle_record,
)


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
        if _is_final_answer_delta(record):
            payload = record_payload(record)
            delta = payload.get("delta")
            if not isinstance(delta, str) or not delta:
                return NotificationTranslation(tape_events=(), stream_events=())
            item_id = record_item_id(record)
            if item_id:
                self._streamed_final_delta_item_ids.add(item_id)
            return NotificationTranslation(tape_events=(), stream_events=(StreamEvent("text", {"delta": delta}),))

        if record.get("method") == "item/agentMessage/delta":
            return NotificationTranslation(tape_events=(), stream_events=())

        tape_events = self._project_tape_events(record)
        return NotificationTranslation(
            tape_events=tape_events,
            stream_events=tuple(self._stream_events_for(tape_events)),
        )

    def _project_tape_events(self, record: JsonObject) -> tuple[TapeEvent, ...]:
        method = str(record.get("method") or "")
        if method == "turn/started":
            return (self._project_turn_lifecycle(record, "codex.turn.started"),)
        if is_completed_assistant_message(record):
            return (self._project_assistant_message(record),)
        if _is_tool_or_side_effect_item(record):
            tool_event = project_tool_event(
                record,
                session_id=self.session_id,
                tape_id=self.tape_id,
                anchor_id=self.anchor_id,
                source=self.source,
            )
            return (tool_event,) if tool_event is not None else ()
        if _is_completed_context_compaction(record):
            return project_compaction_record(
                record,
                session_id=self.session_id,
                tape_id=self.tape_id,
                initiator="codex_runtime",
                reason="auto_compact",
                source=self.source,
            )
        if method == "error":
            return (self._project_codex_error(record),)
        if method == "turn/completed":
            return (self._project_turn_lifecycle(record, "codex.turn.completed"),)
        return ()

    def _project_turn_lifecycle(self, record: JsonObject, event_type: str) -> TapeEvent:
        return project_turn_lifecycle_record(
            record,
            event_type,
            self.session_id,
            self.tape_id,
            self.anchor_id,
            self.source,
        )

    def _project_assistant_message(self, record: JsonObject) -> TapeEvent:
        return project_assistant_message_record(record, self.session_id, self.tape_id, self.anchor_id, self.source)

    def _project_codex_error(self, record: JsonObject) -> TapeEvent:
        return project_codex_error_record(record, self.session_id, self.tape_id, self.anchor_id, self.source)

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

    def _stream_events_for(self, events: Iterable[TapeEvent]) -> list[StreamEvent]:
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


def _is_final_answer_delta(record: JsonObject) -> bool:
    if record.get("method") != "item/agentMessage/delta":
        return False
    return record_payload(record).get("phase") == "final_answer"


def _is_tool_or_side_effect_item(record: JsonObject) -> bool:
    if record.get("method") not in {"item/started", "item/completed"}:
        return False
    item_type = record_item(record).get("type")
    return item_type in TOOL_ITEM_TYPES or item_type in SIDE_EFFECT_ITEM_TYPES


def _is_completed_context_compaction(record: JsonObject) -> bool:
    return record.get("method") == "item/completed" and record_item(record).get("type") == "contextCompaction"
