from __future__ import annotations

import time
from typing import Protocol, Any
from . import config as cfg
from .constants import AgentRole
from .models import AgentTask, AgentResult, ContextPack


def role_key(role: AgentRole | str) -> str:
    """Normalize an AgentRole enum or plain string to a roster key string."""
    if hasattr(role, "value"):
        return str(role.value)
    return str(role)


class AgentExecutor(Protocol):
    def run(
        self,
        role: AgentRole | str,
        task: AgentTask,
        context: ContextPack,
    ) -> AgentResult:
        ...


EXEC_TIMEOUT_SECONDS = 300

# §10 minimum sandbox: env vars an agent subprocess may inherit. Everything
# else (AWS/Azure/GCP creds, DB URLs, arbitrary secrets) is stripped.
_ENV_ALLOWLIST = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "HOME", "USERPROFILE",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMDATA",
}
_ENV_PASSTHROUGH_PREFIXES = ("OPENCODE_", "OPORCH_")
_ENV_DENY_PREFIXES = (
    "AWS_", "AZURE_", "GOOGLE_API", "GOOGLE_APPLICATION",
    "SNOWFLAKE", "DATADOG", "SENDGRID", "STRIPE", "TWILIO",
)


def build_restricted_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return a scrubbed copy of ``base`` (or os.environ) for agent subprocesses."""
    import os

    src = dict(base if base is not None else os.environ)
    out: dict[str, str] = {}
    for key, value in src.items():
        upper = key.upper()
        if any(upper.startswith(p) for p in _ENV_DENY_PREFIXES):
            continue
        if upper in _ENV_ALLOWLIST or any(
            upper.startswith(p) for p in _ENV_PASSTHROUGH_PREFIXES
        ):
            out[key] = value
    return out


def estimate_tokens(text: str | None) -> int:
    """~4 chars/token heuristic (§9: cheap tracking now beats retrofitting)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


async def call_executor_async(
    executor: Any,
    role: AgentRole | str,
    task: AgentTask,
    context: ContextPack,
) -> AgentResult:
    """Await an agent execution, preferring ``run_async`` when available.

    Falls back to running the sync ``run`` in a worker thread so any
    executor implementation works with the parallel dispatcher.
    """
    run_async = getattr(executor, "run_async", None)
    if run_async is not None:
        return await run_async(role, task, context)
    import asyncio

    return await asyncio.to_thread(executor.run, role, task, context)


class FakeAgentExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[AgentRole | str, AgentTask, ContextPack]] = []
        self._next_results: list[AgentResult] = []

    @property
    def _next_result(self) -> AgentResult | None:
        return self._next_results[0] if self._next_results else None

    @_next_result.setter
    def _next_result(self, value: AgentResult | None) -> None:
        if value is None:
            self._next_results.clear()
        else:
            self._next_results = [value]

    def set_next_result(self, result: AgentResult) -> None:
        self._next_results.append(result)

    def run(
        self,
        role: AgentRole | str,
        task: AgentTask,
        context: ContextPack,
    ) -> AgentResult:
        self.calls.append((role, task, context))
        if self._next_results:
            return self._next_results.pop(0)
        return AgentResult(
            role=role_key(role),
            success=True,
            output=f"Fake output for {task.objective}",
        )

    def reset(self) -> None:
        self.calls.clear()
        self._next_results.clear()

    async def run_async(
        self,
        role: AgentRole | str,
        task: AgentTask,
        context: ContextPack,
    ) -> AgentResult:
        import time

        started = time.monotonic()
        result = self.run(role, task, context)
        if result.duration_ms is None:
            result.duration_ms = (time.monotonic() - started) * 1000.0
        return result


class OpenCodeAgentExecutor:
    def __init__(
        self,
        opencode_cmd: str = "opencode",
        sandbox_env: bool = True,
        env: dict[str, str] | None = None,
    ) -> None:
        self._cmd = opencode_cmd
        self._sandbox_env = sandbox_env
        self._env = env

    def _subprocess_env(self) -> dict[str, str] | None:
        if not self._sandbox_env:
            return self._env
        return build_restricted_env(self._env)

    def run(
        self,
        role: AgentRole | str,
        task: AgentTask,
        context: ContextPack,
    ) -> AgentResult:
        import subprocess
        import time

        key = role_key(role)
        prompt = self._build_prompt(key, task, context)
        model_id = task.model_override or cfg.resolve_model(key)
        cmd = [self._cmd, "-p", prompt]
        if model_id:
            cmd += ["-m", model_id]
        cwd = task.working_dir or None

        started = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=cwd,
                env=self._subprocess_env(),
            )
            duration_ms = (time.monotonic() - started) * 1000.0
            return AgentResult(
                role=key,
                success=result.returncode == 0,
                output=result.stdout,
                error=result.stderr if result.returncode != 0 else None,
                tokens_in=estimate_tokens(prompt),
                tokens_out=estimate_tokens(result.stdout),
                duration_ms=duration_ms,
                model_used=model_id,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                role=key,
                success=False,
                output="",
                error="Timeout expired",
                tokens_in=estimate_tokens(prompt),
                duration_ms=(time.monotonic() - started) * 1000.0,
                model_used=model_id,
            )
        except FileNotFoundError:
            return AgentResult(
                role=key,
                success=False,
                output="",
                error=f"OpenCode command '{self._cmd}' not found",
                model_used=model_id,
            )

    async def run_async(
        self,
        role: AgentRole | str,
        task: AgentTask,
        context: ContextPack,
    ) -> AgentResult:
        """Non-blocking variant of :meth:`run` using asyncio subprocesses."""
        import asyncio

        key = role_key(role)
        prompt = self._build_prompt(key, task, context)
        model_id = cfg.resolve_model(key)
        cmd = [self._cmd, "-p", prompt]
        if model_id:
            cmd += ["-m", model_id]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=task.working_dir or None,
                env=self._subprocess_env(),
            )
            started = time.monotonic()
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=EXEC_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return AgentResult(
                    role=key, success=False, output="", error="Timeout expired",
                    tokens_in=estimate_tokens(prompt),
                    duration_ms=(time.monotonic() - started) * 1000.0,
                    model_used=model_id,
                )
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            return AgentResult(
                role=key,
                success=proc.returncode == 0,
                output=out,
                error=err if proc.returncode != 0 else None,
                tokens_in=estimate_tokens(prompt),
                tokens_out=estimate_tokens(out),
                duration_ms=(time.monotonic() - started) * 1000.0,
                model_used=model_id,
            )
        except FileNotFoundError:
            return AgentResult(
                role=key,
                success=False,
                output="",
                error=f"OpenCode command '{self._cmd}' not found",
                model_used=model_id,
            )

    def _build_prompt(
        self,
        role: AgentRole | str,
        task: AgentTask,
        context: ContextPack,
    ) -> str:
        if task.raw_prompt:
            return task.raw_prompt
        parts = [f"You are acting as {role_key(role)}."]
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
        if context.project_memory:
            parts.append(
                "## Known project memory\n"
                "Lessons from previous runs on this project — respect them:\n"
                + "\n".join(f"- {m}" for m in context.project_memory)
            )
        if context.index_summary:
            parts.append(context.index_summary)
        return "\n\n".join(parts)
