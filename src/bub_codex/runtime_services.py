"""Runtime dependency assembly boundary.

This module wires Bub configuration, tape storage, Codex SDK client creation,
and dynamic tools into a live runtime. It should not own turn execution or the
tape-backed create/resume state machine.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from republic import AsyncStreamEvents, StreamState

from bub.types import State

from .codex_thread_service import MaterializingCodexThreadService
from .config import BubCodexSettings, load_settings
from .bub_tools import BubToolRuntimeContext, build_bub_dynamic_tool_provider
from .republic_tape_store import RepublicTapeStoreAdapter
from .runtime_context import RuntimeContextKernel
from .stream_utils import stream_text
from .tape_store import InMemoryTapeStore, TapeStore


class RuntimeStreamService(Protocol):
    async def run_stream(
        self,
        *,
        prompt: str | list[dict],
        session_id: str,
        state: State,
    ) -> AsyncStreamEvents:
        ...

    def current_tape_store(self) -> TapeStore | None:
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
    bub_tools: tuple[str, ...]


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

    def current_tape_store(self) -> TapeStore | None:
        return None


class LazyRuntimeStreamService:
    """Build the real runtime service inside Bub's turn lifecycle."""

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
        cache_key = runtime_cache_key(self.framework, self.settings)
        try:
            runtime = self._cached_runtime_for(cache_key)
            should_close_after_run = cache_key is None
            if runtime is None:
                runtime = self._build_runtime(cache_key)
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
                close_runtime(runtime)
            raise
        if not should_close_after_run:
            return stream

        async def iterator():
            try:
                async for event in stream:
                    yield event
            finally:
                close_runtime(runtime)

        return AsyncStreamEvents(iterator(), state=stream_state(stream))

    def close(self) -> None:
        runtime = self._cached_runtime
        self._cached_runtime = None
        self._cached_key = None
        close_runtime(runtime)

    def current_tape_store(self) -> TapeStore | None:
        cache_key = runtime_cache_key(self.framework, self.settings)
        if cache_key is None:
            return None
        runtime = self._cached_runtime_for(cache_key)
        if runtime is None:
            runtime = self._build_runtime(cache_key)
        return runtime.current_tape_store() if runtime is not None else None

    def _cached_runtime_for(self, cache_key: RuntimeCacheKey | None) -> RuntimeStreamService | None:
        if cache_key is not None and cache_key == self._cached_key:
            return self._cached_runtime
        return None

    def _build_runtime(self, cache_key: RuntimeCacheKey | None) -> RuntimeStreamService:
        self.close()
        runtime = build_runtime_stream_service(self.framework, settings=self.settings)
        if cache_key is not None:
            self._cached_runtime = runtime
            self._cached_key = cache_key
        return runtime


def build_runtime_stream_service(
    framework: Any,
    *,
    settings: BubCodexSettings | None = None,
    codex_config_factory: Callable[..., Any] | None = None,
    codex_client_factory: Callable[..., Any] | None = None,
) -> RuntimeStreamService:
    from .live_stream import BubCodexLiveRuntimeStreamService

    settings = settings or load_settings()
    workspace = runtime_workspace(framework, settings)
    if workspace is None:
        raise RuntimeError("workspace is not available")

    if settings.sdk_python_path is not None:
        sys.path.insert(0, str(settings.sdk_python_path))

    tape_store = runtime_tape_store(framework, settings)

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
    tool_runtime_context = BubToolRuntimeContext()
    dynamic_tool_provider = build_bub_dynamic_tool_provider(
        _model_visible_bub_tools(settings.bub_tools),
        context_factory=tool_runtime_context.context_for_call,
        awaitable_resolver=tool_runtime_context.resolve_awaitable,
    )
    client = codex_client_factory(
        config=client_config,
        approval_handler=dynamic_tool_provider.dispatcher.handle_server_request,
    )
    client.start()
    client.initialize()

    codex_threads = MaterializingCodexThreadService(
        client,
        cwd=str(workspace),
        approval_policy=settings.approval_policy,
        sandbox=settings.sandbox,
        dynamic_tools=dynamic_tool_provider.specs,
    )
    context_kernel = RuntimeContextKernel(tape_store, codex_threads)
    return BubCodexLiveRuntimeStreamService(
        context_kernel,
        tape_store,
        codex_threads,
        tool_runtime_context=tool_runtime_context,
    )


def stream_state(stream: AsyncStreamEvents) -> StreamState | None:
    return getattr(stream, "_state", None)


def close_runtime(runtime: RuntimeStreamService | None) -> None:
    close = getattr(runtime, "close", None)
    if callable(close):
        close()


def runtime_tape_store(framework: Any, settings: BubCodexSettings) -> TapeStore:
    if settings.use_bub_tape_store:
        tape_store = active_bub_tape_store(framework)
        if tape_store is not None:
            return RepublicTapeStoreAdapter(tape_store)
    return InMemoryTapeStore()


def runtime_cache_key(framework: Any, settings: BubCodexSettings) -> RuntimeCacheKey | None:
    active_store = None
    if settings.use_bub_tape_store:
        active_store = active_bub_tape_store(framework)
        if active_store is None:
            return None
    workspace = runtime_workspace(framework, settings)
    return RuntimeCacheKey(
        tape_store_id=id(active_store) if active_store is not None else None,
        workspace=workspace,
        codex_bin=str(settings.codex_bin) if settings.codex_bin else None,
        sdk_python_path=str(settings.sdk_python_path) if settings.sdk_python_path else None,
        approval_policy=settings.approval_policy,
        sandbox=settings.sandbox,
        config_overrides=tuple(settings.config_overrides),
        env=tuple(sorted(settings.env.items())),
        use_bub_tape_store=settings.use_bub_tape_store,
        bub_tools=tuple(settings.bub_tools),
    )


def runtime_workspace(framework: Any, settings: BubCodexSettings) -> str | None:
    workspace = settings.workspace or getattr(framework, "workspace", None)
    return str(workspace) if workspace is not None else None


def active_bub_tape_store(framework: Any) -> Any | None:
    get_tape_store = getattr(framework, "get_tape_store", None)
    if not callable(get_tape_store):
        return None
    return get_tape_store()


def _model_visible_bub_tools(names: list[str]) -> list[Any]:
    import bub.builtin.tools  # noqa: F401
    from bub.tools import REGISTRY, resolve_tool_name

    tools: list[Any] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for name in names:
        resolved_name = resolve_tool_name(name)
        if resolved_name is None:
            unknown.append(name)
            continue
        if resolved_name in seen:
            continue
        seen.add(resolved_name)
        tools.append(REGISTRY[resolved_name])
    if unknown:
        raise ValueError(f"unknown Bub tool(s) configured for Codex: {', '.join(sorted(unknown))}")
    return tools
