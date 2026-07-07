from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import AgentRole, EventType
from .models import OrchestratorEvent

RUNS_DIR = Path(".opencode-orchestrator") / "runs"


class EventLog:
    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._path = RUNS_DIR / run_id / "events.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: list[OrchestratorEvent] = []
        self._load()

    def _load(self) -> None:
        import json
        self._cache = []
        if not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                self._cache.append(OrchestratorEvent(**json.loads(line)))

    def _append(self, event: OrchestratorEvent) -> None:
        import json
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.model_dump(mode="json"), default=str) + "\n")

    def record(
        self,
        event_type: EventType,
        work_unit_id: str | None = None,
        agent_role: AgentRole | None = None,
        details: dict[str, Any] | None = None,
    ) -> OrchestratorEvent:
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
        return event

    def all(self) -> list[OrchestratorEvent]:
        return list(self._cache)

    def filter(self, event_type: EventType) -> list[OrchestratorEvent]:
        return [e for e in self._cache if e.event == event_type]

    def count(self) -> int:
        return len(self._cache)
