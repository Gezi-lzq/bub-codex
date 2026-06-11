from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Literal

from .tape_events import JsonObject, TapeEvent
from .new_thread_materialization import active_thread_id_for_anchor, latest_anchor_created


RuntimeAction = Literal["bootstrap", "materialize_thread", "resume_thread"]


@dataclass(frozen=True, slots=True)
class RuntimeContextResolution:
    action: RuntimeAction
    anchor_id: str | None
    thread_id: str | None
    reason: str

    def to_json(self) -> JsonObject:
        return asdict(self)


def resolve_runtime_context(events: Iterable[TapeEvent]) -> RuntimeContextResolution:
    """Resolve Codex runtime state from tape events only."""

    event_list = list(events)
    anchor = latest_anchor_created(event_list)
    if anchor is None:
        return RuntimeContextResolution(
            action="bootstrap",
            anchor_id=None,
            thread_id=None,
            reason="no_committed_anchor",
        )

    thread_id = active_thread_id_for_anchor(event_list, anchor.anchor_id)
    if thread_id is None:
        return RuntimeContextResolution(
            action="materialize_thread",
            anchor_id=anchor.anchor_id,
            thread_id=None,
            reason="latest_anchor_has_no_thread_binding",
        )

    return RuntimeContextResolution(
        action="resume_thread",
        anchor_id=anchor.anchor_id,
        thread_id=thread_id,
        reason="latest_anchor_has_thread_binding",
    )
