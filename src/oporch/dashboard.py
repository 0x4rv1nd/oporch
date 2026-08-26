"""Live read-only TUI dashboard for a running oporch run (v2, PRD §5).

Polls ``oporch.db`` every ~500ms (safe under SQLite WAL) and renders:
- top bar with run id, orchestrator state, pause badge
- status chips (done / failed / conflict / active / queued)
- progress bar over all work units
- one panel per roster role with clickable, navigable WU cards
- bottom pane tailing the structured events table

Keybinds:
    q        quit
    d/Enter  open WU detail (modal, scrollable output + event trail)
    ↑/k ↓/j  move WU selection
    p        toggle dispatcher pause (cooperative control row)
    r        force refresh now
    Esc      close detail

This process is purely a viewer; opening/closing it never affects the run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, ProgressBar, Static

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
_CHIP_ORDER = (
    ("COMPLETED", "green"),
    ("IN_PROGRESS", "cyan"),
    ("MERGE_CONFLICT", "yellow"),
    ("FAILED", "red"),
    ("BLOCKED", "magenta"),
    ("PENDING", "dim"),
)


class WUCard(Static):
    """One work unit row inside a role panel. Click selects it."""

    def __init__(self, wu: dict[str, Any]) -> None:
        super().__init__(classes="wu-card")
        self.wu_id = wu["id"]
        self.wu_status = wu.get("status") or "PENDING"

    def on_click(self) -> None:
        assert self.app is not None
        self.app.select_wu(self.wu_id)

    def render_card(self, wu: dict[str, Any]) -> None:
        self.wu_status = wu.get("status") or "PENDING"
        self.update(format_wu_line(wu))


def format_wu_line(wu: dict[str, Any]) -> str:
    status = wu.get("status") or "PENDING"
    glyph = _STATUS_GLYPH.get(status, "?")
    style = _STATUS_STYLE.get(status, "white")
    title = (wu.get("title") or "")[:20]
    line = f"[{style}]{glyph}[/{style}] [bold]{wu['id']}[/bold] {title}"
    extra: list[str] = []
    if status == "IN_PROGRESS":
        attempt = int(wu.get("attempt") or 1)
        extra.append(f"att {attempt}")
        started = _parse_ts(wu.get("started_at"))
        if started:
            extra.append(_elapsed(started))
    elif status == "COMPLETED":
        extra.append("[green]done[/green]")
    elif status == "MERGE_CONFLICT":
        extra.append("[yellow]merge conflict[/yellow]")
    elif status == "BLOCKED" and wu.get("depends_on"):
        deps = _load_deps(wu)
        extra.append(f"[magenta]blocked:{','.join(deps[:2])}[/magenta]")
    if extra:
        line += "  [dim]" + " · ".join(extra) + "[/dim]"
    return line


def _load_deps(wu: dict[str, Any]) -> list[str]:
    try:
        import json

        return json.loads(wu["depends_on"])[:3]
    except Exception:
        return []


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _elapsed(since: datetime) -> str:
    delta = max(0, int((datetime.now(timezone.utc) - since).total_seconds()))
    mins, secs = divmod(delta, 60)
    return f"{mins:02d}:{secs:02d}"


class RolePanel(Vertical):
    """Column for one roster role: header + WU cards."""

    def __init__(self, role: dict[str, Any]) -> None:
        super().__init__(
            Static("", classes="role-header"),
            VerticalScroll(classes="role-body"),
            classes="role-panel",
        )
        self.role_key_attr = role["role_key"]

    def show_role(self, role: dict[str, Any], wus: list[dict[str, Any]]) -> None:
        key = role["role_key"]
        running = sum(1 for w in wus if w["status"] == "IN_PROGRESS")
        workers = role.get("max_workers", "-")
        header_lines = [
            f"[bold]{key}[/bold] ({running}/{workers})",
        ]
        model = role.get("model")
        if model:
            header_lines.append(f"[dim]{model}[/dim]")
        header = self.query_one(".role-header", Static)
        header.update("\n".join(header_lines))

        body = self.query_one(".role-body", VerticalScroll)
        existing: dict[str, WUCard] = {}
        for child in body.children:
            if isinstance(child, WUCard):
                existing[child.wu_id] = child
        for wu in wus:
            card = existing.pop(wu["id"], None)
            if card is None:
                card = WUCard(wu)
                body.mount(card)
            card.render_card(wu)
        for stale in existing.values():
            stale.remove()


class WUDetailScreen(ModalScreen):
    """Full-screen drill-down: WU metadata, output, event trail."""

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("d", "dismiss", "Close"),
        ("q", "dismiss", "Close"),
    ]

    def __init__(self, body_text: str = "") -> None:
        super().__init__()
        self._body_text = body_text

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static(self._body_text, id="detail-body"),
            id="detail-scroll",
        )

    @staticmethod
    def build_body(
        wu_row: dict[str, Any],
        events: list[dict[str, Any]],
        output: str | None,
    ) -> str:
        status = wu_row.get("status")
        style = _STATUS_STYLE.get(status, "white")
        lines: list[str] = [
            f"[bold]{wu_row['id']}[/bold] — {wu_row.get('title') or ''}",
            "",
            f"role: {wu_row.get('assigned_role')}   "
            f"status: [{style}]{status}[/{style}]   "
            f"attempt: {wu_row.get('attempt') or 0}",
        ]
        deps = _load_deps(wu_row)
        if deps:
            lines.append(f"depends on: {', '.join(deps)}")
        if wu_row.get("result_summary"):
            lines += ["", "[bold]Summary[/bold]", str(wu_row["result_summary"])[:2000]]
        if output:
            shown = output[:4000]
            suffix = "\n… [dim](truncated)[/dim]" if len(output) > 4000 else ""
            lines += ["", "[bold]Agent output[/bold]", shown + suffix]
        if events:
            lines += ["", "[bold]Event trail[/bold]"]
            for e in events[-40:]:
                ts = (e.get("ts") or "")[11:19]
                level_style = {"error": "red", "warn": "yellow"}.get(
                    e.get("level") or "info", "white"
                )
                bits = [f"{ts}", f"[{level_style}]{e.get('event_type')}[/{level_style}]"]
                if e.get("duration_ms"):
                    bits.append(f"{e['duration_ms']:.0f}ms")
                if e.get("model_used"):
                    bits.append(f"[dim]{e['model_used']}[/dim]")
                lines.append("  " + "  ".join(bits))
        return "\n".join(lines)

    def action_dismiss(self) -> None:
        self.dismiss()


class OporchDashboard(App):
    TITLE = "oporch"
    SUB_TITLE = "live run viewer"
    CSS = """
    #topbar { height: auto; padding: 0 1; color: $text; }
    #chips { height: 1; padding: 0 1; }
    #progress-label { height: 1; padding: 0 1; }
    #bar { margin: 0 1 1 1; }
    #columns { height: 1fr; }
    .role-panel { border: round $panel-lighten-1; margin: 0 1; padding: 0 1;
                  width: 1fr; min-width: 26; }
    .role-header { height: auto; padding-bottom: 1; }
    .role-body { height: 1fr; }
    .wu-card { height: auto; padding: 0 1; }
    .wu-card:hover { background: $surface-lighten-2; }
    .wu-card.selected { background: $accent-darken-1; }
    #events { border: round $secondary; height: 12; padding: 0 1; }
    #detail-scroll { border: double $accent; background: $surface;
                     padding: 0 2; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "open_detail", "WU detail"),
        ("enter", "open_detail", "Open"),
        ("up", "prev_wu", "Prev WU"),
        ("k", "prev_wu", "Prev WU"),
        ("down", "next_wu", "Next WU"),
        ("j", "next_wu", "Next WU"),
        ("p", "toggle_pause", "Pause/Resume"),
        ("r", "refresh_now", "Refresh"),
    ]

    def __init__(self, run_id: str, db: OporchDB | None = None) -> None:
        super().__init__()
        self.run_id = run_id
        self.db = db or OporchDB()
        self._paused = False
        self._cursor = 0
        self._flat: list[dict[str, Any]] = []
        self._last_signature: tuple | None = None

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="topbar")
        yield Static("", id="chips")
        yield Static("", id="progress-label")
        yield ProgressBar(total=100, show_eta=False, id="bar")
        yield Horizontal(id="columns")
        yield VerticalScroll(Static("", id="events-inner"), id="events")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#bar", ProgressBar).display = False
        self.set_interval(REFRESH_SECONDS, self.refresh_from_db)
        self.call_after_refresh(self.refresh_from_db)

    # ------------------------------------------------------------------
    # snapshot helpers (kept API-stable for tooling/tests)
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
    def render_chips(counts: dict[str, int]) -> str:
        chips = []
        for status, color in _CHIP_ORDER:
            n = counts.get(status, 0)
            label = {
                "IN_PROGRESS": "active",
            }.get(status, status.lower())
            chips.append(f"[{color}]{label}:{n}[/{color}]")
        return "   ".join(chips)

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
            dur = e.get("duration_ms")
            tail = f"  [dim]{dur:.0f}ms[/dim]" if dur else ""
            lines.append(
                f"[{style}]{ts}[/{style}] {who:<12} {wu:<10}"
                f"{e.get('event_type')}{tail}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # live update loop
    # ------------------------------------------------------------------
    def on_mount_ready(self) -> None:  # pragma: no cover - safety hook
        self.refresh_from_db()

    async def refresh_from_db(self) -> None:
        try:
            snap = self.snapshot()
        except Exception:
            return

        self._render_topbar(snap)
        self.query_one("#chips", Static).update(self.render_chips(snap["counts"]))
        await self._render_progress_bar(snap)
        await self._render_columns(snap)
        self._render_events_pane(snap)
        self._update_cursor_selection()
        self._detect_new_failures(snap)

    def _render_topbar(self, snap: dict[str, Any]) -> None:
        state = (snap["run"] or {}).get("state") or "UNKNOWN"
        milestone = (snap["run"] or {}).get("milestone_id") or "—"
        left = (
            f"[bold]oporch[/bold] · run [bold]{self.run_id}[/bold] · "
            f"[bold]{state}[/bold] · milestone {milestone}"
        )
        if self._paused:
            left += "  [black on yellow] PAUSED [/black on yellow]"
        self.query_one("#topbar", Static).update(left)
        try:
            self.sub_title = (
                f"PAUSED — dispatcher stops at next wave boundary"
                if self._paused
                else f"run {self.run_id}"
            )
        except Exception:
            pass

    async def _render_progress_bar(self, snap: dict[str, Any]) -> None:
        counts = snap["counts"]
        total = sum(counts.values())
        done = counts.get("COMPLETED", 0)
        label = self.render_progress(snap)
        self.query_one("#progress-label", Static).update(label)
        bar = self.query_one("#bar", ProgressBar)
        if bar.total != total:
            bar.total = total or 1
        bar.progress = done
        bar.display = True

    async def _render_columns(self, snap: dict[str, Any]) -> None:
        container = self.query_one("#columns", Horizontal)
        grouped = self.group_wus_by_role(snap["wus"])
        roles = {r["role_key"]: r for r in snap["roles"]}
        for key in grouped:
            roles.setdefault(key, {"role_key": key, "max_workers": "-",
                                   "model": None})
        panels: dict[str, RolePanel] = {}
        for child in list(container.children):
            if isinstance(child, RolePanel):
                if child.role_key_attr in roles:
                    panels[child.role_key_attr] = child
                else:
                    await container.remove(child)
        for key, role in roles.items():
            panel = panels.get(key)
            if panel is None:
                panel = RolePanel(role)
                await container.mount(panel)
            panel.show_role(role, grouped.get(key, []))

        # Flatten display order for cursor navigation.
        flat: list[dict[str, Any]] = []
        for child in container.children:
            if isinstance(child, RolePanel):
                for card in child.query(WUCard):
                    wu = next(
                        (w for w in grouped.get(child.role_key_attr, [])
                         if w["id"] == card.wu_id),
                        None,
                    )
                    if wu:
                        flat.append(wu)
        self._flat = flat

    def _render_events_pane(self, snap: dict[str, Any]) -> None:
        inner = self.query_one("#events-inner", Static)
        inner.update(self.render_events(snap["events"]))
        scroll = self.query_one("#events", VerticalScroll)
        scroll.scroll_end(animate=False)

    def _update_cursor_selection(self) -> None:
        if not self._flat:
            return
        if self._cursor >= len(self._flat):
            actives = [i for i, w in enumerate(self._flat)
                       if w["status"] == "IN_PROGRESS"]
            self._cursor = actives[0] if actives else len(self._flat) - 1
        target_id = self._flat[self._cursor]["id"]
        for child in self.query("#columns .wu-card"):
            child.remove_class("selected")
            if getattr(child, "wu_id", None) == target_id:
                child.add_class("selected")

    def _detect_new_failures(self, snap: dict[str, Any]) -> None:
        signature = tuple((w["id"], w["status"]) for w in snap["wus"])
        prev = self._last_signature or ()
        prev_map = dict(prev)
        for wid, st in signature:
            if prev_map.get(wid) != st and st in ("FAILED", "MERGE_CONFLICT"):
                self.bell()
        self._last_signature = signature

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        self.db.set_control("pause", "1" if self._paused else "0")
        self.run_worker(self.refresh_from_db())

    def action_refresh_now(self) -> None:
        self.run_worker(self.refresh_from_db())

    def action_next_wu(self) -> None:
        if self._flat:
            self._cursor = min(self._cursor + 1, len(self._flat) - 1)
            self._update_cursor_selection()

    def action_prev_wu(self) -> None:
        if self._flat:
            self._cursor = max(self._cursor - 1, 0)
            self._update_cursor_selection()

    def select_wu(self, wu_id: str) -> None:
        for i, w in enumerate(self._flat):
            if w["id"] == wu_id:
                self._cursor = i
                break
        self._update_cursor_selection()

    async def action_open_detail(self) -> None:
        if isinstance(self.screen, WUDetailScreen):
            self.screen.dismiss()
            return
        if not self._flat:
            return
        wu_id = self._flat[self._cursor]["id"]
        rows = [r for r in self.db.load_work_unit_rows(self.run_id)
                if r["id"] == wu_id]
        if not rows:
            return
        row = rows[0]
        output = None
        try:
            import json

            blob = json.loads(row.get("data") or "{}")
            output = blob.get("output")
        except Exception:
            pass
        body = WUDetailScreen.build_body(
            row, self.db.events_for_wu(self.run_id, wu_id), output,
        )
        await self.push_screen(WUDetailScreen(body))


def launch_dashboard(run_id: str) -> None:
    """Entry point for ``oporch view``."""
    OporchDashboard(run_id).run()
