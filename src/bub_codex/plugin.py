from __future__ import annotations

import shlex
from typing import Any

from republic import AsyncStreamEvents

from bub.hookspecs import hookimpl
from bub.types import State

from .config import load_settings
from .runtime_services import LazyRuntimeStreamService, RuntimeStreamService, UnconfiguredRuntimeStreamService
from .context_materialization import create_new_thread_anchor_events
from .stream_utils import default_tape_id, prompt_text, stream_text


def create_plugin(framework: Any) -> "BubCodexPlugin":
    settings = load_settings()
    if not settings.enabled:
        return BubCodexPlugin()
    return BubCodexPlugin(LazyRuntimeStreamService(framework, settings=settings))


class BubCodexPlugin:
    def __init__(self, runtime: RuntimeStreamService | None = None) -> None:
        self.runtime = runtime or UnconfiguredRuntimeStreamService()

    @hookimpl
    async def run_model_stream(self, prompt: str | list[dict], session_id: str, state: State) -> AsyncStreamEvents:
        if _is_comma_command(prompt):
            return await _run_comma_command(prompt, session_id=session_id, state=state, runtime=self.runtime)
        return await self.runtime.run_stream(prompt=prompt, session_id=session_id, state=state)


async def _run_comma_command(
    prompt: str | list[dict],
    *,
    session_id: str,
    state: State,
    runtime: RuntimeStreamService,
) -> AsyncStreamEvents:
    agent = state.get("_runtime_agent")
    if agent is None or not hasattr(agent, "run"):
        return stream_text(
            "bub-codex cannot run comma command without _runtime_agent",
            ok=False,
            error={
                "kind": "unknown",
                "message": "bub-codex cannot run comma command without _runtime_agent",
            },
        )

    result = agent.run(session_id=session_id, prompt=prompt, state=state)
    if hasattr(result, "__await__"):
        result = await result
    _record_handoff_anchor(prompt, session_id=session_id, state=state, runtime=runtime)
    return stream_text(str(result))


def _is_comma_command(prompt: str | list[dict]) -> bool:
    return isinstance(prompt, str) and prompt.strip().startswith(",")


def _record_handoff_anchor(prompt: str | list[dict], *, session_id: str, state: State, runtime: Any) -> None:
    if not isinstance(prompt, str):
        return
    command = _parse_comma_command(prompt)
    if command is None or command[0] not in {"tape.handoff", "tape_handoff"}:
        return
    tape_store = _runtime_tape_store(runtime)
    if tape_store is None:
        return
    tape_id = default_tape_id(session_id, state)
    existing_events = tape_store.events(session_id=session_id, tape_id=tape_id)
    anchor_events = create_new_thread_anchor_events(
        existing_events,
        session_id=session_id,
        tape_id=tape_id,
        reason="handoff",
        intent=prompt_text(prompt),
        summary=_handoff_summary(command[1]),
        owner="assistant",
        initiator="bub_builtin_command",
    )
    tape_store.append_many(anchor_events)


def _runtime_tape_store(runtime: Any) -> Any | None:
    cached = getattr(runtime, "_cached_runtime", None)
    if cached is not None:
        return getattr(cached, "tape_store", None)
    return getattr(runtime, "tape_store", None)


def _parse_comma_command(prompt: str) -> tuple[str, list[str]] | None:
    try:
        words = shlex.split(prompt.strip()[1:].strip())
    except ValueError:
        return None
    if not words:
        return None
    return words[0], words[1:]


def _handoff_summary(args: list[str]) -> str | None:
    values: dict[str, str] = {}
    positional: list[str] = []
    for token in args:
        if "=" in token:
            key, value = token.split("=", 1)
            values[key] = value
        else:
            positional.append(token)
    summary = values.get("summary")
    if summary:
        return summary
    if positional:
        return " ".join(positional)
    name = values.get("name")
    return name if name else None
