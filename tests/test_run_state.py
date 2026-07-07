from oporch.constants import OrchestratorState
from oporch.models import WorkUnit
from oporch.run_state import PersistentRunState, create_run_state


class TestRunState:
    def setup_method(self):
        self.prs = PersistentRunState()
        self.prs.clear_current()

    def test_no_current_run_initially(self):
        current = self.prs.load_current()
        assert current is None or current.run_id is None

    def test_create_and_save_run(self):
        rs = create_run_state("M0", "Foundation", "SUPERVISED")
        self.prs.save_current(rs.model_dump(mode="json"))
        self.prs.save_run(rs)
        loaded = self.prs.load_run(rs.run_id)
        assert loaded is not None
        assert loaded.milestone_id == "M0"
        assert loaded.objective == "Foundation"

    def test_save_and_load_work_units(self):
        rs = create_run_state("M0", "Test", "AUTONOMOUS")
        units = [
            WorkUnit(id="WU-001", title="A", objective="test"),
            WorkUnit(id="WU-002", title="B", objective="test", dependencies=["WU-001"]),
        ]
        self.prs.save_work_units(rs.run_id, units)
        loaded = self.prs.load_work_units(rs.run_id)
        assert len(loaded) == 2
        assert loaded[0].id == "WU-001"
        assert loaded[1].dependencies == ["WU-001"]

    def test_clear_current(self):
        rs = create_run_state("M0", "Test", "SUPERVISED")
        self.prs.save_current(rs.model_dump(mode="json"))
        self.prs.clear_current()
        current = self.prs.load_current()
        assert current is None or current.run_id is None

    def test_worker_output_roundtrip(self):
        rs = create_run_state("M0", "Test", "AUTONOMOUS")
        self.prs.save_worker_output(rs.run_id, "WU-001", "test output")
        output = self.prs.load_worker_output(rs.run_id, "WU-001")
        assert output == "test output"

    def test_worker_output_not_found(self):
        rs = create_run_state("M0", "Test", "AUTONOMOUS")
        output = self.prs.load_worker_output(rs.run_id, "WU-999")
        assert output is None
