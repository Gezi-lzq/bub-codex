from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub.builtin.store import FileTapeStore  # noqa: E402
from bub_codex import RepublicTapeStoreAdapter, make_tape_event  # noqa: E402


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
            resolution = reloaded.resolve_runtime_context(session_id="session", tape_id=tape_id)

        self.assertEqual([event.type for event in events], ["bub.anchor.created", "codex.thread.bound"])
        self.assertEqual(resolution.action, "resume_thread")
        self.assertEqual(resolution.anchor_id, "anchor-1")
        self.assertEqual(resolution.thread_id, "thread-1")


if __name__ == "__main__":
    unittest.main()
