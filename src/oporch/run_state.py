from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .constants import OrchestratorState, SCHEMA_VERSION
from pydantic import BaseModel

from .models import CurrentRun, MilestoneReport, RunState, WorkUnit
from .state_machine import StateMachine


class RunStateError(Exception):
    pass


ORCHESTRATOR_DIR = Path(".opencode-orchestrator")
STATE_DIR = ORCHESTRATOR_DIR / "state"
RUNS_DIR = ORCHESTRATOR_DIR / "runs"


def _ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    import json
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw) if raw.strip() else None


def _check_schema_version(data: dict[str, Any] | list, label: str) -> None:
    if isinstance(data, dict):
        ver = data.get("schema_version", 0)
        if ver != SCHEMA_VERSION:
            raise RunStateError(
                f"{label} has schema_version {ver}, expected {SCHEMA_VERSION}"
            )


def _save_json(path: Path, data: Any) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


class PersistentRunState:
    def __init__(self) -> None:
        _ensure_dirs()

    def load_current(self) -> CurrentRun | None:
        path = STATE_DIR / "current_run.json"
        data = _load_json(path)
        if data is None:
            return None
        _check_schema_version(data, "current_run.json")
        return CurrentRun(**data)

    def save_current(self, run: CurrentRun | BaseModel | dict) -> None:
        if isinstance(run, BaseModel):
            data = run.model_dump(mode="json")
        else:
            data = run
        _save_json(STATE_DIR / "current_run.json", data)

    def clear_current(self) -> None:
        path = STATE_DIR / "current_run.json"
        if path.exists():
            path.unlink()

    def get_run_path(self, run_id: str) -> Path:
        p = RUNS_DIR / run_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def load_run(self, run_id: str) -> RunState | None:
        path = self.get_run_path(run_id) / "run_state.json"
        data = _load_json(path)
        if data is None:
            return None
        _check_schema_version(data, f"run_state.json ({run_id})")
        return RunState(**data)

    def save_run(self, run_state: RunState) -> None:
        path = self.get_run_path(run_state.run_id) / "run_state.json"
        _save_json(path, run_state.model_dump(mode="json"))

    def load_work_units(self, run_id: str) -> list[WorkUnit]:
        path = self.get_run_path(run_id) / "work_units.json"
        data = _load_json(path)
        if data is None:
            return []
        return [WorkUnit(**wu) for wu in data]

    def save_work_units(self, run_id: str, units: list[WorkUnit]) -> None:
        path = self.get_run_path(run_id) / "work_units.json"
        _save_json(path, [u.model_dump(mode="json") for u in units])

    def load_milestones(self) -> list[dict[str, Any]]:
        path = STATE_DIR / "milestones.json"
        data = _load_json(path)
        return data if isinstance(data, list) else []

    def save_milestones(self, milestones: list[dict[str, Any]]) -> None:
        _save_json(STATE_DIR / "milestones.json", milestones)

    def save_report(self, run_id: str, report: MilestoneReport) -> None:
        path = self.get_run_path(run_id) / "final_report.json"
        _save_json(path, report.model_dump(mode="json"))

    def load_worker_output(self, run_id: str, work_unit_id: str) -> str | None:
        path = self.get_run_path(run_id) / "worker_outputs" / f"{work_unit_id}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def save_worker_output(self, run_id: str, work_unit_id: str, output: str) -> None:
        path = self.get_run_path(run_id) / "worker_outputs"
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{work_unit_id}.txt").write_text(output, encoding="utf-8")

    def save_plan(self, run_id: str, plan: dict) -> None:
        path = self.get_run_path(run_id) / "plan.json"
        _save_json(path, plan)

    def load_plan(self, run_id: str) -> dict | None:
        path = self.get_run_path(run_id) / "plan.json"
        return _load_json(path)


def create_run_state(
    milestone_id: str,
    objective: str,
    approval_mode: str = "SUPERVISED",
) -> RunState:
    now = datetime.now(timezone.utc)
    import uuid
    run_id = str(uuid.uuid4())[:8]
    return RunState(
        run_id=run_id,
        milestone_id=milestone_id,
        objective=objective,
        state=OrchestratorState.IDLE,
        created_at=now,
        updated_at=now,
        approval_mode=approval_mode,
    )
