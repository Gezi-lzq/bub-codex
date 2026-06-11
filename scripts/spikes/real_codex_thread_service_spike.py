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

from bub_codex.codex_thread_service import LowLevelCodexThreadService, MaterializingCodexThreadService
from bub_codex.runtime import BubCodexRuntime
from bub_codex.tape_store import InMemoryTapeStore

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

    args.workspace.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir / f"real-codex-thread-service-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    store = InMemoryTapeStore()
    config = CodexConfig(
        codex_bin=codex_bin,
        cwd=str(args.workspace),
        config_overrides=tuple(args.config_override),
        experimental_api=True,
    )

    turn_stream: list[dict[str, Any]] = []
    with CodexClient(config=config) as client:
        initialize = client.initialize()
        if args.low_level_only:
            thread_service = LowLevelCodexThreadService(
                client,
                cwd=str(args.workspace),
                approval_policy=args.approval_policy,
                sandbox=args.sandbox,
            )
        else:
            thread_service = MaterializingCodexThreadService(
                client,
                cwd=str(args.workspace),
                approval_policy=args.approval_policy,
                sandbox=args.sandbox,
                notification_observer=lambda event: turn_stream.append(notification_record(event)),
                initial_prompt_factory=lambda _anchor_id, _intent: args.initial_prompt,
            )
        runtime = BubCodexRuntime(store, thread_service)

        first = runtime.ensure_thread_context(
            session_id=args.session_id,
            tape_id=args.tape_id,
            cwd=str(args.workspace),
            intent="Start a real Codex SDK thread through BubCodexRuntime.",
        )
        first_read = client.thread_read(first.thread_id, include_turns=not args.low_level_only)

    if not args.low_level_only:
        with CodexClient(config=config) as second_client:
            second_initialize = second_client.initialize()
            second_thread_service = MaterializingCodexThreadService(
                second_client,
                cwd=str(args.workspace),
                approval_policy=args.approval_policy,
                sandbox=args.sandbox,
            )
            second_runtime = BubCodexRuntime(store, second_thread_service)
            second = second_runtime.ensure_thread_context(
                session_id=args.session_id,
                tape_id=args.tape_id,
                cwd=str(args.workspace),
                intent="Resume the same real Codex SDK thread after materialization.",
            )
            second_error = None
            second_read = second_client.thread_read(second.thread_id, include_turns=True)
    else:
        second_initialize = None

        with CodexClient(config=config) as second_client:
            second_initialize = second_client.initialize()
            second_thread_service = LowLevelCodexThreadService(
                second_client,
                cwd=str(args.workspace),
                approval_policy=args.approval_policy,
                sandbox=args.sandbox,
            )
            second_runtime = BubCodexRuntime(store, second_thread_service)
            second = None
            second_error = None
            second_read = None
            try:
                second = second_runtime.ensure_thread_context(
                    session_id=args.session_id,
                    tape_id=args.tape_id,
                    cwd=str(args.workspace),
                    intent="Resume the same real Codex SDK thread through BubCodexRuntime.",
                )
                second_read = second_client.thread_read(second.thread_id, include_turns=False)
            except Exception as exc:
                second_error = {"type": type(exc).__name__, "message": str(exc)}

    assert first.status == "bootstrapped"
    assert first.thread_id
    first_event_types = [event.type for event in first.appended_events]
    assert first_event_types[:3] == [
        "bub.anchor.creation.started",
        "bub.anchor.created",
        "bub.context.materialized",
    ]
    assert first_event_types[-1] == "codex.thread.bound"
    if not args.low_level_only:
        assert "codex.turn.materialization.started" in first_event_types
        assert "codex.turn.materialization.completed" in first_event_types
    else:
        assert first_event_types == [
            "bub.anchor.creation.started",
            "bub.anchor.created",
            "bub.context.materialized",
            "codex.thread.bound",
        ]
    if not args.low_level_only:
        assert second is not None
        assert second.status == "resumed"
        assert second.thread_id == first.thread_id
        assert second.appended_events == ()
        assert second_error is None
        assert any(record["method"] == "turn/completed" for record in turn_stream)
        assert first.appended_events[-1].payload["refs"]["materialization_turn_id"]
    else:
        assert second is None
        assert second_error is not None
        assert "no rollout found for thread id" in second_error["message"]

    result = {
        "metadata": {
            "workspace": str(args.workspace),
            "codex_bin": codex_bin,
            "config_overrides": list(args.config_override),
            "approval_policy": args.approval_policy,
            "sandbox": args.sandbox,
        },
        "initialize": initialize,
        "second_initialize": second_initialize,
        "first": first.to_json(),
        "first_thread_read": first_read,
        "turn_stream": turn_stream,
        "second": second.to_json() if second else None,
        "second_error": second_error,
        "second_thread_read": second_read,
        "tape_event_types": [event.type for event in store.events()],
    }
    write_json(out_dir / "result.json", result)
    print(out_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real Codex SDK thread service runtime facade spike.")
    parser.add_argument("--sdk-python-dir", type=Path, default=DEFAULT_SDK_PYTHON_DIR)
    parser.add_argument("--codex-bin")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("/tmp/bub-codex-real-runtime-workspace"),
    )
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/spikes"))
    parser.add_argument("--session-id", default="real-codex-runtime-session")
    parser.add_argument("--tape-id", default="real-codex-runtime-tape")
    parser.add_argument("--approval-policy", default="never")
    parser.add_argument("--sandbox", default="danger-full-access")
    parser.add_argument("--low-level-only", action="store_true")
    parser.add_argument(
        "--initial-prompt",
        default="Reply exactly with: bub-codex-thread-materialized",
    )
    parser.add_argument(
        "--config-override",
        action="append",
        default=[],
        help="Pass through to codex --config KEY=VALUE. Can be repeated.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
