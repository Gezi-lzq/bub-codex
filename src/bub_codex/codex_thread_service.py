"""Codex thread and turn SDK boundary.

This module is the only place that should call Codex thread/turn methods. It
creates/resumes threads, starts real user turns, and converts SDK notifications
to JSON-like records without adding hidden model turns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterator
from typing import Any, Protocol

from .codex_client import DynamicToolSpec, ThreadStartOptions
from .json_utils import JsonObject
from .runtime_adapter import record_belongs_to_thread


class CodexClientPort(Protocol):
    def thread_start(self, options: JsonObject) -> Any:
        ...

    def thread_resume(self, thread_id: str, params: JsonObject) -> Any:
        ...

    def turn_start(self, thread_id: str, prompt: str, options: JsonObject) -> Any:
        ...

    def next_turn_notification(self, turn_id: str) -> Any:
        ...

    def turn_steer(self, thread_id: str, expected_turn_id: str, input_items: str) -> Any:
        ...

    def unregister_turn_notifications(self, turn_id: str) -> None:
        ...


@dataclass(frozen=True, slots=True)
class ThreadMaterialization:
    thread_id: str
    turn_id: str | None = None
    notification_records: tuple[JsonObject, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CodexTurn:
    thread_id: str
    turn_id: str
    notification_records: tuple[JsonObject, ...] = field(default_factory=tuple)


@dataclass(slots=True)
class CodexTurnSession:
    client: CodexClientPort
    turn_id: str
    thread_id: str

    def records(self) -> Iterator[JsonObject]:
        yield from _iter_turn_records(self.client, turn_id=self.turn_id, thread_id=self.thread_id)

    def steer(self, input_text: str) -> None:
        self.client.turn_steer(self.thread_id, self.turn_id, input_text)

    def close(self) -> None:
        self.client.unregister_turn_notifications(self.turn_id)


class MaterializingCodexThreadService:
    """Create and resume Codex threads without adding hidden model turns."""

    def __init__(
        self,
        client: CodexClientPort,
        *,
        cwd: str,
        approval_policy: str = "never",
        sandbox: str = "danger-full-access",
        dynamic_tools: tuple[DynamicToolSpec, ...] = (),
    ) -> None:
        self._client = client
        self._cwd = cwd
        self._approval_policy = approval_policy
        self._sandbox = sandbox
        self._dynamic_tools = dynamic_tools

    def materialize_thread(self, *, cwd: str, anchor_id: str) -> ThreadMaterialization:
        started = self._client.thread_start(
            ThreadStartOptions(
                cwd=cwd,
                approval_policy=self._approval_policy,
                sandbox=self._sandbox,
                dynamic_tools=self._dynamic_tools,
            ).to_app_server_json()
        )
        thread_id = str(started.thread.id)
        return ThreadMaterialization(thread_id=thread_id)

    def resume_thread(self, thread_id: str) -> None:
        self._client.thread_resume(
            thread_id,
            {
                "cwd": self._cwd,
                "approvalPolicy": self._approval_policy,
                "sandbox": self._sandbox,
            },
        )

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def run_turn(self, *, thread_id: str, cwd: str, prompt: str) -> CodexTurn:
        turn = self._client.turn_start(thread_id, prompt, {"cwd": cwd})
        turn_id = str(turn.turn.id)
        notification_records = _collect_turn_records(self._client, turn_id=turn_id, thread_id=thread_id)
        return CodexTurn(
            thread_id=thread_id,
            turn_id=turn_id,
            notification_records=notification_records,
        )

    def start_turn_stream(
        self,
        *,
        thread_id: str,
        cwd: str,
        prompt: str,
    ) -> CodexTurnSession:
        turn = self._client.turn_start(thread_id, prompt, {"cwd": cwd})
        return CodexTurnSession(
            client=self._client,
            turn_id=str(turn.turn.id),
            thread_id=thread_id,
        )


def _collect_turn_records(client: CodexClientPort, *, turn_id: str, thread_id: str) -> tuple[JsonObject, ...]:
    try:
        return tuple(_iter_turn_records(client, turn_id=turn_id, thread_id=thread_id))
    finally:
        client.unregister_turn_notifications(turn_id)


def _iter_turn_records(client: CodexClientPort, *, turn_id: str, thread_id: str) -> Iterator[JsonObject]:
    while True:
        event = client.next_turn_notification(turn_id)
        record = {
            **_notification_record(event),
            "turn_id": turn_id,
        }
        if not record_belongs_to_thread(record, thread_id):
            continue
        yield record
        if record["method"] == "turn/completed":
            break


def _notification_record(event: Any) -> JsonObject:
    raw_payload = getattr(event, "payload", None)
    payload = raw_payload
    if hasattr(raw_payload, "model_dump"):
        payload = raw_payload.model_dump(mode="json", by_alias=True, exclude_none=False)
    return {
        "method": getattr(event, "method", None),
        "payload_type": type(raw_payload).__name__ if raw_payload is not None else None,
        "payload": payload,
    }
