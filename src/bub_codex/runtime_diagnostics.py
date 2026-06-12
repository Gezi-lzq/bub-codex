from __future__ import annotations

from typing import Any

from .json_utils import JsonObject
from .tape_events import TapeEvent, make_tape_event


def runtime_error_event(
    *,
    stage: str,
    exc: Exception,
    session_id: str,
    tape_id: str,
    anchor_id: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    details: JsonObject | None = None,
) -> TapeEvent:
    summary = runtime_error_summary(exc)
    payload: JsonObject = {
        "stage": stage,
        "error_type": summary["type"],
        "message": summary["message"],
    }
    if details:
        payload["details"] = _json_safe(details)

    return make_tape_event(
        "bub.runtime.error",
        payload=payload,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=thread_id,
        turn_id=turn_id,
    )


def runtime_error_summary(exc: Exception) -> JsonObject:
    return {"type": type(exc).__name__, "message": str(exc)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)
