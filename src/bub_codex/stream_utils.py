from __future__ import annotations

from typing import Any

from republic import AsyncStreamEvents, StreamEvent, StreamState

from bub.types import State

from .turn_translator import StreamDecision


def stream_text(
    text: str,
    *,
    ok: bool = True,
    error: dict[str, Any] | None = None,
) -> AsyncStreamEvents:
    async def iterator():
        if error is not None:
            yield StreamEvent("error", error)
        if text:
            yield StreamEvent("text", {"delta": text})
        yield StreamEvent("final", {"text": text, "ok": ok})

    return AsyncStreamEvents(iterator(), state=StreamState())


def default_tape_id(session_id: str, state: State) -> str:
    return session_id


def prompt_text(prompt: str | list[dict]) -> str:
    if isinstance(prompt, str):
        return prompt
    return "\n".join(str(part.get("text", "")) for part in prompt if isinstance(part, dict) and part.get("type") == "text")


def to_stream_event(decision: StreamDecision) -> StreamEvent:
    return StreamEvent(decision.kind, decision.data)

