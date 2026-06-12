"""Codex notification decoding boundary.

This module converts raw Codex SDK notification records into normalized facts.
It should not choose Bub tape event shapes or emit user-visible stream output.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable

from .json_utils import JsonObject, canonical_json, dict_or_empty, optional_str, sha256_text


@dataclass(frozen=True, slots=True)
class CodexFact:
    """Normalized fact emitted by the Codex runtime adapter.

    This is intentionally smaller than the tape schema. It keeps Codex SDK
    notification payloads out of Bub domain projections.
    """

    kind: str
    event_id: str
    source: str
    payload: JsonObject
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    occurred_at: str | None = None


@dataclass(frozen=True, slots=True)
class _NotificationContext:
    method: str
    payload: JsonObject
    source: str
    occurred_at: str | None
    thread_id: str | None
    turn_id: str | None


_NotificationHandler = Callable[[_NotificationContext], list[CodexFact]]


def facts_from_notification_record(
    record: JsonObject,
    *,
    source: str = "sdk_stream",
) -> list[CodexFact]:
    payload = dict_or_empty(record.get("payload"))
    context = _NotificationContext(
        method=str(record.get("method") or ""),
        payload=payload,
        source=source,
        occurred_at=optional_str(record.get("ts")),
        thread_id=_extract_thread_id(payload),
        turn_id=_extract_turn_id(payload) or optional_str(record.get("turn_id")),
    )
    handler = _NOTIFICATION_HANDLERS.get(context.method, _notification_observed)
    return handler(context)


def facts_from_notification_records(
    records: Iterable[JsonObject],
    *,
    source: str,
    turn_id: str | None,
) -> list[CodexFact]:
    facts: list[CodexFact] = []
    for record in records:
        facts.extend(
            facts_from_notification_record(
                _record_with_turn_id(record, turn_id),
                source=source,
            )
        )
    return facts


def record_belongs_to_thread(record: JsonObject, expected_thread_id: str) -> bool:
    thread_id = record_thread_id(record)
    return thread_id is None or thread_id == expected_thread_id


def record_thread_id(record: JsonObject) -> str | None:
    payload = dict_or_empty(record.get("payload"))
    direct = optional_str(payload.get("threadId"))
    if direct:
        return direct
    turn = dict_or_empty(payload.get("turn"))
    return optional_str(turn.get("threadId"))


def _record_with_turn_id(record: JsonObject, turn_id: str | None) -> JsonObject:
    if turn_id is None or record.get("turn_id") is not None:
        return record
    return {**record, "turn_id": turn_id}


def _turn_started(context: _NotificationContext) -> list[CodexFact]:
    turn = dict_or_empty(context.payload.get("turn"))
    return [
        _context_fact(
            context,
            "codex.turn.started",
            turn_id=optional_str(turn.get("id")) or context.turn_id,
        )
    ]


def _turn_completed(context: _NotificationContext) -> list[CodexFact]:
    turn = dict_or_empty(context.payload.get("turn"))
    return [
        _context_fact(
            context,
            "codex.turn.completed",
            turn_id=optional_str(turn.get("id")) or context.turn_id,
        )
    ]


def _item_started(context: _NotificationContext) -> list[CodexFact]:
    item = dict_or_empty(context.payload.get("item"))
    return [_context_fact(context, "codex.item.started", item_id=optional_str(item.get("id")))]


def _item_completed(context: _NotificationContext) -> list[CodexFact]:
    item = dict_or_empty(context.payload.get("item"))
    item_id = optional_str(item.get("id"))
    facts = [_context_fact(context, "codex.item.completed", item_id=item_id)]
    if item.get("type") == "agentMessage":
        facts.append(
            _context_fact(
                context,
                "codex.assistant_message.completed",
                payload={
                    "text": item.get("text"),
                    "phase": item.get("phase"),
                    "raw": context.payload,
                },
                item_id=item_id,
            )
        )
    if item.get("type") == "contextCompaction":
        facts.append(_context_fact(context, "codex.thread.compacted", item_id=item_id))
    return facts


def _assistant_message_delta(context: _NotificationContext) -> list[CodexFact]:
    return [
        _context_fact(
            context,
            "codex.assistant_message.delta",
            payload={
                "delta": context.payload.get("delta"),
                "phase": context.payload.get("phase"),
                "raw": context.payload,
            },
            item_id=optional_str(context.payload.get("itemId")),
        )
    ]


def _token_usage_updated(context: _NotificationContext) -> list[CodexFact]:
    return [_context_fact(context, "codex.token_usage.updated")]


def _command_output_delta(context: _NotificationContext) -> list[CodexFact]:
    return [
        _context_fact(
            context,
            "codex.command_output.delta",
            item_id=optional_str(context.payload.get("itemId")),
        )
    ]


def _file_change_patch_updated(context: _NotificationContext) -> list[CodexFact]:
    return [
        _context_fact(
            context,
            "codex.file_change.patch_updated",
            item_id=optional_str(context.payload.get("itemId")),
        )
    ]


def _turn_diff_updated(context: _NotificationContext) -> list[CodexFact]:
    return [_context_fact(context, "codex.turn.diff.updated")]


def _error_observed(context: _NotificationContext) -> list[CodexFact]:
    return [_context_fact(context, "codex.error.observed")]


def _notification_observed(context: _NotificationContext) -> list[CodexFact]:
    return [
        _context_fact(
            context,
            "codex.notification.observed",
            payload={"method": context.method, "payload": context.payload},
        )
    ]


_NOTIFICATION_HANDLERS: dict[str, _NotificationHandler] = {
    "turn/started": _turn_started,
    "turn/completed": _turn_completed,
    "item/started": _item_started,
    "item/completed": _item_completed,
    "item/agentMessage/delta": _assistant_message_delta,
    "thread/tokenUsage/updated": _token_usage_updated,
    "item/commandExecution/outputDelta": _command_output_delta,
    "item/fileChange/patchUpdated": _file_change_patch_updated,
    "turn/diff/updated": _turn_diff_updated,
    "error": _error_observed,
}


def _fact(
    kind: str,
    *,
    source: str,
    payload: JsonObject,
    occurred_at: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    item_id: str | None = None,
) -> CodexFact:
    event_id = _event_id(kind, source, occurred_at, thread_id, turn_id, item_id, payload)
    return CodexFact(
        kind=kind,
        event_id=event_id,
        source=source,
        payload=payload,
        thread_id=thread_id,
        turn_id=turn_id,
        item_id=item_id,
        occurred_at=occurred_at,
    )


def _context_fact(
    context: _NotificationContext,
    kind: str,
    *,
    payload: JsonObject | None = None,
    turn_id: str | None = None,
    item_id: str | None = None,
) -> CodexFact:
    return _fact(
        kind,
        source=context.source,
        payload=context.payload if payload is None else payload,
        occurred_at=context.occurred_at,
        thread_id=context.thread_id,
        turn_id=context.turn_id if turn_id is None else turn_id,
        item_id=item_id,
    )


def _event_id(
    kind: str,
    source: str,
    occurred_at: str | None,
    thread_id: str | None,
    turn_id: str | None,
    item_id: str | None,
    payload: JsonObject,
) -> str:
    body = canonical_json(
        {
            "kind": kind,
            "source": source,
            "occurred_at": occurred_at,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "item_id": item_id,
            "payload": payload,
        },
    )
    return sha256_text(body)[:24]


def _extract_thread_id(payload: JsonObject) -> str | None:
    direct = optional_str(payload.get("threadId"))
    if direct:
        return direct
    turn = dict_or_empty(payload.get("turn"))
    return optional_str(turn.get("threadId"))


def _extract_turn_id(payload: JsonObject) -> str | None:
    direct = optional_str(payload.get("turnId"))
    if direct:
        return direct
    turn = dict_or_empty(payload.get("turn"))
    return optional_str(turn.get("id"))
