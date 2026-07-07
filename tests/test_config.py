import pytest

from oporch import config as cfg
from oporch.constants import SCHEMA_VERSION
from oporch.run_state import _check_schema_version, RunStateError


class TestConfig:
    def test_load_roles(self):
        roles = cfg.load_roles()
        assert "orchestrator" in roles.roles
        assert "builder" in roles.roles
        assert "reviewer" in roles.roles
        assert "tester" in roles.roles

    def test_load_policies(self):
        policies = cfg.load_policies()
        assert policies.approval_mode in ("AUTONOMOUS", "SUPERVISED", "STRICT")
        assert policies.retry.max_attempts == 3

    def test_load_models(self):
        models = cfg.load_models()
        assert "deepseek-v4-flash" in models.models
        assert "nemotron-ultra" in models.models

    def test_resolve_model(self):
        model = cfg.resolve_model("orchestrator")
        assert isinstance(model, str)
        assert len(model) > 0

    def test_resolve_model_unknown_role_raises(self):
        with pytest.raises(cfg.ConfigError):
            cfg.resolve_model("nonexistent_role")

    def test_schema_version_constant(self):
        assert SCHEMA_VERSION == 1

    def test_schema_version_accepts_correct(self):
        _check_schema_version({"schema_version": 1}, "test")

    def test_schema_version_rejects_wrong(self):
        with pytest.raises(RunStateError, match="schema_version 0, expected 1"):
            _check_schema_version({"key": "value"}, "test")

    def _temp_config(self, roles_data: dict, models_data: dict) -> str:
        import tempfile, yaml
        from pathlib import Path

        orig_dir = cfg.CONFIG_DIR
        tmp = tempfile.mkdtemp()
        cfg.CONFIG_DIR = Path(tmp)
        (cfg.CONFIG_DIR / "roles.yaml").write_text(yaml.dump(roles_data), encoding="utf-8")
        (cfg.CONFIG_DIR / "models.yaml").write_text(yaml.dump(models_data), encoding="utf-8")
        return orig_dir

    def test_resolve_model_returns_none_when_unresolved(self):
        orig_dir = self._temp_config(
            {"roles": {"ghost": {"description": "x", "model": "no-such-model", "max_workers": 1}}},
            {"models": {"other-model": {"provider": "t", "model_id": "t/test", "context_limit": 1, "output_limit": 1}}},
        )
        try:
            result = cfg.resolve_model("ghost")
            assert result is None
        finally:
            cfg.CONFIG_DIR = orig_dir

    def test_resolve_model_with_fallback(self):
        orig_dir = self._temp_config(
            {
                "roles": {
                    "test_role": {
                        "description": "test",
                        "model": "nonexistent",
                        "fallback": "real-model",
                        "max_workers": 1,
                    }
                }
            },
            {"models": {"real-model": {"provider": "t", "model_id": "t/real", "context_limit": 1, "output_limit": 1}}},
        )
        try:
            result = cfg.resolve_model("test_role")
            assert result == "t/real"
        finally:
            cfg.CONFIG_DIR = orig_dir

    def test_is_initialized(self):
        assert cfg.is_initialized()
