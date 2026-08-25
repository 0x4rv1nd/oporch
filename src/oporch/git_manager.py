"""Git isolation for parallel agents (v2, PRD §7).

One branch + worktree per WORK UNIT (not per agent) under
``.opencode-orchestrator/worktrees/<wu_id>/`` so concurrent agents never
share a working directory or index lock.

The supervisor role (and only the supervisor) merges reviewed WU branches
into a per-run ``oporch/<run_id>/integration`` branch via a dedicated
integration worktree. Merges into protected base branches (main/develop)
are refused outright (§10 ``never_auto_merge_to``).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .redact import redact_secrets

ORCHESTRATOR_DIR = Path(".opencode-orchestrator")
DEFAULT_WORKTREE_ROOT = ORCHESTRATOR_DIR / "worktrees"

_BLOCKED_PUSH_URL = "no-push-allowed-by-oporch"


class GitManagerError(Exception):
    pass


class MergeConflictError(GitManagerError):
    def __init__(self, wu_id: str, conflicts: list[str]) -> None:
        self.wu_id = wu_id
        self.conflicts = conflicts
        super().__init__(
            f"Merge conflict for {wu_id}: {', '.join(conflicts) or 'unknown files'}"
        )


class ProtectedBranchError(GitManagerError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower() or "wu"


class GitManager:
    def __init__(
        self,
        repo_path: str | Path = ".",
        worktree_root: Path | None = None,
    ) -> None:
        self.repo = Path(repo_path).resolve()
        self.worktree_root = (
            worktree_root or self.repo / DEFAULT_WORKTREE_ROOT
        )
        self._integration_worktrees: dict[str, Path] = {}

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _git(self, *args: str, cwd: Path | None = None,
             check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if check and result.returncode != 0:
            raise GitManagerError(
                f"git {' '.join(args)} failed "
                f"(exit {result.returncode}): {result.stderr.strip()}"
            )
        return result

    def is_git_repo(self) -> bool:
        try:
            r = self._git("rev-parse", "--is-inside-work-tree", check=False)
            return r.returncode == 0 and r.stdout.strip() == "true"
        except (GitManagerError, FileNotFoundError):
            return False

    def current_branch(self) -> str | None:
        r = self._git("branch", "--show-current", check=False)
        return r.stdout.strip() or None if r.returncode == 0 else None

    def has_commits(self) -> bool:
        r = self._git("rev-parse", "HEAD", check=False)
        return r.returncode == 0

    # ------------------------------------------------------------------
    # branches / integration
    # ------------------------------------------------------------------
    @staticmethod
    def integration_branch(run_id: str) -> str:
        return f"oporch/{run_id}/integration"

    @staticmethod
    def wu_branch(run_id: str, wu_id: str) -> str:
        return f"oporch/{run_id}/{_slug(wu_id)}"

    def ensure_integration_branch(
        self, run_id: str, base_branch: str | None = None,
    ) -> str:
        """Create (or reuse) the per-run integration branch."""
        branch = self.integration_branch(run_id)
        exists = self._git(
            "rev-parse", "--verify", branch, check=False,
        ).returncode == 0
        if not exists:
            if not self.has_commits():
                raise GitManagerError(
                    "Repository has no commits; cannot create integration branch"
                )
            start = base_branch or self.current_branch() or "HEAD"
            self._git("branch", branch, start)
        return branch

    def ensure_integration_worktree(self, run_id: str) -> Path:
        """Dedicated worktree checked out to the integration branch."""
        if run_id in self._integration_worktrees:
            path = self._integration_worktrees[run_id]
            if path.exists():
                return path
        branch = self.ensure_integration_branch(run_id)
        path = self.worktree_root / f"_integration-{run_id}"
        if not path.exists():
            existing = self._worktree_path_for(branch)
            if existing is not None:
                path = existing
            else:
                self._git("worktree", "add", str(path), branch)
        self._integration_worktrees[run_id] = path
        return path

    def _worktree_path_for(self, branch: str) -> Path | None:
        r = self._git("worktree", "list", "--porcelain", check=False)
        if r.returncode != 0:
            return None
        current_path: Path | None = None
        for line in r.stdout.splitlines():
            if line.startswith("worktree "):
                current_path = Path(line[len("worktree "):])
            elif line.startswith("branch ") and current_path is not None:
                ref = line[len("branch "):].strip()
                if ref == f"refs/heads/{branch}":
                    return current_path
                current_path = None
        return None

    # ------------------------------------------------------------------
    # per-WU worktrees
    # ------------------------------------------------------------------
    def create_worktree(
        self,
        run_id: str,
        wu_id: str,
        base_branch: str | None = None,
    ) -> Path:
        """Create branch + worktree for one work unit. Returns its path."""
        branch = self.wu_branch(run_id, wu_id)
        path = self.worktree_root / _slug(wu_id)
        if path.exists():
            raise GitManagerError(f"Worktree already exists: {path}")
        self.worktree_root.mkdir(parents=True, exist_ok=True)

        exists = self._git(
            "rev-parse", "--verify", branch, check=False,
        ).returncode == 0
        if exists:
            self._git("worktree", "add", str(path), branch)
        else:
            if not self.has_commits():
                raise GitManagerError(
                    "Repository has no commits; commit a baseline first"
                )
            integration = self.integration_branch(run_id)
            if base_branch:
                start = base_branch
            elif self.branch_exists(integration):
                start = integration
            else:
                start = self.current_branch() or "HEAD"
            self._git("worktree", "add", "-b", branch, str(path), start)

        self.disable_push(path)
        return path

    def disable_push(self, worktree_path: Path) -> None:
        """Best-effort structural push block for one worktree (§10 minimum).

        Uses git's per-worktree config so only this worktree gets a broken
        push URL. Non-fatal on older git versions without support.
        """
        try:
            self._git("config", "extensions.worktreeConfig", "true")
            self._git(
                "config", "--worktree", "remote.origin.pushurl",
                _BLOCKED_PUSH_URL, cwd=worktree_path,
            )
        except GitManagerError:
            pass

    def push_blocked(self, worktree_path: Path) -> bool:
        r = self._git(
            "config", "--worktree", "remote.origin.pushurl",
            cwd=worktree_path, check=False,
        )
        return r.returncode == 0 and _BLOCKED_PUSH_URL in r.stdout

    def worktree_path(self, run_id: str, wu_id: str) -> Path:
        return self.worktree_root / _slug(wu_id)

    def cleanup_worktree(self, run_id: str, wu_id: str) -> None:
        path = self.worktree_path(run_id, wu_id)
        if path.exists():
            self._git("worktree", "remove", "--force", str(path))
        # Keep the branch for audit; delete only on explicit request.
        branch = self.wu_branch(run_id, wu_id)
        still_checked_out = self._worktree_path_for(branch) is not None
        if not still_checked_out and self.branch_exists(branch):
            self._git("branch", "-D", branch, check=False)

    def branch_exists(self, branch: str) -> bool:
        return self._git(
            "rev-parse", "--verify", branch, check=False,
        ).returncode == 0

    # ------------------------------------------------------------------
    # committing / diffing inside a WU worktree
    # ------------------------------------------------------------------
    def commit_wu_result(
        self, run_id: str, wu_id: str, message: str,
    ) -> str | None:
        """Stage all changes in the WU worktree and commit. Returns sha."""
        path = self.worktree_path(run_id, wu_id)
        if not path.exists():
            raise GitManagerError(
                f"No worktree for {wu_id}; call create_worktree first"
            )
        self._git("add", "-A", cwd=path)
        status = self._git("status", "--porcelain", cwd=path, check=False)
        if not status.stdout.strip():
            return None  # nothing to commit
        commit = self._git(
            "commit", "-m", redact_secrets(message), cwd=path, check=False,
        )
        if commit.returncode != 0:
            raise GitManagerError(
                f"Commit failed for {wu_id}: {commit.stderr.strip()}"
            )
        return self._git("rev-parse", "HEAD", cwd=path).stdout.strip()

    def diff_for_review(self, run_id: str, wu_id: str) -> str:
        """Full diff of the WU branch against its merge base."""
        branch = self.wu_branch(run_id, wu_id)
        integration = self.integration_branch(run_id)
        base_ref = (
            integration
            if self.branch_exists(integration)
            else "HEAD"
        )
        r = self._git(
            "diff", f"{base_ref}...{branch}", check=False,
        )
        if r.returncode != 0:
            raise GitManagerError(
                f"diff_for_review failed for {wu_id}: {r.stderr.strip()}"
            )
        return redact_secrets(r.stdout)

    def changed_files(self, run_id: str, wu_id: str) -> list[str]:
        branch = self.wu_branch(run_id, wu_id)
        integration = self.integration_branch(run_id)
        base_ref = integration if self.branch_exists(integration) else "HEAD"
        r = self._git(
            "diff", "--name-only", f"{base_ref}...{branch}", check=False,
        )
        return [ln for ln in r.stdout.splitlines() if ln.strip()]

    # ------------------------------------------------------------------
    # supervisor merge gate
    # ------------------------------------------------------------------
    def detect_conflicts(self, run_id: str, wu_id: str) -> list[str]:
        """Dry-run merge of WU branch into integration; returns conflict files."""
        integration = self.ensure_integration_branch(run_id)
        branch = self.wu_branch(run_id, wu_id)
        r = self._git(
            "merge-tree", "--write-tree", "--name-only",
            integration, branch, check=False,
        )
        if r.returncode == 0:
            return []
        conflicts = [
            ln.strip() for ln in r.stdout.splitlines()
            if ln.strip() and not ln.startswith(("Merge", "parents"))
        ]
        return conflicts

    def merge_wu_into_integration(self, run_id: str, wu_id: str) -> str:
        """Squash-merge a passed WU branch into the integration branch.

        Raises MergeConflictError on conflict (leaves integration clean).
        Returns the squash commit sha.
        """
        conflicts = self.detect_conflicts(run_id, wu_id)
        if conflicts:
            raise MergeConflictError(wu_id, conflicts)

        integration_wt = self.ensure_integration_worktree(run_id)
        branch = self.wu_branch(run_id, wu_id)
        merge = self._git(
            "merge", "--squash", branch, cwd=integration_wt, check=False,
        )
        if merge.returncode != 0:
            self._git("merge", "--abort", cwd=integration_wt, check=False)
            raise GitManagerError(
                f"Squash merge failed for {wu_id}: {merge.stderr.strip()}"
            )
        status = self._git("status", "--porcelain", cwd=integration_wt)
        if not status.stdout.strip():
            # Branch adds nothing new (e.g. agent made no file changes):
            # treat as already merged.
            return self._git("rev-parse", "HEAD", cwd=integration_wt).stdout.strip()
        commit = self._git(
            "commit",
            "-m", redact_secrets(f"merge {wu_id} (squash)"),
            cwd=integration_wt, check=False,
        )
        if commit.returncode != 0:
            self._git("merge", "--abort", cwd=integration_wt, check=False)
            raise GitManagerError(
                f"Squash commit failed for {wu_id}: {commit.stderr.strip()}"
            )
        return self._git("rev-parse", "HEAD", cwd=integration_wt).stdout.strip()

    def merge_integration_into_base(self, run_id: str, target_branch: str) -> str:
        """Merge the run's integration branch into a real base branch.

        Hard-refuses protected branches (§10 never_auto_merge_to): those go
        through human PRs only. This method is intentionally the ONLY path
        that touches real base branches.
        """
        from .models import SecurityPolicy

        protected = SecurityPolicy().never_auto_merge_to
        if target_branch in protected:
            raise ProtectedBranchError(
                f"'{target_branch}' is protected by policy "
                f"(never_auto_merge_to={protected}); open a PR instead."
            )
        integration_wt = self.ensure_integration_worktree(run_id)
        self._git("checkout", target_branch)
        try:
            self._git("merge", "--no-ff",
                      self.integration_branch(run_id))
        finally:
            pass
        return self._git("rev-parse", "HEAD").stdout.strip()
