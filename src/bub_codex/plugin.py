from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import bub
from republic import AsyncStreamEvents, StreamEvent, StreamState

from bub.hookspecs import hookimpl
from bub.types import State

from .codex_thread_service import MaterializingCodexThreadService
from .config import BubCodexSettings, load_settings
from .republic_tape_store import RepublicTapeStoreAdapter
from .runtime import BubCodexRuntime, RuntimeTurnResult
from .tape_store import InMemoryTapeStore
from .turn_translator import StreamDecision, stream_success_decisions_from_tape_events


def create_plugin(framework: Any) -> "BubCodexPlugin":
    settings = load_settings()
    if not settings.enabled:
        return BubCodexPlugin()
    return BubCodexPlugin(LazyRuntimeStreamService(framework, settings=settings))


class RuntimeStreamService(Protocol):
    async def run_stream(
        self,
        *,
        prompt: str | list[dict],
        session_id: str,
        state: State,
    ) -> AsyncStreamEvents:
        ...


class ClosableRuntimeStreamService(RuntimeStreamService, Protocol):
    def close(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class RuntimeCacheKey:
    tape_store_id: int | None
    workspace: str | None
    codex_bin: str | None
    sdk_python_path: str | None
    approval_policy: str
    sandbox: str
    config_overrides: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    use_bub_tape_store: bool


class UnconfiguredRuntimeStreamService:
    def __init__(self, message: str = "bub-codex runtime is not configured") -> None:
        self.message = message

    async def run_stream(
        self,
        *,
        prompt: str | list[dict],
        session_id: str,
        state: State,
    ) -> AsyncStreamEvents:
        return stream_text(
            self.message,
            ok=False,
            error={"kind": "unknown", "message": self.message},
        )


class LazyRuntimeStreamService:
    """Build the real runtime service inside Bub's turn lifecycle.

    Bub exposes the active tape store only while `framework.running()` is active.
    Plugin factories run earlier during hook loading, so the plugin must not bind
    the tape store in `create_plugin()`.
    """

    def __init__(self, framework: Any, *, settings: BubCodexSettings) -> None:
        self.framework = framework
        self.settings = settings
        self._cached_runtime: RuntimeStreamService | None = None
        self._cached_key: RuntimeCacheKey | None = None

    async def run_stream(
        self,
        *,
        prompt: str | list[dict],
        session_id: str,
        state: State,
    ) -> AsyncStreamEvents:
        cache_key = _runtime_cache_key(self.framework, self.settings)
        should_close_after_run = False
        try:
            runtime = self._cached_runtime if cache_key is not None and cache_key == self._cached_key else None
            if runtime is None:
                self.close()
                runtime = build_runtime_stream_service(self.framework, settings=self.settings)
                if cache_key is not None:
                    self._cached_runtime = runtime
                    self._cached_key = cache_key
                else:
                    should_close_after_run = True
        except Exception as exc:
            return stream_text(
                f"bub-codex runtime is not configured: {exc}",
                ok=False,
                error={"kind": "unknown", "message": f"bub-codex runtime is not configured: {exc}"},
            )
        try:
            stream = await runtime.run_stream(prompt=prompt, session_id=session_id, state=state)
        except Exception:
            if should_close_after_run:
                _close_runtime(runtime)
            raise
        if not should_close_after_run:
            return stream

        async def iterator():
            try:
                async for event in stream:
                    yield event
            finally:
                _close_runtime(runtime)

        return AsyncStreamEvents(iterator(), state=_stream_state(stream))

    def close(self) -> None:
        runtime = self._cached_runtime
        self._cached_runtime = None
        self._cached_key = None
        _close_runtime(runtime)


class BubCodexPlugin:
    def __init__(self, runtime: RuntimeStreamService | None = None) -> None:
        self.runtime = runtime or UnconfiguredRuntimeStreamService()

    @hookimpl
    async def run_model_stream(self, prompt: str | list[dict], session_id: str, state: State) -> AsyncStreamEvents:
        if _is_comma_command(prompt):
            return await _run_comma_command(prompt, session_id=session_id, state=state)
        return await self.runtime.run_stream(prompt=prompt, session_id=session_id, state=state)


class BubCodexRuntimeStreamService:
    def __init__(
        self,
        runtime: BubCodexRuntime,
        *,
        tape_id_factory: Any | None = None,
    ) -> None:
        self.runtime = runtime
        self._tape_id_factory = tape_id_factory or _default_tape_id

    async def run_stream(
        self,
        *,
        prompt: str | list[dict],
        session_id: str,
        state: State,
    ) -> AsyncStreamEvents:
        prompt_text = _prompt_text(prompt)
        cwd = str(state.get("_runtime_workspace") or ".")
        tape_id = str(self._tape_id_factory(session_id, state))
        try:
            result = self.runtime.run_turn(
                session_id=session_id,
                tape_id=tape_id,
                cwd=cwd,
                prompt=prompt_text,
                workspace_metadata={"cwd": cwd},
            )
        except Exception as exc:
            return stream_text(
                f"{type(exc).__name__}: {exc}",
                ok=False,
                error={"kind": "unknown", "message": str(exc)},
            )
        return stream_runtime_turn_result(result)


def build_runtime_stream_service(
    framework: Any,
    *,
    settings: BubCodexSettings | None = None,
    codex_config_factory: Callable[..., Any] | None = None,
    codex_client_factory: Callable[..., Any] | None = None,
) -> RuntimeStreamService:
    from .live_stream import BubCodexLiveRuntimeStreamService

    settings = settings or load_settings()
    workspace = settings.workspace or getattr(framework, "workspace", None)
    if workspace is None:
        raise RuntimeError("workspace is not available")

    if settings.sdk_python_path is not None:
        sys.path.insert(0, str(settings.sdk_python_path))

    if codex_config_factory is None or codex_client_factory is None:
        try:
            from openai_codex.client import CodexClient, CodexConfig
        except ImportError as exc:
            raise RuntimeError(
                "openai_codex SDK is not importable; install the Codex Python SDK or set BUB_CODEX_SDK_PYTHON_PATH"
            ) from exc
        codex_config_factory = codex_config_factory or CodexConfig
        codex_client_factory = codex_client_factory or CodexClient

    client_config = codex_config_factory(
        codex_bin=str(settings.codex_bin) if settings.codex_bin else None,
        cwd=str(workspace),
        config_overrides=settings.codex_config_overrides(),
        env=dict(settings.env) or None,
        experimental_api=True,
    )
    client = codex_client_factory(config=client_config)
    client.start()
    client.initialize()

    codex_threads = MaterializingCodexThreadService(
        client,
        cwd=str(workspace),
        approval_policy=settings.approval_policy,
        sandbox=settings.sandbox,
    )
    tape_store = _runtime_tape_store(framework, settings)
    runtime = BubCodexRuntime(tape_store, codex_threads)
    return BubCodexLiveRuntimeStreamService(runtime, codex_threads)


def stream_runtime_turn_result(result: RuntimeTurnResult) -> AsyncStreamEvents:
    decisions = stream_success_decisions_from_tape_events(result.appended_events)

    async def iterator():
        for decision in decisions:
            yield _to_stream_event(decision)

    return AsyncStreamEvents(iterator(), state=StreamState())


async def _run_comma_command(prompt: str | list[dict], *, session_id: str, state: State) -> AsyncStreamEvents:
    agent = state.get("_runtime_agent")
    if agent is None or not hasattr(agent, "run"):
        return stream_text(
            "bub-codex cannot run comma command without _runtime_agent",
            ok=False,
            error={
                "kind": "unknown",
                "message": "bub-codex cannot run comma command without _runtime_agent",
            },
        )

    result = agent.run(session_id=session_id, prompt=prompt, state=state)
    if hasattr(result, "__await__"):
        result = await result
    return stream_text(str(result))


def stream_text(
    text: str,
    *,
    ok: bool = True,
    error: dict[str, Any] | None = None,
) -> AsyncStreamEvents:
    async def iterator():
        if error is not None:
            yield StreamEvent("error", error)
        if text:
            yield StreamEvent("text", {"delta": text})
        yield StreamEvent("final", {"text": text, "ok": ok})

    return AsyncStreamEvents(iterator(), state=StreamState())


def _stream_state(stream: AsyncStreamEvents) -> StreamState | None:
    return getattr(stream, "_state", None)


def _close_runtime(runtime: RuntimeStreamService | None) -> None:
    close = getattr(runtime, "close", None)
    if callable(close):
        close()


def _is_comma_command(prompt: str | list[dict]) -> bool:
    return isinstance(prompt, str) and prompt.strip().startswith(",")


def _default_tape_id(session_id: str, state: State) -> str:
    return session_id


def _prompt_text(prompt: str | list[dict]) -> str:
    if isinstance(prompt, str):
        return prompt
    return "\n".join(str(part.get("text", "")) for part in prompt if isinstance(part, dict) and part.get("type") == "text")


def _to_stream_event(decision: StreamDecision) -> StreamEvent:
    return StreamEvent(decision.kind, decision.data)


def _runtime_tape_store(framework: Any, settings: BubCodexSettings):
    if settings.use_bub_tape_store and hasattr(framework, "get_tape_store"):
        tape_store = framework.get_tape_store()
        if tape_store is not None:
            return RepublicTapeStoreAdapter(tape_store)
    return InMemoryTapeStore()


def _runtime_cache_key(framework: Any, settings: BubCodexSettings) -> RuntimeCacheKey | None:
    active_store = None
    if settings.use_bub_tape_store and hasattr(framework, "get_tape_store"):
        active_store = framework.get_tape_store()
        if active_store is None:
            return None
    workspace = settings.workspace or getattr(framework, "workspace", None)
    return RuntimeCacheKey(
        tape_store_id=id(active_store) if active_store is not None else None,
        workspace=str(workspace) if workspace is not None else None,
        codex_bin=str(settings.codex_bin) if settings.codex_bin else None,
        sdk_python_path=str(settings.sdk_python_path) if settings.sdk_python_path else None,
        approval_policy=settings.approval_policy,
        sandbox=settings.sandbox,
        config_overrides=tuple(settings.config_overrides),
        env=tuple(sorted(settings.env.items())),
        use_bub_tape_store=settings.use_bub_tape_store,
    )
