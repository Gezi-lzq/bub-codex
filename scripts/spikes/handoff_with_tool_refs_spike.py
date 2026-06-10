#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.context_materialization import (
    create_new_thread_anchor_events,
    load_tape_events_jsonl,
    materialize_thread_binding_events,
    select_handoff_source_refs,
)


def main() -> None:
    args = parse_args()
    base_events = []
    for path in args.tape:
        base_events.extend(load_tape_events_jsonl(path.read_text(encoding="utf-8").splitlines()))

    source_refs = select_handoff_source_refs(base_events, limit=args.limit)
    anchor_events = create_new_thread_anchor_events(
        base_events,
        session_id=args.session_id,
        tape_id=args.tape_id,
        reason="fresh_handoff",
        intent=args.intent,
        summary=args.summary,
        source_event_refs=source_refs,
        owner="human",
        initiator="human",
    )
    materialization_events = materialize_thread_binding_events(
        [*base_events, *anchor_events],
        session_id=args.session_id,
        tape_id=args.tape_id,
        thread_id=args.thread_id,
        intent=args.intent,
        workspace_metadata={"cwd": str(ROOT), "runtime": "codex-sdk-spike"},
        reason="fresh_handoff",
    )

    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for event in [*anchor_events, *materialization_events]:
            fh.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
    print(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a handoff Anchor that references tool facts.")
    parser.add_argument("tape", nargs="+", type=Path, help="Tape-like event JSONL files")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/spikes/handoff-with-tool-refs-events.jsonl",
    )
    parser.add_argument("--session-id", default="spike-session")
    parser.add_argument("--tape-id", default="spike-tape")
    parser.add_argument("--thread-id", default="codex-thread-tool-handoff")
    parser.add_argument("--intent", default="Continue after tool and file-change observations in a fresh Codex thread.")
    parser.add_argument("--summary", default="Tool and file-change observations were captured as Bub tape facts.")
    parser.add_argument("--limit", type=int, default=16)
    return parser.parse_args()


if __name__ == "__main__":
    main()
