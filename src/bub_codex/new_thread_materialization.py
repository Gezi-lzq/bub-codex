from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from .json_utils import JsonObject, canonical_json, dict_or_empty, sha256_text
from .tape_events import TapeEvent, make_tape_event


@dataclass(frozen=True, slots=True)
class MaterializedContextInput:
    """Anchor context selected once and reused for Codex input and tape evidence."""

    anchor: TapeEvent
    selected_refs: tuple[str, ...]
    workspace_metadata: JsonObject
    text: str


@dataclass(frozen=True, slots=True)
class ThreadBindingEvents:
    materialization_id: str
    materialized: TapeEvent
    bound: TapeEvent


@dataclass(frozen=True, slots=True)
class ThreadBindFailureEvents:
    materialization_id: str
    materialized: TapeEvent
    failed: TapeEvent


@dataclass(frozen=True, slots=True)
class AnchorCreationEvents:
    started: TapeEvent
    created: TapeEvent


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
) -> AnchorCreationEvents:
    """Create the Anchor side of an Anchor + new_thread handoff."""

    event_list = list(tape_events)
    source_refs = list(source_event_refs or [])
    previous_anchor = latest_anchor_created(event_list)
    previous_thread_id = (
        active_thread_id_for_anchor(event_list, previous_anchor.anchor_id)
        if previous_anchor
        else None
    )
    anchor_creation_id = _anchor_stable_id(
        "anchor_creation",
        {
            "session_id": session_id,
            "tape_id": tape_id,
            "method": "new_thread",
            "reason": reason,
            "intent": intent,
            "previous_anchor_id": previous_anchor.anchor_id if previous_anchor else None,
            "previous_thread_id": previous_thread_id,
            "source_event_refs": source_refs,
        },
    )
    anchor_id = _anchor_stable_id("anchor", anchor_creation_id)

    started = make_tape_event(
        "bub.anchor.creation.started",
        payload={
            "anchor_creation_id": anchor_creation_id,
            "method": "new_thread",
            "initiator": initiator,
            "reason": reason,
            "active_anchor_id_before": previous_anchor.anchor_id if previous_anchor else None,
            "active_thread_id_before": previous_thread_id,
            "intent_sha256": sha256_text(intent),
            "source_event_refs": source_refs,
        },
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        thread_id=previous_thread_id,
    )
    created = make_tape_event(
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
                "intent_sha256": sha256_text(intent),
                "source_event_refs": source_refs,
            },
            "initiator": initiator,
        },
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=previous_thread_id,
    )
    return AnchorCreationEvents(started=started, created=created)


def materialize_thread_binding_events(
    tape_events: Iterable[TapeEvent],
    *,
    session_id: str,
    tape_id: str,
    thread_id: str,
    intent: str,
    materialized_context: MaterializedContextInput,
    reason: str = "anchor_materialization",
    materialization_turn_id: str | None = None,
    occurred_at: str | None = None,
) -> ThreadBindingEvents:
    """Materialize a Codex thread from a committed Anchor and bind it to tape."""

    event_list = list(tape_events)
    anchor = materialized_context.anchor
    previous_thread_id = active_thread_id_for_anchor(event_list, anchor.anchor_id)
    materialization_id = _stable_id(
        "materialization",
        {
            "anchor_id": anchor.anchor_id,
            "thread_id": thread_id,
            "intent": intent,
            "selected_refs": list(materialized_context.selected_refs),
        },
    )

    materialized = _context_materialized_event(
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor.anchor_id,
        materialization_id=materialization_id,
        materialized_context=materialized_context,
        intent=intent,
        materialization_turn_id=materialization_turn_id,
        occurred_at=occurred_at,
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
    return ThreadBindingEvents(
        materialization_id=materialization_id,
        materialized=materialized,
        bound=bound,
    )


def materialize_thread_binding_failed_events(
    *,
    session_id: str,
    tape_id: str,
    intent: str,
    error: JsonObject,
    materialized_context: MaterializedContextInput,
    reason: str = "anchor_materialization",
    occurred_at: str | None = None,
) -> ThreadBindFailureEvents:
    """Record failed thread materialization without invalidating the Anchor."""

    anchor = materialized_context.anchor
    materialization_id = _stable_id(
        "materialization",
        {
            "anchor_id": anchor.anchor_id,
            "intent": intent,
            "selected_refs": list(materialized_context.selected_refs),
            "failure": error,
        },
    )

    materialized = _context_materialized_event(
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor.anchor_id,
        materialization_id=materialization_id,
        materialized_context=materialized_context,
        intent=intent,
        occurred_at=occurred_at,
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
    return ThreadBindFailureEvents(
        materialization_id=materialization_id,
        materialized=materialized,
        failed=failed,
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
    anchor_refs = dict_or_empty(anchor.payload.get("refs"))
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


def prepare_materialized_context(
    tape_events: Iterable[TapeEvent],
    *,
    intent: str,
    workspace_metadata: JsonObject,
    anchor_id: str | None = None,
    recent_event_limit: int = 8,
) -> MaterializedContextInput:
    event_list = list(tape_events)
    anchor = find_anchor_created(event_list, anchor_id) if anchor_id else latest_anchor_created(event_list)
    if anchor is None:
        raise ValueError("cannot materialize Codex thread without a committed Anchor")

    selected_refs = select_context_refs(event_list, anchor, recent_event_limit=recent_event_limit)
    return MaterializedContextInput(
        anchor=anchor,
        selected_refs=tuple(selected_refs),
        workspace_metadata=dict(workspace_metadata),
        text=build_initial_input(
            anchor=anchor,
            workspace_metadata=workspace_metadata,
        ),
    )


def build_initial_input(
    *,
    anchor: TapeEvent,
    workspace_metadata: JsonObject,
) -> str:
    state = dict_or_empty(anchor.payload.get("state"))
    material: JsonObject = {
        "workspace_metadata": workspace_metadata,
    }
    summary = state.get("summary")
    if isinstance(summary, str) and summary:
        material["handoff_summary"] = summary
    return json.dumps(material, ensure_ascii=False, sort_keys=True, indent=2)


def _context_materialized_event(
    *,
    session_id: str,
    tape_id: str,
    anchor_id: str,
    materialization_id: str,
    materialized_context: MaterializedContextInput,
    intent: str,
    occurred_at: str | None,
    materialization_turn_id: str | None = None,
) -> TapeEvent:
    payload: JsonObject = {
        "materialization_id": materialization_id,
        "anchor_id": anchor_id,
        "strategy": "workspace_metadata_plus_optional_handoff_summary",
        "selected_fact_refs": list(materialized_context.selected_refs),
        "input_sha256": sha256_text(materialized_context.text),
        "intent_sha256": sha256_text(intent),
        "token_estimate": _rough_token_estimate(materialized_context.text),
        "workspace_metadata": materialized_context.workspace_metadata,
    }
    if materialization_turn_id is not None:
        payload["refs"] = {"materialization_turn_id": materialization_turn_id}
    return make_tape_event(
        "bub.context.materialized",
        payload=payload,
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
    )


def _rough_token_estimate(text: str) -> int:
    return max(1, len(text) // 4)


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}_{sha256_text(canonical_json(value))[:24]}"


def _anchor_stable_id(prefix: str, value: object) -> str:
    digest = sha256_text(f"{prefix}:{canonical_json(value)}")[:24]
    return f"{prefix}_{digest}"


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
