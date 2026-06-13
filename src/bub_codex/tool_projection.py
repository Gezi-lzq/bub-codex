"""Tool and side-effect notification to tape-event mapping."""

from __future__ import annotations

from typing import Any

from .json_utils import JsonObject, dict_or_empty, optional_str, preview_json, sha256_json
from .runtime_adapter import record_event_id, record_item, record_item_id, record_thread_id, record_turn_id
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


def project_tool_event(
    record: JsonObject,
    *,
    session_id: str,
    tape_id: str,
    anchor_id: str | None = None,
    source: str = "sdk_stream:user_turn",
) -> TapeEvent | None:
    if record.get("method") not in {"item/started", "item/completed"}:
        return None

    item = record_item(record)
    item_type = optional_str(item.get("type"))
    if item_type in TOOL_ITEM_TYPES:
        return _project_tool_item(
            record,
            item=item,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
            source=source,
        )
    if item_type in SIDE_EFFECT_ITEM_TYPES:
        return _project_side_effect_item(
            record,
            item=item,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
            source=source,
        )
    return None


def _project_tool_item(
    record: JsonObject,
    *,
    item: JsonObject,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
    source: str,
) -> TapeEvent:
    lifecycle = "started" if record.get("method") == "item/started" else _terminal_lifecycle(item)
    item_type = str(item.get("type"))
    input_payload = _tool_input_payload(item_type, item)
    output_payload = None if lifecycle == "started" else _tool_output_payload(item_type, item)
    event_type = f"bub.tool.call.{lifecycle}"

    return make_tape_event(
        event_type,
        payload={
            "tool_call_id": item.get("id") or record_item_id(record),
            "tool_kind": item_type,
            "tool_name": _tool_name(item_type, item),
            "status": item.get("status"),
            "executor": _executor(item_type, item),
            "input_sha256": sha256_json(input_payload),
            "input_preview": preview_json(input_payload),
            "output_sha256": sha256_json(output_payload) if output_payload is not None else None,
            "output_preview": preview_json(output_payload) if output_payload is not None else None,
            "source_item_id": record_item_id(record),
            "source_fact_id": record_event_id(record, kind=_item_record_kind(record), source=source),
        },
        occurred_at=str(record.get("ts")) if record.get("ts") is not None else None,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=record_thread_id(record),
        turn_id=record_turn_id(record),
    )


def _project_side_effect_item(
    record: JsonObject,
    *,
    item: JsonObject,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
    source: str,
) -> TapeEvent:
    lifecycle = "started" if record.get("method") == "item/started" else _terminal_lifecycle(item)
    payload = {
        "change_id": item.get("id") or record_item_id(record),
        "side_effect_kind": item.get("type"),
        "status": item.get("status"),
        "changes_sha256": sha256_json(item.get("changes")),
        "changes_preview": preview_json(item.get("changes")),
        "source_item_id": record_item_id(record),
        "source_fact_id": record_event_id(record, kind=_item_record_kind(record), source=source),
    }
    return make_tape_event(
        f"bub.side_effect.{lifecycle}",
        payload=payload,
        occurred_at=str(record.get("ts")) if record.get("ts") is not None else None,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=record_thread_id(record),
        turn_id=record_turn_id(record),
    )


def _terminal_lifecycle(item: JsonObject) -> str:
    status = item.get("status")
    if status in {"failed", "declined"}:
        return "failed"
    return "completed"


def _item_record_kind(record: JsonObject) -> str:
    if record.get("method") == "item/started":
        return "codex.item.started"
    return "codex.item.completed"


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
        return {
            "path": item.get("path"),
            "mimeType": item.get("mimeType"),
        }
    return item


def _tool_output_payload(item_type: str, item: JsonObject) -> Any:
    if item_type == "commandExecution":
        return {
            "exitCode": item.get("exitCode"),
            "aggregatedOutput": item.get("aggregatedOutput"),
            "durationMs": item.get("durationMs"),
        }
    if item_type in {"mcpToolCall", "dynamicToolCall", "collabAgentToolCall"}:
        return item.get("result") or item.get("output") or item.get("error")
    if item_type == "webSearch":
        return item.get("results")
    if item_type == "imageView":
        return item.get("metadata")
    return item
