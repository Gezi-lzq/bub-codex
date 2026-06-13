"""Codex notification record helper functions."""

from __future__ import annotations

from .json_utils import JsonObject, canonical_json, dict_or_empty, optional_str, sha256_text


def record_belongs_to_thread(record: JsonObject, expected_thread_id: str) -> bool:
    thread_id = record_thread_id(record)
    return thread_id is None or thread_id == expected_thread_id


def record_thread_id(record: JsonObject) -> str | None:
    payload = record_payload(record)
    direct = optional_str(payload.get("threadId"))
    if direct:
        return direct
    turn = dict_or_empty(payload.get("turn"))
    return optional_str(turn.get("threadId"))


def record_turn_id(record: JsonObject) -> str | None:
    payload = record_payload(record)
    direct = optional_str(payload.get("turnId"))
    if direct:
        return direct
    turn = dict_or_empty(payload.get("turn"))
    return optional_str(turn.get("id")) or optional_str(record.get("turn_id"))


def record_payload(record: JsonObject) -> JsonObject:
    return dict_or_empty(record.get("payload"))


def record_item(record: JsonObject) -> JsonObject:
    return dict_or_empty(record_payload(record).get("item"))


def record_item_id(record: JsonObject) -> str | None:
    payload = record_payload(record)
    item_id = optional_str(payload.get("itemId"))
    if item_id:
        return item_id
    return optional_str(record_item(record).get("id"))


def record_event_id(
    record: JsonObject,
    *,
    kind: str,
    source: str,
    payload: JsonObject | None = None,
    item_id: str | None = None,
    turn_id: str | None = None,
) -> str:
    body = canonical_json(
        {
            "kind": kind,
            "source": source,
            "occurred_at": optional_str(record.get("ts")),
            "thread_id": record_thread_id(record),
            "turn_id": record_turn_id(record) if turn_id is None else turn_id,
            "item_id": record_item_id(record) if item_id is None else item_id,
            "payload": record_payload(record) if payload is None else payload,
        },
    )
    return sha256_text(body)[:24]
