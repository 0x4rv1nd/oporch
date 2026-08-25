"""Memory retrieval wiring + structured event schema (PRD §6 phase 7, §9)."""

from __future__ import annotations

import pytest

from oporch.constants import AgentRole, EventType, WorkUnitStatus
from oporch.context_builder import build_context_for_role
from oporch.db import OporchDB
from oporch.event_log import EventLog
from oporch.executor import FakeAgentExecutor
from oporch.models import (
    AgentResult,
    CompletionGate,
    ContextPack,
    PoliciesConfig,
    RetryPolicy,
    WorkUnit,
)
from oporch.runner import MilestoneRunner
from oporch.run_state import PersistentRunState
from oporch.state_machine import StateMachine


@pytest.fixture()
def db():
    d = OporchDB()
    yield d
    d.close()


class TestContextPackMemory:
    def test_project_memory_field_default(self):
        ctx = ContextPack()
        assert ctx.project_memory == []

    def test_build_context_for_role_carries_memory(self):
        wu = WorkUnit(id="WU-1", title="T", objective="O")
        ctx = build_context_for_role(
            "builder", wu, project_memory=["always run migrations first"],
        )
        assert "always run migrations first" in ctx.project_memory

    def test_string_roles_get_default_ctx_with_memory(self):
        wu = WorkUnit(id="WU-1", title="T", objective="O")
        ctx = build_context_for_role("db_migration", wu, project_memory=["m1"])
        assert ctx.project_memory == ["m1"]

    def test_prompt_includes_known_project_memory(self):
        from oporch.executor import OpenCodeAgentExecutor

        ex = OpenCodeAgentExecutor()
        task = type("T", (), {
            "raw_prompt": None,
            "objective": "obj",
            "work_unit_id": "WU-1",
            "acceptance_criteria": [],
        })()
        ctx = ContextPack(project_memory=["never edit .env"])
        prompt = ex._build_prompt("builder", task, ctx)
        assert "## Known project memory" in prompt
        assert "- never edit .env" in prompt

    def test_prompt_omits_section_when_empty(self):
        from oporch.executor import OpenCodeAgentExecutor

        ex = OpenCodeAgentExecutor()
        task = type("T", (), {"raw_prompt": None, "objective": "o",
                              "work_unit_id": None, "acceptance_criteria": []})()
        prompt = ex._build_prompt("builder", task, ContextPack())
        assert "Known project memory" not in prompt


def _make_runner(db=None, executor=None, run_id=None):
    run_id = run_id or ("mem-" + str(abs(id(db or executor)) % 10**8))
    executor = executor or FakeAgentExecutor()
    prs = PersistentRunState(db=db)
    policies = PoliciesConfig(
        retry=RetryPolicy(max_attempts=3),
        completion_gate=CompletionGate(require_review_approval=False,
                                       require_tests_pass=False),
    )
    runner = MilestoneRunner(
        executor=executor,
        prs=prs,
        policies=policies,
        state_machine=StateMachine(),
        event_log=EventLog(run_id, db=db),
        dispatcher=None,
        db=db,
    )
    return runner, executor, prs


