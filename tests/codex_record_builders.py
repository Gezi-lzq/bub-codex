from __future__ import annotations

from typing import Any


def turn_started(*, thread_id: str = "thread-1", turn_id: str = "turn-1") -> dict[str, Any]:
    return {"method": "turn/started", "payload": {"threadId": thread_id, "turn": {"id": turn_id}}}


def turn_completed(*, thread_id: str = "thread-1", turn_id: str = "turn-1") -> dict[str, Any]:
    return {"method": "turn/completed", "payload": {"threadId": thread_id, "turn": {"id": turn_id}}}


def agent_message_completed(
    *,
    text: str,
    phase: str,
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
    item_id: str | None = None,
) -> dict[str, Any]:
    return {
        "method": "item/completed",
        "payload": {
            "threadId": thread_id,
            "turnId": turn_id,
            "item": {
                "type": "agentMessage",
                "id": item_id or f"message-{phase}",
                "text": text,
                "phase": phase,
                "memoryCitation": None,
            },
        },
    }


def agent_message_delta(
    *,
    delta: str,
    phase: str | None = None,
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
    item_id: str = "message-final_answer",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "threadId": thread_id,
        "turnId": turn_id,
        "itemId": item_id,
        "delta": delta,
    }
    if phase is not None:
        payload["phase"] = phase
    return {
        "method": "item/agentMessage/delta",
        "payload": payload,
    }


def command_execution_started(
    *,
    command: str,
    cwd: str,
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
    item_id: str = "command-1",
) -> dict[str, Any]:
    return _command_execution(
        method="item/started",
        command=command,
        cwd=cwd,
        thread_id=thread_id,
        turn_id=turn_id,
        item_id=item_id,
        status="inProgress",
        aggregated_output=None,
        exit_code=None,
        duration_ms=None,
    )


def command_execution_completed(
    *,
    command: str,
    cwd: str,
    output: str,
    exit_code: int = 0,
    duration_ms: int = 1,
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
    item_id: str = "command-1",
) -> dict[str, Any]:
    return _command_execution(
        method="item/completed",
        command=command,
        cwd=cwd,
        thread_id=thread_id,
        turn_id=turn_id,
        item_id=item_id,
        status="completed" if exit_code == 0 else "failed",
        aggregated_output=output,
        exit_code=exit_code,
        duration_ms=duration_ms,
    )


def context_compaction_completed(
    *,
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
    item_id: str = "compact-1",
) -> dict[str, Any]:
    return {
        "method": "item/completed",
        "payload": {
            "threadId": thread_id,
            "turnId": turn_id,
            "item": {
                "type": "contextCompaction",
                "id": item_id,
                "status": "completed",
            },
        },
    }


def error_notification(
    *,
    message: str = "codex failed",
    error_type: str = "RuntimeError",
    code: str = "internal_error",
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
) -> dict[str, Any]:
    return {
        "method": "error",
        "payload": {
            "threadId": thread_id,
            "turnId": turn_id,
            "type": error_type,
            "message": message,
            "code": code,
        },
    }


def _command_execution(
    *,
    method: str,
    command: str,
    cwd: str,
    thread_id: str,
    turn_id: str,
    item_id: str,
    status: str,
    aggregated_output: str | None,
    exit_code: int | None,
    duration_ms: int | None,
) -> dict[str, Any]:
    return {
        "method": method,
        "payload": {
            "threadId": thread_id,
            "turnId": turn_id,
            "item": {
                "type": "commandExecution",
                "id": item_id,
                "command": command,
                "cwd": cwd,
                "status": status,
                "source": "model",
                "commandActions": [{"type": "unknown", "command": command}],
                "aggregatedOutput": aggregated_output,
                "exitCode": exit_code,
                "durationMs": duration_ms,
            },
        },
    }
