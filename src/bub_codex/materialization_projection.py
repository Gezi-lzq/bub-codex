from __future__ import annotations

from typing import Iterable

from .runtime_adapter import CodexFact
from .tape_events import JsonObject, TapeEvent, make_tape_event
from .tool_projection import project_tool_events


def project_thread_materialization_events(
    facts: Iterable[CodexFact],
    *,
    session_id: str,
    tape_id: str,
    anchor_id: str,
    materialization_id: str,
    materialization_turn_id: str | None,
) -> list[TapeEvent]:
    facts_list = list(facts)
    events: list[TapeEvent] = []

    for fact in facts_list:
        if fact.kind == "codex.turn.started":
            events.append(
                _project_turn_fact(
                    fact,
                    event_type="codex.turn.materialization.started",
                    session_id=session_id,
                    tape_id=tape_id,
                    anchor_id=anchor_id,
                    materialization_id=materialization_id,
                    materialization_turn_id=materialization_turn_id,
                )
            )
        elif fact.kind == "codex.turn.completed":
            events.append(
                _project_turn_fact(
                    fact,
                    event_type="codex.turn.materialization.completed",
                    session_id=session_id,
                    tape_id=tape_id,
                    anchor_id=anchor_id,
                    materialization_id=materialization_id,
                    materialization_turn_id=materialization_turn_id,
                )
            )

    events.extend(
        project_tool_events(
            facts_list,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
        )
    )
    return events


def _project_turn_fact(
    fact: CodexFact,
    *,
    event_type: str,
    session_id: str,
    tape_id: str,
    anchor_id: str,
    materialization_id: str,
    materialization_turn_id: str | None,
) -> TapeEvent:
    payload: JsonObject = {
        "purpose": "thread_materialization",
        "materialization_id": materialization_id,
        "materialization_turn_id": materialization_turn_id or fact.turn_id,
        "source_fact_id": fact.event_id,
    }
    return make_tape_event(
        event_type,
        payload=payload,
        occurred_at=fact.occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=fact.thread_id,
        turn_id=fact.turn_id,
    )
