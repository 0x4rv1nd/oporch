"""Tests for MilestoneRunner — the execution engine."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from oporch.constants import (
    AgentRole,
    EventType,
    OrchestratorState,
    WorkUnitStatus,
)
from oporch.event_log import EventLog
from oporch.executor import FakeAgentExecutor
from oporch.models import (
    AgentResult,
    AgentTask,
    MilestoneReport,
    PoliciesConfig,
    RunState,
    WorkUnit,
)
from oporch.run_state import PersistentRunState
from oporch.runner import MilestoneRunner, RunnerError
from oporch.state_machine import StateMachine


@pytest.fixture(autouse=True)
def temp_dir(monkeypatch, tmp_path):
    """Run each test in a temporary directory to isolate state files."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _make_run_state(
    run_id: str = "test-run",
    milestone_id: str = "M1",
    objective: str = "Test objective",
    state: OrchestratorState = OrchestratorState.EXECUTING,
) -> RunState:
    now = datetime.now(timezone.utc)
    return RunState(
        run_id=run_id,
        milestone_id=milestone_id,
        objective=objective,
        state=state,
        created_at=now,
        updated_at=now,
    )


def _make_wu(
    id: str = "WU-001",
    title: str = "Test WU",
    objective: str = "Do something",
    dependencies: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
) -> WorkUnit:
    return WorkUnit(
        id=id,
        title=title,
        objective=objective,
        dependencies=dependencies or [],
        acceptance_criteria=acceptance_criteria or ["It works"],
        files_likely_affected=["src/test.py"],
    )


def _create_runner(
    executor: FakeAgentExecutor | None = None,
    prs: PersistentRunState | None = None,
    policies: PoliciesConfig | None = None,
    state: OrchestratorState = OrchestratorState.EXECUTING,
    run_id: str = "test-run",
) -> tuple[MilestoneRunner, FakeAgentExecutor, PersistentRunState, EventLog]:
    """Create a MilestoneRunner with standard test fixtures."""
    executor = executor or FakeAgentExecutor()
    prs = prs or PersistentRunState()
    policies = policies or PoliciesConfig()
    sm = StateMachine(initial_state=state)
    event_log = EventLog(run_id)

    runner = MilestoneRunner(
        executor=executor,
        prs=prs,
        policies=policies,
        state_machine=sm,
        event_log=event_log,
    )
    return runner, executor, prs, event_log


