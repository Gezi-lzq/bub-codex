#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FakeStreamEvent:
    kind: str
    data: dict[str, Any]


@dataclass
class FakeAsyncStreamEvents:
    _iterator: AsyncIterator[FakeStreamEvent]

    def __aiter__(self) -> AsyncIterator[FakeStreamEvent]:
        return self._iterator


@dataclass
class FakeBuiltinAgent:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def run(self, *, session_id: str, prompt: str | list[dict], state: dict[str, Any]) -> str:
        self.calls.append({"session_id": session_id, "prompt": prompt, "workspace": state.get("_runtime_workspace")})
        return f"command:{prompt}"


class FakeBuiltinPlugin:
    async def load_state(self, message: dict[str, Any], session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "_runtime_agent": FakeBuiltinAgent()}

    async def build_prompt(self, message: dict[str, Any], session_id: str, state: dict[str, Any]) -> str:
        return str(message.get("content") or "")

    async def run_model(self, prompt: str | list[dict], session_id: str, state: dict[str, Any]) -> str:
        return f"builtin:{prompt}"


class FakeBubCodexPlugin:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run_model_stream(self, prompt: str | list[dict], session_id: str, state: dict[str, Any]):
        self.calls.append(
            {
                "prompt": prompt,
                "session_id": session_id,
                "workspace": state.get("_runtime_workspace"),
                "has_agent": "_runtime_agent" in state,
            }
        )

        if isinstance(prompt, str) and prompt.strip().startswith(","):
            result = await state["_runtime_agent"].run(session_id=session_id, prompt=prompt, state=state)
            return _stream_text(result)

        return _stream_text(f"codex:{prompt}")


class FakeHookRuntime:
    def __init__(self, plugins: list[tuple[str, object]]) -> None:
        self.plugins = plugins

    async def call_many_reversed(self, hook_name: str, **kwargs: Any) -> list[Any]:
        results: list[Any] = []
        for _, plugin in reversed(self.plugins):
            fn = getattr(plugin, hook_name, None)
            if callable(fn):
                value = fn(**_kwargs_for(fn, kwargs))
                if hasattr(value, "__await__"):
                    value = await value
                if value is not None:
                    results.append(value)
        return results

    async def call_first(self, hook_name: str, **kwargs: Any) -> Any:
        for _, plugin in reversed(self.plugins):
            fn = getattr(plugin, hook_name, None)
            if callable(fn):
                value = fn(**_kwargs_for(fn, kwargs))
                if hasattr(value, "__await__"):
                    value = await value
                if value is not None:
                    return value
        return None

    async def run_model(self, prompt: str | list[dict], session_id: str, state: dict[str, Any]) -> str | None:
        for _, plugin in reversed(self.plugins):
            if hasattr(plugin, "run_model"):
                return await self.call_first("run_model", prompt=prompt, session_id=session_id, state=state)
            if hasattr(plugin, "run_model_stream"):
                stream = await self.call_first("run_model_stream", prompt=prompt, session_id=session_id, state=state)
                text = ""
                async for event in stream:
                    if event.kind == "text":
                        text += str(event.data.get("delta", ""))
                return text
        return None

    async def run_model_stream(self, prompt: str | list[dict], session_id: str, state: dict[str, Any]):
        for _, plugin in reversed(self.plugins):
            if hasattr(plugin, "run_model_stream"):
                return await self.call_first("run_model_stream", prompt=prompt, session_id=session_id, state=state)
            if hasattr(plugin, "run_model"):
                result = await self.call_first("run_model", prompt=prompt, session_id=session_id, state=state)
                return _stream_text(str(result or ""))
        return None


async def process_inbound(runtime: FakeHookRuntime, message: dict[str, Any], *, stream_output: bool = False) -> str:
    session_id = str(message.get("session_id") or "default")
    state: dict[str, Any] = {"_runtime_workspace": "/workspace"}
    for hook_state in reversed(await runtime.call_many_reversed("load_state", message=message, session_id=session_id)):
        if isinstance(hook_state, dict):
            state.update(hook_state)
    prompt = await runtime.call_first("build_prompt", message=message, session_id=session_id, state=state)
    if not stream_output:
        return str(await runtime.run_model(prompt=prompt, session_id=session_id, state=state))

    stream = await runtime.run_model_stream(prompt=prompt, session_id=session_id, state=state)
    text = ""
    async for event in stream:
        if event.kind == "text":
            text += str(event.data.get("delta", ""))
    return text


def _stream_text(text: str) -> FakeAsyncStreamEvents:
    async def iterator():
        midpoint = max(1, len(text) // 2)
        yield FakeStreamEvent("text", {"delta": text[:midpoint]})
        yield FakeStreamEvent("text", {"delta": text[midpoint:]})
        yield FakeStreamEvent("final", {"text": text, "ok": True})

    return FakeAsyncStreamEvents(iterator())


def _kwargs_for(fn: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    names = getattr(getattr(fn, "__code__", None), "co_varnames", ())
    return {name: kwargs[name] for name in names if name in kwargs}


async def main() -> None:
    builtin = FakeBuiltinPlugin()
    codex = FakeBubCodexPlugin()
    runtime = FakeHookRuntime([("builtin", builtin), ("bub-codex", codex)])

    plain = await process_inbound(runtime, {"session_id": "s1", "content": "hello"})
    streaming = await process_inbound(runtime, {"session_id": "s2", "content": "stream"}, stream_output=True)
    command = await process_inbound(runtime, {"session_id": "s3", "content": ",tape.info"})

    assert plain == "codex:hello"
    assert streaming == "codex:stream"
    assert command == "command:,tape.info"
    assert [call["workspace"] for call in codex.calls] == ["/workspace", "/workspace", "/workspace"]
    assert all(call["has_agent"] for call in codex.calls)

    print(
        {
            "plain": plain,
            "streaming": streaming,
            "command": command,
            "codex_calls": codex.calls,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
