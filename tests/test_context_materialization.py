from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.context_materialization import build_initial_input, materialize_thread_binding_events  # noqa: E402
from bub_codex.runtime import BubCodexRuntime  # noqa: E402
from bub_codex.tape_store import InMemoryTapeStore  # noqa: E402
from bub_codex.tape_events import make_tape_event  # noqa: E402


class ContextMaterializationTest(unittest.TestCase):
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

        events = materialize_thread_binding_events(
            [anchor],
            session_id="s1",
            tape_id="s1",
            thread_id="thread-1",
            intent=user_task,
        )

        materialized = events[0]
        self.assertEqual(materialized.type, "bub.context.materialized")
        self.assertIn("intent_sha256", materialized.payload)
        self.assertNotIn("input_preview", materialized.payload)
        self.assertNotIn(user_task, json.dumps(materialized.to_json(), ensure_ascii=False))

    def test_initial_input_contains_intent_ref_not_user_task_text(self) -> None:
        user_task = "implement the actual user request"
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

        materialized_input = build_initial_input(
            anchor=anchor,
            intent=user_task,
            selected_refs=[anchor.event_id],
            workspace_metadata={"cwd": "/workspace"},
        )

        parsed = json.loads(materialized_input)
        self.assertIn("current_intent_ref", parsed)
        self.assertNotIn("current_intent", parsed)
        self.assertNotIn(user_task, materialized_input)

    def test_runtime_passes_materialized_context_not_user_task_to_thread_service(self) -> None:
        user_task = "implement the actual user request"
        store = InMemoryTapeStore()
        thread_service = CapturingThreadService()
        runtime = BubCodexRuntime(store, thread_service)

        result = runtime.ensure_thread_context(
            session_id="s1",
            tape_id="s1",
            cwd="/workspace",
            intent=user_task,
        )

        self.assertEqual(result.thread_id, "thread-1")
        self.assertIsNotNone(thread_service.materialized_intent)
        assert thread_service.materialized_intent is not None
        parsed = json.loads(thread_service.materialized_intent)
        self.assertEqual(parsed["anchor"]["anchor_id"], result.anchor_id)
        self.assertIn("current_intent_ref", parsed)
        self.assertNotIn(user_task, thread_service.materialized_intent)


class CapturingThreadService:
    def __init__(self) -> None:
        self.materialized_intent: str | None = None

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> str:
        self.materialized_intent = intent
        return "thread-1"

    def resume_thread(self, thread_id: str) -> None:
        pass

    def run_turn(self, *, thread_id: str, cwd: str, prompt: str):
        raise AssertionError("not used")


if __name__ == "__main__":
    unittest.main()