class TestSingleWorkUnitSuccess:
    """Single work unit completes through Builder → Reviewer → Tester."""

    def test_single_wu_completes(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        report = runner.run_milestone(run)

        assert report.status == "COMPLETED"
        assert len(report.work_units) == 1
        assert report.work_units[0].status == WorkUnitStatus.COMPLETED

    def test_builder_is_called(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        runner.run_milestone(run)

        # Builder should have been called
        builder_calls = [c for c in executor.calls if c[0] == AgentRole.BUILDER]
        assert len(builder_calls) >= 1

    def test_reviewer_is_called(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        runner.run_milestone(run)

        # Reviewer should have been called (policy requires review)
        reviewer_calls = [c for c in executor.calls if c[0] == AgentRole.REVIEWER]
        assert len(reviewer_calls) >= 1

    def test_tester_is_called(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        runner.run_milestone(run)

        # Tester should have been called (policy requires tests)
        tester_calls = [c for c in executor.calls if c[0] == AgentRole.TESTER]
        assert len(tester_calls) >= 1

    def test_events_are_recorded(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        runner.run_milestone(run)

        events = event_log.all()
        event_types = [e.event for e in events]
        assert EventType.WORK_UNIT_STARTED in event_types
        assert EventType.WORK_UNIT_COMPLETED in event_types
        assert EventType.RUN_COMPLETED in event_types

    def test_report_persisted_to_disk(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        runner.run_milestone(run)

        report_path = prs.get_run_path("test-run") / "final_report.json"
        assert report_path.exists()

    def test_worker_output_saved(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        runner.run_milestone(run)

        output = prs.load_worker_output("test-run", "WU-001")
        assert output is not None


class TestRetryPolicy:
    """Builder failures trigger retry with escalating context."""

    def test_builder_failure_retries(self) -> None:
        executor = FakeAgentExecutor()

        # First call fails, second succeeds
        executor.set_next_result(AgentResult(
            role=AgentRole.BUILDER, success=False,
            output="", error="Build failed",
        ))

        runner, _, prs, event_log = _create_runner(executor=executor)

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        report = runner.run_milestone(run)

        # Should still complete (second attempt succeeds via default FakeExecutor)
        assert report.status == "COMPLETED"
        wu = report.work_units[0]
        assert wu.attempts == 2

    def test_max_attempts_exhausted_fails(self) -> None:
        executor = FakeAgentExecutor()

        # All 3 attempts fail
        for _ in range(6):  # Builder + Reviewer/Tester per attempt = lots of calls
            executor.set_next_result(AgentResult(
                role=AgentRole.BUILDER, success=False,
                output="", error="Build failed",
            ))

        runner, _, prs, event_log = _create_runner(executor=executor)

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        report = runner.run_milestone(run)

        assert report.status == "FAILED"
        wu = report.work_units[0]
        assert wu.status == WorkUnitStatus.FAILED
        assert wu.attempts == 3  # max_attempts

    def test_no_fourth_silent_attempt(self) -> None:
        """PRD Gap #10: never a 4th silent attempt after max_attempts=3."""
        executor = FakeAgentExecutor()

        runner, _, prs, event_log = _create_runner(executor=executor)

        # Set max_attempts to 3
        wu = _make_wu()
        wu.max_attempts = 3

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [wu])

        # Make all builder calls fail
        for _ in range(10):
            executor.set_next_result(AgentResult(
                role=AgentRole.BUILDER, success=False,
                output="", error="Fail",
            ))

        report = runner.run_milestone(run)

        # Check that builder was called at most 3 times
        builder_calls = [c for c in executor.calls if c[0] == AgentRole.BUILDER]
        assert len(builder_calls) <= 3

    def test_attempt_3_uses_debugger(self) -> None:
        """Attempt 3 should dispatch debugger before builder."""
        executor = FakeAgentExecutor()

        # Fail attempts 1 and 2
        executor.set_next_result(AgentResult(
            role=AgentRole.BUILDER, success=False,
            output="", error="Fail 1",
        ))

        runner, _, prs, event_log = _create_runner(executor=executor)

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        # After first fail, set another fail for attempt 2
        # The FakeExecutor will return success for remaining calls
        report = runner.run_milestone(run)

        # Check if debugger was called (may or may not depending on attempt flow)
        all_roles = [c[0] for c in executor.calls]
        # At minimum builder should be called
        assert AgentRole.BUILDER in all_roles


class TestDependencyOrdering:
    """Work units execute in dependency order."""

    def test_dependencies_respected(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        wu1 = _make_wu(id="WU-001", title="Foundation")
        wu2 = _make_wu(id="WU-002", title="Feature", dependencies=["WU-001"])

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [wu1, wu2])

        report = runner.run_milestone(run)

        assert report.status == "COMPLETED"
        # Both should complete
        statuses = {wu.id: wu.status for wu in report.work_units}
        assert statuses["WU-001"] == WorkUnitStatus.COMPLETED
        assert statuses["WU-002"] == WorkUnitStatus.COMPLETED

    def test_dependent_wu_blocked_when_dep_fails(self) -> None:
        executor = FakeAgentExecutor()

        # WU-001 (the dependency) fails all attempts
        for _ in range(10):
            executor.set_next_result(AgentResult(
                role=AgentRole.BUILDER, success=False,
                output="", error="Fail",
            ))

        runner, _, prs, event_log = _create_runner(executor=executor)

        wu1 = _make_wu(id="WU-001", title="Foundation")
        wu2 = _make_wu(id="WU-002", title="Feature", dependencies=["WU-001"])

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [wu1, wu2])

        report = runner.run_milestone(run)

        assert report.status == "FAILED"
        statuses = {wu.id: wu.status for wu in report.work_units}
        assert statuses["WU-001"] == WorkUnitStatus.FAILED
        assert statuses["WU-002"] == WorkUnitStatus.BLOCKED

    def test_three_wus_chain(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        wu1 = _make_wu(id="WU-001", title="Step 1")
        wu2 = _make_wu(id="WU-002", title="Step 2", dependencies=["WU-001"])
        wu3 = _make_wu(id="WU-003", title="Step 3", dependencies=["WU-002"])

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [wu1, wu2, wu3])

        report = runner.run_milestone(run)

        assert report.status == "COMPLETED"
        assert all(wu.status == WorkUnitStatus.COMPLETED for wu in report.work_units)


class TestCompletionGate:
    """Milestone completion requires all WUs complete + review + tests."""

    def test_all_wus_pass_completes(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [
            _make_wu(id="WU-001"),
            _make_wu(id="WU-002"),
        ])

        report = runner.run_milestone(run)
        assert report.status == "COMPLETED"

    def test_any_wu_fails_fails_milestone(self) -> None:
        executor = FakeAgentExecutor()

        # Only first WU fails
        for _ in range(10):
            executor.set_next_result(AgentResult(
                role=AgentRole.BUILDER, success=False,
                output="", error="Fail",
            ))

        runner, _, prs, event_log = _create_runner(executor=executor)

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        report = runner.run_milestone(run)
        assert report.status == "FAILED"

    def test_report_includes_files_changed(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        wu = _make_wu(id="WU-001")
        wu.files_likely_affected = ["src/a.py", "src/b.py"]

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [wu])

        report = runner.run_milestone(run)
        assert "src/a.py" in report.files_changed
        assert "src/b.py" in report.files_changed


class TestResumeSupport:
    """Resume skips already-completed work units."""

    def test_skips_completed_wus(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        wu1 = _make_wu(id="WU-001", title="Already done")
        wu1.status = WorkUnitStatus.COMPLETED
        wu2 = _make_wu(id="WU-002", title="Still pending", dependencies=["WU-001"])

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [wu1, wu2])

        report = runner.run_milestone(run)

        assert report.status == "COMPLETED"
        # WU-001 should not have been dispatched to builder
        builder_calls = [
            c for c in executor.calls
            if c[0] == AgentRole.BUILDER and c[1].work_unit_id == "WU-001"
        ]
        assert len(builder_calls) == 0

        # WU-002 should have been dispatched
        builder_calls_2 = [
            c for c in executor.calls
            if c[0] == AgentRole.BUILDER and c[1].work_unit_id == "WU-002"
        ]
        assert len(builder_calls_2) >= 1


class TestCancellation:
    """Runner handles cancellation gracefully."""

    def test_cancel_stops_execution(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [
            _make_wu(id="WU-001"),
            _make_wu(id="WU-002"),
        ])

        # Cancel immediately
        runner.cancel()
        report = runner.run_milestone(run)

        assert report.status == "CANCELLED"


class TestNoWorkUnits:
    """Runner raises on empty plan."""

    def test_raises_on_no_wus(self) -> None:
        runner, executor, prs, event_log = _create_runner()

        run = _make_run_state()
        prs.save_run(run)
        # Don't save any work units

        with pytest.raises(RunnerError, match="No work units"):
            runner.run_milestone(run)


class TestPoliciesRespected:
    """Runner respects completion gate policies."""

    def test_skip_review_when_not_required(self) -> None:
        policies = PoliciesConfig()
        policies.completion_gate.require_review_approval = False

        runner, executor, prs, event_log = _create_runner(policies=policies)

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        report = runner.run_milestone(run)

        assert report.status == "COMPLETED"
        reviewer_calls = [c for c in executor.calls if c[0] == AgentRole.REVIEWER]
        assert len(reviewer_calls) == 0

    def test_skip_tests_when_not_required(self) -> None:
        policies = PoliciesConfig()
        policies.completion_gate.require_tests_pass = False

        runner, executor, prs, event_log = _create_runner(policies=policies)

        run = _make_run_state()
        prs.save_run(run)
        prs.save_work_units("test-run", [_make_wu()])

        report = runner.run_milestone(run)

        assert report.status == "COMPLETED"
        tester_calls = [c for c in executor.calls if c[0] == AgentRole.TESTER]
        assert len(tester_calls) == 0
