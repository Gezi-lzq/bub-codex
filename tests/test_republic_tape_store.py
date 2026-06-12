from __future__ import annotations

import asyncio
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

try:
    from bub_tapestore_sqlite import SQLiteTapeStore  # noqa: E402
except ImportError:  # pragma: no cover - optional integration dependency
    SQLiteTapeStore = None  # type: ignore[assignment]


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

            asyncio.run(adapter.append_many([anchor, binding]))
            reloaded = RepublicTapeStoreAdapter(FileTapeStore(store_dir))

            events = asyncio.run(reloaded.events(session_id="session", tape_id=tape_id))
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
            asyncio.run(
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
            )
            store.append(tape_id, TapeEntry.anchor("handoff", state={"summary": "switch context"}))

            reloaded = RepublicTapeStoreAdapter(FileTapeStore(store_dir))
            events = asyncio.run(reloaded.events(session_id="session", tape_id=tape_id))
            resolution = resolve_runtime_context(events)

        self.assertEqual([event.type for event in events], ["bub.anchor.created", "codex.thread.bound", "bub.anchor.created"])
        self.assertEqual(events[-1].payload["method"], "bub_handoff")
        self.assertEqual(events[-1].payload["state"], {"summary": "switch context"})
        self.assertEqual(resolution.action, "materialize_thread")
        self.assertEqual(resolution.anchor_id, events[-1].anchor_id)
        self.assertIsNone(resolution.thread_id)

    def test_async_tape_store_round_trips_inside_running_event_loop(self) -> None:
        async def run():
            store = FakeAsyncTapeStore()
            adapter = RepublicTapeStoreAdapter(store)
            event = make_tape_event(
                "bub.anchor.created",
                payload={"anchor_id": "anchor-async", "method": "new_thread"},
                session_id="session",
                tape_id="async-tape",
                anchor_id="anchor-async",
            )

            await adapter.append(event)
            return await adapter.events(session_id="session", tape_id="async-tape")

        events = asyncio.run(run())

        self.assertEqual([event.type for event in events], ["bub.anchor.created"])
        self.assertEqual(events[0].anchor_id, "anchor-async")

    @unittest.skipIf(SQLiteTapeStore is None, "bub_tapestore_sqlite is not installed")
    def test_sqlite_async_tape_store_round_trips_inside_running_event_loop(self) -> None:
        async def run():
            with TemporaryDirectory() as tmp:
                store = SQLiteTapeStore(Path(tmp) / "tapes.sqlite3")
                try:
                    adapter = RepublicTapeStoreAdapter(store)
                    event = make_tape_event(
                        "bub.anchor.created",
                        payload={"anchor_id": "anchor-sqlite", "method": "new_thread"},
                        session_id="session",
                        tape_id="sqlite-tape",
                        anchor_id="anchor-sqlite",
                    )

                    await adapter.append(event)
                    return await adapter.events(session_id="session", tape_id="sqlite-tape")
                finally:
                    await store.close()

        events = asyncio.run(run())

        self.assertEqual([event.type for event in events], ["bub.anchor.created"])
        self.assertEqual(events[0].anchor_id, "anchor-sqlite")


class FakeAsyncTapeStore:
    def __init__(self) -> None:
        self.entries: dict[str, list[TapeEntry]] = {}

    async def append(self, tape: str, entry: TapeEntry) -> None:
        self.entries.setdefault(tape, []).append(entry)

    async def fetch_all(self, query) -> list[TapeEntry]:
        return list(self.entries.get(query.tape, ()))


if __name__ == "__main__":
    unittest.main()
