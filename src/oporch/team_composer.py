"""Dynamic team composition (v2).

Replaces the fixed AgentRole enum-only model with a roster resolved at plan
time. ``compose_team`` asks the planner-tier agent for a roster sized to the
plan's phase count; if the agent output is unusable it falls back to a
deterministic keyword-based clustering so the pipeline never blocks.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from .models import Phase, TeamRole, TeamRoster
from .validate import default_repair, validate_agent_output

# Sizing bands per resolved PRD open question (§12): no hard ceiling, scale
# with phase count. (max_phase_inclusive, min_agents, base_max_agents)
BANDS: list[tuple[int, int, int]] = [
    (6, 3, 4),
    (12, 5, 6),
    (20, 7, 9),
]
EXTRA_PER_PHASES = 3  # beyond 20 phases: +1 agent per 3 extra phases


def sizing_band(phase_count: int) -> tuple[int, int]:
    """Return (min_agents, max_agents) for a plan of N phases."""
    if phase_count <= 0:
        return (2, 3)
    for max_phases, lo, hi in BANDS:
        if phase_count <= max_phases:
            return (lo, hi)
    extra = (phase_count - BANDS[-1][0] + EXTRA_PER_PHASES - 1) // EXTRA_PER_PHASES
    return (BANDS[-1][1], BANDS[-1][2] + extra)


CROSS_CUTTING = ("reviewer", "tester")

# Keyword -> canonical domain key used by the deterministic fallback.
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "backend": [
        "api", "endpoint", "auth", "backend", "server", "route", "handler",
        "service", "middleware", "cli", "command",
    ],
    "frontend": [
        "frontend", "ui", "css", "component", "page", "modal", "react",
        "vue", "svelte", "layout", "view", "widget", "form",
    ],
    "db": [
        "database", "db", "migration", "schema", "sql", "table", "index",
        "query", "orm", "model",
    ],
    "infra": [
        "infra", "ci", "cd", "deploy", "docker", "pipeline", "build",
        "release", "config", "environment",
    ],
    "qa": ["test", "testing", "e2e", "coverage", "qa", "regression"],
    "docs": ["doc", "docs", "documentation", "readme", "guide", "changelog"],
}

DEFAULT_MODELS = {
    "reviewer": ("nemotron-ultra", "big-pickle"),
    "tester": ("big-pickle", "deepseek-v4-flash"),
}


class TeamComposerError(Exception):
    pass


class ComposerExecutor(Protocol):
    def run(self, role: Any, task: Any, context: Any) -> Any: ...


def infer_domains(phases: list[Phase]) -> dict[str, list[int]]:
    """Map canonical domain keys to phase numbers whose text mentions them."""
    found: dict[str, list[int]] = {}
    for phase in phases:
        text = f"{phase.title}\n{phase.description or ''}".lower()
        for line in phase.acceptance_criteria:
            text += "\n" + line.lower()
        for domain, keywords in DOMAIN_KEYWORDS.items():
            if any(k in text for k in keywords):
                found.setdefault(domain, []).append(phase.number)
    return found


def _heuristic_roster(
    run_id: str,
    phases: list[Phase],
    available_models: list[str] | None = None,
) -> TeamRoster:
    """Deterministic fallback roster built from keyword clustering."""
    domains = infer_domains(phases)
    lo, hi = sizing_band(len(phases))
    primary_model = (
        "big-pickle" if (available_models and "big-pickle" in available_models)
        else (available_models or ["big-pickle"])[0]
    )

    roles: list[TeamRole] = []
    # Domain agents, largest domain groups first.
    domain_items = sorted(domains.items(), key=lambda kv: -len(kv[1]))
    for key, _phases_hit in domain_items:
        roles.append(
            TeamRole(
                key=key,
                description=f"Implements {key} work across the plan",
                model=primary_model,
                max_workers=2,
                domains=list(DOMAIN_KEYWORDS[key][:4]),
            )
        )
    if not roles:
        roles.append(
            TeamRole(
                key="builder",
                description="General implementation agent",
                model=primary_model,
                max_workers=2,
                domains=["*"],
            )
        )

    # Always keep reviewer + tester as thin cross-cutting roles.
    for key in CROSS_CUTTING:
        model, fb = DEFAULT_MODELS.get(key, (primary_model, None))
        if available_models and model not in available_models:
            model = primary_model
            fb = None
        roles.append(
            TeamRole(
                key=key,
                description=f"Cross-cutting {key} quality gate",
                model=model,
                fallback=fb,
                max_workers=2,
                domains=[],
            )
        )

    roles = _fit_to_band(roles, lo, hi)
    rationale = (
        f"Heuristic roster: {len(phases)} phases matched domains "
        f"{[r.key for r in roles]}; band {lo}-{hi} agents."
    )
    return TeamRoster(run_id=run_id, roles=roles, rationale=rationale)


def _fit_to_band(roles: list[TeamRole], lo: int, hi: int) -> list[TeamRole]:
    """Trim or pad a role list into the sizing band without dropping gates."""
    cross_keys = set(CROSS_CUTTING)
    cross = [r for r in roles if r.key in cross_keys]
    domain = [r for r in roles if r.key not in cross_keys]

    while len(cross) + len(domain) > hi and domain:
        dropped = domain.pop()  # smallest/last domain group
        merged_into = domain[-1] if domain else None
        if merged_into is not None:
            merged_into.domains.extend(dropped.domains)
    while len(cross) + len(domain) < lo:
        fallback_model = (
            (cross[0].fallback or "big-pickle") if cross else "big-pickle"
        )
        domain.append(
            TeamRole(
                key=f"builder{len(domain) + 1}" if len(domain) else "builder",
                description="Additional general implementation agent",
                model=fallback_model,
                max_workers=2,
                domains=["*"],
            )
        )
    return domain + cross


def validate_roster(roster: TeamRoster, phase_count: int | None = None) -> list[str]:
    """Post-conditions every roster must satisfy. Returns problems."""
    problems: list[str] = []
    keys = [r.key for r in roster.roles]
    if len(keys) != len(set(keys)):
        problems.append("duplicate role keys")
    for gate in CROSS_CUTTING:
        if gate not in keys:
            problems.append(f"missing required cross-cutting role '{gate}'")
    for r in roster.roles:
        if r.max_workers < 1:
            problems.append(f"role '{r.key}' has max_workers < 1")
    if phase_count is not None:
        lo, hi = sizing_band(phase_count)
        if len(roster.roles) > hi:
            problems.append(
                f"roster size {len(roster.roles)} exceeds band maximum {hi}"
            )
    return problems


def parse_roster_output(raw_text: str, run_id: str) -> TeamRoster | None:
    """Parse the composer agent's ROSTER JSON (with repair)."""
    from pydantic import BaseModel

    class _Envelope(BaseModel):
        type: str | None = None
        roles: list[TeamRole] = []
        rationale: str = ""

    result = validate_agent_output(raw_text, _Envelope, repair_fn=default_repair)
    if result.status == "failed" or result.parsed is None:
        return None
    data = result.parsed
    roles = [TeamRole(**r) for r in data.get("roles", [])]
    return TeamRoster(run_id=run_id, roles=roles, rationale=data.get("rationale", ""))


