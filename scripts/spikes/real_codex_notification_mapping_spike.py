#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex import (  # noqa: E402
    BubCodexRuntime,
    CodexTurn,
    InMemoryTapeStore,
    MaterializingCodexThreadService,
    ThreadMaterialization,
    facts_from_notification_record,
    project_user_turn_events,
)

DEFAULT_SDK_PYTHON_DIR = Path("/tmp/bub-codex-sources/openai-codex/sdk/python")


@dataclass(slots=True)
class RecordingThreadService:
    inner: MaterializingCodexThreadService
    materializations: list[ThreadMaterialization] = field(default_factory=list)
    turns: list[CodexTurn] = field(default_factory=list)

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> ThreadMaterialization:
        materialization = self.inner.materialize_thread(cwd=cwd, anchor_id=anchor_id, intent=intent)
        self.materializations.append(materialization)
        return materialization

    def resume_thread(self, thread_id: str) -> None:
        self.inner.resume_thread(thread_id)

    def run_turn(self, *, thread_id: str, cwd: str, prompt: str) -> CodexTurn:
        turn = self.inner.run_turn(thread_id=thread_id, cwd=cwd, prompt=prompt)
        self.turns.append(turn)
        return turn


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


def facts_for_records(records: tuple[dict[str, Any], ...], *, turn_id: str | None, source: str):
    facts = []
    rows = []
    for index, record in enumerate(records):
        record_facts = facts_from_notification_record(
            {
                **record,
                "turn_id": turn_id,
            },
            source=source,
        )
        facts.extend(record_facts)
        rows.append(
            {
                "notification_index": index,
                "method": record.get("method"),
                "payload_type": record.get("payload_type"),
                "raw": record,
                "facts": [fact.to_json() for fact in record_facts],
            }
        )
    return facts, rows


def main() -> None:
    add_sdk_to_path(DEFAULT_SDK_PYTHON_DIR)
    from openai_codex.client import CodexClient, CodexConfig

    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("No codex binary found.")

    workspace = Path("/tmp/bub-codex-real-mapping-workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    fibonacci_path = workspace / "fibonacci.py"
    if fibonacci_path.exists():
        fibonacci_path.unlink()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "artifacts/spikes" / f"real-codex-notification-mapping-{stamp}"
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
        service = RecordingThreadService(
            MaterializingCodexThreadService(
                client,
                cwd=str(workspace),
                approval_policy="never",
                sandbox="danger-full-access",
                initial_prompt_factory=lambda _anchor_id, _intent: "Reply exactly with: materialized",
            )
        )
        runtime = BubCodexRuntime(store, service)
        result = runtime.run_turn(
            session_id="real-mapping-session",
            tape_id="real-mapping-session",
            cwd=str(workspace),
            prompt=(
                "In the current directory, create fibonacci.py with fibonacci(n) returning "
                "the first n Fibonacci numbers as a list. Include a __main__ demo that "
                "prints fibonacci(10), run it, and summarize the result."
            ),
        )

    materialization = service.materializations[-1]
    turn = service.turns[-1]
    materialization_facts, materialization_rows = facts_for_records(
        materialization.notification_records,
        turn_id=materialization.turn_id,
        source="sdk_stream:thread_materialization",
    )
    turn_facts, turn_rows = facts_for_records(
        turn.notification_records,
        turn_id=turn.turn_id,
        source="sdk_stream:user_turn",
    )
    projected_turn_events = project_user_turn_events(
        turn_facts,
        session_id="real-mapping-session",
        tape_id="real-mapping-session",
        anchor_id=result.start.anchor_id,
    )

    payload = {
        "result": result.to_json(),
        "materialization": {
            "thread_id": materialization.thread_id,
            "turn_id": materialization.turn_id,
            "notification_rows": materialization_rows,
            "facts": [fact.to_json() for fact in materialization_facts],
        },
        "user_turn": {
            "thread_id": turn.thread_id,
            "turn_id": turn.turn_id,
            "notification_rows": turn_rows,
            "facts": [fact.to_json() for fact in turn_facts],
            "projected_tape_events": [event.to_json() for event in projected_turn_events],
        },
        "all_tape_events": [event.to_json() for event in store.events()],
        "workspace_files": sorted(path.name for path in workspace.iterdir()),
        "fibonacci_py": fibonacci_path.read_text(encoding="utf-8") if fibonacci_path.exists() else None,
    }
    (out_dir / "mapping.json").write_text(
        json.dumps(jsonable(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(out_dir)


if __name__ == "__main__":
    main()
