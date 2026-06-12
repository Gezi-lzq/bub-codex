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
from bub_codex.runtime import BubCodexRuntime  # noqa: E402
from bub_codex.tape_store import InMemoryTapeStore  # noqa: E402
from codex_record_builders import (  # noqa: E402
    agent_message_delta,
    agent_message_completed,
    command_execution_completed,
    command_execution_started,
    context_compaction_completed,
    turn_completed,
    turn_started,
)
from plugin_stream_helpers import run_plugin_stream_once  # noqa: E402


@dataclass(slots=True)
class FakeMaterializingThreadService:
    created: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    fail_resume: bool = False

    def materialize_thread(self, *, cwd: str, anchor_id: str, materialized_context: str) -> ThreadMaterialization:
        thread_id = f"codex-thread-{len(self.created) + 1}"
        self.created.append(thread_id)
        return ThreadMaterialization(thread_id=thread_id)

    def resume_thread(self, thread_id: str) -> None:
        if self.fail_resume:
            raise RuntimeError(f"cannot resume {thread_id}")
        self.resumed.append(thread_id)


class FakeTurnStreamService:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def start_turn_stream(self, *, thread_id: str, cwd: str, prompt: str):
        self.prompts.append(prompt)

        def records():
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

        return StreamRecordsTurnSession(records())


@dataclass(slots=True)
class StreamRecordsTurnSession:
    iterator: Any
    closed: bool = False

    def records(self):
        return self.iterator

    def close(self) -> None:
        close = getattr(self.iterator, "close", None)
        if callable(close):
            close()
        self.closed = True


class DeltaTurnStreamService:
    def start_turn_stream(self, *, thread_id: str, cwd: str, prompt: str):
        def records():
            turn_id = "user-turn-1"
            yield turn_started(thread_id=thread_id, turn_id=turn_id)
            yield agent_message_delta(
                thread_id=thread_id,
                turn_id=turn_id,
                item_id="msg-final-1",
                delta="Hel",
                phase="final_answer",
            )
            yield agent_message_delta(
                thread_id=thread_id,
                turn_id=turn_id,
                item_id="msg-final-1",
                delta="lo.",
                phase="final_answer",
            )
            yield agent_message_completed(
                thread_id=thread_id,
                turn_id=turn_id,
                item_id="msg-final-1",
                text="Hello.",
                phase="final_answer",
            )
            yield turn_completed(thread_id=thread_id, turn_id=turn_id)

        return StreamRecordsTurnSession(records())


class ForeignThreadTurnStreamService:
    def start_turn_stream(self, *, thread_id: str, cwd: str, prompt: str):
        def records():
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

        return StreamRecordsTurnSession(records())


class FailingTurnStreamService:
    def start_turn_stream(self, *, thread_id: str, cwd: str, prompt: str):
        def records():
            yield turn_started(thread_id=thread_id, turn_id="user-turn-1")
            raise RuntimeError("codex stream stopped")

        return StreamRecordsTurnSession(records())


@dataclass(slots=True)
class FailingMaterializationThreadService:
    def materialize_thread(self, *, cwd: str, anchor_id: str, materialized_context: str) -> ThreadMaterialization:
        raise RuntimeError("codex materialization failed")

    def resume_thread(self, thread_id: str) -> None:
        raise AssertionError("not used")


@dataclass(slots=True)
class ClosableTurnSession:
    closed: bool = False

    def records(self):
        yield turn_started(thread_id="codex-thread-1", turn_id="user-turn-1")
        yield agent_message_delta(
            thread_id="codex-thread-1",
            turn_id="user-turn-1",
            item_id="msg-final-1",
            delta="partial",
            phase="final_answer",
        )

    def close(self) -> None:
        self.closed = True


@dataclass(slots=True)
class SessionTurnStreamService:
    session: ClosableTurnSession = field(default_factory=ClosableTurnSession)

    def start_turn_stream(self, *, thread_id: str, cwd: str, prompt: str):
        return self.session


