#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub.channels.message import ChannelMessage
from bub.framework import BubFramework
from bub.hookspecs import hookimpl

from bub_codex import BubCodexPlugin, stream_text


@dataclass
class FakeRuntimeStreamService:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def run_stream(self, *, prompt: str | list[dict], session_id: str, state: dict[str, Any]):
        self.calls.append(
            {
                "prompt": prompt,
                "session_id": session_id,
                "workspace": state.get("_runtime_workspace"),
                "has_agent": "_runtime_agent" in state,
            }
        )
        return stream_text(f"codex:{prompt}")


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


class TestStatePlugin:
    def __init__(self, agent: FakeBuiltinAgent) -> None:
        self.agent = agent

    @hookimpl
    async def load_state(self, message, session_id):
        # Production bub-codex should not own load_state. This spike injects a
        # fake builtin agent to keep comma-command fallback offline.
        return {"_runtime_agent": self.agent}


async def main() -> None:
    framework = BubFramework()
    framework._load_builtin_hooks()

    runtime = FakeRuntimeStreamService()
    command_agent = FakeBuiltinAgent()
    framework._plugin_manager.register(TestStatePlugin(command_agent), name="test-state")
    framework._plugin_manager.register(BubCodexPlugin(runtime=runtime), name="bub-codex")

    plain = await framework.process_inbound(_message("s1", "hello"))
    streaming = await framework.process_inbound(_message("s2", "stream"), stream_output=True)
    command = await framework.process_inbound(_message("s3", ",tape.info"))

    assert plain.model_output.startswith("codex:channel=$cli|chat_id=s1\n---Date: ")
    assert plain.model_output.endswith("\nhello")
    assert streaming.model_output.startswith("codex:channel=$cli|chat_id=s2\n---Date: ")
    assert streaming.model_output.endswith("\nstream")
    assert command.model_output == "command:,tape.info"
    assert [call["session_id"] for call in runtime.calls] == ["s1", "s2"]
    assert command_agent.calls == [
        {"session_id": "s3", "prompt": ",tape.info", "workspace": str(framework.workspace)}
    ]

    print(
        {
            "plain": plain.model_output,
            "streaming": streaming.model_output,
            "command": command.model_output,
            "runtime_calls": runtime.calls,
            "command_agent_calls": command_agent.calls,
            "run_model_stream_hooks": framework.hook_report()["run_model_stream"],
        }
    )


def _message(session_id: str, content: str) -> ChannelMessage:
    return ChannelMessage(session_id=session_id, channel="cli", chat_id=session_id, content=content)


if __name__ == "__main__":
    asyncio.run(main())
