"""Observability CLI tests (PRD §6 phase 10, §9): replay / diff / failures."""

from __future__ import annotations

import uuid

import pytest
from typer.testing import CliRunner

from oporch.cli import app
from oporch.db import OporchDB


def _seed_run(db: OporchDB, run_id: str, *, wu_status="COMPLETED",
              role="backend", attempts=1, duration=100.0) -> None:
    db.upsert_run(run_id, milestone_id="M", state="COMPLETED")
    db.save_roster(run_id, [
        {"role_key": "backend", "description": "", "model": "m",
         "max_workers": 2, "domains": ["api"]},
    ])
    db.save_work_units(run_id, [
        {"id": "WU-001", "title": "t1", "status": wu_status,
         "assigned_role": role, "attempt": attempts},
        {"id": "WU-002", "title": "t2", "status": wu_status,
         "assigned_role": role, "attempt": attempts},
    ])
    for wid in ("WU-001", "WU-002"):
        db.append_event(run_id, "WORK_UNIT_STARTED", role=role, wu_id=wid)
        db.append_event(
            run_id,
            "WORK_UNIT_COMPLETED" if wu_status == "COMPLETED" else "REVIEW_FAILED",
            wu_id=wid,
            duration_ms=duration,
            model_used="opencode/test-model",
            level="info" if wu_status == "COMPLETED" else "error",
        )


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def two_runs():
    db = OporchDB()
    a = "obsA" + str(uuid.uuid4())[:5].replace("-", "")
    b = "obsB" + str(uuid.uuid4())[:5].replace("-", "")
    _seed_run(db, a, wu_status="COMPLETED", attempts=1, duration=50.0)
    _seed_run(db, b, wu_status="FAILED", attempts=3, duration=250.0)
    yield db, a, b
    db.close()


WIDE_ENV = {"COLUMNS": "300", "LINES": "80"}


class TestReplay:
    def test_replay_full_run(self, runner, two_runs):
        _, run_a, _ = two_runs
        result = runner.invoke(app, ["replay", run_a], env=WIDE_ENV)
        assert result.exit_code == 0, result.output
        assert "WORK_UNIT_STARTED" in result.output
        assert "WORK_UNIT_COMPLETED" in result.output

    def test_replay_scoped_to_wu(self, runner, two_runs):
        _, run_a, _ = two_runs
        result = runner.invoke(
            app, ["replay", run_a, "--wu", "WU-001"], env=WIDE_ENV,
        )
        assert result.exit_code == 0
        assert "Final status" in result.output
        assert "attempt(s)" in result.output

    def test_replay_unknown_run_fails(self, runner):
        result = runner.invoke(app, ["replay", "no-such-run-zzz"])
        assert result.exit_code == 1


class TestRunDiff:
    def test_diff_shows_metrics(self, runner, two_runs):
        _, a, b = two_runs
        result = runner.invoke(app, ["diff", a, b], env=WIDE_ENV)
        assert result.exit_code == 0, result.output
        assert "failure_rate" in result.output
        assert "total_duration_ms" in result.output

    def test_per_role_failure_rates(self, runner, two_runs):
        _, a, b = two_runs
        result = runner.invoke(app, ["diff", a, b], env=WIDE_ENV)
        assert "Per-role failure rates" in result.output
        assert "backend" in result.output


class TestFailureReport:
    def test_report_failures_lists_patterns(self, runner):
        from pathlib import Path

        db = OporchDB()
        project = str(Path.cwd())
        marker = f"[failrun-{str(uuid.uuid4())[:6]}] auth WUs rejected"
        db.remember(project, "builder", "failure_pattern", marker)
        db.close()

        result = runner.invoke(app, ["report", "--failures"], env=WIDE_ENV)
        assert result.exit_code == 0, result.output
        assert "auth WUs rejected" in result.output

    def test_report_default_path_still_works(self, runner):
        result = runner.invoke(app, ["report"])
        # No active run is fine — must not crash with traceback
        assert "Traceback" not in (result.output or "")
        assert result.exit_code in (0, 1)


class TestStructuredEventQueries:
    def test_duration_aggregatable_in_sql(self, two_runs):
        db, a, _ = two_runs
        rows = db._query(
            "SELECT SUM(duration_ms) AS total FROM events WHERE run_id = ?"
            " AND duration_ms IS NOT NULL",
            (a,),
        )
        assert rows[0]["total"] == pytest.approx(100.0)

    def test_model_column_queryable(self, two_runs):
        db, a, _ = two_runs
        rows = db._query(
            "SELECT DISTINCT model_used FROM events"
            " WHERE run_id = ? AND model_used IS NOT NULL",
            (a,),
        )
        assert {r["model_used"] for r in rows} == {"opencode/test-model"}