class LiveStreamTest(unittest.TestCase):
    def test_live_bridge_writes_commentary_to_tape_but_only_streams_final_answer(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FakeMaterializingThreadService())
            live = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, FakeTurnStreamService())
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

    def test_live_bridge_wraps_startup_context_on_created_thread_prompt(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FakeMaterializingThreadService())
            stream_service = FakeTurnStreamService()
            live = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, stream_service)
            await run_plugin_stream_once(
                live,
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )
            return stream_service

        stream_service = asyncio.run(run())
        prompt = stream_service.prompts[0]

        self.assertIn("Startup context:\n", prompt)
        self.assertIn('"workspace_metadata"', prompt)
        self.assertTrue(prompt.endswith("\n\nUser message:\nhello"))
        self.assertNotIn("Anchor", prompt)
        self.assertNotIn("materialized", prompt)
        self.assertNotIn("tape", prompt)
        self.assertNotIn("thread", prompt.lower())

    def test_live_bridge_streams_final_answer_delta_without_duplicate_completed_text(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FakeMaterializingThreadService())
            live = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, DeltaTurnStreamService())
            return await run_plugin_stream_once(
                live,
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )

        result = asyncio.run(run())

        self.assertEqual(result.text, "Hello.")
        self.assertEqual(result.final_text, "Hello.")
        self.assertEqual(
            [(event.kind, event.data) for event in result.stream_events if event.kind == "text"],
            [
                ("text", {"delta": "Hel"}),
                ("text", {"delta": "lo."}),
            ],
        )
        assistant_events = [
            event for event in result.tape_events if event.type == "codex.assistant_message.completed"
        ]
        self.assertEqual(len(assistant_events), 1)
        self.assertEqual(assistant_events[0].payload["assistant_text"], "Hello.")

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
            stream_service = FakeTurnStreamService()
            live = BubCodexLiveRuntimeStreamService(
                runtime.context_kernel,
                store,
                stream_service,
                tape_id_factory=_test_tape_id_factory,
            )
            result = await run_plugin_stream_once(
                live,
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )
            return result, threads, stream_service

        result, threads, stream_service = asyncio.run(run())

        self.assertEqual(threads.created, [])
        self.assertEqual(threads.resumed, ["codex-thread-existing"])
        self.assertEqual(result.final_text, "Final answer.")
        self.assertEqual(stream_service.prompts, ["hello"])
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
            live = BubCodexLiveRuntimeStreamService(
                runtime.context_kernel,
                store,
                FakeTurnStreamService(),
                tape_id_factory=_test_tape_id_factory,
            )
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
            live = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, FakeTurnStreamService())
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
        self.assertLess(event_types.index("bub.context.materialized"), event_types.index("codex.thread.bound"))

    def test_live_bridge_projects_compaction_notification_to_anchor(self) -> None:
        class CompactingTurnStreamService(FakeTurnStreamService):
            def start_turn_stream(self, *, thread_id: str, cwd: str, prompt: str):
                def records():
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

                return StreamRecordsTurnSession(records())

        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FakeMaterializingThreadService())
            live = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, CompactingTurnStreamService())
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
        compact_binding = [
            event
            for event in result.tape_events
            if event.type == "codex.thread.bound" and event.payload.get("reason") == "compact_continuity"
        ]
        self.assertEqual(len(compact_binding), 1)
        self.assertEqual(compact_binding[0].anchor_id, compact_anchor[0].anchor_id)
        self.assertEqual(compact_binding[0].thread_id, compact_anchor[0].thread_id)

    def test_live_bridge_resumes_same_thread_after_compact_anchor(self) -> None:
        class CompactingTurnStreamService(FakeTurnStreamService):
            def start_turn_stream(self, *, thread_id: str, cwd: str, prompt: str):
                def records():
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

                return StreamRecordsTurnSession(records())

        async def run():
            store = InMemoryTapeStore()
            threads = FakeMaterializingThreadService()
            runtime = BubCodexRuntime(store, threads)
            first = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, CompactingTurnStreamService())
            await run_plugin_stream_once(
                first,
                prompt="compact",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )
            second = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, FakeTurnStreamService())
            result = await run_plugin_stream_once(
                second,
                prompt="after compact",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )
            return result, threads

        result, threads = asyncio.run(run())

        self.assertEqual(threads.created, ["codex-thread-1"])
        self.assertIn("codex-thread-1", threads.resumed)
        self.assertEqual(result.final_text, "Final answer.")

    def test_live_bridge_surfaces_materialization_failure_root_cause(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FailingMaterializationThreadService())
            live = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, FakeTurnStreamService())
            return await run_plugin_stream_once(
                live,
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
                tape_store=store,
            )

        result = asyncio.run(run())

        self.assertEqual([event.kind for event in result.stream_events], ["error", "text", "final"])
        self.assertIn("codex materialization failed", result.final_text or "")
        self.assertEqual(result.tape_events[-1].type, "codex.thread.bind.failed")

    def test_live_bridge_closes_turn_session_when_consumer_stops_early(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FakeMaterializingThreadService())
            stream_service = SessionTurnStreamService()
            live = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, stream_service)
            stream = await live.run_stream(
                prompt="hello",
                session_id="s1",
                state={"_runtime_workspace": "/workspace"},
            )
            iterator = stream.__aiter__()
            await iterator.__anext__()
            await iterator.aclose()
            return stream_service.session

        session = asyncio.run(run())

        self.assertTrue(session.closed)

    def test_live_bridge_filters_foreign_thread_notifications(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FakeMaterializingThreadService())
            live = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, ForeignThreadTurnStreamService())
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
            live = BubCodexLiveRuntimeStreamService(runtime.context_kernel, store, FailingTurnStreamService())
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


def _test_tape_id_factory(session_id, state):
    return session_id
