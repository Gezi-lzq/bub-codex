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

from codex_record_builders import agent_message_completed, context_compaction_completed  # noqa: E402
from bub_codex.turn_translator import CodexTurnTranslator  # noqa: E402


class CodexTurnTranslatorTest(unittest.TestCase):
    def test_commentary_writes_tape_without_stream_text(self) -> None:
        translator = _translator()

        result = translator.accept(
            agent_message_completed(
                text="I will inspect the workspace.",
                phase="commentary",
            )
        )

        self.assertEqual(
            [event.type for event in result.tape_events],
            ["codex.assistant_message.completed"],
        )
        self.assertEqual(result.tape_events[0].payload["phase"], "commentary")
        self.assertEqual(result.stream_decisions, ())

    def test_final_answer_writes_tape_and_streams_text(self) -> None:
        translator = _translator()

        result = translator.accept(agent_message_completed(text="Done.", phase="final_answer"))
        finish = translator.finish()

        self.assertEqual(
            [event.type for event in result.tape_events],
            ["codex.assistant_message.completed"],
        )
        self.assertEqual(
            [(decision.kind, decision.data) for decision in result.stream_decisions],
            [("text", {"delta": "Done."})],
        )
        self.assertEqual(
            [(decision.kind, decision.data) for decision in finish.stream_decisions],
            [("final", {"text": "Done.", "ok": True})],
        )

    def test_finish_uses_last_assistant_message_when_no_final_answer_exists(self) -> None:
        translator = _translator()

        translator.accept(agent_message_completed(text="First commentary.", phase="commentary"))
        translator.accept(agent_message_completed(text="Last commentary.", phase="commentary"))
        finish = translator.finish()

        self.assertEqual(
            [(decision.kind, decision.data) for decision in finish.stream_decisions],
            [
                ("text", {"delta": "Last commentary."}),
                ("final", {"text": "Last commentary.", "ok": True}),
            ],
        )

    def test_context_compaction_creates_compact_anchor_events(self) -> None:
        translator = _translator()

        result = translator.accept(context_compaction_completed())

        event_types = [event.type for event in result.tape_events]
        compact_anchors = [
            event
            for event in result.tape_events
            if event.type == "bub.anchor.created" and event.payload.get("method") == "compact"
        ]

        self.assertIn("codex.thread.compacted", event_types)
        self.assertEqual(len(compact_anchors), 1)
        self.assertEqual(compact_anchors[0].payload["reason"], "auto_compact")


def _translator() -> CodexTurnTranslator:
    return CodexTurnTranslator(
        session_id="session-1",
        tape_id="tape-1",
        anchor_id="anchor-1",
    )


if __name__ == "__main__":
    unittest.main()
