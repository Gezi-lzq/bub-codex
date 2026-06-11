#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bub_codex.codex_thread_service import CodexTurn, ThreadMaterialization
from bub_codex.runtime import BubCodexRuntime
from bub_codex.tape_store import InMemoryTapeStore


@dataclass(slots=True)
class FakeCodexThreadService:
    created: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    fail_next_create: bool = False

    def materialize_thread(self, *, cwd: str, anchor_id: str, intent: str) -> ThreadMaterialization:
        if self.fail_next_create:
            self.fail_next_create = False
            raise RuntimeError("simulated Codex thread materialization failure")
        thread_id = f"codex-thread-{len(self.created) + 1}"
        turn_id = f"codex-turn-{len(self.created) + 1}"
        self.created.append(thread_id)
        return ThreadMaterialization(thread_id=thread_id, turn_id=turn_id)

    def resume_thread(self, thread_id: str) -> None:
        self.resumed.append(thread_id)

    def run_turn(self, *, thread_id: str, cwd: str, prompt: str) -> CodexTurn:
        return CodexTurn(
            thread_id=thread_id,
            turn_id="codex-user-turn-1",
            notification_records=(
                {
                    "method": "turn/started",
                    "payload": {"threadId": thread_id, "turn": {"id": "codex-user-turn-1"}},
                },
                {
                    "method": "turn/completed",
                    "payload": {"threadId": thread_id, "turn": {"id": "codex-user-turn-1"}},
                },
            ),
        )


def main() -> None:
    store = InMemoryTapeStore()
    threads = FakeCodexThreadService()
    runtime = BubCodexRuntime(store, threads)

    first = runtime.context_kernel.ensure_thread_context(
        session_id="runtime-spike-session",
        tape_id="runtime-spike-tape",
        cwd=str(ROOT),
        intent="Start the runtime facade spike.",
    )
    second = runtime.context_kernel.ensure_thread_context(
        session_id="runtime-spike-session",
        tape_id="runtime-spike-tape",
        cwd=str(ROOT),
        intent="Resume the existing thread.",
    )

    failed_store = InMemoryTapeStore()
    failing_threads = FakeCodexThreadService(fail_next_create=True)
    failing_runtime = BubCodexRuntime(failed_store, failing_threads)
    failed = failing_runtime.context_kernel.ensure_thread_context(
        session_id="runtime-failure-session",
        tape_id="runtime-failure-tape",
        cwd=str(ROOT),
        intent="Start and simulate thread bind failure.",
    )
    retry = failing_runtime.context_kernel.ensure_thread_context(
        session_id="runtime-failure-session",
        tape_id="runtime-failure-tape",
        cwd=str(ROOT),
        intent="Retry from the same Anchor after bind failure.",
    )
    turn = runtime.run_turn(
        session_id="runtime-spike-session",
        tape_id="runtime-spike-tape",
        cwd=str(ROOT),
        prompt="Run a fake user turn.",
    )

    assert first.status == "bootstrapped"
    assert first.thread_id == "codex-thread-1"
    assert first.appended_events[-1].payload["refs"]["materialization_turn_id"] == "codex-turn-1"
    assert [event.type for event in first.appended_events] == [
        "bub.anchor.creation.started",
        "bub.anchor.created",
        "bub.context.materialized",
        "codex.thread.bound",
    ]
    assert second.status == "resumed"
    assert second.thread_id == "codex-thread-1"
    assert second.appended_events == ()
    assert failed.status == "bind_failed"
    assert [event.type for event in failed.appended_events] == [
        "bub.anchor.creation.started",
        "bub.anchor.created",
        "bub.context.materialized",
        "codex.thread.bind.failed",
    ]
    assert retry.status == "materialized"
    assert retry.anchor_id == failed.anchor_id
    assert retry.thread_id == "codex-thread-1"
    assert retry.appended_events[-1].payload["refs"]["materialization_turn_id"] == "codex-turn-1"
    assert turn.turn_id == "codex-user-turn-1"
    assert [event.type for event in turn.appended_events] == [
        "codex.turn.started",
        "codex.turn.completed",
    ]

    print(
        json.dumps(
            {
                "first": first.to_json(),
                "second": second.to_json(),
                "failed": failed.to_json(),
                "retry": retry.to_json(),
                "turn": turn.to_json(),
                "created_threads": threads.created,
                "resumed_threads": threads.resumed,
                "failure_store_event_types": [event.type for event in failed_store.events()],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
