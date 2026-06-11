from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .tape_events import JsonObject, TapeEvent, make_tape_event
from .new_thread_materialization import (
    active_thread_id_for_anchor,
    build_initial_input,
    find_anchor_created,
    latest_anchor_created,
    materialize_thread_binding_events,
    materialize_thread_binding_failed_events,
    select_context_refs,
)


def load_tape_events_jsonl(lines: Iterable[str]) -> list[TapeEvent]:
    events: list[TapeEvent] = []
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        events.append(
            TapeEvent(
                type=str(record["type"]),
                event_id=str(record["event_id"]),
                payload=_dict_or_empty(record.get("payload")),
                occurred_at=_optional_str(record.get("occurred_at")),
                session_id=_optional_str(record.get("session_id")),
                tape_id=_optional_str(record.get("tape_id")),
                anchor_id=_optional_str(record.get("anchor_id")),
                thread_id=_optional_str(record.get("thread_id")),
                turn_id=_optional_str(record.get("turn_id")),
            )
        )
    return events


def create_new_thread_anchor_events(
    tape_events: Iterable[TapeEvent],
    *,
    session_id: str,
    tape_id: str,
    reason: str,
    intent: str,
    summary: str | None = None,
    source_event_refs: list[str] | None = None,
    owner: str = "human",
    initiator: str = "human",
    occurred_at: str | None = None,
) -> list[TapeEvent]:
    """Create the Anchor side of an Anchor + new_thread handoff."""

    previous_anchor = latest_anchor_created(tape_events)
    previous_thread_id = active_thread_id_for_anchor(tape_events, previous_anchor.anchor_id) if previous_anchor else None
    anchor_creation_id = _stable_id(
        "anchor_creation",
        {
            "session_id": session_id,
            "tape_id": tape_id,
            "method": "new_thread",
            "reason": reason,
            "intent": intent,
            "previous_anchor_id": previous_anchor.anchor_id if previous_anchor else None,
            "previous_thread_id": previous_thread_id,
            "source_event_refs": source_event_refs or [],
        },
    )
    anchor_id = _stable_id("anchor", anchor_creation_id)

    return [
        make_tape_event(
            "bub.anchor.creation.started",
            payload={
                "anchor_creation_id": anchor_creation_id,
                "method": "new_thread",
                "initiator": initiator,
                "reason": reason,
                "active_anchor_id_before": previous_anchor.anchor_id if previous_anchor else None,
                "active_thread_id_before": previous_thread_id,
                "intent_sha256": _sha256_text(intent),
                "source_event_refs": source_event_refs or [],
            },
            occurred_at=occurred_at,
            session_id=session_id,
            tape_id=tape_id,
            thread_id=previous_thread_id,
        ),
        make_tape_event(
            "bub.anchor.created",
            payload={
                "anchor_id": anchor_id,
                "method": "new_thread",
                "reason": reason,
                "created_at": occurred_at,
                "state": {
                    "owner": owner,
                    "summary": summary,
                    "summary_status": "ok" if summary else "unavailable",
                },
                "refs": {
                    "source_anchor_creation_id": anchor_creation_id,
                    "previous_anchor_id": previous_anchor.anchor_id if previous_anchor else None,
                    "previous_thread_id": previous_thread_id,
                    "intent_sha256": _sha256_text(intent),
                    "source_event_refs": source_event_refs or [],
                },
                "initiator": initiator,
            },
            occurred_at=occurred_at,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
            thread_id=previous_thread_id,
        ),
    ]


def select_handoff_source_refs(
    events: Iterable[TapeEvent],
    *,
    limit: int = 16,
) -> list[str]:
    candidates = [
        event
        for event in events
        if event.type
        in {
            "bub.tool.call.completed",
            "bub.tool.call.failed",
            "bub.side_effect.completed",
            "bub.side_effect.failed",
            "codex.turn.diff.updated",
        }
    ]
    return [event.event_id for event in candidates[-limit:]]


def _stable_id(prefix: str, value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{prefix}:{encoded}".encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dict_or_empty(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
