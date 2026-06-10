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
    materialize_thread_binding_failed_events,
)


def main() -> None:
    args = parse_args()
    base_events = load_tape_events_jsonl(args.tape.read_text(encoding="utf-8").splitlines())
    anchor_events = create_new_thread_anchor_events(
        base_events,
        session_id=args.session_id,
        tape_id=args.tape_id,
        reason=args.reason,
        intent=args.intent,
        summary=args.summary,
        owner=args.owner,
        initiator=args.initiator,
    )
    if args.simulate_bind_failure:
        materialization_events = materialize_thread_binding_failed_events(
            [*base_events, *anchor_events],
            session_id=args.session_id,
            tape_id=args.tape_id,
            intent=args.intent,
            workspace_metadata={"cwd": str(ROOT), "runtime": "codex-sdk-spike"},
            reason=args.reason,
            error={"type": "simulated_bind_failure", "message": "Codex thread bind failed."},
        )
    else:
        materialization_events = materialize_thread_binding_events(
            [*base_events, *anchor_events],
            session_id=args.session_id,
            tape_id=args.tape_id,
            thread_id=args.thread_id,
            intent=args.intent,
            workspace_metadata={"cwd": str(ROOT), "runtime": "codex-sdk-spike"},
            reason=args.reason,
        )
    events = [*anchor_events, *materialization_events]

    output = args.output or args.tape.with_name("new-thread-materialization-events.jsonl")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
    print(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Spike Anchor + new_thread materialization.")
    parser.add_argument("tape", type=Path, help="Path to projected tape events JSONL")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--session-id", default="spike-session")
    parser.add_argument("--tape-id", default="spike-tape")
    parser.add_argument("--thread-id", default="codex-thread-spike-new")
    parser.add_argument("--reason", default="fresh_handoff")
    parser.add_argument("--intent", default="Continue from the latest Bub Anchor in a fresh Codex thread.")
    parser.add_argument("--summary")
    parser.add_argument("--owner", default="human")
    parser.add_argument("--initiator", default="human")
    parser.add_argument("--simulate-bind-failure", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main()
