#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from republic import AsyncStreamEvents, StreamEvent, StreamState

from bub.channels.message import ChannelMessage
from bub.framework import BubFramework
from bub.hookspecs import hookimpl


@dataclass
class FakeBuiltinAgent:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def run(self, *, session_id: str, prompt: str | list[dict], state: dict[str, Any]) -> str:
        self.calls.append(
            {
                "session_id": session_id,
                "prompt": prompt,
                "workspace": state.get("_runtime_workspace"),
            }
        )
        return f"command:{prompt}"


class FakeBubCodexPlugin:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.command_agent = FakeBuiltinAgent()

    @hookimpl
    async def load_state(self, message, session_id):
        # Production bub-codex should not own load_state. This spike only injects
        # a fake builtin agent to keep comma-command fallback offline.
        return {"_runtime_agent": self.command_agent}

    @hookimpl
    async def run_model_stream(self, prompt, session_id, state):
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


def _stream_text(text: str) -> AsyncStreamEvents:
    async def iterator():
        midpoint = max(1, len(text) // 2)
        yield StreamEvent("text", {"delta": text[:midpoint]})
        yield StreamEvent("text", {"delta": text[midpoint:]})
        yield StreamEvent("final", {"text": text, "ok": True})

    return AsyncStreamEvents(iterator(), state=StreamState())


async def main() -> None:
    framework = BubFramework()
    framework._load_builtin_hooks()
    plugin = FakeBubCodexPlugin()
    framework._plugin_manager.register(plugin, name="bub-codex-fake")

    plain = await framework.process_inbound(_message("s1", "hello"))
    streaming = await framework.process_inbound(_message("s2", "stream"), stream_output=True)
    command = await framework.process_inbound(_message("s3", ",tape.info"))

    assert plain.model_output.startswith("codex:channel=$cli|chat_id=s1\n---Date: ")
    assert plain.model_output.endswith("\nhello")
    assert streaming.model_output.startswith("codex:channel=$cli|chat_id=s2\n---Date: ")
    assert streaming.model_output.endswith("\nstream")
    assert command.model_output == "command:,tape.info"
    assert plugin.command_agent.calls == [
        {"session_id": "s3", "prompt": ",tape.info", "workspace": str(framework.workspace)}
    ]
    assert [call["workspace"] for call in plugin.calls] == [str(framework.workspace)] * 3
    assert all(call["has_agent"] for call in plugin.calls)
    assert framework.hook_report()["run_model_stream"][0] == "builtin"
    assert framework.hook_report()["run_model_stream"][-1] == "bub-codex-fake"

    print(
        {
            "plain": plain.model_output,
            "streaming": streaming.model_output,
            "command": command.model_output,
            "codex_calls": plugin.calls,
            "command_agent_calls": plugin.command_agent.calls,
            "run_model_stream_hooks": framework.hook_report()["run_model_stream"],
        }
    )


def _message(session_id: str, content: str) -> ChannelMessage:
    return ChannelMessage(session_id=session_id, channel="cli", chat_id=session_id, content=content)


if __name__ == "__main__":
    asyncio.run(main())
