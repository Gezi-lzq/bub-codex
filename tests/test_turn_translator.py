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

from codex_record_builders import (  # noqa: E402
    agent_message_completed,
    agent_message_delta,
    context_compaction_completed,
    error_notification,
)
from bub_codex.tape_events import make_tape_event  # noqa: E402
from bub_codex.turn_translator import CodexTurnTranslator, stream_success_decisions_from_tape_events  # noqa: E402


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

    def test_final_answer_delta_streams_without_tape_and_completed_does_not_duplicate_text(self) -> None:
        translator = _translator()

        first = translator.accept(agent_message_delta(delta="Do", phase="final_answer"))
        second = translator.accept(agent_message_delta(delta="ne.", phase="final_answer"))
        completed = translator.accept(agent_message_completed(text="Done.", phase="final_answer"))
        finish = translator.finish()

        self.assertEqual(first.tape_events, ())
        self.assertEqual(second.tape_events, ())
        self.assertEqual(
            [(decision.kind, decision.data) for decision in first.stream_decisions],
            [("text", {"delta": "Do"})],
        )
        self.assertEqual(
            [(decision.kind, decision.data) for decision in second.stream_decisions],
            [("text", {"delta": "ne."})],
        )
        self.assertEqual(
            [event.type for event in completed.tape_events],
            ["codex.assistant_message.completed"],
        )
        self.assertEqual(completed.stream_decisions, ())
        self.assertEqual(
            [(decision.kind, decision.data) for decision in finish.stream_decisions],
            [("final", {"text": "Done.", "ok": True})],
        )

    def test_final_answer_delta_suppression_is_scoped_to_same_item(self) -> None:
        translator = _translator()

        first_delta = translator.accept(
            agent_message_delta(
                delta="First.",
                phase="final_answer",
                item_id="msg-1",
            )
        )
        first_completed = translator.accept(
            agent_message_completed(
                text="First.",
                phase="final_answer",
                item_id="msg-1",
            )
        )
        second_completed = translator.accept(
            agent_message_completed(
                text="Second.",
                phase="final_answer",
                item_id="msg-2",
            )
        )
        finish = translator.finish()

        self.assertEqual(
            [(decision.kind, decision.data) for decision in first_delta.stream_decisions],
            [("text", {"delta": "First."})],
        )
        self.assertEqual(first_completed.stream_decisions, ())
        self.assertEqual(
            [(decision.kind, decision.data) for decision in second_completed.stream_decisions],
            [("text", {"delta": "Second."})],
        )
        self.assertEqual(
            [(decision.kind, decision.data) for decision in finish.stream_decisions],
            [("final", {"text": "First.\nSecond.", "ok": True})],
        )

    def test_commentary_delta_is_not_streamed_or_written_to_tape(self) -> None:
        translator = _translator()

        result = translator.accept(agent_message_delta(delta="I will inspect.", phase="commentary"))

        self.assertEqual(result.tape_events, ())
        self.assertEqual(result.stream_decisions, ())

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
        self.assertNotIn("snapshot_fact_id", compact_anchors[0].payload["refs"])
        compact_bindings = [
            event
            for event in result.tape_events
            if event.type == "codex.thread.bound" and event.payload.get("reason") == "compact_continuity"
        ]
        self.assertEqual(len(compact_bindings), 1)
        self.assertEqual(compact_bindings[0].anchor_id, compact_anchors[0].anchor_id)
        self.assertEqual(compact_bindings[0].thread_id, compact_anchors[0].thread_id)
        self.assertNotIn("snapshot_fact_id", compact_bindings[0].payload["refs"])

    def test_sdk_error_notification_is_written_to_tape(self) -> None:
        translator = _translator()

        result = translator.accept(error_notification(message="model failed", code="bad_request"))

        self.assertEqual([event.type for event in result.tape_events], ["codex.error.observed"])
        self.assertEqual(result.tape_events[0].payload["message"], "model failed")
        self.assertEqual(result.tape_events[0].payload["code"], "bad_request")
        self.assertEqual(result.tape_events[0].payload["raw_error"]["message"], "model failed")
        self.assertEqual(result.stream_decisions, ())

    def test_sdk_error_notification_preserves_raw_payload(self) -> None:
        translator = _translator()
        raw_payload = {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "error": {
                "type": "ApiError",
                "message": "rate limited",
                "code": "rate_limit",
                "status": 429,
            },
            "requestId": "req-1",
        }

        result = translator.accept({"method": "error", "payload": raw_payload})

        self.assertEqual([event.type for event in result.tape_events], ["codex.error.observed"])
        self.assertEqual(result.tape_events[0].payload["raw_error"], raw_payload)
        self.assertEqual(result.stream_decisions, ())

    def test_success_fallback_uses_event_turn_id_when_no_assistant_text_exists(self) -> None:
        decisions = stream_success_decisions_from_tape_events(
            (
                make_tape_event(
                    "codex.turn.completed",
                    payload={"purpose": "user_turn"},
                    session_id="session-1",
                    tape_id="tape-1",
                    turn_id="turn-1",
                ),
            )
        )

        self.assertEqual(
            [(decision.kind, decision.data) for decision in decisions],
            [
                ("text", {"delta": "codex turn completed: turn-1"}),
                ("final", {"text": "codex turn completed: turn-1", "ok": True}),
            ],
        )

    def test_success_decisions_accept_one_shot_event_iterables(self) -> None:
        event = make_tape_event(
            "codex.assistant_message.completed",
            payload={"assistant_text": "Done.", "phase": "final_answer"},
            session_id="session-1",
            tape_id="tape-1",
            turn_id="turn-1",
        )

        decisions = stream_success_decisions_from_tape_events(iter([event]))

        self.assertEqual(
            [(decision.kind, decision.data) for decision in decisions],
            [
                ("text", {"delta": "Done."}),
                ("final", {"text": "Done.", "ok": True}),
            ],
        )


def _translator() -> CodexTurnTranslator:
    return CodexTurnTranslator(
        session_id="session-1",
        tape_id="tape-1",
        anchor_id="anchor-1",
    )


if __name__ == "__main__":
    unittest.main()
