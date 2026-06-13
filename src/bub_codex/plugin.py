"""Bub plugin boundary.

This module owns the Bub hook surface and comma-command delegation. It should
not call Codex SDK methods, decide thread state, or project tape events.
"""

from __future__ import annotations

import inspect
from typing import Any

from republic import AsyncStreamEvents

from bub.envelope import content_of
from bub.hookspecs import hookimpl
from bub.types import State

from .config import load_settings
from .runtime_services import LazyRuntimeStreamService, RuntimeStreamService, UnconfiguredRuntimeStreamService
from .stream_utils import stream_text


def create_plugin(framework: Any) -> "BubCodexPlugin":
    settings = load_settings()
    if not settings.enabled:
        return BubCodexPlugin()
    return BubCodexPlugin(LazyRuntimeStreamService(framework, settings=settings))


class BubCodexPlugin:
    def __init__(self, runtime: RuntimeStreamService | None = None) -> None:
        self.runtime = runtime or UnconfiguredRuntimeStreamService()

    @hookimpl(optionalhook=True)
    def admit_message(self, session_id: str, message: Any, turn: Any) -> Any:
        if _is_comma_message(message):
            return None
        if getattr(turn, "is_running", False):
            return _admit_decision("steer", reason="codex turn is running")
        return None

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
    agent = _comma_command_agent(state)
    if agent is None:
        return stream_text(
            "bub-codex cannot run comma command without _runtime_agent",
            ok=False,
            error={
                "kind": "unknown",
                "message": "bub-codex cannot run comma command without _runtime_agent",
            },
        )

    try:
        result = agent.run(session_id=session_id, prompt=prompt, state=state)
        if inspect.isawaitable(result):
            result = await result
        return stream_text(str(result))
    finally:
        await _close_current_tape_store(runtime)


def _is_comma_command(prompt: str | list[dict]) -> bool:
    return isinstance(prompt, str) and prompt.strip().startswith(",")


def _is_comma_message(message: Any) -> bool:
    if isinstance(message, str):
        return _is_comma_command(message)
    return _is_comma_command(content_of(message))


def _comma_command_agent(state: State) -> Any | None:
    agent = state.get("_runtime_agent")
    run = getattr(agent, "run", None)
    return agent if callable(run) else None


async def _close_current_tape_store(runtime: RuntimeStreamService) -> None:
    tape_store = runtime.current_tape_store()
    if tape_store is None:
        return
    close = getattr(tape_store, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _admit_decision(action: str, *, reason: str | None = None) -> Any:
    try:
        from bub.turn_admission import AdmitDecision
    except ImportError:
        return {"action": action, "reason": reason}
    return AdmitDecision(action=action, reason=reason)
