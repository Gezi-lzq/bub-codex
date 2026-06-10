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

from bub_codex import (  # noqa: E402
    BubCodexRuntime,
    BubCodexRuntimeStreamService,
    InMemoryTapeStore,
    MaterializingCodexThreadService,
    run_plugin_stream_once,
)

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

    workspace = Path("/tmp/bub-codex-real-fibonacci-workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    fibonacci_path = workspace / "fibonacci.py"
    if fibonacci_path.exists():
        fibonacci_path.unlink()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "artifacts/spikes" / f"real-codex-plugin-fibonacci-stream-{stamp}"
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
        stream_service = BubCodexRuntimeStreamService(runtime)
        result = await run_plugin_stream_once(
            stream_service,
            prompt=(
                "In the current directory, create a Python file named fibonacci.py. "
                "It should define fibonacci(n) returning the first n Fibonacci numbers "
                "as a list, include a small CLI demo under if __name__ == '__main__', "
                "then run the file to verify it prints the first 10 numbers. "
                "Finish by summarizing what you created."
            ),
            session_id="real-plugin-fibonacci-session",
            state={"_runtime_workspace": str(workspace)},
            tape_store=store,
        )

    event_types = [event.type for event in result.tape_events]
    tool_events = [event for event in result.tape_events if event.type.startswith("bub.tool.call.")]
    side_effect_events = [
        event for event in result.tape_events if event.type.startswith("bub.side_effect.")
    ]

    payload = {
        **result.to_json(),
        "event_types": event_types,
        "tool_events": [event.to_json() for event in tool_events],
        "side_effect_events": [event.to_json() for event in side_effect_events],
        "workspace_files": sorted(path.name for path in workspace.iterdir()),
        "fibonacci_py": fibonacci_path.read_text(encoding="utf-8") if fibonacci_path.exists() else None,
    }
    (out_dir / "result.json").write_text(
        json.dumps(jsonable(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert fibonacci_path.exists()
    assert tool_events or side_effect_events
    print(out_dir)


if __name__ == "__main__":
    asyncio.run(main())
