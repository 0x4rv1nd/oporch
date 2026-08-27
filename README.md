<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/version-2.0.0-orange?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/tests-386%20passing-brightgreen?style=for-the-badge" alt="Tests">
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
- [Installation Guide](#-installation-guide)
- [Quick Start](#-quick-start)
- [Interactive Commands](#-interactive-commands)
- [Built-in Smart Proxy & Rate-Limit Shield](#-built-in-smart-proxy--rate-limit-shield)
- [Native Codebase AST Indexer](#-native-codebase-ast-indexer)
- [Supervisor & Model Selection](#-supervisor--model-selection)
- [Headroom Proxy Integration](#-headroom-proxy-integration-optional)
- [Codebase-Memory MCP Integration](#-codebase-memory-mcp-integration-optional)
- [Architecture](#-architecture)
- [Data Flow & Lifecycle](#-data-flow--lifecycle)
- [State Machine](#-state-machine)
- [Module Reference](#-module-reference)
- [Configuration](#-configuration)
- [Data Storage (SQLite)](#-data-storage-sqlite)
- [Dynamic Team Composition](#-dynamic-team-composition)
- [Parallel Execution Engine](#-parallel-execution-engine)
- [Git Isolation & Merge Gate](#-git-isolation--merge-gate)
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
5. 🛡️ **Shields your runs** — built-in rate-limit retries, quota fallback & AST codebase intelligence
6. 🔒 **Gates completion** — adversarial review + testing + evidence validation
7. 🧠 **Learns** — persistent memory across runs (gotchas, conventions, failure patterns)

No flags. No multi-step commands. Just paste and build.

### Key Design Principles

| Principle | How oporch implements it |
|-----------|--------------------------|
| **Paste and go** | Interactive REPL: paste a plan, team is composed, agents execute |
| **One orchestrator, many agents** | `HeadOrchestrator` manages state, routing, retries, and escalation |
| **Dynamic teams** | Roster is sized per-plan, not a fixed enum |
| **Supervisor intelligence** | Dynamic model tier selection, self-healing recovery, and merge gate |
| **Built-in Smart Proxy** | Executor-level rate-limit backoff, quota model fallback & concurrency limits |
| **Native Codebase Indexer** | AST symbol extraction & caller graph stored in SQLite; auto-enriches context |
| **Parallel execution** | Asyncio dispatcher with per-role semaphore concurrency |
| **Git isolation** | One worktree per work unit — agents never share a working directory |
| **Evidence-gated completion** | Review + test + supervisor merge must all pass before `COMPLETED` |
| **Persistent memory** | Cross-run fact/gotcha/convention/failure_pattern store in SQLite |
| **Secrets never enter context** | `redact.py` scrubs all payloads before persistent storage |

---

## 📦 Installation

### One-line Install (Recommended)

**macOS / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/0x4rv1nd/oporch/master/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/0x4rv1nd/oporch/master/install.ps1 | iex
```

The script auto-detects `uv` → `pipx` → `pip` and picks the best one.

---

### Manual Install — pick your preferred tool

```bash
# ① uv (fastest — recommended if you have it)
uv tool install oporch

# ② pipx (best for CLI tools — isolated, no env conflicts)
pipx install oporch

# ③ pip (standard)
pip install oporch
```

### From Source (dev / contribute)

```bash
git clone https://github.com/0x4rv1nd/oporch.git
cd oporch
pip install -e ".[dev]"   # installs with pytest, ruff, mypy extras
```

### Prerequisites

- **Python 3.12+** — [python.org](https://python.org)
- **[OpenCode CLI](https://opencode.ai)** — `opencode` must be in PATH
- **Git** — for worktree isolation per work unit

### Verify the install

```bash
oporch doctor
```

```
┌─────────────────────────── Environment Health Check ───────────────────────────┐
│ ✓ Python 3.12+ detected                                                        │
│ ✓ OpenCode CLI available                                                       │
│ ✓ Git repository detected                                                      │
│ ✓ Default configs initialized (.opencode-orchestrator/config)                  │
│ ✓ Model mappings resolved in models.yaml                                       │
│ ✓ SQLite database ready (WAL mode)                                             │
└────────────────────────────────────────────────────────────────────────────────┘
```

## 🚀 Quick Start

```bash
oporch                    # opens the interactive session
```

That's it. Paste your implementation plan and follow the prompts:

```
╭─ ⚡ oporch ── Multi-Agent Orchestrator ──────────────────────╮
│  📂 myproject                                                 │
│  State: IDLE  Head Model: nemotron-ultra                      │
│                                                               │
│  Paste your implementation plan below, or type /help          │
│  Use /head-model to switch supervisor, or /quit to exit.      │
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

🤖 Head Supervisor Model: nemotron-ultra

Sub-Agent Roster & Assigned Models:
┌──────────┬───────────────────┬───────────┬─────────┬────────────────────────┐
│ Role     │ Assigned Model    │ Fallback  │ Workers │ Domains / Scope        │
├──────────┼───────────────────┼───────────┼─────────┼────────────────────────┤
│ db       │ mimo-v2.5         │ fast-code │ 1       │ database, db, migration│
│ backend  │ deepseek-v4-flash │ mimo-v2.5 │ 2       │ api, auth, fastapi     │
│ frontend │ deepseek-v4-flash │ mimo-v2.5 │ 1       │ ui, react, page        │
│ reviewer │ nemotron-ultra    │ —         │ 2       │ security & ast review  │
│ tester   │ deepseek-v4-flash │ —         │ 2       │ testing, regression    │
└──────────┴───────────────────┴───────────┴─────────┴────────────────────────┘

📋 9 work units proposed:
┌────────┬────────────────────────┬───────────────┬──────────────┐
│ ID     │ Title                  │ Assigned Role │ Dependencies │
├────────┼────────────────────────┼───────────────┼──────────────┤
│ WU-001 │ Create users table     │ db            │ --           │
│ WU-002 │ Migration script       │ db            │ WU-001       │
│ WU-003 │ POST /register         │ backend       │ WU-001       │
│ WU-004 │ POST /login            │ backend       │ WU-001       │
│ WU-005 │ GET /me                │ backend       │ WU-004       │
│ WU-006 │ JWT middleware         │ backend       │ WU-004       │
│ WU-007 │ Login page             │ frontend      │ WU-004       │
│ WU-008 │ JWT storage            │ frontend      │ WU-006       │
│ WU-009 │ Route guards           │ frontend      │ WU-008       │
└────────┴────────────────────────┴───────────────┴──────────────┘

Approve and build? [Yes / no / customize sub-models / head-model]: y
✓ Plan approved
▶ Executing...  (type /status or /view for progress)
```

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

### Codebase Intelligence & Proxy

| Command | What it does |
|---------|-------------|
| `/index [--full]` | Trigger incremental (or full) AST re-indexing of the project |
| `/search <pattern>` | Regex/substring search across indexed classes, functions & methods |
| `/callers <name>` | Find all recorded call sites calling `<name>` |
| `/arch` | Print architecture summary (entry points, hotspot functions, top modules) |
| `/proxy-stats` | Show rate-limit retries, quota fallbacks, and per-model token usage |

### Model & Team Selection

| Command | What it does |
|---------|-------------|
| `/head-model [name]` | View or interactively select the Head Supervisor model |
| `/sub-models` | View or customize models assigned to sub-agents |
| `/team` | Show the active roster (roles, models, workers, domains) |
| `/team edit` | Interactive roster editor: add, remove, resize |
| `/team history` | Show which roles were active during which phases |

### Memory & Learning

| Command | What it does |
|---------|-------------|
| `/memory` | List what agents have learned on this project |
| `/remember <text>` | Teach the builder a fact or convention |
| `/forget <id>` | Delete a memory |

### Observability & System

| Command | What it does |
|---------|-------------|
| `/view` | Open the live TUI dashboard (Textual app) |
| `/replay [run_id]` | Chronological reconstruction of a run |
| `/report` | Evidence-backed final report |
| `/report failures` | Aggregate failure patterns across all runs |
| `/logs [N]` | Show last N structured events |
| `/models` | Show resolved role → model mappings |
| `/doctor` | 8 environment health checks |
| `/help` | Show all available commands |
| `/quit` or `/q` | Exit oporch |

---

## 🛡️ Built-in Smart Proxy & Rate-Limit Shield

`oporch` includes an embedded execution-level proxy layer (`RetryingOpenCodeExecutor`) so you never have to worry about rate-limits or quota stalls during long multi-agent runs:

- **⚡ Rate-Limit Detection**: Automatically detects `429`, `rate_limit_error`, `too many requests`, `throttled`, and `retry after` in agent execution streams.
- **⏳ Exponential Backoff + Jitter**: Performs exponential backoff (`min(base * 2^attempt, cap)`) with ±20% jitter to prevent thundering herd spikes.
- **🔄 Instant Quota Fallback**: When hitting `insufficient_quota`, `exceeded your current quota`, or credit exhaustion, oporch immediately switches the role to its designated fallback model without halting the run.
- **🚦 Concurrency Limiter**: Employs per-model semaphores across concurrent agents to keep simultaneous model invocations safely within provider burst limits.
- **🔌 Headroom Coexistence**: Detects if an external Headroom proxy is already active on `localhost:8787` and defers transparently without conflict.

Check live retry & token metrics at any time during your session:
```
oporch ❯ /proxy-stats
```

---

## 🔍 Native Codebase AST Indexer

`oporch` includes a lightweight, built-in code intelligence engine (`CodebaseIndexer`) that indexes your project directly into `oporch.db` using Python's standard library `ast` (plus regex parsers for JS, TS, Go, Rust, Java, and Kotlin):

- **AST Symbol Graph**: Extracts functions, classes, methods, docstrings, imports, and cross-file call sites without third-party dependencies.
- **Incremental Background Indexing**: Auto-indexes changed files on startup via `mtime` comparison in <0.1s.
- **Automated Context Enrichment**: When a builder agent is assigned a work unit, `context_builder.py` automatically injects the affected file signatures and call-graph hierarchies into the agent's context.
- **Instant Code Discovery**: Query symbols and call graphs on the fly:
  ```
  oporch ❯ /search User
  oporch ❯ /callers execute_work_unit
  oporch ❯ /arch
  ```

---

## 🧠 Supervisor & Model Selection

`oporch` features a tiered intelligence architecture separating high-level supervision from specialized sub-agent execution.

### How Model Assignment Works

1. **You choose the Head Supervisor Model** (e.g. `big-pickle`, `nemotron-ultra`, `claude-3-7-sonnet`).
2. **The Head Model analyzes the plan and assigns sub-models** for all roles (builders, reviewers, testers, debuggers) based on task domain and complexity.
3. **You have complete control to customize**: During plan approval, press `c` or use `/sub-models` to override any sub-agent's model before execution.

```
                      ┌─────────────────────────────────────────┐
                      │          HEAD SUPERVISOR MODEL          │
                      │   (big-pickle / nemotron-ultra)         │
                      │  Analyzes plan & assigns sub-models     │
                      └────────────────────┬────────────────────┘
                                           │
       ┌────────────────────────┬──────────┴─────────────┬────────────────────────┐
       ▼                        ▼                        ▼                        ▼
┌──────────────┐         ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│  DB AGENT    │         │  UI BUILDER  │         │   REVIEWER   │         │    TESTER    │
│ (big-pickle) │         │ (deepseek-v4)│         │(nemotron-3)  │         │ (big-pickle) │
│ standard tier│         │  fast coder  │         │  heavy gate  │         │  validation  │
└──────────────┘         └──────────────┘         └──────────────┘         └──────────────┘
```

### Model Tiers

Models are grouped into 3 operational tiers in `models.yaml`:

| Tier | Role Type | Purpose | Default Example |
|------|-----------|---------|-----------------|
| **`fast`** | Builder, Planner, Researcher | High throughput, quick iteration, code generation | `deepseek-v4-flash` / `gpt-4o-mini` |
| **`standard`** | Builder, Debugger, Architect, DB Specialist | Balanced reasoning, deep context diagnostics (default) | `big-pickle` / `claude-3-5-sonnet` |
| **`heavy`** | Supervisor, Reviewer, Tester | Adversarial verification, AST checks, merge governance | `nemotron-ultra` / `claude-3-7-sonnet` |

### Selecting Models in the REPL

- **Switch Head Model**:
  ```
  oporch ❯ /head-model
  ```
- **Customize Sub-Agent Models**:
  ```
  oporch ❯ /sub-models
  ```
- **During Plan Approval**:
  ```
  Approve and build? [Yes / no / customize sub-models / head-model]: c
  ```

### Customizing Models (`roles.yaml` & `models.yaml`)

You can map any provider or model supported by OpenCode:

```yaml
# .opencode-orchestrator/config/models.yaml
models:
  big-pickle:
    provider: "opencode"
    model_id: "opencode/big-pickle"
    tier: "standard"
  lead-reviewer:
    provider: "anthropic"
    model_id: "anthropic/claude-3-7-sonnet"
    tier: "heavy"
```

```yaml
# .opencode-orchestrator/config/roles.yaml
roles:
  builder:
    model: "big-pickle"
    fallback: "deepseek-v4-flash"
    max_workers: 3
  supervisor:
    model: "big-pickle"
    fallback: "nemotron-ultra"
    max_workers: 1
```

---

## 🗜️ Headroom Proxy Integration (Optional)

For large runs with huge tool outputs or test logs, you can optionally pair oporch with **[Headroom](https://github.com/headroomlabs-ai/headroom)** for transparent token compression:

```bash
# 1. Install Headroom
pip install "headroom-ai[all]"

# 2. Wrap OpenCode
headroom wrap opencode

# 3. Launch oporch (built-in proxy automatically defers to Headroom)
oporch
```

---

## 🗺️ Codebase-Memory MCP Integration (Optional)

If you have **[codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)** installed, OpenCode agents can also query external Tree-Sitter knowledge graphs during execution for extended 158+ language coverage and 3D visual graph interfaces.

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

---

## 🧪 Testing

```bash
pytest -v     # 349 tests, ~34 seconds
```

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
