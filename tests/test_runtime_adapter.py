from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from bub_codex.runtime_adapter import facts_from_notification_records  # noqa: E402
from codex_record_builders import agent_message_completed  # noqa: E402


class RuntimeAdapterTest(unittest.TestCase):
    def test_notification_records_resolve_turn_id_from_payload_record_then_default(self) -> None:
        records = (
            {
                "method": "item/agentMessage/delta",
                "payload": {"threadId": "thread-1", "itemId": "message-1", "delta": "hi"},
            },
            {
                "method": "item/agentMessage/delta",
                "payload": {"threadId": "thread-1", "itemId": "message-2", "delta": "there"},
                "turn_id": "record-turn",
            },
            {
                **agent_message_completed(
                    text="done",
                    phase="final_answer",
                    thread_id="thread-1",
                    turn_id="payload-turn",
                    item_id="message-3",
                ),
                "turn_id": "record-turn",
            },
        )

        facts = facts_from_notification_records(
            records,
            source="sdk_stream:test",
            turn_id="default-turn",
        )

        self.assertEqual(
            [fact.kind for fact in facts],
            [
                "codex.assistant_message.delta",
                "codex.assistant_message.delta",
                "codex.item.completed",
                "codex.assistant_message.completed",
            ],
        )
        self.assertEqual(facts[0].turn_id, "default-turn")
        self.assertEqual(facts[1].turn_id, "record-turn")
        self.assertEqual(facts[2].turn_id, "payload-turn")
        self.assertEqual(facts[3].turn_id, "payload-turn")


if __name__ == "__main__":
    unittest.main()
