"""Tests for Head Supervisor Model selection and Sub-Agent Model assignment."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from oporch import config as cfg
from oporch.models import Phase, TeamRole, TeamRoster
from oporch.team_composer import compose_team, build_composer_prompt


@pytest.fixture
def temp_config_dir(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    monkeypatch.setattr(cfg, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cfg, "HEAD_MODEL_FILE", cfg_dir / "head_model.txt")

    models_data = {
        "models": {
            "fast-coder": {
                "provider": "deepseek",
                "model_id": "opencode/deepseek-v4-flash-free",
                "tier": "fast",
            },
            "lead-supervisor": {
                "provider": "nvidia",
                "model_id": "opencode/nemotron-3-ultra-free",
                "tier": "heavy",
            },
            "balanced-model": {
                "provider": "deepseek",
                "model_id": "opencode/mimo-v2.5-free",
                "tier": "standard",
            },
        }
    }
    (cfg_dir / "models.yaml").write_text(yaml.dump(models_data), encoding="utf-8")

    roles_data = {
        "roles": {
            "supervisor": {
                "description": "Supervisor",
                "model": "lead-supervisor",
                "max_workers": 1,
            },
            "builder": {
                "description": "Builder",
                "model": "fast-coder",
                "max_workers": 2,
            },
        }
    }
    (cfg_dir / "roles.yaml").write_text(yaml.dump(roles_data), encoding="utf-8")
    (cfg_dir / "policies.yaml").write_text(yaml.dump({"approval_mode": "SUPERVISED"}), encoding="utf-8")
    return cfg_dir


def test_get_and_set_head_model(temp_config_dir):
    assert cfg.get_head_model() == "lead-supervisor"

    cfg.set_head_model("fast-coder")
    assert cfg.get_head_model() == "fast-coder"

    # roles.yaml supervisor model should be updated
    roles = cfg.load_roles()
    assert roles.roles["supervisor"].model == "fast-coder"


def test_set_role_model(temp_config_dir):
    cfg.set_role_model("builder", "balanced-model", fallback="fast-coder")
    roles = cfg.load_roles()
    assert roles.roles["builder"].model == "balanced-model"
    assert roles.roles["builder"].fallback == "fast-coder"


def test_list_models_summary(temp_config_dir):
    summary = cfg.list_models_summary()
    keys = [s["key"] for s in summary]
    assert "fast-coder" in keys
    assert "lead-supervisor" in keys
    assert "balanced-model" in keys


def test_build_composer_prompt_contains_head_model_and_tiers(temp_config_dir):
    phases = [Phase(number=1, title="DB Setup", acceptance_criteria=["Create tables"])]
    prompt = build_composer_prompt(phases, "Sample repo", head_model="lead-supervisor")

    assert "lead-supervisor" in prompt
    assert "fast-coder" in prompt
    assert "tier:" in prompt


def test_compose_team_uses_head_model(temp_config_dir):
    phases = [Phase(number=1, title="Backend API", acceptance_criteria=["Add endpoints"])]
    mock_executor = MagicMock()
    mock_result = MagicMock()
    mock_result.output = """```json
{
  "type": "ROSTER",
  "roles": [
    {
      "key": "backend",
      "description": "API builder",
      "model": "fast-coder",
      "max_workers": 2,
      "domains": ["api", "auth"]
    },
    {
      "key": "reviewer",
      "description": "Quality reviewer",
      "model": "lead-supervisor",
      "max_workers": 2,
      "domains": []
    },
    {
      "key": "tester",
      "description": "Tester",
      "model": "balanced-model",
      "max_workers": 2,
      "domains": []
    }
  ],
  "rationale": "Supervisor assigned fast-coder for API and lead-supervisor for review"
}
```"""
    mock_executor.run.return_value = mock_result

    roster, from_agent = compose_team(
        phases,
        repo_summary="repo",
        run_id="run-1",
        executor=mock_executor,
        head_model="lead-supervisor",
    )

    assert from_agent is True
    # Verify task was executed with the head model
    task_arg = mock_executor.run.call_args[0][1]
    assert task_arg.model_override == "opencode/nemotron-3-ultra-free"

    # Verify sub-agent models assigned by Head Model
    role_models = {r.key: r.model for r in roster.roles}
    assert role_models["backend"] == "fast-coder"
    assert role_models["reviewer"] == "lead-supervisor"
    assert role_models["tester"] == "balanced-model"


def test_fetch_opencode_models_mocked(monkeypatch):
    sample_output = "opencode/big-pickle\nopenrouter/anthropic/claude-3-7-sonnet\nopenrouter/openai/gpt-4o\n"
    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = sample_output

    with patch("subprocess.run", return_value=mock_run):
        models = cfg.fetch_opencode_models(force_refresh=True)
        assert "opencode/big-pickle" in models
        assert "openrouter/anthropic/claude-3-7-sonnet" in models
        assert "openrouter/openai/gpt-4o" in models


def test_list_models_summary_filters(temp_config_dir, monkeypatch):
    monkeypatch.setattr(
        cfg,
        "fetch_opencode_models",
        lambda force_refresh=False: ["opencode/big-pickle", "openrouter/anthropic/claude-3-7-sonnet", "nvidia/deepseek-ai/deepseek-v4-flash"]
    )
    claude_models = cfg.list_models_summary(filter_query="claude")
    assert len(claude_models) >= 1
    assert all("claude" in m["key"].lower() or "claude" in m["model_id"].lower() for m in claude_models)


def test_resolve_model_uses_live_model_if_not_in_yaml(temp_config_dir, monkeypatch):
    monkeypatch.setattr(
        cfg,
        "fetch_opencode_models",
        lambda force_refresh=False: ["openrouter/anthropic/claude-3-7-sonnet"]
    )
    cfg.set_role_model("builder", "openrouter/anthropic/claude-3-7-sonnet")
    resolved = cfg.resolve_model("builder")
    assert resolved == "openrouter/anthropic/claude-3-7-sonnet"

