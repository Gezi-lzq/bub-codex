from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from republic import AsyncStreamEvents, StreamEvent, StreamState

from bub.types import State

from .plugin import _default_tape_id, _prompt_text
from .runtime import BubCodexRuntime
from .notification_filter import record_belongs_to_thread
from .runtime_diagnostics import runtime_error_event
from .tape_events import JsonObject
from .turn_translator import CodexTurnTranslator, StreamDecision, stream_error_decisions


class CodexTurnStreamService(Protocol):
    def run_turn_stream_records(
        self,
        *,
        thread_id: str,
        cwd: str,
        prompt: str,
    ) -> Iterable[JsonObject]:
        ...


@dataclass(slots=True)
class BubCodexLiveRuntimeStreamService:
    runtime: BubCodexRuntime
    codex_turn_streams: CodexTurnStreamService
    tape_id_factory: Any | None = None

    async def run_stream(
        self,
        *,
        prompt: str | list[dict],
        session_id: str,
        state: State,
    ) -> AsyncStreamEvents:
        prompt_text = _prompt_text(prompt)
        cwd = str(state.get("_runtime_workspace") or ".")
        tape_id_factory = self.tape_id_factory or _default_tape_id
        tape_id = str(tape_id_factory(session_id, state))

        try:
            start = self.runtime.ensure_thread_context(
                session_id=session_id,
                tape_id=tape_id,
                cwd=cwd,
                intent=prompt_text,
                workspace_metadata={"cwd": cwd},
            )
        except Exception as exc:
            return _stream_error(exc)

        if start.thread_id is None:
            return _stream_error(RuntimeError("cannot run turn without a materialized Codex thread"))

        async def fixed_iterator():
            async for stream_event in _iter_live_turn_events(
                runtime=self.runtime,
                stream_service=self.codex_turn_streams,
                session_id=session_id,
                tape_id=tape_id,
                anchor_id=start.anchor_id,
                thread_id=start.thread_id,
                cwd=cwd,
                prompt=prompt_text,
            ):
                yield stream_event

        return AsyncStreamEvents(fixed_iterator(), state=StreamState())


async def _iter_live_turn_events(
    *,
    runtime: BubCodexRuntime,
    stream_service: CodexTurnStreamService,
    session_id: str,
    tape_id: str,
    anchor_id: str | None,
    thread_id: str,
    cwd: str,
    prompt: str,
):
    translator = CodexTurnTranslator(
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
    )
    try:
        for record in stream_service.run_turn_stream_records(
            thread_id=thread_id,
            cwd=cwd,
            prompt=prompt,
        ):
            if not record_belongs_to_thread(record, thread_id):
                continue
            translation = translator.accept(record)
            runtime.tape_store.append_many(translation.tape_events)
            for decision in translation.stream_decisions:
                yield _to_stream_event(decision)
    except Exception as exc:
        runtime.tape_store.append(
            runtime_error_event(
                stage="turn_stream",
                exc=exc,
                session_id=session_id,
                tape_id=tape_id,
                anchor_id=anchor_id,
                thread_id=thread_id,
            )
        )
        for decision in stream_error_decisions(exc):
            yield _to_stream_event(decision)
        return
    for decision in translator.finish().stream_decisions:
        yield _to_stream_event(decision)


def _stream_error(exc: Exception) -> AsyncStreamEvents:
    async def iterator():
        for decision in stream_error_decisions(exc):
            yield _to_stream_event(decision)

    return AsyncStreamEvents(iterator(), state=StreamState())


def _to_stream_event(decision: StreamDecision) -> StreamEvent:
    return StreamEvent(decision.kind, decision.data)
