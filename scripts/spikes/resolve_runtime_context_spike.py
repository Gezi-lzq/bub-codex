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

from bub_codex.context_materialization import load_tape_events_jsonl
from bub_codex.runtime_resolution import resolve_runtime_context


def main() -> None:
    args = parse_args()
    if args.tape:
        events = load_tape_events_jsonl(args.tape.read_text(encoding="utf-8").splitlines())
    else:
        events = []
    print(json.dumps(resolve_runtime_context(events).to_json(), ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve runtime context from tape events only.")
    parser.add_argument("tape", nargs="?", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    main()
