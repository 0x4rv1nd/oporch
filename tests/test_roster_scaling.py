"""Roster auto-scaling tests (PRD §6 phase 8, §8)."""

from __future__ import annotations

import pytest

from oporch.constants import WorkUnitStatus
from oporch.db import OporchDB
from oporch.models import RosterAdjustment, RosterAutoScalePolicy
from oporch.roster_scaling import RosterScaler
from oporch.team_composer import sizing_band


def _seed(db: OporchDB, run_id: str, roles, wus):
    db.upsert_run(run_id, milestone_id="M", state="EXECUTING")
    db.save_roster(run_id, roles)
    if wus:
        db.save_work_units(run_id, wus)


ROLES = [
    {"role_key": "backend", "description": "", "model": "m",
     "max_workers": 2, "domains": ["api", "auth"]},
    {"role_key": "frontend", "description": "", "model": "m",
     "max_workers": 1, "domains": ["ui"]},
    {"role_key": "reviewer", "description": "", "model": "m",
     "max_workers": 2, "domains": []},
    {"role_key": "tester", "description": "", "model": "m",
     "max_workers": 2, "domains": []},
]


@pytest.fixture()
def env():
    import uuid

    db = OporchDB()
    run_id = "scale" + str(uuid.uuid4())[:6]
    yield db, run_id
    db.close()


class TestSuggest:
    def test_resize_up_on_starved_role(self, env):
        db, rid = env
        _seed(db, rid, ROLES, [
            {"id": f"WU-{i:03d}", "title": f"api task {i}",
             "status": "PENDING", "assigned_role": "backend", "phase": 2}
            for i in range(1, 9)
        ])
        scaler = RosterScaler(db, rid, phase_count=10,
                              policies=RosterAutoScalePolicy(enabled=True))
        suggestions = scaler.suggest_adjustments(phase_number=1)
        resizes = [s for s in suggestions
                   if s.action == "resize" and s.role_key == "backend"]
        assert resizes and resizes[0].max_workers == 3

    def test_resize_down_idle_overprovisioned_role(self, env):
        db, rid = env
        roles = [dict(r) for r in ROLES]
        roles[0]["max_workers"] = 4
        _seed(db, rid, roles, [
            # all backend work done; nothing queued anywhere
            {"id": "WU-001", "title": "done api", "status": "COMPLETED",
             "assigned_role": "backend", "phase": 1},
        ])
        scaler = RosterScaler(db, rid, phase_count=10)
        suggestions = scaler.suggest_adjustments(phase_number=1)
        downs = [s for s in suggestions
                 if s.action == "resize" and s.role_key == "backend"
                 and s.max_workers < 4]
        assert downs

    def test_retire_only_when_queue_empty_and_unneeded(self, env):
        db, rid = env
        _seed(db, rid, ROLES + [
            {"role_key": "docs", "description": "", "model": "m",
             "max_workers": 2, "domains": ["doc", "guide"]},
        ], [
            {"id": "WU-001", "title": "write guide",
             "status": "COMPLETED", "assigned_role": "docs", "phase": 1},
        ])
        scaler = RosterScaler(db, rid, phase_count=10)
        suggestions = scaler.suggest_adjustments(phase_number=1)
        retires = [s for s in suggestions if s.action == "retire"]
        assert any(s.role_key == "docs" for s in retires)

    def test_no_retire_with_pending_work(self, env):
        db, rid = env
        _seed(db, rid, ROLES, [
            {"id": "WU-001", "title": "pending ui", "status": "PENDING",
             "assigned_role": "frontend", "phase": 3},
        ])
        scaler = RosterScaler(db, rid, phase_count=10)
        suggestions = scaler.suggest_adjustments(phase_number=1)
        assert all(
            not (s.action == "retire" and s.role_key == "frontend")
            for s in suggestions
        )

    def test_spawn_for_uncovered_domain_within_budget(self, env):
        db, rid = env
        _seed(db, rid, ROLES, [
            {"id": "WU-010", "title": "docker deploy pipeline ci infra",
             "status": "PENDING", "assigned_role": "infra", "phase": 8},
        ])
        scaler = RosterScaler(db, rid, phase_count=15)  # band hi=9
        suggestions = scaler.suggest_adjustments(phase_number=5)
        spawns = [s for s in suggestions if s.action == "spawn"
                  and s.role_key == "infra"]
        assert spawns, "infra domain uncovered by seeded roster"

    def test_spawn_blocked_by_band_budget(self, env):
        db, rid = env
        many_roles = [
            {"role_key": f"r{i}", "description": "", "model": "m",
             "max_workers": 1, "domains": [f"dom{i}"]}
            for i in range(9)
        ]
        _seed(db, rid, many_roles, [])
        scaler = RosterScaler(db, rid, phase_count=12)  # hi=6
        suggestions = scaler.suggest_adjustments(phase_number=1)
        assert not any(s.action == "spawn" for s in suggestions)


