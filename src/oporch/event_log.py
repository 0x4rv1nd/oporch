from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import AgentRole, EventType
from .models import OrchestratorEvent
from .redact import redact_secrets

RUNS_DIR = Path(".opencode-orchestrator") / "runs"


class EventLog:
    """Run event log.

    v2: writes through to the SQLite ``events`` table (via :mod:`oporch.db`)
    while mirroring to the legacy per-run ``events.jsonl`` file. The public
    API is unchanged.
    """

    def __init__(self, run_id: str, db: Any | None = None) -> None:
        self._run_id = run_id
        self._path = RUNS_DIR / run_id / "events.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if db is None:
            from .db import OporchDB

            db = OporchDB()
        self._db = db
        self._cache: list[OrchestratorEvent] = []
        self._load()

    def _load(self) -> None:
        import json
        self._cache = []
        if not self._path.exists():
            # No legacy file: hydrate the cache from SQLite instead.
            for row in self._db.all_events(self._run_id):
                try:
                    self._cache.append(
                        OrchestratorEvent(
                            timestamp=datetime.fromisoformat(row["ts"]),
                            run_id=row["run_id"],
                            event=row["event_type"],
                            work_unit_id=row["wu_id"],
                            agent_role=row["role"],
                            details=json.loads(row["payload"]) if row["payload"] else {},
                        )
                    )
                except Exception:
                    continue
            return
        for line in self._path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                self._cache.append(OrchestratorEvent(**json.loads(line)))

    def _append(self, event: OrchestratorEvent) -> None:
        import json
        line = json.dumps(event.model_dump(mode="json"), default=str)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(redact_secrets(line) + "\n")

    def record(
        self,
        event_type: EventType | str,
        work_unit_id: str | None = None,
        agent_role: AgentRole | str | None = None,
        details: dict[str, Any] | None = None,
        level: str = "info",
        duration_ms: float | None = None,
        model_used: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> OrchestratorEvent:
        event_value = event_type.value if hasattr(event_type, "value") else str(event_type)
        role_value = (
            agent_role.value if hasattr(agent_role, "value") else agent_role
        ) if agent_role is not None else None
        event = OrchestratorEvent(
            timestamp=datetime.now(timezone.utc),
            run_id=self._run_id,
            event=event_type,
            work_unit_id=work_unit_id,
            agent_role=agent_role,
            details=details or {},
        )
        self._cache.append(event)
        self._append(event)
        self._db.append_event(
            self._run_id,
            event_value,
            role=role_value,
            wu_id=work_unit_id,
            payload=details or {},
            level=level,
            duration_ms=duration_ms,
            model_used=model_used,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            ts=event.timestamp.isoformat(),
        )
        return event

    def all(self) -> list[OrchestratorEvent]:
        return list(self._cache)

    def filter(self, event_type: EventType) -> list[OrchestratorEvent]:
        return [e for e in self._cache if e.event == event_type]

    def count(self) -> int:
        return len(self._cache)
