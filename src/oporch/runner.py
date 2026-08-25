"""Milestone execution engine (v2 parallel).

Executes an approved plan by dispatching work units in parallel waves
through the Builder → Reviewer → Tester pipeline. Concurrency per role is
bounded by ``max_workers`` from the run's team roster (semaphore-based).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
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
from .executor import AgentExecutor, call_executor_async
from .models import (
    AgentResult,
    AgentTask,
    ContextPack,
    MilestoneReport,
    PoliciesConfig,
    RunState,
    TeamRoster,
    WorkUnit,
)
from .run_state import PersistentRunState
from .state_machine import StateMachine
from .work_unit import WorkUnitGraph

logger = logging.getLogger(__name__)


class RunnerError(Exception):
    pass


def normalize_role(role: AgentRole | str) -> str:
    return role.value if hasattr(role, "value") else str(role)


class ParallelDispatcher:
    """Runs ready work units concurrently, bounded per-role by semaphores."""

    def __init__(
        self,
        roster: TeamRoster | dict[str, int] | None,
        executor: AgentExecutor,
        db: Any | None = None,
    ) -> None:
        self.executor = executor
        self.db = db
        workers: dict[str, int] = {}
        if isinstance(roster, TeamRoster):
            workers = {r.key: max(1, r.max_workers) for r in roster.roles}
        elif isinstance(roster, dict):
            workers = {k: max(1, v) for k, v in roster.items()}
        self.max_workers = workers
        self.semaphores: dict[str, asyncio.Semaphore] = {
            k: asyncio.Semaphore(v) for k, v in workers.items()
        }
        self.active: set[str] = set()  # wu ids currently running

    def semaphore_for(self, role_key: str) -> asyncio.Semaphore:
        if role_key not in self.semaphores:
            # Unknown roster key: conservative single worker.
            self.semaphores[role_key] = asyncio.Semaphore(
                self.max_workers.get(role_key, 1)
            )
        return self.semaphores[role_key]

    def resize(self, role_key: str, new_max_workers: int) -> None:
        """Widen/narrow a role's concurrency budget (used by auto-scaling)."""
        new_max_workers = max(1, int(new_max_workers))
        self.max_workers[role_key] = new_max_workers
        self.semaphores[role_key] = asyncio.Semaphore(new_max_workers)

    async def run_ready_wave(
        self,
        runner: MilestoneRunner,
        run_state: RunState,
        graph: WorkUnitGraph,
        completed_ids: set[str],
        ready: list[WorkUnit],
    ) -> list[bool]:
        tasks = [
            self._run_one(runner, run_state, graph, completed_ids, wu)
            for wu in ready
        ]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    async def _run_one(
        self,
        runner: MilestoneRunner,
        run_state: RunState,
        graph: WorkUnitGraph,
        completed_ids: set[str],
        wu: WorkUnit,
    ) -> bool:
        role_key = normalize_role(wu.assigned_role) if wu.assigned_role else "builder"
        async with self.semaphore_for(role_key):
            self.active.add(wu.id)
            try:
                ok = await runner._execute_work_unit(
                    run_state, wu, graph, completed_ids,
                )
                if self.db is not None:
                    self.db.record_wu_result(
                        wu.id,
                        run_id=run_state.run_id,
                        status=wu.status.value,
                        attempt=wu.attempts,
                        result_summary=(wu.output or "")[:2000],
                    )
                return ok
            finally:
                self.active.discard(wu.id)

    async def run_pending_drain(
        self,
        runner: MilestoneRunner,
        run_state: RunState,
        graph: WorkUnitGraph,
        completed_ids: set[str],
        failed_ids: set[str],
        scaler=None,
    ) -> None:
        """Wave loop: repeat until the DAG drains or nothing is runnable."""
        while not graph.all_completed():
            if runner.cancelled:
                return
            await runner.wait_if_paused()

            ready = [wu for wu in graph.get_ready(completed_ids)]
            if not ready:
                return

            results = await self.run_ready_wave(
                runner, run_state, graph, completed_ids, ready,
            )
            for wu, ok in zip(ready, results):
                if ok:
                    completed_ids.add(wu.id)
                    # §8: phase-boundary roster adjustment check.
                    if scaler is not None and wu.phase is not None:
                        phase_done = all(
                            g.status == WorkUnitStatus.COMPLETED
                            for g in graph.all()
                            if g.phase == wu.phase
                        )
                        if phase_done:
                            scaler.on_phase_complete(wu.phase)
                else:
                    failed_ids.add(wu.id)
            runner.prs.save_work_units(run_state.run_id, graph.all())


