"""Role-specific context pack construction.

PRD Section 11 — do not broadcast full repo/PRD to everyone.
Each role receives only the context it needs for its task.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import AgentRole
from .models import ContextPack, WorkUnit


def build_builder_context(
    work_unit: WorkUnit,
    *,
    prd_sections: list[str] | None = None,
    architecture_constraints: list[str] | None = None,
    dependency_outputs: list[str] | None = None,
) -> ContextPack:
    """Build context for the Builder role.

    Builder receives: work unit info, relevant files, relevant PRD sections,
    architecture constraints, accepted dependency outputs.
    """
    return ContextPack(
        work_unit_id=work_unit.id,
        relevant_prd_sections=prd_sections or [],
        relevant_files=work_unit.files_likely_affected,
        architecture_constraints=architecture_constraints or [],
        dependency_outputs=dependency_outputs or [],
        acceptance_criteria=work_unit.acceptance_criteria,
    )


def build_reviewer_context(
    work_unit: WorkUnit,
    *,
    diff: str | None = None,
    architecture_constraints: list[str] | None = None,
) -> ContextPack:
    """Build context for the Reviewer role.

    Reviewer receives: acceptance criteria, actual diff,
    relevant tests, architecture constraints.
    """
    return ContextPack(
        work_unit_id=work_unit.id,
        relevant_files=work_unit.files_likely_affected,
        architecture_constraints=architecture_constraints or [],
        acceptance_criteria=work_unit.acceptance_criteria,
        diff=diff,
    )


def build_tester_context(
    work_unit: WorkUnit,
    *,
    diff: str | None = None,
) -> ContextPack:
    """Build context for the Tester role.

    Tester receives: acceptance criteria, changed files,
    test commands, benchmark definitions.
    """
    return ContextPack(
        work_unit_id=work_unit.id,
        relevant_files=work_unit.files_likely_affected,
        acceptance_criteria=work_unit.acceptance_criteria,
        diff=diff,
    )


def build_debugger_context(
    work_unit: WorkUnit,
    *,
    failure_evidence: str | None = None,
    diff: str | None = None,
) -> ContextPack:
    """Build context for the Debugger role.

    Debugger receives: failure evidence, logs, relevant trace, changed diff.
    """
    return ContextPack(
        work_unit_id=work_unit.id,
        relevant_files=work_unit.files_likely_affected,
        acceptance_criteria=work_unit.acceptance_criteria,
        failure_evidence=failure_evidence,
        diff=diff,
    )


def build_context_for_role(
    role: AgentRole,
    work_unit: WorkUnit,
    *,
    prd_sections: list[str] | None = None,
    architecture_constraints: list[str] | None = None,
    dependency_outputs: list[str] | None = None,
    diff: str | None = None,
    failure_evidence: str | None = None,
) -> ContextPack:
    """Dispatch to the appropriate context builder for a given role."""
    if role == AgentRole.BUILDER:
        return build_builder_context(
            work_unit,
            prd_sections=prd_sections,
            architecture_constraints=architecture_constraints,
            dependency_outputs=dependency_outputs,
        )
    elif role == AgentRole.REVIEWER:
        return build_reviewer_context(
            work_unit,
            diff=diff,
            architecture_constraints=architecture_constraints,
        )
    elif role == AgentRole.TESTER:
        return build_tester_context(
            work_unit,
            diff=diff,
        )
    elif role == AgentRole.DEBUGGER:
        return build_debugger_context(
            work_unit,
            failure_evidence=failure_evidence,
            diff=diff,
        )
    else:
        # Default: provide basic context for any other role
        return ContextPack(
            work_unit_id=work_unit.id,
            relevant_files=work_unit.files_likely_affected,
            acceptance_criteria=work_unit.acceptance_criteria,
        )
