#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.codex_thread_service import MaterializingCodexThreadService  # noqa: E402
from bub_codex.live_stream import BubCodexLiveRuntimeStreamService  # noqa: E402
from bub_codex.plugin_stream_integration import run_plugin_stream_once  # noqa: E402
from bub_codex.runtime import BubCodexRuntime  # noqa: E402
from bub_codex.tape_store import InMemoryTapeStore  # noqa: E402

DEFAULT_SDK_PYTHON_DIR = Path("/tmp/bub-codex-sources/openai-codex/sdk/python")


def add_sdk_to_path(sdk_python_dir: Path) -> None:
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


async def main() -> None:
    add_sdk_to_path(DEFAULT_SDK_PYTHON_DIR)
    from openai_codex.client import CodexClient, CodexConfig

    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("No codex binary found.")

    workspace = Path("/tmp/bub-codex-real-live-workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    fibonacci_path = workspace / "fibonacci.py"
    if fibonacci_path.exists():
        fibonacci_path.unlink()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "artifacts/spikes" / f"real-codex-live-stream-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    store = InMemoryTapeStore()
    config = CodexConfig(
        codex_bin=codex_bin,
        cwd=str(workspace),
        config_overrides=(
            'sandbox_mode="danger-full-access"',
            'approval_policy="never"',
        ),
        experimental_api=True,
    )

    with CodexClient(config=config) as client:
        client.initialize()
        service = MaterializingCodexThreadService(
            client,
            cwd=str(workspace),
            approval_policy="never",
            sandbox="danger-full-access",
            initial_prompt_factory=lambda _anchor_id, _intent: "Reply exactly with: materialized",
        )
        runtime = BubCodexRuntime(store, service)
        live = BubCodexLiveRuntimeStreamService(runtime, service)
        result = await run_plugin_stream_once(
            live,
            prompt=(
                "Create fibonacci.py with fibonacci(n) returning the first n Fibonacci "
                "numbers as a list. Include a __main__ demo that prints fibonacci(10), "
                "run it, and summarize the result."
            ),
            session_id="real-live-session",
            state={"_runtime_workspace": str(workspace)},
            tape_store=store,
        )

    assistant_events = [
        event for event in result.tape_events if event.type == "codex.assistant_message.completed"
    ]
    final_answers = [
        event.payload["assistant_text"]
        for event in assistant_events
        if event.payload.get("phase") == "final_answer"
    ]
    commentary = [
        event.payload["assistant_text"]
        for event in assistant_events
        if event.payload.get("phase") == "commentary"
    ]
    event_types = [event.type for event in result.tape_events]

    assert final_answers
    assert result.final_text == "\n".join(final_answers)
    assert result.text == result.final_text
    assert commentary
    assert "bub.side_effect.completed" in event_types
    assert any(event.type.startswith("bub.tool.call.") for event in result.tape_events)

    payload = {
        **result.to_json(),
        "event_types": event_types,
        "commentary_texts": commentary,
        "final_answer_texts": final_answers,
        "workspace_files": sorted(path.name for path in workspace.iterdir()),
        "fibonacci_py": fibonacci_path.read_text(encoding="utf-8") if fibonacci_path.exists() else None,
    }
    (out_dir / "result.json").write_text(
        json.dumps(jsonable(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(out_dir)


if __name__ == "__main__":
    asyncio.run(main())
