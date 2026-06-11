from __future__ import annotations

import asyncio
import sys
import unittest
from importlib.metadata import EntryPoint
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub.framework import BubFramework  # noqa: E402
from bub_codex.config import BubCodexSettings  # noqa: E402
from bub_codex.plugin import BubCodexPlugin  # noqa: E402
from bub_codex.plugin import LazyRuntimeStreamService  # noqa: E402
from bub_codex.plugin import RuntimeCacheKey  # noqa: E402
from bub_codex.plugin import build_runtime_stream_service, create_plugin, stream_text  # noqa: E402
from bub_codex.republic_tape_store import RepublicTapeStoreAdapter  # noqa: E402
from bub_codex.tape_store import InMemoryTapeStore  # noqa: E402


class BubPluginPackageTest(unittest.TestCase):
    def test_bub_entry_point_target_loads_plugin_factory(self) -> None:
        entry_point = EntryPoint(
            name="codex",
            value="bub_codex.plugin:create_plugin",
            group="bub",
        )

        self.assertIs(entry_point.load(), create_plugin)

    def test_create_plugin_returns_clear_unconfigured_plugin_when_sdk_is_missing(self) -> None:
        plugin = create_plugin(SimpleNamespace(workspace=ROOT, get_tape_store=lambda: None))

        self.assertIsInstance(plugin, BubCodexPlugin)
        self.assertIsInstance(plugin.runtime, LazyRuntimeStreamService)

    def test_openai_codex_sdk_dependency_is_importable(self) -> None:
        from openai_codex.client import CodexClient, CodexConfig

        self.assertIsNotNone(CodexClient)
        self.assertIsNotNone(CodexConfig)

    def test_entry_point_import_registers_codex_config(self) -> None:
        from bub.configure import CONFIG_MAP

        self.assertEqual(getattr(BubCodexSettings, "__config_name__"), "codex")
        self.assertIn(BubCodexSettings, CONFIG_MAP["codex"])

    def test_bub_framework_loads_package_entry_point_and_runs_stream_hook(self) -> None:
        framework = BubFramework()
        entry_point = SimpleNamespace(
            name="codex",
            load=lambda: create_plugin,
        )

        with patch("importlib.metadata.entry_points", lambda group: [entry_point]):
            with patch(
                "bub_codex.plugin.build_runtime_stream_service",
                lambda _framework, settings=None: FakeRuntimeStreamService(),
            ):
                framework.load_hooks()

                report = framework.hook_report()
                stream = asyncio.run(
                    framework._hook_runtime.run_model_stream(
                        prompt="hello",
                        session_id="s1",
                        state={"_runtime_workspace": str(ROOT)},
                    )
                )
                text = asyncio.run(_collect_text(stream))

            self.assertTrue(framework._plugin_status["codex"].is_success)
            self.assertIn("codex", report["run_model_stream"])
            self.assertEqual(text, "package lifecycle ok")

    def test_plugin_binds_bub_tape_store_at_run_time_not_load_time(self) -> None:
        class FakeTapeStore:
            pass

        framework_tape_store = FakeTapeStore()

        class FakeFramework:
            workspace = ROOT

            def __init__(self) -> None:
                self.active_store = None

            def get_tape_store(self):
                return self.active_store

        framework = FakeFramework()
        plugin = create_plugin(framework)
        self.assertIsInstance(plugin.runtime, LazyRuntimeStreamService)

        framework.active_store = framework_tape_store
        captured = {}

        def fake_build_runtime_stream_service(active_framework, settings=None):
            captured["store"] = active_framework.get_tape_store()
            return FakeRuntimeStreamService()

        with patch("bub_codex.plugin.build_runtime_stream_service", fake_build_runtime_stream_service):
            stream = asyncio.run(
                plugin.run_model_stream(
                    prompt="hello",
                    session_id="s1",
                    state={"_runtime_workspace": str(ROOT)},
                )
            )
            text = asyncio.run(_collect_text(stream))

        self.assertIs(captured["store"], framework_tape_store)
        self.assertEqual(text, "package lifecycle ok")

    def test_lazy_runtime_reuses_service_for_same_active_tape_store(self) -> None:
        class FakeTapeStore:
            pass

        class FakeFramework:
            workspace = ROOT

            def __init__(self) -> None:
                self.active_store = FakeTapeStore()

            def get_tape_store(self):
                return self.active_store

        framework = FakeFramework()
        plugin = create_plugin(framework)
        build_count = 0

        def fake_build_runtime_stream_service(active_framework, settings=None):
            nonlocal build_count
            build_count += 1
            return FakeRuntimeStreamService(f"runtime-{build_count}")

        with patch("bub_codex.plugin.build_runtime_stream_service", fake_build_runtime_stream_service):
            first = asyncio.run(
                plugin.run_model_stream(
                    prompt="hello",
                    session_id="s1",
                    state={"_runtime_workspace": str(ROOT)},
                )
            )
            second = asyncio.run(
                plugin.run_model_stream(
                    prompt="again",
                    session_id="s1",
                    state={"_runtime_workspace": str(ROOT)},
                )
            )
            first_text = asyncio.run(_collect_text(first))
            second_text = asyncio.run(_collect_text(second))

        self.assertEqual(build_count, 1)
        self.assertEqual(first_text, "runtime-1")
        self.assertEqual(second_text, "runtime-1")

    def test_lazy_runtime_rebuilds_service_when_active_tape_store_changes(self) -> None:
        class FakeTapeStore:
            pass

        class FakeFramework:
            workspace = ROOT

            def __init__(self) -> None:
                self.active_store = FakeTapeStore()

            def get_tape_store(self):
                return self.active_store

        framework = FakeFramework()
        plugin = create_plugin(framework)
        build_count = 0

        def fake_build_runtime_stream_service(active_framework, settings=None):
            nonlocal build_count
            build_count += 1
            return FakeRuntimeStreamService(f"runtime-{build_count}")

        with patch("bub_codex.plugin.build_runtime_stream_service", fake_build_runtime_stream_service):
            first = asyncio.run(
                plugin.run_model_stream(
                    prompt="hello",
                    session_id="s1",
                    state={"_runtime_workspace": str(ROOT)},
                )
            )
            framework.active_store = FakeTapeStore()
            second = asyncio.run(
                plugin.run_model_stream(
                    prompt="again",
                    session_id="s1",
                    state={"_runtime_workspace": str(ROOT)},
                )
            )
            first_text = asyncio.run(_collect_text(first))
            second_text = asyncio.run(_collect_text(second))

        self.assertEqual(build_count, 2)
        self.assertEqual(first_text, "runtime-1")
        self.assertEqual(second_text, "runtime-2")

    def test_lazy_runtime_closes_previous_service_when_cache_key_changes(self) -> None:
        class FakeTapeStore:
            pass

        class FakeFramework:
            workspace = ROOT

            def __init__(self) -> None:
                self.active_store = FakeTapeStore()

            def get_tape_store(self):
                return self.active_store

        framework = FakeFramework()
        plugin = create_plugin(framework)
        built_services: list[FakeRuntimeStreamService] = []

        def fake_build_runtime_stream_service(active_framework, settings=None):
            service = FakeRuntimeStreamService(f"runtime-{len(built_services) + 1}")
            built_services.append(service)
            return service

        with patch("bub_codex.plugin.build_runtime_stream_service", fake_build_runtime_stream_service):
            first = asyncio.run(
                plugin.run_model_stream(
                    prompt="hello",
                    session_id="s1",
                    state={"_runtime_workspace": str(ROOT)},
                )
            )
            asyncio.run(_collect_text(first))
            framework.active_store = FakeTapeStore()
            second = asyncio.run(
                plugin.run_model_stream(
                    prompt="again",
                    session_id="s1",
                    state={"_runtime_workspace": str(ROOT)},
                )
            )
            asyncio.run(_collect_text(second))

        self.assertEqual(len(built_services), 2)
        self.assertTrue(built_services[0].closed)
        self.assertFalse(built_services[1].closed)

    def test_lazy_runtime_closes_uncached_service_after_stream_consumption(self) -> None:
        class FakeFramework:
            workspace = ROOT

            def get_tape_store(self):
                return None

        framework = FakeFramework()
        service = LazyRuntimeStreamService(framework, settings=BubCodexSettings(codex_bin=ROOT / "codex"))
        built_services: list[FakeRuntimeStreamService] = []

        def fake_build_runtime_stream_service(active_framework, settings=None):
            runtime = FakeRuntimeStreamService("uncached-runtime")
            built_services.append(runtime)
            return runtime

        with patch("bub_codex.plugin.build_runtime_stream_service", fake_build_runtime_stream_service):
            stream = asyncio.run(
                service.run_stream(
                    prompt="hello",
                    session_id="s1",
                    state={"_runtime_workspace": str(ROOT)},
                )
            )
            text = asyncio.run(_collect_text(stream))

        self.assertEqual(text, "uncached-runtime")
        self.assertEqual(len(built_services), 1)
        self.assertTrue(built_services[0].closed)
        self.assertIsNone(service._cached_runtime)
        self.assertIsNone(service._cached_key)

    def test_lazy_runtime_closes_uncached_service_when_run_stream_raises(self) -> None:
        class FakeFramework:
            workspace = ROOT

            def get_tape_store(self):
                return None

        framework = FakeFramework()
        service = LazyRuntimeStreamService(framework, settings=BubCodexSettings(codex_bin=ROOT / "codex"))
        built_services: list[FailingRuntimeStreamService] = []

        def fake_build_runtime_stream_service(active_framework, settings=None):
            runtime = FailingRuntimeStreamService()
            built_services.append(runtime)
            return runtime

        with patch("bub_codex.plugin.build_runtime_stream_service", fake_build_runtime_stream_service):
            with self.assertRaises(RuntimeError):
                asyncio.run(
                    service.run_stream(
                        prompt="hello",
                        session_id="s1",
                        state={"_runtime_workspace": str(ROOT)},
                    )
                )

        self.assertEqual(len(built_services), 1)
        self.assertTrue(built_services[0].closed)
        self.assertIsNone(service._cached_runtime)
        self.assertIsNone(service._cached_key)

    def test_runtime_cache_key_is_typed_and_stable_for_same_runtime_inputs(self) -> None:
        class FakeTapeStore:
            pass

        store = FakeTapeStore()
        framework = SimpleNamespace(workspace=ROOT, get_tape_store=lambda: store)
        service = LazyRuntimeStreamService(framework, settings=BubCodexSettings(codex_bin=ROOT / "codex"))

        key = service._cached_key
        self.assertIsNone(key)

        from bub_codex.plugin import _runtime_cache_key

        first = _runtime_cache_key(framework, service.settings)
        second = _runtime_cache_key(framework, service.settings)

        self.assertIsInstance(first, RuntimeCacheKey)
        self.assertEqual(first, second)
        self.assertEqual(first.workspace, str(ROOT))
        self.assertEqual(first.codex_bin, str(ROOT / "codex"))

    def test_runtime_uses_bub_tape_store_when_available(self) -> None:
        class FakeTapeStore:
            pass

        framework_tape_store = FakeTapeStore()
        framework = SimpleNamespace(
            workspace=ROOT,
            get_tape_store=lambda: framework_tape_store,
        )
        settings = BubCodexSettings(
            codex_bin=ROOT / "codex",
        )
        service = build_runtime_stream_service(
            framework,
            settings=settings,
            codex_config_factory=FakeCodexConfig,
            codex_client_factory=FakeCodexClient,
        )

        self.assertIsInstance(service.runtime.tape_store, RepublicTapeStoreAdapter)

    def test_runtime_can_explicitly_disable_bub_tape_store_for_tests(self) -> None:
        class FakeTapeStore:
            pass

        framework = SimpleNamespace(
            workspace=ROOT,
            get_tape_store=lambda: FakeTapeStore(),
        )
        settings = BubCodexSettings(
            codex_bin=ROOT / "codex",
            use_bub_tape_store=False,
        )
        service = build_runtime_stream_service(
            framework,
            settings=settings,
            codex_config_factory=FakeCodexConfig,
            codex_client_factory=FakeCodexClient,
        )

        self.assertIsInstance(service.runtime.tape_store, InMemoryTapeStore)


class FakeRuntimeStreamService:
    def __init__(self, text: str = "package lifecycle ok") -> None:
        self.text = text
        self.closed = False

    async def run_stream(self, *, prompt, session_id, state):
        return stream_text(self.text)

    def close(self) -> None:
        self.closed = True


class FailingRuntimeStreamService(FakeRuntimeStreamService):
    async def run_stream(self, *, prompt, session_id, state):
        raise RuntimeError("stream failed before returning events")


async def _collect_text(stream) -> str:
    parts: list[str] = []
    async for event in stream:
        if event.kind == "text":
            parts.append(str(event.data.get("delta", "")))
    return "".join(parts)


class FakeCodexConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeCodexClient:
    def __init__(self, *, config):
        self.config = config

    def start(self) -> None:
        pass

    def initialize(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
