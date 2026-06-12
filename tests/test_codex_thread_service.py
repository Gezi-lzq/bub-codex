from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from bub_codex.codex_thread_service import BUB_CHANNEL_DEVELOPER_INSTRUCTIONS, MaterializingCodexThreadService  # noqa: E402
from bub_codex.codex_client import DynamicToolSpec  # noqa: E402
from codex_record_builders import agent_message_completed, turn_completed, turn_started  # noqa: E402


class MaterializingCodexThreadServiceTest(unittest.TestCase):
    def test_stream_records_ignore_foreign_thread_completed_without_ending_current_turn(self) -> None:
        client = FakeCodexClient(
            [
                _event(turn_started(thread_id="current-thread", turn_id="turn-1")),
                _event(turn_completed(thread_id="foreign-thread", turn_id="foreign-turn")),
                _event(
                    agent_message_completed(
                        thread_id="current-thread",
                        turn_id="turn-1",
                        phase="final_answer",
                        text="current final",
                    )
                ),
                _event(turn_completed(thread_id="current-thread", turn_id="turn-1")),
            ]
        )
        service = MaterializingCodexThreadService(client, cwd="/workspace")

        session = service.start_turn_stream(
            thread_id="current-thread",
            cwd="/workspace",
            prompt="hello",
        )
        try:
            records = list(session.records())
        finally:
            session.close()

        self.assertEqual([record["method"] for record in records], ["turn/started", "item/completed", "turn/completed"])
        self.assertTrue(all(record["payload"]["threadId"] == "current-thread" for record in records))
        self.assertEqual(client.unregistered_turn_ids, ["turn-1"])

    def test_turn_session_close_unregisters_notifications(self) -> None:
        client = FakeCodexClient(
            [
                _event(turn_started(thread_id="current-thread", turn_id="turn-1")),
            ]
        )
        service = MaterializingCodexThreadService(client, cwd="/workspace")

        session = service.start_turn_stream(
            thread_id="current-thread",
            cwd="/workspace",
            prompt="hello",
        )
        records = session.records()
        self.assertEqual(next(records)["method"], "turn/started")
        session.close()

        self.assertEqual(client.unregistered_turn_ids, ["turn-1"])

    def test_turn_session_steers_current_codex_turn(self) -> None:
        client = FakeCodexClient([])
        service = MaterializingCodexThreadService(client, cwd="/workspace")

        session = service.start_turn_stream(
            thread_id="current-thread",
            cwd="/workspace",
            prompt="hello",
        )
        session.steer("adjust course")

        self.assertEqual(client.turn_steer_calls, [("current-thread", "turn-1", "adjust course")])

    def test_close_closes_underlying_codex_client_when_supported(self) -> None:
        client = FakeCodexClient([])
        service = MaterializingCodexThreadService(client, cwd="/workspace")

        service.close()

        self.assertTrue(client.closed)

    def test_materialization_registers_dynamic_tools_on_thread_start(self) -> None:
        client = FakeCodexClient([])
        tool = DynamicToolSpec(
            namespace="bub",
            name="tape_handoff",
            description="Add a handoff anchor",
            input_schema={"type": "object", "properties": {}},
        )
        service = MaterializingCodexThreadService(client, cwd="/workspace", dynamic_tools=(tool,))

        materialization = service.materialize_thread(
            cwd="/workspace",
            anchor_id="anchor-1",
        )

        self.assertEqual(materialization.thread_id, "thread-1")
        self.assertIsNone(materialization.turn_id)
        self.assertEqual(materialization.notification_records, ())
        self.assertEqual(client.thread_start_options[0]["dynamicTools"][0]["namespace"], "bub")
        self.assertEqual(client.thread_start_options[0]["dynamicTools"][0]["name"], "tape_handoff")

    def test_materialization_adds_bub_channel_developer_instructions(self) -> None:
        client = FakeCodexClient([])
        service = MaterializingCodexThreadService(client, cwd="/workspace")

        service.materialize_thread(
            cwd="/workspace",
            anchor_id="anchor-1",
        )

        self.assertEqual(
            client.thread_start_options[0]["developerInstructions"],
            BUB_CHANNEL_DEVELOPER_INSTRUCTIONS,
        )

    def test_materialization_does_not_start_a_hidden_codex_turn(self) -> None:
        client = FakeCodexClient([])
        service = MaterializingCodexThreadService(client, cwd="/workspace")

        service.materialize_thread(
            cwd="/workspace",
            anchor_id="anchor-1",
        )

        self.assertEqual(client.turn_start_calls, [])
        self.assertEqual(client.unregistered_turn_ids, [])

    def test_resume_uses_only_verified_thread_resume_fields(self) -> None:
        client = FakeCodexClient([])
        tool = DynamicToolSpec(
            namespace="bub",
            name="tape_handoff",
            description="Add a handoff anchor",
            input_schema={"type": "object", "properties": {}},
        )
        service = MaterializingCodexThreadService(
            client,
            cwd="/workspace",
            approval_policy="never",
            sandbox="danger-full-access",
            dynamic_tools=(tool,),
        )

        service.resume_thread("thread-1")

        self.assertEqual(
            client.thread_resume_calls,
            [
                (
                    "thread-1",
                    {
                        "cwd": "/workspace",
                        "approvalPolicy": "never",
                        "sandbox": "danger-full-access",
                    },
                )
            ],
        )


class FakeCodexClient:
    def __init__(self, events):
        self.events = list(events)
        self.unregistered_turn_ids: list[str] = []
        self.thread_start_options: list[dict] = []
        self.thread_resume_calls: list[tuple[str, dict]] = []
        self.turn_start_calls: list[tuple[str, str, dict]] = []
        self.turn_steer_calls: list[tuple[str, str, str]] = []
        self.closed = False

    def thread_start(self, options):
        self.thread_start_options.append(options)
        return SimpleNamespace(thread=SimpleNamespace(id="thread-1"))

    def thread_resume(self, thread_id: str, params: dict):
        self.thread_resume_calls.append((thread_id, params))
        return SimpleNamespace(thread=SimpleNamespace(id=thread_id))

    def turn_start(self, thread_id: str, prompt: str, options):
        self.turn_start_calls.append((thread_id, prompt, options))
        return SimpleNamespace(turn=SimpleNamespace(id="turn-1"))

    def next_turn_notification(self, turn_id: str):
        return self.events.pop(0)

    def turn_steer(self, thread_id: str, expected_turn_id: str, input_items: str):
        self.turn_steer_calls.append((thread_id, expected_turn_id, input_items))
        return SimpleNamespace()

    def unregister_turn_notifications(self, turn_id: str) -> None:
        self.unregistered_turn_ids.append(turn_id)

    def close(self) -> None:
        self.closed = True


def _event(record):
    return SimpleNamespace(
        method=record["method"],
        payload=record["payload"],
    )


if __name__ == "__main__":
    unittest.main()
