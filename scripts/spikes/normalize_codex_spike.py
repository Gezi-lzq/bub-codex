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

from bub_codex.runtime_adapter import (
    facts_from_notification_record,
    facts_from_server_request_record,
    load_compaction_snapshots,
)


def main() -> None:
    args = parse_args()
    facts = []
    for name, source in [
        ("turn-stream.jsonl", "sdk_turn_stream"),
        ("compact-private-turn-stream.jsonl", "sdk_private_compact_stream"),
    ]:
        path = args.spike_dir / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            facts.extend(facts_from_notification_record(json.loads(line), source=source))

    server_requests = args.spike_dir / "server-requests.jsonl"
    if server_requests.exists():
        for line in server_requests.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            facts.extend(facts_from_server_request_record(json.loads(line)))

    compacted = args.spike_dir / "rollout-compacted-items.json"
    if compacted.exists():
        facts.extend(load_compaction_snapshots(compacted))

    out_path = args.output or args.spike_dir / "normalized-facts.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for fact in facts:
            fh.write(json.dumps(fact.to_json(), ensure_ascii=False) + "\n")
    print(out_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize Codex SDK spike artifacts into facts.")
    parser.add_argument("spike_dir", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    main()
