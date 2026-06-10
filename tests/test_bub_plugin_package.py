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
from bub_codex import BubCodexPlugin, BubCodexSettings, InMemoryTapeStore, RepublicTapeStoreAdapter  # noqa: E402
from bub_codex.plugin import build_runtime_stream_service, create_plugin, stream_text  # noqa: E402


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

    def test_openai_codex_sdk_dependency_is_importable(self) -> None:
        from openai_codex.client import CodexClient, CodexConfig

        self.assertIsNotNone(CodexClient)
        self.assertIsNotNone(CodexConfig)

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
    async def run_stream(self, *, prompt, session_id, state):
        return stream_text("package lifecycle ok")


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
