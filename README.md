<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/version-0.1.0-orange?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/tests-84%20passing-brightgreen?style=for-the-badge" alt="Tests">
  <img src="https://img.shields.io/badge/status-foundation%20complete-blueviolet?style=for-the-badge" alt="Status">
</p>

<h1 align="center">oporch</h1>
<p align="center"><strong>Multi-Agent Orchestration System for OpenCode</strong></p>

<p align="center">
  Decompose milestones → parallel DAG of work units →<br>
  specialized AI agents (Builder, Reviewer, Tester, Debugger)<br>
  → evidence collection → gated completion
</p>

<p align="center">
  <code>oporch plan M1</code> &nbsp;·&nbsp;
  <code>oporch run M1</code> &nbsp;·&nbsp;
  <code>oporch report</code>
</p>

---

## The Problem

Building software with AI agents means context-switching across 9 roles manually — planning, architecting, building, reviewing, testing, debugging, researching, benchmarking, orchestrating. Each role needs a different model, different prompt, different workflow.

**oporch** manages all of it. One orchestrator handles state, routing, retries, and escalation so you focus on decisions, not logistics.

---

## Quick Start

```bash
pip install oporch
oporch init
oporch doctor
oporch plan M1 --objective "Add user authentication"
```

**Requirements:** Python 3.12+, [opencode](https://opencode.ai) CLI, git

---

## CLI Reference

| Command | Description | Status |
|---------|-------------|--------|
| `init` | Create `.opencode-orchestrator/` with default configs | ✅ |
| `doctor` | 8 health checks: CLI, configs, git, pytest | ✅ |
| `status` | Show active run state, milestone, WU tree | ✅ |
| `plan <id> [--objective]` | Decompose milestone into DAG via planner agent | ✅ |
| `models` | Show resolved role-to-model-ID mappings | ✅ |
| `cancel` | Cancel active run | ✅ |
| `run <id>` | Execute approved milestone plan | 🔜 |
| `resume` | Resume interrupted run | 🔜 |
| `report` | Generate evidence-backed final report | 🔜 |

### `oporch init`

Creates this directory tree (idempotent — never overwrites existing configs):

```
.opencode-orchestrator/
├── config/
│   ├── roles.yaml       — 9 agent roles with model + fallback
│   ├── models.yaml      — 3 models with real OpenCode model IDs
│   └── policies.yaml    — approval mode, retry, completion gates
├── state/               — current_run.json, decisions.jsonl
├── context/             — auto-generated project_summary.md
└── runs/                — per-run state, plans, events, worker outputs
```

### `oporch plan`

1. Creates a new run (8-char UUID)  
2. Transitions: `IDLE → ANALYZING → PLANNING`  
3. Scans `src/` and `PRD.md` for repo context  
4. Prompts the Planner agent — validates JSON output (auto-repairs code fences, brace extraction)  
5. Supports Planner asking clarification questions  
6. Presents work unit table + assumptions for user approval  

### `oporch doctor`

8 checks: opencode CLI, config init, roles YAML, policies YAML, models YAML, git, writability, pytest. Non-zero exit on any FAIL.

### `oporch models`

Prints each role's model key, fallback, and resolved `model_id`. Debug model resolution without running a plan.

### `oporch status` / `cancel`

Shows active run with colored work-unit tree (blockers shown inline). `cancel` clears the current run.

---

## Architecture

### Module Map

| Module | Lines | Responsibility |
|--------|------:|----------------|
| `cli.py` | 365 | Typer CLI, 9 commands, default config writers |
| `constants.py` | 109 | Enums: 14 states, 9 roles, 7 WU statuses, 17 event types |
| `models.py` | 296 | 25+ Pydantic v2 domain + config schemas |
| `config.py` | 67 | YAML loaders, `resolve_model()` with fallback chain |
| `state_machine.py` | 61 | Transition table, history, terminal/active detection |
| `run_state.py` | 153 | `PersistentRunState`: JSON file I/O for runs, WUs, plans |
| `work_unit.py` | 111 | `WorkUnitGraph`: DAG validation, topo sort, cycle detection |
| `event_log.py` | 61 | `EventLog`: JSONL append, cache, filter by type |
| `decision_ledger.py` | 64 | `DecisionLedger`: JSONL Q&A, search, find-by-question |
| `doctor.py` | 115 | 8 health checks |
| `executor.py` | 127 | `FakeAgentExecutor` + `OpenCodeAgentExecutor` |
| `orchestrator.py` | 195 | `HeadOrchestrator`: plan_milestone, state machine, persistence |
| `validate.py` | 90 | JSON validation + auto-repair |

### State Machine (14 states)

```
IDLE → ANALYZING → PLANNING → AWAITING_PLAN_APPROVAL → EXECUTING
  → REVIEWING → TESTING → VALIDATING → COMPLETED
```

Plus `DEBUGGING`, `REPLANNING`, `AWAITING_USER_INPUT`, `FAILED`, `CANCELLED`.
Every transition validated against the transition table, timestamped, and persisted.

### Agent Pipeline

```
Planner → PlanResult → (user approves) → WorkUnitGraph
  → for each ready WU:
    → Builder (implements)
    → Reviewer (adversarial review)
    → Tester (validates acceptance criteria)
    → (loop or next WU)
  → HeadOrchestrator validates evidence → COMPLETED / FAILED
```

### Model Resolution

Each role references a **logical model key** (e.g. `nemotron-ultra`).
`resolve_model()` looks it up in `models.yaml`, returns the real
`model_id` (e.g. `opencode/nemotron-3-ultra-free`). If the primary key
isn't found, it uses the role's `fallback`. Returns `None` if neither resolves.

This decouples role config from actual model IDs — swap models in one place.

---

## Configuration

### `roles.yaml`

9 roles with model key, optional fallback, max workers:

```yaml
roles:
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
  benchmark_analyst:
    model: "nemotron-ultra"
    fallback: "deepseek-v4-flash"
```

Premium roles have fallbacks so execution continues if the primary model is unavailable.

### `models.yaml`

Maps logical keys to real OpenCode model IDs:

```yaml
models:
  deepseek-v4-flash:
    provider: "deepseek"
    model_id: "opencode/deepseek-v4-flash-free"
    context_limit: 131072
    output_limit: 16384
  nemotron-ultra:
    provider: "nvidia"
    model_id: "opencode/nemotron-3-ultra-free"
  mimo-v2.5:
    provider: "deepseek"
    model_id: "opencode/mimo-v2.5-free"
```

### `policies.yaml`

```yaml
approval_mode: SUPERVISED   # SUPERVISED | AUTONOMOUS | STRICT
retry:
  max_attempts: 3
  attempt_2_receives_review: true
  attempt_3_uses_debugger: true
completion_gate:
  require_review_approval: true
  require_tests_pass: true
```

---

## Agents

### FakeAgentExecutor

Records all calls `(role, task, context)` for test assertions.
Supports `set_next_result()` for deterministic scenarios.

### OpenCodeAgentExecutor

Dispatches to the real opencode CLI:

```python
executor = OpenCodeAgentExecutor(opencode_cmd="opencode")
result = executor.run(AgentRole.BUILDER, task, context_pack)
```

Builds role-specific prompts with objective, acceptance criteria, PRD sections,
relevant files, architecture constraints. Passes `-m <model_id>` flag.

---

## Testing

```
pytest -v    # 84 tests passing
```

| Module | Tests | Status |
|--------|-------|--------|
| Config | load/save, resolve_model, fallback chain, schema versioning | ✅ |
| State machine | transitions, history, terminal detection, invalid transitions | ✅ |
| Run state | CRUD, worker output persistence, plan save/load | ✅ |
| Work unit graph | DAG validation, topological sort, ready detection, cycle detection | ✅ |
| Event log | record, filter, all, persistence | ✅ |
| Decision ledger | append, search, find-by-question, clear | ✅ |
| Doctor | 8 health checks | ✅ |
| Executor | FakeAgentExecutor call tracking | ✅ |
| Orchestrator | plan_milestone, state transitions, planner prompt, question flow | ✅ |
| Validate | JSON repair, planner output, schema mismatch | ✅ |

---

## Project Structure

```
oporch/
├── src/oporch/
│   ├── cli.py              # Typer CLI
│   ├── constants.py         # Enums (states, roles, events)
│   ├── models.py            # Pydantic v2 schemas
│   ├── config.py            # YAML loaders + resolve_model()
│   ├── state_machine.py     # StateMachine
│   ├── run_state.py         # PersistentRunState (JSON I/O)
│   ├── work_unit.py         # WorkUnitGraph (DAG)
│   ├── event_log.py         # EventLog (JSONL)
│   ├── decision_ledger.py   # DecisionLedger (JSONL)
│   ├── doctor.py            # Health checks
│   ├── executor.py          # Agent executors
│   ├── orchestrator.py      # HeadOrchestrator
│   ├── validate.py          # JSON validation + repair
│   └── prompts/
│       └── planner.md       # Planner system prompt
├── tests/
│   ├── test_config.py
│   ├── test_state_machine.py
│   ├── test_run_state.py
│   ├── test_work_unit.py
│   ├── test_event_log.py
│   ├── test_decision_ledger.py
│   ├── test_doctor.py
│   ├── test_executor.py
│   ├── test_orchestrator.py
│   └── test_validate.py
├── CONTEXT.md
├── pyproject.toml
└── README.md
```

---

## Error Handling

| Exception | When |
|-----------|------|
| `ConfigError` | Missing/malformed YAML |
| `InvalidTransitionError` | Illegal state transition |
| `RunStateError` | Schema version mismatch on load |
| `CircularDependencyError` | Cycle in work unit DAG |
| `WorkUnitGraphError` | Unknown dependency reference |
| `OrchestratorError` | Orchestrator-level failures |

All state files carry a `schema_version` field — mismatches raise `RunStateError`
to prevent silent data corruption across version upgrades.

---

## Development

```bash
pip install -e ".[dev]"
pytest -v
mypy src/oporch/
ruff check src/oporch/
```

---

## Roadmap

| Milestone | Focus | Status |
|-----------|-------|--------|
| **M0** | Foundation: CLI, models, config, state machine, persistence, DAG, executors, orchestration, validation | ✅ Done |
| **M1** | Real agent dispatch: `run`/`resume`, OpenCodeAgentExecutor wiring, result collection | 🔜 |
| **M2** | Reporting: `report` command, evidence aggregation, milestone reports | 🔜 |
| **M3** | Escalation: user-in-the-loop, manual intervention, debugger integration | 🔜 |

---

<p align="center">
  <sub>Built with opencode · Python · Typer · Rich · Pydantic</sub>
</p>
