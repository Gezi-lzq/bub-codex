from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from .context_materialization import (
    create_new_thread_anchor_events,
    materialize_thread_binding_events,
    materialize_thread_binding_failed_events,
)
from .codex_thread_service import CodexTurn, ThreadMaterialization
from .materialization_projection import project_thread_materialization_events
from .runtime_adapter import facts_from_notification_record
from .runtime_resolution import RuntimeContextResolution
from .tape_events import JsonObject, TapeEvent
from .tape_store import TapeStore
from .turn_projection import project_user_turn_events


RuntimeStartStatus = Literal["bootstrapped", "materialized", "resumed", "bind_failed"]


class CodexThreadService(Protocol):
    """Minimal thread lifecycle boundary used by the runtime facade."""

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> str | ThreadMaterialization:
        ...

    def resume_thread(self, thread_id: str) -> None:
        ...

    def run_turn(self, *, thread_id: str, cwd: str, prompt: str) -> CodexTurn:
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
            self.codex_threads.resume_thread(resolution.thread_id)
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
        start = self.ensure_thread_context(
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
        try:
            materialization = _normalize_thread_materialization(
                self.codex_threads.materialize_thread(cwd=cwd, anchor_id=anchor_id, intent=intent)
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
