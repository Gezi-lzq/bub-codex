from __future__ import annotations

import asyncio
import inspect
import json
import re
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .codex_client import (
    DynamicToolCall,
    DynamicToolDispatcher,
    DynamicToolResult,
    DynamicToolSpec,
    dynamic_tool_key,
)


JsonObject = dict[str, Any]
BUB_DYNAMIC_TOOL_NAMESPACE = "bub"
_CODEX_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_UNSAFE_CODEX_TOOL_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")


class BubToolLike(Protocol):
    name: str
    description: str | None
    parameters: JsonObject
    handler: Callable[..., Any] | None
    context: bool


@dataclass(frozen=True, slots=True)
class ToolContextLike:
    """Fallback with the same attributes Bub tools use from Republic ToolContext."""

    tape: str | None
    run_id: str
    state: JsonObject


@dataclass(frozen=True, slots=True)
class BubDynamicToolProvider:
    specs: tuple[DynamicToolSpec, ...]
    dispatcher: DynamicToolDispatcher
    codex_to_bub_name: dict[str, str]


@dataclass(frozen=True, slots=True)
class BubToolInvocationAuditRecord:
    """Host-side audit record for Bub dynamic tool handler execution."""

    event_type: str
    call_id: str
    namespace: str | None
    codex_tool_name: str
    bub_tool_name: str
    thread_id: str | None
    turn_id: str | None
    arguments: JsonObject
    occurred_at: str
    success: bool | None = None
    output: JsonObject | None = None
    error_type: str | None = None
    error_message: str | None = None


BubToolInvocationObserver = Callable[[BubToolInvocationAuditRecord], None]
_ASYNC_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bub-codex-tool")


@dataclass(slots=True)
class BubToolRuntimeContext:
    """Mutable per-turn context used by Codex dynamic Bub tool calls."""

    session_id: str = ""
    tape_id: str = ""
    cwd: str = "."
    anchor_id: str | None = None
    state: JsonObject = field(default_factory=dict)

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

    def context_for_call(self, call: DynamicToolCall) -> Any:
        return make_bub_tool_context(
            session_id=self.session_id,
            tape_id=self.tape_id,
            cwd=self.cwd,
            call=call,
            anchor_id=self.anchor_id,
            extra_state=self.state,
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


def build_bub_dynamic_tool_provider(
    tools: Iterable[BubToolLike],
    *,
    namespace: str = BUB_DYNAMIC_TOOL_NAMESPACE,
    context_factory: Callable[[DynamicToolCall], Any] | None = None,
    invocation_observer: BubToolInvocationObserver | None = None,
) -> BubDynamicToolProvider:
    specs: list[DynamicToolSpec] = []
    handlers: dict[tuple[str | None, str], Callable[[DynamicToolCall], DynamicToolResult]] = {}
    codex_to_bub_name: dict[str, str] = {}
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
        codex_to_bub_name[codex_name] = tool.name

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
            invocation_observer=invocation_observer,
        )

    return BubDynamicToolProvider(
        specs=tuple(specs),
        dispatcher=DynamicToolDispatcher(handlers),
        codex_to_bub_name=codex_to_bub_name,
    )


def _is_executable_tool(tool: BubToolLike) -> bool:
    return tool.handler is not None or callable(getattr(tool, "run", None))


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
    invocation_observer: BubToolInvocationObserver | None,
) -> Callable[[DynamicToolCall], DynamicToolResult]:
    def handle(call: DynamicToolCall) -> DynamicToolResult:
        _observe_invocation(
            invocation_observer,
            "bub.tool.invocation.started",
            call=call,
            bub_tool_name=tool.name,
        )
        try:
            kwargs = dict(call.arguments)
            if tool.context:
                if context_factory is None:
                    raise RuntimeError(f"Bub tool requires context but no context_factory was provided: {tool.name}")
                kwargs["context"] = context_factory(call)

            result = _run_bub_tool(tool, kwargs)
            result = _resolve_awaitable(result)
            dynamic_result = _tool_result_to_dynamic_result(result)
            _observe_invocation(
                invocation_observer,
                "bub.tool.invocation.completed",
                call=call,
                bub_tool_name=tool.name,
                success=dynamic_result.success,
                output=dynamic_result.to_app_server_json(),
            )
            return dynamic_result
        except Exception as exc:
            dynamic_result = DynamicToolResult.input_text(f"{type(exc).__name__}: {exc}", success=False)
            _observe_invocation(
                invocation_observer,
                "bub.tool.invocation.failed",
                call=call,
                bub_tool_name=tool.name,
                success=False,
                output=dynamic_result.to_app_server_json(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return dynamic_result

    return handle


def _run_bub_tool(tool: BubToolLike, kwargs: JsonObject) -> Any:
    run = getattr(tool, "run", None)
    if callable(run):
        return run(**kwargs)

    assert tool.handler is not None
    return tool.handler(**kwargs)


def _observe_invocation(
    observer: BubToolInvocationObserver | None,
    event_type: str,
    *,
    call: DynamicToolCall,
    bub_tool_name: str,
    success: bool | None = None,
    output: JsonObject | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    if observer is None:
        return
    observer(
        BubToolInvocationAuditRecord(
            event_type=event_type,
            call_id=call.call_id,
            namespace=call.namespace,
            codex_tool_name=call.tool,
            bub_tool_name=bub_tool_name,
            thread_id=call.thread_id,
            turn_id=call.turn_id,
            arguments=dict(call.arguments),
            occurred_at=datetime.now(timezone.utc).isoformat(),
            success=success,
            output=output,
            error_type=error_type,
            error_message=error_message,
        )
    )


def _resolve_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    return _ASYNC_TOOL_EXECUTOR.submit(asyncio.run, value).result()


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
