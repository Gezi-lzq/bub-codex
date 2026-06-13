from __future__ import annotations

from dataclasses import dataclass

from bub_codex.json_utils import JsonObject, dict_or_empty
from bub_codex.runtime_services import RuntimeStreamService
from bub_codex.tape_events import TapeEvent
from bub_codex.tape_store import TapeStore


@dataclass(frozen=True, slots=True)
class PluginStreamEventRecord:
    kind: str
    data: JsonObject


@dataclass(frozen=True, slots=True)
class PluginStreamResult:
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


async def run_plugin_stream_once(
    runtime_stream: RuntimeStreamService,
    *,
    prompt: str | list[dict],
    session_id: str,
    state: State,
    tape_store: TapeStore | None = None,
) -> PluginStreamResult:
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
                data=dict_or_empty(event.data),
            )
        )

    tape_events = tuple(await tape_store.events() if tape_store is not None else ())
    return PluginStreamResult(
        stream_events=tuple(stream_events),
        tape_events=tape_events,
    )
