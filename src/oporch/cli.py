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
        wu_tree = Tree("Work Units")
        for wu in work_units:
            status_style = {
                "COMPLETED": "green",
                "IN_PROGRESS": "blue",
                "FAILED": "red",
                "PENDING": "white",
                "BLOCKED": "yellow",
            }.get(wu.status.value, "white")
            node = wu_tree.add(
                f"[{status_style}]{wu.id}[/{status_style}]: {wu.title} "
                f"([{status_style}]{wu.status.value}[/{status_style}])"
            )
            if wu.blockers:
                for b in wu.blockers:
                    node.add(f"[red]blocked: {b}[/red]")
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
def run(milestone_id: str) -> None:
    """Execute approved milestone."""
    console.print(f"[yellow]Run execution for {milestone_id} not yet implemented[/yellow]")
    console.print("This will be implemented in a future milestone.")


@app.command()
def resume() -> None:
    """Resume interrupted run."""
    console.print("[yellow]Resume not yet implemented[/yellow]")


@app.command()
def report() -> None:
    """Generate evidence-backed final report."""
    console.print("[yellow]Report generation not yet implemented[/yellow]")


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
