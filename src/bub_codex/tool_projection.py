from __future__ import annotations

from typing import Any, Iterable

from .json_utils import JsonObject, dict_or_empty, optional_str, preview_json, sha256_json
from .runtime_adapter import CodexFact
from .tape_events import TapeEvent, make_tape_event


TOOL_ITEM_TYPES = {
    "commandExecution",
    "mcpToolCall",
    "dynamicToolCall",
    "collabAgentToolCall",
    "webSearch",
    "imageView",
}

SIDE_EFFECT_ITEM_TYPES = {
    "fileChange",
}


def project_tool_events(
    facts: Iterable[CodexFact],
    *,
    session_id: str,
    tape_id: str,
    anchor_id: str | None = None,
) -> list[TapeEvent]:
    events: list[TapeEvent] = []
    for fact in facts:
        event = project_tool_event(
            fact,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
        )
        if event is not None:
            events.append(event)
    return events


def project_tool_event(
    fact: CodexFact,
    *,
    session_id: str,
    tape_id: str,
    anchor_id: str | None = None,
) -> TapeEvent | None:
    if fact.kind not in {"codex.item.started", "codex.item.completed"}:
        return None

    item = dict_or_empty(fact.payload.get("item"))
    item_type = optional_str(item.get("type"))
    if item_type in TOOL_ITEM_TYPES:
        return _project_tool_item(
            fact,
            item=item,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
        )
    if item_type in SIDE_EFFECT_ITEM_TYPES:
        return _project_side_effect_item(
            fact,
            item=item,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
        )
    return None


def _project_tool_item(
    fact: CodexFact,
    *,
    item: JsonObject,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
) -> TapeEvent:
    lifecycle = "started" if fact.kind == "codex.item.started" else _terminal_lifecycle(item)
    item_type = str(item.get("type"))
    input_payload = _tool_input_payload(item_type, item)
    output_payload = None if lifecycle == "started" else _tool_output_payload(item_type, item)
    event_type = f"bub.tool.call.{lifecycle}"

    return make_tape_event(
        event_type,
        payload={
            "tool_call_id": item.get("id") or fact.item_id,
            "tool_kind": item_type,
            "tool_name": _tool_name(item_type, item),
            "status": item.get("status"),
            "executor": _executor(item_type, item),
            "input_sha256": sha256_json(input_payload),
            "input_preview": preview_json(input_payload),
            "output_sha256": sha256_json(output_payload) if output_payload is not None else None,
            "output_preview": preview_json(output_payload) if output_payload is not None else None,
            "source_item_id": fact.item_id,
            "source_fact_id": fact.event_id,
        },
        occurred_at=fact.occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=fact.thread_id,
        turn_id=fact.turn_id,
    )


def _project_side_effect_item(
    fact: CodexFact,
    *,
    item: JsonObject,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
) -> TapeEvent:
    lifecycle = "started" if fact.kind == "codex.item.started" else _terminal_lifecycle(item)
    payload = {
        "change_id": item.get("id") or fact.item_id,
        "side_effect_kind": item.get("type"),
        "status": item.get("status"),
        "changes_sha256": sha256_json(item.get("changes")),
        "changes_preview": preview_json(item.get("changes")),
        "source_item_id": fact.item_id,
        "source_fact_id": fact.event_id,
    }
    return make_tape_event(
        f"bub.side_effect.{lifecycle}",
        payload=payload,
        occurred_at=fact.occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=fact.thread_id,
        turn_id=fact.turn_id,
    )


def _terminal_lifecycle(item: JsonObject) -> str:
    status = item.get("status")
    if status in {"failed", "declined"}:
        return "failed"
    return "completed"


def _tool_name(item_type: str, item: JsonObject) -> str:
    if item_type == "commandExecution":
        return "shell_command"
    if item_type == "mcpToolCall":
        server = optional_str(item.get("server"))
        tool = optional_str(item.get("tool")) or "mcp_tool"
        return f"{server}/{tool}" if server else tool
    if item_type == "dynamicToolCall":
        namespace = optional_str(item.get("namespace"))
        tool = optional_str(item.get("tool")) or "dynamic_tool"
        return f"{namespace}/{tool}" if namespace else tool
    if item_type == "collabAgentToolCall":
        return optional_str(item.get("tool")) or "collab_tool"
    if item_type == "webSearch":
        action = dict_or_empty(item.get("action"))
        return optional_str(action.get("type")) or "web_search"
    if item_type == "imageView":
        return "image_view"
    return item_type


def _executor(item_type: str, item: JsonObject) -> str:
    if item_type == "commandExecution":
        source = optional_str(item.get("source"))
        return f"codex_runtime:{source}" if source else "codex_runtime"
    if item_type == "dynamicToolCall":
        return "client_dynamic_tool"
    if item_type in {"mcpToolCall", "collabAgentToolCall"}:
        return "client_or_plugin_tool"
    return "codex_runtime"


def _tool_input_payload(item_type: str, item: JsonObject) -> JsonObject:
    if item_type == "commandExecution":
        return {
            "command": item.get("command"),
            "cwd": item.get("cwd"),
            "commandActions": item.get("commandActions"),
        }
    if item_type == "mcpToolCall":
        return {
            "server": item.get("server"),
            "tool": item.get("tool"),
            "arguments": item.get("arguments"),
            "pluginId": item.get("pluginId"),
        }
    if item_type == "dynamicToolCall":
        return {
            "namespace": item.get("namespace"),
            "tool": item.get("tool"),
            "arguments": item.get("arguments"),
        }
    if item_type == "collabAgentToolCall":
        return {
            "tool": item.get("tool"),
            "senderThreadId": item.get("senderThreadId"),
            "receiverThreadIds": item.get("receiverThreadIds"),
            "prompt": item.get("prompt"),
            "model": item.get("model"),
            "reasoningEffort": item.get("reasoningEffort"),
        }
    if item_type == "webSearch":
        return {
            "query": item.get("query"),
            "action": item.get("action"),
        }
    if item_type == "imageView":
        return {"path": item.get("path")}
    return dict(item)


def _tool_output_payload(item_type: str, item: JsonObject) -> Any:
    if item_type == "commandExecution":
        return {
            "aggregatedOutput": item.get("aggregatedOutput"),
            "exitCode": item.get("exitCode"),
            "durationMs": item.get("durationMs"),
        }
    if item_type == "mcpToolCall":
        return {
            "result": item.get("result"),
            "error": item.get("error"),
            "mcpAppResourceUri": item.get("mcpAppResourceUri"),
            "durationMs": item.get("durationMs"),
        }
    if item_type == "dynamicToolCall":
        return {
            "contentItems": item.get("contentItems"),
            "success": item.get("success"),
            "durationMs": item.get("durationMs"),
        }
    if item_type == "collabAgentToolCall":
        return {
            "receiverThreadIds": item.get("receiverThreadIds"),
            "agentsStates": item.get("agentsStates"),
        }
    if item_type in {"webSearch", "imageView"}:
        return None
    return None
