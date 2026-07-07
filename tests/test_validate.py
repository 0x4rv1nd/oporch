from oporch.models import PlanResult, WorkerQuestion
from oporch.validate import default_repair, validate_agent_output, validate_planner_output


class TestDefaultRepair:
    def test_strips_code_fences_json(self):
        raw = "```json\n{\"milestone_id\": \"M1\"}\n```"
        assert default_repair(raw) == '{"milestone_id": "M1"}'

    def test_strips_code_fences_no_lang(self):
        raw = "```\n{\"milestone_id\": \"M1\"}\n```"
        assert default_repair(raw) == '{"milestone_id": "M1"}'

    def test_extracts_first_brace_block(self):
        raw = "some text\n{\"milestone_id\": \"M1\"}\ntrailing"
        assert default_repair(raw) == '{"milestone_id": "M1"}'

    def test_extracts_first_bracket_block(self):
        raw = "text\n[{\"id\": \"WU-001\"}]\nmore"
        assert default_repair(raw) == '[{"id": "WU-001"}]'

    def test_returns_raw_if_no_brace_or_bracket(self):
        raw = "just plain text"
        assert default_repair(raw) == "just plain text"


class TestValidateAgentOutput:
    def test_valid_json(self):
        raw = '{"milestone_id": "M1", "objective": "test", "work_units": []}'
        result = validate_agent_output(raw, PlanResult)
        assert result.status == "valid"
        assert result.parsed is not None
        assert result.parsed["milestone_id"] == "M1"

    def test_fails_on_invalid(self):
        raw = "not json"
        result = validate_agent_output(raw, PlanResult)
        assert result.status == "failed"
        assert result.parsed is None

    def test_repair_with_code_fences(self):
        raw = "```json\n{\"milestone_id\": \"M1\", \"objective\": \"test\", \"work_units\": []}\n```"
        result = validate_agent_output(raw, PlanResult, repair_fn=default_repair)
        assert result.status == "repaired"
        assert result.parsed is not None

    def test_no_repair_fn_does_not_repair(self):
        raw = "```json\n{\"milestone_id\": \"M1\", \"objective\": \"test\", \"work_units\": []}\n```"
        result = validate_agent_output(raw, PlanResult, repair_fn=None)
        assert result.status == "failed"

    def test_repair_returns_failed_when_unfixable(self):
        raw = "```json\n{\"milestone_id\": \"M1\"\n```"
        result = validate_agent_output(raw, PlanResult, repair_fn=default_repair)
        assert result.status == "failed"


class TestValidatePlannerOutput:
    def test_valid_plan(self):
        raw = '{"milestone_id": "M1", "objective": "test", "work_units": []}'
        result, parsed = validate_planner_output(raw)
        assert result.status == "valid"
        assert parsed is not None

    def test_valid_question(self):
        raw = '{"type": "QUESTION", "question_id": "Q1", "question": "What?", "why_needed": "Need to know"}'
        result, parsed = validate_planner_output(raw)
        assert result.status == "valid"
        assert parsed is not None
        assert parsed["type"] == "QUESTION"

    def test_failed_when_neither(self):
        raw = "garbage"
        result, parsed = validate_planner_output(raw)
        assert result.status == "failed"
        assert parsed is None
