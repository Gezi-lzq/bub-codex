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

from bub_codex.tape_events import load_facts_jsonl, project_codex_facts_to_tape_events


def main() -> None:
    args = parse_args()
    facts = load_facts_jsonl(args.facts.read_text(encoding="utf-8").splitlines())
    events = project_codex_facts_to_tape_events(
        facts,
        session_id=args.session_id,
        tape_id=args.tape_id,
        initiator=args.initiator,
        reason=args.reason,
    )

    output = args.output or args.facts.with_name("projected-tape-events.jsonl")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
    print(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project normalized Codex facts into tape events.")
    parser.add_argument("facts", type=Path, help="Path to normalized-facts.jsonl")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--session-id", default="spike-session")
    parser.add_argument("--tape-id", default="spike-tape")
    parser.add_argument("--initiator", default="bub_runtime")
    parser.add_argument("--reason", default="user_requested")
    return parser.parse_args()


if __name__ == "__main__":
    main()
