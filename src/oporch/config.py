from __future__ import annotations

from pathlib import Path

import yaml

from .models import (
    ModelsConfig,
    PoliciesConfig,
    RolesConfig,
)

CONFIG_DIR = Path(".opencode-orchestrator") / "config"


class ConfigError(Exception):
    pass


def load_roles() -> RolesConfig:
    path = CONFIG_DIR / "roles.yaml"
    if not path.exists():
        raise ConfigError(f"Roles config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RolesConfig(**data)


def load_policies() -> PoliciesConfig:
    path = CONFIG_DIR / "policies.yaml"
    if not path.exists():
        raise ConfigError(f"Policies config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return PoliciesConfig(**data)


def load_models() -> ModelsConfig:
    path = CONFIG_DIR / "models.yaml"
    if not path.exists():
        raise ConfigError(f"Models config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ModelsConfig(**data)


def resolve_model(role_name: str) -> str | None:
    roles = load_roles()
    if role_name not in roles.roles:
        raise ConfigError(f"Unknown role: {role_name}")

    role = roles.roles[role_name]
    try:
        mcfg = load_models()
    except Exception:
        return None

    if role.model in mcfg.models:
        return mcfg.models[role.model].model_id
    if role.fallback and role.fallback in mcfg.models:
        return mcfg.models[role.fallback].model_id
    return None


def is_initialized() -> bool:
    return (
        (CONFIG_DIR / "roles.yaml").exists()
        and (CONFIG_DIR / "policies.yaml").exists()
        and (CONFIG_DIR / "models.yaml").exists()
    )


HEAD_MODEL_FILE = CONFIG_DIR / "head_model.txt"


def get_head_model() -> str:
    """Return the active Head Supervisor model key (e.g. 'nemotron-ultra' or 'lead-reviewer')."""
    if HEAD_MODEL_FILE.exists():
        val = HEAD_MODEL_FILE.read_text(encoding="utf-8").strip()
        if val:
            return val
    try:
        roles = load_roles()
        if "supervisor" in roles.roles:
            return roles.roles["supervisor"].model
    except Exception:
        pass
    try:
        mcfg = load_models()
        for key, m in mcfg.models.items():
            if getattr(m, "tier", None) == "heavy":
                return key
        if mcfg.models:
            return next(iter(mcfg.models.keys()))
    except Exception:
        pass
    return "nemotron-ultra"


def set_head_model(model_key: str) -> None:
    """Set the active Head Supervisor model key and update roles.yaml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    HEAD_MODEL_FILE.write_text(f"{model_key.strip()}\n", encoding="utf-8")
    try:
        path = CONFIG_DIR / "roles.yaml"
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if "roles" in data and "supervisor" in data["roles"]:
                data["roles"]["supervisor"]["model"] = model_key.strip()
                path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    except Exception:
        pass


def set_role_model(role_key: str, model_key: str, fallback: str | None = None) -> None:
    """Update the assigned model and optional fallback for a role in roles.yaml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / "roles.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "roles" not in data:
            data["roles"] = {}
        if role_key not in data["roles"]:
            data["roles"][role_key] = {
                "description": f"{role_key} agent",
                "model": model_key,
                "max_workers": 2,
            }
        else:
            data["roles"][role_key]["model"] = model_key
            if fallback is not None:
                data["roles"][role_key]["fallback"] = fallback
        path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")


def list_models_summary() -> list[dict[str, Any]]:
    """Return a list of available models with metadata."""
    try:
        mcfg = load_models()
        out = []
        for key, m in mcfg.models.items():
            out.append({
                "key": key,
                "provider": getattr(m, "provider", "custom"),
                "model_id": getattr(m, "model_id", key),
                "tier": getattr(m, "tier", "standard"),
                "context_limit": getattr(m, "context_limit", 131072),
            })
        return out
    except Exception:
        return [
            {"key": "deepseek-v4-flash", "provider": "deepseek", "model_id": "opencode/deepseek-v4-flash-free", "tier": "fast"},
            {"key": "nemotron-ultra", "provider": "nvidia", "model_id": "opencode/nemotron-3-ultra-free", "tier": "heavy"},
            {"key": "mimo-v2.5", "provider": "deepseek", "model_id": "opencode/mimo-v2.5-free", "tier": "standard"},
        ]

