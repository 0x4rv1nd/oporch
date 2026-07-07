import uuid

from oporch.constants import AgentRole, EventType
from oporch.event_log import EventLog
from oporch.run_state import RUNS_DIR


class TestEventLog:
    def setup_method(self):
        self.run_id = str(uuid.uuid4())[:8]
        self.log = EventLog(self.run_id)

    def test_empty_log(self):
        assert self.log.count() == 0
        assert self.log.all() == []

    def test_record_event(self):
        event = self.log.record(EventType.RUN_CREATED)
        assert event.event == EventType.RUN_CREATED
        assert event.run_id == self.run_id
        assert self.log.count() == 1

    def test_record_with_details(self):
        self.log.record(
            EventType.WORK_UNIT_STARTED,
            work_unit_id="WU-001",
            agent_role=AgentRole.BUILDER,
            details={"file": "test.py"},
        )
        events = self.log.filter(EventType.WORK_UNIT_STARTED)
        assert len(events) == 1
        assert events[0].work_unit_id == "WU-001"
        assert events[0].agent_role == AgentRole.BUILDER
        assert events[0].details["file"] == "test.py"

    def test_filter_by_type(self):
        self.log.record(EventType.RUN_CREATED)
        self.log.record(EventType.PLAN_CREATED)
        self.log.record(EventType.WORK_UNIT_STARTED)
        assert len(self.log.filter(EventType.RUN_CREATED)) == 1
        assert len(self.log.filter(EventType.PLAN_CREATED)) == 1
        assert len(self.log.filter(EventType.WORK_UNIT_STARTED)) == 1

    def test_multiple_same_type(self):
        self.log.record(EventType.WORK_UNIT_STARTED, work_unit_id="WU-001")
        self.log.record(EventType.WORK_UNIT_STARTED, work_unit_id="WU-002")
        assert len(self.log.filter(EventType.WORK_UNIT_STARTED)) == 2

    def test_persists_to_disk(self):
        self.log.record(EventType.RUN_CREATED)
        path = RUNS_DIR / self.run_id / "events.jsonl"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "RUN_CREATED" in content

    def test_reloads_from_disk(self):
        self.log.record(EventType.PLAN_CREATED)
        log2 = EventLog(self.run_id)
        assert log2.count() == 1
        assert log2.all()[0].event == EventType.PLAN_CREATED
