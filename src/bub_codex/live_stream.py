"""Bub live stream runtime.

This module owns streaming side effects: consuming Codex turn records, appending
tape events, and emitting Bub stream events. It relies on `runtime_context.py`
for thread state decisions and on `notification_translator.py` for notification
mapping.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from republic import AsyncStreamEvents, StreamEvent, StreamState

from bub.envelope import content_of
from bub.types import State

from .runtime_adapter import record_belongs_to_thread
from .runtime_context import ContextUnavailable, ExecutableContext, RuntimeContextKernel
from .runtime_diagnostics import runtime_error_event
from .startup_context import prompt_with_startup_context
from .stream_utils import default_tape_id, prompt_text as extract_prompt_text
from .json_utils import JsonObject
from .tape_store import TapeStore
from .notification_translator import BubCodexNotificationTranslator, stream_error_events


STEERING_POLL_INTERVAL_SECONDS = 0.05


class CodexTurnSession(Protocol):
    def records(self) -> Iterable[JsonObject]:
        ...

    def steer(self, input_text: str) -> None:
        ...

    def close(self) -> None:
        ...


class CodexTurnStreamService(Protocol):
    def start_turn_stream(
        self,
        *,
        thread_id: str,
        cwd: str,
        prompt: str,
    ) -> CodexTurnSession:
        ...


class ToolRuntimeContext(Protocol):
    def update(
        self,
        *,
        session_id: str,
        tape_id: str,
        cwd: str,
        anchor_id: str | None,
        state: State,
    ) -> None:
        ...

    def register_turn_context(
        self,
        *,
        thread_id: str,
        turn_id: str | None,
        session_id: str,
        tape_id: str,
        cwd: str,
        anchor_id: str | None,
        state: State,
    ) -> None:
        ...

    def clear_turn_context(self, *, thread_id: str, turn_id: str | None) -> None:
        ...


@dataclass(slots=True)
class BubCodexLiveRuntimeStreamService:
    context_kernel: RuntimeContextKernel
    tape_store: TapeStore
    codex_turn_streams: CodexTurnStreamService
    tape_id_factory: Callable[[str, State], str] | None = None
    tool_runtime_context: ToolRuntimeContext | None = None

    async def close(self) -> None:
        close = getattr(self.codex_turn_streams, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
        close_tape_store = getattr(self.tape_store, "close", None)
        if callable(close_tape_store):
            result = close_tape_store()
            if inspect.isawaitable(result):
                await result

    def current_tape_store(self) -> TapeStore | None:
        return self.tape_store

    def set_tape_store(self, tape_store: TapeStore) -> None:
        self.tape_store = tape_store
        self.context_kernel.tape_store = tape_store

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
        _bind_tool_runtime_loop(self.tool_runtime_context)
        _update_tool_runtime_context(
            self.tool_runtime_context,
            session_id=session_id,
            tape_id=tape_id,
            cwd=cwd,
            anchor_id=None,
            state=state,
        )

        try:
            context = await self.context_kernel.ensure_executable_context(
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

        _update_tool_runtime_context(
            self.tool_runtime_context,
            session_id=session_id,
            tape_id=tape_id,
            cwd=cwd,
            anchor_id=context.anchor_id,
            state=state,
        )

        async def fixed_iterator():
            async for stream_event in _iter_live_turn_events(
                tape_store=self.tape_store,
                stream_service=self.codex_turn_streams,
                session_id=session_id,
                tape_id=tape_id,
                context=context,
                cwd=cwd,
                prompt=prompt_text,
                state=state,
                tool_runtime_context=self.tool_runtime_context,
            ):
                yield stream_event

        return AsyncStreamEvents(fixed_iterator(), state=StreamState())


async def _iter_live_turn_events(
    *,
    tape_store: TapeStore,
    stream_service: CodexTurnStreamService,
    session_id: str,
    tape_id: str,
    context: ExecutableContext,
    cwd: str,
    prompt: str,
    state: State,
    tool_runtime_context: ToolRuntimeContext | None,
):
    translator = BubCodexNotificationTranslator(
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=context.anchor_id,
    )
    turn_session = stream_service.start_turn_stream(
        thread_id=context.thread_id,
        cwd=cwd,
        prompt=prompt_with_startup_context(prompt=prompt, startup_context=context.start.startup_context),
    )
    turn_id = _turn_session_id(turn_session)
    _register_tool_runtime_turn_context(
        tool_runtime_context,
        session_id=session_id,
        tape_id=tape_id,
        context=context,
        cwd=cwd,
        state=state,
        turn_id=turn_id,
    )
    try:
        async for record in _iter_turn_records_with_steering(turn_session, state=state):
            if not record_belongs_to_thread(record, context.thread_id):
                continue
            translation = translator.translate(record)
            await tape_store.append_many(translation.tape_events)
            for stream_event in translation.stream_events:
                yield stream_event
    except Exception as exc:
        await tape_store.append(
            runtime_error_event(
                stage="turn_stream",
                exc=exc,
                session_id=session_id,
                tape_id=tape_id,
                anchor_id=context.anchor_id,
                thread_id=context.thread_id,
            )
        )
        for stream_event in stream_error_events(exc):
            yield stream_event
        return
    finally:
        _clear_tool_runtime_turn_context(
            tool_runtime_context,
            thread_id=context.thread_id,
            turn_id=turn_id,
        )
        turn_session.close()
    for stream_event in translator.finish().stream_events:
        yield stream_event


async def _iter_turn_records_with_steering(
    turn_session: CodexTurnSession,
    *,
    state: State,
):
    steering = state.get("_runtime_steering")
    if not _can_drain_steering(steering):
        for record in turn_session.records():
            yield record
        return

    records = iter(turn_session.records())
    sentinel = object()
    while True:
        next_record = asyncio.create_task(asyncio.to_thread(next, records, sentinel))
        while not next_record.done():
            _drain_steering(turn_session, steering)
            await asyncio.sleep(STEERING_POLL_INTERVAL_SECONDS)
        record = await next_record
        _drain_steering(turn_session, steering)
        if record is sentinel:
            break
        yield record


def _can_drain_steering(steering: Any) -> bool:
    return callable(getattr(steering, "get_nowait", None))


def _drain_steering(turn_session: CodexTurnSession, steering: Any) -> None:
    get_nowait = getattr(steering, "get_nowait", None)
    if not callable(get_nowait):
        return
    while True:
        message = get_nowait()
        if message is None:
            return
        text = content_of(message).strip()
        if text:
            turn_session.steer(text)


def _stream_error(exc: Exception) -> AsyncStreamEvents:
    async def iterator():
        for stream_event in stream_error_events(exc):
            yield stream_event

    return AsyncStreamEvents(iterator(), state=StreamState())


def _stream_context_unavailable(context: ContextUnavailable) -> AsyncStreamEvents:
    error_type = str(context.error.get("type") or "RuntimeError")
    message = str(context.error.get("message") or "runtime context is unavailable")
    text = f"{error_type}: {message}"

    async def iterator():
        for stream_event in (
            StreamEvent("error", {"kind": "unknown", "message": message}),
            StreamEvent("text", {"delta": text}),
            StreamEvent("final", {"text": text, "ok": False}),
        ):
            yield stream_event

    return AsyncStreamEvents(iterator(), state=StreamState())


def _update_tool_runtime_context(
    tool_runtime_context: ToolRuntimeContext | None,
    *,
    session_id: str,
    tape_id: str,
    cwd: str,
    anchor_id: str | None,
    state: State,
) -> None:
    if tool_runtime_context is None:
        return
    tool_runtime_context.update(
        session_id=session_id,
        tape_id=tape_id,
        cwd=cwd,
        anchor_id=anchor_id,
        state=state,
    )


def _bind_tool_runtime_loop(tool_runtime_context: ToolRuntimeContext | None) -> None:
    bind = getattr(tool_runtime_context, "bind_event_loop", None)
    if callable(bind):
        bind(asyncio.get_running_loop())


def _turn_session_id(turn_session: CodexTurnSession) -> str | None:
    turn_id = getattr(turn_session, "turn_id", None)
    return str(turn_id) if turn_id is not None else None


def _register_tool_runtime_turn_context(
    tool_runtime_context: ToolRuntimeContext | None,
    *,
    session_id: str,
    tape_id: str,
    context: ExecutableContext,
    cwd: str,
    state: State,
    turn_id: str | None,
) -> None:
    register = getattr(tool_runtime_context, "register_turn_context", None)
    if callable(register):
        register(
            thread_id=context.thread_id,
            turn_id=turn_id,
            session_id=session_id,
            tape_id=tape_id,
            cwd=cwd,
            anchor_id=context.anchor_id,
            state=state,
        )


def _clear_tool_runtime_turn_context(
    tool_runtime_context: ToolRuntimeContext | None,
    *,
    thread_id: str,
    turn_id: str | None,
) -> None:
    clear = getattr(tool_runtime_context, "clear_turn_context", None)
    if callable(clear):
        clear(thread_id=thread_id, turn_id=turn_id)
