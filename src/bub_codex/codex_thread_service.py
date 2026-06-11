from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterator
from typing import Any, Callable

from .codex_client import DynamicToolSpec, ThreadStartOptions
from .notification_filter import record_belongs_to_thread


NotificationObserver = Callable[[Any], None]
InitialPromptFactory = Callable[[str, str], str]


@dataclass(frozen=True, slots=True)
class ThreadMaterialization:
    thread_id: str
    turn_id: str | None = None
    notification_records: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CodexTurn:
    thread_id: str
    turn_id: str
    notification_records: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class LowLevelCodexThreadService:
    """Codex thread lifecycle adapter backed by the low-level Python SDK client.

    `thread_start` allocates a thread id, but real SDK testing shows the thread is
    not resumable until an initial turn creates a rollout. Treat this adapter as
    a low-level building block, not the final production binding boundary.
    """

    def __init__(
        self,
        client: Any,
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

    def create_thread(self, *, cwd: str, anchor_id: str, intent: str) -> str:
        response = self._client.thread_start(
            ThreadStartOptions(
                cwd=cwd,
                approval_policy=self._approval_policy,
                sandbox=self._sandbox,
                dynamic_tools=self._dynamic_tools,
            ).to_app_server_json()
        )
        return str(response.thread.id)

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> str:
        return self.create_thread(cwd=cwd, anchor_id=anchor_id, intent=intent)

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


class MaterializingCodexThreadService:
    """Create a Codex thread and complete the initial materialization turn."""

    def __init__(
        self,
        client: Any,
        *,
        cwd: str,
        approval_policy: str = "never",
        sandbox: str = "danger-full-access",
        dynamic_tools: tuple[DynamicToolSpec, ...] = (),
        notification_observer: NotificationObserver | None = None,
        initial_prompt_factory: InitialPromptFactory | None = None,
    ) -> None:
        self._client = client
        self._cwd = cwd
        self._approval_policy = approval_policy
        self._sandbox = sandbox
        self._dynamic_tools = dynamic_tools
        self._notification_observer = notification_observer
        self._initial_prompt_factory = initial_prompt_factory or _default_initial_prompt

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> ThreadMaterialization:
        started = self._client.thread_start(
            ThreadStartOptions(
                cwd=cwd,
                approval_policy=self._approval_policy,
                sandbox=self._sandbox,
                dynamic_tools=self._dynamic_tools,
            ).to_app_server_json()
        )
        thread_id = str(started.thread.id)
        turn = self._client.turn_start(
            thread_id,
            self._initial_prompt_factory(anchor_id, intent),
            {"cwd": cwd},
        )
        turn_id = turn.turn.id
        notification_records: list[dict[str, Any]] = []
        try:
            while True:
                event = self._client.next_turn_notification(turn_id)
                record = _notification_record(event)
                if not record_belongs_to_thread(record, thread_id):
                    continue
                notification_records.append(record)
                if self._notification_observer:
                    self._notification_observer(event)
                if record["method"] == "turn/completed":
                    break
        finally:
            self._client.unregister_turn_notifications(turn_id)

        self._client.thread_read(thread_id, include_turns=True)
        return ThreadMaterialization(
            thread_id=thread_id,
            turn_id=turn_id,
            notification_records=tuple(notification_records),
        )

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
        turn_id = turn.turn.id
        notification_records: list[dict[str, Any]] = []
        try:
            while True:
                event = self._client.next_turn_notification(turn_id)
                record = _notification_record(event)
                if not record_belongs_to_thread(record, thread_id):
                    continue
                notification_records.append(record)
                if record["method"] == "turn/completed":
                    break
        finally:
            self._client.unregister_turn_notifications(turn_id)
        return CodexTurn(
            thread_id=thread_id,
            turn_id=turn_id,
            notification_records=tuple(notification_records),
        )

    def run_turn_stream_records(
        self,
        *,
        thread_id: str,
        cwd: str,
        prompt: str,
    ) -> Iterator[dict[str, Any]]:
        turn = self._client.turn_start(thread_id, prompt, {"cwd": cwd})
        turn_id = turn.turn.id
        try:
            while True:
                event = self._client.next_turn_notification(turn_id)
                record = {
                    **_notification_record(event),
                    "turn_id": turn_id,
                }
                if not record_belongs_to_thread(record, thread_id):
                    continue
                yield record
                if record["method"] == "turn/completed":
                    break
        finally:
            self._client.unregister_turn_notifications(turn_id)


def _default_initial_prompt(anchor_id: str, materialized_context: str) -> str:
    return (
        "Materialize this Bub Anchor as the starting context for this Codex thread. "
        "Do not answer or execute the user's task during materialization.\n\n"
        f"Anchor: {anchor_id}\n"
        f"Materialized context:\n{materialized_context}\n\n"
        "Reply only with a concise acknowledgement that the Anchor was materialized."
    )


def _notification_record(event: Any) -> dict[str, Any]:
    payload = getattr(event, "payload", None)
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json", by_alias=True, exclude_none=False)
    return {
        "method": getattr(event, "method", None),
        "payload_type": type(getattr(event, "payload", None)).__name__
        if getattr(event, "payload", None) is not None
        else None,
        "payload": payload,
    }
