"""Phase-boundary roster auto-scaling (v2, PRD §8).

After each completed PHASE (not per-WU, to avoid thrashing) the scaler
re-evaluates the roster against the domains of remaining phases:

- ``resize``  — widen/narrow a role's semaphore budget (auto-applies)
- ``retire``  — drop an idle role whose WU queue is empty (auto-applies,
                never the last role)
- ``spawn``   — add a role for an uncovered domain (gated: parked as a
                pending approval unless policy auto-approves spawns)

Guardrails: roster stays within the sizing band for the plan's phase
count; at least one role always remains; retire never kills a role with
queued/in-flight work.
"""

from __future__ import annotations

from typing import Any

from .constants import EventType, WorkUnitStatus
from .db import OporchDB
from .models import RosterAdjustment, RosterAutoScalePolicy
from .team_composer import DOMAIN_KEYWORDS, sizing_band


class RosterScaler:
    def __init__(
        self,
        db: OporchDB,
        run_id: str,
        phase_count: int,
        policies: RosterAutoScalePolicy | None = None,
        dispatcher: Any | None = None,
        event_log: Any | None = None,
    ) -> None:
        self.db = db
        self.run_id = run_id
        self.phase_count = max(1, phase_count)
        self.policies = policies or RosterAutoScalePolicy()
        self.dispatcher = dispatcher
        self.event_log = event_log
        self.checked_phases: set[int] = set()

    # ------------------------------------------------------------------
    def active_roles(self) -> list[dict[str, Any]]:
        return self.db.get_roster(self.run_id)

    def wu_queue_stats(self) -> dict[str, dict[str, int]]:
        """Per assigned_role counts of pending vs in-flight work units."""
        rows = self.db.load_work_unit_rows(self.run_id)
        stats: dict[str, dict[str, int]] = {}
        for r in rows:
            role = r.get("assigned_role") or "builder"
            s = stats.setdefault(role, {"pending": 0, "active": 0, "done": 0})
            status = r.get("status")
            if status in ("PENDING", "READY", "BLOCKED"):
                s["pending"] += 1
            elif status == "IN_PROGRESS":
                s["active"] += 1
            elif status == "COMPLETED":
                s["done"] += 1
        return stats

    def remaining_domains(self, completed_phase: int) -> dict[str, list[int]]:
        """Domains still needed by phases AFTER ``completed_phase``."""
        from .team_composer import infer_domains

        rows = [
            r for r in self.db.load_work_unit_rows(self.run_id)
            if (r.get("phase") or 0) > completed_phase
        ]
        titles_by_phase: dict[int, str] = {}
        for r in rows:
            ph = r.get("phase") or 0
            titles_by_phase.setdefault(ph, "")
            titles_by_phase[ph] += f" {r.get('title') or ''}"
        pseudo = []
        for num, title in titles_by_phase.items():
            pseudo.append(type("P", (), {
                "number": num, "title": title,
                "description": "", "acceptance_criteria": [],
            })())
        return infer_domains(pseudo)

    # ------------------------------------------------------------------
    # suggestion
    # ------------------------------------------------------------------
    def suggest_adjustments(self, phase_number: int) -> list[RosterAdjustment]:
        roles = self.active_roles()
        stats = self.wu_queue_stats()
        _, hi = sizing_band(self.phase_count)
        suggestions: list[RosterAdjustment] = []

        covered_domains = {d for r in roles for d in (r.get("domains") or [])}
        needed = self.remaining_domains(phase_number)

        # SPAWN: a needed domain no active role covers.
        for domain, phases_hit in sorted(needed.items()):
            if domain in covered_domains or any(
                domain in (r.get("domains") or []) for r in roles
            ):
                continue
            if len(roles) >= hi:
                break  # budget guard: redistribute only within the band
            keywords = DOMAIN_KEYWORDS.get(domain, [])[:3]
            if any(k in covered_domains for k in keywords):
                continue
            suggestions.append(
                RosterAdjustment(
                    phase_number=phase_number,
                    action="spawn",
                    role_key=domain,
                    max_workers=2,
                    based_on_domain=domain,
                    reason=f"phases {phases_hit} need '{domain}' work; "
                           f"no active role covers it",
                )
            )
            roles.append({
                "role_key": domain, "max_workers": 2, "domains": keywords,
            })

        # RESIZE up: starved role with more ready work than capacity.
        for r in roles:
            key = r["role_key"]
            s = stats.get(key)
            if not s:
                continue
            workers = int(r.get("max_workers") or 1)
            backlog = s["pending"] + s["active"]
            if backlog >= workers * 3 and workers < 4 and len(roles) <= hi:
                suggestions.append(
                    RosterAdjustment(
                        phase_number=phase_number,
                        action="resize",
                        role_key=key,
                        max_workers=min(workers + 1, 4),
                        reason=f"{backlog} queued/active vs {workers} workers",
                    )
                )
            elif (
                s["pending"] == 0
                and s["active"] == 0
                and workers > 1
            ):
                suggestions.append(
                    RosterAdjustment(
                        phase_number=phase_number,
                        action="resize",
                        role_key=key,
                        max_workers=1,
                        reason=f"idle role with {workers} workers; narrowing",
                    )
                )

        # RETIRE: fully idle role with nothing left to do.
        for r in list(roles):
            key = r["role_key"]
            if key in ("reviewer", "tester", "supervisor"):
                continue
            s = stats.get(key, {"pending": 0, "active": 0})
            if s["pending"] == 0 and s["active"] == 0 and len(roles) > 1:
                # Only retire if remaining phases don't need this domain.
                domains = set(r.get("domains") or []) | {key}
                still_needed = any(nd in domains for nd in needed)
                if not still_needed:
                    suggestions.append(
                        RosterAdjustment(
                            phase_number=phase_number,
                            action="retire",
                            role_key=key,
                            reason="queue empty and no remaining phases "
                                   "need this role",
                        )
                    )
                    roles.remove(r)

        return suggestions

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------
    def apply(self, adj: RosterAdjustment) -> RosterAdjustment:
        roles = self.active_roles()

        if adj.action == "resize":
            ok = self.db.resize_role(self.run_id, adj.role_key, adj.max_workers)
            if ok and self.dispatcher is not None:
                try:
                    self.dispatcher.resize(adj.role_key, adj.max_workers)
                except Exception:
                    pass
            adj.applied = ok

        elif adj.action == "retire":
            stats = self.wu_queue_stats().get(adj.role_key, {})
            if stats.get("active", 0) > 0:
                adj.deferred_reason = "role has in-flight work"
            elif len([r for r in roles if not r.get("active_until")]) <= 1:
                adj.deferred_reason = "cannot retire the last role"
            else:
                adj.applied = self.db.retire_role(self.run_id, adj.role_key)

        elif adj.action == "spawn":
            if self.policies.require_approval_for_spawn:
                key = f"roster_spawn:{self.run_id}:{adj.role_key}"
                payload = f"{adj.max_workers}|{adj.based_on_domain}|{adj.reason}"
                self.db.set_control(key, f"pending:{payload}")
                adj.deferred_reason = f"awaiting approval via control key '{key}'"
            else:
                adj.applied = self._spawn_now(adj)

        self.log_event(adj)
        return adj

    def _spawn_now(self, adj: RosterAdjustment) -> bool:
        try:
            self.db.save_roster(
                self.run_id,
                [{
                    "role_key": adj.role_key,
                    "description": f"spawned at phase {adj.phase_number}",
                    "model": "deepseek-v4-flash",
                    "max_workers": adj.max_workers or 2,
                    "domains": DOMAIN_KEYWORDS.get(
                        adj.based_on_domain or "", []
                    )[:4],
                }],
                rationale=f"auto-scale spawn at phase {adj.phase_number}: {adj.reason}",
            )
            if self.dispatcher is not None and adj.max_workers:
                self.dispatcher.resize(adj.role_key, adj.max_workers)
            return True
        except Exception:
            return False

    def approve_pending_spawn(self, control_key: str) -> bool:
        """Resolve a parked spawn approval created by :meth:`apply`."""
        value = self.db.get_control(control_key)
        if not value or not value.startswith("pending:"):
            return False
        parts = control_key.split(":")
        if len(parts) != 3 or parts[0] != "roster_spawn":
            return False
        run_id, role_key = parts[1], parts[2]
        payload = value[len("pending:"):]
        fields = (payload.split("|") + ["", "", ""])[:3]
        workers, domain, reason = fields
        self.db.set_control(control_key, "approved")
        return self._spawn_now(
            RosterAdjustment(
                phase_number=-1,
                action="spawn",
                role_key=role_key,
                max_workers=int(workers),
                based_on_domain=domain,
                reason=reason,
            )
        )

    # ------------------------------------------------------------------
    def on_phase_complete(self, phase_number: int) -> list[RosterAdjustment]:
        """Entry point called once per completed phase boundary."""
        if phase_number in self.checked_phases:
            return []
        self.checked_phases.add(phase_number)
        out = []
        for adj in self.suggest_adjustments(phase_number):
            out.append(self.apply(adj))
        return out

    def log_event(self, adj: RosterAdjustment) -> None:
        if self.event_log is None:
            return
        try:
            self.event_log.record(
                EventType.ROSTER_ADJUSTED,
                details={
                    "phase": adj.phase_number,
                    "action": adj.action,
                    "role": adj.role_key,
                    "max_workers": adj.max_workers,
                    "applied": adj.applied,
                    "deferred": adj.deferred_reason,
                    "reason": adj.reason,
                },
            )
        except Exception:
            pass
