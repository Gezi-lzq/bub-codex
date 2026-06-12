from __future__ import annotations

import json
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.codex_thread_service import ThreadMaterialization  # noqa: E402
from bub_codex.runtime import BubCodexRuntime  # noqa: E402
from bub_codex.startup_context import prompt_with_startup_context  # noqa: E402
from bub_codex.tape_store import InMemoryTapeStore  # noqa: E402
from bub_codex.tape_events import make_tape_event  # noqa: E402
from bub_codex.new_thread_materialization import (  # noqa: E402
    build_initial_input,
    create_new_thread_anchor_events,
    materialize_thread_binding_events,
    prepare_materialized_context,
)


class ContextMaterializationTest(unittest.TestCase):
    def test_anchor_creation_events_have_named_started_and_created_events(self) -> None:
        created = create_new_thread_anchor_events(
            [],
            session_id="s1",
            tape_id="s1",
            reason="session_start",
            intent="hello",
            owner="human",
            initiator="bub_runtime",
        )

        self.assertEqual(created.started.type, "bub.anchor.creation.started")
        self.assertEqual(created.created.type, "bub.anchor.created")
        self.assertEqual(
            created.created.payload["refs"]["source_anchor_creation_id"],
            created.started.payload["anchor_creation_id"],
        )

    def test_anchor_creation_accepts_one_shot_event_iterables(self) -> None:
        previous_anchor = make_tape_event(
            "bub.anchor.created",
            payload={"anchor_id": "anchor-1", "method": "new_thread", "state": {}, "refs": {}},
            session_id="s1",
            tape_id="s1",
            anchor_id="anchor-1",
        )
        previous_binding = make_tape_event(
            "codex.thread.bound",
            payload={"anchor_id": "anchor-1", "thread_id": "thread-1"},
            session_id="s1",
            tape_id="s1",
            anchor_id="anchor-1",
            thread_id="thread-1",
        )

        created = create_new_thread_anchor_events(
            (event for event in [previous_anchor, previous_binding]),
            session_id="s1",
            tape_id="s1",
            reason="handoff",
            intent="continue elsewhere",
        )

        self.assertEqual(created.started.payload["active_anchor_id_before"], "anchor-1")
        self.assertEqual(created.started.payload["active_thread_id_before"], "thread-1")
        self.assertEqual(created.created.payload["refs"]["previous_anchor_id"], "anchor-1")
        self.assertEqual(created.created.payload["refs"]["previous_thread_id"], "thread-1")

    def test_materialization_payload_references_intent_without_previewing_user_task(self) -> None:
        user_task = "read secrets.txt and summarize it"
        anchor = make_tape_event(
            "bub.anchor.created",
            payload={
                "anchor_id": "anchor-1",
                "method": "new_thread",
                "reason": "session_start",
                "state": {},
                "refs": {},
            },
            session_id="s1",
            tape_id="s1",
            anchor_id="anchor-1",
        )

        binding = materialize_thread_binding_events(
            [anchor],
            session_id="s1",
            tape_id="s1",
            thread_id="thread-1",
            intent=user_task,
            materialized_context=prepare_materialized_context(
                [anchor],
                intent=user_task,
                workspace_metadata={},
            ),
        )

        materialized = binding.materialized
        self.assertEqual(materialized.type, "bub.context.materialized")
        self.assertIn("intent_sha256", materialized.payload)
        self.assertNotIn("input_preview", materialized.payload)
        self.assertNotIn(user_task, json.dumps(materialized.to_json(), ensure_ascii=False))

    def test_materialization_event_hashes_the_startup_context(self) -> None:
        import hashlib

        user_task = "continue from the anchor"
        anchor = make_tape_event(
            "bub.anchor.created",
            payload={
                "anchor_id": "anchor-1",
                "method": "new_thread",
                "reason": "session_start",
                "state": {},
                "refs": {},
            },
            session_id="s1",
            tape_id="s1",
            anchor_id="anchor-1",
        )
        prepared = prepare_materialized_context(
            [anchor],
            intent=user_task,
            workspace_metadata={"cwd": "/workspace"},
        )

        binding = materialize_thread_binding_events(
            [anchor],
            session_id="s1",
            tape_id="s1",
            thread_id="thread-1",
            intent=user_task,
            materialized_context=prepared,
        )

        expected_hash = hashlib.sha256(prepared.text.encode("utf-8")).hexdigest()
        self.assertEqual(binding.materialization_id, binding.materialized.payload["materialization_id"])
        self.assertEqual(binding.materialization_id, binding.bound.payload["materialization_id"])
        self.assertEqual(binding.materialized.payload["input_sha256"], expected_hash)
        self.assertEqual(binding.materialized.payload["selected_fact_refs"], list(prepared.selected_refs))
        self.assertEqual(binding.materialized.payload["workspace_metadata"], {"cwd": "/workspace"})

    def test_initial_input_contains_only_model_visible_startup_context(self) -> None:
        user_task = "implement the actual user request"
        anchor = make_tape_event(
            "bub.anchor.created",
            payload={
                "anchor_id": "anchor-1",
                "method": "new_thread",
                "reason": "session_start",
                "state": {"summary": "continue the payment refactor"},
                "refs": {"previous_anchor_id": "anchor-old"},
            },
            session_id="s1",
            tape_id="s1",
            anchor_id="anchor-1",
        )

        materialized_input = build_initial_input(
            anchor=anchor,
            workspace_metadata={"cwd": "/workspace"},
        )

        parsed = json.loads(materialized_input)
        self.assertEqual(parsed, {"workspace_metadata": {"cwd": "/workspace"}, "handoff_summary": "continue the payment refactor"})
        self.assertNotIn(user_task, materialized_input)
        self.assertNotIn("anchor-1", materialized_input)
        self.assertNotIn(anchor.event_id, materialized_input)
        self.assertNotIn("previous_anchor_id", materialized_input)

    def test_runtime_prepares_startup_context_without_user_task_text(self) -> None:
        async def run():
            user_task = "implement the actual user request"
            store = InMemoryTapeStore()
            thread_service = CapturingThreadService()
            runtime = BubCodexRuntime(store, thread_service)
            result = await runtime.context_kernel.ensure_thread_context(
                session_id="s1",
                tape_id="s1",
                cwd="/workspace",
                intent=user_task,
            )
            return user_task, thread_service, result

        user_task, thread_service, result = asyncio.run(run())

        self.assertEqual(result.status, "created_anchor_and_materialized")
        self.assertEqual(result.thread_id, "thread-1")
        self.assertIsNotNone(result.startup_context)
        self.assertEqual(thread_service.materialize_calls, [("/workspace", str(result.anchor_id))])
        startup_context = result.startup_context or ""
        parsed = json.loads(startup_context)
        self.assertEqual(parsed, {"workspace_metadata": {"cwd": "/workspace"}})
        self.assertNotIn(str(result.anchor_id), startup_context)
        self.assertNotIn(user_task, startup_context)

    def test_startup_context_wraps_only_first_real_user_prompt(self) -> None:
        prompt = prompt_with_startup_context(
            prompt="hello",
            startup_context='{"workspace_metadata": {"cwd": "/workspace"}}',
        )

        self.assertEqual(
            prompt,
            'Startup context:\n{"workspace_metadata": {"cwd": "/workspace"}}\n\nUser message:\nhello',
        )
        self.assertNotIn("Anchor", prompt)
        self.assertNotIn("materialized", prompt)
        self.assertNotIn("tape", prompt)
        self.assertNotIn("thread", prompt.lower())

    def test_runtime_materializes_existing_unbound_anchor(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            await store.append(
                make_tape_event(
                    "bub.anchor.created",
                    payload={"anchor_id": "anchor-1", "method": "new_thread", "state": {}, "refs": {}},
                    session_id="s1",
                    tape_id="s1",
                    anchor_id="anchor-1",
                )
            )
            runtime = BubCodexRuntime(store, CapturingThreadService())
            return await runtime.context_kernel.ensure_thread_context(
                session_id="s1",
                tape_id="s1",
                cwd="/workspace",
                intent="continue",
            )

        result = asyncio.run(run())

        self.assertEqual(result.status, "materialized_existing_anchor")
        self.assertEqual(result.anchor_id, "anchor-1")
        self.assertEqual(result.thread_id, "thread-1")
        self.assertIsNotNone(result.startup_context)

    def test_runtime_resume_existing_thread_has_no_startup_context(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            await store.append_many(
                [
                    make_tape_event(
                        "bub.anchor.created",
                        payload={"anchor_id": "anchor-1", "method": "new_thread", "state": {}, "refs": {}},
                        session_id="s1",
                        tape_id="s1",
                        anchor_id="anchor-1",
                    ),
                    make_tape_event(
                        "codex.thread.bound",
                        payload={"anchor_id": "anchor-1", "thread_id": "thread-1"},
                        session_id="s1",
                        tape_id="s1",
                        anchor_id="anchor-1",
                        thread_id="thread-1",
                    ),
                ]
            )
            runtime = BubCodexRuntime(store, CapturingThreadService())
            return await runtime.context_kernel.ensure_thread_context(
                session_id="s1",
                tape_id="s1",
                cwd="/workspace",
                intent="continue",
            )

        result = asyncio.run(run())

        self.assertEqual(result.status, "resumed_existing_thread")
        self.assertEqual(result.thread_id, "thread-1")
        self.assertIsNone(result.startup_context)

    def test_runtime_returns_materialization_failed_state_without_thread_id(self) -> None:
        async def run():
            store = InMemoryTapeStore()
            runtime = BubCodexRuntime(store, FailingThreadService())
            return await runtime.context_kernel.ensure_thread_context(
                session_id="s1",
                tape_id="s1",
                cwd="/workspace",
                intent="hello",
            )

        result = asyncio.run(run())

        self.assertEqual(result.status, "materialization_failed")
        self.assertIsNone(result.thread_id)
        self.assertEqual(result.error, {"type": "RuntimeError", "message": "materialization failed"})


class CapturingThreadService:
    def __init__(self) -> None:
        self.materialize_calls: list[tuple[str, str]] = []

    def materialize_thread(self, *, cwd: str, anchor_id: str) -> ThreadMaterialization:
        self.materialize_calls.append((cwd, anchor_id))
        return ThreadMaterialization(thread_id="thread-1")

    def resume_thread(self, thread_id: str) -> None:
        pass

    def run_turn(self, *, thread_id: str, cwd: str, prompt: str):
        raise AssertionError("not used")


class FailingThreadService:
    def materialize_thread(self, *, cwd: str, anchor_id: str) -> ThreadMaterialization:
        raise RuntimeError("materialization failed")

    def resume_thread(self, thread_id: str) -> None:
        pass

    def run_turn(self, *, thread_id: str, cwd: str, prompt: str):
        raise AssertionError("not used")


if __name__ == "__main__":
    unittest.main()
