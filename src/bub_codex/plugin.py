from __future__ import annotations

from typing import Any

from republic import AsyncStreamEvents

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

    @hookimpl
    async def run_model_stream(self, prompt: str | list[dict], session_id: str, state: State) -> AsyncStreamEvents:
        if _is_comma_command(prompt):
            return await _run_comma_command(prompt, session_id=session_id, state=state)
        return await self.runtime.run_stream(prompt=prompt, session_id=session_id, state=state)


async def _run_comma_command(prompt: str | list[dict], *, session_id: str, state: State) -> AsyncStreamEvents:
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
    return stream_text(str(result))


def _is_comma_command(prompt: str | list[dict]) -> bool:
    return isinstance(prompt, str) and prompt.strip().startswith(",")

