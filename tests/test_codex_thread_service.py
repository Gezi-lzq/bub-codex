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

from bub_codex.codex_thread_service import MaterializingCodexThreadService, _default_initial_prompt  # noqa: E402
from codex_record_builders import agent_message_completed, turn_completed, turn_started  # noqa: E402


class MaterializingCodexThreadServiceTest(unittest.TestCase):
    def test_default_materialization_prompt_includes_anchor_materialized_context(self) -> None:
        prompt = _default_initial_prompt("anchor-1", "anchor-derived context")

        self.assertIn("anchor-1", prompt)
        self.assertIn("Materialized context:", prompt)
        self.assertIn("anchor-derived context", prompt)
        self.assertIn("Do not answer or execute the user's task", prompt)
        self.assertNotIn("Intent:", prompt)

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

        records = list(
            service.run_turn_stream_records(
                thread_id="current-thread",
                cwd="/workspace",
                prompt="hello",
            )
        )

        self.assertEqual([record["method"] for record in records], ["turn/started", "item/completed", "turn/completed"])
        self.assertTrue(all(record["payload"]["threadId"] == "current-thread" for record in records))
        self.assertEqual(client.unregistered_turn_ids, ["turn-1"])

    def test_close_closes_underlying_codex_client_when_supported(self) -> None:
        client = FakeCodexClient([])
        service = MaterializingCodexThreadService(client, cwd="/workspace")

        service.close()

        self.assertTrue(client.closed)


class FakeCodexClient:
    def __init__(self, events):
        self.events = list(events)
        self.unregistered_turn_ids: list[str] = []
        self.closed = False

    def turn_start(self, thread_id: str, prompt: str, options):
        return SimpleNamespace(turn=SimpleNamespace(id="turn-1"))

    def next_turn_notification(self, turn_id: str):
        return self.events.pop(0)

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
