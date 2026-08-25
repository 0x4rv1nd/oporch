"""Tests for the parallel dispatcher and async executor (PRD §6 phase 4)."""

from __future__ import annotations

import asyncio
import time

import pytest

from oporch.constants import AgentRole, WorkUnitStatus
from oporch.event_log import EventLog
from oporch.executor import (
    FakeAgentExecutor,
    OpenCodeAgentExecutor,
    call_executor_async,
)
from oporch.models import (
    AgentResult,
    AgentTask,
    CompletionGate,
    ContextPack,
    PoliciesConfig,
    RetryPolicy,
    RunState,
    TeamRole,
    TeamRoster,
)
from oporch.runner import MilestoneRunner, ParallelDispatcher
from oporch.run_state import PersistentRunState
from oporch.state_machine import StateMachine


def _make_wu(id="WU-001", title="T", deps=None, role="builder", **kw):
    from oporch.models import WorkUnit

    return WorkUnit(
        id=id,
        title=title,
        objective=f"Objective {id}",
        dependencies=deps or [],
        assigned_role=role,
        acceptance_criteria=["works"],
        **kw,
    )


def _graph(wus):
    from oporch.work_unit import WorkUnitGraph

    g = WorkUnitGraph(wus)
    g.validate()
    return g


def _make_runner(executor=None, policies=None):
    executor = executor or FakeAgentExecutor()
    prs = PersistentRunState()
    event_log = EventLog("disp-test")
    sm = StateMachine()
    policies = policies or PoliciesConfig(
        retry=RetryPolicy(max_attempts=3),
        completion_gate=CompletionGate(require_review_approval=False,
                                       require_tests_pass=False),
    )
    runner = MilestoneRunner(
        executor=executor,
        prs=prs,
        policies=policies,
        state_machine=sm,
        event_log=event_log,
    )
    return runner, executor, prs, event_log


