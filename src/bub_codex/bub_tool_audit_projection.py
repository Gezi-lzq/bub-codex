from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .bub_tools import BubToolInvocationAuditRecord
from .tape_events import JsonObject, TapeEvent, make_tape_event


def project_bub_tool_invocation_records(
    records: Iterable[BubToolInvocationAuditRecord],
    *,
    session_id: str,
    tape_id: str,
    anchor_id: str | None = None,
) -> list[TapeEvent]:
    return [
        make_tape_event(
            record.event_type,
            payload={
                "tool_call_id": record.call_id,
                "namespace": record.namespace,
                "codex_tool_name": record.codex_tool_name,
                "bub_tool_name": record.bub_tool_name,
                "arguments_sha256": _sha256_json(record.arguments),
                "arguments_preview": _preview(record.arguments),
                "success": record.success,
                "output_sha256": _sha256_json(record.output) if record.output is not None else None,
                "output_preview": _preview(record.output) if record.output is not None else None,
                "error_type": record.error_type,
                "error_message": record.error_message,
            },
            occurred_at=record.occurred_at,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
            thread_id=record.thread_id,
            turn_id=record.turn_id,
        )
        for record in records
    ]


def _sha256_json(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _preview(value: object, *, max_chars: int = 800) -> str | None:
    if value is None:
        return None
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= max_chars else text[:max_chars] + "...<truncated>"