def compose_team(
    phases: list[Phase],
    repo_summary: str = "",
    run_id: str = "pending",
    executor: ComposerExecutor | None = None,
    available_models: list[str] | None = None,
    head_model: str | None = None,
) -> tuple[TeamRoster, bool]:
    """Propose a team roster for a parsed plan using the Head Supervisor Model.

    Returns ``(roster, from_agent)`` where ``from_agent`` is False when the
    deterministic fallback produced the roster.
    """
    from . import config as cfg

    active_head = head_model or cfg.get_head_model()

    if executor is not None:
        prompt = build_composer_prompt(phases, repo_summary, available_models, head_model=active_head)
        try:
            from .models import AgentTask, ContextPack

            # Resolve actual model_id for the Head Model if configured
            resolved_head_model_id = None
            try:
                mcfg = cfg.load_models()
                if active_head in mcfg.models:
                    resolved_head_model_id = mcfg.models[active_head].model_id
            except Exception:
                pass

            task = AgentTask(
                objective=f"Compose team roster for {len(phases)} phases",
                raw_prompt=prompt,
                model_override=resolved_head_model_id or active_head,
            )
            result = executor.run("supervisor", task, ContextPack())
            roster = parse_roster_output(result.output, run_id)
            if roster is not None and not validate_roster(roster, len(phases)):
                return roster, True
        except Exception:
            pass
    return _heuristic_roster(run_id, phases, available_models), False


def build_composer_prompt(
    phases: list[Phase],
    repo_summary: str,
    available_models: list[str] | None = None,
    head_model: str | None = None,
) -> str:
    from pathlib import Path
    from . import config as cfg

    prompt_path = Path(__file__).parent / "prompts" / "team_composer.md"
    template = (
        prompt_path.read_text(encoding="utf-8")
        if prompt_path.exists()
        else "You are a team composer AI. Output a ROSTER JSON object."
    )

    phase_lines = []
    for p in phases:
        criteria = "; ".join(p.acceptance_criteria[:3]) or "(none listed)"
        phase_lines.append(f"- Phase {p.number}: {p.title} — {criteria}")

    models_desc: list[str] = []
    try:
        mcfg = cfg.load_models()
        for key, m in mcfg.models.items():
            tier = getattr(m, "tier", "standard")
            provider = getattr(m, "provider", "")
            models_desc.append(f"- `{key}` (tier: {tier}, provider: {provider})")
    except Exception:
        for m in available_models or []:
            models_desc.append(f"- `{m}`")

    models_text = "\n".join(models_desc) if models_desc else "(default models)"

    replacements = {
        "{phases}": "\n".join(phase_lines) or "(no phases parsed)",
        "{repo_summary}": repo_summary or "(empty)",
        "{models}": models_text,
        "{head_model}": head_model or "nemotron-ultra",
    }
    out = template
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out