def _make_run_state(run_id=None):
    import uuid
    from datetime import datetime, timezone

    return RunState(
        run_id=run_id or ("disp-" + str(uuid.uuid4())[:8]),
        milestone_id="M1",
        objective="parallel test",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


class TestParallelDispatcherBasics:
    def test_semaphores_from_team_roster(self):
        roster = TeamRoster(
            run_id="r",
            roles=[
                TeamRole(key="backend", description="", model="m", max_workers=2),
                TeamRole(key="frontend", description="", model="m", max_workers=1),
            ],
        )
        d = ParallelDispatcher(roster, FakeAgentExecutor())
        assert isinstance(d.semaphores["backend"], asyncio.Semaphore)
        # internal counter reflects max_workers
        assert d.semaphores["frontend"]._value == 1
        assert d.semaphores["backend"]._value == 2

    def test_semaphores_from_dict(self):
        d = ParallelDispatcher({"builder": 3}, FakeAgentExecutor())
        assert d.semaphores["builder"]._value == 3

    def test_unknown_role_gets_default_semaphore(self):
        d = ParallelDispatcher(None, FakeAgentExecutor())
        sem = d.semaphore_for("db_migration")
        assert sem._value == 1

    def test_resize_replaces_semaphore(self):
        d = ParallelDispatcher({"backend": 1}, FakeAgentExecutor())
        d.resize("backend", 4)
        assert d.max_workers["backend"] == 4
        assert d.semaphores["backend"]._value == 4

    @pytest.mark.asyncio
    async def test_wave_runs_all_ready_units(self):
        runner, executor, prs, event_log = _make_runner()
        run_state = _make_run_state()

        wus = [_make_wu(f"WU-00{i}") for i in range(1, 4)]
        graph = _graph(wus)

        dispatcher = ParallelDispatcher({"builder": 2}, executor)
        ready = graph.get_ready(set())
        results = await dispatcher.run_ready_wave(
            runner, run_state, graph, set(), ready,
        )
        assert all(results)
        assert len(executor.calls) >= 3


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_max_workers_bounds_concurrency(self):
        """With max_workers=1 for the role, WUs must not overlap."""

        class SlowFake(FakeAgentExecutor):
            def __init__(self):
                super().__init__()
                self.concurrent = 0
                self.max_concurrent = 0

            async def run_async(self, role, task, context):
                self.concurrent += 1
                self.max_concurrent = max(self.max_concurrent, self.concurrent)
                await asyncio.sleep(0.05)
                self.concurrent -= 1
                return AgentResult(role=str(role), success=True, output="ok")

        executor = SlowFake()
        runner, _, prs, _ = _make_runner(executor=executor)
        run_state = _make_run_state()

        wus = [_make_wu(f"WU-00{i}") for i in range(1, 5)]
        dispatcher = ParallelDispatcher({"builder": 1}, executor)
        ready = _graph(wus).get_ready(set())
        await dispatcher.run_ready_wave(runner, run_state, _graph(wus), set(), ready)
        assert executor.max_concurrent == 1

    @pytest.mark.asyncio
    async def test_higher_max_workers_runs_parallel(self):
        class SlowFake(FakeAgentExecutor):
            def __init__(self):
                super().__init__()
                self.concurrent = 0
                self.max_concurrent = 0

            async def run_async(self, role, task, context):
                self.concurrent += 1
                self.max_concurrent = max(self.max_concurrent, self.concurrent)
                await asyncio.sleep(0.08)
                self.concurrent -= 1
                return AgentResult(role=str(role), success=True, output="ok")

        executor = SlowFake()
        runner, _, _, _ = _make_runner(executor=executor)
        run_state = _make_run_state()

        wus = [_make_wu(f"WU-00{i}") for i in range(1, 5)]
        dispatcher = ParallelDispatcher({"builder": 4}, executor)
        ready = _graph(wus).get_ready(set())
        await dispatcher.run_ready_wave(runner, run_state, _graph(wus), set(), ready)
        assert executor.max_concurrent == 4

    @pytest.mark.asyncio
    async def test_role_semaphores_are_independent(self):
        """Two roles each with budget 1 still overlap across roles."""
        events: list[tuple[str, float]] = []

        class RoleFake(FakeAgentExecutor):
            async def run_async(self, role, task, context):
                start = time.monotonic()
                events.append(("start-" + str(role), start))
                await asyncio.sleep(0.06)
                events.append(("end-" + str(role), time.monotonic()))
                return AgentResult(role=str(role), success=True, output="ok")

        executor = RoleFake()
        runner, _, _, _ = _make_runner(executor=executor)
        run_state = _make_run_state()

        wus = [
            _make_wu("WU-B", role="backend"),
            _make_wu("WU-F", role="frontend"),
        ]
        dispatcher = ParallelDispatcher({"backend": 1, "frontend": 1}, executor)
        ready = _graph(wus).get_ready(set())
        await dispatcher.run_ready_wave(runner, run_state, _graph(wus), set(), ready)

        starts = [e for e in events if e[0].startswith("start")]
        ends = [e for e in events if e[0].startswith("end")]
        first_end = min(e[1] for e in ends)
        last_start = max(e[1] for e in starts)
        assert last_start < first_end, "roles should overlap"


class TestWaveDrainLoop:
    def _runner_with_dispatcher(self, executor=None):
        runner, executor, prs, event_log = _make_runner(executor=executor)
        dispatcher = ParallelDispatcher({"builder": 2}, executor, db=None)
        runner.dispatcher = dispatcher
        return runner, executor, prs

    def test_diamond_dag_completes(self):
        runner, executor, prs = self._runner_with_dispatcher()
        run_state = _make_run_state()

        wus = [
            _make_wu("WU-A"),
            _make_wu("WU-B1", deps=["WU-A"]),
            _make_wu("WU-B2", deps=["WU-A"]),
            _make_wu("WU-C", deps=["WU-B1", "WU-B2"]),
        ]
        prs.save_work_units(run_state.run_id, wus)
        report = runner.run_milestone(run_state)

        assert report.status == "COMPLETED"
        statuses = {wu.id: wu.status for wu in report.work_units}
        assert all(s == WorkUnitStatus.COMPLETED for s in statuses.values())

    def test_failed_dep_blocks_dependents(self):
        executor = FakeAgentExecutor()
        for _ in range(10):
            executor.set_next_result(
                AgentResult(role="builder", success=False, error="boom")
            )
        runner, _, prs = self._runner_with_dispatcher(executor)
        run_state = _make_run_state()

        wus = [
            _make_wu("WU-A"),
            _make_wu("WU-B", deps=["WU-A"]),
        ]
        prs.save_work_units(run_state.run_id, wus)
        report = runner.run_milestone(run_state)

        assert report.status == "FAILED"
        statuses = {wu.id: wu.status for wu in report.work_units}
        assert statuses["WU-A"] == WorkUnitStatus.FAILED
        assert statuses["WU-B"] == WorkUnitStatus.BLOCKED
        assert "WU-A" in statuses and True

    def test_assigned_role_used_for_builder_dispatch(self):
        runner, executor, prs = self._runner_with_dispatcher()
        run_state = _make_run_state()

        wus = [_make_wu("WU-DB", role="db_migration")]
        prs.save_work_units(run_state.run_id, wus)
        report = runner.run_milestone(run_state)
        assert report.status == "COMPLETED"
        builder_calls = [c for c in executor.calls if c[0] == "db_migration"]
        assert len(builder_calls) == 1


class TestPauseControl:
    def setup_method(self):
        from oporch.db import OporchDB

        self.db = OporchDB()

    def teardown_method(self):
        self.db.set_control("pause", "0")
        self.db.close()

    @pytest.mark.asyncio
    async def test_wait_if_paused_blocks_until_released(self):
        runner, _, _, _ = _make_runner()
        runner._db = self.db

        self.db.set_control("pause", "1")
        started = time.monotonic()
        release_task = asyncio.get_running_loop().run_in_executor(
            None, self._release_later,
        )
        await runner.wait_if_paused()
        elapsed = time.monotonic() - started
        assert elapsed >= 0.3
        await release_task

    def _release_later(self):
        time.sleep(0.4)
        self.db.set_control("pause", "0")


class TestAsyncExecutor:
    @pytest.mark.asyncio
    async def test_call_executor_async_prefers_run_async(self):
        marker = AgentResult(role="x", success=True, output="async path")

        class AsyncOnly:
            async def run_async(self, role, task, context):
                return marker

        result = await call_executor_async(AsyncOnly(), "builder",
                                           AgentTask(objective="o"), ContextPack())
        assert result is marker

    @pytest.mark.asyncio
    async def test_call_executor_async_falls_back_to_thread(self):
        executor = FakeAgentExecutor()
        result = await call_executor_async(
            executor, "reviewer", AgentTask(objective="o"), ContextPack(),
        )
        assert result.success
        assert result.role == "reviewer"

    @pytest.mark.asyncio
    async def test_opencode_run_async_missing_binary(self):
        ex = OpenCodeAgentExecutor(opencode_cmd="__no_such_opencode_bin__")
        result = await ex.run_async(
            "builder", AgentTask(objective="o"), ContextPack(),
        )
        assert result.success is False
        assert "not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_opencode_run_async_success(self, tmp_path):
        """Run against a stub 'opencode' executable."""
        if __import__("sys").platform != "win32":
            pytest.skip("windows-specific stub")
        stub = tmp_path / "opencode.bat"
        stub.write_text("@echo off\necho stub-output\n", encoding="utf-8")
        ex = OpenCodeAgentExecutor()
        ex._cmd = str(stub)
        result = await ex.run_async(
            "builder", AgentTask(objective="o"), ContextPack(),
        )
        assert result.success is True
        assert result.output.strip() == "stub-output"
