import pytest

from oporch.constants import AgentRole, WorkUnitStatus
from oporch.models import WorkUnit
from oporch.work_unit import (
    CircularDependencyError,
    WorkUnitGraph,
    WorkUnitGraphError,
)


class TestWorkUnitGraph:
    def test_empty_graph(self):
        g = WorkUnitGraph()
        assert g.all() == []
        assert g.all_completed()

    def test_add_and_get(self):
        g = WorkUnitGraph()
        wu = WorkUnit(id="WU-001", title="Test", objective="Test objective")
        g.add(wu)
        assert g.get("WU-001") is wu
        assert g.get("WU-999") is None

    def test_duplicate_raises(self):
        g = WorkUnitGraph()
        g.add(WorkUnit(id="WU-001", title="A", objective="test"))
        with pytest.raises(WorkUnitGraphError):
            g.add(WorkUnit(id="WU-001", title="B", objective="test"))

    def test_unknown_dependency_raises(self):
        g = WorkUnitGraph()
        g.add(WorkUnit(
            id="WU-002", title="B", objective="test",
            dependencies=["WU-999"],
        ))
        with pytest.raises(WorkUnitGraphError):
            g.validate()

    def test_circular_dependency_detected(self):
        g = WorkUnitGraph()
        g.add(WorkUnit(id="WU-001", title="A", objective="test", dependencies=["WU-003"]))
        g.add(WorkUnit(id="WU-002", title="B", objective="test", dependencies=["WU-001"]))
        g.add(WorkUnit(id="WU-003", title="C", objective="test", dependencies=["WU-002"]))
        with pytest.raises(CircularDependencyError):
            g.validate()

    def test_self_loop_detected(self):
        g = WorkUnitGraph()
        g.add(WorkUnit(id="WU-001", title="A", objective="test", dependencies=["WU-001"]))
        with pytest.raises(CircularDependencyError):
            g.validate()

    def test_is_ready_no_deps(self):
        wu = WorkUnit(id="WU-001", title="A", objective="test")
        assert wu.is_ready(set())

    def test_is_ready_with_deps_met(self):
        wu = WorkUnit(id="WU-003", title="C", objective="test", dependencies=["WU-001", "WU-002"])
        assert wu.is_ready({"WU-001", "WU-002"})
        assert not wu.is_ready({"WU-001"})
        assert not wu.is_ready(set())

    def test_is_ready_skips_non_pending(self):
        wu = WorkUnit(
            id="WU-001", title="A", objective="test",
            status=WorkUnitStatus.COMPLETED,
        )
        assert not wu.is_ready(set())

    def test_get_ready_ordering(self):
        g = WorkUnitGraph()
        g.add(WorkUnit(id="WU-001", title="A", objective="test"))
        g.add(WorkUnit(id="WU-002", title="B", objective="test", dependencies=["WU-001"]))
        g.add(WorkUnit(id="WU-003", title="C", objective="test", dependencies=["WU-001"]))
        ready = g.get_ready(set())
        assert len(ready) == 1
        assert ready[0].id == "WU-001"

        ready2 = g.get_ready({"WU-001"})
        assert len(ready2) == 2
        ids = {u.id for u in ready2}
        assert "WU-001" not in ids
        assert ids == {"WU-002", "WU-003"}

    def test_topological_order(self):
        g = WorkUnitGraph()
        g.add(WorkUnit(id="WU-001", title="A", objective="test"))
        g.add(WorkUnit(id="WU-002", title="B", objective="test", dependencies=["WU-001"]))
        g.add(WorkUnit(id="WU-003", title="C", objective="test", dependencies=["WU-002"]))
        order = g.topological_order()
        assert order.index("WU-001") < order.index("WU-002")
        assert order.index("WU-002") < order.index("WU-003")

    def test_count_by_status(self):
        g = WorkUnitGraph()
        g.add(WorkUnit(id="WU-001", title="A", objective="test", status=WorkUnitStatus.COMPLETED))
        g.add(WorkUnit(id="WU-002", title="B", objective="test", status=WorkUnitStatus.PENDING))
        g.add(WorkUnit(id="WU-003", title="C", objective="test", status=WorkUnitStatus.PENDING))
        counts = g.count_by_status()
        assert counts[WorkUnitStatus.COMPLETED] == 1
        assert counts[WorkUnitStatus.PENDING] == 2

    def test_all_completed(self):
        g = WorkUnitGraph()
        g.add(WorkUnit(id="WU-001", title="A", objective="test", status=WorkUnitStatus.COMPLETED))
        g.add(WorkUnit(id="WU-002", title="B", objective="test", status=WorkUnitStatus.COMPLETED))
        assert g.all_completed()

    def test_not_all_completed(self):
        g = WorkUnitGraph()
        g.add(WorkUnit(id="WU-001", title="A", objective="test", status=WorkUnitStatus.COMPLETED))
        g.add(WorkUnit(id="WU-002", title="B", objective="test", status=WorkUnitStatus.PENDING))
        assert not g.all_completed()
