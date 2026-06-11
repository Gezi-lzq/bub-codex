from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from bub_codex.codex_thread_service import ThreadMaterialization  # noqa: E402
from bub_codex.live_stream import BubCodexLiveRuntimeStreamService  # noqa: E402
from bub_codex.plugin_stream_integration import run_plugin_stream_once  # noqa: E402
from bub_codex.runtime import BubCodexRuntime  # noqa: E402
from bub_codex.tape_store import InMemoryTapeStore  # noqa: E402
from codex_record_builders import (  # noqa: E402
    agent_message_completed,
    command_execution_completed,
    command_execution_started,
    context_compaction_completed,
    turn_completed,
    turn_started,
)


@dataclass(slots=True)
class FakeMaterializingThreadService:
    created: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    fail_resume: bool = False

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> ThreadMaterialization:
        thread_id = f"codex-thread-{len(self.created) + 1}"
        self.created.append(thread_id)
        return ThreadMaterialization(
            thread_id=thread_id,
            turn_id="materialization-turn-1",
            notification_records=(
                turn_started(thread_id=thread_id, turn_id="materialization-turn-1"),
                turn_completed(thread_id=thread_id, turn_id="materialization-turn-1"),
            ),
        )

    def resume_thread(self, thread_id: str) -> None:
        if self.fail_resume:
            raise RuntimeError(f"cannot resume {thread_id}")
        self.resumed.append(thread_id)


class FakeTurnStreamService:
    def run_turn_stream_records(self, *, thread_id: str, cwd: str, prompt: str):
        turn_id = "user-turn-1"
        yield turn_started(thread_id=thread_id, turn_id=turn_id)
        yield agent_message_completed(
            thread_id=thread_id,
            turn_id=turn_id,
            item_id="msg-commentary-1",
            text="I will inspect the workspace.",
            phase="commentary",
        )
        yield command_execution_started(
            thread_id=thread_id,
            turn_id=turn_id,
            item_id="call-1",
            command="pwd",
            cwd=cwd,
        )
        yield command_execution_completed(
            thread_id=thread_id,
            turn_id=turn_id,
            item_id="call-1",
            command="pwd",
            cwd=cwd,
            output=f"{cwd}\n",
        )
        yield agent_message_completed(
            thread_id=thread_id,
            turn_id=turn_id,
            item_id="msg-final-1",
            text="Final answer.",
            phase="final_answer",
        )
        yield turn_completed(thread_id=thread_id, turn_id=turn_id)


class ForeignThreadTurnStreamService:
    def run_turn_stream_records(self, *, thread_id: str, cwd: str, prompt: str):
        yield turn_started(thread_id=thread_id, turn_id="user-turn-1")
        yield agent_message_completed(
            thread_id="foreign-thread",
            turn_id="foreign-turn",
            item_id="foreign-message",
            text="Foreign background message.",
            phase="final_answer",
        )
        yield command_execution_started(
            thread_id="foreign-thread",
            turn_id="foreign-turn",
            item_id="foreign-call",
            command="pwd",
            cwd=cwd,
        )
        yield agent_message_completed(
            thread_id=thread_id,
            turn_id="user-turn-1",
            item_id="current-final",
            text="Current thread final.",
            phase="final_answer",
        )
        yield turn_completed(thread_id=thread_id, turn_id="user-turn-1")


