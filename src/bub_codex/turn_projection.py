"""User-turn tape projection boundary.

This module maps normalized Codex facts from one user turn into Bub tape events.
It delegates specialized tool and compaction facts to their projection modules.
"""

from __future__ import annotations

from typing import Iterable

from .runtime_adapter import CodexFact
from .compact_projection import project_compaction_events
from .json_utils import JsonObject
from .tape_events import TapeEvent, make_tape_event
from .tool_projection import project_tool_event


def project_user_turn_events(
    facts: Iterable[CodexFact],
    *,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
) -> list[TapeEvent]:
    events: list[TapeEvent] = []
    for fact in facts:
        if fact.kind == "codex.turn.started":
            events.append(_project_turn_fact(fact, "codex.turn.started", session_id, tape_id, anchor_id))
        elif tool_event := project_tool_event(
            fact,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
        ):
            events.append(tool_event)
        elif fact.kind == "codex.assistant_message.completed":
            events.append(_project_assistant_message_fact(fact, session_id, tape_id, anchor_id))
        elif fact.kind == "codex.thread.compacted":
            events.extend(
                project_compaction_events(
                    [fact],
                    session_id=session_id,
                    tape_id=tape_id,
                    initiator="codex_runtime",
                    reason="auto_compact",
                )
            )
        elif fact.kind == "codex.error.observed":
            events.append(_project_codex_error_fact(fact, session_id, tape_id, anchor_id))
        elif fact.kind == "codex.turn.completed":
            events.append(_project_turn_fact(fact, "codex.turn.completed", session_id, tape_id, anchor_id))
    return events


def _project_assistant_message_fact(
    fact: CodexFact,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
) -> TapeEvent:
    payload: JsonObject = {
        "source_fact_id": fact.event_id,
        "source_item_id": fact.item_id,
        "assistant_text": fact.payload.get("text"),
        "phase": fact.payload.get("phase"),
    }
    return make_tape_event(
        "codex.assistant_message.completed",
        payload=payload,
        occurred_at=fact.occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=fact.thread_id,
        turn_id=fact.turn_id,
    )


def _project_turn_fact(
    fact: CodexFact,
    event_type: str,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
) -> TapeEvent:
    return make_tape_event(
        event_type,
        payload={
            "purpose": "user_turn",
            "source_fact_id": fact.event_id,
        },
        occurred_at=fact.occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=fact.thread_id,
        turn_id=fact.turn_id,
    )


def _project_codex_error_fact(
    fact: CodexFact,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
) -> TapeEvent:
    payload: JsonObject = {
        "source_fact_id": fact.event_id,
        "error_type": fact.payload.get("type"),
        "message": fact.payload.get("message"),
        "code": fact.payload.get("code"),
    }
    return make_tape_event(
        "codex.error.observed",
        payload=payload,
        occurred_at=fact.occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=fact.thread_id,
        turn_id=fact.turn_id,
    )
