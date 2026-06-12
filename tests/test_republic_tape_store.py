from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from republic import TapeEntry  # noqa: E402
from bub.builtin.store import FileTapeStore  # noqa: E402
from bub_codex.republic_tape_store import RepublicTapeStoreAdapter  # noqa: E402
from bub_codex.runtime_context import resolve_runtime_context  # noqa: E402
from bub_codex.tape_events import make_tape_event  # noqa: E402


class RepublicTapeStoreAdapterTest(unittest.TestCase):
    def test_file_tape_store_round_trips_bub_codex_events_and_resolves_runtime_context(self) -> None:
        tape_id = "session__codex"
        with TemporaryDirectory() as tmp:
            store_dir = Path(tmp)
            adapter = RepublicTapeStoreAdapter(FileTapeStore(store_dir))
            anchor = make_tape_event(
                "bub.anchor.created",
                payload={"anchor_id": "anchor-1", "method": "new_thread"},
                session_id="session",
                tape_id=tape_id,
                anchor_id="anchor-1",
            )
            binding = make_tape_event(
                "codex.thread.bound",
                payload={"anchor_id": "anchor-1", "thread_id": "thread-1"},
                session_id="session",
                tape_id=tape_id,
                anchor_id="anchor-1",
                thread_id="thread-1",
            )

            adapter.append_many([anchor, binding])
            reloaded = RepublicTapeStoreAdapter(FileTapeStore(store_dir))

            events = reloaded.events(session_id="session", tape_id=tape_id)
            resolution = resolve_runtime_context(events)

        self.assertEqual([event.type for event in events], ["bub.anchor.created", "codex.thread.bound"])
        self.assertEqual(resolution.action, "resume_thread")
        self.assertEqual(resolution.anchor_id, "anchor-1")
        self.assertEqual(resolution.thread_id, "thread-1")

    def test_native_bub_anchor_supersedes_previous_codex_thread_binding(self) -> None:
        tape_id = "session__codex"
        with TemporaryDirectory() as tmp:
            store_dir = Path(tmp)
            store = FileTapeStore(store_dir)
            adapter = RepublicTapeStoreAdapter(store)
            adapter.append_many(
                [
                    make_tape_event(
                        "bub.anchor.created",
                        payload={"anchor_id": "anchor-old", "method": "new_thread"},
                        session_id="session",
                        tape_id=tape_id,
                        anchor_id="anchor-old",
                    ),
                    make_tape_event(
                        "codex.thread.bound",
                        payload={"anchor_id": "anchor-old", "thread_id": "thread-old"},
                        session_id="session",
                        tape_id=tape_id,
                        anchor_id="anchor-old",
                        thread_id="thread-old",
                    ),
                ]
            )
            store.append(tape_id, TapeEntry.anchor("handoff", state={"summary": "switch context"}))

            reloaded = RepublicTapeStoreAdapter(FileTapeStore(store_dir))
            events = reloaded.events(session_id="session", tape_id=tape_id)
            resolution = resolve_runtime_context(events)

        self.assertEqual([event.type for event in events], ["bub.anchor.created", "codex.thread.bound", "bub.anchor.created"])
        self.assertEqual(events[-1].payload["method"], "bub_handoff")
        self.assertEqual(events[-1].payload["state"], {"summary": "switch context"})
        self.assertEqual(resolution.action, "materialize_thread")
        self.assertEqual(resolution.anchor_id, events[-1].anchor_id)
        self.assertIsNone(resolution.thread_id)


if __name__ == "__main__":
    unittest.main()
