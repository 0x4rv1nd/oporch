from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from . import config as cfg
from .constants import SCHEMA_VERSION, SCHEMA_VERSION_FILE
from .doctor import run_doctor

app = typer.Typer(
    name="oporch",
    help="Multi-agent orchestration system for OpenCode",
    no_args_is_help=True,
)
memory_app = typer.Typer(help="Durable agent memory (cross-run)")
app.add_typer(memory_app, name="memory")
team_app = typer.Typer(help="Dynamic team roster operations")
app.add_typer(team_app, name="team")
console = Console()

CONFIG_DIR = Path(".opencode-orchestrator") / "config"
STATE_DIR = Path(".opencode-orchestrator") / "state"
CONTEXT_DIR = Path(".opencode-orchestrator") / "context"
RUNS_DIR = Path(".opencode-orchestrator") / "runs"
LOCKS_DIR = Path(".opencode-orchestrator") / "locks"


@app.command()
def init() -> None:
    """Create configuration and state directories."""
    for d in [CONFIG_DIR, STATE_DIR, CONTEXT_DIR, RUNS_DIR, LOCKS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    default_roles = CONFIG_DIR / "roles.yaml"
    if not default_roles.exists():
        _write_default_roles()

    default_policies = CONFIG_DIR / "policies.yaml"
    if not default_policies.exists():
        _write_default_policies()

    default_models = CONFIG_DIR / "models.yaml"
    if not default_models.exists():
        _write_default_models()

    schema_file = STATE_DIR / SCHEMA_VERSION_FILE
    if not schema_file.exists():
        schema_file.write_text(f"{SCHEMA_VERSION}\n", encoding="utf-8")

    console.print("[green]OK[/green] Orchestrator initialized")
    console.print(f"  Config:  {CONFIG_DIR}")
    console.print(f"  State:   {STATE_DIR}")
    console.print(f"  Context: {CONTEXT_DIR}")
    console.print(f"  Runs:    {RUNS_DIR}")


@app.command()
def doctor() -> None:
    """Verify environment is ready for orchestration."""
    result = run_doctor()
    table = Table(title="Environment Check")
    table.add_column("Check", style="bold")
    table.add_column("Status", style="bold")
    table.add_column("Detail")

    for check in result.checks:
        status_style = {
            "PASS": "green",
            "FAIL": "red",
            "WARN": "yellow",
        }.get(check["status"], "white")
        table.add_row(
            check["name"],
            f"[{status_style}]{check['status']}[/{status_style}]",
            check["detail"],
        )

    console.print(table)
    summary = (
        f"[green]{result.passed} passed[/green], "
        f"[red]{result.failed} failed[/red], "
        f"[yellow]{result.warnings} warnings[/yellow]"
    )
    console.print(f"\n{summary}")

    if result.failed > 0:
        raise typer.Exit(code=1)


@app.command()
def status() -> None:
    """Show current orchestrator state and blockers."""
    from .run_state import PersistentRunState

    prs = PersistentRunState()
    current = prs.load_current()

    if current is None or current.run_id is None:
        console.print("[yellow]No active run[/yellow]")
        return

    tree = Tree(f"Run [bold]{current.run_id}[/bold]")
    tree.add(f"Milestone: {current.milestone_id}")
    tree.add(f"State: [bold]{current.state.value}[/bold]")

    run_state = prs.load_run(current.run_id)
    if run_state:
        tree.add(f"Created: {run_state.created_at.isoformat()}")
        tree.add(f"Updated: {run_state.updated_at.isoformat()}")
        tree.add(f"Mode: {run_state.approval_mode}")

    work_units = prs.load_work_units(current.run_id)
    if work_units:
        # Progress summary
        completed = sum(1 for wu in work_units if wu.status.value == "COMPLETED")
        failed = sum(1 for wu in work_units if wu.status.value == "FAILED")
        total = len(work_units)
        tree.add(
            f"Progress: [green]{completed}[/green]/{total} completed"
            + (f", [red]{failed} failed[/red]" if failed else "")
        )

        wu_tree = Tree("Work Units")
        for wu in work_units:
            status_style = {
                "COMPLETED": "green",
                "IN_PROGRESS": "blue",
                "FAILED": "red",
                "PENDING": "white",
                "BLOCKED": "yellow",
                "READY": "cyan",
                "SKIPPED": "dim",
            }.get(wu.status.value, "white")
            attempt_info = f" (attempt {wu.attempts}/{wu.max_attempts})" if wu.attempts > 0 else ""
            node = wu_tree.add(
                f"[{status_style}]{wu.id}[/{status_style}]: {wu.title} "
                f"([{status_style}]{wu.status.value}[/{status_style}])"
                f"{attempt_info}"
            )
            if wu.blockers:
                for b in wu.blockers:
                    node.add(f"[red]blocked: {b}[/red]")
            if wu.dependencies:
                node.add(f"[dim]deps: {', '.join(wu.dependencies)}[/dim]")
        tree.add(wu_tree)

    console.print(tree)


@app.command()
def plan(
    source: str = typer.Argument(..., help="Milestone id, or path to a plan document (.md)"),
    objective: str = typer.Option("", "--objective", "-o", help="Objective text (when SOURCE is a milestone id)"),
    milestone_id: str = typer.Option(None, "--milestone-id", "-m", help="Milestone id when SOURCE is a plan document"),
    skip_approval: bool = typer.Option(False, "--yes", help="Skip interactive approval prompts"),
) -> None:
    """Generate a work graph from an objective or a plan document."""
    from pathlib import Path as _Path

    from .orchestrator import HeadOrchestrator

    source_path: _Path | None = _Path(source)
    if source_path.exists() and source_path.is_file():
        plan_doc = str(source_path)
        mid = milestone_id or f"plan-{source_path.stem}"
        obj = objective
    else:
        plan_doc = None
        if not milestone_id:
            if source_path.exists() and not source_path.is_file():
                console.print(f"[red]{source} exists but is not a file[/red]")
                raise typer.Exit(code=1)
            mid = source
        else:
            mid = milestone_id
        obj = objective

    orchestrator = HeadOrchestrator()
    validation, plan_or_question = orchestrator.plan_milestone(
        mid, obj, plan_source_path=plan_doc,
    )

    if plan_or_question is None:
        console.print("[red]Plan generation failed[/red]")
        console.print(f"  Error: {validation.error}")
        raise typer.Exit(code=1)

    if isinstance(plan_or_question, dict) and plan_or_question.get("type") == "QUESTION":
        console.print("[yellow]Planner needs clarification:[/yellow]")
        console.print(f"  Question: {plan_or_question.get('question', '')}")
        console.print(f"  Why: {plan_or_question.get('why_needed', '')}")
        if plan_or_question.get("options"):
            console.print("  Options:")
            for opt in plan_or_question["options"]:
                console.print(f"    - {opt}")
        return

    plan = plan_or_question
    console.print(f"[green]Plan generated for {milestone_id}[/green]")
    console.print(f"  Objective: {plan.objective}")
    console.print(f"  Work Units: {len(plan.work_units)}")

    table = Table(title="Work Units")
    table.add_column("ID", style="bold")
    table.add_column("Title")
    table.add_column("Role")
    table.add_column("Deps")
    table.add_column("Status")

    for wu in plan.work_units:
        deps = ", ".join(wu.dependencies) if wu.dependencies else "--"
        table.add_row(wu.id, wu.title, str(wu.assigned_role), deps, wu.status.value)

    console.print(table)

    if plan.assumptions:
        console.print("\n[yellow]Assumptions:[/yellow]")
        for a in plan.assumptions:
            console.print(f"  - {a}")

    # Show proposed roster when a plan doc drove team composition.
    from .db import OporchDB

    db = OporchDB()
    try:
        roster_rows = db.get_roster(orchestrator.prs.load_current().run_id)
    except Exception:
        roster_rows = []
    finally:
        db.close()

    if roster_rows and not skip_approval:
        console.print("\n[bold]Proposed team roster[/bold] " + f"({len(roster_rows)} roles)")
        for r in roster_rows:
            domains = ", ".join(r["domains"]) if r["domains"] else "--"
            console.print(f"  [bold]{r['role_key']}[/bold] ×{r['max_workers']}  [{domains}]")
        edit_choice = typer.prompt("Edit roster before approval? (yes/no)", default="no")
        if edit_choice.lower() in ("y", "yes"):
            rid = orchestrator.prs.load_current().run_id
            db = OporchDB()
            try:
                _interactive_roster_edit(db, rid)
            finally:
                db.close()

    validation_result = typer.prompt("\nApprove plan? (yes/no)", default="yes")
    if validation_result.lower() not in ("y", "yes"):
        console.print("[red]Plan rejected[/red]")
        raise typer.Exit(code=1)

    console.print("[green]Plan approved[/green]")


def _interactive_roster_edit(db, run_id: str) -> None:
    """Minimal inline roster editor used by the plan flow."""
    while True:
        roles = db.get_roster(run_id)
        console.print("Roles: " + ", ".join(f"{r['role_key']}(x{r['max_workers']})" for r in roles))
        cmd = typer.prompt(
            "Command: workers KEY N | remove KEY | add KEY | done",
            default="done",
        ).strip()
        parts = cmd.split()
        verb = parts[0].lower() if parts else "done"
        if verb == "done":
            break
        if verb == "workers" and len(parts) >= 3:
            try:
                db.resize_role(run_id, parts[1], int(parts[2]))
                console.print(f"[green]{parts[1]} -> {parts[2]} workers[/green]")
            except ValueError:
                console.print("[red]N must be an integer[/red]")
        elif verb == "remove" and len(parts) >= 2:
            active = [r["role_key"] for r in db.get_roster(run_id)]
            if len(active) <= 1:
                console.print("[red]Cannot remove the last role[/red]")
            else:
                db.retire_role(run_id, parts[1])
                console.print(f"[green]Retired {parts[1]}[/green]")
        elif verb == "add" and len(parts) >= 2:
            db.save_roster(
                run_id,
                [{
                    "role_key": parts[1],
                    "description": f"{parts[1]} agent",
                    "model": "deepseek-v4-flash",
                    "max_workers": 2,
                    "domains": [],
                }],
            )
            console.print(f"[green]Added {parts[1]}[/green]")
        else:
            console.print("[yellow]Unknown command[/yellow]")


@app.command()
def run(
    milestone_id: str,
    executor_type: str = typer.Option(
        "fake", "--executor", "-e",
        help="Executor to use: 'fake' (default) or 'opencode'",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Show detailed output during execution",
    ),
) -> None:
    """Execute approved milestone."""
    from .executor import FakeAgentExecutor, OpenCodeAgentExecutor
    from .orchestrator import HeadOrchestrator, OrchestratorError
    from .run_state import PersistentRunState

    prs = PersistentRunState()
    current = prs.load_current()

    if current is None or current.run_id is None:
        console.print("[red]No active run found.[/red] Run 'oporch plan' first.")
        raise typer.Exit(code=1)

    if executor_type == "opencode":
        executor = OpenCodeAgentExecutor()
    else:
        executor = FakeAgentExecutor()

    orchestrator = HeadOrchestrator(executor=executor, run_state=prs)

    console.print(f"[bold]Executing milestone[/bold] {milestone_id}")
    console.print(f"  Run ID: {current.run_id}")
    console.print(f"  Executor: {executor_type}")
    console.print()

    try:
        report = orchestrator.run_milestone(current.run_id)
    except OrchestratorError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    # Display results
    status_style = {
        "COMPLETED": "green",
        "FAILED": "red",
        "CANCELLED": "yellow",
    }.get(report.status, "white")

    console.print(f"\n[{status_style}]Run {report.status}[/{status_style}]")
    console.print(f"  Objective: {report.objective}")

    # Show work unit summary
    table = Table(title="Work Unit Results")
    table.add_column("ID", style="bold")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Attempts")

    for wu in report.work_units:
        wu_style = {
            "COMPLETED": "green",
            "FAILED": "red",
            "IN_PROGRESS": "blue",
            "BLOCKED": "yellow",
            "PENDING": "white",
        }.get(wu.status.value, "white")
        table.add_row(
            wu.id,
            wu.title,
            f"[{wu_style}]{wu.status.value}[/{wu_style}]",
            str(wu.attempts),
        )

    console.print(table)

    if report.status == "FAILED":
        raise typer.Exit(code=1)


@app.command()
def resume(
    executor_type: str = typer.Option(
        "fake", "--executor", "-e",
        help="Executor to use: 'fake' (default) or 'opencode'",
    ),
) -> None:
    """Resume interrupted run."""
    from .executor import FakeAgentExecutor, OpenCodeAgentExecutor
    from .orchestrator import HeadOrchestrator, OrchestratorError
    from .run_state import PersistentRunState

    prs = PersistentRunState()

    if executor_type == "opencode":
        executor = OpenCodeAgentExecutor()
    else:
        executor = FakeAgentExecutor()

    orchestrator = HeadOrchestrator(executor=executor, run_state=prs)

    console.print("[bold]Resuming interrupted run...[/bold]")

    try:
        report = orchestrator.resume_run()
    except OrchestratorError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    status_style = {
        "COMPLETED": "green",
        "FAILED": "red",
        "CANCELLED": "yellow",
    }.get(report.status, "white")

    console.print(f"\n[{status_style}]Run {report.status}[/{status_style}]")
    console.print(f"  Objective: {report.objective}")

    if report.status == "FAILED":
        raise typer.Exit(code=1)


@app.command()
def replay(
    run_id: str = typer.Argument(..., help="Run id to reconstruct"),
    wu: str = typer.Option(None, "--wu", help="Scope to one work unit"),
    limit: int = typer.Option(200, "--limit", "-n", help="Max events to print"),
) -> None:
    """Reconstruct what actually happened in a run, chronologically."""
    from .db import OporchDB

    db = OporchDB()
    try:
        run = db.get_run(run_id)
        if run is None:
            console.print(f"[red]Run {run_id} not found[/red]")
            raise typer.Exit(code=1)

        events = (
            db.events_for_wu(run_id, wu)
            if wu
            else db.all_events(run_id)
        )
        if not events:
            console.print("[yellow]No events recorded for this scope[/yellow]")
            return

        state = run.get("state") or "?"
        title = f"Replay · run {run_id} · {state}"
        if wu:
            title += f" · WU {wu}"
        console.print(f"[bold]{title}[/bold] ({len(events)} events)")

        table = Table(show_header=True)
        table.add_column("#", style="dim")
        table.add_column("Time", style="dim")
        table.add_column("Level", style="dim")
        table.add_column("Role", style="bold")
        table.add_column("WU")
        table.add_column("Event")
        table.add_column("Duration")
        table.add_column("Model")

        for i, e in enumerate(events[:limit], start=1):
            level_style = {
                "error": "red", "warn": "yellow",
            }.get(e.get("level") or "info", "white")
            duration = e.get("duration_ms")
            dur_text = f"{duration:.0f}ms" if duration else "--"
            table.add_row(
                str(i),
                (e.get("ts") or "")[11:19],
                f"[{level_style}]{e.get('level') or 'info'}[/{level_style}]",
                e.get("role") or "--",
                e.get("wu_id") or "--",
                str(e.get("event_type") or ""),
                dur_text,
                e.get("model_used") or "--",
            )
        console.print(table)

        # Attempt history for scoped replays
        if wu:
            rows = db.load_work_unit_rows(run_id)
            row = next((r for r in rows if r["id"] == wu), None)
            if row:
                console.print(
                    f"\nFinal status: [bold]{row['status']}[/bold] "
                    f"after {row.get('attempt') or 0} attempt(s)"
                )
    finally:
        db.close()


@app.command("diff")
def diff_runs(
    run_a: str = typer.Argument(...),
    run_b: str = typer.Argument(...),
) -> None:
    """Compare two runs: duration, attempts, failure rate per role."""
    from .db import OporchDB

    db = OporchDB()
    try:
        stats = {rid: _run_stats(db, rid) for rid in (run_a, run_b)}
    finally:
        db.close()

    a, b = stats[run_a], stats[run_b]
    table = Table(title=f"Run diff: {run_a} vs {run_b}")
    table.add_column("Metric", style="bold")
    table.add_column(run_a)
    table.add_column(run_b)
    table.add_column("Delta")

    def fmt(v):
        return f"{v:.1f}" if isinstance(v, float) else str(v)

    for metric in ("total_wus", "completed", "failed", "failure_rate",
                   "total_duration_ms", "avg_attempt"):
        va, vb = a[metric], b[metric]
        delta = vb - va
        delta_s = f"{delta:+.2f}" if isinstance(delta, float) else f"{delta:+d}"
        table.add_row(metric, fmt(va), fmt(vb), delta_s)

    console.print(table)

    console.print("\n[bold]Per-role failure rates[/bold]")
    roles = sorted(set(a["role_failures"]) | set(b["role_failures"]))
    rt = Table()
    rt.add_column("Role", style="bold")
    rt.add_column(run_a)
    rt.add_column(run_b)
    for role in roles:
        ra = a["role_failures"].get(role, {"fail": 0, "total": 0})
        rb = b["role_failures"].get(role, {"fail": 0, "total": 0})
        fa = ra["fail"] / ra["total"] * 100 if ra["total"] else 0.0
        fb = rb["fail"] / rb["total"] * 100 if rb["total"] else 0.0
        rt.add_row(role, f"{fa:.0f}% ({ra['fail']}/{ra['total']})",
                   f"{fb:.0f}% ({rb['fail']}/{rb['total']})")
    console.print(rt)


def _run_stats(db, run_id: str) -> dict:
    import json as _json

    rows = db.load_work_unit_rows(run_id)
    completed = sum(1 for r in rows if r["status"] == "COMPLETED")
    failed = sum(
        1 for r in rows
        if r["status"] in ("FAILED", "MERGE_CONFLICT")
    )
    total = len(rows)
    durations = []
    attempts = []
    role_failures: dict[str, dict[str, int]] = {}
    for r in rows:
        attempt = int(r.get("attempt") or 0)
        if attempt:
            attempts.append(attempt)
        try:
            ev = db.events_for_wu(run_id, r["id"])
        except Exception:
            ev = []
        for e in ev:
            if e.get("duration_ms"):
                durations.append(e["duration_ms"])
        status_fail = r["status"] in ("FAILED", "MERGE_CONFLICT")
        rf = role_failures.setdefault(
            r.get("assigned_role") or "builder",
            {"fail": 0, "total": 0},
        )
        rf["total"] += 1
        if status_fail:
            rf["fail"] += 1
    return {
        "total_wus": total,
        "completed": completed,
        "failed": failed,
        "failure_rate": round(failed / total, 4) if total else 0.0,
        "total_duration_ms": float(sum(durations)),
        "avg_attempt": round(sum(attempts) / len(attempts), 2) if attempts else 0.0,
        "role_failures": role_failures,
    }


@app.command("report")
def report(
    failures: bool = typer.Option(False, "--failures", help="Aggregate failure patterns across all runs"),
) -> None:
    """Generate evidence-backed final report (or failure analytics)."""
    from .db import OporchDB

    if failures:
        db = OporchDB()
        try:
            project = str(Path.cwd())
            patterns = [
                m for m in db.recall(project, limit=1000)
                if m["memory_type"] == "failure_pattern"
            ]
        finally:
            db.close()

        if not patterns:
            console.print("[green]No failure patterns recorded[/green]")
            return

        table = Table(title=f"Failure Patterns · {project}")
        table.add_column("ID", style="dim")
        table.add_column("Role", style="bold")
        table.add_column("Score")
        table.add_column("Pattern")
        for p in patterns[-30:]:
            content = p["content"]
            if len(content) > 90:
                content = content[:90] + "..."
            table.add_row(
                str(p["id"]), p["role_key"],
                f"{p['relevance_score']:.2f}", content,
            )
        console.print(table)
        return

    _report_default()


def _report_default() -> None:
    from .models import MilestoneReport
    from .run_state import PersistentRunState

    prs = PersistentRunState()
    current = prs.load_current()

    if current is None or current.run_id is None:
        console.print("[yellow]No active run[/yellow]")
        raise typer.Exit(code=1)

    # Try to load the report
    run_path = prs.get_run_path(current.run_id)
    report_path = run_path / "final_report.json"

    if not report_path.exists():
        console.print("[yellow]No report found.[/yellow] Run 'oporch run' first.")
        raise typer.Exit(code=1)

    import json
    data = json.loads(report_path.read_text(encoding="utf-8"))
    rpt = MilestoneReport(**data)

    status_style = {
        "COMPLETED": "green",
        "FAILED": "red",
        "CANCELLED": "yellow",
    }.get(rpt.status, "white")

    console.print(f"\n[bold]Milestone Report[/bold]")
    console.print(f"  Objective: {rpt.objective}")
    console.print(f"  Status: [{status_style}]{rpt.status}[/{status_style}]")

    # Work units table
    table = Table(title="Work Units")
    table.add_column("ID", style="bold")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Attempts")

    for wu in rpt.work_units:
        wu_style = {
            "COMPLETED": "green",
            "FAILED": "red",
            "IN_PROGRESS": "blue",
            "BLOCKED": "yellow",
            "PENDING": "white",
        }.get(wu.status.value, "white")
        table.add_row(
            wu.id,
            wu.title,
            f"[{wu_style}]{wu.status.value}[/{wu_style}]",
            str(wu.attempts),
        )

    console.print(table)

    if rpt.files_changed:
        console.print("\n[bold]Files Changed:[/bold]")
        seen: set[str] = set()
        for f in rpt.files_changed:
            if f not in seen:
                console.print(f"  - {f}")
                seen.add(f)

    if rpt.known_limitations:
        console.print("\n[yellow]Known Limitations:[/yellow]")
        for lim in rpt.known_limitations:
            console.print(f"  - {lim}")

    if rpt.unresolved_risks:
        console.print("\n[red]Unresolved Risks:[/red]")
        for risk in rpt.unresolved_risks:
            console.print(f"  - {risk}")

    if rpt.recommendation:
        console.print(f"\n[bold]Recommendation:[/bold] {rpt.recommendation}")


@app.command()
def models() -> None:
    """Show resolved role to model mappings."""
    from .config import resolve_model

    roles = cfg.load_roles()
    table = Table(title="Role to Model Mappings")
    table.add_column("Role", style="bold")
    table.add_column("Model Config")
    table.add_column("Fallback")
    table.add_column("Model ID")
    table.add_column("Status")

    for role_name, role_cfg in roles.roles.items():
        model_id = resolve_model(role_name)
        status = "[green]OK[/green]" if model_id else "[red]UNRESOLVED[/red]"
        table.add_row(
            role_name,
            role_cfg.model,
            role_cfg.fallback or "--",
            model_id or "[red]--none--[/red]",
            status,
        )
    console.print(table)

@app.command()
def logs(
    last: int = typer.Option(
        20, "--last", "-n",
        help="Number of recent events to show",
    ),
) -> None:
    """Show structured event log for current run."""
    from .run_state import PersistentRunState
    from .event_log import EventLog

    prs = PersistentRunState()
    current = prs.load_current()

    if current is None or current.run_id is None:
        console.print("[yellow]No active run[/yellow]")
        raise typer.Exit(code=1)

    event_log = EventLog(current.run_id)
    events = event_log.all()

    if not events:
        console.print("[yellow]No events recorded yet[/yellow]")
        return

    # Show the last N events
    display_events = events[-last:]

    table = Table(title=f"Events (showing last {len(display_events)} of {len(events)})")
    table.add_column("Timestamp", style="dim")
    table.add_column("Event", style="bold")
    table.add_column("Work Unit")
    table.add_column("Role")
    table.add_column("Details")

    for event in display_events:
        ts = event.timestamp.strftime("%H:%M:%S") if event.timestamp else "--"
        wu = event.work_unit_id or "--"
        role = event.agent_role.value if event.agent_role else "--"

        # Summarize details
        detail_parts: list[str] = []
        for k, v in event.details.items():
            if isinstance(v, str) and len(v) > 40:
                v = v[:40] + "..."
            detail_parts.append(f"{k}={v}")
        details = ", ".join(detail_parts[:3]) if detail_parts else "--"

        event_style = {
            "RUN_COMPLETED": "green",
            "WORK_UNIT_COMPLETED": "green",
            "RUN_FAILED": "red",
            "REVIEW_FAILED": "red",
            "TEST_FAILED": "red",
        }.get(event.event.value, "white")

        table.add_row(
            ts,
            f"[{event_style}]{event.event.value}[/{event_style}]",
            wu,
            role,
            details,
        )

    console.print(table)


@app.command()
def view(
    run_id: str = typer.Option(None, "--run-id", help="Run id (defaults to current run)"),
) -> None:
    """Open the live read-only dashboard (safe alongside a running run)."""
    from .dashboard import launch_dashboard

    rid = run_id or _current_run_id()
    if not rid:
        console.print("[yellow]No active run[/yellow] — start one with 'oporch plan' + 'oporch run'")
        raise typer.Exit(code=1)
    launch_dashboard(rid)


@app.command("approvals")
def approvals() -> None:
    """List pending supervisor merge approvals (STRICT mode)."""
    from .db import OporchDB

    db = OporchDB()
    rows = db._query("SELECT key, value FROM control WHERE key LIKE 'merge_pending:%'")
    db.close()

    pending = [r for r in rows if r["value"] == "1"]
    if not pending:
        console.print("[green]No pending merge approvals[/green]")
        return
    for r in pending:
        console.print(f"[yellow]PENDING[/yellow] {r['key']}")
    console.print("\nResolve with: [bold]oporch approve <control-key>[/bold]")


@app.command("approve")
def approve(control_key: str) -> None:
    """Approve a pending supervisor merge or roster spawn."""
    from .db import OporchDB

    if not (
        control_key.startswith("merge_pending:")
        or control_key.startswith("roster_spawn:")
    ):
        console.print("[red]Not an approval key[/red]")
        raise typer.Exit(code=1)

    db = OporchDB()
    current = db.get_control(control_key)
    if current == "approved":
        db.close()
        console.print(f"[yellow]{control_key} already approved[/yellow]")
        return
    if not current:
        db.close()
        console.print(f"[yellow]No pending approval at {control_key}[/yellow]")
        return
    if control_key.startswith("roster_spawn:"):
        from .roster_scaling import RosterScaler

        run_id = control_key.split(":")[1]
        scaler = RosterScaler(db, run_id, phase_count=1)
        ok = scaler.approve_pending_spawn(control_key)
        db.close()
        if ok:
            console.print(f"[green]Spawn approved and applied ({control_key})[/green]")
        else:
            console.print("[red]Failed to apply spawn[/red]")
        return

    db.set_control(control_key, "approved")
    db.close()
    console.print(f"[green]Approved {control_key}[/green]")


@app.command("reject")
def reject(control_key: str) -> None:
    """Reject a pending supervisor merge (STRICT mode)."""
    from .db import OporchDB

    if not control_key.startswith("merge_pending:"):
        console.print("[red]Not a merge approval key[/red]")
        raise typer.Exit(code=1)

    db = OporchDB()
    db.set_control(control_key, "rejected")
    db.close()
    console.print(f"[red]Rejected {control_key}[/red]")


@app.command("merge-integration")
def merge_integration(
    target: str = typer.Argument(..., help="Target branch to merge integration into"),
    run_id: str = typer.Option(None, "--run-id", help="Run id (defaults to current run)"),
) -> None:
    """Merge the run's integration branch into an unprotected base branch.

    Protected branches (never_auto_merge_to) are refused — open a PR instead.
    """
    from .git_manager import GitManager, GitManagerError, ProtectedBranchError

    rid = run_id or _current_run_id()
    if not rid:
        console.print("[yellow]No active run[/yellow]")
        raise typer.Exit(code=1)

    gm = GitManager()
    try:
        sha = gm.merge_integration_into_base(rid, target)
    except ProtectedBranchError as e:
        console.print(f"[red]Refused:[/red] {e}")
        raise typer.Exit(code=1)
    except GitManagerError as e:
        console.print(f"[red]Merge failed:[/red] {e}")
        raise typer.Exit(code=1)
    console.print(f"[green]Merged oporch/{rid}/integration into {target}[/green]")
    console.print(f"  Commit: {sha[:12]}")


@app.command("migrate-db")
def migrate_db() -> None:
    """Backfill legacy runs/*/ JSON + JSONL files into oporch.db."""
    from .db import OporchDB, migrate_legacy_files

    db = OporchDB()
    counts = migrate_legacy_files(db)
    db.close()
    console.print("[green]Migration complete[/green]")
    for k, v in counts.items():
        console.print(f"  {k}: {v}")
    console.print("[dim]Legacy files archived in place (not deleted).[/dim]")


@memory_app.command("list")
def memory_list(
    role: str = typer.Option(None, "--role", "-r", help="Filter by role key"),
    mtype: str = typer.Option(None, "--type", "-t", help="Filter by memory type"),
) -> None:
    """Show what agents have learned on this project."""
    from .db import OporchDB

    db = OporchDB()
    project = str(Path.cwd())
    rows = db.recall(project, role_key=role, limit=1000)
    if mtype:
        rows = [r for r in rows if r["memory_type"] == mtype]
    db.close()

    if not rows:
        console.print("[yellow]No memories recorded yet[/yellow]")
        return

    table = Table(title=f"Agent Memory ({project})")
    table.add_column("ID", style="dim")
    table.add_column("Role", style="bold")
    table.add_column("Type")
    table.add_column("Relevance")
    table.add_column("Content")
    for r in rows:
        content = r["content"]
        if len(content) > 80:
            content = content[:80] + "..."
        table.add_row(
            str(r["id"]),
            r["role_key"],
            r["memory_type"],
            f"{r['relevance_score']:.2f}",
            content,
        )
    console.print(table)


@memory_app.command("add")
def memory_add(
    content: str,
    role: str = typer.Option("builder", "--role", "-r"),
    mtype: str = typer.Option("fact", "--type", "-t"),
) -> None:
    """Manually record a memory (e.g. a convention or gotcha)."""
    from .db import OporchDB

    valid_types = ("fact", "gotcha", "convention", "failure_pattern")
    if mtype not in valid_types:
        console.print(f"[red]Invalid type.[/red] Choose one of: {', '.join(valid_types)}")
        raise typer.Exit(code=1)

    db = OporchDB()
    mid = db.remember(str(Path.cwd()), role, mtype, content)
    db.close()
    console.print(f"[green]Memory #{mid} recorded[/green] for role '{role}'")


@memory_app.command("forget")
def memory_forget(memory_id: int) -> None:
    """Delete a memory by id."""
    from .db import OporchDB

    db = OporchDB()
    removed = db.forget(memory_id)
    db.close()
    if removed:
        console.print(f"[green]Memory #{memory_id} forgotten[/green]")
    else:
        console.print(f"[yellow]No memory with id {memory_id}[/yellow]")


@memory_app.command("export")
def memory_export(
    out: Path = typer.Option(
        Path(".opencode-orchestrator") / "memory_export.jsonl",
        "--out", "-o",
        help="JSONL output path (git-friendly portable snapshot)",
    ),
) -> None:
    """Export agent_memory rows to JSONL for committing to git."""
    from .db import OporchDB

    db = OporchDB()
    n = db.export_memory(out)
    db.close()
    console.print(f"[green]Exported {n} memories[/green] to {out}")


@memory_app.command("import")
def memory_import(
    src: Path = typer.Argument(..., help="JSONL file previously written by memory export"),
) -> None:
    """Import agent memories from an exported JSONL snapshot."""
    from .db import OporchDB

    db = OporchDB()
    n = db.import_memory(src)
    db.close()
    console.print(f"[green]Imported {n} memories[/green] from {src}")


def _current_run_id() -> str | None:
    from .run_state import PersistentRunState

    current = PersistentRunState().load_current()
    return current.run_id if current and current.run_id else None


@team_app.command("show")
def team_show(
    run_id: str = typer.Option(None, "--run-id", help="Run id (defaults to current run)"),
) -> None:
    """Print the active roster for a run."""
    from .db import OporchDB

    rid = run_id or _current_run_id()
    if not rid:
        console.print("[yellow]No active run[/yellow]")
        raise typer.Exit(code=1)

    db = OporchDB()
    roles = db.get_roster(rid)
    history = db.get_roster_history(rid)
    db.close()

    if not roles:
        console.print(f"[yellow]No roster found for run {rid}[/yellow]")
        raise typer.Exit(code=1)

    table = Table(title=f"Team Roster · run {rid} ({len(roles)} active)")
    table.add_column("Role", style="bold")
    table.add_column("Description")
    table.add_column("Model")
    table.add_column("Fallback")
    table.add_column("Workers")
    table.add_column("Domains")
    for r in roles:
        domains = ", ".join(r["domains"]) if r["domains"] else "--"
        cross = " ✓" if r["role_key"] in ("reviewer", "tester") else ""
        table.add_row(
            r["role_key"] + cross,
            (r["description"] or "")[:60],
            r["model"] or "--",
            r["fallback"] or "--",
            str(r["max_workers"]),
            domains,
        )
    console.print(table)

    retired = [h for h in history if h["active_until"]]
    if retired:
        console.print(f"[dim]{len(retired)} retired role(s) in history — see 'oporch team history'[/dim]")


@team_app.command("edit")
def team_edit(
    run_id: str = typer.Option(None, "--run-id", help="Run id (defaults to current run)"),
) -> None:
    """Interactively edit the roster before approval.

    Commands: list / rename OLD NEW / merge A INTO_B / split KEY /
    workers KEY N / remove KEY / add KEY / done
    """
    from .db import OporchDB

    rid = run_id or _current_run_id()
    if not rid:
        console.print("[yellow]No active run[/yellow]")
        raise typer.Exit(code=1)

    db = OporchDB()
    try:
        while True:
            roles = db.get_roster(rid)
            console.print(f"\n[bold]Roster for {rid}:[/bold] " + ", ".join(r["role_key"] for r in roles))
            cmd = typer.prompt("team edit command (or 'done')", default="list").strip()
            parts = cmd.split()
            verb = parts[0].lower() if parts else "list"

            if verb in ("done", "q", "quit", "approve"):
                break
            elif verb == "list":
                continue
            elif verb == "rename" and len(parts) >= 3:
                db._execute(
                    "UPDATE roster SET role_key = ? WHERE run_id = ? AND role_key = ?"
                    " AND active_until IS NULL",
                    (parts[2], rid, parts[1]),
                )
                console.print(f"[green]Renamed {parts[1]} -> {parts[2]}[/green]")
            elif verb == "merge" and len(parts) >= 3:
                src, dst = parts[1], parts[2]
                rows = [r for r in db.get_roster(rid) if r["role_key"] == src]
                if not rows:
                    console.print(f"[red]No role '{src}'[/red]")
                    continue
                merged_domains = rows[0]["domains"]
                dst_rows = [r for r in roles if r["role_key"] == dst]
                if dst_rows:
                    new_domains = sorted(set(dst_rows[0]["domains"]) | set(merged_domains))
                    import json as _json

                    db._execute(
                        "UPDATE roster SET domains = ? WHERE run_id = ? AND role_key = ?"
                        " AND active_until IS NULL",
                        (_json.dumps(new_domains), rid, dst),
                    )
                db.retire_role(rid, src)
                console.print(f"[green]Merged {src} into {dst}[/green]")
            elif verb == "split" and len(parts) >= 2:
                src = parts[1]
                rows = [r for r in db.get_roster(rid) if r["role_key"] == src]
                if not rows:
                    console.print(f"[red]No role '{src}'[/red]")
                    continue
                src_row = rows[0]
                half = max(1, src_row["max_workers"] // 2)
                new_key = f"{src}-2"
                db.save_roster(
                    rid,
                    [{
                        "role_key": new_key,
                        "description": f"Split from {src}",
                        "model": src_row["model"],
                        "fallback": src_row["fallback"],
                        "max_workers": half,
                        "domains": src_row["domains"],
                    }],
                )
                db.resize_role(rid, src, half)
                console.print(f"[green]Split {src} -> {new_key}[/green]")
            elif verb == "workers" and len(parts) >= 3:
                try:
                    n = int(parts[2])
                except ValueError:
                    console.print("[red]N must be an integer[/red]")
                    continue
                db.resize_role(rid, parts[1], n)
                console.print(f"[green]{parts[1]} max_workers -> {n}[/green]")
            elif verb == "remove" and len(parts) >= 2:
                remaining = [r["role_key"] for r in db.get_roster(rid)]
                if len(remaining) <= 1:
                    console.print("[red]Cannot remove the last role[/red]")
                    continue
                db.retire_role(rid, parts[1])
                console.print(f"[green]Retired {parts[1]}[/green]")
            elif verb == "add" and len(parts) >= 2:
                db.save_roster(
                    rid,
                    [{
                        "role_key": parts[1],
                        "description": f"{parts[1]} agent",
                        "model": "deepseek-v4-flash",
                        "max_workers": 2,
                        "domains": [],
                    }],
                )
                console.print(f"[green]Added {parts[1]}[/green]")
            else:
                console.print("[yellow]Unknown command. Use: list rename merge split workers remove add done[/yellow]")
    finally:
        db.close()
    console.print("[green]Roster edit complete[/green]")


@team_app.command("history")
def team_history(
    run_id: str = typer.Option(None, "--run-id", help="Run id (defaults to current run)"),
) -> None:
    """Show the roster timeline for a run."""
    from .db import OporchDB

    rid = run_id or _current_run_id()
    if not rid:
        console.print("[yellow]No active run[/yellow]")
        raise typer.Exit(code=1)

    db = OporchDB()
    history = db.get_roster_history(rid)
    db.close()

    if not history:
        console.print(f"[yellow]No roster history for run {rid}[/yellow]")
        return

    table = Table(title=f"Roster Timeline · run {rid}")
    table.add_column("Role", style="bold")
    table.add_column("Model")
    table.add_column("Workers")
    table.add_column("Active From")
    table.add_column("Active Until")
    for h in history:
        until = h["active_until"] or "[green]active[/green]"
        table.add_row(
            h["role_key"], h["model"] or "--", str(h["max_workers"]),
            (h["active_from"] or "")[:19].replace("T", " "),
            str(until)[:19].replace("T", " ") if h["active_until"] else str(until),
        )
    console.print(table)


@app.command()
def cancel() -> None:
    """Cancel current run."""
    from .run_state import PersistentRunState
    prs = PersistentRunState()
    current = prs.load_current()
    if current and current.run_id:
        prs.clear_current()
        console.print("[red]Run cancelled[/red]")
    else:
        console.print("[yellow]No active run to cancel[/yellow]")


def _write_default_roles() -> None:
    import yaml
    data = {
        "roles": {
            "orchestrator": {
                "description": "Controls overall milestone execution, delegates work, evaluates evidence",
                "model": "deepseek-v4-flash",
                "max_workers": 1,
            },
            "planner": {
                "description": "Analyzes objectives and produces atomic work units",
                "model": "deepseek-v4-flash",
                "max_workers": 1,
            },
            "architect": {
                "description": "Reviews architectural impact and identifies structural risks",
                "model": "deepseek-v4-flash",
                "max_workers": 1,
            },
            "builder": {
                "description": "Implements work units with smallest coherent changes",
                "model": "deepseek-v4-flash",
                "max_workers": 3,
            },
            "reviewer": {
                "description": "Adversarial code review against acceptance criteria",
                "model": "nemotron-ultra",
                "fallback": "deepseek-v4-flash",
                "max_workers": 1,
            },
            "tester": {
                "description": "Independent validation of acceptance criteria",
                "model": "nemotron-ultra",
                "fallback": "deepseek-v4-flash",
                "max_workers": 1,
            },
            "debugger": {
                "description": "Root-cause analysis of failures",
                "model": "mimo-v2.5",
                "fallback": "deepseek-v4-flash",
                "max_workers": 1,
            },
            "researcher": {
                "description": "External library and documentation investigation",
                "model": "deepseek-v4-flash",
                "max_workers": 1,
            },
            "benchmark_analyst": {
                "description": "Before/after metrics comparison and drift detection",
                "model": "nemotron-ultra",
                "fallback": "deepseek-v4-flash",
                "max_workers": 1,
            },
            "supervisor": {
                "description": "Merge gate: re-diffs WU branches and merges into integration",
                "model": "nemotron-ultra",
                "fallback": "deepseek-v4-flash",
                "max_workers": 1,
            },
        }
    }
    with open(CONFIG_DIR / "roles.yaml", "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False)


def _write_default_policies() -> None:
    import yaml
    data = {
        "approval_mode": "SUPERVISED",
        "retry": {
            "max_attempts": 3,
            "attempt_2_receives_review": True,
            "attempt_3_uses_debugger": True,
        },
        "completion_gate": {
            "require_review_approval": True,
            "require_tests_pass": True,
            "require_benchmark_evidence": False,
            "max_critical_findings": 0,
            "max_high_findings": 0,
            "require_supervisor_merge": False,
        },
        "context": {
            "include_relevant_prd_sections": True,
            "include_prior_decisions": True,
            "include_dependency_outputs": True,
        },
        "merge_conflict": {
            "route": "debugger",
            "max_debugger_attempts": 1,
        },
        "security": {
            "never_auto_merge_to": ["main", "develop", "master"],
            "strict_disables_auto_merge": True,
        },
    }
    with open(CONFIG_DIR / "policies.yaml", "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False)


def _write_default_models() -> None:
    import yaml
    data = {
        "models": {
            "deepseek-v4-flash": {
                "provider": "deepseek",
                "model_id": "opencode/deepseek-v4-flash-free",
                "context_limit": 131072,
                "output_limit": 16384,
            },
            "nemotron-ultra": {
                "provider": "nvidia",
                "model_id": "opencode/nemotron-3-ultra-free",
                "context_limit": 131072,
                "output_limit": 16384,
            },
            "mimo-v2.5": {
                "provider": "deepseek",
                "model_id": "opencode/mimo-v2.5-free",
                "context_limit": 131072,
                "output_limit": 16384,
            },
        }
    }
    with open(CONFIG_DIR / "models.yaml", "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False)


if __name__ == "__main__":
    app()
