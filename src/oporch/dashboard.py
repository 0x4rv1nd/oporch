"""Live read-only TUI dashboard for a running oporch run (v2, PRD §5).

Polls ``oporch.db`` every ~500ms (safe under SQLite WAL) and renders:
- header with run id, state, phase progress
- one column per roster role with live WU cards
- bottom pane tailing the events table

Keybinds: ``q`` quit · ``d`` drill into WU detail · ``p`` toggle dispatcher
pause (cooperative — the runner polls the same control row between waves).

This process is purely a viewer; opening/closing it never affects the run.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, Header, Static

from .db import OporchDB

REFRESH_SECONDS = 0.5

_STATUS_GLYPH = {
    "IN_PROGRESS": "▶",
    "COMPLETED": "✓",
    "FAILED": "✗",
    "MERGE_CONFLICT": "⚡",
    "PENDING": "⏸",
    "READY": "…",
    "BLOCKED": "⊘",
    "SKIPPED": "-",
}
_STATUS_STYLE = {
    "IN_PROGRESS": "cyan",
    "COMPLETED": "green",
    "FAILED": "red",
    "MERGE_CONFLICT": "yellow",
    "READY": "blue",
    "BLOCKED": "magenta",
}


class RoleColumn(Static):
    def show_role(self, role: dict[str, Any], wus: list[dict[str, Any]]) -> None:
        key = role["role_key"]
        running = sum(1 for w in wus if w["status"] == "IN_PROGRESS")
        lines = [f"[bold]{key}[/bold] ({running}/{role['max_workers']})"]
        if not wus:
            lines.append("[dim]  idle[/dim]")
        for wu in wus:
            status = wu["status"] or "PENDING"
            glyph = _STATUS_GLYPH.get(status, "?")
            style = _STATUS_STYLE.get(status, "white")
            title = (wu.get("title") or "")[:18]
            line = f"[{style}]{glyph}[/{style}] {wu['id']} {title}"
            if status == "IN_PROGRESS":
                line += f" [dim]att {wu.get('attempt') or 1}[/dim]"
            if status == "COMPLETED" and wu.get("finished_at"):
                line += " [green]done[/green]"
            if status == "MERGE_CONFLICT":
                line += " [yellow]conflict[/yellow]"
            lines.append(line)
        self.update("\n".join(lines))


class WUDetailScreen(Static):
    """Modal-ish full output view for one work unit."""

    BINDINGS = [("d", "close_detail", "Close"), ("escape", "close_detail", "Close")]

    def show_wu(self, wu_row: dict[str, Any], events: list[dict[str, Any]]) -> None:
        lines = [
            f"[bold]{wu_row['id']}[/bold] · {wu_row.get('title', '')}",
            f"role: {wu_row.get('assigned_role')}  status: {wu_row.get('status')}"
            f"  attempt: {wu_row.get('attempt')}",
        ]
        if wu_row.get("result_summary"):
            lines.append("")
            lines.append("[bold]Summary[/bold]")
            lines.append(str(wu_row["result_summary"])[:2000])
        if events:
            lines.append("")
            lines.append("[bold]Event trail[/bold]")
            for e in events[-25:]:
                ts = (e.get("ts") or "")[11:19]
                lines.append(
                    f"{ts} {e.get('event_type')} "
                    f"[dim]{e.get('model_used') or ''}"
                    + (f" {e['duration_ms']:.0f}ms" if e.get("duration_ms") else "")
                    + "[/dim]"
                )
        self.update("\n".join(lines))

    def action_close_detail(self) -> None:
        self.display = False


class OporchDashboard(App):
    TITLE = "oporch"
    CSS = """
    #columns { height: auto; }
    RoleColumn { border: round $primary; padding: 0 1; width: 1fr; margin: 0 1; }
    #events { border: round $secondary; height: 40%; }
    #progress { padding: 0 1; }
    WUDetailScreen { border: double $accent; padding: 0 2; display: none;
                     height: 60%; background: $surface; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "toggle_detail", "WU detail"),
        ("p", "toggle_pause", "Pause/Resume"),
    ]

    selected_wu: reactive[str | None] = reactive(None)

    def __init__(self, run_id: str, db: OporchDB | None = None) -> None:
        super().__init__()
        self.run_id = run_id
        self.db = db or OporchDB()
        self._paused = False
        self._roles_container = None
        self._events_widget = None
        self._detail = None
        self._header = None
        self._last_wu_states: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="progress")
        yield Horizontal(id="columns")
        yield VerticalScroll(Static("", id="events-inner"), id="events")
        yield WUDetailScreen(id="detail")
        yield Footer()

    # ------------------------------------------------------------------
    # snapshot helpers
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        run = self.db.get_run(self.run_id) or {}
        roles = self.db.get_roster(self.run_id)
        rows = self.db.load_work_unit_rows(self.run_id)
        counts = self.db.count_wu_by_status(self.run_id)
        events = self.db.tail_events(self.run_id, limit=15)
        return {
            "run": run,
            "roles": roles,
            "wus": rows,
            "counts": counts,
            "events": events,
        }

    @staticmethod
    def render_progress(snap: dict[str, Any]) -> str:
        counts = snap["counts"]
        total = sum(counts.values())
        done = counts.get("COMPLETED", 0)
        failed = counts.get("FAILED", 0)
        conflict = counts.get("MERGE_CONFLICT", 0)
        pct = int(done * 100 / total) if total else 0
        filled = done * 20 // total if total else 0
        bar = "█" * filled + "░" * (20 - filled)
        state = (snap["run"] or {}).get("state") or "UNKNOWN"
        parts = [
            f"run {snap['run'] and snap['run'].get('id') or '—'}",
            f"[bold]{state}[/bold]",
            f"Phase progress {done}/{total} [{bar}] {pct}%",
        ]
        if failed:
            parts.append(f"[red]{failed} failed[/red]")
        if conflict:
            parts.append(f"[yellow]{conflict} conflicts[/yellow]")
        return " · ".join(parts)

    @staticmethod
    def group_wus_by_role(wus: list[dict[str, Any]]) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for w in wus:
            grouped.setdefault(w.get("assigned_role") or "builder", []).append(w)
        return grouped

    @staticmethod
    def render_events(events: list[dict[str, Any]]) -> str:
        if not events:
            return "[dim]no events yet[/dim]"
        lines = ["[bold]Recent events[/bold]"]
        for e in events[-10:]:
            ts = (e.get("ts") or "")[11:19]
            level = e.get("level") or "info"
            style = {"error": "red", "warn": "yellow"}.get(level, "white")
            who = e.get("role") or "-"
            wu = e.get("wu_id") or "-"
            lines.append(
                f"[{style}]{ts}[/{style}] {who:<12} {wu:<10} {e.get('event_type')}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # live update loop
    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        self._roles_container = self.query_one("#columns", Horizontal)
        self._events_widget = self.query_one("#events-inner", Static)
        self._detail = self.query_one("#detail", WUDetailScreen)
        self.set_interval(REFRESH_SECONDS, self.refresh_from_db)

    def refresh_from_db(self) -> None:
        try:
            snap = self.snapshot()
        except Exception:
            return
        self.query_one("#progress", Static).update(self.render_progress(snap))
        self._render_columns(snap)
        self._events_widget.update(self.render_events(snap["events"]))
        self._detect_new_failures(snap)

    def _render_columns(self, snap: dict[str, Any]) -> None:
        assert self._roles_container is not None
        grouped = self.group_wus_by_role(snap["wus"])
        roles = snap["roles"] or []
        known = {r["role_key"]: r for r in roles}
        # Include roles that only appear via assigned WUs (pre-roster runs).
        for key in grouped:
            if key not in known:
                known[key] = {
                    "role_key": key, "max_workers": "-", "description": "",
                    "domains": [],
                }
        existing: dict[str, RoleColumn] = {}
        for child in list(self._roles_container.children):
            if isinstance(child, RoleColumn):
                existing[child.role_key_attr] = child

        order = list(known.keys())
        for idx, key in enumerate(order):
            col = existing.pop(key, None)
            if col is None:
                col = RoleColumn()
                col.role_key_attr = key
                self._roles_container.mount(col)
            col.show_role(known[key], grouped.get(key, []))
        for stale in existing.values():
            stale.remove()

    def _detect_new_failures(self, snap: dict[str, Any]) -> None:
        for wu in snap["wus"]:
            wid, st = wu["id"], wu["status"]
            prev = self._last_wu_states.get(wid)
            if prev != st and st in ("FAILED", "MERGE_CONFLICT"):
                self.bell()
            self._last_wu_states[wid] = st

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        self.db.set_control("pause", "1" if self._paused else "0")
        self.sub_title = (
            f"run {self.run_id} — PAUSED (dispatcher stops at next wave boundary)"
            if self._paused
            else f"run {self.run_id}"
        )

    async def action_toggle_detail(self) -> None:
        detail = self.query_one("#detail", WUDetailScreen)
        if detail.display:
            detail.display = False
            return
        wu_id = self.selected_or_first_wu()
        if not wu_id:
            return
        rows = [r for r in self.db.load_work_unit_rows(self.run_id)
                if r["id"] == wu_id]
        if not rows:
            return
        detail.show_wu(rows[0], self.db.events_for_wu(self.run_id, wu_id))
        detail.display = True

    def selected_or_first_wu(self) -> str | None:
        if self.selected_wu:
            return self.selected_wu
        rows = self.db.load_work_unit_rows(self.run_id)
        active = [r for r in rows if r["status"] == "IN_PROGRESS"]
        pool = active or rows
        return pool[0]["id"] if pool else None


def launch_dashboard(run_id: str) -> None:
    """Entry point for ``oporch view``."""
    OporchDashboard(run_id).run()
