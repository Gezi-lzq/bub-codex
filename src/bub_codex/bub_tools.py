"""Bub tool to Codex dynamic-tool adapter boundary.

This module exposes a small allowlist of Bub tape tools to Codex dynamic tools
and injects explicit per-turn tool context. It should not own Codex thread
lifecycle or runtime state transitions.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .codex_client import (
    DynamicToolCall,
    DynamicToolDispatcher,
    DynamicToolResult,
    DynamicToolSpec,
    dynamic_tool_key,
)
from .json_utils import JsonObject


BUB_DYNAMIC_TOOL_NAMESPACE = "bub"
_CODEX_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_UNSAFE_CODEX_TOOL_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")


class BubToolLike(Protocol):
    name: str
    description: str | None
    parameters: JsonObject
    context: bool


@dataclass(frozen=True, slots=True)
class ToolContextLike:
    """Fallback with the same attributes Bub tools use from Republic ToolContext."""

    tape: str | None
    run_id: str
    state: JsonObject


@dataclass(frozen=True, slots=True)
class BubDynamicToolBridge:
    """Runtime-facing bundle for Codex dynamic Bub tools."""

    runtime_context: "BubToolRuntimeContext"
    specs: tuple[DynamicToolSpec, ...]
    dispatcher: DynamicToolDispatcher

    def handle_server_request(self, method: str, params: JsonObject | None) -> JsonObject:
        return self.dispatcher.handle_server_request(method, params)

    def bind_event_loop(self, event_loop: asyncio.AbstractEventLoop) -> None:
        self.runtime_context.bind_event_loop(event_loop)

    def update(
        self,
        *,
        session_id: str,
        tape_id: str,
        cwd: str,
        anchor_id: str | None,
        state: JsonObject | None = None,
    ) -> None:
        self.runtime_context.update(
            session_id=session_id,
            tape_id=tape_id,
            cwd=cwd,
            anchor_id=anchor_id,
            state=state,
        )

    def register_turn_context(
        self,
        *,
        thread_id: str,
        turn_id: str | None,
        session_id: str,
        tape_id: str,
        cwd: str,
        anchor_id: str | None,
        state: JsonObject | None = None,
    ) -> None:
        self.runtime_context.register_turn_context(
            thread_id=thread_id,
            turn_id=turn_id,
            session_id=session_id,
            tape_id=tape_id,
            cwd=cwd,
            anchor_id=anchor_id,
            state=state,
        )

    def clear_turn_context(self, *, thread_id: str, turn_id: str | None) -> None:
        self.runtime_context.clear_turn_context(thread_id=thread_id, turn_id=turn_id)


@dataclass(frozen=True, slots=True)
class BubToolCallContext:
    session_id: str
    tape_id: str
    cwd: str
    anchor_id: str | None
    state: JsonObject


@dataclass(slots=True)
class BubToolRuntimeContext:
    """Mutable per-turn context used by Codex dynamic Bub tool calls."""

    session_id: str = ""
    tape_id: str = ""
    cwd: str = "."
    anchor_id: str | None = None
    state: JsonObject = field(default_factory=dict)
    event_loop: asyncio.AbstractEventLoop | None = None
    call_contexts: dict[tuple[str, str], BubToolCallContext] = field(default_factory=dict)

    def bind_event_loop(self, event_loop: asyncio.AbstractEventLoop) -> None:
        self.event_loop = event_loop

    def update(
        self,
        *,
        session_id: str,
        tape_id: str,
        cwd: str,
        anchor_id: str | None,
        state: JsonObject | None = None,
    ) -> None:
        self.session_id = session_id
        self.tape_id = tape_id
        self.cwd = cwd
        self.anchor_id = anchor_id
        self.state = dict(state or {})

    def register_turn_context(
        self,
        *,
        thread_id: str,
        turn_id: str | None,
        session_id: str,
        tape_id: str,
        cwd: str,
        anchor_id: str | None,
        state: JsonObject | None = None,
    ) -> None:
        context = BubToolCallContext(
            session_id=session_id,
            tape_id=tape_id,
            cwd=cwd,
            anchor_id=anchor_id,
            state=dict(state or {}),
        )
        if turn_id is not None:
            self.call_contexts[(thread_id, turn_id)] = context

    def clear_turn_context(self, *, thread_id: str, turn_id: str | None) -> None:
        if turn_id is not None:
            self.call_contexts.pop((thread_id, turn_id), None)

    def context_for_call(self, call: DynamicToolCall) -> Any:
        context = self._context_for_call(call)
        return make_bub_tool_context(
            session_id=context.session_id,
            tape_id=context.tape_id,
            cwd=context.cwd,
            call=call,
            anchor_id=context.anchor_id,
            extra_state=context.state,
        )

    def resolve_awaitable(self, value: Any) -> Any:
        if not inspect.isawaitable(value):
            return value

        if self.event_loop is not None and self.event_loop.is_running():
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is not self.event_loop:
                return asyncio.run_coroutine_threadsafe(value, self.event_loop).result()

        return _resolve_awaitable(value)

    def _context_for_call(self, call: DynamicToolCall) -> BubToolCallContext:
        if call.thread_id is None or call.turn_id is None:
            raise RuntimeError(
                "Codex dynamic tool call is missing required app-server ids "
                f"thread_id={call.thread_id!r} turn_id={call.turn_id!r}"
            )
        context = self.call_contexts.get((call.thread_id, call.turn_id))
        if context is not None:
            return context
        raise RuntimeError(
            "no Bub runtime context registered for Codex dynamic tool call "
            f"thread_id={call.thread_id!r} turn_id={call.turn_id!r}"
        )


def make_bub_tool_context(
    *,
    session_id: str,
    tape_id: str,
    cwd: str,
    call: DynamicToolCall,
    anchor_id: str | None = None,
    extra_state: JsonObject | None = None,
) -> Any:
    state: JsonObject = {
        "session_id": session_id,
        "_runtime_workspace": cwd,
        "_runtime_anchor_id": anchor_id,
        "_runtime_thread_id": call.thread_id,
        "_runtime_turn_id": call.turn_id,
        "_runtime_tool_call_id": call.call_id,
    }
    if extra_state:
        state.update(extra_state)

    run_id = call.turn_id or call.call_id or "codex_dynamic_tool"
    try:
        from republic import ToolContext
    except ImportError:
        return ToolContextLike(tape=tape_id, run_id=run_id, state=state)
    return ToolContext(tape=tape_id, run_id=run_id, state=state)


def _build_bub_dynamic_tool_parts(
    tools: Iterable[BubToolLike],
    *,
    namespace: str = BUB_DYNAMIC_TOOL_NAMESPACE,
    context_factory: Callable[[DynamicToolCall], Any] | None = None,
    awaitable_resolver: Callable[[Any], Any] | None = None,
) -> tuple[tuple[DynamicToolSpec, ...], DynamicToolDispatcher]:
    specs: list[DynamicToolSpec] = []
    handlers: dict[tuple[str | None, str], Callable[[DynamicToolCall], DynamicToolResult]] = {}
    seen: dict[str, str] = {}

    for tool in tools:
        if not _is_executable_tool(tool):
            continue

        codex_name = bub_tool_name_to_codex_name(tool.name)
        if existing := seen.get(codex_name):
            raise ValueError(
                f"Codex dynamic tool name collision: {existing!r} and {tool.name!r} both map to {codex_name!r}"
        )
        seen[codex_name] = tool.name

        spec = DynamicToolSpec(
            namespace=namespace,
            name=codex_name,
            description=tool.description or tool.name,
            input_schema=_object_schema(tool.parameters),
        )
        specs.append(spec)
        handlers[dynamic_tool_key(spec)] = _make_bub_tool_handler(
            tool,
            context_factory=context_factory,
            awaitable_resolver=awaitable_resolver,
        )

    return tuple(specs), DynamicToolDispatcher(handlers)


def build_bub_dynamic_tool_bridge(
    tools: Iterable[BubToolLike],
    *,
    namespace: str = BUB_DYNAMIC_TOOL_NAMESPACE,
) -> BubDynamicToolBridge:
    runtime_context = BubToolRuntimeContext()
    specs, dispatcher = _build_bub_dynamic_tool_parts(
        tools,
        namespace=namespace,
        context_factory=runtime_context.context_for_call,
        awaitable_resolver=runtime_context.resolve_awaitable,
    )
    return BubDynamicToolBridge(
        runtime_context=runtime_context,
        specs=specs,
        dispatcher=dispatcher,
    )


def _is_executable_tool(tool: BubToolLike) -> bool:
    return callable(getattr(tool, "handler", None)) or callable(getattr(tool, "run", None))


def bub_tool_name_to_codex_name(name: str) -> str:
    codex_name = _UNSAFE_CODEX_TOOL_NAME_CHARS.sub("_", name.strip()).strip("_")
    if not codex_name:
        raise ValueError(f"Cannot derive Codex dynamic tool name from {name!r}")
    if not _CODEX_TOOL_NAME_PATTERN.fullmatch(codex_name):
        raise ValueError(f"Invalid Codex dynamic tool name derived from {name!r}: {codex_name!r}")
    return codex_name


def _make_bub_tool_handler(
    tool: BubToolLike,
    *,
    context_factory: Callable[[DynamicToolCall], Any] | None,
    awaitable_resolver: Callable[[Any], Any] | None,
) -> Callable[[DynamicToolCall], DynamicToolResult]:
    def handle(call: DynamicToolCall) -> DynamicToolResult:
        try:
            kwargs = dict(call.arguments)
            if tool.context:
                if context_factory is None:
                    raise RuntimeError(f"Bub tool requires context but no context_factory was provided: {tool.name}")
                kwargs["context"] = context_factory(call)

            result = _run_bub_tool(tool, kwargs)
            result = (awaitable_resolver or _resolve_awaitable)(result)
            return _tool_result_to_dynamic_result(result)
        except Exception as exc:
            return DynamicToolResult.input_text(f"{type(exc).__name__}: {exc}", success=False)

    return handle


def _run_bub_tool(tool: BubToolLike, kwargs: JsonObject) -> Any:
    run = getattr(tool, "run", None)
    if callable(run):
        return run(**kwargs)

    handler = getattr(tool, "handler", None)
    if not callable(handler):
        raise RuntimeError(f"Bub tool is not executable: {tool.name}")
    return handler(**kwargs)


def _resolve_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    raise RuntimeError("cannot synchronously resolve an awaitable from the current event loop")


def _tool_result_to_dynamic_result(value: Any) -> DynamicToolResult:
    if isinstance(value, DynamicToolResult):
        return value
    if isinstance(value, str):
        return DynamicToolResult.input_text(value)
    return DynamicToolResult.input_text(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _object_schema(value: Any) -> JsonObject:
    if isinstance(value, dict) and value.get("type") == "object":
        return dict(value)
    if isinstance(value, dict):
        schema = dict(value)
        schema.setdefault("type", "object")
        return schema
    return {"type": "object", "properties": {}, "additionalProperties": False}
