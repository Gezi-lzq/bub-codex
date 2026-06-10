#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.context_materialization import (
    create_new_thread_anchor_events,
    materialize_thread_binding_events,
)
from bub_codex.tape_store import InMemoryTapeStore


SESSION_ID = "store-spike-session"
TAPE_ID = "store-spike-tape"


def main() -> None:
    store = InMemoryTapeStore()
    results = []
    results.append({"step": "empty", "resolution": store.resolve_runtime_context(session_id=SESSION_ID, tape_id=TAPE_ID).to_json()})

    bootstrap_anchor = create_new_thread_anchor_events(
        [],
        session_id=SESSION_ID,
        tape_id=TAPE_ID,
        reason="session_start",
        intent="Bootstrap an in-memory tape store session.",
        initiator="bub_runtime",
    )
    store.append_many(bootstrap_anchor)
    results.append({"step": "anchor_created", "resolution": store.resolve_runtime_context(session_id=SESSION_ID, tape_id=TAPE_ID).to_json()})

    binding = materialize_thread_binding_events(
        store.events(session_id=SESSION_ID, tape_id=TAPE_ID),
        session_id=SESSION_ID,
        tape_id=TAPE_ID,
        thread_id="codex-thread-store-spike",
        intent="Bootstrap an in-memory tape store session.",
        reason="session_start",
    )
    store.append_many(binding)
    results.append({"step": "thread_bound", "resolution": store.resolve_runtime_context(session_id=SESSION_ID, tape_id=TAPE_ID).to_json()})

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
