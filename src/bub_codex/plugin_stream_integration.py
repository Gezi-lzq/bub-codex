from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bub.types import State

from .runtime_services import RuntimeStreamService
from .tape_events import JsonObject, TapeEvent
from .tape_store import TapeStore


@dataclass(frozen=True, slots=True)
class PluginStreamEventRecord:
    kind: str
    data: JsonObject

    def to_json(self) -> JsonObject:
        return {"kind": self.kind, "data": self.data}


@dataclass(frozen=True, slots=True)
class PluginStreamIntegrationResult:
    stream_events: tuple[PluginStreamEventRecord, ...]
    tape_events: tuple[TapeEvent, ...]

    @property
    def final_text(self) -> str | None:
        for event in reversed(self.stream_events):
            if event.kind == "final":
                text = event.data.get("text")
                return text if isinstance(text, str) else None
        return None

    @property
    def text(self) -> str:
        return "".join(
            str(event.data.get("delta", ""))
            for event in self.stream_events
            if event.kind == "text"
        )

    def to_json(self) -> JsonObject:
        return {
            "stream_events": [event.to_json() for event in self.stream_events],
            "tape_events": [event.to_json() for event in self.tape_events],
        }


async def run_plugin_stream_once(
    runtime_stream: RuntimeStreamService,
    *,
    prompt: str | list[dict],
    session_id: str,
    state: State,
    tape_store: TapeStore | None = None,
) -> PluginStreamIntegrationResult:
    stream = await runtime_stream.run_stream(
        prompt=prompt,
        session_id=session_id,
        state=state,
    )
    stream_events = []
    async for event in stream:
        stream_events.append(
            PluginStreamEventRecord(
                kind=str(event.kind),
                data=_dict_or_empty(event.data),
            )
        )

    tape_events = tuple(tape_store.events() if tape_store is not None else ())
    return PluginStreamIntegrationResult(
        stream_events=tuple(stream_events),
        tape_events=tape_events,
    )


def _dict_or_empty(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}
