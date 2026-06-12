"""Codex compaction tape projection boundary.

This module records compaction as a Bub continuity event and binds the compact
Anchor back to the existing Codex thread. It does not decide normal
create/resume state.
"""

from __future__ import annotations

from typing import Iterable

from .json_utils import sha256_text
from .runtime_adapter import CodexFact
from .tape_events import TapeEvent, make_tape_event


def project_compaction_events(
    facts: Iterable[CodexFact],
    *,
    session_id: str,
    tape_id: str,
    initiator: str = "bub_runtime",
    reason: str = "user_requested",
) -> list[TapeEvent]:
    """Project Codex compaction facts into a new Anchor bound to the same thread."""

    events: list[TapeEvent] = []
    for fact in facts:
        if fact.kind != "codex.thread.compacted":
            continue

        anchor_creation_id = _stable_id("anchor_creation", fact.event_id)
        anchor_id = _stable_id("anchor", fact.event_id)
        common = {
            "session_id": session_id,
            "tape_id": tape_id,
            "thread_id": fact.thread_id,
            "turn_id": fact.turn_id,
            "occurred_at": fact.occurred_at,
        }

        events.append(
            make_tape_event(
                "bub.anchor.creation.started",
                payload={
                    "anchor_creation_id": anchor_creation_id,
                    "method": "compact",
                    "initiator": initiator,
                    "reason": reason,
                    "active_thread_id_before": fact.thread_id,
                    "source_fact_id": fact.event_id,
                },
                **common,
            )
        )

        events.append(
            make_tape_event(
                "codex.thread.compacted",
                payload={
                    "anchor_creation_id": anchor_creation_id,
                    "trigger": _compact_trigger(initiator, reason),
                    "source_fact_id": fact.event_id,
                },
                **common,
            )
        )

        events.append(
            make_tape_event(
                "bub.anchor.created",
                payload={
                    "anchor_id": anchor_id,
                    "method": "compact",
                    "reason": reason,
                    "created_at": fact.occurred_at,
                    "state": {
                        "summary": None,
                        "summary_status": "unavailable",
                    },
                    "refs": {
                        "source_anchor_creation_id": anchor_creation_id,
                        "thread_id": fact.thread_id,
                        "turn_id": fact.turn_id,
                        "source_fact_id": fact.event_id,
                    },
                    "initiator": initiator,
                },
                anchor_id=anchor_id,
                **common,
            )
        )
        events.append(
            _compact_continuity_binding_event(
                session_id=session_id,
                tape_id=tape_id,
                anchor_id=anchor_id,
                thread_id=fact.thread_id,
                previous_thread_id=fact.thread_id,
                anchor_creation_id=anchor_creation_id,
                source_fact_id=fact.event_id,
                turn_id=fact.turn_id,
                occurred_at=fact.occurred_at,
            )
        )

    return events


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
