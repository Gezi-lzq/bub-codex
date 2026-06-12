from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from bub_codex.codex_thread_service import CodexTurn, ThreadMaterialization  # noqa: E402
from bub_codex.runtime import BubCodexRuntime  # noqa: E402
from bub_codex.tape_store import InMemoryTapeStore  # noqa: E402
from codex_record_builders import (  # noqa: E402
    agent_message_completed,
    command_execution_completed,
    command_execution_started,
    turn_completed,
    turn_started,
)
from plugin_stream_helpers import BatchRuntimeStreamService, run_plugin_stream_once  # noqa: E402


@dataclass(slots=True)
class FakeCodexThreadService:
    created: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def materialize_thread(self, *, cwd: str, anchor_id: str) -> ThreadMaterialization:
        thread_id = f"codex-thread-{len(self.created) + 1}"
        self.created.append(thread_id)
        return ThreadMaterialization(thread_id=thread_id)

    def resume_thread(self, thread_id: str) -> None:
        self.resumed.append(thread_id)

    def run_turn(self, *, thread_id: str, cwd: str, prompt: str) -> CodexTurn:
        self.prompts.append(prompt)
        turn_id = f"codex-user-turn-{len(self.prompts)}"
        return CodexTurn(
            thread_id=thread_id,
            turn_id=turn_id,
            notification_records=(
                turn_started(thread_id=thread_id, turn_id=turn_id),
                agent_message_completed(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_id=f"assistant-message-{len(self.prompts)}",
                    text="commentary:received",
                    phase="commentary",
                ),
                command_execution_started(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_id="command-1",
                    command="pwd",
                    cwd=cwd,
                ),
                command_execution_completed(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_id="command-1",
                    command="pwd",
                    cwd=cwd,
                    output=f"{cwd}\n",
                ),
                agent_message_completed(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_id=f"assistant-message-final-{len(self.prompts)}",
                    text="final:received",
                    phase="final_answer",
                ),
                turn_completed(thread_id=thread_id, turn_id=turn_id),
            ),
        )


class PluginStreamIntegrationTest(unittest.TestCase):
    def test_runtime_stream_outputs_assistant_message_and_records_tape(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            threads = FakeCodexThreadService()
            runtime = BubCodexRuntime(store, threads)
            stream_service = BatchRuntimeStreamService(runtime)
            result = await run_plugin_stream_once(
                stream_service,
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )
            return result, threads

        result, threads = asyncio.run(run())

        self.assertEqual(result.text, "final:received")
        self.assertEqual(result.final_text, "final:received")
        self.assertEqual(threads.created, ["codex-thread-1"])
        self.assertEqual(len(threads.prompts), 1)
        self.assertIn("Startup context:\n", threads.prompts[0])
        self.assertTrue(threads.prompts[0].endswith("\n\nUser message:\nhello"))
        self.assertEqual(
            [event.type for event in result.tape_events],
            [
                "bub.anchor.creation.started",
                "bub.anchor.created",
                "bub.context.materialized",
                "codex.thread.bound",
                "codex.turn.started",
                "codex.assistant_message.completed",
                "bub.tool.call.started",
                "bub.tool.call.completed",
                "codex.assistant_message.completed",
                "codex.turn.completed",
            ],
        )

    def test_runtime_stream_sends_startup_context_only_once_per_bound_thread(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            threads = FakeCodexThreadService()
            runtime = BubCodexRuntime(store, threads)
            stream_service = BatchRuntimeStreamService(runtime)
            await run_plugin_stream_once(
                stream_service,
                prompt="first",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )
            await run_plugin_stream_once(
                stream_service,
                prompt="second",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )
            return threads

        threads = asyncio.run(run())

        self.assertEqual(len(threads.prompts), 2)
        self.assertIn("Startup context:\n", threads.prompts[0])
        self.assertTrue(threads.prompts[0].endswith("\n\nUser message:\nfirst"))
        self.assertEqual(threads.prompts[1], "second")


if __name__ == "__main__":
    unittest.main()
