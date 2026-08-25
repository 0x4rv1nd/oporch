"""Tests for git worktree isolation + supervisor merge gate (PRD §7)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oporch.constants import AgentRole, ApprovalMode, WorkUnitStatus, EventType
from oporch.executor import FakeAgentExecutor
from oporch.git_manager import (
    GitManager,
    GitManagerError,
    MergeConflictError,
    ProtectedBranchError,
)
from oporch.models import (
    AgentResult,
    CompletionGate,
    PoliciesConfig,
)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} failed: {r.stderr}")
    return r


@pytest.fixture()
def repo(tmp_path):
    """A temp git repo with one baseline commit."""
    base = tmp_path / "repo"
    base.mkdir()
    _git(base, "init", "-b", "main")
    _git(base, "config", "user.email", "test@oporch.local")
    _git(base, "config", "user.name", "oporch tests")
    (base / "app.txt").write_text("baseline\n", encoding="utf-8")
    (base / "shared.txt").write_text("shared\n", encoding="utf-8")
    _git(base, "add", "-A")
    _git(base, "commit", "-m", "baseline")
    return base


@pytest.fixture()
def gm(repo):
    return GitManager(repo_path=repo, worktree_root=repo / "_wt")


class TestWorktrees:
    def test_is_git_repo(self, gm, repo):
        assert gm.is_git_repo() is True

    def test_create_worktree_makes_branch_and_dir(self, gm, repo):
        path = gm.create_worktree("run1", "WU-001")
        assert path.exists()
        assert gm.branch_exists(gm.wu_branch("run1", "WU-001"))
        out = _git(repo, "worktree", "list").stdout
        assert path.as_posix() in out

    def test_commit_wu_result(self, gm, repo):
        path = gm.create_worktree("run1", "WU-002")
        (path / "new_file.txt").write_text("agent output\n", encoding="utf-8")
        sha = gm.commit_wu_result("run1", "WU-002", "implement WU-002")
        assert sha is not None and len(sha) == 40

    def test_commit_with_no_changes_returns_none(self, gm, repo):
        gm.create_worktree("run1", "WU-003")
        assert gm.commit_wu_result("run1", "WU-003", "empty") is None

    def test_diff_for_review_redacts_secrets(self, gm, repo):
        gm.create_worktree("run1", "WU-004")
        path = gm.worktree_path("run1", "WU-004")
        (path / "secret.txt").write_text(
            "token=AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8",
        )
        gm.commit_wu_result("run1", "WU-004", "adds secret")
        diff = gm.diff_for_review("run1", "WU-004")
        assert "AKIAIOSFODNN7EXAMPLE" not in diff
        assert "[REDACTED]" in diff or "REDACTED" in diff

    def test_changed_files(self, gm, repo):
        gm.create_worktree("run1", "WU-005")
        path = gm.worktree_path("run1", "WU-005")
        (path / "a.txt").write_text("x\n", encoding="utf-8")
        gm.commit_wu_result("run1", "WU-005", "change a")
        files = gm.changed_files("run1", "WU-005")
        assert "a.txt" in files

    def test_cleanup_worktree(self, gm, repo):
        gm.create_worktree("run1", "WU-006")
        path = gm.worktree_path("run1", "WU-006")
        gm.cleanup_worktree("run1", "WU-006")
        assert not path.exists()
        assert not gm.branch_exists(gm.wu_branch("run1", "WU-006"))

    def test_push_blocked_in_worktree(self, gm, repo):
        gm.create_worktree("run1", "WU-007")
        path = gm.worktree_path("run1", "WU-007")
        assert gm.push_blocked(path) is True


class TestSupervisorMerge:
    def test_clean_merge_into_integration(self, gm, repo):
        integration = gm.ensure_integration_branch("runX")
        gm.create_worktree("runX", "WU-A")
        path = gm.worktree_path("runX", "WU-A")
        (path / "feature_a.txt").write_text("A\n", encoding="utf-8")
        gm.commit_wu_result("runX", "WU-A", "feature A")

        sha = gm.merge_wu_into_integration("runX", "WU-A")
        assert len(sha) == 40
        # integration branch now contains feature file
        content = _git(
            gm.ensure_integration_worktree("runX"),
            "show", f"{integration}:feature_a.txt",
        ).stdout
        assert content.strip() == "A"

    def test_conflict_detected(self, gm, repo):
        gm.ensure_integration_branch("runY")

        # WU-B edits shared.txt to version B
        gm.create_worktree("runY", "WU-B")
        b = gm.worktree_path("runY", "WU-B")
        (b / "shared.txt").write_text("version B\n", encoding="utf-8")
        gm.commit_wu_result("runY", "WU-B", "B edit")

        # WU-C edits shared.txt differently on top of integration
        gm.create_worktree("runY", "WU-C")
        c = gm.worktree_path("runY", "WU-C")
        (c / "shared.txt").write_text("version C\n", encoding="utf-8")
        gm.commit_wu_result("runY", "WU-C", "C edit")

        # merge C first -> clean; then B conflicts against updated integration
        gm.merge_wu_into_integration("runY", "WU-C")
        conflicts = gm.detect_conflicts("runY", "WU-B")
        assert conflicts, "expected conflict between B and merged C"
        with pytest.raises(MergeConflictError):
            gm.merge_wu_into_integration("runY", "WU-B")

    def test_second_merge_after_first_updates_base(self, gm, repo):
        gm.ensure_integration_branch("runZ")
        for wu, name in (("WU-D", "D"), ("WU-E", "E")):
            gm.create_worktree("runZ", wu)
            p = gm.worktree_path("runZ", wu)
            (p / f"{name.lower()}.txt").write_text(f"{name}\n", encoding="utf-8")
            gm.commit_wu_result("runZ", wu, name)
            gm.merge_wu_into_integration("runZ", wu)
        wt = gm.ensure_integration_worktree("runZ")
        names = {p.name for p in wt.iterdir()}
        assert "d.txt" in names and "e.txt" in names


class TestProtectedBranches:
    def test_never_auto_merge_to_main_refused(self, gm, repo):
        gm.ensure_integration_branch("runP")
        with pytest.raises(ProtectedBranchError):
            gm.merge_integration_into_base("runP", "main")

    def test_never_auto_merge_to_develop_refused(self, gm, repo):
        gm.ensure_integration_branch("runQ")
        with pytest.raises(ProtectedBranchError):
            gm.merge_integration_into_base("runQ", "develop")

    def test_unprotected_branch_allowed(self, gm, repo):
        gm.ensure_integration_branch("runR")
        _git(repo, "branch", "feature-base")
        # checkout via detached worktree-safe route: use main repo checkout
        _git(repo, "checkout", "feature-base")
        try:
            sha = gm.merge_integration_into_base("runR", "feature-base")
            assert len(sha) == 40
        finally:
            _git(repo, "checkout", "main")


# ---------------------------------------------------------------------------
# runner-level gate integration (FakeAgentExecutor, real temp repo)
# ---------------------------------------------------------------------------

def _runner_with_gate(tmp_path, executor=None):
    import os

    from oporch.event_log import EventLog
    from oporch.git_manager import GitManager
    from oporch.runner import MilestoneRunner
    from oporch.run_state import PersistentRunState
    from oporch.state_machine import StateMachine

    os.chdir(tmp_path)

    policies = PoliciesConfig(
        approval_mode="SUPERVISED",
        completion_gate=CompletionGate(
            require_review_approval=False,
            require_tests_pass=False,
            require_supervisor_merge=True,
        ),
    )
    executor = executor or FakeAgentExecutor()
    prs = PersistentRunState()
    sm = StateMachine()
    runner = MilestoneRunner(
        executor=executor,
        prs=prs,
        policies=policies,
        state_machine=sm,
        event_log=EventLog("gate-run-" + str(abs(hash(str(tmp_path))) % 10**8)),
    )
    runner._git_manager = GitManager(
        repo_path=tmp_path, worktree_root=tmp_path / "_wt",
    )
    return runner, executor, prs


@pytest.fixture()
def git_repo_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "base")
    return tmp_path


class TestRunnerMergeGate:
    def test_wu_completes_through_gate(self, git_repo_cwd):
        from datetime import datetime, timezone

        from oporch.models import RunState, WorkUnit

        runner, executor, prs = _runner_with_gate(git_repo_cwd)
        run_id = "gaterun"
        run_state = RunState(
            run_id=run_id, milestone_id="M", objective="gate",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        prs.save_run(run_state)
        wus = [WorkUnit(id="WU-G1", title="G", objective="obj")]
        prs.save_work_units(run_id, wus)

        report = runner.run_milestone(run_state)
        assert report.status == "COMPLETED"
        statuses = {wu.id: wu.status for wu in report.work_units}
        assert statuses["WU-G1"] == WorkUnitStatus.COMPLETED
        # integration branch exists and has a squash commit
        gm = runner.git
        assert gm.branch_exists(gm.integration_branch(run_id))

    def test_strict_mode_blocks_until_approved(self, git_repo_cwd):
        import asyncio
        from datetime import datetime, timezone

        from oporch.models import RunState, WorkUnit
        from oporch.db import OporchDB

        runner, executor, prs = _runner_with_gate(git_repo_cwd)
        runner.policies.approval_mode = ApprovalMode.STRICT.value

        async def scenario():
            db = OporchDB()

            async def approver():
                deadline = asyncio.get_running_loop().time() + 10
                while asyncio.get_running_loop().time() < deadline:
                    rows = db._query(
                        "SELECT key FROM control WHERE key LIKE 'merge_pending:%'"
                        " AND value='1'"
                    )
                    if rows:
                        db.set_control(rows[-1]["key"], "approved")
                        return
                    await asyncio.sleep(0.2)
                raise AssertionError("approval row never appeared")

            task = asyncio.create_task(approver())
            run_id = "strict-run-" + str(abs(hash(str(git_repo_cwd))) % 10**6)
            run_state = RunState(
                run_id=run_id, milestone_id="M", objective="s",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            prs.save_run(run_state)
            wus = [WorkUnit(id="WU-S1", title="S", objective="o")]
            prs.save_work_units(run_id, wus)
            report = await runner.run_milestone_async(run_state)
            await task
            db.close()
            return report

        report = asyncio.run(scenario())
        assert report.status == "COMPLETED"


class TestPolicyDefaults:
    def test_default_policy_protects_main(self):
        p = PoliciesConfig()
        assert "main" in p.security.never_auto_merge_to
        assert "develop" in p.security.never_auto_merge_to

    def test_merge_conflict_defaults_to_debugger(self):
        p = PoliciesConfig()
        assert p.merge_conflict.route == "debugger"

    def test_supervisor_role_exists(self):
        assert AgentRole.SUPERVISOR.value == "supervisor"

    def test_merge_conflict_status_exists(self):
        assert WorkUnitStatus.MERGE_CONFLICT.value == "MERGE_CONFLICT"
