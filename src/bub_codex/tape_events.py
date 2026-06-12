"""Internal tape event data model.

This module defines the stable event shape and deterministic event ids used by
the runtime state machine and projection modules. Storage adapters live
elsewhere.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .json_utils import JsonObject, canonical_json, sha256_text


@dataclass(frozen=True, slots=True)
class TapeEvent:
    """Minimal Bub tape-like event projected from normalized adapter facts."""

    type: str
    event_id: str
    payload: JsonObject
    occurred_at: str | None = None
    session_id: str | None = None
    tape_id: str | None = None
    anchor_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None

    def to_json(self) -> JsonObject:
        return asdict(self)


def make_tape_event(
    event_type: str,
    *,
    payload: JsonObject,
    occurred_at: str | None = None,
    session_id: str | None = None,
    tape_id: str | None = None,
    anchor_id: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> TapeEvent:
    event_id = _event_id(
        event_type,
        payload,
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=thread_id,
        turn_id=turn_id,
    )
    return TapeEvent(
        type=event_type,
        event_id=event_id,
        payload=payload,
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=thread_id,
        turn_id=turn_id,
    )


def _event_id(
    event_type: str,
    payload: JsonObject,
    *,
    occurred_at: str | None,
    session_id: str | None,
    tape_id: str | None,
    anchor_id: str | None,
    thread_id: str | None,
    turn_id: str | None,
) -> str:
    body = canonical_json(
        {
            "type": event_type,
            "payload": payload,
            "occurred_at": occurred_at,
            "session_id": session_id,
            "tape_id": tape_id,
            "anchor_id": anchor_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
        },
    )
    return sha256_text(body)[:24]
