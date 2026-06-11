from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol, TypeAlias

from .context_materialization import create_new_thread_anchor_events
from .codex_thread_service import ThreadMaterialization
from .materialization_projection import project_thread_materialization_events
from .runtime_adapter import facts_from_notification_record
from .runtime_diagnostics import runtime_error_event
from .runtime_resolution import RuntimeContextResolution
from .tape_events import JsonObject, TapeEvent
from .tape_store import TapeStore
from .new_thread_materialization import (
    build_initial_input,
    find_anchor_created,
    materialize_thread_binding_events,
    materialize_thread_binding_failed_events,
    select_context_refs,
)


RuntimeStartStatus = Literal["bootstrapped", "materialized", "resumed", "bind_failed"]
ContextUnavailableReason = Literal["thread_materialization_failed"]


class CodexThreadContextAdapter(Protocol):
    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> str | ThreadMaterialization:
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
    error: JsonObject | None = None

    def to_json(self) -> JsonObject:
        data = asdict(self)
        data["resolution"] = self.resolution.to_json()
        data["appended_events"] = [event.to_json() for event in self.appended_events]
        return data


@dataclass(frozen=True, slots=True)
class ExecutableContext:
    session_id: str
    tape_id: str
    anchor_id: str
    thread_id: str
    cwd: str
    source: Literal["bootstrapped", "materialized", "resumed"]
    appended_events: tuple[TapeEvent, ...]
    start: RuntimeStartResult


@dataclass(frozen=True, slots=True)
class ContextUnavailable:
    session_id: str
    tape_id: str
    anchor_id: str | None
    cwd: str
    reason: ContextUnavailableReason
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
                source=_runtime_context_source(start.status),
                appended_events=start.appended_events,
                start=start,
            )
        error = start.error or {"type": "RuntimeError", "message": "cannot run turn without a materialized Codex thread"}
        return ContextUnavailable(
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=start.anchor_id,
            cwd=cwd,
            reason="thread_materialization_failed",
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
        resolution = self.tape_store.resolve_runtime_context(session_id=session_id, tape_id=tape_id)

        if resolution.action == "resume_thread":
            assert resolution.thread_id is not None
            try:
                self.codex_threads.resume_thread(resolution.thread_id)
            except Exception as exc:
                diagnostic = runtime_error_event(
                    stage="thread_resume",
                    exc=exc,
                    session_id=session_id,
                    tape_id=tape_id,
                    anchor_id=resolution.anchor_id,
                    thread_id=resolution.thread_id,
                )
                self.tape_store.append(diagnostic)
                raise
            return RuntimeStartResult(
                status="resumed",
                resolution=resolution,
                anchor_id=resolution.anchor_id,
                thread_id=resolution.thread_id,
                appended_events=(),
            )

        if resolution.action == "bootstrap":
            anchor_events = create_new_thread_anchor_events(
                events,
                session_id=session_id,
                tape_id=tape_id,
                reason="session_start",
                intent=intent,
                owner="human",
                initiator="bub_runtime",
            )
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
                status="bootstrapped",
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
            status="materialized",
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
        status: Literal["bootstrapped", "materialized"],
        already_appended: tuple[TapeEvent, ...],
    ) -> RuntimeStartResult:
        anchor_id = _latest_anchor_id(base_events)
        if anchor_id is None:
            raise RuntimeError("cannot materialize thread without a committed Anchor")

        metadata = {"cwd": cwd, **(workspace_metadata or {})}
        materialization_input = _materialization_input_for_anchor(
            base_events,
            anchor_id=anchor_id,
            intent=intent,
            workspace_metadata=metadata,
        )
        try:
            materialization = _normalize_thread_materialization(
                self.codex_threads.materialize_thread(
                    cwd=cwd,
                    anchor_id=anchor_id,
                    intent=materialization_input,
                )
            )
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            failed_events = materialize_thread_binding_failed_events(
                base_events,
                session_id=session_id,
                tape_id=tape_id,
                intent=intent,
                workspace_metadata=metadata,
                anchor_id=anchor_id,
                reason=reason,
                error=error,
            )
            self.tape_store.append_many(failed_events)
            return RuntimeStartResult(
                status="bind_failed",
                resolution=resolution,
                anchor_id=anchor_id,
                thread_id=None,
                appended_events=(*already_appended, *failed_events),
                error=error,
            )

        binding_events = materialize_thread_binding_events(
            base_events,
            session_id=session_id,
            tape_id=tape_id,
            thread_id=materialization.thread_id,
            intent=intent,
            workspace_metadata=metadata,
            anchor_id=anchor_id,
            reason=reason,
            materialization_turn_id=materialization.turn_id,
        )
        materialized_event = binding_events[0]
        bound_event = binding_events[1]
        materialization_facts = _facts_from_materialization_notifications(materialization)
        materialization_events = project_thread_materialization_events(
            materialization_facts,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
            materialization_id=str(materialized_event.payload.get("materialization_id")),
            materialization_turn_id=materialization.turn_id,
        )
        appended_events = (*already_appended, materialized_event, *materialization_events, bound_event)
        self.tape_store.append_many([materialized_event, *materialization_events, bound_event])
        return RuntimeStartResult(
            status=status,
            resolution=resolution,
            anchor_id=anchor_id,
            thread_id=materialization.thread_id,
            appended_events=appended_events,
        )


def _latest_anchor_id(events: list[TapeEvent]) -> str | None:
    for event in reversed(events):
        if event.type == "bub.anchor.created" and event.anchor_id:
            return event.anchor_id
    return None


def _runtime_context_source(
    status: RuntimeStartStatus,
) -> Literal["bootstrapped", "materialized", "resumed"]:
    if status in ("bootstrapped", "materialized", "resumed"):
        return status
    raise RuntimeError(f"runtime context is not executable: {status}")


def _materialization_input_for_anchor(
    events: list[TapeEvent],
    *,
    anchor_id: str,
    intent: str,
    workspace_metadata: JsonObject,
) -> str:
    anchor = find_anchor_created(events, anchor_id)
    if anchor is None:
        raise RuntimeError("cannot materialize thread without a committed Anchor")
    return build_initial_input(
        anchor=anchor,
        intent=intent,
        selected_refs=select_context_refs(events, anchor, recent_event_limit=8),
        workspace_metadata=workspace_metadata,
    )


def _normalize_thread_materialization(value: str | ThreadMaterialization) -> ThreadMaterialization:
    if isinstance(value, ThreadMaterialization):
        return value
    return ThreadMaterialization(thread_id=value)


def _facts_from_materialization_notifications(materialization: ThreadMaterialization):
    facts = []
    for record in materialization.notification_records:
        facts.extend(
            facts_from_notification_record(
                {
                    **record,
                    "turn_id": materialization.turn_id,
                },
                source="sdk_stream:thread_materialization",
            )
        )
    return facts

