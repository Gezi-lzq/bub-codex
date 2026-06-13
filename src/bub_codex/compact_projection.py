"""Codex compaction notification to tape-event mapping."""

from __future__ import annotations

from typing import Iterable

from .json_utils import JsonObject, sha256_text
from .runtime_adapter import record_event_id, record_thread_id, record_turn_id
from .tape_events import TapeEvent, make_tape_event


def project_compaction_events(
    records: Iterable[JsonObject],
    *,
    session_id: str,
    tape_id: str,
    initiator: str = "bub_runtime",
    reason: str = "user_requested",
    source: str = "sdk_stream:user_turn",
) -> list[TapeEvent]:
    """Project Codex compaction notifications into Anchors bound to the same thread."""

    events: list[TapeEvent] = []
    for record in records:
        events.extend(
            project_compaction_record(
                record,
                session_id=session_id,
                tape_id=tape_id,
                initiator=initiator,
                reason=reason,
                source=source,
            )
        )
    return events


def project_compaction_record(
    record: JsonObject,
    *,
    session_id: str,
    tape_id: str,
    initiator: str = "bub_runtime",
    reason: str = "user_requested",
    source: str = "sdk_stream:user_turn",
) -> tuple[TapeEvent, ...]:
    """Project one Codex compaction notification into continuity tape events."""

    if record.get("method") != "item/completed":
        return ()
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ()
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "contextCompaction":
        return ()

    source_id = record_event_id(record, kind="codex.thread.compacted", source=source)
    anchor_creation_id = _stable_id("anchor_creation", source_id)
    anchor_id = _stable_id("anchor", source_id)
    common = {
        "session_id": session_id,
        "tape_id": tape_id,
        "thread_id": record_thread_id(record),
        "turn_id": record_turn_id(record),
        "occurred_at": str(record.get("ts")) if record.get("ts") is not None else None,
    }

    return (
        make_tape_event(
            "bub.anchor.creation.started",
            payload={
                "anchor_creation_id": anchor_creation_id,
                "method": "compact",
                "initiator": initiator,
                "reason": reason,
                "active_thread_id_before": record_thread_id(record),
                "source_fact_id": source_id,
            },
            **common,
        ),
        make_tape_event(
            "codex.thread.compacted",
            payload={
                "anchor_creation_id": anchor_creation_id,
                "trigger": _compact_trigger(initiator, reason),
                "source_fact_id": source_id,
            },
            **common,
        ),
        make_tape_event(
            "bub.anchor.created",
            payload={
                "anchor_id": anchor_id,
                "method": "compact",
                "reason": reason,
                "created_at": common["occurred_at"],
                "state": {
                    "summary": None,
                    "summary_status": "unavailable",
                },
                "refs": {
                    "source_anchor_creation_id": anchor_creation_id,
                    "thread_id": record_thread_id(record),
                    "turn_id": record_turn_id(record),
                    "source_fact_id": source_id,
                },
                "initiator": initiator,
            },
            anchor_id=anchor_id,
            **common,
        ),
        _compact_continuity_binding_event(
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
            thread_id=record_thread_id(record),
            previous_thread_id=record_thread_id(record),
            anchor_creation_id=anchor_creation_id,
            source_fact_id=source_id,
            turn_id=record_turn_id(record),
            occurred_at=common["occurred_at"],
        ),
    )


def _compact_trigger(initiator: str, reason: str) -> str:
    if initiator == "codex_runtime" or reason == "auto_compact":
        return "auto"
    if initiator == "human":
        return "manual"
    return "bub_anchor_compact"


def _compact_continuity_binding_event(
    *,
    session_id: str,
    tape_id: str,
    anchor_id: str,
    thread_id: str | None,
    previous_thread_id: str | None,
    anchor_creation_id: str,
    source_fact_id: str,
    turn_id: str | None = None,
    occurred_at: str | None = None,
) -> TapeEvent:
    return make_tape_event(
        "codex.thread.bound",
        payload={
            "anchor_id": anchor_id,
            "thread_id": thread_id,
            "previous_thread_id": previous_thread_id,
            "reason": "compact_continuity",
            "archived_previous": False,
            "refs": {
                "source_anchor_creation_id": anchor_creation_id,
                "source_fact_id": source_fact_id,
            },
        },
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=thread_id,
        turn_id=turn_id,
    )


def _stable_id(prefix: str, value: str) -> str:
    digest = sha256_text(f"{prefix}:{value}")[:24]
    return f"{prefix}_{digest}"
