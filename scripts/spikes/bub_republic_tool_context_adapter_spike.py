#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex import (
    DynamicToolCall,
    build_bub_dynamic_tool_provider,
    make_bub_tool_context,
    project_bub_tool_invocation_records,
)


@dataclass(frozen=True, slots=True)
class FakeRepublicTool:
    name: str
    description: str | None
    parameters: dict[str, Any]
    context: bool = True
    handler: None = None

    def run(self, message: str, *, context: Any) -> dict[str, Any]:
        return {
            "message": message,
            "context_type": type(context).__name__,
            "tape": context.tape,
            "run_id": context.run_id,
            "state": context.state,
        }


def main() -> None:
    invocation_records: list[Any] = []
    tool = FakeRepublicTool(
        name="tests.context_echo",
        description="Echo context shape.",
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
    )

    def context_factory(call: DynamicToolCall) -> Any:
        return make_bub_tool_context(
            session_id="session_1",
            tape_id="tape_1",
            anchor_id="anchor_1",
            cwd="/workspace",
            call=call,
            extra_state={"custom": "value"},
        )

    provider = build_bub_dynamic_tool_provider(
        [tool],
        context_factory=context_factory,
        invocation_observer=invocation_records.append,
    )

    response = provider.dispatcher.handle_server_request(
        "item/tool/call",
        {
            "callId": "call_1",
            "namespace": "bub",
            "tool": "tests_context_echo",
            "arguments": {"message": "hello"},
            "threadId": "thread_1",
            "turnId": "turn_1",
        },
    )
    assert response["success"] is True
    payload = json.loads(response["contentItems"][0]["text"])
    assert payload["message"] == "hello"
    assert payload["tape"] == "tape_1"
    assert payload["run_id"] == "turn_1"
    assert payload["state"] == {
        "session_id": "session_1",
        "_runtime_workspace": "/workspace",
        "_runtime_anchor_id": "anchor_1",
        "_runtime_thread_id": "thread_1",
        "_runtime_turn_id": "turn_1",
        "_runtime_tool_call_id": "call_1",
        "custom": "value",
    }

    event_types = [record.event_type for record in invocation_records]
    assert event_types == ["bub.tool.invocation.started", "bub.tool.invocation.completed"]
    tape_events = project_bub_tool_invocation_records(
        invocation_records,
        session_id="session_1",
        tape_id="tape_1",
        anchor_id="anchor_1",
    )
    assert [event.type for event in tape_events] == event_types

    print(
        json.dumps(
            {
                "specs": [spec.to_app_server_json() for spec in provider.specs],
                "response": response,
                "invocation_records": [asdict(record) for record in invocation_records],
                "tape_events": [event.to_json() for event in tape_events],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
