"""Interactive REPL for oporch — opencode-style slash commands.

Launch with ``oporch`` (no arguments). Paste a multi-phase plan as free text
and the supervisor analyses, composes a team, proposes work units, and asks
for approval before executing — all in one interactive session.

Slash commands (``/plan``, ``/build``, ``/status``, ``/team``, ``/memory``,
``/view``, ``/help``, ``/quit``, etc.) replace the old flag-heavy CLI.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from . import config as cfg
from .constants import OrchestratorState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONFIG_DIR = Path(".opencode-orchestrator") / "config"
STATE_DIR = Path(".opencode-orchestrator") / "state"
CONTEXT_DIR = Path(".opencode-orchestrator") / "context"
RUNS_DIR = Path(".opencode-orchestrator") / "runs"
LOCKS_DIR = Path(".opencode-orchestrator") / "locks"

_SLASH_COMMANDS: dict[str, str] = {
    "/plan": "Paste or re-analyse a plan",
    "/build": "Start executing the approved plan",
    "/resume": "Resume an interrupted run",
    "/status": "Show current run + work unit tree",
    "/view": "Open live TUI dashboard",
    "/team": "Show the current roster",
    "/team edit": "Interactively edit the roster",
    "/team history": "Show roster timeline",
    "/memory": "List agent memories",
    "/remember <text>": "Add a memory (fact) for builder",
    "/forget <id>": "Delete a memory by id",
    "/replay [run_id]": "Reconstruct what happened in a run",
    "/report": "Show final report",
    "/report failures": "Aggregate failure patterns",
    "/models": "Show resolved model mappings",
    "/logs [N]": "Show last N structured events",
    "/cancel": "Cancel the current run",
    "/doctor": "Run environment health checks",
    # ─ Codebase index ───────────────────────────────────────────
    "/index": "Force full re-index of codebase (runs automatically on startup)",
    "/search <pat>": "Search indexed symbols by regex/substring",
    "/callers <name>": "Show all call sites that call <name>",
    "/arch": "Show codebase architecture summary",
    # ─ Proxy stats ────────────────────────────────────────────
    "/proxy-stats": "Show rate-limit retry/fallback and token usage stats",
    "/help": "Show this help",
    "/quit": "Exit oporch",
    "/q": "Exit oporch",
}


def _project_name() -> str:
    """Return a human-readable project name from cwd."""
    return Path.cwd().name


def _current_run_id() -> str | None:
    from .run_state import PersistentRunState

    try:
        current = PersistentRunState().load_current()
        return current.run_id if current and current.run_id else None
    except Exception:
        return None


def _current_state() -> str:
    from .run_state import PersistentRunState

    try:
        current = PersistentRunState().load_current()
        if current and current.state:
            return current.state.value
    except Exception:
        pass
    return "IDLE"


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------


class OporchREPL:
    """Interactive orchestrator session."""

    def __init__(self, executor_type: str = "opencode") -> None:
        self.console = Console()
        self.executor_type = executor_type
        self._bg_thread: threading.Thread | None = None
        self._stop = False

    # ----- lifecycle -------------------------------------------------------

    def run(self) -> None:
        """Main REPL loop."""
        self._auto_init()
        self._print_banner()
        while not self._stop:
            try:
                text = self._read_input()
                if not text.strip():
                    continue
                if text.strip().startswith("/"):
                    self._dispatch_command(text.strip())
                else:
                    self._handle_plan_input(text)
            except KeyboardInterrupt:
                self.console.print("\n[dim]Press Ctrl+C again or type /quit to exit[/dim]")
                try:
                    # Second Ctrl+C exits immediately
                    time.sleep(0.3)
                except KeyboardInterrupt:
                    break
            except EOFError:
                break
        self.console.print("\n[dim]Goodbye.[/dim]")

    # ----- input -----------------------------------------------------------

    def _prompt_text(self) -> str:
        state = _current_state()
        run_id = _current_run_id()
        parts: list[str] = []
        if state != "IDLE":
            parts.append(f"[bold cyan]{state}[/bold cyan]")
        if run_id:
            parts.append(f"[dim]{run_id[:8]}[/dim]")
        prefix = " ".join(parts)
        if prefix:
            return f"{prefix} ❯ "
        return "[bold green]oporch[/bold green] ❯ "

    def _read_input(self) -> str:
        """Read input supporting multi-line paste (double-Enter to submit)."""
        first_line = self.console.input(self._prompt_text())
        if not first_line.strip():
            return ""
        # Single-line slash commands return immediately.
        if first_line.strip().startswith("/"):
            return first_line
        # Multi-line accumulation for plan pasting.
        lines = [first_line]
        self.console.print("[dim]  (paste more lines, press Enter on empty line to submit)[/dim]")
        while True:
            try:
                line = self.console.input("  ... ")
            except EOFError:
                break
            if not line and lines:
                break
            if line.strip() == "--end":
                break
            lines.append(line)
        return "\n".join(lines)

    # ----- auto init -------------------------------------------------------

    def _auto_init(self) -> None:
        """Silently initialise orchestrator dirs + config if missing."""
        for d in [CONFIG_DIR, STATE_DIR, CONTEXT_DIR, RUNS_DIR, LOCKS_DIR]:
            d.mkdir(parents=True, exist_ok=True)
        from .constants import SCHEMA_VERSION, SCHEMA_VERSION_FILE

        schema_file = STATE_DIR / SCHEMA_VERSION_FILE
        if not schema_file.exists():
            schema_file.write_text(f"{SCHEMA_VERSION}\n", encoding="utf-8")
        if not (CONFIG_DIR / "roles.yaml").exists():
            from .cli import _write_default_roles

            _write_default_roles()
        if not (CONFIG_DIR / "policies.yaml").exists():
            from .cli import _write_default_policies

            _write_default_policies()
        if not (CONFIG_DIR / "models.yaml").exists():
            from .cli import _write_default_models

            _write_default_models()
        # Start background codebase indexer (incremental — only changed files)
        self._start_background_index()

    def _start_background_index(self) -> None:
        """Kick off incremental indexing in a background daemon thread."""
        def _bg() -> None:
            try:
                from .db import OporchDB
                from .codebase_index import CodebaseIndexer
                db = OporchDB()
                indexer = CodebaseIndexer(db)
                # Register as global indexer so commands can access it
                import oporch.codebase_index as _ci
                _ci._global_indexer = indexer
                counts = indexer.index_project(full=False)
                if counts["files"] > 0:
                    self.console.print(
                        f"[dim]⚙ Index updated: {counts['files']} files, "
                        f"{counts['symbols']} symbols[/dim]"
                    )
            except Exception:
                pass  # Indexing is best-effort; never crash the REPL
        t = threading.Thread(target=_bg, daemon=True, name="oporch-indexer")
        t.start()

    # ----- banner ----------------------------------------------------------

    def _print_banner(self) -> None:
        project = _project_name()
        run_id = _current_run_id()
        state = _current_state()

        status_line = f"[dim]State:[/dim] [bold]{state}[/bold]"
        if run_id:
            status_line += f"  [dim]Run:[/dim] {run_id[:8]}"

        body = (
            f"[bold]📂 {escape(project)}[/bold]\n"
            f"{status_line}\n\n"
            "[dim]Paste your implementation plan below, or type [bold]/help[/bold] for commands.[/dim]\n"
            "[dim]Use [bold]/quit[/bold] to exit.[/dim]"
        )
        self.console.print(
            Panel(
                body,
                title="[bold bright_cyan]⚡ oporch[/bold bright_cyan]",
                subtitle="[dim]Multi-Agent Orchestrator[/dim]",
                border_style="bright_cyan",
                padding=(1, 2),
            )
        )

    # ----- command dispatch ------------------------------------------------

    def _dispatch_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        handlers: dict[str, Any] = {
            "/plan": self._cmd_plan,
            "/build": self._cmd_build,
            "/resume": self._cmd_resume,
            "/status": self._cmd_status,
            "/view": self._cmd_view,
            "/team": self._cmd_team,
            "/memory": self._cmd_memory,
            "/remember": self._cmd_remember,
            "/forget": self._cmd_forget,
            "/replay": self._cmd_replay,
            "/report": self._cmd_report,
            "/models": self._cmd_models,
            "/logs": self._cmd_logs,
            "/cancel": self._cmd_cancel,
            "/doctor": self._cmd_doctor,
            # codebase index commands
            "/index": self._cmd_index,
            "/search": self._cmd_search,
            "/callers": self._cmd_callers,
            "/arch": self._cmd_arch,
            # proxy stats
            "/proxy-stats": self._cmd_proxy_stats,
            "/help": self._cmd_help,
            "/quit": self._cmd_quit,
            "/q": self._cmd_quit,
        }
        handler = handlers.get(cmd)
        if handler:
            try:
                handler(arg)
            except Exception as exc:
                self.console.print(f"[red]Error:[/red] {exc}")
        else:
            self.console.print(f"[red]Unknown command:[/red] {cmd}  [dim](type /help)[/dim]")

    # ======================================================================
    # Slash command implementations
    # ======================================================================

    # ----- /plan -----------------------------------------------------------

    def _cmd_plan(self, arg: str) -> None:
        """Accept a pasted plan (or re-read from prompt)."""
        if arg:
            self._handle_plan_input(arg)
        else:
            self.console.print("[dim]Paste your plan below (Enter on empty line to submit):[/dim]")
            lines: list[str] = []
            while True:
                try:
                    line = self.console.input("  ... ")
                except EOFError:
                    break
                if not line and lines:
                    break
                if line.strip() == "--end":
                    break
                lines.append(line)
            if lines:
                self._handle_plan_input("\n".join(lines))
            else:
                self.console.print("[yellow]No plan provided.[/yellow]")

    # ----- /build ----------------------------------------------------------

    def _cmd_build(self, arg: str) -> None:
        """Start executing the approved plan."""
        run_id = _current_run_id()
        if not run_id:
            self.console.print("[yellow]No active run.[/yellow] Paste a plan first.")
            return
        state = _current_state()
        if state not in ("AWAITING_PLAN_APPROVAL", "EXECUTING"):
            self.console.print(
                f"[yellow]Current state is {state}.[/yellow] "
                "Plan must be in AWAITING_PLAN_APPROVAL to build."
            )
            return
        self._run_execution(run_id)

    # ----- /resume ---------------------------------------------------------

    def _cmd_resume(self, arg: str) -> None:
        """Resume an interrupted run."""
        run_id = _current_run_id()
        if not run_id:
            self.console.print("[yellow]No active run to resume.[/yellow]")
            return
        self._run_execution(run_id, resume=True)

    # ----- /status ---------------------------------------------------------

    def _cmd_status(self, arg: str) -> None:
        """Show current run state + work unit tree."""
        from .run_state import PersistentRunState

        prs = PersistentRunState()
        current = prs.load_current()
        if current is None or current.run_id is None:
            self.console.print("[yellow]No active run[/yellow]")
            return

        tree = Tree(f"Run [bold]{current.run_id}[/bold]")
        tree.add(f"Milestone: {current.milestone_id}")
        tree.add(f"State: [bold]{current.state.value}[/bold]")

        run_state = prs.load_run(current.run_id)
        if run_state:
            tree.add(f"Created: {run_state.created_at.isoformat()}")
            tree.add(f"Mode: {run_state.approval_mode}")

        work_units = prs.load_work_units(current.run_id)
        if work_units:
            completed = sum(1 for wu in work_units if wu.status.value == "COMPLETED")
            failed = sum(1 for wu in work_units if wu.status.value == "FAILED")
            total = len(work_units)
            tree.add(
                f"Progress: [green]{completed}[/green]/{total} completed"
                + (f", [red]{failed} failed[/red]" if failed else "")
            )

            wu_tree = Tree("Work Units")
            for wu in work_units:
                style = {
                    "COMPLETED": "green",
                    "IN_PROGRESS": "cyan",
                    "FAILED": "red",
                    "PENDING": "white",
                    "BLOCKED": "yellow",
                    "READY": "blue",
                    "SKIPPED": "dim",
                    "MERGE_CONFLICT": "bright_yellow",
                }.get(wu.status.value, "white")
                attempt = f" (attempt {wu.attempts}/{wu.max_attempts})" if wu.attempts > 0 else ""
                node = wu_tree.add(
                    f"[{style}]{wu.id}[/{style}]: {wu.title} "
                    f"([{style}]{wu.status.value}[/{style}]){attempt}"
                )
                if wu.blockers:
                    for b in wu.blockers:
                        node.add(f"[red]blocked: {b}[/red]")
            tree.add(wu_tree)
        self.console.print(tree)

    # ----- /view -----------------------------------------------------------

    def _cmd_view(self, arg: str) -> None:
        """Open the live TUI dashboard."""
        rid = arg.strip() or _current_run_id()
        if not rid:
            self.console.print("[yellow]No active run[/yellow]")
            return
        from .dashboard import launch_dashboard

        launch_dashboard(rid)

    # ----- /team -----------------------------------------------------------

    def _cmd_team(self, arg: str) -> None:
        """Show or edit the current roster."""
        sub = arg.strip().lower()
        run_id = _current_run_id()
        if not run_id:
            self.console.print("[yellow]No active run[/yellow]")
            return

        from .db import OporchDB

        db = OporchDB()
        try:
            if sub == "edit":
                self._team_edit_interactive(db, run_id)
            elif sub == "history":
                self._team_history(db, run_id)
            else:
                self._team_show(db, run_id)
        finally:
            db.close()

    def _team_show(self, db: Any, run_id: str) -> None:
        roles = db.get_roster(run_id)
        if not roles:
            self.console.print("[yellow]No roster found[/yellow]")
            return
        table = Table(title=f"Team Roster · {run_id[:8]}")
        table.add_column("Role", style="bold")
        table.add_column("Model")
        table.add_column("Workers")
        table.add_column("Domains")
        for r in roles:
            domains = ", ".join(r["domains"]) if r["domains"] else "--"
            table.add_row(r["role_key"], r["model"] or "--", str(r["max_workers"]), domains)
        self.console.print(table)

    def _team_history(self, db: Any, run_id: str) -> None:
        history = db.get_roster_history(run_id)
        if not history:
            self.console.print("[yellow]No roster history[/yellow]")
            return
        table = Table(title=f"Roster Timeline · {run_id[:8]}")
        table.add_column("Role", style="bold")
        table.add_column("Model")
        table.add_column("Workers")
        table.add_column("Active From")
        table.add_column("Active Until")
        for h in history:
            until = h["active_until"] or "[green]active[/green]"
            table.add_row(
                h["role_key"],
                h["model"] or "--",
                str(h["max_workers"]),
                (h["active_from"] or "")[:19],
                str(until)[:19] if h["active_until"] else str(until),
            )
        self.console.print(table)

    def _team_edit_interactive(self, db: Any, run_id: str) -> None:
        self.console.print("[dim]Commands: workers KEY N | remove KEY | add KEY | done[/dim]")
        while True:
            roles = db.get_roster(run_id)
            self.console.print(
                "Roles: " + ", ".join(f"{r['role_key']}(x{r['max_workers']})" for r in roles)
            )
            try:
                cmd = self.console.input("[dim]team>[/dim] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            parts = cmd.split()
            verb = parts[0].lower() if parts else "done"
            if verb in ("done", "q"):
                break
            elif verb == "workers" and len(parts) >= 3:
                try:
                    db.resize_role(run_id, parts[1], int(parts[2]))
                    self.console.print(f"[green]{parts[1]} -> {parts[2]} workers[/green]")
                except ValueError:
                    self.console.print("[red]N must be an integer[/red]")
            elif verb == "remove" and len(parts) >= 2:
                active = [r["role_key"] for r in db.get_roster(run_id)]
                if len(active) <= 1:
                    self.console.print("[red]Cannot remove the last role[/red]")
                else:
                    db.retire_role(run_id, parts[1])
                    self.console.print(f"[green]Retired {parts[1]}[/green]")
            elif verb == "add" and len(parts) >= 2:
                db.save_roster(
                    run_id,
                    [{"role_key": parts[1], "description": f"{parts[1]} agent",
                      "model": "deepseek-v4-flash", "max_workers": 2, "domains": []}],
                )
                self.console.print(f"[green]Added {parts[1]}[/green]")
            else:
                self.console.print("[yellow]Unknown. Use: workers KEY N | remove KEY | add KEY | done[/yellow]")
        self.console.print("[green]Roster edit complete[/green]")

    # ----- /memory ---------------------------------------------------------

    def _cmd_memory(self, arg: str) -> None:
        """List agent memories."""
        from .db import OporchDB

        db = OporchDB()
        project = str(Path.cwd())
        role = arg.strip() or None
        rows = db.recall(project, role_key=role, limit=50)
        db.close()

        if not rows:
            self.console.print("[yellow]No memories recorded yet[/yellow]")
            return

        table = Table(title="Agent Memory")
        table.add_column("ID", style="dim")
        table.add_column("Role", style="bold")
        table.add_column("Type")
        table.add_column("Content")
        for r in rows:
            content = r["content"]
            if len(content) > 80:
                content = content[:80] + "..."
            table.add_row(str(r["id"]), r["role_key"], r["memory_type"], content)
        self.console.print(table)

    # ----- /remember -------------------------------------------------------

    def _cmd_remember(self, arg: str) -> None:
        """Add a memory for builder role."""
        if not arg.strip():
            self.console.print("[yellow]Usage:[/yellow] /remember <text>")
            return
        from .db import OporchDB

        db = OporchDB()
        mid = db.remember(str(Path.cwd()), "builder", "fact", arg.strip())
        db.close()
        self.console.print(f"[green]Memory #{mid} recorded[/green]")

    # ----- /forget ---------------------------------------------------------

    def _cmd_forget(self, arg: str) -> None:
        """Delete a memory by id."""
        if not arg.strip():
            self.console.print("[yellow]Usage:[/yellow] /forget <id>")
            return
        from .db import OporchDB

        try:
            memory_id = int(arg.strip())
        except ValueError:
            self.console.print("[red]ID must be an integer[/red]")
            return
        db = OporchDB()
        removed = db.forget(memory_id)
        db.close()
        if removed:
            self.console.print(f"[green]Memory #{memory_id} forgotten[/green]")
        else:
            self.console.print(f"[yellow]No memory with id {memory_id}[/yellow]")

    # ----- /replay ---------------------------------------------------------

    def _cmd_replay(self, arg: str) -> None:
        """Reconstruct what happened in a run."""
        run_id = arg.strip() or _current_run_id()
        if not run_id:
            self.console.print("[yellow]Usage:[/yellow] /replay <run_id>")
            return

        from .db import OporchDB

        db = OporchDB()
        try:
            run = db.get_run(run_id)
            if run is None:
                self.console.print(f"[red]Run {run_id} not found[/red]")
                return
            events = db.all_events(run_id)
        finally:
            db.close()

        if not events:
            self.console.print("[yellow]No events recorded[/yellow]")
            return

        table = Table(title=f"Replay · {run_id[:8]}")
        table.add_column("Time", style="dim")
        table.add_column("Role", style="bold")
        table.add_column("WU")
        table.add_column("Event")
        table.add_column("Duration")
        for e in events[-100:]:
            dur = e.get("duration_ms")
            table.add_row(
                (e.get("ts") or "")[11:19],
                e.get("role") or "--",
                e.get("wu_id") or "--",
                str(e.get("event_type") or ""),
                f"{dur:.0f}ms" if dur else "--",
            )
        self.console.print(table)

    # ----- /report ---------------------------------------------------------

    def _cmd_report(self, arg: str) -> None:
        """Show final report or failure patterns."""
        if arg.strip().lower() == "failures":
            from .db import OporchDB

            db = OporchDB()
            patterns = [
                m for m in db.recall(str(Path.cwd()), limit=1000)
                if m["memory_type"] == "failure_pattern"
            ]
            db.close()
            if not patterns:
                self.console.print("[green]No failure patterns recorded[/green]")
                return
            table = Table(title="Failure Patterns")
            table.add_column("ID", style="dim")
            table.add_column("Role", style="bold")
            table.add_column("Pattern")
            for p in patterns[-30:]:
                content = p["content"][:90] + "..." if len(p["content"]) > 90 else p["content"]
                table.add_row(str(p["id"]), p["role_key"], content)
            self.console.print(table)
            return

        # Default: final report for current run.
        from .run_state import PersistentRunState

        prs = PersistentRunState()
        current = prs.load_current()
        if current is None or current.run_id is None:
            self.console.print("[yellow]No active run[/yellow]")
            return
        run_path = prs.get_run_path(current.run_id)
        report_path = run_path / "final_report.json"
        if not report_path.exists():
            self.console.print("[yellow]No report found.[/yellow] Run /build first.")
            return

        from .models import MilestoneReport

        data = json.loads(report_path.read_text(encoding="utf-8"))
        rpt = MilestoneReport(**data)
        style = {"COMPLETED": "green", "FAILED": "red"}.get(rpt.status, "white")
        self.console.print(f"[bold]Milestone Report[/bold]  [{style}]{rpt.status}[/{style}]")
        self.console.print(f"  Objective: {rpt.objective}")
        if rpt.files_changed:
            self.console.print(f"  Files changed: {len(rpt.files_changed)}")
        if rpt.recommendation:
            self.console.print(f"  Recommendation: {rpt.recommendation}")

    # ----- /models ---------------------------------------------------------

    def _cmd_models(self, arg: str) -> None:
        """Show resolved role->model mappings."""
        roles = cfg.load_roles()
        table = Table(title="Model Mappings")
        table.add_column("Role", style="bold")
        table.add_column("Model Key")
        table.add_column("Fallback")
        table.add_column("Model ID")
        for role_name, role_cfg in roles.roles.items():
            model_id = cfg.resolve_model(role_name)
            status = "[green]OK[/green]" if model_id else "[red]--[/red]"
            table.add_row(role_name, role_cfg.model, role_cfg.fallback or "--",
                          (model_id or "--") + " " + status)
        self.console.print(table)

    # ----- /logs -----------------------------------------------------------

    def _cmd_logs(self, arg: str) -> None:
        """Show last N structured events."""
        run_id = _current_run_id()
        if not run_id:
            self.console.print("[yellow]No active run[/yellow]")
            return
        from .event_log import EventLog

        limit = 20
        if arg.strip():
            try:
                limit = int(arg.strip())
            except ValueError:
                pass
        el = EventLog(run_id)
        events = el.all()
        if not events:
            self.console.print("[yellow]No events recorded[/yellow]")
            return
        display = events[-limit:]
        table = Table(title=f"Events (last {len(display)} of {len(events)})")
        table.add_column("Time", style="dim")
        table.add_column("Event", style="bold")
        table.add_column("WU")
        table.add_column("Role")
        for ev in display:
            ts = ev.timestamp.strftime("%H:%M:%S") if ev.timestamp else "--"
            style = {"RUN_COMPLETED": "green", "RUN_FAILED": "red",
                     "REVIEW_FAILED": "red", "TEST_FAILED": "red"}.get(ev.event.value, "white")
            table.add_row(ts, f"[{style}]{ev.event.value}[/{style}]",
                          ev.work_unit_id or "--",
                          ev.agent_role.value if ev.agent_role else "--")
        self.console.print(table)

    # ----- /cancel ---------------------------------------------------------

    def _cmd_cancel(self, arg: str) -> None:
        from .run_state import PersistentRunState

        prs = PersistentRunState()
        current = prs.load_current()
        if current and current.run_id:
            prs.clear_current()
            self.console.print("[red]Run cancelled[/red]")
        else:
            self.console.print("[yellow]No active run to cancel[/yellow]")

    # ----- /doctor ---------------------------------------------------------

    def _cmd_doctor(self, arg: str) -> None:
        from .doctor import run_doctor

        result = run_doctor()
        table = Table(title="Environment Check")
        table.add_column("Check", style="bold")
        table.add_column("Status")
        table.add_column("Detail")
        for check in result.checks:
            style = {"PASS": "green", "FAIL": "red", "WARN": "yellow"}.get(
                check["status"], "white"
            )
            table.add_row(check["name"], f"[{style}]{check['status']}[/{style}]", check["detail"])
        self.console.print(table)

    # ----- /help -----------------------------------------------------------

    def _cmd_help(self, arg: str) -> None:
        table = Table(title="Available Commands", show_header=False, box=None, padding=(0, 2))
        table.add_column("Command", style="bold cyan")
        table.add_column("Description")
        for cmd, desc in _SLASH_COMMANDS.items():
            table.add_row(cmd, desc)
        self.console.print(table)
        self.console.print(
            "\n[dim]Or just paste your implementation plan as free text.[/dim]"
        )

    # ----- /quit -----------------------------------------------------------

    def _cmd_quit(self, arg: str) -> None:
        self._stop = True

    # ======================================================================
    # Plan handling (free-text input)
    # ======================================================================

    def _handle_plan_input(self, text: str) -> None:
        """Process pasted free-text as a plan, run the full pipeline."""
        from .context_builder import parse_plan_doc

        # 1. Parse phases
        phases = parse_plan_doc(text)
        if not phases:
            self.console.print(
                "[yellow]Could not detect phases.[/yellow] "
                "Use headings like '## Phase 1: Title' or '## Title'."
            )
            # Fall back to single-objective mode
            self.console.print("[dim]Treating as a single-objective plan...[/dim]")
            self._run_single_objective(text.strip().split("\n")[0][:200])
            return

        self.console.print(f"\n[bold bright_cyan]⚡ Analyzing plan...[/bold bright_cyan] {len(phases)} phases detected\n")
        for p in phases:
            criteria = "; ".join(p.acceptance_criteria[:2]) or "(no criteria)"
            self.console.print(f"  [bold]Phase {p.number}:[/bold] {p.title}")
            if p.acceptance_criteria:
                self.console.print(f"    [dim]{criteria}[/dim]")

        # 2. Write to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(text)
            plan_path = f.name

        try:
            # 3. Run planning pipeline
            self.console.print(f"\n[bold]🤖 Composing team & generating work units...[/bold]\n")
            from .orchestrator import HeadOrchestrator

            orchestrator = HeadOrchestrator()
            validation, plan_or_question = orchestrator.plan_milestone(
                f"plan-{datetime.now().strftime('%H%M%S')}",
                objective="",
                plan_source_path=plan_path,
            )

            if plan_or_question is None:
                self.console.print(f"[red]Planning failed:[/red] {validation.error}")
                return

            # Handle planner asking a question
            from .models import WorkerQuestion

            if isinstance(plan_or_question, WorkerQuestion):
                self.console.print(f"[yellow]Planner needs clarification:[/yellow]")
                self.console.print(f"  {plan_or_question.question}")
                if plan_or_question.options:
                    for opt in plan_or_question.options:
                        self.console.print(f"    - {opt}")
                return

            plan = plan_or_question

            # 4. Show roster
            from .db import OporchDB

            db = OporchDB()
            try:
                run_id = orchestrator.prs.load_current().run_id
                roster_rows = db.get_roster(run_id)
            except Exception:
                roster_rows = []
            finally:
                db.close()

            if roster_rows:
                role_parts = []
                for r in roster_rows:
                    key = r["role_key"]
                    workers = r["max_workers"]
                    if key in ("reviewer", "tester"):
                        role_parts.append(f"[green]{key} ✓[/green]")
                    else:
                        role_parts.append(f"[cyan]{key}[/cyan] x{workers}")
                self.console.print("[bold]🤖 Team:[/bold] " + "  ".join(role_parts))

            # 5. Show work units
            self.console.print(f"\n[bold]📋 {len(plan.work_units)} work units[/bold] proposed:\n")
            table = Table(show_header=True)
            table.add_column("ID", style="bold")
            table.add_column("Title")
            table.add_column("Role")
            table.add_column("Deps")
            for wu in plan.work_units:
                deps = ", ".join(wu.dependencies[:3]) if wu.dependencies else "--"
                table.add_row(wu.id, wu.title[:50], str(wu.assigned_role), deps)
            self.console.print(table)

            if plan.assumptions:
                self.console.print("\n[yellow]Assumptions:[/yellow]")
                for a in plan.assumptions:
                    self.console.print(f"  - {a}")

            # 6. Ask for approval
            self.console.print()
            try:
                answer = self.console.input("[bold]Approve and start building? [Y/n][/bold] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"

            if answer in ("", "y", "yes"):
                self.console.print("[green]✓ Plan approved[/green]")
                self._run_execution(run_id)
            else:
                self.console.print("[yellow]Plan not approved.[/yellow] Edit with /team edit, or paste a new plan.")

        finally:
            try:
                os.unlink(plan_path)
            except OSError:
                pass

    def _run_single_objective(self, objective: str) -> None:
        """Plan from a single-line objective (no phases)."""
        from .orchestrator import HeadOrchestrator

        self.console.print(f"\n[bold bright_cyan]⚡ Planning:[/bold bright_cyan] {objective}\n")
        orchestrator = HeadOrchestrator()
        validation, plan_or_question = orchestrator.plan_milestone(
            f"obj-{datetime.now().strftime('%H%M%S')}",
            objective=objective,
        )
        if plan_or_question is None:
            self.console.print(f"[red]Planning failed:[/red] {validation.error}")
            return

        plan = plan_or_question
        self.console.print(f"[bold]📋 {len(plan.work_units)} work units[/bold] proposed")

        table = Table(show_header=True)
        table.add_column("ID", style="bold")
        table.add_column("Title")
        table.add_column("Role")
        for wu in plan.work_units:
            table.add_row(wu.id, wu.title[:50], str(wu.assigned_role))
        self.console.print(table)

        try:
            answer = self.console.input("[bold]Approve and start building? [Y/n][/bold] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer in ("", "y", "yes"):
            self.console.print("[green]✓ Plan approved[/green]")
            run_id = _current_run_id()
            if run_id:
                self._run_execution(run_id)
        else:
            self.console.print("[yellow]Plan not approved.[/yellow]")

    # ======================================================================
    # Execution
    # ======================================================================

    def _run_execution(self, run_id: str, resume: bool = False) -> None:
        """Start milestone execution in a background thread."""
        if self._bg_thread and self._bg_thread.is_alive():
            self.console.print("[yellow]Execution already running.[/yellow] Type /status to check progress.")
            return

        self.console.print(
            f"\n[bold]▶ {'Resuming' if resume else 'Executing'}...[/bold]  "
            f"[dim](type /status or /view for progress)[/dim]\n"
        )

        def _worker() -> None:
            try:
                from .executor import FakeAgentExecutor, OpenCodeAgentExecutor
                from .orchestrator import HeadOrchestrator, OrchestratorError
                from .run_state import PersistentRunState
                from .smart_proxy import RetryingOpenCodeExecutor

                if self.executor_type == "opencode":
                    base_executor = OpenCodeAgentExecutor()
                    executor = RetryingOpenCodeExecutor(base_executor)
                else:
                    executor = FakeAgentExecutor()

                prs = PersistentRunState()
                orch = HeadOrchestrator(executor=executor, run_state=prs)

                if resume:
                    report = orch.resume_run()
                else:
                    report = orch.run_milestone(run_id)

                status_style = {"COMPLETED": "green", "FAILED": "red"}.get(
                    report.status, "white"
                )
                self.console.print(
                    f"\n[{status_style}]{'=' * 40}[/{status_style}]"
                )
                self.console.print(
                    f"[{status_style}]Run {report.status}[/{status_style}]  "
                    f"{report.objective}"
                )
                completed = sum(1 for wu in report.work_units if wu.status.value == "COMPLETED")
                total = len(report.work_units)
                self.console.print(
                    f"  [green]{completed}[/green]/{total} work units completed"
                )
                self.console.print(
                    f"[{status_style}]{'=' * 40}[/{status_style}]\n"
                )
            except Exception as exc:
                self.console.print(f"\n[red]Execution error:[/red] {exc}\n")

        self._bg_thread = threading.Thread(target=_worker, daemon=True)
        self._bg_thread.start()

    # ======================================================================
    # Codebase index commands
    # ======================================================================

    def _cmd_index(self, arg: str) -> None:
        """/index [--full] — Re-index the codebase."""
        full = "--full" in arg or "-f" in arg
        self.console.print(
            f"[dim]⚙ {'Full re-index' if full else 'Incremental index'} started...[/dim]"
        )
        try:
            from .db import OporchDB
            from .codebase_index import CodebaseIndexer
            import oporch.codebase_index as _ci

            db = OporchDB()
            indexer = CodebaseIndexer(db)
            _ci._global_indexer = indexer
            counts = indexer.index_project(full=full)
            self.console.print(
                f"[green]✓ Index complete:[/green] "
                f"{counts['files']} files  "
                f"{counts['symbols']} symbols  "
                f"{counts['calls']} call sites  "
                f"{counts['imports']} imports"
            )
        except Exception as exc:
            self.console.print(f"[red]Index error:[/red] {exc}")

    def _cmd_search(self, arg: str) -> None:
        """/search <pattern> — Search indexed symbols."""
        if not arg:
            self.console.print("[yellow]Usage:[/yellow] /search <pattern>")
            return
        try:
            import oporch.codebase_index as _ci
            from .db import OporchDB
            from pathlib import Path as _P

            indexer = _ci._global_indexer
            if indexer is None:
                db = OporchDB()
                indexer = _ci.CodebaseIndexer(db)
                _ci._global_indexer = indexer

            results = indexer.search_symbols(arg, limit=30)
            if not results:
                self.console.print(f"[dim]No symbols matching '{arg}'[/dim]")
                return

            table = Table(title=f"Symbols matching '{arg}'", show_header=True)
            table.add_column("Kind", style="cyan", width=10)
            table.add_column("Name", style="white bold")
            table.add_column("File", style="dim")
            table.add_column("Line", justify="right", style="dim")
            table.add_column("Parent", style="dim")
            for r in results:
                fp = _P(r.get("filepath", "")).name
                table.add_row(
                    r.get("kind", ""),
                    r.get("name", ""),
                    fp,
                    str(r.get("line_start", "")),
                    r.get("parent") or "",
                )
            self.console.print(table)
        except Exception as exc:
            self.console.print(f"[red]Search error:[/red] {exc}")

    def _cmd_callers(self, arg: str) -> None:
        """/callers <name> — Show who calls a function."""
        if not arg:
            self.console.print("[yellow]Usage:[/yellow] /callers <function_name>")
            return
        try:
            import oporch.codebase_index as _ci
            from .db import OporchDB
            from pathlib import Path as _P

            indexer = _ci._global_indexer
            if indexer is None:
                db = OporchDB()
                indexer = _ci.CodebaseIndexer(db)
                _ci._global_indexer = indexer

            results = indexer.get_callers(arg.strip(), limit=30)
            if not results:
                self.console.print(f"[dim]No recorded callers for '{arg}'[/dim]")
                return

            table = Table(title=f"Callers of '{arg}'", show_header=True)
            table.add_column("Caller", style="white bold")
            table.add_column("File", style="dim")
            table.add_column("Line", justify="right", style="dim")
            for r in results:
                fp = _P(r.get("caller_file", "")).name
                table.add_row(r.get("caller_name", ""), fp, str(r.get("line", "")))
            self.console.print(table)
        except Exception as exc:
            self.console.print(f"[red]Callers error:[/red] {exc}")

    def _cmd_arch(self, arg: str) -> None:
        """/arch — Show project architecture summary."""
        try:
            import oporch.codebase_index as _ci
            from .db import OporchDB

            indexer = _ci._global_indexer
            if indexer is None:
                db = OporchDB()
                indexer = _ci.CodebaseIndexer(db)
                _ci._global_indexer = indexer

            arch = indexer.get_architecture()
            table = Table(title="📐 Architecture Summary", show_header=False)
            table.add_column("Key", style="cyan")
            table.add_column("Value", style="white")
            table.add_row("Files", str(arch.total_files))
            table.add_row("Symbols", str(arch.total_symbols))
            table.add_row("Languages", ", ".join(
                f"{k}:{v}" for k, v in sorted(arch.languages.items())
            ))
            table.add_row("Top modules", "\n".join(arch.top_modules[:5]))
            table.add_row("Entry points", "\n".join(arch.entry_points) or "—")
            table.add_row("Hotspot functions", "\n".join(
                f"{name} ({n} calls)" for name, n in arch.hotspots[:5]
            ) or "—")
            table.add_row("Classes", ", ".join(arch.classes[:15]) or "—")
            self.console.print(table)
        except Exception as exc:
            self.console.print(f"[red]Arch error:[/red] {exc}")

    # ======================================================================
    # Proxy stats command
    # ======================================================================

    def _cmd_proxy_stats(self, arg: str) -> None:
        """/proxy-stats — Show built-in proxy rate-limit and token usage stats."""
        try:
            from .smart_proxy import proxy_stats, headroom_running

            if headroom_running():
                self.console.print(
                    "[cyan]ℹ Headroom proxy active on :8787 — "
                    "oporch built-in retry proxy is deferred.[/cyan]"
                )
                return
            if proxy_stats.is_empty():
                self.console.print("[dim]No proxy activity yet (no agents have run this session).[/dim]")
                return
            self.console.print(proxy_stats.as_rich_table())
        except Exception as exc:
            self.console.print(f"[red]Proxy stats error:[/red] {exc}")





# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def launch_repl(executor_type: str = "opencode") -> None:
    """Start the interactive REPL."""
    OporchREPL(executor_type=executor_type).run()
