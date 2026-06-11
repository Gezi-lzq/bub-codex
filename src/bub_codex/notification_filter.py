from __future__ import annotations

from typing import Any

from .tape_events import JsonObject


def record_belongs_to_thread(record: JsonObject, expected_thread_id: str) -> bool:
    thread_id = record_thread_id(record)
    return thread_id is None or thread_id == expected_thread_id


def record_thread_id(record: JsonObject) -> str | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None

    direct = _optional_str(payload.get("threadId"))
    if direct:
        return direct

    turn = payload.get("turn")
    if isinstance(turn, dict):
        return _optional_str(turn.get("threadId"))

    return None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
