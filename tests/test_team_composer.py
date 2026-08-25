import pytest

from oporch.constants import OrchestratorState
from oporch.models import AgentTask, ContextPack, Phase, TeamRoster
from oporch.team_composer import (
    CROSS_CUTTING,
    _fit_to_band,
    compose_team,
    infer_domains,
    parse_roster_output,
    sizing_band,
    validate_roster,
)


def make_phases(n: int, domain_hint: str = "api endpoint") -> list[Phase]:
    return [
        Phase(number=i + 1, title=f"Phase {i + 1}: {domain_hint} work")
        for i in range(n)
    ]


class TestSizingBand:
    def test_short_plans(self):
        assert sizing_band(1) == (3, 4)
        assert sizing_band(6) == (3, 4)

    def test_medium_plans(self):
        assert sizing_band(7) == (5, 6)
        assert sizing_band(12) == (5, 6)

    def test_large_plans(self):
        assert sizing_band(13) == (7, 9)
        assert sizing_band(20) == (7, 9)

    def test_no_hard_ceiling(self):
        lo, hi = sizing_band(29)
        assert hi > 9  # 9 extra phases -> +3 agents
        lo2, hi2 = sizing_band(100)
        assert hi2 > hi

    def test_zero_phases(self):
        lo, hi = sizing_band(0)
        assert lo >= 2


class TestInferDomains:
    def test_backend_keywords(self):
        phases = [Phase(number=1, title="Add auth API endpoints")]
        domains = infer_domains(phases)
        assert "backend" in domains
        assert domains["backend"] == [1]

    def test_frontend_and_db(self):
        phases = [
            Phase(number=1, title="Build modal component CSS"),
            Phase(number=2, title="Create migration schema table"),
        ]
        domains = infer_domains(phases)
        assert "frontend" in domains
        assert "db" in domains
        assert domains["frontend"] == [1]
        assert domains["db"] == [2]

    def test_acceptance_criteria_scanned(self):
        phases = Phase(
            number=1, title="Misc", acceptance_criteria=["docker pipeline passes"]
        ).model_copy(deep=True), 
        domains = infer_domains(list(phases))
        assert "infra" in domains


class TestHeuristicComposer:
    def test_always_includes_gates(self):
        roster, from_agent = compose_team(make_phases(5))
        assert from_agent is False
        keys = roster.role_keys()
        for gate in CROSS_CUTTING:
            assert gate in keys

    def test_respects_sizing_band(self):
        for n in (3, 8, 15, 25):
            roster, _ = compose_team(make_phases(n))
            lo, hi = sizing_band(n)
            assert lo <= len(roster.roles) <= hi, f"n={n}"

    def test_domains_routed(self):
        phases = [
            Phase(number=1, title="API endpoints and auth handlers"),
            Phase(number=2, title="UI modal components"),
            Phase(number=3, title="Database migration schema"),
            Phase(number=4, title="CI deploy pipeline"),
            Phase(number=5, title="Docs guide update"),
        ]
        roster, _ = compose_team(phases)
        keys = roster.role_keys()
        assert "backend" in keys or "builder" in keys
        # reviewer/tester never carry domains
        for r in roster.roles:
            if r.key in CROSS_CUTTING:
                assert r.domains == []


class TestFitToBand:
    def test_trim_overfull(self):
        roles = []
        for i in range(10):
            from oporch.models import TeamRole

            roles.append(TeamRole(key=f"d{i}", description="", model="m", max_workers=1, domains=[str(i)]))
        from oporch.models import TeamRole

        roles += [
            TeamRole(key=g, description="", model="m", max_workers=2, domains=[])
            for g in CROSS_CUTTING
        ]
        fitted = _fit_to_band(roles, 5, 6)
        assert len(fitted) <= 6
        assert {g for g in CROSS_CUTTING} <= {r.key for r in fitted}

    def test_pad_underfull(self):
        from oporch.models import TeamRole

        roles = [
            TeamRole(key=g, description="", model="m", max_workers=2, domains=[])
            for g in CROSS_CUTTING
        ]
        fitted = _fit_to_band(roles, 4, 6)
        assert len(fitted) >= 4


