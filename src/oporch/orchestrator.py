from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg
from .constants import AgentRole, EventType, OrchestratorState
from .event_log import EventLog
from .executor import AgentExecutor, FakeAgentExecutor
from .models import (
    AgentOutputResult,
    AgentResult,
    AgentTask,
    ContextPack,
    PlannerContextPack,
    PlanResult,
    RunState,
    WorkerQuestion,
)
from .run_state import PersistentRunState, create_run_state
from .state_machine import StateMachine
from .validate import validate_planner_output

CONTEXT_DIR = Path(".opencode-orchestrator") / "context"


class OrchestratorError(Exception):
    pass


def generate_repo_summary() -> str:
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    target = CONTEXT_DIR / "project_summary.md"

    if target.exists():
        return target.read_text(encoding="utf-8")

    parts: list[str] = []
    src = Path("src")
    if src.exists():
        py_files = sorted(src.rglob("*.py"))
        parts.append(f"Python source files: {len(py_files)}")
        for f in py_files[:50]:
            parts.append(f"- {f.relative_to('.')}")

    tests = Path("tests")
    if tests.exists():
        test_files = sorted(tests.rglob("*.py"))
        parts.append(f"Test files: {len(test_files)}")

    readme = Path("README.md")
    if readme.exists():
        parts.append("README.md present")

    prd = Path("PRD.md")
    if prd.exists():
        prd_text = prd.read_text(encoding="utf-8")
        parts.append("PRD.md sections found:")
        for line in prd_text.splitlines():
            if line.startswith("## "):
                parts.append(f"  {line.strip()}")

    summary = "\n".join(parts)
    target.write_text(summary, encoding="utf-8")
    return summary


def _load_planner_prompt() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "planner.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "You are a planner AI. Decompose the milestone into work units."


class HeadOrchestrator:
    def __init__(
        self,
        executor: AgentExecutor | None = None,
        run_state: PersistentRunState | None = None,
    ) -> None:
        self.executor = executor or FakeAgentExecutor()
        self.prs = run_state or PersistentRunState()
        self._event_log: EventLog | None = None
        self.sm = StateMachine()

    @property
    def event_log(self) -> EventLog:
        if self._event_log is None:
            raise RuntimeError("event_log not initialized; call plan_milestone first")
        return self._event_log

    def plan_milestone(
        self,
        milestone_id: str,
        objective: str = "",
    ) -> tuple[AgentOutputResult, PlanResult | WorkerQuestion | None]:
        run = create_run_state(milestone_id, objective)
        self.prs.save_current(run.model_dump(mode="json"))
        self.prs.save_run(run)
        self._event_log = EventLog(run.run_id)

        self.sm.transition(OrchestratorState.ANALYZING)
        self._update_run_state(run, OrchestratorState.ANALYZING)

        repo_summary = generate_repo_summary()

        self.sm.transition(OrchestratorState.PLANNING)
        self._update_run_state(run, OrchestratorState.PLANNING)

        planner_prompt = _load_planner_prompt()

        prd_sections: list[str] = []
        prd = Path("PRD.md")
        if prd.exists():
            prd_sections = [line.strip() for line in prd.read_text(encoding="utf-8").splitlines() if line.startswith("## ")]

        ctx = PlannerContextPack(
            milestone_id=milestone_id,
            milestone_objective=objective,
            relevant_prd_sections=prd_sections,
            repo_summary=repo_summary,
            architecture_constraints=[],
            prior_decisions=[],
            prior_milestone_summaries=[],
        )

        full_prompt = _build_planner_prompt(planner_prompt, ctx)

        task = AgentTask(
            objective=objective or f"Plan milestone {milestone_id}",
            raw_prompt=full_prompt,
            max_attempts=3,
        )

        result: AgentResult = self.executor.run(
            AgentRole.PLANNER,
            task,
            ContextPack(),
        )

        validation, parsed = validate_planner_output(result.output)

        self.sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        self._update_run_state(run, OrchestratorState.AWAITING_PLAN_APPROVAL)

        if validation.status == "failed":
            self.event_log.record(
                EventType.PLAN_CREATED,
                details={"status": "failed", "error": validation.error, "milestone_id": milestone_id},
            )
            return validation, None

        if parsed and "type" in parsed and parsed["type"] == "QUESTION":
            question = WorkerQuestion(**parsed)
            self.event_log.record(
                EventType.WORKER_QUESTION,
                details={"question_id": question.question_id, "question": question.question},
            )
            return validation, question

        plan_result = PlanResult(**parsed)
        self.prs.save_plan(run.run_id, parsed)
        self.prs.save_work_units(run.run_id, plan_result.work_units)

        self.event_log.record(
            EventType.PLAN_CREATED,
            details={
                "status": "ok",
                "milestone_id": milestone_id,
                "work_units": len(plan_result.work_units),
                "assumptions": plan_result.assumptions,
            },
        )

        return validation, plan_result

    def _update_run_state(self, run: RunState, new_state: OrchestratorState) -> None:
        run.state = new_state
        run.updated_at = datetime.now(timezone.utc)
        self.prs.save_run(run)
        self.prs.save_current(run.model_dump(mode="json"))


def _build_planner_prompt(template: str, ctx: PlannerContextPack) -> str:
    replacements = {
        "{milestone_id}": ctx.milestone_id,
        "{objective}": ctx.milestone_objective,
        "{repo_summary}": ctx.repo_summary,
        "{architecture_constraints}": "\n".join(f"- {c}" for c in ctx.architecture_constraints) or "None",
        "{prd_sections}": "\n".join(f"- {s}" for s in ctx.relevant_prd_sections) or "None",
        "{prior_decisions}": "\n".join(f"- {d.question}: {d.decision}" for d in ctx.prior_decisions) or "None",
    }
    for key, val in replacements.items():
        template = template.replace(key, val)
    return template
