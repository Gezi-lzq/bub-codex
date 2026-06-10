from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .tape_events import JsonObject, TapeEvent, make_tape_event


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
            "input_preview": materialized_input[:800],
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
            "input_preview": materialized_input[:800],
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
        "current_intent": intent,
    }
    return json.dumps(material, ensure_ascii=False, sort_keys=True, indent=2)


def _rough_token_estimate(text: str) -> int:
    return max(1, len(text) // 4)


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
