"""Supervisor intelligence (v2, PRD §11).

Three responsibilities beyond the merge gate:

- **11a dynamic model selection**: score per-WU complexity from signals
  already on the task, pick a concrete model within the role's configured
  tier range, de-scope to lighter tiers once the run crosses its soft token
  budget. Every decision is logged as a MODEL_SELECTED event.
- **11c scoped file access**: validate an agent's changed-file list against
  its role's allowed_paths globs before results are accepted.
- **11b self-healing**: resolve a recovery strategy for failed attempts
  (retry → model bump → narrower scope → debugger prefix → rollback and
  reassign), capped by ``self_heal.max_strategies``, then escalate.
- **run-level health checks**: phase-boundary scan for rising failure
  rates, starved roles with ready work, and runaway worktree disk usage.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from .constants import EventType
from .models import PoliciesConfig, WorkUnit

TIER_ORDER = ("fast", "standard", "heavy")


# ---------------------------------------------------------------------------
# §11a model selection
# ---------------------------------------------------------------------------

@dataclass
class ModelDecision:
    model_id: str | None
    tier: str | None
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)


def complexity_signals(wu: WorkUnit) -> dict[str, Any]:
    """Cheap signals available without any agent call."""
    text = f"{wu.title} {wu.objective}".lower()
    return {
        "criteria_count": len(wu.acceptance_criteria),
        "files_count": len(wu.files_likely_affected),
        "attempt": wu.attempts,
        "high_risk_domain": any(
            d in text or d == (wu.assigned_role or "")
            for d in ("db", "migration", "auth", "security", "infra")
        ),
    }


class Supervisor:
    def __init__(
        self,
        policies: PoliciesConfig,
        roles_cfg=None,
        models_cfg=None,
        event_log: Any | None = None,
        db: Any | None = None,
        run_id: str = "",
    ) -> None:
        self.policies = policies
        self.roles_cfg = roles_cfg
        self.models_cfg = models_cfg
        self.event_log = event_log
        self.db = db
        self.run_id = run_id

    # -- helpers -----------------------------------------------------------
    def _role_tier_range(self, role_key: str) -> tuple[str, str] | None:
        if self.roles_cfg is None:
            return None
        cfg_role = self.roles_cfg.roles.get(role_key)
        if cfg_role is None or cfg_role.tier_min is None:
            return None
        lo = cfg_role.tier_min
        hi = cfg_role.tier_max or cfg_role.tier_min
        return (lo, hi)

    def _models_by_tier(self) -> dict[str, list[tuple[str, Any]]]:
        out: dict[str, list[tuple[str, Any]]] = {t: [] for t in TIER_ORDER}
        if self.models_cfg is None:
            return out
        for key, info in self.models_cfg.models.items():
            tier = getattr(info, "tier", None) or "standard"
            out.setdefault(tier, []).append((key, info))
        return out

    def _run_tokens_spent(self) -> int:
        if self.db is None or not self.run_id:
            return 0
        try:
            rows = self.db._query(
                "SELECT COALESCE(SUM(tokens_in),0)+COALESCE(SUM(tokens_out),0)"
                " AS n FROM events WHERE run_id = ?",
                (self.run_id,),
            )
            return int(rows[0]["n"] or 0)
        except Exception:
            return 0

    def _log_decision(self, wu: WorkUnit, decision: ModelDecision) -> None:
        if self.event_log is None:
            return
        try:
            self.event_log.record(
                EventType.MODEL_SELECTED,
                work_unit_id=wu.id,
                agent_role=wu.assigned_role,
                details={
                    "model": decision.model_id,
                    "tier": decision.tier,
                    "reason": decision.reason,
                    **decision.signals,
                },
            )
        except Exception:
            pass

    # -- public ------------------------------------------------------------
    def select_model(self, wu: WorkUnit) -> ModelDecision:
        """Pick the concrete model_id for this WU dispatch.

        Falls back to ``None`` (executor uses the role's static mapping)
        whenever tiers are unconfigured or resolution fails — never blocks.
        """
        signals = complexity_signals(wu)
        role_key = wu.assigned_role or "builder"
        tier_range = self._role_tier_range(role_key)

        if tier_range is None:
            decision = ModelDecision(
                model_id=None, tier=None,
                reason="role has no tier range; using static mapping",
                signals=signals,
            )
            self._log_decision(wu, decision)
            return decision

        lo_i = TIER_ORDER.index(tier_range[0])
        hi_i = TIER_ORDER.index(tier_range[1])
        target = hi_i

        reasons: list[str] = []
        if signals["attempt"] >= 2:
            target = min(target + 1, len(TIER_ORDER) - 1)
            reasons.append(f"retry attempt {signals['attempt']} bumps a tier")
        if signals["high_risk_domain"] and (
            any(d in self.policies.high_risk_domains for d in
                ["db", "auth", "migration", "infra", "db_migration",
                 "security"])
        ):
            target = min(max(target, lo_i + 1), len(TIER_ORDER) - 1)
            reasons.append("high blast-radius domain bumps a tier")
        if signals["criteria_count"] >= 5:
            reasons.append(f"{signals['criteria_count']} acceptance criteria")

        # Budget guard: below soft limit only heavy stays eligible;
        # past it, cap at standard unless already bumped for risk/retry.
        spent = self._run_tokens_spent()
        over_budget = spent > self.policies.model_budget_soft_limit
        if over_budget and TIER_ORDER[target] == "heavy" and not reasons:
            target = TIER_ORDER.index("standard")
            reasons.append("soft token budget exceeded; demoting heavy tier")
        signals["tokens_spent"] = spent

        # Clamp into the role's allowed band.
        target = max(lo_i, min(hi_i, target)) if not (
            over_budget and target > hi_i
        ) else hi_i
        tier_name = TIER_ORDER[target]

        candidates = self._models_by_tier().get(tier_name, [])
        model_id = None
        chosen_key = None
        if candidates:
            chosen_key, _info = candidates[0]
            try:
                model_id = self.models_cfg.models[chosen_key].model_id
            except Exception:
                model_id = None

        if model_id is None:
            reason = (
                f"no '{tier_name}'-tier model in models.yaml; "
                "falling back to static mapping"
            )
        else:
            reason = "; ".join(reasons) or f"baseline tier for {role_key}"

        decision = ModelDecision(
            model_id=model_id, tier=tier_name, reason=reason, signals=signals,
        )
        self._log_decision(wu, decision)
        return decision


# ---------------------------------------------------------------------------
# §11c scoped file access
# ---------------------------------------------------------------------------

DEFAULT_PATH_GLOBS: dict[str, list[str]] = {
    "frontend": ["src/frontend/**", "**/*.tsx", "**/*.css"],
    "ui": ["src/frontend/**", "**/*.css"],
    "db": ["migrations/**", "src/db/**", "**/migrations/**"],
    "db_migration": ["migrations/**", "src/db/**", "**/migrations/**"],
    "infra": [".github/**", "deploy/**", "**/Dockerfile*", "**/*.tf"],
    "docs": ["docs/**", "**/*.md"],
    "qa": ["tests/**"],
}
CROSS_CUTTING_ROLES = {"reviewer", "tester", "supervisor", "debugger",
                       "orchestrator", "planner"}


def compile_glob(pattern: str) -> str:
    """Normalize a glob so fnmatch handles ``**`` sanely."""
    p = pattern.replace("\\\\", "/").lstrip("./")
    if p.endswith("/**"):
        return p + "/*"
    return p


def paths_allowed(
    changed_files: list[str],
    patterns: list[str] | None,
) -> tuple[bool, list[str]]:
    """Return (allowed, violating_files).

    ``patterns`` empty/None means unrestricted. Matching is case-insensitive
    on Windows-friendly paths; ``**/`` prefixes match at any depth.
    """
    if not patterns:
        return True, []
    compiled = [compile_glob(p).lower() for p in patterns]
    violations = []
    for f in changed_files:
        norm = f.replace("\\\\", "/").lstrip("./").lower()
        if not any(fnmatch.fnmatch(norm, pat) for pat in compiled):
            violations.append(f)
    return (not violations), violations


def default_globs_for(role_key: str) -> list[str] | None:
    if role_key in CROSS_CUTTING_ROLES:
        return None  # cross-cutting roles see everything by design
    return DEFAULT_PATH_GLOBS.get(role_key)


# ---------------------------------------------------------------------------
# §11b self-healing ladder
# ---------------------------------------------------------------------------

RECOVERY_LADDER = (
    "retry_same_model",
    "retry_with_model_bump",
    "retry_with_narrower_scope",
    "retry_with_debugger_prefix",
    "rollback_and_reassign",
)


@dataclass
class RecoveryPlan:
    strategy: str          # ladder entry or "escalate"
    attempt: int
    bump_model: bool = False
    debugger_prefix: bool = False
    fresh_worktree: bool = False
    split_scope: bool = False
    reason: str = ""


class SelfHealer:
    """Resolve which recovery strategy applies to the next attempt."""

    def __init__(self, policies: PoliciesConfig) -> None:
        self.enabled = policies.self_heal.enabled
        self.max_strategies = max(1, policies.self_heal.max_strategies)

    def plan_next_attempt(
        self,
        wu: WorkUnit,
        last_failure_stage: str | None,
    ) -> RecoveryPlan:
        """Map the just-failed attempt onto the next ladder rung."""
        next_attempt = wu.attempts + 1
        if not self.enabled:
            # Legacy fixed ladder semantics.
            if last_failure_stage == "review":
                return RecoveryPlan(next_attempt, "review_feedback",
                                    reason="policy: attempt N gets review notes")
            if next_attempt >= 3:
                return RecoveryPlan(next_attempt, "debugger_prefix",
                                    reason="policy: attempt 3+ debugs first")
            return RecoveryPlan(next_attempt, "retry_same_model")

        strategies_used = max(0, wu.attempts - 1)
        if strategies_used >= self.max_strategies:
            return RecoveryPlan(
                next_attempt, "escalate",
                reason=f"{strategies_used} strategies tried "
                       f"(cap {self.max_strategies}); escalating to user",
            )
        idx = min(strategies_used, len(RECOVERY_LADDER) - 1)
        strategy = RECOVERY_LADDER[idx]
        plan = RecoveryPlan(
            attempt=next_attempt,
            strategy=strategy,
            bump_model=strategy in ("retry_with_model_bump",),
            debugger_prefix=strategy == "retry_with_debugger_prefix",
            fresh_worktree=strategy == "rollback_and_reassign",
            split_scope=strategy == "retry_with_narrower_scope",
            reason=f"self-heal rung {idx + 1}/{len(RECOVERY_LADDER)} after "
                   f"{last_failure_stage or 'build'} failure",
        )
        return plan


# ---------------------------------------------------------------------------
# run-level health monitoring (§11b)
# ---------------------------------------------------------------------------

@dataclass
class HealthReport:
    healthy: bool
    findings: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)


def check_run_health(
    *,
    failure_rate_recent: float,
    idle_starved_roles: list[str],
    worktree_disk_mb: float,
    disk_limit_mb: float = 2048.0,
    failure_rate_threshold: float = 0.5,
    min_completed: int = 4,
    completed_recent: int = 0,
) -> HealthReport:
    findings: list[str] = []
    actions: list[str] = []

    if completed_recent >= min_completed and (
        failure_rate_recent > failure_rate_threshold
    ):
        findings.append(
            f"failure rate {failure_rate_recent:.0%} over last "
            f"{completed_recent} completed WUs exceeds "
            f"{failure_rate_threshold:.0%}"
        )
        actions.append("escalate_run_health")

    for role in idle_starved_roles:
        findings.append(f"role '{role}' idle while WUs await it (deadlock smell)")
        actions.append(f"resize:{role}")

    if worktree_disk_mb > disk_limit_mb:
        findings.append(
            f"worktrees use {worktree_disk_mb:.0f}MB (> {disk_limit_mb:.0f}MB)"
        )
        actions.append("cleanup_worktrees")

    return HealthReport(
        healthy=not findings, findings=findings, actions=actions,
    )
