# oporch — Comprehensive Project Documentation

> **Multi-Agent Orchestration System for OpenCode**
> Decompose milestones → parallel DAG of work units → specialized AI agents → evidence collection → gated completion

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Module Reference](#module-reference)
4. [CLI Command Reference](#cli-command-reference)
5. [Data Flow & Lifecycle](#data-flow--lifecycle)
6. [State Machine](#state-machine)
7. [Configuration System](#configuration-system)
8. [Data Storage](#data-storage)
9. [Team Composition & Roster](#team-composition--roster)
10. [Parallel Execution Engine](#parallel-execution-engine)
11. [Git Isolation & Merge Gate](#git-isolation--merge-gate)
12. [Agent Memory System](#agent-memory-system)
13. [Security & Redaction](#security--redaction)
14. [Observability & Diagnostics](#observability--diagnostics)
15. [Live Dashboard (TUI)](#live-dashboard-tui)
16. [Roster Auto-Scaling](#roster-auto-scaling)
17. [Error Handling](#error-handling)
18. [Testing](#testing)
19. [Gap Analysis — Missing & Incomplete Features](#gap-analysis--missing--incomplete-features)

---

## Overview

**oporch** is a CLI-driven multi-agent orchestration system that sits on top of [OpenCode](https://opencode.ai). It automates the entire software engineering lifecycle — planning, team composition, parallel execution, adversarial code review, testing, debugging, and evidence-gated completion — across 5–9+ specialized AI agents.

### Key Design Principles

| Principle | How oporch implements it |
|-----------|--------------------------|
| **One orchestrator, many agents** | `HeadOrchestrator` manages state, routing, retries, and escalation |
| **Dynamic teams** | Roster is sized per-plan, not a fixed enum |
| **Parallel execution** | Asyncio dispatcher with per-role semaphore concurrency |
| **Git isolation** | One worktree per work unit — agents never share a working directory |
| **Evidence-gated completion** | Review + test + supervisor merge must all pass before `COMPLETED` |
| **Persistent memory** | Cross-run fact/gotcha/convention/failure_pattern store in SQLite |
| **Secrets never enter context** | `redact.py` scrubs all payloads before persistent storage |

### Requirements

- Python 3.12+
- [opencode](https://opencode.ai) CLI
- git
- Dependencies: `pydantic>=2`, `typer>=0.12`, `rich>=13`, `pyyaml>=6`, `textual>=0.50`

---

## Architecture

### High-Level Data Flow

```mermaid
graph TD
    A["oporch plan &lt;doc.md&gt;"] --> B[ANALYZING]
    B --> C[COMPOSING_TEAM]
    C --> D[PLANNING]
    D --> E[AWAITING_PLAN_APPROVAL]
    E --> F[EXECUTING — Parallel Waves]
    F --> G[REVIEWING]
    G --> H[TESTING]
    H --> I[VALIDATING]
    I --> J[COMPLETED]
    F --> K["DEBUGGING (retry ladder)"]
    K --> F
    F --> L["MERGE_CONFLICT → Debugger / User"]
```

### Module Dependency Graph

```mermaid
graph LR
    CLI["cli.py"] --> ORCH["orchestrator.py"]
    CLI --> DB["db.py"]
    CLI --> DASH["dashboard.py"]
    CLI --> SCALE["roster_scaling.py"]
    CLI --> GIT["git_manager.py"]
    ORCH --> RUNNER["runner.py"]
    ORCH --> TC["team_composer.py"]
    ORCH --> SM["state_machine.py"]
    ORCH --> EL["event_log.py"]
    ORCH --> EXEC["executor.py"]
    ORCH --> CB["context_builder.py"]
    ORCH --> VAL["validate.py"]
    ORCH --> RS["run_state.py"]
    RUNNER --> EXEC
    RUNNER --> CB
    RUNNER --> DB
    RUNNER --> SCALE
    DB --> REDACT["redact.py"]
    GIT --> REDACT
    EXEC --> CFG["config.py"]
    MODELS["models.py"] --> CONST["constants.py"]
```

---

## Module Reference

### Core Modules

| Module | Lines | File | Responsibility |
|--------|------:|------|----------------|
| [`constants.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/constants.py) | 136 | Enums | 15 orchestrator states, 10 agent roles, 8 WU statuses, 25 event types, approval modes, severities, claim types |
| [`models.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/models.py) | 398 | Pydantic v2 | 30+ domain schemas: `WorkUnit`, `TeamRole`, `TeamRoster`, `PlanResult`, `AgentResult`, `MilestoneReport`, `ContextPack`, policies, configs |
| [`config.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/config.py) | ~67 | YAML I/O | `load_roles()`, `load_policies()`, `load_models()`, `resolve_model()` with fallback chain |
| [`state_machine.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/state_machine.py) | ~61 | FSM | Transition table, history tracking, terminal/active detection |

### Orchestration & Execution

| Module | Lines | File | Responsibility |
|--------|------:|------|----------------|
| [`orchestrator.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/orchestrator.py) | 374 | Head Orchestrator | `plan_milestone()`, `run_milestone()`, `resume_run()`, `compose_roster()`, state machine driving |
| [`runner.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/runner.py) | 894 | Milestone Runner | `MilestoneRunner` (Builder→Reviewer→Tester pipeline), `ParallelDispatcher` (async waves, semaphores) |
| [`executor.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/executor.py) | 276 | Agent Executors | `FakeAgentExecutor` (testing), `OpenCodeAgentExecutor` (real subprocess dispatch), `build_restricted_env()` for sandboxing |
| [`team_composer.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/team_composer.py) | 273 | Dynamic Roster | `compose_team()`, `sizing_band()`, keyword-based domain inference, agent-driven roster with heuristic fallback |
| [`context_builder.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/context_builder.py) | 252 | Role-Specific Context | `parse_plan_doc()`, `build_context_for_role()`, per-role context builders (Builder/Reviewer/Tester/Debugger) |

### Storage & Persistence

| Module | Lines | File | Responsibility |
|--------|------:|------|----------------|
| [`db.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/db.py) | 713 | SQLite Layer | WAL-mode database with 7 tables, CRUD for runs/roster/WUs/events/decisions/memory/control, migration, export/import |
| [`run_state.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/run_state.py) | ~153 | JSON File I/O | `PersistentRunState`: legacy JSON persistence for runs, WUs, plans (mirrored through `db.py`) |
| [`event_log.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/event_log.py) | ~61 | Event JSONL | `EventLog`: JSONL append, cache, filter by type |
| [`decision_ledger.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/decision_ledger.py) | ~64 | Decision JSONL | `DecisionLedger`: JSONL Q&A, search, find-by-question |

### Git, Security, Diagnostics

| Module | Lines | File | Responsibility |
|--------|------:|------|----------------|
| [`git_manager.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/git_manager.py) | 365 | Git Isolation | Per-WU worktrees, integration branch, supervisor merge gate, push blocking, conflict detection, protected branch enforcement |
| [`redact.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/redact.py) | 94 | Secret Scrubbing | 14 regex patterns (Bearer, API keys, PEM, JWT, GitHub/Slack tokens, connection strings, .env assignments), sensitive path detection |
| [`dashboard.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/dashboard.py) | 526 | Live TUI | Textual app polling SQLite, role panels, WU cards, progress bar, event tail, pause control |
| [`doctor.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/doctor.py) | ~115 | Health Checks | 8 checks: opencode CLI, config init, roles/policies/models YAML, git, writability, pytest |
| [`roster_scaling.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/roster_scaling.py) | 291 | Auto-Scaling | Phase-boundary roster adjustment: spawn/retire/resize, sizing band enforcement, approval gating |
| [`validate.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/validate.py) | ~90 | JSON Repair | Planner output validation, code fence stripping, brace extraction, auto-repair |
| [`work_unit.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/work_unit.py) | ~111 | DAG | `WorkUnitGraph`: add/get/ready, topological sort, cycle detection |

### Prompts

| File | Purpose |
|------|---------|
| [`prompts/planner.md`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/prompts) | System prompt template for the Planner agent during `plan_milestone()` |
| [`prompts/team_composer.md`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/prompts/team_composer.md) | System prompt template for the team composer agent; includes sizing bands, domain clustering rules, output JSON schema |

---

## CLI Command Reference

### Core Workflow Commands

| Command | Description | Status |
|---------|-------------|--------|
| `oporch init` | Create `.opencode-orchestrator/` with default configs | ✅ |
| `oporch plan <source> [--objective] [--milestone-id] [--yes]` | Generate work graph from an objective or plan document. Supports plan-doc → team composition → planning | ✅ |
| `oporch run <milestone_id> [--executor fake\|opencode] [--verbose]` | Execute an approved plan through the parallel pipeline | ✅ |
| `oporch resume [--executor]` | Resume an interrupted run from where it left off | ✅ |
| `oporch status` | Show active run state, milestone, work unit tree with blockers | ✅ |
| `oporch cancel` | Cancel the active run | ✅ |
| `oporch report [--failures]` | Generate evidence-backed final report, or aggregate failure patterns across all runs | ✅ |

### Team Roster Commands

| Command | Description |
|---------|-------------|
| `oporch team show [--run-id]` | Print the active roster for a run (roles, models, workers, domains) |
| `oporch team edit [--run-id]` | Interactive roster editor: rename, merge, split, add/remove roles, adjust workers |
| `oporch team history [--run-id]` | Show the roster timeline — which roles were active during which phases |

### Agent Memory Commands

| Command | Description |
|---------|-------------|
| `oporch memory list [--role] [--type]` | Show what agents have learned on this project |
| `oporch memory add <content> [--role] [--type]` | Manually record a fact, gotcha, convention, or failure_pattern |
| `oporch memory forget <id>` | Delete a memory by ID |
| `oporch memory export [--out]` | Export agent_memory rows to a git-friendly JSONL file |
| `oporch memory import <src>` | Import memories from an exported JSONL snapshot |

### Observability Commands

| Command | Description |
|---------|-------------|
| `oporch replay <run_id> [--wu] [--limit]` | Reconstruct what happened in a run — chronological event stream with role/WU labels |
| `oporch diff <run_a> <run_b>` | Compare two runs: duration, attempts, failure rate per role |
| `oporch logs [--last N]` | Show the structured event log for the current run |
| `oporch models` | Print resolved role→model→model_id mappings |
| `oporch doctor` | 8 environment health checks |
| `oporch view [--run-id]` | Open the live read-only TUI dashboard |

### Git & Approval Commands

| Command | Description |
|---------|-------------|
| `oporch merge-integration <target> [--run-id]` | Merge the run's integration branch into an unprotected base branch. Protected branches are refused. |
| `oporch approvals` | List pending supervisor merge approvals (STRICT mode) |
| `oporch approve <control-key>` | Approve a pending merge or roster spawn |
| `oporch reject <control-key>` | Reject a pending supervisor merge |
| `oporch migrate-db` | Backfill legacy JSON/JSONL files into `oporch.db` |

---

## Data Flow & Lifecycle

### Full Run Lifecycle

```
1. oporch init
   └── Creates .opencode-orchestrator/{config,state,context,runs,locks}/
       Creates default roles.yaml, policies.yaml, models.yaml

2. oporch plan phase15.md
   ├── IDLE → ANALYZING
   │   └── Scans src/, PRD.md for repo context → project_summary.md
   ├── ANALYZING → COMPOSING_TEAM
   │   └── Parses plan doc into Phase objects
   │   └── compose_team() proposes a dynamic roster
   │   └── Saves roster to SQLite
   ├── COMPOSING_TEAM → PLANNING
   │   └── Planner agent decomposes into WorkUnitGraph
   │   └── Validates JSON output (auto-repairs code fences, brace extraction)
   │   └── Supports planner asking clarification questions
   └── PLANNING → AWAITING_PLAN_APPROVAL
       └── User approves (or edits roster interactively)

3. oporch run M1
   ├── AWAITING_PLAN_APPROVAL → EXECUTING
   │   └── ParallelDispatcher pulls READY WUs, groups by role
   │   └── Bounded concurrency via asyncio.Semaphore per role
   │   └── Wave loop: repeat until DAG drains
   │
   │   For each WU:
   │   ├── Builder (assigned roster role) implements
   │   ├── Reviewer adversarially reviews (policy-gated)
   │   ├── Tester validates acceptance criteria
   │   └── Retry ladder:
   │       ├── Attempt 2: receives prior review feedback
   │       └── Attempt 3: debugger diagnoses first
   │
   │   Phase boundary → RosterScaler checks for spawn/retire/resize
   │
   ├── EXECUTING → REVIEWING → TESTING → VALIDATING
   └── VALIDATING → COMPLETED or FAILED

4. oporch report
   └── Evidence-backed final report with files changed, test results,
       review findings, decisions, escalations, limitations, risks

5. oporch view (separate terminal)
   └── Live TUI polling oporch.db every 500ms
       └── Role panels, WU cards, progress bar, event tail
```

### Agent Pipeline Per Work Unit

```
┌──────────────────────────────────────────────────────────┐
│  1. Context Building                                      │
│     build_context_for_role() → ContextPack               │
│     Includes: PRD sections, relevant files, architecture │
│     constraints, dependency outputs, project memory      │
├──────────────────────────────────────────────────────────┤
│  2. Builder (assigned_role from roster)                   │
│     OpenCodeAgentExecutor → opencode -p <prompt> -m <id> │
│     Working dir: per-WU git worktree                     │
├──────────────────────────────────────────────────────────┤
│  3. Reviewer (cross-cutting)                              │
│     Adversarial review against acceptance criteria        │
│     Verdict: APPROVE / REQUEST_CHANGES / BLOCK           │
├──────────────────────────────────────────────────────────┤
│  4. Tester (cross-cutting)                                │
│     Validates acceptance criteria, runs tests             │
│     Records: passed/failed/skipped counts                │
├──────────────────────────────────────────────────────────┤
│  5. Supervisor Merge Gate                                 │
│     merge_wu_into_integration() — squash merge            │
│     Protected branches refused (never_auto_merge_to)     │
│     Conflict → MERGE_CONFLICT → debugger or user         │
└──────────────────────────────────────────────────────────┘
```

---

## State Machine

### 15 States

```python
class OrchestratorState(str, Enum):
    IDLE               # No run active
    ANALYZING          # Scanning repo, parsing plan doc
    COMPOSING_TEAM     # Dynamic roster proposal (v2)
    PLANNING           # Planner agent decomposing into WU DAG
    AWAITING_PLAN_APPROVAL  # User review of plan + roster
    EXECUTING          # Parallel agent dispatch
    REVIEWING          # Adversarial code review
    TESTING            # Test validation
    DEBUGGING          # Retry with debugger
    REPLANNING         # Re-plan after failures
    AWAITING_USER_INPUT # Escalation to human
    VALIDATING         # Final evidence check
    COMPLETED          # Terminal: success
    FAILED             # Terminal: failure
    CANCELLED          # Terminal: user cancelled
```

### Transition Table

```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> ANALYZING
    IDLE --> CANCELLED
    ANALYZING --> COMPOSING_TEAM
    ANALYZING --> PLANNING
    ANALYZING --> FAILED
    ANALYZING --> CANCELLED
    COMPOSING_TEAM --> PLANNING
    COMPOSING_TEAM --> AWAITING_USER_INPUT
    COMPOSING_TEAM --> FAILED
    COMPOSING_TEAM --> CANCELLED
    PLANNING --> AWAITING_PLAN_APPROVAL
    PLANNING --> FAILED
    PLANNING --> CANCELLED
    AWAITING_PLAN_APPROVAL --> EXECUTING
    AWAITING_PLAN_APPROVAL --> REPLANNING
    AWAITING_PLAN_APPROVAL --> CANCELLED
    EXECUTING --> REVIEWING
    EXECUTING --> REPLANNING
    EXECUTING --> FAILED
    EXECUTING --> CANCELLED
    REVIEWING --> TESTING
    REVIEWING --> EXECUTING
    REVIEWING --> AWAITING_USER_INPUT
    REVIEWING --> FAILED
    REVIEWING --> CANCELLED
    TESTING --> VALIDATING
    TESTING --> DEBUGGING
    TESTING --> CANCELLED
    DEBUGGING --> EXECUTING
    DEBUGGING --> REPLANNING
    DEBUGGING --> AWAITING_USER_INPUT
    DEBUGGING --> CANCELLED
    REPLANNING --> PLANNING
    REPLANNING --> AWAITING_USER_INPUT
    REPLANNING --> FAILED
    REPLANNING --> CANCELLED
    AWAITING_USER_INPUT --> ANALYZING
    AWAITING_USER_INPUT --> EXECUTING
    AWAITING_USER_INPUT --> REPLANNING
    AWAITING_USER_INPUT --> CANCELLED
    VALIDATING --> COMPLETED
    VALIDATING --> EXECUTING
    VALIDATING --> REPLANNING
    VALIDATING --> FAILED
    VALIDATING --> CANCELLED
    COMPLETED --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
```

Every transition is validated against the table, timestamped, and persisted. Invalid transitions raise `InvalidTransitionError`.

---

## Configuration System

### Directory Layout

```
.opencode-orchestrator/
├── config/
│   ├── roles.yaml       — 9+ agent roles with model + fallback
│   ├── models.yaml      — Logical model keys → real OpenCode model IDs
│   └── policies.yaml    — Approval mode, retry, completion gates, security
├── state/               — current_run.json, decisions.jsonl
├── context/             — Auto-generated project_summary.md
├── runs/                — Per-run state, plans, events, worker outputs
├── locks/
├── worktrees/           — Per-WU git worktrees (created at runtime)
└── oporch.db            — SQLite database (WAL mode)
```

### `roles.yaml` — Agent Role Definitions

9 built-in roles with model assignments, fallbacks, and concurrency limits:

```yaml
roles:
  planner:
    description: "Decomposes milestones into work unit DAGs"
    model: "nemotron-ultra"
    max_workers: 1
  builder:
    description: "Implements work units with smallest coherent changes"
    model: "deepseek-v4-flash"
    max_workers: 3
  reviewer:
    description: "Adversarial code review against acceptance criteria"
    model: "nemotron-ultra"
    fallback: "deepseek-v4-flash"
    max_workers: 1
  tester:
    model: "nemotron-ultra"
    fallback: "deepseek-v4-flash"
  debugger:
    model: "mimo-v2.5"
    fallback: "deepseek-v4-flash"
  supervisor:
    description: "Merge gate — only role that can merge into integration"
    model: "nemotron-ultra"
  benchmark_analyst:
    model: "nemotron-ultra"
    fallback: "deepseek-v4-flash"
```

### `models.yaml` — Model Resolution

```yaml
models:
  deepseek-v4-flash:
    provider: "deepseek"
    model_id: "opencode/deepseek-v4-flash-free"
    context_limit: 131072
    output_limit: 16384
    tier: "fast"
  nemotron-ultra:
    provider: "nvidia"
    model_id: "opencode/nemotron-3-ultra-free"
    tier: "heavy"
  mimo-v2.5:
    provider: "deepseek"
    model_id: "opencode/mimo-v2.5-free"
    tier: "standard"
```

**Resolution chain**: role config → logical model key → `models.yaml` lookup → real `model_id`. If primary not found, uses `fallback`. Returns `None` if neither resolves.

### `policies.yaml` — Behavior Policies

```yaml
approval_mode: SUPERVISED        # SUPERVISED | AUTONOMOUS | STRICT
retry:
  max_attempts: 3
  attempt_2_receives_review: true
  attempt_3_uses_debugger: true
completion_gate:
  require_review_approval: true
  require_tests_pass: true
  require_benchmark_evidence: false
  require_supervisor_merge: false
merge_conflict:
  route: "debugger"              # "debugger" or "user"
  max_debugger_attempts: 1
security:
  never_auto_merge_to: ["main", "develop", "master"]
  strict_disables_auto_merge: true
roster_auto_scale:
  enabled: false
  require_approval_for_spawn: true
```

---

## Data Storage

### SQLite Schema (`oporch.db`)

7 tables with WAL mode for concurrent reader/writer access:

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `runs` | Run metadata | `id`, `milestone_id`, `plan_source_path`, `state`, timestamps |
| `roster` | Dynamic team roles | `run_id`, `role_key`, `model`, `max_workers`, `domains`, `allowed_paths`, `active_from`/`active_until` |
| `work_units` | Work unit DAG nodes | `(run_id, id)` PK, `phase`, `assigned_role`, `status`, `depends_on`, `attempt`, `evidence` |
| `events` | Structured event stream | `run_id`, `ts`, `event_type`, `role`, `wu_id`, `level`, `duration_ms`, `model_used`, `tokens_in`/`out`, `payload` |
| `decisions` | Q&A ledger | `run_id`, `question`, `answer`, `asked_by_role` |
| `agent_memory` | Cross-run persistent memory | `project_path`, `role_key`, `memory_type`, `content`, `relevance_score` |
| `control` | Cooperative control channel | `key`, `value` — used for pause flag, pending approvals |

All text payloads are passed through `redact_secrets()` before insertion.

### Legacy File Mirror

The `run_state.py`, `event_log.py`, and `decision_ledger.py` modules still write to JSON/JSONL files under `runs/` for backward compatibility, while also writing through to SQLite. `oporch migrate-db` backfills legacy files into the database.

---

## Team Composition & Roster

### Sizing Bands

Roster size scales with plan phase count — no hard ceiling:

| Phases | Agents |
|--------|--------|
| 1–6 | 3–4 |
| 7–12 | 5–6 |
| 13–20 | 7–9 |
| 20+ | +1 per 3 extra phases |

### Composition Process

1. **Parse plan document** → `Phase` objects via `parse_plan_doc()`
2. **Infer domains** → keyword matching against 6 domain categories (`backend`, `frontend`, `db`, `infra`, `qa`, `docs`)
3. **Agent-driven roster** (preferred) → Planner agent proposes a `ROSTER` JSON via `prompts/team_composer.md`
4. **Heuristic fallback** (always works) → Deterministic domain clustering if agent output is unusable
5. **Cross-cutting gates** → `reviewer` and `tester` are always injected regardless of domain clustering
6. **Band fitting** → Trim or pad roles to stay within the sizing band

### Roster Lifecycle

- Created at `COMPOSING_TEAM` state, saved to SQLite `roster` table
- User can edit interactively via `oporch team edit` (rename, merge, split, add/remove, adjust workers)
- Auto-scaled at phase boundaries by `RosterScaler` (spawn/retire/resize)
- Timeline tracked via `active_from`/`active_until` columns

---

## Parallel Execution Engine

### ParallelDispatcher

```python
class ParallelDispatcher:
    semaphores: dict[str, asyncio.Semaphore]   # per-role concurrency
    active: set[str]                            # currently running WU ids

    async def run_ready_wave(...)  # Gather all ready WUs, run concurrently
    async def run_pending_drain(...)  # Wave loop until DAG drains
    def resize(role_key, new_max_workers)  # Live concurrency adjustment
```

### Wave Execution Loop

```
while not graph.all_completed():
    if cancelled: return
    await wait_if_paused()          # cooperative pause via control table
    ready = graph.get_ready(completed_ids)
    if not ready: return            # deadlock or all blocked
    results = await run_ready_wave(ready)
    for wu, ok in zip(ready, results):
        if ok:
            completed_ids.add(wu.id)
            if phase_complete:
                scaler.on_phase_complete(phase)    # auto-scaling check
        else:
            failed_ids.add(wu.id)
    save_work_units()
```

### Retry Ladder (Per Work Unit)

| Attempt | Strategy |
|---------|----------|
| 1 | Builder implements |
| 2 | Builder receives prior review feedback (`attempt_2_receives_review`) |
| 3 | Debugger diagnoses first, then Builder re-attempts (`attempt_3_uses_debugger`) |
| >3 | Escalate to `AWAITING_USER_INPUT` |

### Cooperative Pause

The `control` table in SQLite provides a cooperative pause mechanism:
- `oporch view` sets `control.pause = "1"` when user presses `p`
- The dispatcher polls this value between waves via `wait_if_paused()`
- No signals or IPC required — pure DB-based coordination

---

## Git Isolation & Merge Gate

### Per-WU Worktree Isolation

```
.opencode-orchestrator/worktrees/
├── wu-001/     ← git worktree for WU-001
├── wu-002/     ← git worktree for WU-002
├── wu-003/     ← git worktree for WU-003
└── _integration-<run_id>/  ← integration branch worktree
```

- Each WU gets its own branch: `oporch/<run_id>/<wu-slug>`
- Each WU gets its own worktree directory — agents never share a working directory or index lock
- Push is structurally disabled per-worktree (`remote.origin.pushurl` set to a broken URL)
- Only the supervisor role can merge into the integration branch

### Supervisor Merge Gate

1. **Conflict detection**: `git merge-tree --write-tree` dry-run against integration
2. **Clean merge**: squash-merge WU branch into `oporch/<run_id>/integration`
3. **Conflict**: WU status → `MERGE_CONFLICT`, routed to debugger (or user per policy)
4. **Protected branches**: `merge_integration_into_base()` hard-refuses branches in `never_auto_merge_to` list

### Branch Naming Convention

```
oporch/<run_id>/integration     ← per-run integration branch
oporch/<run_id>/<wu-slug>       ← per-WU feature branch
```

---

## Agent Memory System

### Memory Types

| Type | Description | Example |
|------|-------------|---------|
| `fact` | A learned fact about the project | "This project uses FastAPI with SQLAlchemy" |
| `gotcha` | A known pitfall or trap | "migration files must be run before tests" |
| `convention` | A project convention | "All API responses use snake_case" |
| `failure_pattern` | A recurring failure | "reviewer rejects auth WUs 40% on first attempt" |

### Memory Flow

1. **Storage**: SQLite `agent_memory` table, scoped by `(project_path, role_key)`
2. **Recall**: `db.recall(project_path, role_key, limit)` returns memories sorted by relevance
3. **Injection**: `context_builder.py` pulls top-K memories into the `## Known project memory` section of each agent prompt
4. **Relevance**: `relevance_score` field (default 1.0) — intended for decay/boost on reuse
5. **Portability**: `oporch memory export` → JSONL (git-friendly), `oporch memory import` → SQLite

### CLI Access

```bash
oporch memory list --role builder                  # what builder has learned
oporch memory add --role builder "always run migrations before tests"
oporch memory forget <id>
oporch memory export                                # → .opencode-orchestrator/memory_export.jsonl
oporch memory import memory_export.jsonl
```

---

## Security & Redaction

### Secrets Never Enter Agent Context

1. **Path exclusion**: `is_sensitive_path()` filters `.env`, `.pem`, `.key`, SSH keys, cloud credentials from repo scans
2. **Content redaction**: `redact_secrets()` runs 14 regex patterns before any payload touches persistent storage:
   - Bearer tokens, API keys, hex/base64 blobs, AWS keys
   - Connection strings with passwords (`scheme://user:password@host`)
   - PEM private key blocks
   - JWTs, GitHub tokens, Slack tokens
   - `.env`-style `SECRET=value` assignments

### Sandboxed Subprocess Environment

`build_restricted_env()` creates a scrubbed environment for agent subprocesses:

| Category | Treatment |
|----------|-----------|
| **Allowed** | `PATH`, `TEMP`, `HOME`, `USERPROFILE`, system essentials |
| **Passthrough** | `OPENCODE_*`, `OPORCH_*` prefixes |
| **Denied** | `AWS_*`, `AZURE_*`, `GOOGLE_API*`, `SNOWFLAKE*`, `DATADOG*`, `SENDGRID*`, `STRIPE*`, `TWILIO*` |

### Protected Branch Enforcement

- `SecurityPolicy.never_auto_merge_to` defaults to `["main", "develop", "master"]`
- `GitManager.merge_integration_into_base()` raises `ProtectedBranchError` — enforced structurally, not by convention
- STRICT approval mode disables auto-merge entirely

---

## Observability & Diagnostics

### Structured Event Schema

Every event row contains a consistent envelope:

```
run_id | wu_id | role | ts | event_type | level | payload | 
duration_ms | model_used | tokens_in | tokens_out
```

25 event types covering the full lifecycle (see [`constants.py`](file:///c:/Users/Administrator/Documents/oporch/src/oporch/constants.py) `EventType` enum).

### CLI Tooling

| Tool | What it shows |
|------|---------------|
| `oporch replay <run>` | Chronological reconstruction of a run — who did what, when, in what order |
| `oporch replay <run> --wu WU-041` | Scoped replay: every event, prompt, and diff for one WU including attempt history |
| `oporch diff <run_a> <run_b>` | Side-by-side comparison of two runs: WU count, completion, failures, per-role failure rates |
| `oporch report --failures` | Aggregated failure patterns across all historical runs for a project |
| `oporch logs --last N` | Tail the structured event log |
| `oporch doctor` | 8 health checks: CLI, configs, git, pytest |

---

## Live Dashboard (TUI)

### `oporch view` — Textual Application

A read-only TUI polling `oporch.db` every ~500ms:

```
┌─ oporch · run 8f3a21c4 · EXECUTING ─────────────────────────────┐
│ Phase 4/15  ████████░░░░░░░░  27%                                │
├───────────────┬───────────────┬───────────────┬─────────────────┤
│ backend (2/2)  │ frontend (1/2)│ db (1/1)      │ qa (0/1 idle)   │
│ ▶ WU-041 auth  │ ▶ WU-052 modal│ ✓ WU-039      │  waiting on     │
│   attempt 1    │   attempt 2   │   done 00:42  │  backend, db    │
│ ▶ WU-044 jwt   │ ⏸ WU-053      │               │                 │
│   attempt 1    │   queued      │               │                 │
├───────────────┴───────────────┴───────────────┴─────────────────┤
│ Recent events                                                    │
│ 12:04:03 builder  WU-041 review requested                        │
│ 12:03:55 reviewer WU-039 approved                                │
└────────────────────────────────────────────────────────────────┘
```

### Keybinds

| Key | Action |
|-----|--------|
| `q` | Quit |
| `d` / `Enter` | Drill into WU detail (modal with scrollable output + event trail) |
| `↑`/`k` `↓`/`j` | Navigate WU selection |
| `p` | Toggle dispatcher pause (cooperative control) |
| `r` | Force refresh |
| `Esc` | Close detail modal |

### Status Glyphs

| Glyph | Status |
|-------|--------|
| ▶ | IN_PROGRESS |
| ✓ | COMPLETED |
| ✗ | FAILED |
| ⚡ | MERGE_CONFLICT |
| ⏸ | PENDING |
| … | READY |
| ⊘ | BLOCKED |

---

## Roster Auto-Scaling

### Phase-Boundary Adjustment

After each completed **phase** (not per-WU), `RosterScaler` evaluates the roster:

| Action | Description | Approval |
|--------|-------------|----------|
| `resize` | Widen/narrow a role's semaphore budget | Auto-applies |
| `retire` | Drop an idle role with empty WU queue | Auto-applies (never the last role) |
| `spawn` | Add a role for an uncovered domain | Gated: parked as pending approval unless policy auto-approves |

### Guardrails

- Roster stays within the sizing band for the plan's phase count
- At least one role always remains
- `retire` never kills a role with queued or in-flight work
- Cross-cutting roles (`reviewer`, `tester`, `supervisor`) cannot be retired

### Approval Flow for Spawns

When `require_approval_for_spawn: true`:
1. Spawn creates a control key: `roster_spawn:<run_id>:<role_key>`
2. Value set to `pending:<max_workers>|<domain>|<reason>`
3. User resolves via `oporch approve roster_spawn:<run_id>:<role_key>`

---

## Error Handling

| Exception | Module | When |
|-----------|--------|------|
| `ConfigError` | `config.py` | Missing/malformed YAML |
| `InvalidTransitionError` | `state_machine.py` | Illegal state transition |
| `RunStateError` | `run_state.py` | Schema version mismatch on load |
| `CircularDependencyError` | `work_unit.py` | Cycle in work unit DAG |
| `WorkUnitGraphError` | `work_unit.py` | Unknown dependency reference |
| `OrchestratorError` | `orchestrator.py` | Orchestrator-level failures |
| `RunnerError` | `runner.py` | No work units found, execution failures |
| `TeamComposerError` | `team_composer.py` | Roster composition failures |
| `GitManagerError` | `git_manager.py` | Git operations failed |
| `MergeConflictError` | `git_manager.py` | Squash merge hit conflicts |
| `ProtectedBranchError` | `git_manager.py` | Attempted merge to protected branch |

All state files carry a `schema_version` field — mismatches raise `RunStateError` to prevent silent data corruption.

---

## Testing

### Test Suite

**330 tests passing** across 22 test files:

| Test File | Tests | What it covers |
|-----------|-------|----------------|
| [`test_config.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_config.py) | 11 | YAML load/save, `resolve_model()`, fallback chain, schema versioning |
| [`test_context_builder.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_context_builder.py) | 39 | Plan-doc parsing (phased/generic/edge cases), per-role context building |
| [`test_dashboard.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_dashboard.py) | 9 | Dashboard widget rendering, WU card formatting, progress calculation |
| [`test_db.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_db.py) | 25 | SQLite CRUD for all 7 tables, WAL mode, migration, export/import |
| [`test_decision_ledger.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_decision_ledger.py) | 9 | JSONL Q&A, search, find-by-question, clear |
| [`test_dispatcher.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_dispatcher.py) | 16 | Parallel wave execution, semaphore bounds, resize, cancellation |
| [`test_doctor.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_doctor.py) | 2 | Health check pass/fail |
| [`test_event_log.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_event_log.py) | 7 | Record, filter, all, persistence |
| [`test_executor.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_executor.py) | 4 | FakeAgentExecutor call tracking, `set_next_result` |
| [`test_git_manager.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_git_manager.py) | 20 | Worktree create/cleanup, commit, diff, merge gate, conflict detection, push blocking, protected branch |
| [`test_memory_wiring.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_memory_wiring.py) | 10 | Memory recall → context injection, relevance scoring |
| [`test_observability.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_observability.py) | 9 | Structured events, replay query, diff stats |
| [`test_orchestrator.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_orchestrator.py) | 9 | `plan_milestone()`, `run_milestone()`, `resume_run()`, state transitions, planner prompt |
| [`test_redact.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_redact.py) | 13 | All 14 secret patterns, sensitive path detection, filter |
| [`test_roster_scaling.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_roster_scaling.py) | 15 | Suggest/apply for spawn/retire/resize, approval gating, phase boundary checks |
| [`test_run_state.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_run_state.py) | 6 | CRUD, worker output persistence, plan save/load |
| [`test_runner.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_runner.py) | 22 | Full milestone execution, retry ladder, review/test integration, completion evaluation |
| [`test_security.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_security.py) | 32 | Connection strings, PEM keys, JWT, GitHub/Slack tokens, .env assignments, sensitive paths, restricted env |
| [`test_state_machine.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_state_machine.py) | 20 | All transitions, history, terminal detection, invalid transition errors, `COMPOSING_TEAM` integration |
| [`test_team_composer.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_team_composer.py) | 25 | Sizing bands, domain inference, heuristic roster, band fitting, roster validation, parser |
| [`test_validate.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_validate.py) | 13 | JSON repair, code fence stripping, brace extraction, planner output validation |
| [`test_work_unit.py`](file:///c:/Users/Administrator/Documents/oporch/tests/test_work_unit.py) | 14 | DAG validation, topological sort, ready detection, cycle detection |

### Running Tests

```bash
# Using the project's virtualenv
.venv\Scripts\pytest -v          # Windows
.venv/bin/pytest -v              # Linux/macOS

# All 330 tests, ~23 seconds
```

---

## Gap Analysis — Missing & Incomplete Features

Cross-referencing the implemented codebase against the [PRD (`oporch_v2_prd.md`)](file:///c:/Users/Administrator/Documents/oporch/oporch_v2_prd.md), the following features are **missing, incomplete, or deferred**:

### Critical Gaps (High Impact)

| # | Feature | PRD Section | Status | Details |
|---|---------|-------------|--------|---------|
| 1 | **Supervisor dynamic model selection** | §11a | ❌ Not implemented | `resolve_model()` is still a static lookup. The PRD calls for `supervisor.select_model(wu, attempt_history)` that scores task complexity and picks a model within a `tier` range per WU. `ModelInfo.tier` field exists but is unused. |
| 2 | **Self-healing recovery ladder** | §11b | ❌ Not implemented | Current retry is a fixed 3-attempt sequence. PRD specifies a supervisor-driven recovery strategy ladder: `retry_same_model` → `retry_with_model_bump` → `retry_with_narrower_scope` (split WU) → `retry_with_debugger_prefix` → `rollback_and_reassign` → user. `self_heal_max_strategies` policy not present. |
| 3 | **Scoped file/folder access enforcement** | §11c | ⚠️ Schema only | `TeamRole.allowed_paths` field exists in models and DB, but **no enforcement** — the supervisor never validates `AgentResult` diffs against `allowed_paths` before accepting. The PRD requires post-hoc diff validation and ideally worktree-level filesystem fencing. |
| 4 | **Real worktree integration in runner** | §7 + §3 | ⚠️ Partial | `GitManager` is fully implemented, but `MilestoneRunner._execute_work_unit()` does not actually call `git_manager.create_worktree()` or `commit_wu_result()` during execution. The git isolation exists as a module but is **not wired into the execution pipeline**. |

### Moderate Gaps (Medium Impact)

| # | Feature | PRD Section | Status | Details |
|---|---------|-------------|--------|---------|
| 5 | **Budget/cost tracking enforcement** | §11a / §9 | ⚠️ Schema only | `events` table has `tokens_in`/`tokens_out`/`duration_ms` columns, but `OpenCodeAgentExecutor` does not populate them — it only captures `stdout`/`stderr`. No `model_budget_soft_limit` policy. |
| 6 | **Failure pattern auto-population** | §9 | ⚠️ Partial | The `agent_memory` table and `failure_pattern` memory type exist, but the runner does not **automatically** write failure patterns when WUs fail or hit `MERGE_CONFLICT`. Only manual `oporch memory add` populates this. |
| 7 | **Memory relevance decay/boost** | §4 | ⚠️ Schema only | `relevance_score` column exists (default 1.0) but is never updated — no decay on age or boost on reuse. |
| 8 | **Structured event payload standardization** | §9 | ⚠️ Partial | Event columns exist for `level`, `duration_ms`, `model_used`, `tokens_in`/`out`, but most events are recorded with only `event_type` and a loose `details` dict. No consistent envelope enforcement. |
| 9 | **Run-level health checks by supervisor** | §11b | ❌ Not implemented | No periodic (per phase boundary) run-health validation — rising failure rates, stuck idle roles, deadlock detection. |

### Minor Gaps (Low Impact / Polish)

| # | Feature | PRD Section | Status | Details |
|---|---------|-------------|--------|---------|
| 10 | **Dashboard merge-status column** | §5 / §7 | ⚠️ Missing | Dashboard shows WU status but doesn't display merge gate status (`merged`/`pending-merge`/`conflict`) per WU. |
| 11 | **Dashboard role column animation** | §8 | ❌ Not implemented | When a role is retired, its TUI column should collapse; when spawned, a new column should animate in. |
| 12 | **Plan-doc parser — raw text preservation** | §2 / §3 | ⚠️ Partial | `Phase.raw` field exists but `parse_plan_doc()` always sets it to `None`. |
| 13 | **OpenCodeAgentExecutor — model_id validation** | §2 | ⚠️ Missing | No validation that the resolved `model_id` actually exists or is reachable before dispatching. |
| 14 | **README test count discrepancy** | — | ⚠️ Stale | README says "84 tests passing" and CONTEXT.md says "84 tests" — actual count is 330. |

### Deferred to v3 (Per PRD)

| # | Feature | PRD Section | Status |
|---|---------|-------------|--------|
| 15 | **Containerized/namespace sandboxing** | §10 "stronger tier" | Deferred | Run each worker in a lightweight container/namespace (bubblewrap, Docker) |
| 16 | **Supervisor dynamic model selection + self-healing ladder** | §11 | Deferred | Full tier-based model routing and multi-strategy recovery |

### Open Questions (Unresolved in PRD)

| # | Question | PRD Section |
|---|----------|-------------|
| 1 | Pause/resume semantics for parallel dispatcher — abort in-flight subprocesses or let current wave finish? | §12 |
| 2 | Merge-conflict default: auto-route to debugger or always escalate to user? | §12 (partially resolved: defaults to `"debugger"` in code) |
| 3 | Should auto-scaling adjustments require approval every time or only for spawns? | §12 (resolved: spawns gated, shrink/resize auto-apply) |
| 4 | Is the "minimum" security tier sufficient, or is containerized sandboxing needed for prod codebases? | §12 |

### Recommendations for Next Steps

> [!IMPORTANT]
> The highest-value items to implement next are **#4 (wire git worktrees into runner)** and **#6 (auto-populate failure patterns)**, since the infrastructure already exists — they just need connection plumbing.

1. **Wire `GitManager` into `MilestoneRunner`** — Call `create_worktree()` before builder dispatch, `commit_wu_result()` after, and `merge_wu_into_integration()` in the supervisor stage. This is the biggest bang-for-buck change.
2. **Auto-populate `failure_pattern` memories** when WUs fail or hit merge conflicts — feeds the memory system without manual intervention.
3. **Populate `tokens_in`/`tokens_out`/`duration_ms`** in `OpenCodeAgentExecutor` from subprocess output parsing — enables real cost tracking.
4. **Enforce `allowed_paths`** in the supervisor merge gate — diff validation against each role's path globs.
5. **Update README** — test count (84 → 330), command table (many new commands not listed), module map (new modules not listed).
