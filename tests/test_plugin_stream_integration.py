from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex import (  # noqa: E402
    BubCodexRuntime,
    BubCodexRuntimeStreamService,
    CodexTurn,
    InMemoryTapeStore,
    ThreadMaterialization,
    run_plugin_stream_once,
)


@dataclass(slots=True)
class FakeCodexThreadService:
    created: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> ThreadMaterialization:
        thread_id = f"codex-thread-{len(self.created) + 1}"
        turn_id = f"codex-materialization-turn-{len(self.created) + 1}"
        self.created.append(thread_id)
        return ThreadMaterialization(
            thread_id=thread_id,
            turn_id=turn_id,
            notification_records=(
                {
                    "method": "turn/started",
                    "payload": {"threadId": thread_id, "turn": {"id": turn_id}},
                },
                {
                    "method": "turn/completed",
                    "payload": {"threadId": thread_id, "turn": {"id": turn_id}},
                },
            ),
        )

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
                    "method": "item/started",
                    "payload": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "type": "commandExecution",
                            "id": "command-1",
                            "command": "pwd",
                            "cwd": cwd,
                            "source": "model",
                            "status": "inProgress",
                            "commandActions": [{"type": "unknown", "command": "pwd"}],
                            "aggregatedOutput": None,
                            "exitCode": None,
                            "durationMs": None,
                        },
                    },
                },
                {
                    "method": "item/completed",
                    "payload": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "type": "commandExecution",
                            "id": "command-1",
                            "command": "pwd",
                            "cwd": cwd,
                            "source": "model",
                            "status": "completed",
                            "commandActions": [{"type": "unknown", "command": "pwd"}],
                            "aggregatedOutput": f"{cwd}\n",
                            "exitCode": 0,
                            "durationMs": 1,
                        },
                    },
                },
                {
                    "method": "item/completed",
                    "payload": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "type": "agentMessage",
                            "id": f"assistant-message-final-{len(self.prompts)}",
                            "text": f"final:{prompt}",
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


class PluginStreamIntegrationTest(unittest.TestCase):
    def test_runtime_stream_outputs_assistant_message_and_records_tape(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            threads = FakeCodexThreadService()
            runtime = BubCodexRuntime(store, threads)
            stream_service = BubCodexRuntimeStreamService(runtime)
            result = await run_plugin_stream_once(
                stream_service,
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )
            return result, threads

        result, threads = asyncio.run(run())

        self.assertEqual(result.text, "assistant:hello\nfinal:hello")
        self.assertEqual(result.final_text, "assistant:hello\nfinal:hello")
        self.assertEqual(threads.created, ["codex-thread-1"])
        self.assertEqual(threads.prompts, ["hello"])
        self.assertEqual(
            [event.type for event in result.tape_events],
            [
                "bub.anchor.creation.started",
                "bub.anchor.created",
                "bub.context.materialized",
                "codex.turn.materialization.started",
                "codex.turn.materialization.completed",
                "codex.thread.bound",
                "codex.turn.started",
                "codex.assistant_message.completed",
                "bub.tool.call.started",
                "bub.tool.call.completed",
                "codex.assistant_message.completed",
                "codex.turn.completed",
            ],
        )


if __name__ == "__main__":
    unittest.main()