class TestApply:
    def test_resize_applies_to_db_and_dispatcher(self, env):
        db, rid = env
        _seed(db, rid, ROLES, [])

        class FakeDisp:
            def resize(self, key, n):
                self.calls = getattr(self, "calls", [])
                self.calls.append((key, n))

        disp = FakeDisp()
        scaler = RosterScaler(db, rid, phase_count=10, dispatcher=disp)
        adj = scaler.apply(RosterAdjustment(
            phase_number=1, action="resize", role_key="backend",
            max_workers=3, reason="t"))
        assert adj.applied is True
        active = {r["role_key"]: r for r in db.get_roster(rid)}
        assert active["backend"]["max_workers"] == 3
        assert disp.calls == [("backend", 3)]

    def test_retire_blocks_on_in_flight_work(self, env):
        db, rid = env
        _seed(db, rid, ROLES, [
            {"id": "WU-A", "title": "running", "status": "IN_PROGRESS",
             "assigned_role": "backend", "phase": 1},
        ])
        scaler = RosterScaler(db, rid, phase_count=10)
        adj = scaler.apply(RosterAdjustment(
            phase_number=1, action="retire", role_key="backend", reason="t"))
        assert adj.applied is False
        assert "in-flight" in (adj.deferred_reason or "")

    def test_retire_never_kills_last_role(self, env):
        db, rid = env
        _seed(db, rid, [ROLES[0]], [])
        scaler = RosterScaler(db, rid, phase_count=10)
        adj = scaler.apply(RosterAdjustment(
            phase_number=1, action="retire", role_key="backend", reason="t"))
        assert adj.applied is False
        assert len(db.get_roster(rid)) == 1

    def test_spawn_parks_pending_by_default(self, env):
        db, rid = env
        _seed(db, rid, ROLES, [])
        scaler = RosterScaler(db, rid, phase_count=10)  # default policy gates spawn
        adj = scaler.apply(RosterAdjustment(
            phase_number=2, action="spawn", role_key="db",
            max_workers=2, based_on_domain="db", reason="need db"))
        assert adj.applied is False
        assert "awaiting approval" in (adj.deferred_reason or "")
        keys = db._query("SELECT key FROM control WHERE key LIKE 'roster_spawn:%'")
        assert keys

    def test_spawn_applies_when_auto_approved(self, env):
        db, rid = env
        _seed(db, rid, ROLES, [])
        scaler = RosterScaler(
            db, rid, phase_count=10,
            policies=RosterAutoScalePolicy(enabled=True,
                                           require_approval_for_spawn=False),
        )
        adj = scaler.apply(RosterAdjustment(
            phase_number=2, action="spawn", role_key="db",
            max_workers=2, based_on_domain="db", reason="need db"))
        assert adj.applied is True
        keys = {r["role_key"] for r in db.get_roster(rid)}
        assert "db" in keys

    def test_approve_pending_spawn_flow(self, env):
        db, rid = env
        _seed(db, rid, ROLES, [])
        scaler = RosterScaler(db, rid, phase_count=10)
        adj = scaler.apply(RosterAdjustment(
            phase_number=2, action="spawn", role_key="qa",
            max_workers=2, based_on_domain="qa", reason="testing needed"))
        key = adj.deferred_reason.split("'")[1]
        ok = scaler.approve_pending_spawn(key)
        assert ok is True
        assert "qa" in {r["role_key"] for r in db.get_roster(rid)}
        assert db.get_control(key) == "approved"


class TestPhaseBoundaryHook:
    def test_on_phase_complete_runs_once(self, env):
        db, rid = env
        _seed(db, rid, ROLES, [])
        scaler = RosterScaler(db, rid, phase_count=10)
        first = scaler.on_phase_complete(1)
        second = scaler.on_phase_complete(1)
        assert isinstance(first, list)
        assert second == []

    def test_sizing_bands_consistent(self):
        assert sizing_band(6) == (3, 4)
        assert sizing_band(20)[1] >= sizing_band(13)[1]


class TestPolicyDefaults:
    def test_default_disabled_and_gated(self):
        from oporch.models import PoliciesConfig

        p = PoliciesConfig()
        assert p.roster_auto_scale.enabled is False
        assert p.roster_auto_scale.require_approval_for_spawn is True
