from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol

from .context_materialization import active_thread_id_for_anchor, latest_anchor_created
from .runtime_resolution import RuntimeContextResolution, resolve_runtime_context
from .tape_events import TapeEvent


class TapeStore(Protocol):
    """Minimal append-only tape store boundary for runtime spikes."""

    def append(self, event: TapeEvent) -> None:
        ...

    def append_many(self, events: Iterable[TapeEvent]) -> None:
        ...

    def events(self, *, session_id: str | None = None, tape_id: str | None = None) -> list[TapeEvent]:
        ...

    def resolve_runtime_context(
        self,
        *,
        session_id: str,
        tape_id: str,
    ) -> RuntimeContextResolution:
        ...


@dataclass(slots=True)
class InMemoryTapeStore:
    """Small append-only tape store for validating domain projections."""

    _events: list[TapeEvent] = field(default_factory=list)

    def append(self, event: TapeEvent) -> None:
        self._events.append(event)

    def append_many(self, events: Iterable[TapeEvent]) -> None:
        self._events.extend(events)

    def events(self, *, session_id: str | None = None, tape_id: str | None = None) -> list[TapeEvent]:
        selected = self._events
        if session_id is not None:
            selected = [event for event in selected if event.session_id == session_id]
        if tape_id is not None:
            selected = [event for event in selected if event.tape_id == tape_id]
        return list(selected)

    def latest_anchor(self, *, session_id: str, tape_id: str) -> TapeEvent | None:
        return latest_anchor_created(self.events(session_id=session_id, tape_id=tape_id))

    def active_thread_id(self, *, session_id: str, tape_id: str) -> str | None:
        events = self.events(session_id=session_id, tape_id=tape_id)
        anchor = latest_anchor_created(events)
        if anchor is None:
            return None
        return active_thread_id_for_anchor(events, anchor.anchor_id)

    def resolve_runtime_context(
        self,
        *,
        session_id: str,
        tape_id: str,
    ) -> RuntimeContextResolution:
        return resolve_runtime_context(self.events(session_id=session_id, tape_id=tape_id))
