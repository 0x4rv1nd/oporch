"""Role-specific context pack construction.

PRD Section 11 — do not broadcast full repo/PRD to everyone.
Each role receives only the context it needs for its task.

v2: also hosts the plan-document parser that turns an arbitrary N-phase
markdown implementation plan into ``Phase`` objects (``## Phase N: Title``
headers plus bullet acceptance criteria).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .constants import AgentRole
from .models import ContextPack, Phase, WorkUnit


_PHASE_HEADER = re.compile(r"^#{1,3}\s*[Pp]hase\s*(\d+)\s*[:.\-–]?\s*(.*)$")
_ANY_HEADER = re.compile(r"^#{1,3}\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")


def parse_plan_doc(text: str) -> list[Phase]:
    """Parse an N-phase markdown plan into Phase objects.

    Recognizes ``## Phase N: Title`` headers (heading level 1-3, case
    insensitive). Bullet lines directly under a phase header become its
    acceptance criteria; leading prose becomes the description. If the doc
    has no ``Phase N`` headers, every top section becomes a sequentially
    numbered phase. Returns [] for empty input.
    """
    if not text or not text.strip():
        return []

    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    saw_phase_header = False

    for line in text.splitlines():
        m = _PHASE_HEADER.match(line)
        if m:
            saw_phase_header = True
            if current is not None:
                entries.append(current)
            current = {
                "number": int(m.group(1)),
                "title": (m.group(2) or "").strip(),
                "desc": [],
                "criteria": [],
            }
            continue

        g = _ANY_HEADER.match(line)
        if g:
            title = g.group(1).strip()
            if not saw_phase_header and title:
                # Generic sectioned doc: number assigned after the loop.
                if current is not None:
                    entries.append(current)
                current = {"number": None, "title": title, "desc": [], "criteria": []}
                continue
            # Non-phase heading inside a phased doc counts as prose.
            if current is not None and len(current["desc"]) < 6:
                current["desc"].append(title)
            continue

        if current is None:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            item = re.sub(
                r"^\*{0,2}(?:AC\s*-?\s*\d+\s*[:.)\-]?\*?)?\s*",
                "",
                bullet.group(1).strip(),
            ).strip()
            item = item.lstrip("*").strip()
            if item:
                current["criteria"].append(item)
        else:
            stripped = line.strip()
            if stripped and len(current["desc"]) < 6:
                current["desc"].append(stripped)

    if current is not None:
        entries.append(current)

    if saw_phase_header:
        # Drop generic preamble sections captured before the first
        # "Phase N" header (document title, intro, etc.).
        entries = [e for e in entries if e["number"] is not None]
    else:
        for i, e in enumerate(entries):
            e["number"] = i + 1

    phases: list[Phase] = []
    for i, e in enumerate(entries):
        number = e["number"] if e["number"] is not None else i + 1
        phases.append(
            Phase(
                number=number,
                title=e["title"] or f"Phase {number}",
                description=" ".join(e["desc"]).strip() or None,
                acceptance_criteria=e["criteria"],
                raw=None,
            )
        )
    return phases


def load_plan_doc(path: Path | str) -> list[Phase]:
    """Read a markdown plan file and parse it into Phases."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Plan document not found: {p}")
    return parse_plan_doc(p.read_text(encoding="utf-8"))


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
    role: AgentRole | str,
    work_unit: WorkUnit,
    *,
    prd_sections: list[str] | None = None,
    architecture_constraints: list[str] | None = None,
    dependency_outputs: list[str] | None = None,
    diff: str | None = None,
    failure_evidence: str | None = None,
    project_memory: list[str] | None = None,
) -> ContextPack:
    """Dispatch to the appropriate context builder for a given role.

    ``role`` accepts an AgentRole enum or a plain roster key string.
    ``project_memory`` carries recalled agent_memory rows (v2 §4).
    """
    key = role.value if hasattr(role, "value") else str(role)
    memory = project_memory or []
    if key == AgentRole.BUILDER.value:
        ctx = build_builder_context(
            work_unit,
            prd_sections=prd_sections,
            architecture_constraints=architecture_constraints,
            dependency_outputs=dependency_outputs,
        )
    elif key == AgentRole.REVIEWER.value:
        ctx = build_reviewer_context(
            work_unit,
            diff=diff,
            architecture_constraints=architecture_constraints,
        )
    elif key == AgentRole.TESTER.value:
        ctx = build_tester_context(
            work_unit,
            diff=diff,
        )
    elif key == AgentRole.DEBUGGER.value:
        ctx = build_debugger_context(
            work_unit,
            failure_evidence=failure_evidence,
            diff=diff,
        )
    else:
        # Default: provide basic context for any other (dynamic roster) role
        ctx = ContextPack(
            work_unit_id=work_unit.id,
            relevant_files=work_unit.files_likely_affected,
            acceptance_criteria=work_unit.acceptance_criteria,
        )
    if memory:
        ctx.project_memory = memory
    return ctx
