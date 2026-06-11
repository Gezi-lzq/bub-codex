from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class CodexFact:
    """Normalized fact emitted by the Codex runtime adapter.

    This is intentionally smaller than the final tape schema. It is the adapter
    boundary that lets us compare SDK stream, thread.read, and rollout sources
    without leaking generated SDK models into Bub domain code.
    """

    kind: str
    event_id: str
    source: str
    payload: JsonObject
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    occurred_at: str | None = None

    def to_json(self) -> JsonObject:
        return asdict(self)


def facts_from_notification_record(
    record: JsonObject,
    *,
    source: str = "sdk_stream",
) -> list[CodexFact]:
    method = str(record.get("method") or "")
    payload = _dict_or_empty(record.get("payload"))
    occurred_at = _optional_str(record.get("ts"))
    thread_id = _extract_thread_id(payload)
    turn_id = _extract_turn_id(payload) or _optional_str(record.get("turn_id"))

    if method == "turn/started":
        turn = _dict_or_empty(payload.get("turn"))
        return [
            _fact(
                "codex.turn.started",
                source=source,
                payload=payload,
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=_optional_str(turn.get("id")) or turn_id,
            )
        ]

    if method == "turn/completed":
        turn = _dict_or_empty(payload.get("turn"))
        return [
            _fact(
                "codex.turn.completed",
                source=source,
                payload=payload,
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=_optional_str(turn.get("id")) or turn_id,
            )
        ]

    if method == "item/started":
        item = _dict_or_empty(payload.get("item"))
        return [
            _fact(
                "codex.item.started",
                source=source,
                payload=payload,
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=_optional_str(item.get("id")),
            )
        ]

    if method == "item/completed":
        item = _dict_or_empty(payload.get("item"))
        facts = [
            _fact(
                "codex.item.completed",
                source=source,
                payload=payload,
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=_optional_str(item.get("id")),
            )
        ]
        if item.get("type") == "agentMessage":
            facts.append(
                _fact(
                    "codex.assistant_message.completed",
                    source=source,
                    payload={
                        "text": item.get("text"),
                        "phase": item.get("phase"),
                        "raw": payload,
                    },
                    occurred_at=occurred_at,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_id=_optional_str(item.get("id")),
                )
            )
        if item.get("type") == "contextCompaction":
            facts.append(
                _fact(
                    "codex.thread.compacted",
                    source=source,
                    payload=payload,
                    occurred_at=occurred_at,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_id=_optional_str(item.get("id")),
                )
            )
        return facts

    if method == "item/agentMessage/delta":
        return [
            _fact(
                "codex.assistant_message.delta",
                source=source,
                payload={
                    "delta": payload.get("delta"),
                    "phase": payload.get("phase"),
                    "raw": payload,
                },
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=_optional_str(payload.get("itemId")),
            )
        ]

    if method == "thread/tokenUsage/updated":
        return [
            _fact(
                "codex.token_usage.updated",
                source=source,
                payload=payload,
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=turn_id,
            )
        ]

    if method == "item/commandExecution/outputDelta":
        return [
            _fact(
                "codex.command_output.delta",
                source=source,
                payload=payload,
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=_optional_str(payload.get("itemId")),
            )
        ]

    if method == "item/fileChange/patchUpdated":
        return [
            _fact(
                "codex.file_change.patch_updated",
                source=source,
                payload=payload,
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=_optional_str(payload.get("itemId")),
            )
        ]

    if method == "turn/diff/updated":
        return [
            _fact(
                "codex.turn.diff.updated",
                source=source,
                payload=payload,
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=turn_id,
            )
        ]

    if method == "error":
        return [
            _fact(
                "codex.error.observed",
                source=source,
                payload=payload,
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=turn_id,
            )
        ]

    return [
        _fact(
            "codex.notification.observed",
            source=source,
            payload={"method": method, "payload": payload},
            occurred_at=occurred_at,
            thread_id=thread_id,
            turn_id=turn_id,
        )
    ]


def load_compaction_snapshots(path: Path) -> list[CodexFact]:
    """Load rollout compacted items from a spike `rollout-compacted-items.json` file."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    facts: list[CodexFact] = []
    for entry in raw:
        item = _dict_or_empty(entry.get("item"))
        payload = _dict_or_empty(item.get("payload"))
        replacement_history = payload.get("replacement_history")
        facts.append(
            _fact(
                "codex.compaction.snapshot",
                source="rollout",
                payload={
                    "line": entry.get("line"),
                    "message": payload.get("message"),
                    "message_sha256": _sha256_text(payload.get("message")),
                    "replacement_history_len": len(replacement_history)
                    if isinstance(replacement_history, list)
                    else None,
                    "replacement_history_sha256": _sha256_json(replacement_history),
                    "parse_status": _parse_status(payload),
                    "raw": item,
                },
                occurred_at=_optional_str(item.get("timestamp")),
            )
        )
    return facts


def facts_from_server_request_record(
    record: JsonObject,
    *,
    source: str = "sdk_server_request",
) -> list[CodexFact]:
    method = str(record.get("method") or "")
    params = _dict_or_empty(record.get("params"))
    occurred_at = _optional_str(record.get("ts"))
    thread_id = _optional_str(params.get("threadId"))
    turn_id = _optional_str(params.get("turnId"))

    if method == "item/tool/call":
        return [
            _fact(
                "codex.dynamic_tool.requested",
                source=source,
                payload=params,
                occurred_at=occurred_at,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=_optional_str(params.get("callId")),
            )
        ]

    return [
        _fact(
            "codex.server_request.observed",
            source=source,
            payload={"method": method, "params": params},
            occurred_at=occurred_at,
            thread_id=thread_id,
            turn_id=turn_id,
        )
    ]


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


def _event_id(
    kind: str,
    source: str,
    occurred_at: str | None,
    thread_id: str | None,
    turn_id: str | None,
    item_id: str | None,
    payload: JsonObject,
) -> str:
    body = json.dumps(
        {
            "kind": kind,
            "source": source,
            "occurred_at": occurred_at,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "item_id": item_id,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def _extract_thread_id(payload: JsonObject) -> str | None:
    direct = _optional_str(payload.get("threadId"))
    if direct:
        return direct
    turn = _dict_or_empty(payload.get("turn"))
    return _optional_str(turn.get("threadId"))


def _extract_turn_id(payload: JsonObject) -> str | None:
    direct = _optional_str(payload.get("turnId"))
    if direct:
        return direct
    turn = _dict_or_empty(payload.get("turn"))
    return _optional_str(turn.get("id"))


def _parse_status(payload: JsonObject) -> str:
    if payload.get("message") and payload.get("replacement_history") is not None:
        return "ok"
    if payload.get("message"):
        return "replacement_history_missing"
    if payload.get("replacement_history") is not None:
        return "summary_missing"
    return "failed"


def _dict_or_empty(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _sha256_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str | None:
    if value is None:
        return None
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
