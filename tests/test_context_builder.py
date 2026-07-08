"""Tests for role-specific context pack construction."""

from __future__ import annotations

import pytest

from oporch.constants import AgentRole, WorkUnitStatus
from oporch.context_builder import (
    build_builder_context,
    build_context_for_role,
    build_debugger_context,
    build_reviewer_context,
    build_tester_context,
)
from oporch.models import WorkUnit


def _make_wu(**overrides) -> WorkUnit:
    """Helper to create a WorkUnit with sensible defaults."""
    defaults = {
        "id": "WU-001",
        "title": "Implement feature X",
        "objective": "Add feature X to module Y",
        "acceptance_criteria": ["Tests pass", "No regressions"],
        "files_likely_affected": ["src/module.py", "tests/test_module.py"],
        "tests_required": ["test_feature_x"],
    }
    defaults.update(overrides)
    return WorkUnit(**defaults)


class TestBuildBuilderContext:
    """Builder should receive: work unit, files, PRD, constraints, dep outputs."""

    def test_includes_work_unit_id(self) -> None:
        wu = _make_wu()
        ctx = build_builder_context(wu)
        assert ctx.work_unit_id == "WU-001"

    def test_includes_affected_files(self) -> None:
        wu = _make_wu()
        ctx = build_builder_context(wu)
        assert "src/module.py" in ctx.relevant_files
        assert "tests/test_module.py" in ctx.relevant_files

    def test_includes_acceptance_criteria(self) -> None:
        wu = _make_wu()
        ctx = build_builder_context(wu)
        assert "Tests pass" in ctx.acceptance_criteria

    def test_includes_prd_sections(self) -> None:
        wu = _make_wu()
        ctx = build_builder_context(wu, prd_sections=["## Auth", "## API"])
        assert "## Auth" in ctx.relevant_prd_sections

    def test_includes_architecture_constraints(self) -> None:
        wu = _make_wu()
        ctx = build_builder_context(wu, architecture_constraints=["No global state"])
        assert "No global state" in ctx.architecture_constraints

    def test_includes_dependency_outputs(self) -> None:
        wu = _make_wu()
        ctx = build_builder_context(wu, dependency_outputs=["WU-000 output: schema created"])
        assert "WU-000 output: schema created" in ctx.dependency_outputs

    def test_no_diff_or_failure_evidence(self) -> None:
        wu = _make_wu()
        ctx = build_builder_context(wu)
        assert ctx.diff is None
        assert ctx.failure_evidence is None


class TestBuildReviewerContext:
    """Reviewer should receive: acceptance criteria, diff, constraints only."""

    def test_includes_work_unit_id(self) -> None:
        wu = _make_wu()
        ctx = build_reviewer_context(wu)
        assert ctx.work_unit_id == "WU-001"

    def test_includes_diff(self) -> None:
        wu = _make_wu()
        ctx = build_reviewer_context(wu, diff="--- a/file\n+++ b/file")
        assert "--- a/file" in ctx.diff

    def test_includes_acceptance_criteria(self) -> None:
        wu = _make_wu()
        ctx = build_reviewer_context(wu)
        assert "Tests pass" in ctx.acceptance_criteria

    def test_includes_constraints(self) -> None:
        wu = _make_wu()
        ctx = build_reviewer_context(wu, architecture_constraints=["Immutable"])
        assert "Immutable" in ctx.architecture_constraints

    def test_no_prd_sections(self) -> None:
        wu = _make_wu()
        ctx = build_reviewer_context(wu)
        assert ctx.relevant_prd_sections == []

    def test_no_dependency_outputs(self) -> None:
        wu = _make_wu()
        ctx = build_reviewer_context(wu)
        assert ctx.dependency_outputs == []


class TestBuildTesterContext:
    """Tester should receive: acceptance criteria, changed files, diff."""

    def test_includes_work_unit_id(self) -> None:
        wu = _make_wu()
        ctx = build_tester_context(wu)
        assert ctx.work_unit_id == "WU-001"

    def test_includes_affected_files(self) -> None:
        wu = _make_wu()
        ctx = build_tester_context(wu)
        assert len(ctx.relevant_files) == 2

    def test_includes_acceptance_criteria(self) -> None:
        wu = _make_wu()
        ctx = build_tester_context(wu)
        assert "No regressions" in ctx.acceptance_criteria

    def test_no_prd_or_constraints(self) -> None:
        wu = _make_wu()
        ctx = build_tester_context(wu)
        assert ctx.relevant_prd_sections == []
        assert ctx.architecture_constraints == []


class TestBuildDebuggerContext:
    """Debugger should receive: failure evidence, diff, relevant files."""

    def test_includes_failure_evidence(self) -> None:
        wu = _make_wu()
        ctx = build_debugger_context(wu, failure_evidence="AssertionError in test_x")
        assert "AssertionError" in ctx.failure_evidence

    def test_includes_diff(self) -> None:
        wu = _make_wu()
        ctx = build_debugger_context(wu, diff="+added line")
        assert "+added line" in ctx.diff

    def test_includes_affected_files(self) -> None:
        wu = _make_wu()
        ctx = build_debugger_context(wu)
        assert "src/module.py" in ctx.relevant_files

    def test_no_prd_or_dep_outputs(self) -> None:
        wu = _make_wu()
        ctx = build_debugger_context(wu)
        assert ctx.relevant_prd_sections == []
        assert ctx.dependency_outputs == []


class TestBuildContextForRole:
    """build_context_for_role dispatches correctly."""

    def test_dispatches_builder(self) -> None:
        wu = _make_wu()
        ctx = build_context_for_role(AgentRole.BUILDER, wu, prd_sections=["## S1"])
        assert "## S1" in ctx.relevant_prd_sections

    def test_dispatches_reviewer(self) -> None:
        wu = _make_wu()
        ctx = build_context_for_role(AgentRole.REVIEWER, wu, diff="diff text")
        assert ctx.diff == "diff text"
        assert ctx.relevant_prd_sections == []

    def test_dispatches_tester(self) -> None:
        wu = _make_wu()
        ctx = build_context_for_role(AgentRole.TESTER, wu)
        assert ctx.work_unit_id == "WU-001"

    def test_dispatches_debugger(self) -> None:
        wu = _make_wu()
        ctx = build_context_for_role(
            AgentRole.DEBUGGER, wu, failure_evidence="Traceback..."
        )
        assert ctx.failure_evidence == "Traceback..."

    def test_fallback_for_unknown_role(self) -> None:
        wu = _make_wu()
        ctx = build_context_for_role(AgentRole.PLANNER, wu)
        assert ctx.work_unit_id == "WU-001"
        assert ctx.relevant_files == wu.files_likely_affected
