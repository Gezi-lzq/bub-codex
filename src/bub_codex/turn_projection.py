"""User-turn notification to tape-event mapping."""

from __future__ import annotations

from .json_utils import JsonObject
from .runtime_adapter import (
    record_event_id,
    record_item,
    record_item_id,
    record_payload,
    record_thread_id,
    record_turn_id,
)
from .tape_events import TapeEvent, make_tape_event


def is_completed_assistant_message(record: JsonObject) -> bool:
    return record.get("method") == "item/completed" and record_item(record).get("type") == "agentMessage"


def project_assistant_message_record(
    record: JsonObject,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
    source: str,
) -> TapeEvent:
    item = record_item(record)
    payload: JsonObject = {
        "source_fact_id": record_event_id(
            record,
            kind="codex.assistant_message.completed",
            source=source,
            payload={
                "text": item.get("text"),
                "phase": item.get("phase"),
                "raw": record_payload(record),
            },
        ),
        "source_item_id": record_item_id(record),
        "assistant_text": item.get("text"),
        "phase": item.get("phase"),
    }
    return make_tape_event(
        "codex.assistant_message.completed",
        payload=payload,
        occurred_at=str(record.get("ts")) if record.get("ts") is not None else None,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=record_thread_id(record),
        turn_id=record_turn_id(record),
    )


def project_turn_lifecycle_record(
    record: JsonObject,
    event_type: str,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
    source: str,
) -> TapeEvent:
    return make_tape_event(
        event_type,
        payload={
            "purpose": "user_turn",
            "source_fact_id": record_event_id(record, kind=event_type, source=source),
        },
        occurred_at=str(record.get("ts")) if record.get("ts") is not None else None,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=record_thread_id(record),
        turn_id=record_turn_id(record),
    )


def project_codex_error_record(
    record: JsonObject,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
    source: str,
) -> TapeEvent:
    payload = record_payload(record)
    tape_payload: JsonObject = {
        "source_fact_id": record_event_id(record, kind="codex.error.observed", source=source),
        "error_type": payload.get("type"),
        "message": payload.get("message"),
        "code": payload.get("code"),
        "raw_error": payload,
    }
    return make_tape_event(
        "codex.error.observed",
        payload=tape_payload,
        occurred_at=str(record.get("ts")) if record.get("ts") is not None else None,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=record_thread_id(record),
        turn_id=record_turn_id(record),
    )
