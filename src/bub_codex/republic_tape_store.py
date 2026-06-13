"""Republic tape-store adapter boundary.

This module is the only place that translates between the internal TapeStore
port and Bub/Republic tape entries, including native Bub Anchor entries.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from republic import TapeEntry, TapeQuery
from republic.core.errors import ErrorKind, RepublicError
from republic.tape.store import is_async_tape_store

from .json_utils import dict_or_empty, optional_str, sha256_text
from .tape_events import TapeEvent


BUB_CODEX_META_KEY = "bub_codex_event"
BUB_CODEX_META_VALUE = "v0"
BUB_CODEX_DATA_KEY = "bub_codex_data"


@dataclass(slots=True)
class RepublicTapeStoreAdapter:
    """Adapter from Bub/Republic tape storage to the bub-codex TapeStore port."""

    store: Any

    async def append(self, event: TapeEvent) -> None:
        await self.append_many((event,))

    async def append_many(self, events: Iterable[TapeEvent]) -> None:
        for event in events:
            await self._append_one(event)

    async def events(self, *, session_id: str | None = None, tape_id: str | None = None) -> list[TapeEvent]:
        if tape_id is None:
            return []
        return [
            event
            for event in await self._read_tape_events(tape_id)
            if session_id is None or event.session_id in (session_id, None)
        ]

    async def close(self) -> None:
        close = getattr(self.store, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    async def _append_one(self, event: TapeEvent) -> None:
        entry = _entry_for_event(event)
        result = self.store.append(event.tape_id or "", entry)
        if hasattr(result, "__await__"):
            await result

    async def _read_tape_events(self, tape_id: str) -> list[TapeEvent]:
        entries = await _read_entries(self.store, tape_id)
        events: list[TapeEvent] = []
        for entry in entries:
            if entry.meta.get(BUB_CODEX_META_KEY) != BUB_CODEX_META_VALUE:
                if native_anchor := _native_anchor_event(entry, tape_id=tape_id):
                    events.append(native_anchor)
                continue
            data = entry.meta.get(BUB_CODEX_DATA_KEY)
            if not isinstance(data, dict):
                payload = entry.payload
                data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                events.append(_event_from_json(data))
        return events


async def _read_entries(store: Any, tape_id: str) -> list[TapeEntry]:
    if hasattr(store, "read"):
        entries = store.read(tape_id)
        if hasattr(entries, "__await__"):
            entries = await entries
        return list(entries or ())
    if hasattr(store, "fetch_all"):
        query = TapeQuery(tape=tape_id, store=store)
        result = store.fetch_all(query)
        if is_async_tape_store(store) or hasattr(result, "__await__"):
            result = await result
        return list(result or ())
    return []


def _entry_for_event(event: TapeEvent) -> TapeEntry:
    meta = {
        BUB_CODEX_META_KEY: BUB_CODEX_META_VALUE,
        BUB_CODEX_DATA_KEY: event.to_json(),
    }
    if event.type == "bub.anchor.created":
        return TapeEntry.anchor(
            _anchor_name(event),
            state=_anchor_state(event),
            **meta,
        )
    if event.type == "codex.assistant_message.completed":
        return TapeEntry.message(
            {
                "role": "assistant",
                "content": event.payload.get("assistant_text"),
                "phase": event.payload.get("phase"),
            },
            **meta,
        )
    if event.type in {"codex.error.observed", "bub.runtime.error", "bub.anchor.creation.failed"}:
        return TapeEntry.error(_republic_error_for_event(event), **meta)
    if event.type == "bub.tool.call.started":
        return TapeEntry.tool_call([event.payload], **meta)
    if event.type == "bub.tool.call.completed":
        return TapeEntry.tool_result([event.payload], **meta)
    return TapeEntry.event(event.type, event.to_json(), **meta)


def _anchor_name(event: TapeEvent) -> str:
    reason = event.payload.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    anchor_id = event.payload.get("anchor_id") or event.anchor_id
    return str(anchor_id or "bub-codex")


def _anchor_state(event: TapeEvent) -> dict[str, Any]:
    state = event.payload.get("state")
    if isinstance(state, dict):
        return dict(state)
    return dict(event.payload)


def _republic_error_for_event(event: TapeEvent) -> RepublicError:
    message = event.payload.get("message") or event.payload.get("error") or event.type
    details = dict(event.payload)
    details.setdefault("event_type", event.type)
    return RepublicError(ErrorKind.UNKNOWN, str(message), details=details)


def _event_from_json(data: dict[str, Any]) -> TapeEvent:
    return TapeEvent(
        type=str(data["type"]),
        event_id=str(data["event_id"]),
        payload=dict_or_empty(data.get("payload")),
        occurred_at=optional_str(data.get("occurred_at")),
        session_id=optional_str(data.get("session_id")),
        tape_id=optional_str(data.get("tape_id")),
        anchor_id=optional_str(data.get("anchor_id")),
        thread_id=optional_str(data.get("thread_id")),
        turn_id=optional_str(data.get("turn_id")),
    )


def _native_anchor_event(entry: TapeEntry, *, tape_id: str) -> TapeEvent | None:
    if entry.kind != "anchor" or not isinstance(entry.payload, dict):
        return None
    name = str(entry.payload.get("name") or "anchor")
    state = entry.payload.get("state")
    if not isinstance(state, dict):
        state = {}
    occurred_at = optional_str(getattr(entry, "date", None))
    source_entry_id = str(getattr(entry, "id", ""))
    anchor_id = _native_anchor_id(tape_id=tape_id, source_entry_id=source_entry_id, name=name, occurred_at=occurred_at)
    payload = {
        "anchor_id": anchor_id,
        "method": "bub_handoff",
        "reason": name,
        "state": state,
        "refs": {"source_entry_id": source_entry_id},
        "initiator": "bub_builtin",
    }
    return TapeEvent(
        type="bub.anchor.created",
        event_id=_native_anchor_id(
            tape_id=tape_id,
            source_entry_id=source_entry_id,
            name=f"event:{name}",
            occurred_at=occurred_at,
        ),
        payload=payload,
        occurred_at=occurred_at,
        session_id=None,
        tape_id=tape_id,
        anchor_id=anchor_id,
    )


def _native_anchor_id(*, tape_id: str, source_entry_id: str, name: str, occurred_at: str | None) -> str:
    body = "|".join((tape_id, source_entry_id, name, occurred_at or ""))
    return "anchor_" + sha256_text(body)[:24]
