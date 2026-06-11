from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .tape_events import JsonObject, TapeEvent, make_tape_event


def materialize_thread_binding_events(
    tape_events: Iterable[TapeEvent],
    *,
    session_id: str,
    tape_id: str,
    thread_id: str,
    intent: str,
    workspace_metadata: JsonObject | None = None,
    anchor_id: str | None = None,
    reason: str = "anchor_materialization",
    materialization_turn_id: str | None = None,
    occurred_at: str | None = None,
    recent_event_limit: int = 8,
) -> list[TapeEvent]:
    """Materialize a Codex thread from a committed Anchor and bind it to tape."""

    event_list = list(tape_events)
    anchor = find_anchor_created(event_list, anchor_id) if anchor_id else latest_anchor_created(event_list)
    if anchor is None:
        raise ValueError("cannot materialize Codex thread without a committed Anchor")

    previous_thread_id = active_thread_id_for_anchor(event_list, anchor.anchor_id)
    selected_refs = select_context_refs(event_list, anchor, recent_event_limit=recent_event_limit)
    materialization_id = _stable_id(
        "materialization",
        {
            "anchor_id": anchor.anchor_id,
            "thread_id": thread_id,
            "intent": intent,
            "selected_refs": selected_refs,
        },
    )
    materialized_input = build_initial_input(
        anchor=anchor,
        intent=intent,
        selected_refs=selected_refs,
        workspace_metadata=workspace_metadata or {},
    )

    materialized = make_tape_event(
        "bub.context.materialized",
        payload={
            "materialization_id": materialization_id,
            "anchor_id": anchor.anchor_id,
            "strategy": "anchor_state_plus_selected_tape_refs",
            "selected_fact_refs": selected_refs,
            "input_sha256": _sha256_text(materialized_input),
            "intent_sha256": _sha256_text(intent),
            "token_estimate": _rough_token_estimate(materialized_input),
            "workspace_metadata": workspace_metadata or {},
            "refs": {
                "materialization_turn_id": materialization_turn_id,
            },
        },
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor.anchor_id,
    )
    bound = make_tape_event(
        "codex.thread.bound",
        payload={
            "anchor_id": anchor.anchor_id,
            "thread_id": thread_id,
            "previous_thread_id": previous_thread_id,
            "materialization_id": materialization_id,
            "reason": reason,
            "archived_previous": False,
            "refs": {
                "materialization_turn_id": materialization_turn_id,
            },
        },
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor.anchor_id,
        thread_id=thread_id,
    )
    return [materialized, bound]


def materialize_thread_binding_failed_events(
    tape_events: Iterable[TapeEvent],
    *,
    session_id: str,
    tape_id: str,
    intent: str,
    error: JsonObject,
    workspace_metadata: JsonObject | None = None,
    anchor_id: str | None = None,
    reason: str = "anchor_materialization",
    occurred_at: str | None = None,
    recent_event_limit: int = 8,
) -> list[TapeEvent]:
    """Record failed thread materialization without invalidating the Anchor."""

    event_list = list(tape_events)
    anchor = find_anchor_created(event_list, anchor_id) if anchor_id else latest_anchor_created(event_list)
    if anchor is None:
        raise ValueError("cannot materialize Codex thread without a committed Anchor")

    selected_refs = select_context_refs(event_list, anchor, recent_event_limit=recent_event_limit)
    materialization_id = _stable_id(
        "materialization",
        {
            "anchor_id": anchor.anchor_id,
            "intent": intent,
            "selected_refs": selected_refs,
            "failure": error,
        },
    )
    materialized_input = build_initial_input(
        anchor=anchor,
        intent=intent,
        selected_refs=selected_refs,
        workspace_metadata=workspace_metadata or {},
    )

    materialized = make_tape_event(
        "bub.context.materialized",
        payload={
            "materialization_id": materialization_id,
            "anchor_id": anchor.anchor_id,
            "strategy": "anchor_state_plus_selected_tape_refs",
            "selected_fact_refs": selected_refs,
            "input_sha256": _sha256_text(materialized_input),
            "intent_sha256": _sha256_text(intent),
            "token_estimate": _rough_token_estimate(materialized_input),
            "workspace_metadata": workspace_metadata or {},
        },
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor.anchor_id,
    )
    failed = make_tape_event(
        "codex.thread.bind.failed",
        payload={
            "anchor_id": anchor.anchor_id,
            "materialization_id": materialization_id,
            "reason": reason,
            "error": error,
        },
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor.anchor_id,
    )
    return [materialized, failed]


def compact_continuity_binding_event(
    *,
    session_id: str,
    tape_id: str,
    anchor_id: str,
    thread_id: str | None,
    previous_thread_id: str | None,
    anchor_creation_id: str,
    source_fact_id: str,
    snapshot_fact_id: str | None,
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
                "snapshot_fact_id": snapshot_fact_id,
            },
        },
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=thread_id,
        turn_id=turn_id,
    )


def latest_anchor_created(events: Iterable[TapeEvent]) -> TapeEvent | None:
    anchors = [event for event in events if event.type == "bub.anchor.created" and event.anchor_id]
    return anchors[-1] if anchors else None


def find_anchor_created(events: Iterable[TapeEvent], anchor_id: str | None) -> TapeEvent | None:
    if not anchor_id:
        return None
    matches = [
        event
        for event in events
        if event.type == "bub.anchor.created" and event.anchor_id == anchor_id
    ]
    return matches[-1] if matches else None


def active_thread_id_for_anchor(events: Iterable[TapeEvent], anchor_id: str | None) -> str | None:
    if not anchor_id:
        return None
    bindings = [
        event
        for event in events
        if event.type == "codex.thread.bound" and event.anchor_id == anchor_id and event.thread_id
    ]
    return bindings[-1].thread_id if bindings else None


def select_context_refs(
    events: Iterable[TapeEvent],
    anchor: TapeEvent,
    *,
    recent_event_limit: int,
) -> list[str]:
    event_list = list(events)
    refs = [anchor.event_id]
    anchor_refs = _dict_or_empty(anchor.payload.get("refs"))
    source_event_refs = anchor_refs.get("source_event_refs")
    if isinstance(source_event_refs, list):
        refs.extend(ref for ref in source_event_refs if isinstance(ref, str))
    try:
        anchor_index = next(
            index for index, event in enumerate(event_list) if event.event_id == anchor.event_id
        )
    except StopIteration:
        return _dedupe(refs)

    tail = event_list[anchor_index + 1 :]
    refs.extend(event.event_id for event in tail[-recent_event_limit:])
    return _dedupe(refs)


def build_initial_input(
    *,
    anchor: TapeEvent,
    intent: str,
    selected_refs: list[str],
    workspace_metadata: JsonObject,
) -> str:
    state = _dict_or_empty(anchor.payload.get("state"))
    refs = _dict_or_empty(anchor.payload.get("refs"))
    material = {
        "anchor": {
            "anchor_id": anchor.anchor_id,
            "method": anchor.payload.get("method"),
            "reason": anchor.payload.get("reason"),
            "state": state,
            "refs": refs,
        },
        "selected_fact_refs": selected_refs,
        "workspace_metadata": workspace_metadata,
        "current_intent_ref": {
            "sha256": _sha256_text(intent),
        },
    }
    return json.dumps(material, ensure_ascii=False, sort_keys=True, indent=2)


def _rough_token_estimate(text: str) -> int:
    return max(1, len(text) // 4)


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{_sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True))[:24]}"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dict_or_empty(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}

