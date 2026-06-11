#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv/bin/python"
BUB_HOME = Path.home() / ".bub"
TAPES_DIR = BUB_HOME / "tapes"


@dataclass(frozen=True, slots=True)
class CliRun:
    name: str
    command: list[str]
    returncode: int
    elapsed_s: float
    stdout: str
    stderr: str


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "artifacts/spikes" / f"real-bub-cli-research-{stamp}"
    workspace = Path("/tmp") / f"bub-codex-real-cli-workspace-{stamp}"
    workspace.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    before_tapes = _snapshot_tapes()
    session_id = f"bub-codex-real-cli-{stamp}"
    env = _env(workspace)

    runs = [
        _run_bub(
            name="turn-1-project",
            message=(
                "In this workspace, create a small Python project named weather_stats. "
                "It should contain weather_stats.py with functions mean(values), "
                "median(values), and summarize(values). Add tests in test_weather_stats.py, "
                "run the tests with python -m unittest, and summarize what you changed."
            ),
            session_id=session_id,
            workspace=workspace,
            env=env,
        ),
        _run_bub(
            name="turn-2-resume-modify",
            message=(
                "Continue the same task. Add standard_deviation(values) to weather_stats.py, "
                "extend summarize(values) to include it, update the tests, run the tests, "
                "and mention whether this turn resumed the existing context."
            ),
            session_id=session_id,
            workspace=workspace,
            env=env,
        ),
        _run_bub(
            name="turn-3-chat",
            message=(
                "Now answer conversationally: based on the files in this workspace, what are "
                "the module's public functions and one improvement you would make next? "
                "Do not edit files in this turn."
            ),
            session_id=session_id,
            workspace=workspace,
            env=env,
        ),
    ]

    after_tapes = _snapshot_tapes()
    changed_tapes = _changed_tapes(before_tapes, after_tapes)
    tape_payloads = [_read_tape(path) for path in changed_tapes]
    workspace_files = _workspace_files(workspace)

    result = {
        "metadata": {
            "session_id": session_id,
            "workspace": str(workspace),
            "out_dir": str(out_dir),
            "python": str(PYTHON),
            "bub_home": str(BUB_HOME),
            "env": {
                key: env.get(key)
                for key in sorted(env)
                if key.startswith("BUB_CODEX_")
            },
        },
        "runs": [asdict(run) for run in runs],
        "workspace_files": workspace_files,
        "changed_tapes": [str(path) for path in changed_tapes],
        "tapes": tape_payloads,
        "analysis": _analyze(runs, workspace_files, tape_payloads),
    }
    _write_json(out_dir / "result.json", result)
    print(out_dir)


def _env(workspace: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["BUB_CODEX_ENABLED"] = "true"
    env["BUB_CODEX_WORKSPACE"] = str(workspace)
    env["BUB_CODEX_APPROVAL_POLICY"] = "never"
    env["BUB_CODEX_SANDBOX"] = "danger-full-access"
    codex_bin = _which("codex")
    if codex_bin:
        env["BUB_CODEX_CODEX_BIN"] = codex_bin
    return env


def _which(name: str) -> str | None:
    for part in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(part) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _run_bub(
    *,
    name: str,
    message: str,
    session_id: str,
    workspace: Path,
    env: dict[str, str],
) -> CliRun:
    command = [
        str(PYTHON),
        "-m",
        "bub",
        "--workspace",
        str(workspace),
        "run",
        "--session-id",
        session_id,
        "--chat-id",
        "research",
        message,
    ]
    start = time.monotonic()
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=240,
    )
    return CliRun(
        name=name,
        command=command,
        returncode=result.returncode,
        elapsed_s=round(time.monotonic() - start, 3),
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _snapshot_tapes() -> dict[str, tuple[int, int]]:
    TAPES_DIR.mkdir(parents=True, exist_ok=True)
    snapshot: dict[str, tuple[int, int]] = {}
    for path in TAPES_DIR.glob("*.jsonl"):
        stat = path.stat()
        snapshot[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _changed_tapes(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[Path]:
    changed: list[Path] = []
    for path, state in after.items():
        if before.get(path) != state:
            changed.append(Path(path))
    return sorted(changed)


def _read_tape(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append({"raw": line})
    return {
        "path": str(path),
        "line_count": len(lines),
        "entries": entries,
    }


def _workspace_files(workspace: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            try:
                files[str(path.relative_to(workspace))] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                files[str(path.relative_to(workspace))] = "<binary>"
    return files


def _analyze(runs: list[CliRun], workspace_files: dict[str, str], tapes: list[dict[str, Any]]) -> dict[str, Any]:
    event_types: list[str] = []
    thread_ids: list[str] = []
    runtime_errors: list[dict[str, Any]] = []
    for tape in tapes:
        for entry in tape.get("entries", []):
            payload = entry.get("payload") if isinstance(entry, dict) else None
            if not isinstance(payload, dict):
                continue
            event_type = payload.get("type") or payload.get("event_type")
            if isinstance(event_type, str):
                event_types.append(event_type)
            thread_id = payload.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                thread_ids.append(thread_id)
            if event_type == "bub.runtime.error":
                runtime_errors.append(payload)
    return {
        "all_runs_succeeded": all(run.returncode == 0 for run in runs),
        "returncodes": {run.name: run.returncode for run in runs},
        "workspace_file_names": sorted(workspace_files),
        "changed_tape_count": len(tapes),
        "tape_persisted_bub_codex_events": bool(event_types),
        "suspected_in_memory_tape_store": all(run.returncode == 0 for run in runs) and not event_types,
        "event_types": event_types,
        "unique_thread_ids": sorted(set(thread_ids)),
        "runtime_error_count": len(runtime_errors),
        "runtime_errors": runtime_errors,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
