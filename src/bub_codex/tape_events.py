from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .runtime_adapter import CodexFact


JsonObject = dict[str, Any]


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


def load_facts_jsonl(lines: Iterable[str]) -> list[CodexFact]:
    facts: list[CodexFact] = []
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        facts.append(
            CodexFact(
                kind=str(record["kind"]),
                event_id=str(record["event_id"]),
                source=str(record["source"]),
                payload=_dict_or_empty(record.get("payload")),
                thread_id=_optional_str(record.get("thread_id")),
                turn_id=_optional_str(record.get("turn_id")),
                item_id=_optional_str(record.get("item_id")),
                occurred_at=_optional_str(record.get("occurred_at")),
            )
        )
    return facts


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
    body = json.dumps(
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
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
