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
