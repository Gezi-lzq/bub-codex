from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from republic import AsyncStreamEvents, StreamEvent, StreamState

from bub.types import State

from .plugin import _default_tape_id, _prompt_text
from .runtime import BubCodexRuntime
from .runtime_adapter import facts_from_notification_record
from .tape_events import JsonObject, TapeEvent
from .turn_projection import project_user_turn_events


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

        fallback_holder = {"text": None}

        def _set_fallback_text(text: str) -> None:
            fallback_holder["text"] = text

        async def fixed_iterator():
            final_texts: list[str] = []
            async for stream_event in _iter_live_turn_events(
                runtime=self.runtime,
                stream_service=self.codex_turn_streams,
                session_id=session_id,
                tape_id=tape_id,
                anchor_id=start.anchor_id,
                thread_id=start.thread_id,
                cwd=cwd,
                prompt=prompt_text,
                final_texts=final_texts,
                fallback_text_ref=_set_fallback_text,
            ):
                yield stream_event

            text = "\n".join(final_texts) if final_texts else fallback_holder["text"] or ""
            if text and not final_texts:
                yield StreamEvent("text", {"delta": text})
            yield StreamEvent("final", {"text": text, "ok": True})

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
    final_texts: list[str],
    fallback_text_ref,
):
    for record in stream_service.run_turn_stream_records(
        thread_id=thread_id,
        cwd=cwd,
        prompt=prompt,
    ):
        facts = facts_from_notification_record(record, source="sdk_stream:user_turn")
        tape_events = project_user_turn_events(
            facts,
            session_id=session_id,
            tape_id=tape_id,
            anchor_id=anchor_id,
        )
        runtime.tape_store.append_many(tape_events)
        for text in _collect_assistant_texts(
            tape_events,
            final_texts=final_texts,
            fallback_text_ref=fallback_text_ref,
        ):
            yield StreamEvent("text", {"delta": text})


def _collect_assistant_texts(
    events: Iterable[TapeEvent],
    *,
    final_texts: list[str],
    fallback_text_ref,
) -> list[str]:
    new_final_texts: list[str] = []
    for event in events:
        if event.type != "codex.assistant_message.completed":
            continue
        text = event.payload.get("assistant_text")
        if not isinstance(text, str) or not text:
            continue
        fallback_text_ref(text)
        if event.payload.get("phase") == "final_answer":
            final_texts.append(text)
            new_final_texts.append(text)
    return new_final_texts


def _stream_error(exc: Exception) -> AsyncStreamEvents:
    async def iterator():
        text = f"{type(exc).__name__}: {exc}"
        yield StreamEvent("error", {"kind": "unknown", "message": str(exc)})
        yield StreamEvent("text", {"delta": text})
        yield StreamEvent("final", {"text": text, "ok": False})

    return AsyncStreamEvents(iterator(), state=StreamState())