class TestParseRosterOutput:
    ROSTER_JSON = """{
      "type": "ROSTER",
      "roles": [
        {"key": "backend", "description": "b", "model": "deepseek-v4-flash",
         "fallback": null, "max_workers": 2, "domains": ["api"]},
        {"key": "reviewer", "description": "gate", "model": "nemotron-ultra",
         "max_workers": 2, "domains": []},
        {"key": "tester", "description": "gate", "model": "nemotron-ultra",
         "max_workers": 2, "domains": []}
      ],
      "rationale": "small backend-only plan"
    }"""

    def test_parse_valid(self):
        roster = parse_roster_output(self.ROSTER_JSON, "runX")
        assert roster is not None
        assert roster.run_id == "runX"
        assert roster.role_keys()[:1] == ["backend"]
        assert roster.rationale == "small backend-only plan"

    def test_parse_with_code_fence(self):
        fenced = "```json\n" + self.ROSTER_JSON + "\n```"
        roster = parse_roster_output(fenced, "runX")
        assert roster is not None
        assert "reviewer" in roster.role_keys()

    def test_parse_garbage_returns_none(self):
        assert parse_roster_output("no json here at all :)", "runX") is None


class _ScriptedExecutor:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def run(self, role, task, context):
        self.calls.append((role, task))
        return type("R", (), {"output": self.outputs.pop(0), "success": True})()


class TestComposeTeamAgentPath:
    def test_agent_roster_used_when_valid(self):
        executor = _ScriptedExecutor([self._valid()])
        roster, from_agent = compose_team(make_phases(4), "", run_id="r1", executor=executor)
        assert from_agent is True
        assert "reviewer" in roster.role_keys()
        # prompt was built with phase content
        raw_prompt = executor.calls[0][1].raw_prompt
        assert "Phase 1" in raw_prompt

    def _valid(self):
        return """{
          "type": "ROSTER",
          "roles": [
            {"key": "backend", "description": "b", "model": "m1", "max_workers": 2, "domains": ["api"]},
            {"key": "frontend", "description": "f", "model": "m1", "max_workers": 1, "domains": ["ui"]},
            {"key": "reviewer", "description": "g", "model": "m1", "max_workers": 2, "domains": []},
            {"key": "tester", "description": "g", "model": "m1", "max_workers": 2, "domains": []}
          ],
          "rationale": "ok"
        }"""

    def test_falls_back_on_invalid_output(self):
        executor = _ScriptedExecutor(["complete garbage"])
        roster, from_agent = compose_team(make_phases(4), "", run_id="r1", executor=executor)
        assert from_agent is False
        assert "reviewer" in roster.role_keys()

    def test_falls_back_when_band_exceeded(self):
        too_many = {
            "type": "ROSTER",
            "roles": [
                {"key": f"role{i}", "description": "", "model": "m1", "max_workers": 1, "domains": [str(i)]}
                for i in range(12)
            ] + [
                {"key": "reviewer", "description": "", "model": "m1", "max_workers": 1, "domains": []},
                {"key": "tester", "description": "", "model": "m1", "max_workers": 1, "domains": []},
            ],
            "rationale": "way oversized",
        }
        import json

        executor = _ScriptedExecutor([json.dumps(too_many)])
        roster, from_agent = compose_team(make_phases(4), "", run_id="r1", executor=executor)
        assert from_agent is False


class TestValidateRoster:
    def test_missing_gate_flagged(self):
        roster = TeamRoster(run_id="r", roles=[], rationale="")
        problems = validate_roster(roster, phase_count=5)
        assert any("tester" in p for p in problems)
        assert any("reviewer" in p for p in problems)

    def test_oversize_flagged(self):
        import json

        roster = parse_roster_output(json.dumps({
            "type": "ROSTER",
            "roles": [
                {"key": f"r{i}", "description": "", "model": "m", "max_workers": 1, "domains": []}
                for i in range(10)
            ] + [
                {"key": "reviewer", "description": "", "model": "m", "max_workers": 1},
                {"key": "tester", "description": "", "model": "m", "max_workers": 1},
            ],
            "rationale": "",
        }), "r")
        problems = validate_roster(roster, phase_count=4)
        assert any("band" in p for p in problems)


class TestStateTransition:
    def test_composing_team_inserted(self):
        from oporch.state_machine import StateMachine

        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.COMPOSING_TEAM)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        assert sm.current == OrchestratorState.AWAITING_PLAN_APPROVAL

    def test_analyzing_to_planning_still_allowed(self):
        from oporch.state_machine import StateMachine

        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)

    def test_composing_team_can_escalate(self):
        from oporch.state_machine import StateMachine

        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.COMPOSING_TEAM)
        sm.transition(OrchestratorState.AWAITING_USER_INPUT)


class TestWorkUnitStringRole:
    def test_assigned_role_is_plain_string(self):
        from oporch.models import WorkUnit

        wu = WorkUnit(id="WU-1", title="t", objective="o")
        assert wu.assigned_role == "builder"
        wu2 = WorkUnit(id="WU-2", title="t", objective="o", assigned_role="db_migration")
        assert wu2.assigned_role == "db_migration"
