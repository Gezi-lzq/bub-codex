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

from bub_codex.runtime_adapter import record_turn_id  # noqa: E402
from codex_record_builders import agent_message_completed  # noqa: E402


class RuntimeAdapterTest(unittest.TestCase):
    def test_record_turn_id_prefers_payload_record_then_default(self) -> None:
        self.assertEqual(
            record_turn_id(
                {
                    "method": "item/agentMessage/delta",
                    "payload": {"threadId": "thread-1", "itemId": "message-1", "delta": "hi"},
                    "turn_id": "default-turn",
                }
            ),
            "default-turn",
        )
        self.assertEqual(
            record_turn_id(
                {
                    "method": "item/agentMessage/delta",
                    "payload": {"threadId": "thread-1", "turnId": "payload-turn", "itemId": "message-2", "delta": "hi"},
                    "turn_id": "record-turn",
                }
            ),
            "payload-turn",
        )
        self.assertEqual(
            record_turn_id(
                {
                    **agent_message_completed(
                        text="done",
                        phase="final_answer",
                        thread_id="thread-1",
                        turn_id="nested-turn",
                        item_id="message-3",
                    ),
                    "turn_id": "record-turn",
                }
            ),
            "nested-turn",
        )


if __name__ == "__main__":
    unittest.main()
