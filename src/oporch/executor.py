from __future__ import annotations

from typing import Protocol
from . import config as cfg
from .constants import AgentRole
from .models import AgentTask, AgentResult, ContextPack


class AgentExecutor(Protocol):
    def run(
        self,
        role: AgentRole,
        task: AgentTask,
        context: ContextPack,
    ) -> AgentResult:
        ...


class FakeAgentExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[AgentRole, AgentTask, ContextPack]] = []
        self._next_result: AgentResult | None = None

    def set_next_result(self, result: AgentResult) -> None:
        self._next_result = result

    def run(
        self,
        role: AgentRole,
        task: AgentTask,
        context: ContextPack,
    ) -> AgentResult:
        self.calls.append((role, task, context))
        if self._next_result is not None:
            result = self._next_result
            self._next_result = None
            return result
        return AgentResult(
            role=role,
            success=True,
            output=f"Fake output for {task.objective}",
        )

    def reset(self) -> None:
        self.calls.clear()
        self._next_result = None


class OpenCodeAgentExecutor:
    def __init__(self, opencode_cmd: str = "opencode") -> None:
        self._cmd = opencode_cmd

    def run(
        self,
        role: AgentRole,
        task: AgentTask,
        context: ContextPack,
    ) -> AgentResult:
        import subprocess

        prompt = self._build_prompt(role, task, context)
        model_id = cfg.resolve_model(role.value)
        cmd = [self._cmd, "-p", prompt]
        if model_id:
            cmd += ["-m", model_id]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            return AgentResult(
                role=role,
                success=result.returncode == 0,
                output=result.stdout,
                error=result.stderr if result.returncode != 0 else None,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                role=role,
                success=False,
                output="",
                error="Timeout expired",
            )
        except FileNotFoundError:
            return AgentResult(
                role=role,
                success=False,
                output="",
                error=f"OpenCode command '{self._cmd}' not found",
            )

    def _build_prompt(
        self,
        role: AgentRole,
        task: AgentTask,
        context: ContextPack,
    ) -> str:
        if task.raw_prompt:
            return task.raw_prompt
        parts = [f"You are acting as {role.value}."]
        parts.append(f"Objective: {task.objective}")
        if task.work_unit_id:
            parts.append(f"Work Unit: {task.work_unit_id}")
        if task.acceptance_criteria:
            parts.append(
                "Acceptance Criteria:\n"
                + "\n".join(f"- {c}" for c in task.acceptance_criteria)
            )
        if context.relevant_prd_sections:
            parts.append(
                "Relevant PRD sections:\n"
                + "\n".join(f"- {s}" for s in context.relevant_prd_sections)
            )
        if context.relevant_files:
            parts.append(
                "Relevant files:\n"
                + "\n".join(f"- {f}" for f in context.relevant_files)
            )
        if context.architecture_constraints:
            parts.append(
                "Architecture constraints:\n"
                + "\n".join(f"- {c}" for c in context.architecture_constraints)
            )
        return "\n\n".join(parts)
