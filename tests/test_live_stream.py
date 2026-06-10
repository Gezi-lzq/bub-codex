from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex import (  # noqa: E402
    BubCodexLiveRuntimeStreamService,
    BubCodexRuntime,
    InMemoryTapeStore,
    ThreadMaterialization,
    run_plugin_stream_once,
)


@dataclass(slots=True)
class FakeMaterializingThreadService:
    created: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> ThreadMaterialization:
        thread_id = f"codex-thread-{len(self.created) + 1}"
        self.created.append(thread_id)
        return ThreadMaterialization(
            thread_id=thread_id,
            turn_id="materialization-turn-1",
            notification_records=(
                {
                    "method": "turn/started",
                    "payload": {"threadId": thread_id, "turn": {"id": "materialization-turn-1"}},
                },
                {
                    "method": "turn/completed",
                    "payload": {"threadId": thread_id, "turn": {"id": "materialization-turn-1"}},
                },
            ),
        )

    def resume_thread(self, thread_id: str) -> None:
        self.resumed.append(thread_id)


class FakeTurnStreamService:
    def run_turn_stream_records(self, *, thread_id: str, cwd: str, prompt: str):
        turn_id = "user-turn-1"
        yield {"method": "turn/started", "payload": {"threadId": thread_id, "turn": {"id": turn_id}}}
        yield _agent_message(
            thread_id=thread_id,
            turn_id=turn_id,
            item_id="msg-commentary-1",
            text="I will inspect the workspace.",
            phase="commentary",
        )
        yield {
            "method": "item/started",
            "payload": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "type": "commandExecution",
                    "id": "call-1",
                    "command": "pwd",
                    "cwd": cwd,
                    "status": "inProgress",
                    "source": "model",
                    "commandActions": [{"type": "unknown", "command": "pwd"}],
                    "aggregatedOutput": None,
                    "exitCode": None,
                    "durationMs": None,
                },
            },
        }
        yield {
            "method": "item/completed",
            "payload": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "type": "commandExecution",
                    "id": "call-1",
                    "command": "pwd",
                    "cwd": cwd,
                    "status": "completed",
                    "source": "model",
                    "commandActions": [{"type": "unknown", "command": "pwd"}],
                    "aggregatedOutput": f"{cwd}\n",
                    "exitCode": 0,
                    "durationMs": 1,
                },
            },
        }
        yield _agent_message(
            thread_id=thread_id,
            turn_id=turn_id,
            item_id="msg-final-1",
            text="Final answer.",
            phase="final_answer",
        )
        yield {"method": "turn/completed", "payload": {"threadId": thread_id, "turn": {"id": turn_id}}}


def _agent_message(*, thread_id: str, turn_id: str, item_id: str, text: str, phase: str) -> dict[str, Any]:
    return {
        "method": "item/completed",
        "payload": {
            "threadId": thread_id,
            "turnId": turn_id,
            "item": {
                "type": "agentMessage",
                "id": item_id,
                "text": text,
                "phase": phase,
                "memoryCitation": None,
            },
        },
    }


class LiveStreamTest(unittest.TestCase):
    def test_live_bridge_writes_commentary_to_tape_but_only_streams_final_answer(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FakeMaterializingThreadService())
            live = BubCodexLiveRuntimeStreamService(runtime, FakeTurnStreamService())
            return await run_plugin_stream_once(
                live,
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )

        result = asyncio.run(run())

        self.assertEqual(result.text, "Final answer.")
        self.assertEqual(result.final_text, "Final answer.")
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
        assistant_events = [
            event for event in result.tape_events if event.type == "codex.assistant_message.completed"
        ]
        self.assertEqual([event.payload["phase"] for event in assistant_events], ["commentary", "final_answer"])

    def test_live_bridge_resumes_thread_from_tape_binding(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            anchor = _anchor_event(session_id="s1", tape_id="s1", anchor_id="anchor-existing")
            binding = _thread_bound_event(
                session_id="s1",
                tape_id="s1",
                anchor_id="anchor-existing",
                thread_id="codex-thread-existing",
            )
            store.append_many([anchor, binding])
            threads = FakeMaterializingThreadService()
            runtime = BubCodexRuntime(store, threads)
            live = BubCodexLiveRuntimeStreamService(runtime, FakeTurnStreamService())
            result = await run_plugin_stream_once(
                live,
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )
            return result, threads

        result, threads = asyncio.run(run())

        self.assertEqual(threads.created, [])
        self.assertEqual(threads.resumed, ["codex-thread-existing"])
        self.assertEqual(result.final_text, "Final answer.")
        self.assertEqual(
            [event.type for event in result.tape_events[:2]],
            ["bub.anchor.created", "codex.thread.bound"],
        )

    def test_live_bridge_projects_compaction_notification_to_anchor(self) -> None:
        class CompactingTurnStreamService(FakeTurnStreamService):
            def run_turn_stream_records(self, *, thread_id: str, cwd: str, prompt: str):
                yield {"method": "turn/started", "payload": {"threadId": thread_id, "turn": {"id": "turn-compact"}}}
                yield {
                    "method": "item/completed",
                    "payload": {
                        "threadId": thread_id,
                        "turnId": "turn-compact",
                        "item": {
                            "type": "contextCompaction",
                            "id": "compact-1",
                            "status": "completed",
                        },
                    },
                }
                yield _agent_message(
                    thread_id=thread_id,
                    turn_id="turn-compact",
                    item_id="msg-final-compact",
                    text="Compacted.",
                    phase="final_answer",
                )
                yield {"method": "turn/completed", "payload": {"threadId": thread_id, "turn": {"id": "turn-compact"}}}

        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FakeMaterializingThreadService())
            live = BubCodexLiveRuntimeStreamService(runtime, CompactingTurnStreamService())
            return await run_plugin_stream_once(
                live,
                prompt="compact",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )

        result = asyncio.run(run())
        event_types = [event.type for event in result.tape_events]
        compact_anchor = [
            event
            for event in result.tape_events
            if event.type == "bub.anchor.created" and event.payload.get("method") == "compact"
        ]

        self.assertIn("codex.thread.compacted", event_types)
        self.assertEqual(len(compact_anchor), 1)
        self.assertEqual(compact_anchor[0].payload["reason"], "auto_compact")


if __name__ == "__main__":
    unittest.main()


def _anchor_event(*, session_id: str, tape_id: str, anchor_id: str):
    from bub_codex import make_tape_event

    return make_tape_event(
        "bub.anchor.created",
        payload={"anchor_id": anchor_id, "method": "new_thread"},
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
    )


def _thread_bound_event(*, session_id: str, tape_id: str, anchor_id: str, thread_id: str):
    from bub_codex import make_tape_event

    return make_tape_event(
        "codex.thread.bound",
        payload={"anchor_id": anchor_id, "thread_id": thread_id},
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=thread_id,
    )
