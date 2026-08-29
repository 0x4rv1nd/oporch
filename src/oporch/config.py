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


import shutil
import subprocess
import time
from typing import Any

_CACHED_OPENCODE_MODELS: list[str] = []
_CACHED_OPENCODE_MODELS_TIME: float = 0.0
_OPENCODE_MODELS_CACHE_TTL = 300.0  # 5 minutes


def fetch_opencode_models(force_refresh: bool = False) -> list[str]:
    """Fetch live list of available models from `opencode models` CLI."""
    global _CACHED_OPENCODE_MODELS, _CACHED_OPENCODE_MODELS_TIME
    now = time.time()
    if not force_refresh and _CACHED_OPENCODE_MODELS and (now - _CACHED_OPENCODE_MODELS_TIME) < _OPENCODE_MODELS_CACHE_TTL:
        return _CACHED_OPENCODE_MODELS

    opencode_bin = shutil.which("opencode") or "opencode"
    try:
        # On Windows, run with shell=True or executable path
        res = subprocess.run(
            [opencode_bin, "models"],
            capture_output=True,
            text=True,
            timeout=8,
            shell=True if shutil.which("opencode") is None or not shutil.which("opencode").endswith(".exe") else False,
        )
        if res.returncode == 0 and res.stdout.strip():
            models = [line.strip() for line in res.stdout.splitlines() if line.strip() and not line.strip().startswith("#")]
            if models:
                _CACHED_OPENCODE_MODELS = models
                _CACHED_OPENCODE_MODELS_TIME = now
                return models
    except Exception:
        pass

    # Fallback to known models if opencode CLI call fails
    fallback = [
        "opencode/big-pickle",
        "opencode/hy3-free",
        "opencode/mimo-v2.5-free",
        "opencode/nemotron-3-ultra-free",
        "opencode/nemotron-3.5-lightning-free",
        "nvidia/deepseek-ai/deepseek-v4-flash",
        "openrouter/anthropic/claude-3-7-sonnet",
        "openrouter/openai/gpt-4o",
        "openrouter/openai/o3-mini",
    ]
    if not _CACHED_OPENCODE_MODELS:
        _CACHED_OPENCODE_MODELS = fallback
        _CACHED_OPENCODE_MODELS_TIME = now
    return _CACHED_OPENCODE_MODELS


def _infer_tier(model_name: str) -> str:
    """Infer model tier (fast, standard, heavy) from model name patterns."""
    lower = model_name.lower()
    heavy_signals = ("ultra", "opus", "sonnet", "large", "max", "pro", "r1", "o1", "o3", "405b", "70b", "lead-reviewer")
    fast_signals = ("flash", "mini", "nano", "small", "spark", "haiku", "8b", "7b", "free", "light", "fast")
    if any(s in lower for s in heavy_signals):
        return "heavy"
    if any(s in lower for s in fast_signals):
        return "fast"
    return "standard"


def _infer_provider(model_name: str) -> str:
    """Infer provider name from model ID."""
    if "/" in model_name:
        return model_name.split("/")[0]
    return "custom"


def resolve_model(role_name: str) -> str | None:
    roles = load_roles()
    if role_name not in roles.roles:
        raise ConfigError(f"Unknown role: {role_name}")

    role = roles.roles[role_name]
    try:
        mcfg = load_models()
    except Exception:
        mcfg = None

    if mcfg and role.model in mcfg.models:
        return mcfg.models[role.model].model_id
    if mcfg and role.fallback and role.fallback in mcfg.models:
        return mcfg.models[role.fallback].model_id

    # If role.model is directly a full model_id from opencode models, return it
    live_models = fetch_opencode_models()
    if role.model in live_models:
        return role.model
    if role.fallback and role.fallback in live_models:
        return role.fallback

    return None



def is_initialized() -> bool:
    return (
        (CONFIG_DIR / "roles.yaml").exists()
        and (CONFIG_DIR / "policies.yaml").exists()
        and (CONFIG_DIR / "models.yaml").exists()
    )


HEAD_MODEL_FILE = CONFIG_DIR / "head_model.txt"


def get_head_model() -> str:
    """Return the active Head Supervisor model key (e.g. 'opencode/big-pickle', 'big-pickle')."""
    if HEAD_MODEL_FILE.exists():
        val = HEAD_MODEL_FILE.read_text(encoding="utf-8").strip()
        if val:
            return val
    try:
        roles = load_roles()
        if "supervisor" in roles.roles and roles.roles["supervisor"].model:
            return roles.roles["supervisor"].model
    except Exception:
        pass
    try:
        mcfg = load_models()
        if "big-pickle" in mcfg.models:
            return "big-pickle"
        for key, m in mcfg.models.items():
            if getattr(m, "tier", None) == "heavy":
                return key
        if mcfg.models:
            return next(iter(mcfg.models.keys()))
    except Exception:
        pass
    return "big-pickle"


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


def list_models_summary(filter_query: str | None = None) -> list[dict[str, Any]]:
    """Return a list of available models combining configured models and live `opencode models`."""
    seen_model_ids: set[str] = set()
    out: list[dict[str, Any]] = []

    # 1. Configured models from models.yaml (first priority)
    try:
        mcfg = load_models()
        for key, m in mcfg.models.items():
            mid = getattr(m, "model_id", key)
            seen_model_ids.add(mid)
            seen_model_ids.add(key)
            out.append({
                "key": key,
                "provider": getattr(m, "provider", _infer_provider(mid)),
                "model_id": mid,
                "tier": getattr(m, "tier", _infer_tier(mid)),
                "context_limit": getattr(m, "context_limit", 131072),
                "is_configured": True,
            })
    except Exception:
        pass

    # 2. Live models fetched from `opencode models` CLI
    live_models = fetch_opencode_models()
    for mid in live_models:
        if mid not in seen_model_ids:
            seen_model_ids.add(mid)
            out.append({
                "key": mid,
                "provider": _infer_provider(mid),
                "model_id": mid,
                "tier": _infer_tier(mid),
                "context_limit": 131072,
                "is_configured": False,
            })

    # Apply search filter if provided
    if filter_query:
        q = filter_query.strip().lower()
        out = [m for m in out if q in m["key"].lower() or q in m["model_id"].lower() or q in m["provider"].lower()]

    return out



