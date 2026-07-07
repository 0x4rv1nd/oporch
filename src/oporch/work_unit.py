from __future__ import annotations

from .constants import WorkUnitStatus
from .models import WorkUnit


class WorkUnitGraphError(Exception):
    pass


class CircularDependencyError(WorkUnitGraphError):
    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Circular dependency detected: {' -> '.join(cycle)}")


class WorkUnitGraph:
    def __init__(self, units: list[WorkUnit] | None = None) -> None:
        self._units: dict[str, WorkUnit] = {}
        if units:
            for u in units:
                self._units[u.id] = u

    def add(self, unit: WorkUnit) -> None:
        if unit.id in self._units:
            raise WorkUnitGraphError(f"Work unit {unit.id} already exists")
        self._units[unit.id] = unit

    def get(self, unit_id: str) -> WorkUnit | None:
        return self._units.get(unit_id)

    def all(self) -> list[WorkUnit]:
        return list(self._units.values())

    def validate(self) -> None:
        for uid, unit in self._units.items():
            for dep in unit.dependencies:
                if dep not in self._units:
                    raise WorkUnitGraphError(
                        f"Work unit {uid} depends on unknown unit {dep}"
                    )
        self._detect_circular()

    def _detect_circular(self) -> list[str]:
        visited: set[str] = set()
        in_stack: set[str] = set()
        stack: list[str] = []

        def dfs(node: str) -> list[str] | None:
            visited.add(node)
            in_stack.add(node)
            stack.append(node)
            unit = self._units.get(node)
            if unit:
                for dep in unit.dependencies:
                    if dep not in visited:
                        result = dfs(dep)
                        if result:
                            return result
                    elif dep in in_stack:
                        cycle_start = stack.index(dep)
                        return stack[cycle_start:] + [dep]
            stack.pop()
            in_stack.discard(node)
            return None

        for uid in self._units:
            if uid not in visited:
                cycle = dfs(uid)
                if cycle:
                    raise CircularDependencyError(cycle)
        return []

    def get_ready(self, completed_ids: set[str]) -> list[WorkUnit]:
        ready = []
        for unit in self._units.values():
            if unit.id in completed_ids:
                continue
            if unit.is_ready(completed_ids):
                ready.append(unit)
        return sorted(ready, key=lambda u: u.id)

    def topological_order(self) -> list[str]:
        self.validate()
        visited: set[str] = set()
        result: list[str] = []

        def dfs(node: str) -> None:
            visited.add(node)
            unit = self._units.get(node)
            if unit:
                for dep in unit.dependencies:
                    if dep not in visited:
                        dfs(dep)
            result.append(node)

        for uid in self._units:
            if uid not in visited:
                dfs(uid)
        return result

    def count_by_status(self) -> dict[WorkUnitStatus, int]:
        counts: dict[WorkUnitStatus, int] = {}
        for unit in self._units.values():
            counts[unit.status] = counts.get(unit.status, 0) + 1
        return counts

    def all_completed(self) -> bool:
        return all(
            u.status == WorkUnitStatus.COMPLETED for u in self._units.values()
        )
