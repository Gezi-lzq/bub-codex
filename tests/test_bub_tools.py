from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.bub_tools import BubToolRuntimeContext, build_bub_dynamic_tool_provider  # noqa: E402


class BubDynamicToolsTest(unittest.TestCase):
    def test_dynamic_tool_handler_runs_async_bub_tool_with_current_turn_context(self) -> None:
        calls = []

        async def handler(summary: str, *, context):
            calls.append((summary, context.tape, context.state["_runtime_anchor_id"], context.state["_runtime_agent"]))
            return "anchor added"

        tool = FakeTool(
            name="tape.handoff",
            description="Add a handoff anchor",
            parameters={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
            },
            handler=handler,
            context=True,
        )
        runtime_context = BubToolRuntimeContext()
        runtime_context.update(
            session_id="s1",
            tape_id="tape-1",
            cwd="/workspace",
            anchor_id="anchor-1",
            state={"_runtime_agent": "agent"},
        )
        provider = build_bub_dynamic_tool_provider([tool], context_factory=runtime_context.context_for_call)

        result = provider.dispatcher.handle_server_request(
            "item/tool/call",
            {
                "callId": "call-1",
                "namespace": "bub",
                "tool": "tape_handoff",
                "arguments": {"summary": "handoff summary"},
                "threadId": "thread-1",
                "turnId": "turn-1",
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["contentItems"][0]["text"], "anchor added")
        self.assertEqual(calls, [("handoff summary", "tape-1", "anchor-1", "agent")])

    def test_dynamic_tool_provider_accepts_run_only_bub_tools(self) -> None:
        tool = RunOnlyTool()
        provider = build_bub_dynamic_tool_provider([tool])

        result = provider.dispatcher.handle_server_request(
            "item/tool/call",
            {
                "callId": "call-1",
                "namespace": "bub",
                "tool": "tape_info",
                "arguments": {"verbose": True},
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["contentItems"][0]["text"], "ran with verbose=True")


class FakeTool:
    def __init__(self, *, name, description, parameters, handler, context):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.context = context


class RunOnlyTool:
    name = "tape.info"
    description = "Show tape info"
    parameters = {"type": "object", "properties": {"verbose": {"type": "boolean"}}}
    context = False

    def run(self, *, verbose: bool) -> str:
        return f"ran with verbose={verbose}"


if __name__ == "__main__":
    unittest.main()
