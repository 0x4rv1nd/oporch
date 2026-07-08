from pathlib import Path

import pytest

from oporch.constants import AgentRole, OrchestratorState
from oporch.executor import FakeAgentExecutor
from oporch.models import AgentResult, AgentTask, PlanResult
from oporch.orchestrator import HeadOrchestrator, _build_planner_prompt
from oporch.run_state import PersistentRunState
from oporch.validate import validate_planner_output


class TestBuildPlannerPrompt:
    def test_replaces_placeholders(self):
        from oporch.models import PlannerContextPack
        ctx = PlannerContextPack(
            milestone_id="M1",
            milestone_objective="Test objective",
        )
        template = "Milestone: {milestone_id}\nObjective: {objective}"
        result = _build_planner_prompt(template, ctx)
        assert "Milestone: M1" in result
        assert "Objective: Test objective" in result

    def test_handles_empty_constraints(self):
        from oporch.models import PlannerContextPack
        ctx = PlannerContextPack(milestone_id="M1", milestone_objective="test")
        result = _build_planner_prompt("{architecture_constraints}", ctx)
        assert "None" in result


class TestHeadOrchestratorPlanMilestone:
    def test_plan_milestone_returns_valid_plan(self, tmp_path):
        original_cwd = Path.cwd()
        try:
            (tmp_path / "PRD.md").write_text("## M1 Objective\nDo the thing", encoding="utf-8")
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "main.py").write_text("# main", encoding="utf-8")
            (tmp_path / "tests").mkdir()
            (tmp_path / "tests" / "test_main.py").write_text("# test", encoding="utf-8")
            (tmp_path / "README.md").write_text("# Project", encoding="utf-8")

            import os
            os.chdir(str(tmp_path))

            executor = FakeAgentExecutor()
            plan_json = (
                '{"milestone_id": "M1", "objective": "Do the thing", '
                '"work_units": [{"id": "WU-001", "title": "Setup", '
                '"objective": "Set up project", "dependencies": [], '
                '"assigned_role": "builder", "acceptance_criteria": ["Works"]}], '
                '"assumptions": ["Repo is initialized"]}'
            )
            executor.set_next_result(
                AgentResult(role=AgentRole.PLANNER, success=True, output=plan_json)
            )

            orchestrator = HeadOrchestrator(executor=executor)
            validation, plan_or_question = orchestrator.plan_milestone("M1", "Do the thing")

            assert validation.status in ("valid", "repaired")
            assert plan_or_question is not None
            assert isinstance(plan_or_question, PlanResult)
            assert len(plan_or_question.work_units) == 1
            assert plan_or_question.work_units[0].id == "WU-001"
            assert "Repo is initialized" in plan_or_question.assumptions
        finally:
            import os
            os.chdir(str(original_cwd))

    def test_plan_milestone_returns_question(self, tmp_path):
        original_cwd = Path.cwd()
        try:
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "main.py").write_text("# main", encoding="utf-8")
            import os
            os.chdir(str(tmp_path))

            executor = FakeAgentExecutor()
            question_json = (
                '{"type": "QUESTION", "question_id": "Q1", '
                '"question": "Which approach?", "why_needed": "Need to decide", '
                '"blocking": true, "options": ["A", "B"]}'
            )
            executor.set_next_result(
                AgentResult(role=AgentRole.PLANNER, success=True, output=question_json)
            )

            orchestrator = HeadOrchestrator(executor=executor)
            validation, plan_or_question = orchestrator.plan_milestone("M1", "Test")

            assert validation.status == "valid"
            from oporch.models import WorkerQuestion
            assert isinstance(plan_or_question, WorkerQuestion)
            assert plan_or_question.question_id == "Q1"
        finally:
            import os
            os.chdir(str(original_cwd))

    def test_plan_milestone_fails_on_bad_output(self, tmp_path):
        original_cwd = Path.cwd()
        try:
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "main.py").write_text("# main", encoding="utf-8")
            import os
            os.chdir(str(tmp_path))

            executor = FakeAgentExecutor()
            executor.set_next_result(
                AgentResult(role=AgentRole.PLANNER, success=True, output="garbage output")
            )

            orchestrator = HeadOrchestrator(executor=executor)
            validation, plan_or_question = orchestrator.plan_milestone("M1", "Test")

            assert validation.status == "failed"
            assert plan_or_question is None
        finally:
            import os
            os.chdir(str(original_cwd))

    def test_plan_milestone_saves_plan(self, tmp_path):
        original_cwd = Path.cwd()
        try:
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "main.py").write_text("# main", encoding="utf-8")
            import os
            os.chdir(str(tmp_path))

            executor = FakeAgentExecutor()
            plan_json = (
                '{"milestone_id": "M1", "objective": "Do the thing", '
                '"work_units": [{"id": "WU-001", "title": "Setup", '
                '"objective": "Set up", "dependencies": [], '
                '"assigned_role": "builder", "acceptance_criteria": ["Works"]}], '
                '"assumptions": []}'
            )
            executor.set_next_result(
                AgentResult(role=AgentRole.PLANNER, success=True, output=plan_json)
            )

            prs = PersistentRunState()
            orchestrator = HeadOrchestrator(executor=executor, run_state=prs)
            validation, plan = orchestrator.plan_milestone("M1", "Do the thing")

            current = prs.load_current()
            assert current is not None
            assert current.milestone_id == "M1"
            assert current.state == OrchestratorState.AWAITING_PLAN_APPROVAL

            saved_plan = prs.load_plan(current.run_id)
            assert saved_plan is not None
            assert saved_plan["milestone_id"] == "M1"
        finally:
            import os
            os.chdir(str(original_cwd))

    def test_plan_milestone_tracks_state_transitions(self, tmp_path):
        original_cwd = Path.cwd()
        try:
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "main.py").write_text("# main", encoding="utf-8")
            import os
            os.chdir(str(tmp_path))

            executor = FakeAgentExecutor()
            plan_json = (
                '{"milestone_id": "M1", "objective": "test", '
                '"work_units": [], "assumptions": []}'
            )
            executor.set_next_result(
                AgentResult(role=AgentRole.PLANNER, success=True, output=plan_json)
            )

            orchestrator = HeadOrchestrator(executor=executor)
            orchestrator.plan_milestone("M1", "test")

            history = orchestrator.sm.history
            states = [(h["from"], h["to"]) for h in history]
            assert ("IDLE", "ANALYZING") in states
            assert ("ANALYZING", "PLANNING") in states
            assert ("PLANNING", "AWAITING_PLAN_APPROVAL") in states
        finally:
            import os
            os.chdir(str(original_cwd))


