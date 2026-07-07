from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass
class DoctorResult:
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    checks: list[dict] = field(default_factory=list)

    def add_pass(self, name: str, detail: str = "") -> None:
        self.passed += 1
        self.checks.append({"name": name, "status": "PASS", "detail": detail})

    def add_fail(self, name: str, detail: str = "") -> None:
        self.failed += 1
        self.checks.append({"name": name, "status": "FAIL", "detail": detail})

    def add_warning(self, name: str, detail: str = "") -> None:
        self.warnings += 1
        self.checks.append({"name": name, "status": "WARN", "detail": detail})


def _check_cmd(name: str) -> str | None:
    return shutil.which(name)


def run_doctor() -> DoctorResult:
    result = DoctorResult()

    opencode_path = _check_cmd("opencode")
    if opencode_path:
        result.add_pass("opencode CLI", opencode_path)
    else:
        result.add_fail("opencode CLI", "Not found in PATH")

    from . import config as cfg
    if cfg.is_initialized():
        result.add_pass("orchestrator config", "Config files present")
    else:
        result.add_fail("orchestrator config", "Run 'oporch init' first")

    try:
        roles = cfg.load_roles()
        role_count = len(roles.roles)
        result.add_pass("roles config", f"{role_count} roles defined")
    except Exception as e:
        result.add_fail("roles config", str(e))

    try:
        policies = cfg.load_policies()
        result.add_pass("policies config", f"mode={policies.approval_mode}")
    except Exception as e:
        result.add_fail("policies config", str(e))

    try:
        models = cfg.load_models()
        model_count = len(models.models)
        result.add_pass("models config", f"{model_count} models defined")
    except Exception as e:
        result.add_fail("models config", str(e))

    git_path = _check_cmd("git")
    if git_path:
        result.add_pass("git", git_path)
    else:
        result.add_fail("git", "Not found in PATH")

    import os
    project_writable = os.access(".", os.W_OK)
    if project_writable:
        result.add_pass("project writable", "Yes")
    else:
        result.add_fail("project writable", "Not writable")

    import json
    opencode_configs = [
        os.path.expanduser("~/.config/opencode/opencode.json"),
        os.path.expanduser("~/.config/opencode/opencode.jsonc"),
    ]
    found_config = False
    for oc_path in opencode_configs:
        if os.path.exists(oc_path):
            found_config = True
            try:
                with open(oc_path, encoding="utf-8") as f:
                    raw = f.read()
                data = json.loads(raw)
                providers = list(data.get("provider", {}).keys())
                result.add_pass(
                    "opencode config", f"{oc_path} ({', '.join(providers)})"
                )
            except Exception as e:
                result.add_warning("opencode config", f"{oc_path}: {e}")
            break
    if not found_config:
        result.add_warning("opencode config", "Not found")

    try:
        subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        result.add_pass("pytest", "Available")
    except Exception:
        result.add_fail("pytest", "Not available")

    return result
