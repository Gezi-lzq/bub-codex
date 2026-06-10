from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .runtime_adapter import CodexFact


JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class TapeEvent:
    """Minimal Bub tape-like event projected from normalized adapter facts."""

    type: str
    event_id: str
    payload: JsonObject
    occurred_at: str | None = None
    session_id: str | None = None
    tape_id: str | None = None
    anchor_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None

    def to_json(self) -> JsonObject:
        return asdict(self)


def load_facts_jsonl(lines: Iterable[str]) -> list[CodexFact]:
    facts: list[CodexFact] = []
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        facts.append(
            CodexFact(
                kind=str(record["kind"]),
                event_id=str(record["event_id"]),
                source=str(record["source"]),
                payload=_dict_or_empty(record.get("payload")),
                thread_id=_optional_str(record.get("thread_id")),
                turn_id=_optional_str(record.get("turn_id")),
                item_id=_optional_str(record.get("item_id")),
                occurred_at=_optional_str(record.get("occurred_at")),
            )
        )
    return facts


def project_codex_facts_to_tape_events(
    facts: Iterable[CodexFact],
    *,
    session_id: str,
    tape_id: str,
    initiator: str = "bub_runtime",
    reason: str = "user_requested",
) -> list[TapeEvent]:
    """Project normalized Codex facts to a conservative Bub tape event slice.

    v0 only materializes the compact path because the SDK spike currently proves
    compact notifications and rollout snapshots. New-thread materialization will
    use the same TapeEvent shape once the context assembler exists.
    """

    facts_list = list(facts)
    snapshots = [fact for fact in facts_list if fact.kind == "codex.compaction.snapshot"]
    events: list[TapeEvent] = []

    for fact in facts_list:
        if fact.kind != "codex.thread.compacted":
            continue

        snapshot = _best_snapshot_for_compaction(fact, snapshots)
        anchor_creation_id = _stable_id("anchor_creation", fact.event_id)
        anchor_id = _stable_id("anchor", fact.event_id)
        snapshot_ref = snapshot.event_id if snapshot else None
        summary_status = _summary_status(snapshot)

        common = {
            "session_id": session_id,
            "tape_id": tape_id,
            "thread_id": fact.thread_id,
            "turn_id": fact.turn_id,
            "occurred_at": fact.occurred_at,
        }

        events.append(
            make_tape_event(
                "bub.anchor.creation.started",
                payload={
                    "anchor_creation_id": anchor_creation_id,
                    "method": "compact",
                    "initiator": initiator,
                    "reason": reason,
                    "active_thread_id_before": fact.thread_id,
                    "source_fact_id": fact.event_id,
                },
                **common,
            )
        )

        events.append(
            make_tape_event(
                "codex.thread.compacted",
                payload={
                    "anchor_creation_id": anchor_creation_id,
                    "trigger": _compact_trigger(initiator, reason),
                    "snapshot_ref": snapshot_ref,
                    "parse_status": snapshot.payload.get("parse_status") if snapshot else None,
                    "source_fact_id": fact.event_id,
                },
                **common,
            )
        )

        if snapshot:
            events.append(
                make_tape_event(
                    "codex.compaction.snapshot",
                    payload={
                        "anchor_creation_id": anchor_creation_id,
                        "source_fact_id": snapshot.event_id,
                        "parse_status": snapshot.payload.get("parse_status"),
                        "message_sha256": snapshot.payload.get("message_sha256"),
                        "replacement_history_len": snapshot.payload.get(
                            "replacement_history_len"
                        ),
                        "replacement_history_sha256": snapshot.payload.get(
                            "replacement_history_sha256"
                        ),
                    },
                    occurred_at=snapshot.occurred_at,
                    session_id=session_id,
                    tape_id=tape_id,
                    thread_id=fact.thread_id,
                    turn_id=fact.turn_id,
                )
            )

        events.append(
            make_tape_event(
                "bub.anchor.created",
                payload={
                    "anchor_id": anchor_id,
                    "method": "compact",
                    "reason": reason,
                    "created_at": fact.occurred_at,
                    "state": {
                        "summary": snapshot.payload.get("message") if snapshot else None,
                        "summary_status": summary_status,
                    },
                    "refs": {
                        "source_anchor_creation_id": anchor_creation_id,
                        "thread_id": fact.thread_id,
                        "turn_id": fact.turn_id,
                        "source_fact_id": fact.event_id,
                        "snapshot_fact_id": snapshot_ref,
                    },
                    "initiator": initiator,
                },
                anchor_id=anchor_id,
                **common,
            )
        )

    return events


def make_tape_event(
    event_type: str,
    *,
    payload: JsonObject,
    occurred_at: str | None = None,
    session_id: str | None = None,
    tape_id: str | None = None,
    anchor_id: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> TapeEvent:
    event_id = _event_id(
        event_type,
        payload,
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=thread_id,
        turn_id=turn_id,
    )
    return TapeEvent(
        type=event_type,
        event_id=event_id,
        payload=payload,
        occurred_at=occurred_at,
        session_id=session_id,
        tape_id=tape_id,
        anchor_id=anchor_id,
        thread_id=thread_id,
        turn_id=turn_id,
    )


def _best_snapshot_for_compaction(
    compaction: CodexFact,
    snapshots: list[CodexFact],
) -> CodexFact | None:
    if not snapshots:
        return None
    if len(snapshots) == 1:
        return snapshots[0]

    after = [
        snapshot
        for snapshot in snapshots
        if snapshot.occurred_at and compaction.occurred_at and snapshot.occurred_at >= compaction.occurred_at
    ]
    return after[0] if after else snapshots[-1]


def _summary_status(snapshot: CodexFact | None) -> str:
    if snapshot is None:
        return "unavailable"
    parse_status = snapshot.payload.get("parse_status")
    if parse_status == "ok" and snapshot.payload.get("message"):
        return "ok"
    if isinstance(parse_status, str):
        return parse_status
    return "parse_failed"


def _compact_trigger(initiator: str, reason: str) -> str:
    if initiator == "codex_runtime" or reason == "auto_compact":
        return "auto"
    if initiator == "human":
        return "manual"
    return "bub_anchor_compact"


def _event_id(
    event_type: str,
    payload: JsonObject,
    *,
    occurred_at: str | None,
    session_id: str | None,
    tape_id: str | None,
    anchor_id: str | None,
    thread_id: str | None,
    turn_id: str | None,
) -> str:
    body = json.dumps(
        {
            "type": event_type,
            "payload": payload,
            "occurred_at": occurred_at,
            "session_id": session_id,
            "tape_id": tape_id,
            "anchor_id": anchor_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{value}".encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _dict_or_empty(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