class MilestoneRunner:
    """Executes an approved plan through the agent pipeline (parallel waves).

    Per work unit: Builder (the WU's assigned roster role) implements,
    Reviewer adversarially reviews (if policy requires), Tester validates.
    Retry ladder: attempt 2 receives review feedback, attempt 3 gets a
    debugger analysis first.
    """

    def __init__(
        self,
        executor: AgentExecutor,
        prs: PersistentRunState,
        policies: PoliciesConfig,
        state_machine: StateMachine,
        event_log: EventLog,
        dispatcher: ParallelDispatcher | None = None,
        db: Any | None = None,
    ) -> None:
        self.executor = executor
        self.prs = prs
        self.policies = policies
        self.sm = state_machine
        self.event_log = event_log
        self.dispatcher = dispatcher
        self._db = db
        self._git_manager = None
        self._cancelled = False
        self._sm_lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def db(self):
        if self._db is None:
            from .db import OporchDB

            self._db = OporchDB()
        return self._db

    def cancel(self) -> None:
        """Request cancellation of the current run."""
        self._cancelled = True

    async def wait_if_paused(self) -> None:
        """Cooperative pause: poll the control table until released."""
        while not self._cancelled:
            value = self.db.get_control("pause")
            if value != "1":
                return
            await asyncio.sleep(0.25)

    # ------------------------------------------------------------------
    # public entry points
    # ------------------------------------------------------------------
    def run_milestone(self, run_state: RunState) -> MilestoneReport:
        """Execute all work units in the approved plan (blocking)."""
        return asyncio.run(self.run_milestone_async(run_state))

    async def run_milestone_async(self, run_state: RunState) -> MilestoneReport:
        run_id = run_state.run_id
        work_units = self.prs.load_work_units(run_id)

        if not work_units:
            raise RunnerError(f"No work units found for run {run_id}")

        graph = WorkUnitGraph(work_units)
        graph.validate()

        # Transition to EXECUTING
        self._guarded_transition(
            run_state, OrchestratorState.EXECUTING,
        )

        completed_ids: set[str] = set()
        failed_ids: set[str] = set()

        # Resume support: previously completed work units count as done.
        for wu in graph.all():
            if wu.status == WorkUnitStatus.COMPLETED:
                completed_ids.add(wu.id)

        dispatcher = self.dispatcher
        if dispatcher is None:
            dispatcher = self._build_dispatcher(run_id)

        scaler = self._build_scaler(run_id, dispatcher, graph)

        await dispatcher.run_pending_drain(
            self, run_state, graph, completed_ids, failed_ids,
            scaler=scaler,
        )

        if self._cancelled:
            self._handle_cancellation(run_state)

        # Anything left unfinished because its dependencies failed is BLOCKED.
        for wu in graph.all():
            if wu.status in (
                WorkUnitStatus.COMPLETED,
                WorkUnitStatus.FAILED,
                WorkUnitStatus.SKIPPED,
            ):
                continue
            unmet = [d for d in wu.dependencies if d not in completed_ids]
            if unmet and not self._cancelled:
                wu.status = WorkUnitStatus.BLOCKED
                wu.blockers = unmet
                failed_ids.add(wu.id)

        self.prs.save_work_units(run_id, graph.all())

        report = await self._evaluate_completion(
            run_state, graph, completed_ids, failed_ids,
        )
        return report

    def _build_dispatcher(self, run_id: str) -> ParallelDispatcher:
        roster_dict: dict[str, int] = {}
        try:
            rows = self.db.get_roster(run_id)
            roster_dict = {
                r["role_key"]: int(r["max_workers"] or 1)
                for r in rows
                if r["role_key"] not in ("reviewer", "tester", "debugger", "supervisor")
            }
        except Exception:
            roster_dict = {}
        if not roster_dict:
            roster_dict = {"builder": 3}
        return ParallelDispatcher(roster_dict, self.executor, db=self.db)

    def _build_scaler(self, run_id: str, dispatcher: ParallelDispatcher,
                      graph: WorkUnitGraph):
        """Phase-boundary roster scaler (only when policy enables it)."""
        if not self.policies.roster_auto_scale.enabled:
            return None
        try:
            from .roster_scaling import RosterScaler

            phases = {
                wu.phase for wu in graph.all() if wu.phase is not None
            }
            phase_count = max(phases) if phases else 1
            return RosterScaler(
                self.db,
                run_id,
                phase_count=phase_count,
                policies=self.policies.roster_auto_scale,
                dispatcher=dispatcher,
                event_log=self.event_log,
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # per-WU pipeline
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # agent memory (v2 §4)
    # ------------------------------------------------------------------
    def _recall_memory(self, wu: WorkUnit, role_key: str) -> tuple[list[str], list[int]]:
        """Top-K relevant memories for this role + WU domain keywords."""
        try:
            from pathlib import Path as _P

            project = str(_P.cwd())
            query = f"{wu.title} {wu.objective} {' '.join(wu.acceptance_criteria)}"
            hits = self.db.recall(project, role_key=role_key, query=query, limit=5)
        except Exception:
            return [], []
        return [h["content"] for h in hits], [h["id"] for h in hits]

    def _boost_memories(self, ids: list[int]) -> None:
        if not ids:
            return
        try:
            self.db.boost_memory(ids)
        except Exception:
            pass

    def _record_failure_pattern(self, run_id: str, wu: WorkUnit, reason: str) -> None:
        try:
            from pathlib import Path as _P

            project = str(_P.cwd())
            role_key = wu.assigned_role or "builder"
            self.db.remember(
                project,
                role_key,
                "failure_pattern",
                f"[{run_id}/{wu.id}] {reason}"[:500],
                source_run_id=run_id,
            )
        except Exception:
            pass

    @staticmethod
    def _model_for(role_key: str) -> str | None:
        try:
            return cfg.resolve_model(role_key)
        except Exception:
            return None

    async def _execute_work_unit(
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
        memory_texts, memory_ids = self._recall_memory(
            wu, wu.assigned_role or AgentRole.BUILDER.value,
        )

        for attempt in range(1, max_attempts + 1):
            if self._cancelled:
                return False

            wu.attempts = attempt

            self.event_log.record(
                EventType.WORK_UNIT_STARTED,
                work_unit_id=wu.id,
                agent_role=wu.assigned_role or AgentRole.BUILDER.value,
                details={
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "milestone_id": run_state.milestone_id,
                },
            )
            self.db.record_wu_result(
                wu.id, run_id=run_id, status="IN_PROGRESS",
                attempt=attempt, started=True,
            )

            # Gather dependency outputs
            dep_outputs: list[str] = []
            for dep_id in wu.dependencies:
                dep_output = self.prs.load_worker_output(run_id, dep_id)
                if dep_output:
                    dep_outputs.append(dep_outputs_text(dep_id, dep_output))

            builder_role = wu.assigned_role or AgentRole.BUILDER.value

            # Build context based on attempt number
            if attempt == 2 and self.policies.retry.attempt_2_receives_review:
                context = build_context_for_role(
                    builder_role,
                    wu,
                    dependency_outputs=dep_outputs,
                    failure_evidence=last_review_feedback or last_failure_evidence,
                    project_memory=memory_texts,
                )
            elif attempt >= 3 and self.policies.retry.attempt_3_uses_debugger:
                debug_result = await self._run_debugger(
                    run_state, wu, last_failure_evidence,
                )
                context = build_context_for_role(
                    builder_role,
                    wu,
                    dependency_outputs=dep_outputs,
                    failure_evidence=debug_result,
                    project_memory=memory_texts,
                )
            else:
                context = build_context_for_role(
                    builder_role,
                    wu,
                    dependency_outputs=dep_outputs,
                    failure_evidence=last_failure_evidence,
                    project_memory=memory_texts,
                )

            builder_task = AgentTask(
                objective=wu.objective,
                work_unit_id=wu.id,
                acceptance_criteria=wu.acceptance_criteria,
                input_context=wu.input_context,
                max_attempts=max_attempts,
                working_dir=self._wu_working_dir(run_state.run_id, wu),
            )

            started = time.monotonic()
            builder_result: AgentResult = await call_executor_async(
                self.executor, builder_role, builder_task, context,
            )
            duration_ms = (time.monotonic() - started) * 1000.0

            if not builder_result.success:
                last_failure_evidence = builder_result.error or builder_result.output
                self._record_failure_pattern(
                    run_id, wu, last_failure_evidence or "builder failed",
                )
                self.event_log.record(
                    EventType.WORK_UNIT_COMPLETED,
                    work_unit_id=wu.id,
                    agent_role=builder_role,
                    details={
                        "status": "failed",
                        "attempt": attempt,
                        "error": builder_result.error,
                    },
                    level="error",
                    duration_ms=duration_ms,
                    model_used=self._model_for(builder_role),
                )
                continue

            # Save builder output
            self.prs.save_worker_output(run_id, wu.id, builder_result.output)
            wu.output = builder_result.output

            review_diff: str | None = builder_result.output
            if self.policies.completion_gate.require_supervisor_merge:
                commit_sha = await asyncio.to_thread(
                    self._commit_wu_changes, run_state.run_id, wu,
                )
                if commit_sha:
                    review_diff = await asyncio.to_thread(
                        self.git.diff_for_review, run_state.run_id, wu.id,
                    )

            # Review phase (if policy requires)
            review_passed = True
            if self.policies.completion_gate.require_review_approval:
                review_passed, review_feedback = await self._run_review(
                    run_state, wu, review_diff,
                )
                if not review_passed:
                    last_review_feedback = review_feedback
                    last_failure_evidence = review_feedback
                    continue

            # Test phase (if policy requires)
            tests_passed = True
            if self.policies.completion_gate.require_tests_pass:
                tests_passed = await self._run_tests(run_state, wu)
                if not tests_passed:
                    last_failure_evidence = f"Tests failed for {wu.id}"
                    continue

            # Supervisor merge gate (v2 §7): only the supervisor merges into
            # the per-run integration branch.
            if self.policies.completion_gate.require_supervisor_merge:
                merged = await self._supervisor_merge_gate(run_state, wu)
                if not merged:
                    continue

            # Work unit succeeded
            wu.status = WorkUnitStatus.COMPLETED
            self._boost_memories(memory_ids)
            self.event_log.record(
                EventType.WORK_UNIT_COMPLETED,
                work_unit_id=wu.id,
                details={"status": "completed", "attempt": attempt},
                duration_ms=duration_ms,
                model_used=self._model_for(builder_role),
            )
            return True

        # All attempts exhausted
        wu.status = WorkUnitStatus.FAILED
        self._record_failure_pattern(run_id, wu, "attempts exhausted")
        self.event_log.record(
            EventType.WORK_UNIT_COMPLETED,
            work_unit_id=wu.id,
            details={
                "status": "failed",
                "attempts_exhausted": True,
                "total_attempts": max_attempts,
            },
            level="error",
        )
        return False

    async def _run_review(
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
            agent_role=AgentRole.REVIEWER.value,
        )

        self._guarded_transition(run_state, OrchestratorState.REVIEWING)

        context = build_context_for_role(
            AgentRole.REVIEWER.value,
            wu,
            diff=builder_output,
        )
        review_task = AgentTask(
            objective=f"Review work unit {wu.id}: {wu.title}",
            work_unit_id=wu.id,
            acceptance_criteria=wu.acceptance_criteria,
        )

        result = await call_executor_async(
            self.executor, AgentRole.REVIEWER.value, review_task, context,
        )

        if not result.success:
            self.event_log.record(
                EventType.REVIEW_FAILED,
                work_unit_id=wu.id,
                agent_role=AgentRole.REVIEWER.value,
                details={"error": result.error},
                level="warn",
            )
            return False, result.error or "Review failed"

        # For now, treat success output as approval unless it contains rejection signals
        # Full ReviewResult parsing would be done with validate_agent_output in production
        return True, None

    async def _run_tests(
        self,
        run_state: RunState,
        wu: WorkUnit,
    ) -> bool:
        """Dispatch Tester and evaluate results."""
        self.event_log.record(
            EventType.TEST_STARTED,
            work_unit_id=wu.id,
            agent_role=AgentRole.TESTER.value,
        )

        self._guarded_transition(run_state, OrchestratorState.TESTING)

        context = build_context_for_role(AgentRole.TESTER.value, wu)
        test_task = AgentTask(
            objective=f"Test work unit {wu.id}: {wu.title}",
            work_unit_id=wu.id,
            acceptance_criteria=wu.acceptance_criteria,
        )

        result = await call_executor_async(
            self.executor, AgentRole.TESTER.value, test_task, context,
        )

        if not result.success:
            self.event_log.record(
                EventType.TEST_FAILED,
                work_unit_id=wu.id,
                agent_role=AgentRole.TESTER.value,
                details={"error": result.error},
                level="warn",
            )
            return False

        return True

    # ------------------------------------------------------------------
    # supervisor merge gate (§7)
    # ------------------------------------------------------------------
    @property
    def git(self):
        if self._git_manager is None:
            from .git_manager import GitManager

            self._git_manager = GitManager()
        return self._git_manager

    def _wu_working_dir(self, run_id: str, wu: WorkUnit) -> str | None:
        """Per-WU worktree path when the merge gate is enabled, else None."""
        if not self.policies.completion_gate.require_supervisor_merge:
            return None
        try:
            path = self.git.create_worktree(run_id, wu.id)
        except Exception as exc:
            from .git_manager import GitManagerError

            if isinstance(exc, GitManagerError) and "already exists" in str(exc):
                return str(self.git.worktree_path(run_id, wu.id))
            self.event_log.record(
                EventType.USER_ESCALATION,
                work_unit_id=wu.id,
                details={"reason": "worktree creation failed", "error": str(exc)},
                level="warn",
            )
            return None
        return str(path)

    def _commit_wu_changes(self, run_id: str, wu: WorkUnit) -> str | None:
        """Commit whatever the agent changed inside its worktree (if any)."""
        try:
            return self.git.commit_wu_result(
                run_id, wu.id, f"oporch {run_id} {wu.id}: {wu.title}",
            )
        except Exception as exc:
            self.event_log.record(
                EventType.USER_ESCALATION,
                work_unit_id=wu.id,
                details={"reason": "worktree commit failed", "error": str(exc)},
                level="warn",
            )
            return None

    async def _supervisor_merge_gate(self, run_state: RunState, wu: WorkUnit) -> bool:
        """Merge a reviewed+tested WU into the run's integration branch.

        Returns True when the WU may proceed to COMPLETED. On conflict the
        WU is flagged MERGE_CONFLICT and optionally re-routed through the
        debugger once (policy ``merge_conflict.route``). In STRICT approval
        mode every merge waits for human approval via the control table.
        """
        from .git_manager import GitManagerError, MergeConflictError
        from .constants import ApprovalMode

        run_id = run_state.run_id

        # STRICT mode: park for explicit human approval.
        if (
            self.policies.approval_mode == ApprovalMode.STRICT.value
            and self.policies.security.strict_disables_auto_merge
        ):
            key = f"merge_pending:{run_id}:{wu.id}"
            self.db.set_control(key, "1")
            self.event_log.record(
                EventType.USER_ESCALATION,
                work_unit_id=wu.id,
                agent_role=AgentRole.SUPERVISOR.value,
                details={"reason": "strict mode merge approval", "control_key": key},
                level="warn",
            )
            await self._await_control(key, {"0", "approved"})
            approved = self.db.get_control(key) == "approved"
            if not approved:
                wu.status = WorkUnitStatus.BLOCKED
                return False
            self.db.set_control(key, "resolved")

        try:
            sha = await asyncio.to_thread(
                self.git.merge_wu_into_integration, run_id, wu.id,
            )
        except MergeConflictError as exc:
            wu.status = WorkUnitStatus.MERGE_CONFLICT
            self.event_log.record(
                EventType.MERGE_CONFLICT_EVENT,
                work_unit_id=wu.id,
                agent_role=AgentRole.SUPERVISOR.value,
                details={
                    "conflicts": exc.conflicts,
                    "route": self.policies.merge_conflict.route,
                },
                level="warn",
            )
            if self.policies.merge_conflict.route == "debugger":
                await self._run_debugger(run_state, wu, str(exc.conflicts))
            return False
        except GitManagerError as exc:
            last = f"Supervisor merge failed: {exc}"
            self.event_log.record(
                EventType.MERGE_CONFLICT_EVENT,
                work_unit_id=wu.id,
                agent_role=AgentRole.SUPERVISOR.value,
                details={"error": last},
                level="error",
            )
            return False

        self.db.record_wu_result(
            wu.id, run_id=run_id, status="COMPLETED", result_summary=sha,
        )
        self.event_log.record(
            EventType.WU_MERGED,
            work_unit_id=wu.id,
            agent_role=AgentRole.SUPERVISOR.value,
            details={"merge_commit": sha},
        )
        return True

    async def _await_control(self, key: str, resolved_values: set[str]) -> None:
        while not self._cancelled:
            value = self.db.get_control(key)
            if value in resolved_values:
                return
            await asyncio.sleep(0.25)

    async def _run_debugger(
        self,
        run_state: RunState,
        wu: WorkUnit,
        failure_evidence: str | None,
    ) -> str:
        """Dispatch Debugger for root-cause analysis before a retry."""
        self.event_log.record(
            EventType.DEBUG_STARTED,
            work_unit_id=wu.id,
            agent_role=AgentRole.DEBUGGER.value,
        )

        context = build_context_for_role(
            AgentRole.DEBUGGER.value,
            wu,
            failure_evidence=failure_evidence,
        )
        debug_task = AgentTask(
            objective=f"Debug failure for work unit {wu.id}: {wu.title}",
            work_unit_id=wu.id,
        )

        result = await call_executor_async(
            self.executor, AgentRole.DEBUGGER.value, debug_task, context,
        )
        return result.output if result.success else (
            result.error or "Debug analysis unavailable"
        )

    # ------------------------------------------------------------------
    # completion / lifecycle
    # ------------------------------------------------------------------
    async def _evaluate_completion(
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
            self._guarded_transition(run_state, OrchestratorState.VALIDATING)
            self._guarded_transition(run_state, OrchestratorState.COMPLETED)

            self.event_log.record(
                EventType.RUN_COMPLETED,
                details={
                    "milestone_id": run_state.milestone_id,
                    "work_units_completed": len(completed_ids),
                },
            )
            final_status = "COMPLETED"
        else:
            self._guarded_transition(run_state, OrchestratorState.FAILED)

            self.event_log.record(
                EventType.RUN_FAILED,
                details={
                    "milestone_id": run_state.milestone_id,
                    "work_units_completed": len(completed_ids),
                    "work_units_failed": len(failed_ids),
                },
                level="error",
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

        self.prs.save_report(run_state.run_id, report)
        return report

    def _handle_cancellation(self, run_state: RunState) -> None:
        self._guarded_transition(run_state, OrchestratorState.CANCELLED)

    def _guarded_transition(
        self,
        run_state: RunState,
        target: OrchestratorState,
    ) -> None:
        """Best-effort run-level state transition.

        Concurrent waves race for intermediate states (REVIEWING/TESTING),
        so transitions are serialized and skipped if another wave moved the
        machine on already. The final VALIDATING→COMPLETED/FAILED sequence
        is what matters for correctness.
        """
        with self._sm_lock:
            try:
                if self.sm.can_transition(target):
                    self.sm.transition(target)
            except Exception:
                return
            run_state.state = self.sm.current
            run_state.updated_at = datetime.now(timezone.utc)
            self.prs.save_run(run_state)
            self.prs.save_current(run_state.model_dump(mode="json"))
            try:
                self.db.set_run_state(run_state.run_id, run_state.state.value)
            except Exception:
                pass


def dep_outputs_text(dep_id: str, output: str) -> str:
    """Label dependency output text for downstream context packs."""
    head = (output or "").strip()
    if len(head) > 2000:
        head = head[:2000] + "\n... (truncated)"
    return f"[output of {dep_id}]\n{head}"