class TestRunnerMemoryWiring:
    @pytest.mark.asyncio
    async def test_builder_context_includes_recalled_memory(self, db, monkeypatch):
        project = _project_dir()
        monkeypatch.chdir(project)
        db.remember(str(project), "db_migration", "gotcha",
                    "always backup the users table before migrating")

        runner, executor, _ = _make_runner(db=db)
        from datetime import datetime, timezone
        from oporch.models import RunState

        rs = RunState(run_id="mem-r1", milestone_id="M", objective="o",
                      created_at=datetime.now(timezone.utc),
                      updated_at=datetime.now(timezone.utc))
        runner.prs.save_run(rs)
        wu = WorkUnit(id="WU-M1", title="migrate users table",
                      objective="migration", assigned_role="db_migration")
        ok = await runner._execute_work_unit(rs, wu, _graph([wu]), set())
        assert ok is True
        build_calls = [c for c in executor.calls if c[0] == "db_migration"]
        assert build_calls, "builder dispatched under roster role"
        memory_used = build_calls[0][2].project_memory
        assert any("backup" in m for m in memory_used)

    def test_failure_pattern_recorded_on_exhaustion(self, db, monkeypatch):
        project = _project_dir()
        monkeypatch.chdir(project)
        executor = FakeAgentExecutor()
        for _ in range(6):
            executor.set_next_result(
                AgentResult(role="builder", success=False, error="boom")
            )
        runner, _, prs = _make_runner(db=db, executor=executor)
        from datetime import datetime, timezone
        from oporch.models import RunState

        rid = "mem-fail"
        rs = RunState(run_id=rid, milestone_id="M", objective="o",
                      created_at=datetime.now(timezone.utc),
                      updated_at=datetime.now(timezone.utc))
        prs.save_run(rs)
        wu = WorkUnit(id="WU-F1", title="explode on purpose",
                      objective="fail", assigned_role="backend")
        import asyncio

        asyncio.run(runner._execute_work_unit(rs, wu, _graph([wu]), set()))
        patterns = [
            h for h in db.recall(str(project), role_key="backend")
            if h["memory_type"] == "failure_pattern"
        ]
        assert any("attempts exhausted" in p["content"] for p in patterns)

    def test_memories_boosted_on_success(self, db, monkeypatch):
        project = _project_dir()
        monkeypatch.chdir(project)
        mid = db.remember(str(project), "builder", "fact", "use uv not pip for installs")
        runner, _, prs = _make_runner(db=db)
        from datetime import datetime, timezone
        from oporch.models import RunState

        rs = RunState(run_id="mem-boost", milestone_id="M", objective="o",
                      created_at=datetime.now(timezone.utc),
                      updated_at=datetime.now(timezone.utc))
        prs.save_run(rs)
        wu = WorkUnit(id="WU-B1", title="install deps uv",
                      objective="deps", assigned_role="builder",
                      acceptance_criteria=["uv lock works"])
        import asyncio

        asyncio.run(runner._execute_work_unit(rs, wu, _graph([wu]), set()))
        rows = {r["id"]: r for r in db.recall(str(project))}
        assert rows[mid]["relevance_score"] > 1.0


class TestStructuredEvents:
    def test_event_columns_persisted(self, db):
        db.append_event(
            "run-s", "WORK_UNIT_COMPLETED", wu_id="WU-9",
            duration_ms=1234.5, model_used="opencode/x", tokens_in=10,
            tokens_out=5, level="info",
        )
        row = db.all_events("run-s")[-1]
        assert row["duration_ms"] == 1234.5
        assert row["model_used"] == "opencode/x"
        assert row["tokens_in"] == 10 and row["tokens_out"] == 5

    @pytest.mark.asyncio
    async def test_runner_writes_duration_and_model(self, db, monkeypatch):
        monkeypatch.chdir(_project_dir())
        rid = "struct-e"
        runner, executor, prs = _make_runner(db=db, run_id=rid)
        from datetime import datetime, timezone
        from oporch.models import RunState

        rs = RunState(run_id=rid, milestone_id="M", objective="o",
                      created_at=datetime.now(timezone.utc),
                      updated_at=datetime.now(timezone.utc))
        prs.save_run(rs)
        wu = WorkUnit(id="WU-E1", title="t", objective="o")
        await runner._execute_work_unit(rs, wu, _graph([wu]), set())

        events = db.all_events(rid)
        completed = [e for e in events if e["event_type"] == "WORK_UNIT_COMPLETED"
                     and e["level"] != "error"]
        # duration recorded on success event (model may be None without config)
        assert completed and completed[-1]["duration_ms"] is not None


def _graph(wus):
    from oporch.work_unit import WorkUnitGraph

    g = WorkUnitGraph(wus)
    g.validate()
    return g


def _project_dir():
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp(prefix="oporch-memproj-"))
    if not d.exists():
        d.mkdir(parents=True)
    return d