class FailingTurnStreamService:
    def run_turn_stream_records(self, *, thread_id: str, cwd: str, prompt: str):
        yield turn_started(thread_id=thread_id, turn_id="user-turn-1")
        raise RuntimeError("codex stream stopped")


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

    def test_live_bridge_surfaces_resume_failure_without_materializing_replacement(self) -> None:
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
            threads = FakeMaterializingThreadService(fail_resume=True)
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
        self.assertEqual(threads.resumed, [])
        self.assertEqual([event.kind for event in result.stream_events], ["error", "text", "final"])
        self.assertEqual(result.stream_events[0].data["message"], "cannot resume codex-thread-existing")
        self.assertEqual(result.final_text, "RuntimeError: cannot resume codex-thread-existing")
        self.assertFalse(result.stream_events[-1].data["ok"])
        self.assertEqual(
            [event.type for event in result.tape_events],
            ["bub.anchor.created", "codex.thread.bound", "bub.runtime.error"],
        )
        diagnostic = result.tape_events[-1]
        self.assertEqual(diagnostic.payload["stage"], "thread_resume")
        self.assertEqual(diagnostic.payload["error_type"], "RuntimeError")
        self.assertEqual(diagnostic.payload["message"], "cannot resume codex-thread-existing")
        self.assertEqual(diagnostic.thread_id, "codex-thread-existing")

    def test_live_bridge_materializes_thread_from_latest_anchor_without_binding(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            store.append_many(
                [
                    _anchor_event(
                        session_id="s1",
                        tape_id="s1",
                        anchor_id="anchor-without-binding",
                    )
                ]
            )
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

        self.assertEqual(threads.created, ["codex-thread-1"])
        self.assertEqual(threads.resumed, [])
        self.assertEqual(result.final_text, "Final answer.")
        event_types = [event.type for event in result.tape_events]
        self.assertEqual(event_types[0], "bub.anchor.created")
        self.assertIn("bub.context.materialized", event_types)
        self.assertIn("codex.thread.bound", event_types)
        self.assertLess(event_types.index("codex.turn.materialization.completed"), event_types.index("codex.thread.bound"))

    def test_live_bridge_projects_compaction_notification_to_anchor(self) -> None:
        class CompactingTurnStreamService(FakeTurnStreamService):
            def run_turn_stream_records(self, *, thread_id: str, cwd: str, prompt: str):
                yield turn_started(thread_id=thread_id, turn_id="turn-compact")
                yield context_compaction_completed(thread_id=thread_id, turn_id="turn-compact")
                yield agent_message_completed(
                    thread_id=thread_id,
                    turn_id="turn-compact",
                    item_id="msg-final-compact",
                    text="Compacted.",
                    phase="final_answer",
                )
                yield turn_completed(thread_id=thread_id, turn_id="turn-compact")

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

    def test_live_bridge_filters_foreign_thread_notifications(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FakeMaterializingThreadService())
            live = BubCodexLiveRuntimeStreamService(runtime, ForeignThreadTurnStreamService())
            return await run_plugin_stream_once(
                live,
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )

        result = asyncio.run(run())

        self.assertEqual(result.final_text, "Current thread final.")
        self.assertNotIn("Foreign background message.", result.text)
        self.assertFalse(any(event.thread_id == "foreign-thread" for event in result.tape_events))
        self.assertFalse(
            any(
                event.type.startswith("bub.tool.call")
                and event.payload.get("thread_id") == "foreign-thread"
                for event in result.tape_events
            )
        )

    def test_live_bridge_records_runtime_error_when_turn_stream_fails(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FakeMaterializingThreadService())
            live = BubCodexLiveRuntimeStreamService(runtime, FailingTurnStreamService())
            return await run_plugin_stream_once(
                live,
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )

        result = asyncio.run(run())
        diagnostic = result.tape_events[-1]

        self.assertEqual([event.kind for event in result.stream_events[-3:]], ["error", "text", "final"])
        self.assertEqual(result.final_text, "RuntimeError: codex stream stopped")
        self.assertFalse(result.stream_events[-1].data["ok"])
        self.assertEqual(diagnostic.type, "bub.runtime.error")
        self.assertEqual(diagnostic.payload["stage"], "turn_stream")
        self.assertEqual(diagnostic.payload["error_type"], "RuntimeError")
        self.assertEqual(diagnostic.payload["message"], "codex stream stopped")
        self.assertEqual(diagnostic.thread_id, "codex-thread-1")


if __name__ == "__main__":
    unittest.main()


def _anchor_event(*, session_id: str, tape_id: str, anchor_id: str):
    from bub_codex.tape_events import make_tape_event

    return make_tape_event(
        "bub.anchor.created",
        payload={"anchor_id": anchor_id, "method": "new_thread"},
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
    )


def _thread_bound_event(*, session_id: str, tape_id: str, anchor_id: str, thread_id: str):
    from bub_codex.tape_events import make_tape_event

    return make_tape_event(
        "codex.thread.bound",
        payload={"anchor_id": anchor_id, "thread_id": thread_id},
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=thread_id,
    )
