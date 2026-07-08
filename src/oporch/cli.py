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
    milestone_id: str,
    objective: str = "",
) -> None:
    """Generate milestone work graph without coding."""
    from .orchestrator import HeadOrchestrator
    from .validate import validate_planner_output

    orchestrator = HeadOrchestrator()
    validation, plan_or_question = orchestrator.plan_milestone(milestone_id, objective)

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
        table.add_row(wu.id, wu.title, wu.assigned_role.value, deps, wu.status.value)

    console.print(table)

    if plan.assumptions:
        console.print("\n[yellow]Assumptions:[/yellow]")
        for a in plan.assumptions:
            console.print(f"  - {a}")

    validation_result = typer.prompt("\nApprove plan? (yes/no)", default="yes")
    if validation_result.lower() not in ("y", "yes"):
        console.print("[red]Plan rejected[/red]")
        raise typer.Exit(code=1)

    console.print("[green]Plan approved[/green]")


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
def report() -> None:
    """Generate evidence-backed final report."""
    from .run_state import PersistentRunState
    from .models import MilestoneReport

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
        },
        "context": {
            "include_relevant_prd_sections": True,
            "include_prior_decisions": True,
            "include_dependency_outputs": True,
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
