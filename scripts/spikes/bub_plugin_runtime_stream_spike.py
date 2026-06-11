#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub.channels.message import ChannelMessage
from bub.framework import BubFramework
from bub.hookspecs import hookimpl

from bub_codex.codex_thread_service import CodexTurn, ThreadMaterialization
from bub_codex.plugin import BubCodexPlugin
from bub_codex.runtime_services import BubCodexRuntimeStreamService
from bub_codex.runtime import BubCodexRuntime
from bub_codex.tape_store import InMemoryTapeStore


@dataclass(slots=True)
class FakeCodexThreadService:
    created: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> ThreadMaterialization:
        thread_id = f"codex-thread-{len(self.created) + 1}"
        turn_id = f"codex-materialization-turn-{len(self.created) + 1}"
        self.created.append(thread_id)
        return ThreadMaterialization(thread_id=thread_id, turn_id=turn_id)

    def resume_thread(self, thread_id: str) -> None:
        self.resumed.append(thread_id)

    def run_turn(self, *, thread_id: str, cwd: str, prompt: str) -> CodexTurn:
        self.prompts.append(prompt)
        turn_id = f"codex-user-turn-{len(self.prompts)}"
        return CodexTurn(
            thread_id=thread_id,
            turn_id=turn_id,
            notification_records=(
                {
                    "method": "turn/started",
                    "payload": {"threadId": thread_id, "turn": {"id": turn_id}},
                },
                {
                    "method": "item/completed",
                    "payload": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "type": "agentMessage",
                            "id": f"assistant-message-{len(self.prompts)}",
                            "text": f"assistant:{prompt}",
                            "phase": "final_answer",
                            "memoryCitation": None,
                        },
                    },
                },
                {
                    "method": "turn/completed",
                    "payload": {"threadId": thread_id, "turn": {"id": turn_id}},
                },
            ),
        )


@dataclass
class FakeBuiltinAgent:
    calls: list[dict] = field(default_factory=list)

    async def run(self, *, session_id: str, prompt: str | list[dict], state: dict) -> str:
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
        return {"_runtime_agent": self.agent}


async def main() -> None:
    store = InMemoryTapeStore()
    threads = FakeCodexThreadService()
    runtime = BubCodexRuntime(store, threads)
    stream_service = BubCodexRuntimeStreamService(runtime)

    framework = BubFramework()
    framework._load_builtin_hooks()
    command_agent = FakeBuiltinAgent()
    framework._plugin_manager.register(TestStatePlugin(command_agent), name="test-state")
    framework._plugin_manager.register(BubCodexPlugin(runtime=stream_service), name="bub-codex")

    first = await framework.process_inbound(_message("s1", "hello"))
    second = await framework.process_inbound(_message("s1", "again"), stream_output=True)
    command = await framework.process_inbound(_message("s1", ",tape.info"))

    assert first.model_output == f"assistant:{threads.prompts[0]}"
    assert second.model_output == f"assistant:{threads.prompts[1]}"
    assert command.model_output == "command:,tape.info"
    assert threads.created == ["codex-thread-1"]
    assert threads.resumed == ["codex-thread-1"]
    assert len(threads.prompts) == 2
    assert threads.prompts[0].endswith("\nhello")
    assert threads.prompts[1].endswith("\nagain")
    assert command_agent.calls == [
        {"session_id": "s1", "prompt": ",tape.info", "workspace": str(framework.workspace)}
    ]

    event_types = [event.type for event in store.events(session_id="s1", tape_id="s1")]
    assert event_types == [
        "bub.anchor.creation.started",
        "bub.anchor.created",
        "bub.context.materialized",
        "codex.thread.bound",
        "codex.turn.started",
        "codex.assistant_message.completed",
        "codex.turn.completed",
        "codex.turn.started",
        "codex.assistant_message.completed",
        "codex.turn.completed",
    ]

    print(
        {
            "first": first.model_output,
            "second": second.model_output,
            "command": command.model_output,
            "created": threads.created,
            "resumed": threads.resumed,
            "prompts": threads.prompts,
            "event_types": event_types,
        }
    )


def _message(session_id: str, content: str) -> ChannelMessage:
    return ChannelMessage(session_id=session_id, channel="cli", chat_id=session_id, content=content)


if __name__ == "__main__":
    asyncio.run(main())
