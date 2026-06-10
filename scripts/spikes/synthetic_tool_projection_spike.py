#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.runtime_adapter import CodexFact
from bub_codex.tool_projection import project_tool_events


def main() -> None:
    facts = [
        _fact(
            "codex.item.started",
            "mcp-1-start",
            {
                "id": "mcp-1",
                "type": "mcpToolCall",
                "server": "docs",
                "tool": "search",
                "arguments": {"q": "bub"},
                "status": "inProgress",
                "pluginId": "plugin-docs",
            },
        ),
        _fact(
            "codex.item.completed",
            "mcp-1-done",
            {
                "id": "mcp-1",
                "type": "mcpToolCall",
                "server": "docs",
                "tool": "search",
                "arguments": {"q": "bub"},
                "status": "completed",
                "pluginId": "plugin-docs",
                "result": {"content": [{"type": "text", "text": "result"}]},
                "durationMs": 12,
            },
        ),
        _fact(
            "codex.item.started",
            "dyn-1-start",
            {
                "id": "dyn-1",
                "type": "dynamicToolCall",
                "namespace": "bub",
                "tool": "lookup_anchor",
                "arguments": {"anchor_id": "a1"},
                "status": "inProgress",
            },
        ),
        _fact(
            "codex.item.completed",
            "dyn-1-done",
            {
                "id": "dyn-1",
                "type": "dynamicToolCall",
                "namespace": "bub",
                "tool": "lookup_anchor",
                "arguments": {"anchor_id": "a1"},
                "status": "completed",
                "contentItems": [{"type": "inputText", "text": "anchor found"}],
                "success": True,
                "durationMs": 7,
            },
        ),
        _fact(
            "codex.item.completed",
            "collab-1-done",
            {
                "id": "collab-1",
                "type": "collabAgentToolCall",
                "tool": "spawn_agent",
                "senderThreadId": "t1",
                "receiverThreadIds": ["t2"],
                "prompt": "inspect docs",
                "status": "completed",
                "agentsStates": {"t2": {"status": "idle"}},
            },
        ),
    ]
    events = project_tool_events(
        facts,
        session_id="synthetic-session",
        tape_id="synthetic-tape",
        anchor_id="synthetic-anchor",
    )
    output = ROOT / "artifacts/spikes/synthetic-tool-projection-events.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
    print(output)


def _fact(kind: str, event_id: str, item: dict) -> CodexFact:
    return CodexFact(
        kind=kind,
        event_id=event_id,
        source="synthetic",
        payload={"item": item},
        thread_id="synthetic-thread",
        turn_id="synthetic-turn",
        item_id=item["id"],
        occurred_at="2026-06-10T00:00:00+00:00",
    )


if __name__ == "__main__":
    main()
