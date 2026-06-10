from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from republic import TapeEntry, TapeQuery
from republic.tape.store import is_async_tape_store

from .runtime_resolution import RuntimeContextResolution, resolve_runtime_context
from .tape_events import TapeEvent


BUB_CODEX_META_KEY = "bub_codex_event"
BUB_CODEX_META_VALUE = "v0"


@dataclass(slots=True)
class RepublicTapeStoreAdapter:
    """Adapter from Bub/Republic tape storage to the bub-codex TapeStore port."""

    store: Any

    def append(self, event: TapeEvent) -> None:
        self.append_many((event,))

    def append_many(self, events: Iterable[TapeEvent]) -> None:
        for event in events:
            self._append_one(event)

    def events(self, *, session_id: str | None = None, tape_id: str | None = None) -> list[TapeEvent]:
        if tape_id is None:
            return []
        return [
            event
            for event in self._read_tape_events(tape_id)
            if session_id is None or event.session_id == session_id
        ]

    def resolve_runtime_context(
        self,
        *,
        session_id: str,
        tape_id: str,
    ) -> RuntimeContextResolution:
        return resolve_runtime_context(self.events(session_id=session_id, tape_id=tape_id))

    def _append_one(self, event: TapeEvent) -> None:
        entry = TapeEntry.event(
            event.type,
            event.to_json(),
            bub_codex_event=BUB_CODEX_META_VALUE,
        )
        result = self.store.append(event.tape_id or "", entry)
        if hasattr(result, "__await__"):
            _run_awaitable(result)

    def _read_tape_events(self, tape_id: str) -> list[TapeEvent]:
        entries = _read_entries(self.store, tape_id)
        events: list[TapeEvent] = []
        for entry in entries:
            if entry.meta.get(BUB_CODEX_META_KEY) != BUB_CODEX_META_VALUE:
                continue
            payload = entry.payload
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                events.append(_event_from_json(data))
        return events


def _read_entries(store: Any, tape_id: str) -> list[TapeEntry]:
    if hasattr(store, "read"):
        entries = store.read(tape_id)
        return list(entries or ())
    if hasattr(store, "fetch_all"):
        query = TapeQuery(tape=tape_id, store=store)
        result = store.fetch_all(query)
        if is_async_tape_store(store) or hasattr(result, "__await__"):
            result = _run_awaitable(result)
        return list(result or ())
    return []


def _event_from_json(data: dict[str, Any]) -> TapeEvent:
    return TapeEvent(
        type=str(data["type"]),
        event_id=str(data["event_id"]),
        payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
        occurred_at=_optional_str(data.get("occurred_at")),
        session_id=_optional_str(data.get("session_id")),
        tape_id=_optional_str(data.get("tape_id")),
        anchor_id=_optional_str(data.get("anchor_id")),
        thread_id=_optional_str(data.get("thread_id")),
        turn_id=_optional_str(data.get("turn_id")),
    )


def _run_awaitable(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("bub-codex RepublicTapeStoreAdapter cannot call async tape store from a running loop yet")


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
