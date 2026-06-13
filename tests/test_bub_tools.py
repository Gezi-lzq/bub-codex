from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.bub_tools import build_bub_dynamic_tool_bridge  # noqa: E402


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
        bridge = build_bub_dynamic_tool_bridge([tool])
        bridge.update(
            session_id="s1",
            tape_id="tape-1",
            cwd="/workspace",
            anchor_id="anchor-1",
            state={"_runtime_agent": "agent"},
        )
        bridge.register_turn_context(
            thread_id="thread-1",
            turn_id="turn-1",
            session_id="s1",
            tape_id="tape-1",
            cwd="/workspace",
            anchor_id="anchor-1",
            state={"_runtime_agent": "agent"},
        )

        result = bridge.handle_server_request(
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

    def test_async_bub_tool_runs_on_bound_event_loop_from_worker_thread(self) -> None:
        async def run():
            calls = []
            loop = asyncio.get_running_loop()

            async def handler(*, context):
                calls.append(asyncio.get_running_loop() is loop)
                return "ok"

            tool = FakeTool(
                name="tape.info",
                description="Show tape info",
                parameters={"type": "object", "properties": {}},
                handler=handler,
                context=True,
            )
            bridge = build_bub_dynamic_tool_bridge([tool])
            bridge.bind_event_loop(loop)
            bridge.update(session_id="s1", tape_id="tape-1", cwd="/workspace", anchor_id="anchor-1")
            bridge.register_turn_context(
                thread_id="thread-1",
                turn_id="turn-1",
                session_id="s1",
                tape_id="tape-1",
                cwd="/workspace",
                anchor_id="anchor-1",
            )

            result = await asyncio.to_thread(
                bridge.handle_server_request,
                "item/tool/call",
                {
                    "callId": "call-1",
                    "namespace": "bub",
                    "tool": "tape_info",
                    "arguments": {},
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                },
            )
            return result, calls

        result, calls = asyncio.run(run())

        self.assertTrue(result["success"])
        self.assertEqual(result["contentItems"][0]["text"], "ok")
        self.assertEqual(calls, [True])

    def test_dynamic_tool_context_is_selected_by_codex_thread_and_turn(self) -> None:
        calls = []

        def handler(*, context):
            calls.append((context.tape, context.state["session_id"], context.state["_runtime_anchor_id"]))
            return "ok"

        tool = FakeTool(
            name="tape.info",
            description="Show tape info",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            context=True,
        )
        bridge = build_bub_dynamic_tool_bridge([tool])
        bridge.register_turn_context(
            thread_id="thread-1",
            turn_id="turn-1",
            session_id="s1",
            tape_id="tape-1",
            cwd="/workspace/one",
            anchor_id="anchor-1",
        )
        bridge.register_turn_context(
            thread_id="thread-2",
            turn_id="turn-2",
            session_id="s2",
            tape_id="tape-2",
            cwd="/workspace/two",
            anchor_id="anchor-2",
        )

        result = bridge.handle_server_request(
            "item/tool/call",
            {
                "callId": "call-1",
                "namespace": "bub",
                "tool": "tape_info",
                "arguments": {},
                "threadId": "thread-1",
                "turnId": "turn-1",
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(calls, [("tape-1", "s1", "anchor-1")])

    def test_dynamic_tool_provider_accepts_run_only_bub_tools(self) -> None:
        tool = RunOnlyTool()
        bridge = build_bub_dynamic_tool_bridge([tool])

        result = bridge.handle_server_request(
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

    def test_dynamic_tool_handler_fails_when_runtime_context_is_not_registered(self) -> None:
        tool = FakeTool(
            name="tape.info",
            description="Show tape info",
            parameters={"type": "object", "properties": {}},
            handler=lambda *, context: "not reached",
            context=True,
        )
        bridge = build_bub_dynamic_tool_bridge([tool])

        result = bridge.handle_server_request(
            "item/tool/call",
            {
                "callId": "call-1",
                "namespace": "bub",
                "tool": "tape_info",
                "arguments": {},
                "threadId": "thread-1",
                "turnId": "turn-1",
            },
        )

        self.assertFalse(result["success"])
        self.assertIn("no Bub runtime context registered", result["contentItems"][0]["text"])


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
