from oporch.constants import OrchestratorState
from oporch.state_machine import InvalidTransitionError, StateMachine


class TestStateMachine:
    def test_initial_state(self):
        sm = StateMachine()
        assert sm.current == OrchestratorState.IDLE
        assert not sm.is_active()
        assert not sm.is_terminal()

    def test_valid_transition(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        assert sm.current == OrchestratorState.ANALYZING
        assert sm.is_active()

    def test_invalid_transition_raises(self):
        sm = StateMachine()
        import pytest
        with pytest.raises(InvalidTransitionError) as exc:
            sm.transition(OrchestratorState.COMPLETED)
        assert "IDLE" in str(exc.value)
        assert "COMPLETED" in str(exc.value)

    def test_can_transition(self):
        sm = StateMachine()
        assert sm.can_transition(OrchestratorState.ANALYZING)
        assert not sm.can_transition(OrchestratorState.COMPLETED)

    def test_full_approval_flow(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.EXECUTING)
        sm.transition(OrchestratorState.REVIEWING)
        sm.transition(OrchestratorState.TESTING)
        sm.transition(OrchestratorState.VALIDATING)
        sm.transition(OrchestratorState.COMPLETED)
        assert sm.current == OrchestratorState.COMPLETED
        assert sm.is_terminal()

    def test_failure_flow(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.EXECUTING)
        sm.transition(OrchestratorState.REVIEWING)
        sm.transition(OrchestratorState.FAILED)
        assert sm.is_terminal()

    def test_debug_loop(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.EXECUTING)
        sm.transition(OrchestratorState.REVIEWING)
        sm.transition(OrchestratorState.TESTING)
        sm.transition(OrchestratorState.DEBUGGING)
        sm.transition(OrchestratorState.EXECUTING)
        assert sm.current == OrchestratorState.EXECUTING

    def test_history_tracked(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        assert len(sm.history) == 2
        assert sm.history[0]["from"] == "IDLE"
        assert sm.history[0]["to"] == "ANALYZING"
        assert sm.history[1]["from"] == "ANALYZING"
        assert sm.history[1]["to"] == "PLANNING"

    def test_reset(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.reset()
        assert sm.current == OrchestratorState.IDLE
        assert len(sm.history) == 0

    def test_cancelled_is_terminal(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.CANCELLED)
        assert sm.is_terminal()

    def test_review_to_executing_retry(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.EXECUTING)
        sm.transition(OrchestratorState.REVIEWING)
        sm.transition(OrchestratorState.EXECUTING)
        assert sm.current == OrchestratorState.EXECUTING

    def test_cancelled_from_idle(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.CANCELLED)
        assert sm.is_terminal()

    def test_cancelled_from_analyzing(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.CANCELLED)
        assert sm.is_terminal()

    def test_cancelled_from_planning(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.CANCELLED)
        assert sm.is_terminal()

    def test_cancelled_from_executing(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.EXECUTING)
        sm.transition(OrchestratorState.CANCELLED)
        assert sm.is_terminal()

    def test_cancelled_from_reviewing(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.EXECUTING)
        sm.transition(OrchestratorState.REVIEWING)
        sm.transition(OrchestratorState.CANCELLED)
        assert sm.is_terminal()

    def test_cancelled_from_testing(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.EXECUTING)
        sm.transition(OrchestratorState.REVIEWING)
        sm.transition(OrchestratorState.TESTING)
        sm.transition(OrchestratorState.CANCELLED)
        assert sm.is_terminal()

    def test_cancelled_from_debugging(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.EXECUTING)
        sm.transition(OrchestratorState.REVIEWING)
        sm.transition(OrchestratorState.TESTING)
        sm.transition(OrchestratorState.DEBUGGING)
        sm.transition(OrchestratorState.CANCELLED)
        assert sm.is_terminal()

    def test_cancelled_from_validating(self):
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.EXECUTING)
        sm.transition(OrchestratorState.REVIEWING)
        sm.transition(OrchestratorState.TESTING)
        sm.transition(OrchestratorState.VALIDATING)
        sm.transition(OrchestratorState.CANCELLED)
        assert sm.is_terminal()

    def test_cancelled_not_from_completed(self):
        """Terminal states cannot transition to CANCELLED."""
        import pytest
        sm = StateMachine()
        sm.transition(OrchestratorState.ANALYZING)
        sm.transition(OrchestratorState.PLANNING)
        sm.transition(OrchestratorState.AWAITING_PLAN_APPROVAL)
        sm.transition(OrchestratorState.EXECUTING)
        sm.transition(OrchestratorState.REVIEWING)
        sm.transition(OrchestratorState.TESTING)
        sm.transition(OrchestratorState.VALIDATING)
        sm.transition(OrchestratorState.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(OrchestratorState.CANCELLED)

