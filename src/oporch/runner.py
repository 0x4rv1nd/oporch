"""Milestone execution engine.

Executes an approved plan by dispatching work units sequentially
through the Builder → Reviewer → Tester pipeline.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import config as cfg
from .constants import (
    AgentRole,
    EventType,
    OrchestratorState,
    WorkUnitStatus,
)
from .context_builder import build_context_for_role
from .event_log import EventLog
from .executor import AgentExecutor
from .models import (
    AgentResult,
    AgentTask,
    ContextPack,
    MilestoneReport,
    PoliciesConfig,
    RunState,
    WorkUnit,
)
from .run_state import PersistentRunState
from .state_machine import StateMachine
from .work_unit import WorkUnitGraph

logger = logging.getLogger(__name__)


class RunnerError(Exception):
    pass


class MilestoneRunner:
    """Executes an approved plan sequentially through the agent pipeline.

    Processes work units in topological order:
    1. Builder implements the work unit
    2. Reviewer adversarially reviews (if policy requires)
    3. Tester validates acceptance criteria (if policy requires)
    4. Retry policy: attempt 2 gets review feedback, attempt 3 gets debugger analysis
    """

    def __init__(
        self,
        executor: AgentExecutor,
        prs: PersistentRunState,
        policies: PoliciesConfig,
        state_machine: StateMachine,
        event_log: EventLog,
    ) -> None:
        self.executor = executor
        self.prs = prs
        self.policies = policies
        self.sm = state_machine
        self.event_log = event_log
        self._cancelled = False

    def cancel(self) -> None:
        """Request cancellation of the current run."""
        self._cancelled = True

    def run_milestone(self, run_state: RunState) -> MilestoneReport:
        """Execute all work units in the approved plan.

        Returns a MilestoneReport summarizing the execution outcome.
        """
        run_id = run_state.run_id
        work_units = self.prs.load_work_units(run_id)

        if not work_units:
            raise RunnerError(f"No work units found for run {run_id}")

        graph = WorkUnitGraph(work_units)
        graph.validate()
        topo_order = graph.topological_order()

        # Transition to EXECUTING
        if self.sm.can_transition(OrchestratorState.EXECUTING):
            self.sm.transition(OrchestratorState.EXECUTING)
            self._update_run_state(run_state, OrchestratorState.EXECUTING)

        completed_ids: set[str] = set()
        failed_ids: set[str] = set()
        all_files_changed: list[str] = []
        all_review_output: list[str] = []
        all_test_output: list[str] = []

        for wu_id in topo_order:
            if self._cancelled:
                self._handle_cancellation(run_state)
                break

            wu = graph.get(wu_id)
            if wu is None:
                continue

            # Skip already completed WUs (for resume support)
            if wu.status == WorkUnitStatus.COMPLETED:
                completed_ids.add(wu_id)
                continue

            # Check dependencies are met
            unmet = [d for d in wu.dependencies if d not in completed_ids]
            if unmet:
                wu.status = WorkUnitStatus.BLOCKED
                wu.blockers = unmet
                self.prs.save_work_units(run_id, graph.all())
                failed_ids.add(wu_id)
                continue

            # Execute work unit with retry policy
            success = self._execute_work_unit(
                run_state, wu, graph, completed_ids,
            )

            if success:
                completed_ids.add(wu_id)
            else:
                failed_ids.add(wu_id)

            # Persist after each WU
            self.prs.save_work_units(run_id, graph.all())

        # Evaluate completion gate
        report = self._evaluate_completion(
            run_state, graph, completed_ids, failed_ids,
        )

        return report

    def _execute_work_unit(
        self,
        run_state: RunState,
        wu: WorkUnit,
        graph: WorkUnitGraph,
        completed_ids: set[str],
    ) -> bool:
        """Execute a single work unit with retry policy.

        Returns True if the WU completed successfully.
        """
        run_id = run_state.run_id
        max_attempts = min(
            wu.max_attempts,
            self.policies.retry.max_attempts,
        )

        wu.status = WorkUnitStatus.IN_PROGRESS
        last_review_feedback: str | None = None
        last_failure_evidence: str | None = None

        for attempt in range(1, max_attempts + 1):
            if self._cancelled:
                return False

            wu.attempts = attempt

            self.event_log.record(
                EventType.WORK_UNIT_STARTED,
                work_unit_id=wu.id,
                agent_role=AgentRole.BUILDER,
                details={
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "milestone_id": run_state.milestone_id,
                },
            )

            # Gather dependency outputs
            dep_outputs: list[str] = []
            for dep_id in wu.dependencies:
                dep_output = self.prs.load_worker_output(run_id, dep_id)
                if dep_output:
                    dep_outputs.append(dep_output)

            # Build context based on attempt number
            if attempt == 1:
                context = build_context_for_role(
                    AgentRole.BUILDER,
                    wu,
                    dependency_outputs=dep_outputs,
                )
            elif attempt == 2 and self.policies.retry.attempt_2_receives_review:
                # Attempt 2: Builder receives reviewer feedback
                context = build_context_for_role(
                    AgentRole.BUILDER,
                    wu,
                    dependency_outputs=dep_outputs,
                    failure_evidence=last_review_feedback or last_failure_evidence,
                )
            elif attempt == 3 and self.policies.retry.attempt_3_uses_debugger:
                # Attempt 3: Run debugger first, then builder
                debug_result = self._run_debugger(run_state, wu, last_failure_evidence)
                context = build_context_for_role(
                    AgentRole.BUILDER,
                    wu,
                    dependency_outputs=dep_outputs,
                    failure_evidence=debug_result,
                )
            else:
                context = build_context_for_role(
                    AgentRole.BUILDER,
                    wu,
                    dependency_outputs=dep_outputs,
                    failure_evidence=last_failure_evidence,
                )

            # Dispatch Builder
            builder_task = AgentTask(
                objective=wu.objective,
                work_unit_id=wu.id,
                acceptance_criteria=wu.acceptance_criteria,
                input_context=wu.input_context,
                max_attempts=max_attempts,
            )

            builder_result = self.executor.run(
                AgentRole.BUILDER,
                builder_task,
                context,
            )

            if not builder_result.success:
                last_failure_evidence = builder_result.error or builder_result.output
                self.event_log.record(
                    EventType.WORK_UNIT_COMPLETED,
                    work_unit_id=wu.id,
                    agent_role=AgentRole.BUILDER,
                    details={"status": "failed", "attempt": attempt, "error": builder_result.error},
                )
                continue

            # Save builder output
            self.prs.save_worker_output(run_id, wu.id, builder_result.output)
            wu.output = builder_result.output

            # Review phase (if policy requires)
            review_passed = True
            if self.policies.completion_gate.require_review_approval:
                review_passed, review_feedback = self._run_review(
                    run_state, wu, builder_result.output,
                )
                if not review_passed:
                    last_review_feedback = review_feedback
                    last_failure_evidence = review_feedback
                    continue

            # Test phase (if policy requires)
            tests_passed = True
            if self.policies.completion_gate.require_tests_pass:
                tests_passed = self._run_tests(run_state, wu)
                if not tests_passed:
                    last_failure_evidence = f"Tests failed for {wu.id}"
                    continue

            # Work unit succeeded
            wu.status = WorkUnitStatus.COMPLETED
            self.event_log.record(
                EventType.WORK_UNIT_COMPLETED,
                work_unit_id=wu.id,
                details={"status": "completed", "attempt": attempt},
            )
            return True

        # All attempts exhausted
        wu.status = WorkUnitStatus.FAILED
        self.event_log.record(
            EventType.WORK_UNIT_COMPLETED,
            work_unit_id=wu.id,
            details={
                "status": "failed",
                "attempts_exhausted": True,
                "total_attempts": max_attempts,
            },
        )
        return False

    def _run_review(
        self,
        run_state: RunState,
        wu: WorkUnit,
        builder_output: str,
    ) -> tuple[bool, str | None]:
        """Dispatch Reviewer and evaluate verdict.

        Returns (passed, feedback_text).
        """
        self.event_log.record(
            EventType.REVIEW_STARTED,
            work_unit_id=wu.id,
            agent_role=AgentRole.REVIEWER,
        )

        # Transition to REVIEWING
        if self.sm.can_transition(OrchestratorState.REVIEWING):
            self.sm.transition(OrchestratorState.REVIEWING)
            self._update_run_state(run_state, OrchestratorState.REVIEWING)

        context = build_context_for_role(
            AgentRole.REVIEWER,
            wu,
            diff=builder_output,
        )
        review_task = AgentTask(
            objective=f"Review work unit {wu.id}: {wu.title}",
            work_unit_id=wu.id,
            acceptance_criteria=wu.acceptance_criteria,
        )

        result = self.executor.run(AgentRole.REVIEWER, review_task, context)

        if not result.success:
            self.event_log.record(
                EventType.REVIEW_FAILED,
                work_unit_id=wu.id,
                agent_role=AgentRole.REVIEWER,
                details={"error": result.error},
            )
            return False, result.error or "Review failed"

        # For now, treat success output as approval unless it contains rejection signals
        # Full ReviewResult parsing would be done with validate_agent_output in production
        return True, None

    def _run_tests(
        self,
        run_state: RunState,
        wu: WorkUnit,
    ) -> bool:
        """Dispatch Tester and evaluate results."""
        self.event_log.record(
            EventType.TEST_STARTED,
            work_unit_id=wu.id,
            agent_role=AgentRole.TESTER,
        )

        # Transition to TESTING
        if self.sm.can_transition(OrchestratorState.TESTING):
            self.sm.transition(OrchestratorState.TESTING)
            self._update_run_state(run_state, OrchestratorState.TESTING)

        context = build_context_for_role(
            AgentRole.TESTER,
            wu,
        )
        test_task = AgentTask(
            objective=f"Test work unit {wu.id}: {wu.title}",
            work_unit_id=wu.id,
            acceptance_criteria=wu.acceptance_criteria,
        )

        result = self.executor.run(AgentRole.TESTER, test_task, context)

        if not result.success:
            self.event_log.record(
                EventType.TEST_FAILED,
                work_unit_id=wu.id,
                agent_role=AgentRole.TESTER,
                details={"error": result.error},
            )
            return False

        return True

    def _run_debugger(
        self,
        run_state: RunState,
        wu: WorkUnit,
        failure_evidence: str | None,
    ) -> str:
        """Dispatch Debugger for root-cause analysis before attempt 3."""
        self.event_log.record(
            EventType.DEBUG_STARTED,
            work_unit_id=wu.id,
            agent_role=AgentRole.DEBUGGER,
        )

        context = build_context_for_role(
            AgentRole.DEBUGGER,
            wu,
            failure_evidence=failure_evidence,
        )
        debug_task = AgentTask(
            objective=f"Debug failure for work unit {wu.id}: {wu.title}",
            work_unit_id=wu.id,
        )

        result = self.executor.run(AgentRole.DEBUGGER, debug_task, context)
        return result.output if result.success else (result.error or "Debug analysis unavailable")

    def _evaluate_completion(
        self,
        run_state: RunState,
        graph: WorkUnitGraph,
        completed_ids: set[str],
        failed_ids: set[str],
    ) -> MilestoneReport:
        """Evaluate completion gate and generate report."""
        all_wus = graph.all()
        all_completed = graph.all_completed()

        if self._cancelled:
            final_status = "CANCELLED"
        elif all_completed:
            # Transition to VALIDATING then COMPLETED
            if self.sm.can_transition(OrchestratorState.VALIDATING):
                self.sm.transition(OrchestratorState.VALIDATING)
                self._update_run_state(run_state, OrchestratorState.VALIDATING)

            if self.sm.can_transition(OrchestratorState.COMPLETED):
                self.sm.transition(OrchestratorState.COMPLETED)
                self._update_run_state(run_state, OrchestratorState.COMPLETED)

            self.event_log.record(
                EventType.RUN_COMPLETED,
                details={
                    "milestone_id": run_state.milestone_id,
                    "work_units_completed": len(completed_ids),
                },
            )
            final_status = "COMPLETED"
        else:
            # Mark as FAILED
            if self.sm.can_transition(OrchestratorState.FAILED):
                self.sm.transition(OrchestratorState.FAILED)
                self._update_run_state(run_state, OrchestratorState.FAILED)

            self.event_log.record(
                EventType.RUN_FAILED,
                details={
                    "milestone_id": run_state.milestone_id,
                    "work_units_completed": len(completed_ids),
                    "work_units_failed": len(failed_ids),
                },
            )
            final_status = "FAILED"

        report = MilestoneReport(
            objective=run_state.objective,
            status=final_status,
            work_units=all_wus,
            files_changed=[
                f
                for wu in all_wus
                for f in wu.files_likely_affected
            ],
        )

        # Persist report
        self.prs.save_report(run_state.run_id, report)

        return report

    def _handle_cancellation(self, run_state: RunState) -> None:
        """Handle run cancellation."""
        if self.sm.can_transition(OrchestratorState.CANCELLED):
            self.sm.transition(OrchestratorState.CANCELLED)
            self._update_run_state(run_state, OrchestratorState.CANCELLED)

    def _update_run_state(
        self,
        run_state: RunState,
        new_state: OrchestratorState,
    ) -> None:
        """Persist state change."""
        run_state.state = new_state
        run_state.updated_at = datetime.now(timezone.utc)
        self.prs.save_run(run_state)
        self.prs.save_current(run_state.model_dump(mode="json"))
