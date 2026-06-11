#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.bub_tool_audit_projection import project_bub_tool_invocation_records
from bub_codex.bub_tools import build_bub_dynamic_tool_provider


@dataclass(frozen=True, slots=True)
class FakeBubTool:
    name: str
    description: str | None
    parameters: dict[str, Any]
    handler: Callable[..., Any] | None
    context: bool = False


def main() -> None:
    invocation_records: list[Any] = []

    async def async_echo(message: str) -> dict[str, str]:
        return {"async_echo": message}

    def context_echo(context: dict[str, str], message: str) -> dict[str, str]:
        return {"message": message, "thread_id": context["thread_id"], "turn_id": context["turn_id"]}

    def failing_tool(message: str) -> str:
        raise ValueError(f"bad message: {message}")

    provider = build_bub_dynamic_tool_provider(
        [
            fake_tool("demo.echo", lambda message: {"echo": message}),
            fake_tool("demo.async", async_echo),
            fake_tool("demo.context", context_echo, context=True),
            fake_tool("demo.fail", failing_tool),
        ],
        context_factory=lambda call: {"thread_id": call.thread_id or "", "turn_id": call.turn_id or ""},
        invocation_observer=invocation_records.append,
    )

    responses = {
        "echo": call_tool(provider, "demo_echo", {"message": "hello"}),
        "async": call_tool(provider, "demo_async", {"message": "hello async"}),
        "context": call_tool(provider, "demo_context", {"message": "hello context"}),
        "failure": call_tool(provider, "demo_fail", {"message": "hello failure"}),
    }

    collision_error = None
    try:
        build_bub_dynamic_tool_provider(
            [
                fake_tool("demo.collision", lambda: "a"),
                fake_tool("demo_collision", lambda: "b"),
            ]
        )
    except ValueError as exc:
        collision_error = str(exc)

    assert [spec.name for spec in provider.specs] == ["demo_echo", "demo_async", "demo_context", "demo_fail"]
    assert provider.codex_to_bub_name["demo_echo"] == "demo.echo"
    assert input_text(responses["echo"]) == '{"echo": "hello"}'
    assert input_text(responses["async"]) == '{"async_echo": "hello async"}'
    assert input_text(responses["context"]) == (
        '{"message": "hello context", "thread_id": "thread_1", "turn_id": "turn_1"}'
    )
    assert responses["failure"]["success"] is False
    assert "ValueError: bad message: hello failure" == input_text(responses["failure"])
    assert collision_error is not None
    assert "both map to 'demo_collision'" in collision_error
    invocation_record_dicts = [asdict(record) for record in invocation_records]
    assert [record["event_type"] for record in invocation_record_dicts] == [
        "bub.tool.invocation.started",
        "bub.tool.invocation.completed",
        "bub.tool.invocation.started",
        "bub.tool.invocation.completed",
        "bub.tool.invocation.started",
        "bub.tool.invocation.completed",
        "bub.tool.invocation.started",
        "bub.tool.invocation.failed",
    ]
    assert [record["bub_tool_name"] for record in invocation_record_dicts[::2]] == [
        "demo.echo",
        "demo.async",
        "demo.context",
        "demo.fail",
    ]
    assert invocation_record_dicts[-1]["error_type"] == "ValueError"
    assert invocation_record_dicts[-1]["success"] is False

    invocation_tape_events = project_bub_tool_invocation_records(
        invocation_records,
        session_id="session_1",
        tape_id="tape_1",
        anchor_id="anchor_1",
    )
    assert [event.type for event in invocation_tape_events] == [
        record["event_type"] for record in invocation_record_dicts
    ]
    assert invocation_tape_events[-1].payload["error_type"] == "ValueError"
    assert invocation_tape_events[-1].payload["bub_tool_name"] == "demo.fail"

    print(
        json.dumps(
            {
                "specs": [spec.to_app_server_json() for spec in provider.specs],
                "codex_to_bub_name": provider.codex_to_bub_name,
                "responses": responses,
                "invocation_records": invocation_record_dicts,
                "invocation_tape_events": [event.to_json() for event in invocation_tape_events],
                "collision_error": collision_error,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def fake_tool(name: str, handler: Callable[..., Any], *, context: bool = False) -> FakeBubTool:
    return FakeBubTool(
        name=name,
        description=f"Fake Bub tool {name}.",
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
        handler=handler,
        context=context,
    )


def call_tool(provider: Any, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return provider.dispatcher.handle_server_request(
        "item/tool/call",
        {
            "callId": f"call_{tool}",
            "namespace": "bub",
            "tool": tool,
            "arguments": arguments,
            "threadId": "thread_1",
            "turnId": "turn_1",
        },
    )


def input_text(response: dict[str, Any]) -> str:
    content_items = response.get("contentItems")
    assert isinstance(content_items, list)
    assert content_items
    text = content_items[0].get("text")
    assert isinstance(text, str)
    return text


if __name__ == "__main__":
    main()
