<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/version-2.0.0-orange?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/tests-349%20passing-brightgreen?style=for-the-badge" alt="Tests">
  <img src="https://img.shields.io/badge/status-v2%20complete-blueviolet?style=for-the-badge" alt="Status">
</p>

<h1 align="center">⚡ oporch</h1>
<p align="center"><strong>Multi-Agent Orchestration System for OpenCode</strong></p>

<p align="center">
  Paste a plan → team auto-composed → parallel agent execution →<br>
  adversarial code review → testing → evidence-gated completion
</p>

<p align="center">
  <code>oporch</code> &nbsp;→&nbsp; paste plan &nbsp;→&nbsp; <code>/build</code> &nbsp;→&nbsp; done
</p>

---

## Table of Contents

- [What is oporch?](#-what-is-oporch)
- [Quick Start](#-quick-start)
- [Interactive Commands](#-interactive-commands)
- [Architecture](#-architecture)
- [Data Flow & Lifecycle](#-data-flow--lifecycle)
- [State Machine](#-state-machine)
- [Module Reference](#-module-reference)
- [Configuration](#-configuration)
- [Data Storage (SQLite)](#-data-storage-sqlite)
- [Dynamic Team Composition](#-dynamic-team-composition)
- [Parallel Execution Engine](#-parallel-execution-engine)
- [Git Isolation & Merge Gate](#-git-isolation--merge-gate)
- [Supervisor Intelligence](#-supervisor-intelligence)
- [Agent Memory](#-agent-memory)
- [Security](#-security)
- [Live Dashboard (TUI)](#-live-dashboard-tui)
- [Observability](#-observability)
- [Legacy CLI Reference](#-legacy-cli-reference)
- [Error Handling](#-error-handling)
- [Testing](#-testing)
- [Project Structure](#-project-structure)
- [Development](#-development)
- [Roadmap](#-roadmap)

---

## ✨ What is oporch?

**oporch** is an interactive multi-agent orchestrator that sits on top of [OpenCode](https://opencode.ai). You paste a multi-phase implementation plan, and oporch:

1. 🔍 **Analyzes** your plan into phases with acceptance criteria
2. 🤖 **Composes a team** — sized to the work (3–9+ specialized AI agents)
3. 📋 **Decomposes** phases into a DAG of atomic work units
4. ⚡ **Executes in parallel** — bounded concurrency per role, retry ladder, merge gate
5. 🔒 **Gates completion** — adversarial review + testing + evidence validation
6. 🧠 **Learns** — persistent memory across runs (gotchas, conventions, failure patterns)

No flags. No multi-step commands. Just paste and build.

### Key Design Principles

| Principle | How oporch implements it |
|-----------|--------------------------|
| **Paste and go** | Interactive REPL: paste a plan, team is composed, agents execute |
| **One orchestrator, many agents** | `HeadOrchestrator` manages state, routing, retries, and escalation |
| **Dynamic teams** | Roster is sized per-plan, not a fixed enum |
| **Parallel execution** | Asyncio dispatcher with per-role semaphore concurrency |
| **Git isolation** | One worktree per work unit — agents never share a working directory |
| **Evidence-gated completion** | Review + test + supervisor merge must all pass before `COMPLETED` |
| **Persistent memory** | Cross-run fact/gotcha/convention/failure_pattern store in SQLite |
| **Secrets never enter context** | `redact.py` scrubs all payloads before persistent storage |

---

## 🚀 Quick Start

```bash
pip install oporch
oporch                    # opens the interactive session
```

That's it. Paste your implementation plan and follow the prompts:

```
╭─ ⚡ oporch ── Multi-Agent Orchestrator ──────────────────────╮
│  📂 myproject                                                 │
│  State: IDLE                                                  │
│                                                               │
│  Paste your implementation plan below, or type /help          │
│  Use /quit to exit.                                           │
╰──────────────────────────────────────────────────────────────╯

oporch ❯ ## Phase 1: Database Schema
  ...   - Create users table with auth fields
  ...   - Add migration script
  ...   ## Phase 2: API Endpoints
  ...   - POST /register, POST /login, GET /me
  ...   ## Phase 3: Frontend Auth
  ...   - Login page, JWT storage, route guards
  ...                                          ← (Enter on empty line submits)

⚡ Analyzing plan... 3 phases detected

  Phase 1: Database Schema
    Create users table with auth fields; Add migration script
  Phase 2: API Endpoints
    POST /register, POST /login, GET /me
  Phase 3: Frontend Auth
    Login page, JWT storage, route guards

🤖 Composing team & generating work units...

🤖 Team: backend x2  db x1  frontend x1  reviewer ✓  tester ✓

📋 9 work units proposed:
┌────────┬────────────────────────┬──────────┬────────┐
│ ID     │ Title                  │ Role     │ Deps   │
├────────┼────────────────────────┼──────────┼────────┤
│ WU-001 │ Create users table     │ db       │ --     │
│ WU-002 │ Migration script       │ db       │ WU-001 │
│ WU-003 │ POST /register         │ backend  │ WU-001 │
│ WU-004 │ POST /login            │ backend  │ WU-001 │
│ WU-005 │ GET /me                │ backend  │ WU-004 │
│ WU-006 │ JWT middleware         │ backend  │ WU-004 │
│ WU-007 │ Login page             │ frontend │ WU-004 │
│ WU-008 │ JWT storage            │ frontend │ WU-006 │
│ WU-009 │ Route guards           │ frontend │ WU-008 │
└────────┴────────────────────────┴──────────┴────────┘

Approve and start building? [Y/n] y
✓ Plan approved
▶ Executing...  (type /status or /view for progress)
```

**Requirements:** Python 3.12+, [opencode](https://opencode.ai) CLI, git

---

## 💬 Interactive Commands

Everything happens inside the REPL. Type `/` to see commands:

### Core Workflow

| Command | What it does |
|---------|-------------|
| *(paste text)* | Auto-analyze as a plan, compose team, propose work units |
| `/plan` | Re-paste or load a new plan |
| `/build` | Start executing the approved plan |
| `/resume` | Resume an interrupted run |
| `/status` | Show run state + work unit tree with progress |
| `/cancel` | Cancel the current run |

### Team & Memory

| Command | What it does |
|---------|-------------|
| `/team` | Show the active roster (roles, models, workers, domains) |
| `/team edit` | Interactive roster editor: add, remove, resize |
| `/team history` | Show which roles were active during which phases |
| `/memory` | List what agents have learned on this project |
| `/remember <text>` | Teach the builder a fact or convention |
| `/forget <id>` | Delete a memory |

### Observability

| Command | What it does |
|---------|-------------|
| `/view` | Open the live TUI dashboard (Textual app) |
| `/replay [run_id]` | Chronological reconstruction of a run |
| `/report` | Evidence-backed final report |
| `/report failures` | Aggregate failure patterns across all runs |
| `/logs [N]` | Show last N structured events |
| `/models` | Show resolved role → model mappings |
| `/doctor` | 8 environment health checks |

### Session

| Command | What it does |
|---------|-------------|
| `/help` | Show all available commands |
| `/quit` or `/q` | Exit oporch |

### Input Handling

- **Multi-line paste**: Accumulates lines until double-Enter or `--end`
- **Phase detection**: Parses `## Phase N: Title` headers (or generic `## Title`)
- **Fallback**: If no phases detected, treats first line as a single objective
- **State-aware prompt**: Shows `[EXECUTING] 8f3a21c4 ❯` during runs
- **Background execution**: REPL stays responsive — check progress anytime

---

## 🏗️ Architecture

### How a Run Flows

```
 Paste Plan                    Team Composition               Execution
╭──────────╮    ╭────────────────────╮    ╭─────────────────────────────╮
│  Parse   │───▶│  Infer domains     │───▶│  Parallel wave dispatch     │
│  phases  │    │  Size roster (3-9) │    │  Per-role semaphore bounds  │
│  + ACs   │    │  Assign models     │    │  Builder → Reviewer → Test  │
╰──────────╯    ╰────────────────────╯    │  Retry ladder (3 attempts) │
                                          │  Git worktree isolation     │
                                          │  Supervisor merge gate      │
                                          ╰─────────────────────────────╯
                                                      │
                                          ╭───────────▼────────────────╮
                                          │  Evidence validation       │
                                          │  Review ✓  Tests ✓         │
                                          │  → COMPLETED or FAILED     │
                                          ╰────────────────────────────╯
```

### Agent Pipeline Per Work Unit

```
┌─ 1. Context Building ─────────────────────────────────────┐
│  Role-specific context: PRD sections, relevant files,      │
│  architecture constraints, dependency outputs, memory      │
├─ 2. Builder (roster role) ─────────────────────────────────┤
│  opencode -p <prompt> -m <model_id>                        │
│  Runs in per-WU git worktree (isolated working dir)        │
├─ 3. Reviewer (adversarial) ───────────────────────────────┤
│  Review against acceptance criteria                        │
│  Verdict: APPROVE / REQUEST_CHANGES / BLOCK                │
├─ 4. Tester ───────────────────────────────────────────────┤
│  Validates acceptance criteria, runs test commands          │
├─ 5. Supervisor Merge Gate ─────────────────────────────────┤
│  Squash-merge WU branch into integration                   │
│  Protected branches refused (main, develop, master)        │
└────────────────────────────────────────────────────────────┘
```

### Retry Ladder

| Attempt | Strategy |
|---------|----------|
| 1 | Builder implements |
| 2 | Builder receives prior review feedback |
| 3 | Debugger diagnoses first, then Builder retries |
| >3 | Escalate to user |

---

## 🔄 Data Flow & Lifecycle

```
1. oporch
   └── Auto-creates .opencode-orchestrator/{config,state,context,runs}/

2. Paste plan
   ├── IDLE → ANALYZING
   │   └── Scans src/, PRD.md for repo context
   ├── ANALYZING → COMPOSING_TEAM
   │   └── Parses plan into Phase objects
   │   └── compose_team() proposes dynamic roster
   ├── COMPOSING_TEAM → PLANNING
   │   └── Planner agent decomposes into WorkUnitGraph
   └── PLANNING → AWAITING_PLAN_APPROVAL
       └── User reviews and approves (or edits roster)

3. /build
   ├── AWAITING_PLAN_APPROVAL → EXECUTING
   │   └── ParallelDispatcher pulls READY WUs, groups by role
   │   └── Wave loop until DAG drains
   │
   │   For each WU:
   │   ├── Builder implements
   │   ├── Reviewer adversarially reviews
   │   ├── Tester validates acceptance criteria
   │   └── Retry ladder (3 attempts → debugger → user)
   │
   │   Phase boundary → RosterScaler auto-adjusts
   │
   └── VALIDATING → COMPLETED or FAILED

4. /report
   └── Evidence-backed report with files changed, test results, risks
```

---

## ⚙️ State Machine

### 15 States

```python
class OrchestratorState(str, Enum):
    IDLE                     # No run active
    ANALYZING                # Scanning repo, parsing plan doc
    COMPOSING_TEAM           # Dynamic roster proposal
    PLANNING                 # Planner agent decomposing into WU DAG
    AWAITING_PLAN_APPROVAL   # User review of plan + roster
    EXECUTING                # Parallel agent dispatch
    REVIEWING                # Adversarial code review
    TESTING                  # Test validation
    DEBUGGING                # Retry with debugger
    REPLANNING               # Re-plan after failures
    AWAITING_USER_INPUT      # Escalation to human
    VALIDATING               # Final evidence check
    COMPLETED                # Terminal: success
    FAILED                   # Terminal: failure
    CANCELLED                # Terminal: user cancelled
```

### Transition Flow

```
IDLE → ANALYZING → COMPOSING_TEAM → PLANNING → AWAITING_PLAN_APPROVAL
  → EXECUTING → REVIEWING → TESTING → VALIDATING → COMPLETED
```

Plus `DEBUGGING`, `REPLANNING`, `AWAITING_USER_INPUT`, `FAILED`, `CANCELLED`.
Every transition validated against the transition table, timestamped, and persisted.
Invalid transitions raise `InvalidTransitionError`.

---

## 📦 Module Reference

### 25 Source Modules

| Module | Responsibility |
|--------|----------------|
| `repl.py` | Interactive REPL: 18 slash commands, multi-line paste, auto-init, background execution |
| `cli.py` | Typer CLI: 25+ commands, default config writers, REPL callback on no-args |
| `orchestrator.py` | `HeadOrchestrator`: plan_milestone, run_milestone, resume_run, compose_roster |
| `runner.py` | `MilestoneRunner` + `ParallelDispatcher`: async waves, per-role semaphores |
| `supervisor.py` | §11a model selection, §11b self-healing strategies, §11c scoped file access |
| `team_composer.py` | `compose_team()`: sizing bands, domain inference, agent-driven + heuristic fallback |
| `roster_scaling.py` | Phase-boundary auto-scaling: spawn/retire/resize roles |
| `context_builder.py` | `parse_plan_doc()`, `build_context_for_role()`, per-role context packs |
| `executor.py` | `OpenCodeAgentExecutor` (real) + `FakeAgentExecutor` (testing) |
| `git_manager.py` | Per-WU worktrees, integration branch, merge gate, push blocking |
| `db.py` | SQLite (WAL mode): 7 tables for runs, roster, work_units, events, decisions, memory, control |
| `dashboard.py` | Live Textual TUI: role panels, WU cards, progress bar, event tail, pause control |
| `models.py` | 30+ Pydantic v2 domain schemas |
| `constants.py` | Enums: 15 states, 10 roles, 8 WU statuses, 25 event types |
| `config.py` | YAML loaders + `resolve_model()` with fallback chain |
| `state_machine.py` | Transition table, history tracking, terminal detection |
| `redact.py` | 14 regex patterns: tokens, PEM, JWT, connection strings |
| `validate.py` | JSON repair for agent output (code fences, brace extraction) |
| `run_state.py` | Legacy JSON file persistence (mirrored to SQLite) |
| `event_log.py` | JSONL event stream |
| `decision_ledger.py` | Q&A ledger for planner decisions |
| `work_unit.py` | `WorkUnitGraph`: DAG, topo sort, cycle detection, ready detection |
| `doctor.py` | 8 environment health checks |
| `prompts/planner.md` | System prompt template for plan decomposition |
| `prompts/team_composer.md` | System prompt for roster proposal |

---

## ⚙️ Configuration

Auto-created on first launch (`oporch`). Edit anytime under `.opencode-orchestrator/config/`:

```
.opencode-orchestrator/
├── config/
│   ├── roles.yaml       ← 10 agent roles with model + fallback
│   ├── models.yaml      ← Logical model keys → real OpenCode model IDs
│   └── policies.yaml    ← Approval mode, retry, completion gates, security
├── state/               ← Current run state
├── context/             ← Auto-generated project summary
├── runs/                ← Per-run data (plans, events, outputs)
├── worktrees/           ← Per-WU git worktrees (created at runtime)
└── oporch.db            ← SQLite database (WAL mode)
```

### `roles.yaml` — Agent Roles

```yaml
roles:
  orchestrator:
    description: "Controls overall milestone execution, delegates work, evaluates evidence"
    model: "deepseek-v4-flash"
    max_workers: 1
  planner:
    description: "Analyzes objectives and produces atomic work units"
    model: "deepseek-v4-flash"
    max_workers: 1
  architect:
    description: "Reviews architectural impact and identifies structural risks"
    model: "deepseek-v4-flash"
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
    description: "Independent validation of acceptance criteria"
    model: "nemotron-ultra"
    fallback: "deepseek-v4-flash"
    max_workers: 1
  debugger:
    description: "Root-cause analysis of failures"
    model: "mimo-v2.5"
    fallback: "deepseek-v4-flash"
    max_workers: 1
  researcher:
    description: "External library and documentation investigation"
    model: "deepseek-v4-flash"
    max_workers: 1
  benchmark_analyst:
    description: "Before/after metrics comparison and drift detection"
    model: "nemotron-ultra"
    fallback: "deepseek-v4-flash"
    max_workers: 1
  supervisor:
    description: "Merge gate: re-diffs WU branches and merges into integration"
    model: "nemotron-ultra"
    fallback: "deepseek-v4-flash"
    max_workers: 1
```

Premium roles (reviewer, tester, debugger, supervisor) have fallbacks so execution continues if the primary model is unavailable.

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
    context_limit: 131072
    output_limit: 16384
    tier: "heavy"
  mimo-v2.5:
    provider: "deepseek"
    model_id: "opencode/mimo-v2.5-free"
    context_limit: 131072
    output_limit: 16384
    tier: "standard"
```

Each role references a **logical model key** (e.g. `nemotron-ultra`). `resolve_model()` looks it up in `models.yaml`, returns the real `model_id` (e.g. `opencode/nemotron-3-ultra-free`). If the primary key isn't found, it falls back to the role's `fallback`. This decouples role config from actual model IDs — swap models in one place.

### `policies.yaml` — Behavior Control

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
  max_critical_findings: 0
  max_high_findings: 0
  require_supervisor_merge: false
context:
  include_relevant_prd_sections: true
  include_prior_decisions: true
  include_dependency_outputs: true
merge_conflict:
  route: "debugger"
  max_debugger_attempts: 1
security:
  never_auto_merge_to: ["main", "develop", "master"]
  strict_disables_auto_merge: true
```

---

## 💾 Data Storage (SQLite)

`oporch.db` uses WAL mode for concurrent reader/writer access.

### 7 Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `runs` | Run metadata | `id`, `milestone_id`, `state`, `created_at`, `objective` |
| `roster` | Dynamic team roles | `run_id`, `role_key`, `model`, `max_workers`, `active_from/until` |
| `work_units` | WU DAG nodes | `id`, `run_id`, `status`, `assigned_role`, `depends_on`, `attempt` |
| `events` | Structured event stream | `run_id`, `event_type`, `role`, `wu_id`, `duration_ms`, `tokens_used` |
| `decisions` | Q&A ledger | `run_id`, `question`, `answer`, `decided_by` |
| `agent_memory` | Cross-run memory | `project`, `role_key`, `memory_type`, `content`, `relevance_score` |
| `control` | Cooperative control | `run_id`, `key`, `value` (e.g. `paused=true`, pending approvals) |

All text payloads pass through `redact_secrets()` before insertion.

---

## 🤖 Dynamic Team Composition

Roster size automatically scales with plan complexity:

| Phases | Agents | Example |
|--------|--------|---------|
| 1–6 | 3–4 | `backend ×2, reviewer ✓, tester ✓` |
| 7–12 | 5–6 | `+ frontend ×1, db ×1` |
| 13–20 | 7–9 | `+ infra ×1, qa ×1, docs ×1` |
| 20+ | +1 per 3 | Keeps growing with work volume |

### Composition Process

1. **Parse plan** → `Phase` objects via `parse_plan_doc()`
2. **Infer domains** → keyword matching (backend, frontend, db, infra, qa, docs)
3. **Agent-driven roster** (preferred) → Planner proposes JSON via `prompts/team_composer.md`
4. **Heuristic fallback** → deterministic domain clustering if agent output is invalid
5. **Cross-cutting gates** → `reviewer` and `tester` always injected
6. **Band fitting** → trim/pad to stay within sizing band

### Roster Editing

Before approving, edit the roster interactively:

```
oporch ❯ /team edit
  Commands: workers KEY N | remove KEY | add KEY | done
  Roles: backend(x2), db(x1), reviewer(x1), tester(x1)
  team> add frontend
  ✓ Added frontend
  team> workers backend 3
  ✓ backend → 3 workers
  team> done
  ✓ Roster edit complete
```

### Phase-Boundary Auto-Scaling

After each completed phase, `RosterScaler` re-evaluates:

| Action | Approval | Guard |
|--------|----------|-------|
| **Resize** | Auto-applies | Stay within sizing band |
| **Retire** | Auto-applies | Never the last role; never cross-cutting (reviewer/tester) |
| **Spawn** | Gated (requires approval) | Budget and band limits |

---

## ⚡ Parallel Execution Engine

### Wave Execution Loop

```python
while not graph.all_completed():
    await wait_if_paused()          # cooperative pause via control table
    ready = graph.get_ready()
    results = await run_ready_wave(ready)  # bounded by per-role semaphore
    for wu, ok in zip(ready, results):
        if ok:
            completed_ids.add(wu.id)
            if phase_complete:
                scaler.on_phase_complete(phase)
```

- **Per-role semaphores** bound concurrency (e.g. `builder: max_workers=3` → at most 3 builders run simultaneously)
- **Wave grouping**: Ready WUs dispatched together, results collected as a batch
- **Pause/resume**: Dashboard `p` key or control table toggles cooperative pause
- **Cancellation**: `/cancel` sets flag checked at each wave boundary

---

## 🔀 Git Isolation & Merge Gate

### Per-WU Worktree Isolation

Every work unit gets its own git worktree — agents never share a working directory:

```
.opencode-orchestrator/worktrees/
├── wu-001/                      ← git worktree for WU-001
├── wu-002/                      ← git worktree for WU-002
└── _integration-<run_id>/       ← integration branch
```

- **Branch naming**: `oporch/<run_id>/<wu-slug>`
- **Push disabled** per-worktree (`remote.origin.pushurl` set to broken URL)
- **Only the supervisor** can merge into integration

### Supervisor Merge Gate

1. Conflict detection via `git merge-tree --write-tree`
2. **Clean** → squash-merge into `oporch/<run_id>/integration`
3. **Conflict** → `MERGE_CONFLICT` → routed to debugger (or user, per policy)
4. **Protected branches** hard-refused (`never_auto_merge_to: ["main", "develop", "master"]`)

---

## 🧩 Supervisor Intelligence

### §11a — Dynamic Model Selection

`SupervisorModelSelector.select_model()` scores per-WU complexity from:
- Number of files affected
- Acceptance criteria count
- Attempt history (failures → bump tier)
- Remaining token budget

Picks a model within the role's configured `tier` range (`fast` → `standard` → `heavy`), logged as a `MODEL_SELECTED` event.

### §11b — Self-Healing Recovery

`RecoveryLadder.next_strategy()` resolves strategies in order:
1. `retry_same_model`
2. `retry_with_model_bump`
3. `retry_with_narrower_scope` (split WU)
4. `retry_with_debugger_prefix`
5. `rollback_and_reassign`
6. Escalate to user

### §11c — Scoped File Access

`validate_file_access()` checks an agent's changed-file list against `allowed_paths` globs before results are accepted.

### Run-Level Health Checks

`RunHealthCheck.check()` at phase boundaries:
- Rising failure rates → flag
- Starved roles with ready work → recommend resize
- Runaway worktree disk usage → warn

---

## 🧠 Agent Memory

Persistent cross-run memory so agents learn from past runs:

| Type | Example |
|------|---------|
| `fact` | "This project uses FastAPI with SQLAlchemy" |
| `gotcha` | "Migration files must be run before tests" |
| `convention` | "All API responses use snake_case" |
| `failure_pattern` | "Reviewer rejects auth WUs 40% on first attempt" |

```
oporch ❯ /remember always run migrations before pytest
  ✓ Memory #3 recorded

oporch ❯ /memory
  ┌────┬─────────┬──────┬──────────────────────────────────────────┐
  │ ID │ Role    │ Type │ Content                                  │
  ├────┼─────────┼──────┼──────────────────────────────────────────┤
  │  1 │ builder │ fact │ This project uses FastAPI + SQLAlchemy    │
  │  3 │ builder │ fact │ always run migrations before pytest       │
  └────┴─────────┴──────┴──────────────────────────────────────────┘
```

Memories are injected into each agent's context as `## Known project memory`. Portable via `oporch memory export/import`.

---

## 🔒 Security

### Secrets Never Enter Agent Context

1. **Path filtering**: `.env`, `.pem`, `.key`, SSH keys, cloud creds excluded from repo scans
2. **Content redaction**: 14 regex patterns scrub payloads before persistence:
   - Bearer tokens, API keys, AWS keys, hex/base64 blobs
   - Connection strings, PEM blocks, JWTs
   - GitHub/Slack tokens, `.env` assignments
3. **Sandboxed env**: Agent subprocesses inherit only safe env vars (PATH, TEMP, HOME)
   - Denied: `AWS_*`, `AZURE_*`, `GOOGLE_API*`, `STRIPE*`, `TWILIO*`, etc.

### Protected Branch Enforcement

- `never_auto_merge_to: ["main", "develop", "master"]` — enforced structurally via `ProtectedBranchError`
- STRICT mode requires manual approval for every merge
- Per-WU git worktrees disable push (structural, not by convention)

---

## 📊 Live Dashboard (TUI)

Open with `/view` or `oporch view`:

```
┌─ oporch · run 8f3a21c4 · EXECUTING ─────────────────────────────┐
│ Phase 4/15  ████████░░░░░░░░  27%                                │
├───────────────┬───────────────┬───────────────┬─────────────────┤
│ backend (2/2)  │ frontend (1/2)│ db (1/1)      │ qa (0/1 idle)   │
│ ▶ WU-041 auth  │ ▶ WU-052 modal│ ✓ WU-039      │  waiting on     │
│   attempt 1    │   attempt 2   │   done 00:42  │  backend, db    │
│ ▶ WU-044 jwt   │ ⏸ WU-053      │               │                 │
├───────────────┴───────────────┴───────────────┴─────────────────┤
│ 12:04:03 builder  WU-041 review requested                        │
│ 12:03:55 reviewer WU-039 approved                                │
└──────────────────────────────────────────────────────────────────┘
```

| Key | Action |
|-----|--------|
| `q` | Quit dashboard |
| `d` / `Enter` | Drill into WU detail (output, event trail) |
| `p` | Toggle dispatcher pause |
| `r` | Force refresh |

**Status glyphs:** `▶` IN_PROGRESS &nbsp; `✓` COMPLETED &nbsp; `✗` FAILED &nbsp; `⚡` MERGE_CONFLICT &nbsp; `⏸` PENDING &nbsp; `…` READY &nbsp; `⊘` BLOCKED

---

## 📈 Observability

| Command | What it shows |
|---------|---------------|
| `/replay [run_id]` | Chronological event reconstruction |
| `/logs [N]` | Last N structured events (default 20) |
| `/report` | Evidence-backed final report |
| `/report failures` | Aggregate failure patterns across all runs |
| `oporch diff <a> <b>` | Side-by-side run comparison (WU count, completion rate, per-role failure rates) |
| `/doctor` | 8 health checks: opencode CLI, configs, git, pytest |

### Run Replay Example

```
oporch ❯ /replay
  ┌──────────┬─────────┬────────┬──────────────────┬──────────┐
  │ Time     │ Role    │ WU     │ Event            │ Duration │
  ├──────────┼─────────┼────────┼──────────────────┼──────────┤
  │ 12:01:03 │ planner │ --     │ PLAN_CREATED     │ 4200ms   │
  │ 12:01:08 │ builder │ WU-001 │ BUILD_STARTED    │ --       │
  │ 12:01:42 │ builder │ WU-001 │ BUILD_COMPLETED  │ 34000ms  │
  │ 12:01:43 │ reviewer│ WU-001 │ REVIEW_APPROVED  │ 8200ms   │
  └──────────┴─────────┴────────┴──────────────────┴──────────┘
```

---

## 📟 Legacy CLI Reference

All Typer subcommands still work for scripting and CI:

| Command | Description |
|---------|-------------|
| `oporch init` | Create `.opencode-orchestrator/` with default configs |
| `oporch plan <source> [--objective] [--milestone-id] [--yes]` | Generate work graph |
| `oporch run <milestone_id> [--executor fake\|opencode] [--verbose]` | Execute plan |
| `oporch resume [--executor]` | Resume interrupted run |
| `oporch status` | Show active run state |
| `oporch cancel` | Cancel active run |
| `oporch report [--failures]` | Final report or failure analytics |
| `oporch view [--run-id]` | Live TUI dashboard |
| `oporch team show/edit/history` | Roster management |
| `oporch memory list/add/forget/export/import` | Agent memory CRUD |
| `oporch replay <run_id> [--wu] [--limit]` | Run reconstruction |
| `oporch diff <run_a> <run_b>` | Compare two runs |
| `oporch logs [--last N]` | Structured event log |
| `oporch models` | Model mappings |
| `oporch doctor` | Health checks |
| `oporch merge-integration <target>` | Merge integration into base branch |
| `oporch approvals` | List pending merge approvals |
| `oporch approve/reject <key>` | Resolve approvals |
| `oporch migrate-db` | Backfill legacy JSON into SQLite |

---

## ⚠️ Error Handling

| Exception | When |
|-----------|------|
| `ConfigError` | Missing/malformed YAML |
| `InvalidTransitionError` | Illegal state transition |
| `RunStateError` | Schema version mismatch on load |
| `CircularDependencyError` | Cycle in work unit DAG |
| `WorkUnitGraphError` | Unknown dependency reference |
| `OrchestratorError` | Orchestrator-level failures |
| `RunnerError` | No work units or execution failures |
| `GitManagerError` | Git operations failed |
| `MergeConflictError` | Squash merge conflict |
| `ProtectedBranchError` | Merge to protected branch |

All state files carry a `schema_version` field — mismatches raise `RunStateError` to prevent silent data corruption.

---

## 🧪 Testing

```bash
pytest -v     # 349 tests, ~34 seconds
```

### 24 Test Files

| Test File | Tests | Focus |
|-----------|-------|-------|
| `test_repl.py` | 16 | Slash dispatch, multi-line input, plan detection, clean exit |
| `test_runner.py` | 22 | Full milestone execution, retry ladder, review/test integration |
| `test_dispatcher.py` | 16 | Parallel waves, semaphore bounds, resize, cancellation |
| `test_db.py` | 25 | SQLite CRUD for all 7 tables, WAL mode, migration, export/import |
| `test_git_manager.py` | 20 | Worktree create/cleanup, commit, diff, merge gate, protected branches |
| `test_security.py` | 32 | 14 secret patterns, env sandboxing, redaction |
| `test_orchestrator.py` | 9 | plan/run/resume, state transitions |
| `test_team_composer.py` | 25 | Sizing bands, domain inference, roster validation |
| `test_context_builder.py` | 39 | Plan-doc parsing, per-role context building |
| `test_state_machine.py` | 20 | All transitions, history, terminal detection |
| `test_roster_scaling.py` | 15 | Suggest/apply for spawn/retire/resize |
| `test_memory_wiring.py` | 10 | Memory recall → context injection |
| `test_observability.py` | 9 | Structured events, replay, diff stats |
| `test_dashboard.py` | 8 | TUI rendering, WU cards, role panels |
| `test_config.py` | 12 | Load/save YAML, resolve_model, fallback chain |
| `test_validate.py` | 9 | JSON repair, planner output, schema mismatch |
| `test_work_unit.py` | 11 | DAG validation, topo sort, cycle detection |
| `test_event_log.py` | 6 | Record, filter, all, persistence |
| `test_decision_ledger.py` | 8 | Append, search, find-by-question, clear |
| `test_redact.py` | 14 | All 14 secret patterns |
| `test_executor.py` | 5 | FakeAgentExecutor call tracking |
| `test_doctor.py` | 3 | Health check pass/fail |
| `test_run_state.py` | 8 | CRUD, worker output persistence |
| `test_dashboard.py` | 8 | TUI rendering, WU cards, role panels |

---

## 📁 Project Structure

```
oporch/
├── src/oporch/
│   ├── __init__.py
│   ├── __main__.py          # Entry point (python -m oporch)
│   ├── repl.py              # Interactive REPL with slash commands
│   ├── cli.py               # Typer CLI (legacy + REPL callback)
│   ├── orchestrator.py      # HeadOrchestrator
│   ├── runner.py            # MilestoneRunner + ParallelDispatcher
│   ├── supervisor.py        # Model selection, self-healing, file access
│   ├── team_composer.py     # Dynamic roster composition
│   ├── roster_scaling.py    # Phase-boundary auto-scaling
│   ├── context_builder.py   # Plan parser + context packs
│   ├── executor.py          # OpenCode + Fake agent executors
│   ├── git_manager.py       # Git worktree isolation
│   ├── db.py                # SQLite storage (WAL)
│   ├── dashboard.py         # Live Textual TUI
│   ├── models.py            # Pydantic v2 schemas
│   ├── constants.py         # Enums (states, roles, events)
│   ├── config.py            # YAML loaders + model resolver
│   ├── state_machine.py     # FSM transitions
│   ├── redact.py            # Secret scrubbing
│   ├── validate.py          # JSON repair
│   ├── run_state.py         # Legacy JSON persistence
│   ├── event_log.py         # Event JSONL
│   ├── decision_ledger.py   # Decision Q&A
│   ├── work_unit.py         # DAG operations
│   ├── doctor.py            # Health checks
│   └── prompts/
│       ├── planner.md       # Planner system prompt
│       └── team_composer.md # Composer system prompt
├── tests/                   # 24 test files, 349 tests
├── pyproject.toml           # Package config
├── .gitignore
└── README.md                # This file
```

---

## 🛠️ Development

```bash
git clone https://github.com/0x4rv1nd/oporch.git
cd oporch
pip install -e ".[dev]"
pytest -v                  # 349 tests, ~34 seconds
```

---

## 🗺️ Roadmap

| Milestone | Focus | Status |
|-----------|-------|--------|
| **M0** | Foundation: CLI, models, config, state machine, persistence, DAG, executors, orchestration | ✅ |
| **M1** | SQLite storage, dynamic team composer, `COMPOSING_TEAM` state, plan-doc parser | ✅ |
| **M2** | Parallel dispatcher, git worktree isolation, supervisor merge gate | ✅ |
| **M3** | Live TUI dashboard, agent memory, structured events | ✅ |
| **M4** | Roster auto-scaling, security hardening, observability (`/replay`, `/report`) | ✅ |
| **M5** | Interactive REPL with slash commands (opencode-style UX) | ✅ |
| **M6** | Wire git worktrees into runner, auto-populate failure patterns, token tracking | 🔜 |
| **v3** | Containerized sandboxing, full supervisor model selection, self-healing ladder | 📋 |

---

<p align="center">
  <sub>Built with opencode · Python · Typer · Rich · Pydantic · Textual · SQLite</sub>
</p>
