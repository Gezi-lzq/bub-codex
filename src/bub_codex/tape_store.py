"""Internal tape-store port.

This module defines the minimal append/read interface used by the runtime.
Republic-specific storage behavior belongs in `republic_tape_store.py`.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Iterable, Protocol

from .tape_events import TapeEvent


class TapeStore(Protocol):
    """Append-only event store used by the runtime state machine."""

    async def append(self, event: TapeEvent) -> None:
        ...

    async def append_many(self, events: Iterable[TapeEvent]) -> None:
        ...

    async def events(self, *, session_id: str | None = None, tape_id: str | None = None) -> list[TapeEvent]:
        ...


async def close_tape_store(tape_store: TapeStore | None) -> None:
    if tape_store is None:
        return
    close = getattr(tape_store, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


@dataclass(slots=True)
class InMemoryTapeStore:
    """In-process TapeStore for tests and explicit non-Bub runtime mode."""

    _events: list[TapeEvent] = field(default_factory=list)

    async def append(self, event: TapeEvent) -> None:
        self._events.append(event)

    async def append_many(self, events: Iterable[TapeEvent]) -> None:
        self._events.extend(events)

    async def events(self, *, session_id: str | None = None, tape_id: str | None = None) -> list[TapeEvent]:
        selected = self._events
        if session_id is not None:
            selected = [event for event in selected if event.session_id == session_id]
        if tape_id is not None:
            selected = [event for event in selected if event.tape_id == tape_id]
        return list(selected)
