from __future__ import annotations

import asyncio
import sys
import unittest
from importlib.metadata import EntryPoint
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from republic import TapeEntry

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub.framework import BubFramework  # noqa: E402
from bub import tool  # noqa: E402
from bub_codex.config import BubCodexSettings  # noqa: E402
from bub_codex.plugin import BubCodexPlugin  # noqa: E402
from bub_codex.plugin import create_plugin  # noqa: E402
from bub_codex.republic_tape_store import RepublicTapeStoreAdapter  # noqa: E402
from bub_codex.runtime_services import LazyRuntimeStreamService  # noqa: E402
from bub_codex.runtime_services import build_runtime_stream_service  # noqa: E402
from bub_codex.stream_utils import default_tape_id  # noqa: E402
from bub_codex.stream_utils import stream_text  # noqa: E402
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

    def test_bub_tools_config_accepts_comma_and_json_lists(self) -> None:
        comma = BubCodexSettings(bub_tools="tape.info,schedule.add")
        json_list = BubCodexSettings(bub_tools='["tape.info", "schedule.add"]')

        self.assertEqual(comma.bub_tools, ["tape.info", "schedule.add"])
        self.assertEqual(json_list.bub_tools, ["tape.info", "schedule.add"])

    def test_bub_tools_env_accepts_comma_list(self) -> None:
        with patch.dict("os.environ", {"BUB_CODEX_BUB_TOOLS": "tape.info,schedule.add"}, clear=True):
            settings = BubCodexSettings()

        self.assertEqual(settings.bub_tools, ["tape.info", "schedule.add"])

    def test_default_tape_id_matches_bub_builtin_session_tape_name(self) -> None:
        import hashlib

        session_id = "test-codex-chat"
        workspace_hash = hashlib.md5(str(ROOT.resolve()).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        session_hash = hashlib.md5(session_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]

        tape_id = default_tape_id(session_id, {"_runtime_workspace": str(ROOT)})

        self.assertEqual(tape_id, f"{workspace_hash}__{session_hash}")

    def test_bub_framework_loads_package_entry_point_and_runs_stream_hook(self) -> None:
        framework = BubFramework()
        entry_point = SimpleNamespace(
            name="codex",
            load=lambda: create_plugin,
        )

        with patch("importlib.metadata.entry_points", lambda group: [entry_point]):
            with patch(
                "bub_codex.runtime_services.build_runtime_stream_service",
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

    def test_plugin_admits_messages_as_steering_only_while_turn_is_running(self) -> None:
        plugin = BubCodexPlugin(FakeRuntimeStreamService())

        running = plugin.admit_message(
            session_id="s1",
            message={"content": "adjust course"},
            turn=SimpleNamespace(is_running=True),
        )
        idle = plugin.admit_message(
            session_id="s1",
            message={"content": "next turn"},
            turn=SimpleNamespace(is_running=False),
        )

        self.assertEqual(_decision_field(running, "action"), "steer")
        self.assertEqual(_decision_field(running, "reason"), "codex turn is running")
        self.assertIsNone(idle)

    def test_plugin_does_not_steer_comma_commands_while_turn_is_running(self) -> None:
        plugin = BubCodexPlugin(FakeRuntimeStreamService())

        decision = plugin.admit_message(
            session_id="s1",
            message={"content": ",tape.info"},
            turn=SimpleNamespace(is_running=True),
        )

        self.assertIsNone(decision)

    def test_comma_handoff_only_delegates_to_bub_builtin_agent(self) -> None:
        store = InMemoryTapeStore()
        runtime = FakeRuntimeStreamService(tape_store=store)
        plugin = BubCodexPlugin(runtime)
        agent = FakeAgent("anchor added: handoff")
        state = {
            "_runtime_workspace": str(ROOT),
            "_runtime_agent": agent,
        }

        stream = asyncio.run(
            plugin.run_model_stream(
                prompt=',tape.handoff name=handoff summary="new context"',
                session_id="s1",
                state=state,
            )
        )
        text = asyncio.run(_collect_text(stream))
        events = asyncio.run(store.events(session_id="s1", tape_id=default_tape_id("s1", state)))

        self.assertEqual(text, "anchor added: handoff")
        self.assertEqual(agent.calls, [("s1", ',tape.handoff name=handoff summary="new context"', state)])
        self.assertEqual(events, [])

    def test_lazy_runtime_comma_command_does_not_build_codex_runtime(self) -> None:
        framework_store = FakeRepublicTapeStore()
        framework = SimpleNamespace(workspace=ROOT, get_tape_store=lambda: framework_store)
        plugin = create_plugin(framework)
        built_services: list[FakeRuntimeStreamService] = []
        state = {
            "_runtime_workspace": str(ROOT),
            "_runtime_agent": FakeAgent("anchor added: handoff"),
        }

        def fake_build_runtime_stream_service(active_framework, settings=None):
            runtime = FakeRuntimeStreamService()
            built_services.append(runtime)
            return runtime

        with patch("bub_codex.runtime_services.build_runtime_stream_service", fake_build_runtime_stream_service):
            stream = asyncio.run(
                plugin.run_model_stream(
                    prompt=',tape.handoff summary="first command"',
                    session_id="s1",
                    state=state,
                )
            )
            text = asyncio.run(_collect_text(stream))

        self.assertEqual(text, "anchor added: handoff")
        self.assertEqual(built_services, [])
        self.assertTrue(framework_store.closed)

    def test_lazy_runtime_closes_active_tape_store_after_non_handoff_comma_command(self) -> None:
        framework_store = FakeRepublicTapeStore()
        framework = SimpleNamespace(workspace=ROOT, get_tape_store=lambda: framework_store)
        plugin = create_plugin(framework)
        state = {
            "_runtime_workspace": str(ROOT),
            "_runtime_agent": FakeAgent("tape info"),
        }

        stream = asyncio.run(
            plugin.run_model_stream(
                prompt=",tape.info",
                session_id="s1",
                state=state,
            )
        )
        text = asyncio.run(_collect_text(stream))

        self.assertEqual(text, "tape info")
        self.assertTrue(framework_store.closed)

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

        with patch("bub_codex.runtime_services.build_runtime_stream_service", fake_build_runtime_stream_service):
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

    def test_lazy_runtime_reuses_runtime_for_same_workspace_and_settings(self) -> None:
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

        with patch("bub_codex.runtime_services.build_runtime_stream_service", fake_build_runtime_stream_service):
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

        self.assertEqual(len(built_services), 1)
        self.assertEqual(first_text, "runtime-1")
        self.assertEqual(second_text, "runtime-1")
        self.assertFalse(built_services[0].closed)
        asyncio.run(plugin.runtime.close())
        self.assertTrue(built_services[0].closed)

    def test_lazy_runtime_uses_current_active_tape_store_for_each_turn(self) -> None:
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
            service.set_tape_store(active_framework.get_tape_store())
            built_services.append(service)
            return service

        with patch("bub_codex.runtime_services.build_runtime_stream_service", fake_build_runtime_stream_service):
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

        self.assertEqual(len(built_services), 1)
        self.assertIsInstance(built_services[0].tape_store, RepublicTapeStoreAdapter)
        self.assertIs(built_services[0].tape_store.store, framework.active_store)
        self.assertEqual(first_text, "runtime-1")
        self.assertEqual(second_text, "runtime-1")

    def test_lazy_runtime_keeps_service_open_after_stream_consumption_without_bub_tape_store(self) -> None:
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

        with patch("bub_codex.runtime_services.build_runtime_stream_service", fake_build_runtime_stream_service):
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
        self.assertFalse(built_services[0].closed)
        asyncio.run(service.close())
        self.assertTrue(built_services[0].closed)

    def test_lazy_runtime_closes_service_after_bub_run_stream_consumption(self) -> None:
        class FakeFramework:
            workspace = ROOT

            def get_tape_store(self):
                return None

        framework = FakeFramework()
        service = LazyRuntimeStreamService(framework, settings=BubCodexSettings(codex_bin=ROOT / "codex"))
        built_services: list[FakeRuntimeStreamService] = []

        def fake_build_runtime_stream_service(active_framework, settings=None):
            runtime = FakeRuntimeStreamService("one-shot-runtime")
            built_services.append(runtime)
            return runtime

        original_argv = sys.argv
        try:
            sys.argv = ["bub", "run", "hello"]
            with patch("bub_codex.runtime_services.build_runtime_stream_service", fake_build_runtime_stream_service):
                stream = asyncio.run(
                    service.run_stream(
                        prompt="hello",
                        session_id="s1",
                        state={"_runtime_workspace": str(ROOT)},
                    )
                )
                text = asyncio.run(_collect_text(stream))
        finally:
            sys.argv = original_argv

        self.assertEqual(text, "one-shot-runtime")
        self.assertEqual(len(built_services), 1)
        self.assertTrue(built_services[0].closed)

    def test_lazy_runtime_awaits_async_runtime_close_when_service_closes(self) -> None:
        class FakeFramework:
            workspace = ROOT

            def get_tape_store(self):
                return None

        framework = FakeFramework()
        service = LazyRuntimeStreamService(framework, settings=BubCodexSettings(codex_bin=ROOT / "codex"))
        built_services: list[AsyncClosingRuntimeStreamService] = []

        def fake_build_runtime_stream_service(active_framework, settings=None):
            runtime = AsyncClosingRuntimeStreamService()
            built_services.append(runtime)
            return runtime

        with patch("bub_codex.runtime_services.build_runtime_stream_service", fake_build_runtime_stream_service):
            stream = asyncio.run(
                service.run_stream(
                    prompt="hello",
                    session_id="s1",
                    state={"_runtime_workspace": str(ROOT)},
                )
            )
            text = asyncio.run(_collect_text(stream))

        self.assertEqual(text, "async-close-runtime")
        self.assertEqual(len(built_services), 1)
        self.assertFalse(built_services[0].closed)
        asyncio.run(service.close())
        self.assertTrue(built_services[0].closed)

    def test_lazy_runtime_closes_service_when_run_stream_raises(self) -> None:
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

        with patch("bub_codex.runtime_services.build_runtime_stream_service", fake_build_runtime_stream_service):
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

        self.assertIsInstance(service.tape_store, RepublicTapeStoreAdapter)
        self.assertIsNotNone(service.tool_runtime_context)
        self.assertIsNotNone(service.codex_turn_streams._client.approval_handler)
        dynamic_tool_names = [tool.name for tool in service.codex_turn_streams._dynamic_tools]
        self.assertEqual(dynamic_tool_names, ["tape_info", "tape_search", "tape_anchors", "tape_handoff"])

    def test_runtime_exposes_configured_bub_tools_to_codex(self) -> None:
        @tool(name="codex.test_extra", context=True)
        def _codex_test_extra(context) -> str:
            return f"session={context.state.get('session_id', '')}"

        framework = SimpleNamespace(
            workspace=ROOT,
            get_tape_store=lambda: None,
        )
        settings = BubCodexSettings(
            codex_bin=ROOT / "codex",
            use_bub_tape_store=False,
            bub_tools=["tape.info", "codex.test_extra"],
        )
        service = build_runtime_stream_service(
            framework,
            settings=settings,
            codex_config_factory=FakeCodexConfig,
            codex_client_factory=FakeCodexClient,
        )

        dynamic_tool_names = [tool.name for tool in service.codex_turn_streams._dynamic_tools]
        self.assertEqual(dynamic_tool_names, ["tape_info", "codex_test_extra"])

    def test_runtime_rejects_unknown_configured_bub_tool(self) -> None:
        framework = SimpleNamespace(
            workspace=ROOT,
            get_tape_store=lambda: None,
        )
        settings = BubCodexSettings(
            codex_bin=ROOT / "codex",
            use_bub_tape_store=False,
            bub_tools=["missing.tool"],
        )

        with self.assertRaisesRegex(ValueError, "missing.tool"):
            build_runtime_stream_service(
                framework,
                settings=settings,
                codex_config_factory=FakeCodexConfig,
                codex_client_factory=FakeCodexClient,
            )

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

        self.assertIsInstance(service.tape_store, InMemoryTapeStore)

    def test_runtime_uses_async_bub_tape_store_when_available(self) -> None:
        class FakeAsyncTapeStore:
            async def append(self, tape, entry):
                pass

            async def fetch_all(self, query):
                return []

        framework = SimpleNamespace(
            workspace=ROOT,
            get_tape_store=lambda: FakeAsyncTapeStore(),
        )
        settings = BubCodexSettings(
            codex_bin=ROOT / "codex",
        )
        built_clients: list[FakeCodexClient] = []

        def fake_codex_client_factory(*, config, approval_handler=None):
            client = FakeCodexClient(config=config, approval_handler=approval_handler)
            built_clients.append(client)
            return client

        service = build_runtime_stream_service(
            framework,
            settings=settings,
            codex_config_factory=FakeCodexConfig,
            codex_client_factory=fake_codex_client_factory,
        )

        self.assertIsInstance(service.tape_store, RepublicTapeStoreAdapter)
        self.assertEqual(len(built_clients), 1)


class FakeRuntimeStreamService:
    def __init__(self, text: str = "package lifecycle ok", tape_store=None) -> None:
        self.text = text
        self.tape_store = tape_store
        self.closed = False

    async def run_stream(self, *, prompt, session_id, state):
        return stream_text(self.text)

    async def close_current_tape_store(self) -> None:
        close = getattr(self.tape_store, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result

    def set_tape_store(self, tape_store) -> None:
        self.tape_store = tape_store

    def close(self) -> None:
        self.closed = True


class FakeRepublicTapeStore:
    def __init__(self) -> None:
        self.entries: dict[str, list[TapeEntry]] = {}
        self.closed = False

    def append(self, tape: str, entry: TapeEntry) -> None:
        self.entries.setdefault(tape, []).append(entry)

    def read(self, tape: str) -> list[TapeEntry]:
        return list(self.entries.get(tape, ()))

    async def close(self) -> None:
        self.closed = True


class FakeAgent:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls = []

    async def run(self, *, session_id, prompt, state):
        self.calls.append((session_id, prompt, state))
        return self.result


class FailingRuntimeStreamService(FakeRuntimeStreamService):
    async def run_stream(self, *, prompt, session_id, state):
        raise RuntimeError("stream failed before returning events")


class AsyncClosingRuntimeStreamService(FakeRuntimeStreamService):
    def __init__(self) -> None:
        super().__init__("async-close-runtime")

    async def close(self) -> None:
        self.closed = True


async def _collect_text(stream) -> str:
    parts: list[str] = []
    async for event in stream:
        if event.kind == "text":
            parts.append(str(event.data.get("delta", "")))
    return "".join(parts)


def _decision_field(decision, field: str):
    if isinstance(decision, dict):
        return decision.get(field)
    return getattr(decision, field, None)


class FakeCodexConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeCodexClient:
    def __init__(self, *, config, approval_handler=None):
        self.config = config
        self.approval_handler = approval_handler

    def start(self) -> None:
        pass

    def initialize(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
