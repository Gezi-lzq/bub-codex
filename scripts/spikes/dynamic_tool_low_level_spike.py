#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex import (
    DynamicToolCall,
    DynamicToolDispatcher,
    DynamicToolResult,
    DynamicToolSpec,
    ThreadStartOptions,
    dynamic_tool_key,
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
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(jsonable(value), ensure_ascii=False) + "\n")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def notification_record(event: Any) -> dict[str, Any]:
    payload = getattr(event, "payload", None)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": getattr(event, "method", None),
        "payload_type": type(payload).__name__ if payload is not None else None,
        "payload": jsonable(payload),
    }


def main() -> None:
    args = parse_args()
    add_sdk_to_path(args.sdk_python_dir)

    from openai_codex.client import CodexClient, CodexConfig

    codex_bin = args.codex_bin or shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("No codex binary found. Pass --codex-bin or install codex.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir / f"dynamic-tool-low-level-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    request_log = out_dir / "server-requests.jsonl"

    def observe_server_request(method: str, params: dict[str, Any] | None) -> None:
        append_jsonl(
            request_log,
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "method": method,
                "params": params,
            },
        )

    dynamic_tool = DynamicToolSpec(
        namespace="bub",
        name="echo",
        description="Echo a short message for bub-codex dynamic tool spike.",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
    )

    def echo_tool(call: DynamicToolCall) -> DynamicToolResult:
        return DynamicToolResult.input_text(f"dynamic-ok:{call.tool}")

    dispatcher = DynamicToolDispatcher(
        {dynamic_tool_key(dynamic_tool): echo_tool},
        observer=observe_server_request,
    )

    config = CodexConfig(
        codex_bin=codex_bin,
        cwd=str(args.workspace),
        config_overrides=tuple(args.config_override),
        experimental_api=True,
    )
    write_json(
        out_dir / "metadata.json",
        {
            "workspace": str(args.workspace),
            "codex_bin": codex_bin,
            "config_overrides": list(args.config_override),
            "prompt": args.prompt,
        },
    )

    with CodexClient(config=config, approval_handler=dispatcher.handle_server_request) as client:
        write_json(out_dir / "initialize.json", client.initialize())
        started = client.thread_start(
            ThreadStartOptions(
                cwd=str(args.workspace),
                dynamic_tools=(dynamic_tool,),
            ).to_app_server_json()
        )
        thread_id = started.thread.id
        write_json(out_dir / "thread-start.json", started)

        turn = client.turn_start(thread_id, args.prompt, {"cwd": str(args.workspace)})
        turn_id = turn.turn.id
        write_json(out_dir / "turn-start.json", turn)

        try:
            while True:
                event = client.next_turn_notification(turn_id)
                append_jsonl(out_dir / "turn-stream.jsonl", notification_record(event))
                if getattr(event, "method", None) == "turn/completed":
                    break
        finally:
            client.unregister_turn_notifications(turn_id)

        write_json(out_dir / "thread-read-after-turn.json", client.thread_read(thread_id, True))

    print(out_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Low-level Codex dynamic tool app-server spike.")
    parser.add_argument("--sdk-python-dir", type=Path, default=DEFAULT_SDK_PYTHON_DIR)
    parser.add_argument("--codex-bin")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/spikes"))
    parser.add_argument(
        "--config-override",
        action="append",
        default=[],
        help="Pass through to codex --config KEY=VALUE. Can be repeated.",
    )
    parser.add_argument(
        "--prompt",
        default=(
            "Call the bub echo dynamic tool with message 'hello dynamic tool'. "
            "Then reply with the exact text returned by the tool."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
