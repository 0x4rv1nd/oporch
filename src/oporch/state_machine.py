from __future__ import annotations

from datetime import datetime, timezone

from .constants import OrchestratorState, STATE_TRANSITIONS


class StateMachineError(Exception):
    pass


class InvalidTransitionError(StateMachineError):
    def __init__(self, current: OrchestratorState, target: OrchestratorState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition: {current.value} -> {target.value}")


class StateMachine:
    def __init__(self, initial_state: OrchestratorState = OrchestratorState.IDLE) -> None:
        self._current = initial_state
        self._history: list[dict] = []

    @property
    def current(self) -> OrchestratorState:
        return self._current

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    def transition(self, target: OrchestratorState) -> OrchestratorState:
        allowed = STATE_TRANSITIONS.get(self._current, [])
        if target not in allowed:
            raise InvalidTransitionError(self._current, target)

        now = datetime.now(timezone.utc)
        self._history.append({
            "from": self._current.value,
            "to": target.value,
            "timestamp": now.isoformat(),
        })
        self._current = target
        return self._current

    def can_transition(self, target: OrchestratorState) -> bool:
        return target in STATE_TRANSITIONS.get(self._current, [])

    def reset(self) -> None:
        self._current = OrchestratorState.IDLE
        self._history = []

    def is_terminal(self) -> bool:
        return self._current in (
            OrchestratorState.COMPLETED,
            OrchestratorState.FAILED,
            OrchestratorState.CANCELLED,
        )

    def is_active(self) -> bool:
        return not self.is_terminal() and self._current != OrchestratorState.IDLE
