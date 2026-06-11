#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.codex_thread_service import MaterializingCodexThreadService, ThreadMaterialization  # noqa: E402
from bub_codex.live_stream import BubCodexLiveRuntimeStreamService  # noqa: E402
from bub_codex.plugin_stream_integration import PluginStreamIntegrationResult, run_plugin_stream_once  # noqa: E402
from bub_codex.runtime import BubCodexRuntime  # noqa: E402
from bub_codex.tape_events import TapeEvent  # noqa: E402
from bub_codex.tape_store import InMemoryTapeStore  # noqa: E402


@dataclass(slots=True)
class RecordingLiveThreadService:
    inner: MaterializingCodexThreadService
    materializations: list[ThreadMaterialization] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    streamed_records: list[dict[str, Any]] = field(default_factory=list)

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> ThreadMaterialization:
        materialization = self.inner.materialize_thread(cwd=cwd, anchor_id=anchor_id, intent=intent)
        self.materializations.append(materialization)
        return materialization

    def resume_thread(self, thread_id: str) -> None:
        self.inner.resume_thread(thread_id)
        self.resumed.append(thread_id)

    def run_turn_stream_records(self, *, thread_id: str, cwd: str, prompt: str) -> Iterable[dict[str, Any]]:
        for record in self.inner.run_turn_stream_records(thread_id=thread_id, cwd=cwd, prompt=prompt):
            self.streamed_records.append(record)
            yield record


def add_sdk_to_path(sdk_python_dir: Path | None) -> None:
    if sdk_python_dir is None:
        return
    src = sdk_python_dir / "src"
    if not (src / "openai_codex").is_dir():
        raise RuntimeError(f"OpenAI Codex SDK source not found at {src}")
    sys.path.insert(0, str(src))


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=False)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def run_live_turn(
    *,
    client: Any,
    workspace: Path,
    store: InMemoryTapeStore,
    session_id: str,
    prompt: str,
    approval_policy: str,
    sandbox: str,
    initial_prompt: str,
) -> tuple[PluginStreamIntegrationResult, RecordingLiveThreadService]:
    service = RecordingLiveThreadService(
        MaterializingCodexThreadService(
            client,
            cwd=str(workspace),
            approval_policy=approval_policy,
            sandbox=sandbox,
            initial_prompt_factory=lambda _anchor_id, _intent: initial_prompt,
        )
    )
    runtime = BubCodexRuntime(store, service)
    live = BubCodexLiveRuntimeStreamService(runtime, service)
    result = await run_plugin_stream_once(
        live,
        prompt=prompt,
        session_id=session_id,
        state={"_runtime_workspace": str(workspace)},
        tape_store=store,
    )
    return result, service


async def main() -> None:
    args = parse_args()
    add_sdk_to_path(args.sdk_python_dir)

    from openai_codex.client import CodexClient, CodexConfig

    codex_bin = args.codex_bin or shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("No codex binary found. Pass --codex-bin or install codex.")

    args.workspace.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir / f"real-codex-resume-smoke-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    store = InMemoryTapeStore()
    config = CodexConfig(
        codex_bin=codex_bin,
        cwd=str(args.workspace),
        config_overrides=tuple(args.config_override),
        experimental_api=True,
    )

    with CodexClient(config=config) as first_client:
        first_client.initialize()
        first_result, first_service = await run_live_turn(
            client=first_client,
            workspace=args.workspace,
            store=store,
            session_id=args.session_id,
            prompt=args.first_prompt,
            approval_policy=args.approval_policy,
            sandbox=args.sandbox,
            initial_prompt=args.initial_prompt,
        )

    first_thread_id = _only_thread_bound(store.events()).thread_id
    first_event_count = len(store.events())

    with CodexClient(config=config) as second_client:
        second_client.initialize()
        second_result, second_service = await run_live_turn(
            client=second_client,
            workspace=args.workspace,
            store=store,
            session_id=args.session_id,
            prompt=args.second_prompt,
            approval_policy=args.approval_policy,
            sandbox=args.sandbox,
            initial_prompt=args.initial_prompt,
        )

    second_events = store.events()[first_event_count:]
    second_thread_ids = sorted({event.thread_id for event in second_events if event.thread_id})

    assert first_thread_id
    assert len(first_service.materializations) == 1
    assert first_service.resumed == []
    assert second_service.materializations == []
    assert second_service.resumed == [first_thread_id]
    assert second_thread_ids == [first_thread_id]
    assert second_result.final_text
    assert not any(event.type == "codex.thread.bound" for event in second_events)
    assert not any(event.type == "bub.runtime.error" for event in second_events)

    payload = {
        "metadata": {
            "workspace": args.workspace,
            "codex_bin": codex_bin,
            "config_overrides": list(args.config_override),
            "approval_policy": args.approval_policy,
            "sandbox": args.sandbox,
            "session_id": args.session_id,
        },
        "summary": {
            "ok": True,
            "first_thread_id": first_thread_id,
            "second_resumed_thread_ids": second_service.resumed,
            "second_materialization_count": len(second_service.materializations),
            "second_event_types": [event.type for event in second_events],
            "second_thread_ids": second_thread_ids,
        },
        "first": {
            "result": first_result.to_json(),
            "materializations": first_service.materializations,
            "resumed": first_service.resumed,
            "streamed_methods": [record.get("method") for record in first_service.streamed_records],
        },
        "second": {
            "result": second_result.to_json(),
            "materializations": second_service.materializations,
            "resumed": second_service.resumed,
            "streamed_methods": [record.get("method") for record in second_service.streamed_records],
            "new_tape_events": [event.to_json() for event in second_events],
        },
        "all_tape_event_types": [event.type for event in store.events()],
    }
    write_json(out_dir / "result.json", payload)
    print(out_dir)


def _only_thread_bound(events: list[TapeEvent]) -> TapeEvent:
    bound = [event for event in events if event.type == "codex.thread.bound"]
    if len(bound) != 1:
        raise AssertionError(f"expected exactly one codex.thread.bound event, got {len(bound)}")
    return bound[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real Codex SDK live bridge resume smoke.")
    parser.add_argument("--sdk-python-dir", type=Path, default=None)
    parser.add_argument("--codex-bin")
    parser.add_argument("--workspace", type=Path, default=Path("/tmp/bub-codex-real-resume-workspace"))
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/spikes"))
    parser.add_argument("--session-id", default="real-codex-resume-session")
    parser.add_argument("--approval-policy", default="never")
    parser.add_argument("--sandbox", default="danger-full-access")
    parser.add_argument("--initial-prompt", default="Reply exactly with: bub-codex-thread-materialized")
    parser.add_argument(
        "--first-prompt",
        default="Reply with exactly this sentence: first live bub-codex turn complete.",
    )
    parser.add_argument(
        "--second-prompt",
        default="Reply with exactly this sentence: second live bub-codex turn resumed.",
    )
    parser.add_argument(
        "--config-override",
        action="append",
        default=[
            'sandbox_mode="danger-full-access"',
            'approval_policy="never"',
        ],
        help="Pass through to Codex config_overrides. Can be repeated.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
