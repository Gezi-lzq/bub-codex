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
    materialize_thread_binding_events,
)


def main() -> None:
    args = parse_args()
    anchor_events = create_new_thread_anchor_events(
        [],
        session_id=args.session_id,
        tape_id=args.tape_id,
        reason="session_start",
        intent=args.intent,
        summary=None,
        owner="human",
        initiator="bub_runtime",
    )
    materialization_events = materialize_thread_binding_events(
        anchor_events,
        session_id=args.session_id,
        tape_id=args.tape_id,
        thread_id=args.thread_id,
        intent=args.intent,
        workspace_metadata={"cwd": str(ROOT), "runtime": "codex-sdk-spike"},
        reason="session_start",
    )
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for event in [*anchor_events, *materialization_events]:
            fh.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
    print(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Spike bootstrap Anchor + new_thread binding.")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/spikes/bootstrap-new-thread-events.jsonl",
    )
    parser.add_argument("--session-id", default="spike-bootstrap-session")
    parser.add_argument("--tape-id", default="spike-bootstrap-tape")
    parser.add_argument("--thread-id", default="codex-thread-spike-bootstrap")
    parser.add_argument("--intent", default="Start a new Bub session backed by a Codex thread.")
    return parser.parse_args()


if __name__ == "__main__":
    main()
