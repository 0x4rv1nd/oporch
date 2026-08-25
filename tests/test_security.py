"""Security hardening tests (PRD §6 phase 9, §10)."""

from __future__ import annotations

import pytest

from oporch.redact import (
    filter_sensitive_paths,
    is_sensitive_path,
    redact_secrets,
)


class TestRedactExtensions:
    def test_connection_string_password(self):
        text = "connect with postgres://admin:s3cretpw@db.host:5432/prod"
        out = redact_secrets(text)
        assert "s3cretpw" not in out
        assert "db.host" in out  # host survives, password gone

    def test_pem_private_key_block(self):
        key = (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQ\nabc==\n"
            "-----END RSA PRIVATE KEY-----"
        )
        out = redact_secrets(f"data: {key}")
        assert "PRIVATE KEY" not in out or "[REDACTED]" in out
        assert "MIIEowIBAAKCAQ" not in out

    def test_jwt_redacted(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        assert jwt not in redact_secrets(f"token {jwt}")

    def test_github_tokens(self):
        tok = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        assert tok not in redact_secrets(tok)
        pat = "github_pat_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
        assert pat not in redact_secrets(pat)

    def test_slack_token(self):
        tok = "xoxb-mocktokenvalue"
        assert tok not in redact_secrets(tok)

    def test_env_style_assignment(self):
        text = "DATABASE_PASSWORD=hunter2\nNORMAL_VAR=hello\n"
        out = redact_secrets(text)
        assert "hunter2" not in out
        assert "hello" in out


class TestSensitivePaths:
    @pytest.mark.parametrize("path", [
        ".env",
        ".env.local",
        "config/.env.production",
        "certs/server.pem",
        "keys/api.key",
        "~/.ssh/id_rsa",
        "aws-credentials.txt",
        "secrets.yaml",
        ".npmrc",
        ".netrc",
    ])
    def test_sensitive_paths_detected(self, path):
        assert is_sensitive_path(path) is True

    @pytest.mark.parametrize("path", [
        "src/main.py",
        "README.md",
        "tests/test_auth.py",
        "config/settings.yaml",
        "docs/keys_of_success.md",
    ])
    def test_normal_paths_allowed(self, path):
        assert is_sensitive_path(path) is False

    def test_filter_list(self):
        paths = ["src/app.py", ".env", "tests/x.py", "id_rsa"]
        assert filter_sensitive_paths(paths) == ["src/app.py", "tests/x.py"]


class TestPersistentTablesRedacted:
    def test_events_table(self, tmp_path=None):
        from oporch.db import OporchDB

        db = OporchDB()
        try:
            db.append_event(
                "sec-run",
                "DEBUG",
                payload={"cmd": "postgres://u:hunter2pw@h/db"},
            )
            row = db.all_events("sec-run")[-1]
            assert "hunter2pw" not in (row["payload"] or "")
        finally:
            db.close()

    def test_memory_table(self):
        from oporch.db import OporchDB

        db = OporchDB()
        try:
            db.remember("/p", "builder", "fact",
                        "conn string was mysql://root:supersecret99@localhost/x")
            content = db.recall("/p")[0]["content"]
            assert "supersecret99" not in content
        finally:
            db.close()

    def test_decisions_table(self):
        from oporch.db import OporchDB

        db = OporchDB()
        try:
            db.append_decision("r", "use key sk-abcdefghij0123456789abcdef?",
                               "yes")
            found = db.search_decisions("yes")[0]
            assert "sk-abcdefghij0123456789abcdef" not in found["question"]
        finally:
            db.close()

    def test_work_unit_data_blob(self):
        from oporch.constants import WorkUnitStatus
        from oporch.models import WorkUnit
        from oporch.db import OporchDB

        db = OporchDB()
        try:
            wu = WorkUnit(id="WU-S", title="t", objective="o",
                          output="password=AKIAIOSFODNN7EXAMPLE")
            db.save_work_units("run-s", [wu])
            blob = db.load_work_unit_rows("run-s")[0]["data"]
            assert "AKIAIOSFODNN7EXAMPLE" not in blob
        finally:
            db.close()


class TestRestrictedSubprocessEnv:
    def test_build_restricted_env_strips_cloud_creds(self):
        from oporch.executor import build_restricted_env

        env = build_restricted_env({
            "PATH": "/usr/bin",
            "SYSTEMROOT": "C:\\Windows",
            "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI",
            "AZURE_CLIENT_SECRET": "abc123",
            "GOOGLE_API_KEY": "xyz",
            "OPENCODE_MODEL": "custom-model",
            "SOME_RANDOM_TOKEN": "leakme",
        })
        keys = set(env.keys())
        assert "AWS_SECRET_ACCESS_KEY" not in keys
        assert "AZURE_CLIENT_SECRET" not in keys
        assert "GOOGLE_API_KEY" not in keys
        assert "SOME_RANDOM_TOKEN" not in keys
        assert "PATH" in keys
        assert "SYSTEMROOT" in keys
        assert env["OPENCODE_MODEL"] == "custom-model"

    def test_executor_uses_restricted_env_by_default(self, tmp_path):
        """Stub executable prints its env; cloud creds must be absent."""
        import sys as _sys

        if _sys.platform != "win32":
            pytest.skip("windows stub")

        stub = tmp_path / "opencode.bat"
        stub.write_text("@echo off\necho KEY=%AWS_SECRET_ACCESS_KEY%\n", encoding="utf-8")

        from oporch.executor import OpenCodeAgentExecutor
        from oporch.models import AgentTask, ContextPack
        import os

        os.environ["AWS_SECRET_ACCESS_KEY"] = "wJalrXUtnFEMIfakekey"
        try:
            ex = OpenCodeAgentExecutor()
            ex._cmd = str(stub)
            result = ex.run("builder", AgentTask(objective="o"), ContextPack())
            assert result.success
            assert "wJalrXUtnFEMIfakekey" not in result.output
        finally:
            os.environ.pop("AWS_SECRET_ACCESS_KEY", None)

    def test_sandbox_disabled_passes_full_env(self, tmp_path):
        import sys as _sys

        if _sys.platform != "win32":
            pytest.skip("windows stub")

        stub = tmp_path / "opencode.bat"
        stub.write_text("@echo off\necho %OPORCH_TEST_MARKER%\n", encoding="utf-8")
        from oporch.executor import OpenCodeAgentExecutor
        from oporch.models import AgentTask, ContextPack

        ex = OpenCodeAgentExecutor(sandbox_env=False,
                                   env={"OPORCH_TEST_MARKER": "passed"})
        ex._cmd = str(stub)
        result = ex.run("builder", AgentTask(objective="o"), ContextPack())
        assert "passed" in result.output


class TestStrictModeNoAutoMerge:
    def test_strict_parks_merge_for_approval(self, git_repo_cwd=None):
        from oporch.models import PoliciesConfig
        from oporch.constants import ApprovalMode

        p = PoliciesConfig(approval_mode=ApprovalMode.STRICT.value)
        assert p.security.strict_disables_auto_merge is True

    def test_never_auto_merge_default_includes_main_and_develop(self):
        from oporch.models import PoliciesConfig

        sec = PoliciesConfig().security.never_auto_merge_to
        assert "main" in sec and "develop" in sec and "master" in sec

    def test_git_manager_refuses_protected_branch(self, tmp_path):
        import subprocess
        from oporch.git_manager import GitManager, ProtectedBranchError

        r = subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path,
                           capture_output=True, text=True)
        assert r.returncode == 0
        gm = GitManager(repo_path=tmp_path, worktree_root=tmp_path / "_wt")
        with pytest.raises(ProtectedBranchError):
            gm.merge_integration_into_base("some-run", "main")


@pytest.fixture()
def git_repo_cwd(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path,
                   capture_output=True)
    return tmp_path
