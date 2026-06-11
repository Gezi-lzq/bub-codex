from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .codex_thread_service import CodexTurn
from .runtime_adapter import facts_from_notification_record
from .runtime_context import (
    CodexThreadContextAdapter,
    ContextUnavailable,
    ExecutableContext,
    RuntimeContext,
    RuntimeContextKernel,
    RuntimeStartResult,
)
from .tape_events import JsonObject, TapeEvent
from .tape_store import TapeStore
from .turn_projection import project_user_turn_events


class CodexThreadService(CodexThreadContextAdapter, Protocol):
    """Codex thread adapter with the batch/reference user-turn method."""

    def run_turn(self, *, thread_id: str, cwd: str, prompt: str) -> CodexTurn:
        ...


@dataclass(frozen=True, slots=True)
class RuntimeTurnResult:
    start: RuntimeStartResult
    thread_id: str
    turn_id: str
    appended_events: tuple[TapeEvent, ...]

    def to_json(self) -> JsonObject:
        return {
            "start": self.start.to_json(),
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "appended_events": [event.to_json() for event in self.appended_events],
        }


@dataclass(slots=True)
class BubCodexRuntime:
    tape_store: TapeStore
    codex_threads: CodexThreadService
    context_kernel: RuntimeContextKernel = field(init=False)

    def __post_init__(self) -> None:
        self.context_kernel = RuntimeContextKernel(self.tape_store, self.codex_threads)

    def run_turn(
        self,
        *,
        session_id: str,
        tape_id: str,
        cwd: str,
        prompt: str,
        intent: str | None = None,
        workspace_metadata: JsonObject | None = None,
    ) -> RuntimeTurnResult:
        start = self.context_kernel.ensure_thread_context(
            session_id=session_id,
            tape_id=tape_id,
            cwd=cwd,
            intent=intent or prompt,
            workspace_metadata=workspace_metadata,
        )
        if start.thread_id is None:
            raise RuntimeError("cannot run turn without a materialized Codex thread")

        turn = self.codex_threads.run_turn(
            thread_id=start.thread_id,
            cwd=cwd,
            prompt=prompt,
        )
        facts = _facts_from_turn_notifications(turn)
        turn_events = project_user_turn_events(
            facts,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=start.anchor_id,
        )
        self.tape_store.append_many(turn_events)
        return RuntimeTurnResult(
            start=start,
            thread_id=turn.thread_id,
            turn_id=turn.turn_id,
            appended_events=tuple(turn_events),
        )


def _facts_from_turn_notifications(turn: CodexTurn):
    facts = []
    for record in turn.notification_records:
        facts.extend(
            facts_from_notification_record(
                {
                    **record,
                    "turn_id": turn.turn_id,
                },
                source="sdk_stream:user_turn",
            )
        )
    return facts


__all__ = [
    "BubCodexRuntime",
    "CodexThreadService",
    "ContextUnavailable",
    "ExecutableContext",
    "RuntimeContext",
    "RuntimeStartResult",
    "RuntimeTurnResult",
]
