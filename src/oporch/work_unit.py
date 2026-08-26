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

    def pending_ids(self) -> set[str]:
        return {
            uid for uid, u in self._units.items()
            if u.status not in (
                WorkUnitStatus.COMPLETED,
                WorkUnitStatus.FAILED,
                WorkUnitStatus.SKIPPED,
            )
        }

    def split_work_unit(self, wu_id: str) -> list[WorkUnit]:
        """§11b 'retry_with_narrower_scope': split a WU into two smaller ones.

        Children inherit the original's dependencies and split its
        acceptance criteria + affected files between them; dependents are
        rewired to depend on both children. The original is removed.
        Returns the two new units. Raises if the WU can't be split.
        """
        original = self._units.get(wu_id)
        if original is None:
            raise WorkUnitGraphError(f"Unknown work unit {wu_id}")
        if len(original.acceptance_criteria) < 2:
            raise WorkUnitGraphError(
                f"{wu_id} has fewer than 2 acceptance criteria; cannot split"
            )

        criteria = original.acceptance_criteria
        files = original.files_likely_affected
        mid_c = len(criteria) // 2
        mid_f = len(files) // 2 if files else 0

        def child(suffix: str, crit: list[str], fls: list[str]) -> WorkUnit:
            from .models import WorkUnit as _WU

            return _WU(
                id=f"{wu_id}{suffix}",
                title=f"{original.title} ({suffix})",
                objective=original.objective,
                dependencies=list(original.dependencies),
                assigned_role=original.assigned_role,
                phase=original.phase,
                acceptance_criteria=crit,
                files_likely_affected=fls,
                tests_required=list(original.tests_required),
                max_attempts=original.max_attempts,
            )

        part_a = child("a", criteria[:mid_c], files[:mid_f])
        part_b = child("b", criteria[mid_c:], files[mid_f:] or files[:mid_f])

        # Rewire dependents of the original onto both parts.
        for unit in self._units.values():
            if wu_id in unit.dependencies:
                unit.dependencies = [
                    d for d in unit.dependencies if d != wu_id
                ] + [part_a.id, part_b.id]

        del self._units[wu_id]
        self.add(part_a)
        self.add(part_b)
        self.validate()
        return [part_a, part_b]