class TestHeadOrchestratorRunMilestone:
    def test_run_milestone_success(self, tmp_path):
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(str(tmp_path))

            prs = PersistentRunState()
            executor = FakeAgentExecutor()
            orchestrator = HeadOrchestrator(executor=executor, run_state=prs)

            # Setup a plan
            from oporch.orchestrator import create_run_state
            run = create_run_state("M1", "Do the thing")
            run.state = OrchestratorState.AWAITING_PLAN_APPROVAL
            prs.save_run(run)
            prs.save_current(run)

            # Save a work unit
            from oporch.models import WorkUnit
            wu = WorkUnit(
                id="WU-001",
                title="WU 1",
                objective="Obj 1",
                dependencies=[],
                acceptance_criteria=["Works"],
            )
            prs.save_work_units(run.run_id, [wu])

            report = orchestrator.run_milestone()

            assert report.status == "COMPLETED"
            assert orchestrator.sm.current == OrchestratorState.COMPLETED

            # Verify run state updated on disk
            updated_run = prs.load_run(run.run_id)
            assert updated_run.state == OrchestratorState.COMPLETED
        finally:
            import os
            os.chdir(str(original_cwd))

    def test_resume_run_success(self, tmp_path):
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(str(tmp_path))

            prs = PersistentRunState()
            executor = FakeAgentExecutor()
            orchestrator = HeadOrchestrator(executor=executor, run_state=prs)

            # Setup run that is in AWAITING_PLAN_APPROVAL state
            from oporch.orchestrator import create_run_state
            run = create_run_state("M1", "Do the thing")
            run.state = OrchestratorState.AWAITING_PLAN_APPROVAL
            prs.save_run(run)
            prs.save_current(run)

            # Save work units, one complete, one pending
            from oporch.models import WorkUnit
            from oporch.constants import WorkUnitStatus
            wu1 = WorkUnit(
                id="WU-001",
                title="WU 1",
                objective="Obj 1",
                status=WorkUnitStatus.COMPLETED,
                dependencies=[],
                acceptance_criteria=["Works"],
            )
            wu2 = WorkUnit(
                id="WU-002",
                title="WU 2",
                objective="Obj 2",
                dependencies=["WU-001"],
                acceptance_criteria=["Works"],
            )
            prs.save_work_units(run.run_id, [wu1, wu2])

            report = orchestrator.resume_run()

            assert report.status == "COMPLETED"
            
            # WU-001 should not be rerun, WU-002 should run
            # FakeAgentExecutor records calls. Let's filter for builders
            builders = [c for c in executor.calls if c[0] == AgentRole.BUILDER]
            assert len(builders) == 1
            assert builders[0][1].work_unit_id == "WU-002"
        finally:
            import os
            os.chdir(str(original_cwd))

