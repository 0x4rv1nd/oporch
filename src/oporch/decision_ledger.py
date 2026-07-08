from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import OrchestratorDecision
from .redact import redact_secrets

STATE_DIR = Path(".opencode-orchestrator") / "state"


class DecisionLedger:
    def __init__(self) -> None:
        self._path = STATE_DIR / "decisions.jsonl"
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache: list[OrchestratorDecision] = []
        self._load()

    def _load(self) -> None:
        import json
        self._cache = []
        if not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                self._cache.append(OrchestratorDecision(**json.loads(line)))

    def _append(self, decision: OrchestratorDecision) -> None:
        import json
        line = json.dumps(decision.model_dump(mode="json"), default=str)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(redact_secrets(line) + "\n")

    def append(self, decision: OrchestratorDecision) -> None:
        if not decision.timestamp:
            decision.timestamp = datetime.now(timezone.utc)
        self._cache.append(decision)
        self._append(decision)

    def all(self) -> list[OrchestratorDecision]:
        return list(self._cache)

    def search(self, query: str) -> list[OrchestratorDecision]:
        q = query.lower()
        return [
            d for d in self._cache
            if q in d.question.lower() or q in d.decision.lower()
        ]

    def find_by_question(self, question: str) -> OrchestratorDecision | None:
        q = question.lower().strip()
        for d in reversed(self._cache):
            if d.question.lower().strip() == q:
                return d
        return None

    def count(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache = []
        if self._path.exists():
            self._path.unlink()

    def next_id(self) -> str:
        return f"DEC-{self.count() + 1:04d}"
