"""Tape-backed runtime state machine.

This module is the only owner of create-anchor, create-thread, bind-thread, and
resume-thread decisions. It records startup context evidence, but it does not
run Codex turns or stream model output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

from .codex_thread_service import ThreadMaterialization
from .runtime_diagnostics import runtime_error_event, runtime_error_summary
from .json_utils import JsonObject
from .tape_events import TapeEvent
from .tape_store import TapeStore
from .new_thread_materialization import (
    active_thread_id_for_anchor,
    create_new_thread_anchor_events,
    latest_anchor_created,
    materialize_thread_binding_events,
    materialize_thread_binding_failed_events,
    prepare_materialized_context,
)


RuntimeAction = Literal["create_anchor", "materialize_thread", "resume_thread"]
RuntimeStartStatus = Literal[
    "created_anchor_and_materialized",
    "materialized_existing_anchor",
    "resumed_existing_thread",
    "materialization_failed",
]


@dataclass(frozen=True, slots=True)
class RuntimeContextResolution:
    action: RuntimeAction
    anchor_id: str | None
    thread_id: str | None
    reason: str


def resolve_runtime_context(events: list[TapeEvent]) -> RuntimeContextResolution:
    anchor = latest_anchor_created(events)
    if anchor is None:
        return RuntimeContextResolution(
            action="create_anchor",
            anchor_id=None,
            thread_id=None,
            reason="no_committed_anchor",
        )

    thread_id = active_thread_id_for_anchor(events, anchor.anchor_id)
    if thread_id is None:
        return RuntimeContextResolution(
            action="materialize_thread",
            anchor_id=anchor.anchor_id,
            thread_id=None,
            reason="latest_anchor_has_no_thread_binding",
        )

    return RuntimeContextResolution(
        action="resume_thread",
        anchor_id=anchor.anchor_id,
        thread_id=thread_id,
        reason="latest_anchor_has_thread_binding",
    )


class CodexThreadContextAdapter(Protocol):
    def materialize_thread(self, *, cwd: str, anchor_id: str) -> ThreadMaterialization:
        ...

    def resume_thread(self, thread_id: str) -> None:
        ...


@dataclass(frozen=True, slots=True)
class RuntimeStartResult:
    status: RuntimeStartStatus
    resolution: RuntimeContextResolution
    anchor_id: str | None
    thread_id: str | None
    appended_events: tuple[TapeEvent, ...]
    startup_context: str | None = None
    error: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class ExecutableContext:
    session_id: str
    tape_id: str
    anchor_id: str
    thread_id: str
    cwd: str
    appended_events: tuple[TapeEvent, ...]
    start: RuntimeStartResult


@dataclass(frozen=True, slots=True)
class ContextUnavailable:
    session_id: str
    tape_id: str
    anchor_id: str | None
    cwd: str
    error: JsonObject
    appended_events: tuple[TapeEvent, ...]
    start: RuntimeStartResult


RuntimeContext: TypeAlias = ExecutableContext | ContextUnavailable


@dataclass(slots=True)
class RuntimeContextKernel:
    tape_store: TapeStore
    codex_threads: CodexThreadContextAdapter

    def ensure_executable_context(
        self,
        *,
        session_id: str,
        tape_id: str,
        cwd: str,
        intent: str,
        workspace_metadata: JsonObject | None = None,
    ) -> RuntimeContext:
        start = self.ensure_thread_context(
            session_id=session_id,
            tape_id=tape_id,
            cwd=cwd,
            intent=intent,
            workspace_metadata=workspace_metadata,
        )
        if start.thread_id is not None and start.anchor_id is not None:
            return ExecutableContext(
                session_id=session_id,
                tape_id=tape_id,
                anchor_id=start.anchor_id,
                thread_id=start.thread_id,
                cwd=cwd,
                appended_events=start.appended_events,
                start=start,
            )
        error = start.error or {"type": "RuntimeError", "message": "cannot run turn without a bound Codex thread"}
        return ContextUnavailable(
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=start.anchor_id,
            cwd=cwd,
            error=error,
            appended_events=start.appended_events,
            start=start,
        )

    def ensure_thread_context(
        self,
        *,
        session_id: str,
        tape_id: str,
        cwd: str,
        intent: str,
        workspace_metadata: JsonObject | None = None,
    ) -> RuntimeStartResult:
        events = self.tape_store.events(session_id=session_id, tape_id=tape_id)
        resolution = resolve_runtime_context(events)

        if resolution.action == "resume_thread":
            thread_id = resolution.thread_id
            if thread_id is None:
                raise RuntimeError("resume_thread resolution must include thread_id")
            try:
                self.codex_threads.resume_thread(thread_id)
            except Exception as exc:
                diagnostic = runtime_error_event(
                    stage="thread_resume",
                    exc=exc,
                    session_id=session_id,
                    tape_id=tape_id,
                    anchor_id=resolution.anchor_id,
                    thread_id=thread_id,
                )
                self.tape_store.append(diagnostic)
                raise
            return RuntimeStartResult(
                status="resumed_existing_thread",
                resolution=resolution,
                anchor_id=resolution.anchor_id,
                thread_id=thread_id,
                appended_events=(),
            )

        if resolution.action == "create_anchor":
            anchor_creation = create_new_thread_anchor_events(
                events,
                session_id=session_id,
                tape_id=tape_id,
                reason="session_start",
                intent=intent,
                owner="human",
                initiator="bub_runtime",
            )
            anchor_events = (anchor_creation.started, anchor_creation.created)
            self.tape_store.append_many(anchor_events)
            return self._materialize_from_latest_anchor(
                base_events=[*events, *anchor_events],
                resolution=resolution,
                session_id=session_id,
                tape_id=tape_id,
                cwd=cwd,
                intent=intent,
                workspace_metadata=workspace_metadata,
                reason="session_start",
                status="created_anchor_and_materialized",
                already_appended=tuple(anchor_events),
            )

        return self._materialize_from_latest_anchor(
            base_events=events,
            resolution=resolution,
            session_id=session_id,
            tape_id=tape_id,
            cwd=cwd,
            intent=intent,
            workspace_metadata=workspace_metadata,
            reason="anchor_materialization",
            status="materialized_existing_anchor",
            already_appended=(),
        )

    def _materialize_from_latest_anchor(
        self,
        *,
        base_events: list[TapeEvent],
        resolution: RuntimeContextResolution,
        session_id: str,
        tape_id: str,
        cwd: str,
        intent: str,
        workspace_metadata: JsonObject | None,
        reason: str,
        status: Literal["created_anchor_and_materialized", "materialized_existing_anchor"],
        already_appended: tuple[TapeEvent, ...],
    ) -> RuntimeStartResult:
        anchor = latest_anchor_created(base_events)
        if anchor is None:
            raise RuntimeError("cannot materialize thread without a committed Anchor")
        anchor_id = anchor.anchor_id

        metadata = {"cwd": cwd, **(workspace_metadata or {})}
        materialized_context = prepare_materialized_context(
            base_events,
            intent=intent,
            workspace_metadata=metadata,
            anchor_id=anchor_id,
        )
        try:
            materialization = self.codex_threads.materialize_thread(
                cwd=cwd,
                anchor_id=anchor_id,
            )
        except Exception as exc:
            error = runtime_error_summary(exc)
            failed_binding = materialize_thread_binding_failed_events(
                session_id=session_id,
                tape_id=tape_id,
                intent=intent,
                materialized_context=materialized_context,
                reason=reason,
                error=error,
            )
            failed_events = (failed_binding.materialized, failed_binding.failed)
            self.tape_store.append_many(failed_events)
            return RuntimeStartResult(
                status="materialization_failed",
                resolution=resolution,
                anchor_id=anchor_id,
                thread_id=None,
                appended_events=(*already_appended, *failed_events),
                error=error,
            )

        binding = materialize_thread_binding_events(
            base_events,
            session_id=session_id,
            tape_id=tape_id,
            thread_id=materialization.thread_id,
            intent=intent,
            materialized_context=materialized_context,
            reason=reason,
            materialization_turn_id=materialization.turn_id,
        )
        materialized_event = binding.materialized
        bound_event = binding.bound
        appended_events = (*already_appended, materialized_event, bound_event)
        self.tape_store.append_many((materialized_event, bound_event))
        return RuntimeStartResult(
            status=status,
            resolution=resolution,
            anchor_id=anchor_id,
            thread_id=materialization.thread_id,
            appended_events=appended_events,
            startup_context=materialized_context.text,
        )
