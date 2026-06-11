from __future__ import annotations

import hashlib
from pathlib import Path
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
    workspace = state.get("_runtime_workspace")
    if not workspace:
        return session_id
    workspace_hash = hashlib.md5(
        str(Path(str(workspace)).resolve()).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    session_hash = hashlib.md5(session_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"{workspace_hash}__{session_hash}"


def prompt_text(prompt: str | list[dict]) -> str:
    if isinstance(prompt, str):
        return prompt
    return "\n".join(str(part.get("text", "")) for part in prompt if isinstance(part, dict) and part.get("type") == "text")


def to_stream_event(decision: StreamDecision) -> StreamEvent:
    return StreamEvent(decision.kind, decision.data)
