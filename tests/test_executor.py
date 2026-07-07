from oporch.constants import AgentRole
from oporch.models import AgentResult, AgentTask, ContextPack
from oporch.executor import FakeAgentExecutor


class TestFakeExecutor:
    def test_default_output(self):
        executor = FakeAgentExecutor()
        task = AgentTask(objective="Test objective")
        ctx = ContextPack()
        result = executor.run(AgentRole.BUILDER, task, ctx)
        assert result.success
        assert "Fake output" in result.output
        assert result.role == AgentRole.BUILDER

    def test_tracks_calls(self):
        executor = FakeAgentExecutor()
        task = AgentTask(objective="Task A")
        ctx = ContextPack()
        executor.run(AgentRole.PLANNER, task, ctx)
        executor.run(AgentRole.BUILDER, task, ctx)
        assert len(executor.calls) == 2
        assert executor.calls[0][0] == AgentRole.PLANNER
        assert executor.calls[1][0] == AgentRole.BUILDER

    def test_set_next_result(self):
        executor = FakeAgentExecutor()
        custom = AgentResult(role=AgentRole.REVIEWER, success=True, output="Custom output")
        executor.set_next_result(custom)
        task = AgentTask(objective="Review")
        ctx = ContextPack()
        result = executor.run(AgentRole.REVIEWER, task, ctx)
        assert result.output == "Custom output"
        assert result.success

    def test_reset(self):
        executor = FakeAgentExecutor()
        task = AgentTask(objective="Test")
        ctx = ContextPack()
        executor.run(AgentRole.BUILDER, task, ctx)
        executor.reset()
        assert len(executor.calls) == 0
