from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from republic import AsyncStreamEvents, StreamState

from bub.types import State

from .notification_filter import record_belongs_to_thread
from .runtime_context import ContextUnavailable, ExecutableContext, RuntimeContextKernel
from .runtime_diagnostics import runtime_error_event
from .stream_utils import default_tape_id, prompt_text as extract_prompt_text, to_stream_event
from .tape_events import JsonObject
from .turn_translator import CodexTurnTranslator, StreamDecision, stream_error_decisions


class CodexTurnStreamService(Protocol):
    def start_turn_stream(
        self,
        *,
        thread_id: str,
        cwd: str,
        prompt: str,
    ) -> Any:
        ...


@dataclass(slots=True)
class BubCodexLiveRuntimeStreamService:
    context_kernel: RuntimeContextKernel
    tape_store: Any
    codex_turn_streams: CodexTurnStreamService
    tape_id_factory: Any | None = None

    def close(self) -> None:
        close = getattr(self.codex_turn_streams, "close", None)
        if callable(close):
            close()

    async def run_stream(
        self,
        *,
        prompt: str | list[dict],
        session_id: str,
        state: State,
    ) -> AsyncStreamEvents:
        prompt_text = extract_prompt_text(prompt)
        cwd = str(state.get("_runtime_workspace") or ".")
        tape_id_factory = self.tape_id_factory or default_tape_id
        tape_id = str(tape_id_factory(session_id, state))

        try:
            context = self.context_kernel.ensure_executable_context(
                session_id=session_id,
                tape_id=tape_id,
                cwd=cwd,
                intent=prompt_text,
                workspace_metadata={"cwd": cwd},
            )
        except Exception as exc:
            return _stream_error(exc)

        if isinstance(context, ContextUnavailable):
            return _stream_context_unavailable(context)

        async def fixed_iterator():
            async for stream_event in _iter_live_turn_events(
                context_kernel=self.context_kernel,
                tape_store=self.tape_store,
                stream_service=self.codex_turn_streams,
                session_id=session_id,
                tape_id=tape_id,
                context=context,
                cwd=cwd,
                prompt=prompt_text,
            ):
                yield stream_event

        return AsyncStreamEvents(fixed_iterator(), state=StreamState())


async def _iter_live_turn_events(
    *,
    context_kernel: RuntimeContextKernel,
    tape_store: Any,
    stream_service: CodexTurnStreamService,
    session_id: str,
    tape_id: str,
    context: ExecutableContext,
    cwd: str,
    prompt: str,
):
    translator = CodexTurnTranslator(
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=context.anchor_id,
    )
    turn_session = stream_service.start_turn_stream(
        thread_id=context.thread_id,
        cwd=cwd,
        prompt=prompt,
    )
    try:
        for record in turn_session.records():
            if not record_belongs_to_thread(record, context.thread_id):
                continue
            translation = translator.accept(record)
            tape_store.append_many(translation.tape_events)
            for decision in translation.stream_decisions:
                yield to_stream_event(decision)
    except Exception as exc:
        tape_store.append(
            runtime_error_event(
                stage="turn_stream",
                exc=exc,
                session_id=session_id,
                tape_id=tape_id,
                anchor_id=context.anchor_id,
                thread_id=context.thread_id,
            )
        )
        for decision in stream_error_decisions(exc):
            yield to_stream_event(decision)
        return
    finally:
        turn_session.close()
    for decision in translator.finish().stream_decisions:
        yield to_stream_event(decision)


def _stream_error(exc: Exception) -> AsyncStreamEvents:
    async def iterator():
        for decision in stream_error_decisions(exc):
            yield to_stream_event(decision)

    return AsyncStreamEvents(iterator(), state=StreamState())


def _stream_context_unavailable(context: ContextUnavailable) -> AsyncStreamEvents:
    error_type = str(context.error.get("type") or "RuntimeError")
    message = str(context.error.get("message") or "runtime context is unavailable")
    text = f"{error_type}: {message}"

    async def iterator():
        for decision in (
            StreamDecision("error", {"kind": "unknown", "message": message}),
            StreamDecision("text", {"delta": text}),
            StreamDecision("final", {"text": text, "ok": False}),
        ):
            yield to_stream_event(decision)

    return AsyncStreamEvents(iterator(), state=StreamState())
