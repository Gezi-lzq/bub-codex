#!/usr/bin/env python3
"""Spike harness for Codex Python SDK/app-server event capture.

This script intentionally keeps dependencies narrow:

- it imports the local OpenAI Codex Python SDK source tree from /tmp by default;
- it uses an installed `codex` binary by default instead of requiring
  openai-codex-cli-bin;
- it writes raw observations to artifacts/spikes for later schema work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(jsonable(value), ensure_ascii=False) + "\n")


def notification_record(event: Any) -> dict[str, Any]:
    payload = getattr(event, "payload", None)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": getattr(event, "method", None),
        "payload_type": type(payload).__name__ if payload is not None else None,
        "payload": jsonable(payload),
    }


def find_rollout_files(thread_id: str, roots: list[Path]) -> list[Path]:
    matches: list[Path] = []
    needles = {thread_id, thread_id.replace("-", "")}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            name = path.name
            if any(needle in name for needle in needles):
                matches.append(path)
                continue
            try:
                head = path.read_text(encoding="utf-8", errors="replace")[:4096]
            except OSError:
                continue
            if thread_id in head:
                matches.append(path)
    return sorted(set(matches), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def extract_compacted_items(rollout_path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with rollout_path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, start=1):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "compacted":
                items.append({"line": lineno, "item": obj})
            payload = obj.get("payload")
            if obj.get("type") == "event_msg" and isinstance(payload, dict):
                if payload.get("type") in {"compacted", "context_compaction"}:
                    items.append({"line": lineno, "item": obj})
    return items


def pending_turn_ids(codex: Any) -> list[str]:
    sync_client = getattr(getattr(codex, "_client", None), "_sync", None)
    router = getattr(sync_client, "_router", None)
    pending = getattr(router, "_pending_turn_notifications", {}) if router is not None else {}
    return list(pending.keys())


async def wait_for_private_compact_turn(codex: Any, timeout_s: float) -> str | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ids = pending_turn_ids(codex)
        if ids:
            return ids[0]
        await asyncio.sleep(0.1)
    return None


async def stream_private_turn(codex: Any, turn_id: str, out_path: Path) -> None:
    """Consume a turn-scoped notification stream using SDK-private routing.

    thread.compact() does not return a turn handle. The SDK router stores
    turn-scoped notifications in a private pending map keyed by turn id. This
    spike registers that discovered turn id and consumes it to completion.
    """

    codex._client.register_turn_notifications(turn_id)
    try:
        while True:
            event = await codex._client.next_turn_notification(turn_id)
            append_jsonl(
                out_path,
                {
                    "source": "private_turn_stream",
                    "turn_id": turn_id,
                    **notification_record(event),
                },
            )
            if event.method == "turn/completed":
                break
    finally:
        codex._client.unregister_turn_notifications(turn_id)


async def run(args: argparse.Namespace) -> None:
    add_sdk_to_path(args.sdk_python_dir)

    from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox

    codex_bin = args.codex_bin or shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("No codex binary found. Pass --codex-bin or install codex.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir / f"codex-sdk-harness-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    config = CodexConfig(
        codex_bin=codex_bin,
        cwd=str(args.workspace),
        config_overrides=tuple(args.config_override),
    )

    metadata: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(args.workspace),
        "sdk_python_dir": str(args.sdk_python_dir),
        "codex_bin": codex_bin,
        "config_overrides": list(args.config_override),
        "out_dir": str(out_dir),
    }
    write_json(out_dir / "metadata.json", metadata)

    async with AsyncCodex(config=config) as codex:
        write_json(out_dir / "initialize.json", codex.metadata)

        thread = await codex.thread_start(
            approval_mode=ApprovalMode.deny_all,
            cwd=str(args.workspace),
            sandbox=Sandbox.full_access,
        )
        metadata["thread_id"] = thread.id
        write_json(out_dir / "metadata.json", metadata)

        turn = await thread.turn(
            args.prompt,
            approval_mode=ApprovalMode.deny_all,
            cwd=str(args.workspace),
            sandbox=Sandbox.full_access,
        )
        metadata["turn_id"] = turn.id
        write_json(out_dir / "metadata.json", metadata)

        async for event in turn.stream():
            append_jsonl(out_dir / "turn-stream.jsonl", notification_record(event))

        write_json(out_dir / "thread-read-before-compact.json", await thread.read(include_turns=True))

        compact_response = await thread.compact()
        write_json(out_dir / "compact-start-response.json", compact_response)
        compact_turn_id = await wait_for_private_compact_turn(codex, args.compact_wait_s)
        metadata["compact_turn_id"] = compact_turn_id
        write_json(out_dir / "metadata.json", metadata)
        if compact_turn_id is None:
            write_json(
                out_dir / "compact-private-turn-error.json",
                {"error": "No private pending compact turn id observed"},
            )
        else:
            await stream_private_turn(
                codex,
                compact_turn_id,
                out_dir / "compact-private-turn-stream.jsonl",
            )

        write_json(out_dir / "thread-read-after-compact.json", await thread.read(include_turns=True))

    rollout_roots = [Path.home() / ".codex" / "sessions", Path.home() / ".codex"]
    rollout_files = find_rollout_files(metadata["thread_id"], rollout_roots)
    write_json(out_dir / "rollout-files.json", [str(path) for path in rollout_files[:10]])
    if rollout_files:
        rollout_path = rollout_files[0]
        copied = out_dir / "rollout.jsonl"
        copied.write_text(rollout_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        write_json(out_dir / "rollout-compacted-items.json", extract_compacted_items(rollout_path))

    print(out_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-python-dir", type=Path, default=DEFAULT_SDK_PYTHON_DIR)
    parser.add_argument("--codex-bin", default=None)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/spikes"))
    parser.add_argument(
        "--config-override",
        action="append",
        default=[],
        help="Pass through to codex --config KEY=VALUE. Can be repeated.",
    )
    parser.add_argument("--compact-wait-s", type=float, default=8.0)
    parser.add_argument(
        "--prompt",
        default=(
            "Spike task: inspect this repository briefly, then create no files. "
            "Reply with the names of two existing top-level files and one sentence "
            "about what this repository is researching."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
