"""Codex app-server parameter and dynamic-tool boundary.

This module owns raw app-server JSON extensions such as `dynamicTools` and the
server-request dispatcher for dynamic tools/approvals. Thread and turn lifecycle
calls belong in `codex_thread_service.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .json_utils import JsonObject, dict_or_empty, optional_str


@dataclass(frozen=True, slots=True)
class DynamicToolSpec:
    name: str
    description: str
    input_schema: JsonObject
    namespace: str | None = None
    defer_loading: bool | None = None

    def to_app_server_json(self) -> JsonObject:
        data: JsonObject = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.namespace:
            data["namespace"] = self.namespace
        if self.defer_loading is not None:
            data["deferLoading"] = self.defer_loading
        return data


@dataclass(frozen=True, slots=True)
class DynamicToolCall:
    call_id: str
    tool: str
    arguments: JsonObject
    thread_id: str | None = None
    turn_id: str | None = None
    namespace: str | None = None

    @classmethod
    def from_app_server_params(cls, params: JsonObject) -> "DynamicToolCall":
        return cls(
            call_id=str(params.get("callId") or ""),
            namespace=optional_str(params.get("namespace")),
            tool=str(params.get("tool") or ""),
            arguments=dict_or_empty(params.get("arguments")),
            thread_id=optional_str(params.get("threadId")),
            turn_id=optional_str(params.get("turnId")),
        )


@dataclass(frozen=True, slots=True)
class DynamicToolResult:
    content_items: tuple[JsonObject, ...]
    success: bool

    @classmethod
    def input_text(cls, text: str, *, success: bool = True) -> "DynamicToolResult":
        return cls(content_items=({"type": "inputText", "text": text},), success=success)

    def to_app_server_json(self) -> JsonObject:
        return {
            "contentItems": list(self.content_items),
            "success": self.success,
        }


@dataclass(frozen=True, slots=True)
class ThreadStartOptions:
    cwd: str
    approval_policy: str = "never"
    sandbox: str = "danger-full-access"
    dynamic_tools: tuple[DynamicToolSpec, ...] = ()

    def to_app_server_json(self) -> JsonObject:
        data: JsonObject = {
            "cwd": self.cwd,
            "approvalPolicy": self.approval_policy,
            "sandbox": self.sandbox,
        }
        if self.dynamic_tools:
            data["dynamicTools"] = [tool.to_app_server_json() for tool in self.dynamic_tools]
        return data


class DynamicToolDispatcher:
    def __init__(
        self,
        handlers: dict[tuple[str | None, str], Callable[[DynamicToolCall], DynamicToolResult]],
    ) -> None:
        self._handlers = handlers

    def handle_server_request(self, method: str, params: JsonObject | None) -> JsonObject:
        if method == "item/tool/call":
            call = DynamicToolCall.from_app_server_params(params or {})
            handler = self._handlers.get((call.namespace, call.tool))
            if handler is None:
                handler = self._handlers.get((None, call.tool))
            if handler is None:
                return DynamicToolResult.input_text(
                    f"dynamic tool not registered: {call.tool}",
                    success=False,
                ).to_app_server_json()
            return handler(call).to_app_server_json()

        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            return {"decision": "accept"}

        return {}


def dynamic_tool_key(spec: DynamicToolSpec) -> tuple[str | None, str]:
    return (spec.namespace, spec.name)
