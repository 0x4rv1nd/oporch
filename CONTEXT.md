# oporch — Multi-Agent Orchestration System for OpenCode

## What
CLI tool that decomposes software milestones into parallel DAGs of work units, executes them through specialized AI agents (Builder, Reviewer, Tester, Debugger, etc.), collects evidence, and gates completion on review+test approval.

## Why
Stop context-switching across 9 agent roles manually. One orchestrator manages state, routing, retries, escalation.

## Architecture

```
M0 FOUNDATION (done, 84 tests)

cli.py              Typer CLI: init/doctor/status/cancel
                    plan/run/report (stubs) + models cmd
constants.py        enums: OrchestratorState (14 states),
                    AgentRole, WorkUnitStatus, EventType
models.py           Pydantic v2 schemas: all domain models,
                    ModelInfo with model_id field,
                    RoleConfig with fallback
config.py           load_roles/policies/models +
                    resolve_model() w/ fallback chain,
                    returns model_id string
state_machine.py    Enum+table state machine, validates
                    every transition, history tracking
run_state.py        PersistentRunState: JSON file I/O
                    for runs/current/WUs, schema_version
                    validation on load
work_unit.py        WorkUnitGraph DAG: add/get/ready/
                    topological sort, cycle detection
event_log.py        JSONL event log for audit trail
decision_ledger.py  DecisionLedger: searchable Q&A
doctor.py           health checks: dirs, configs, models, git, pytest
executor.py         FakeAgentExecutor for testing +
                    OpenCodeAgentExecutor passes -m <model_id>
                    flag to opencode CLI
orchestrator.py     HeadOrchestrator: plan_milestone() with
                    state transitions, planner prompt, persistence
validate.py         JSON output validation with repair
                    (code fence stripping, brace extraction)
```

## Data Flow
```
oporch init  → .opencode-orchestrator/{config,state,context,runs,locks}/
oporch plan M1 → HeadOrchestrator → Planner → PlanResult
  → user approves plan → stored to disk
oporch run M1 → HeadOrchestrator → WorkUnitGraph
  → for each ready WU: OpenCodeAgentExecutor with -m <model_id>
  → Builder → Reviewer → Tester → (loop|next)
  → HeadOrchestrator validates evidence → COMPLETED|FAILED
```

## State Machine (14 states)
IDLE → ANALYZING → PLANNING → AWAITING_PLAN_APPROVAL → EXECUTING → REVIEWING → TESTING → VALIDATING → COMPLETED (plus DEBUGGING, REPLANNING, AWAITING_USER_INPUT, FAILED, CANCELLED)

## Config Layout
```
.opencode-orchestrator/
├── config/
│   ├── roles.yaml      — 9 roles, each with model + fallback
│   ├── models.yaml     — 3 models with model_id (real OpenCode IDs)
│   └── policies.yaml   — approval_mode, retry, completion gates
├── state/              — current_run.json, decisions.jsonl
├── context/            — project_summary.md (auto-generated)
├── runs/               — per-run state, plans, events, worker outputs
└── locks/
```

## Model Resolution
Each role config references a logical model key (e.g. `nemotron-ultra`).
`resolve_model()` looks up the key in `models.yaml`, finds the real
`model_id` (e.g. `opencode/nemotron-3-ultra-free`). If primary not
found, uses `fallback`. Returns `None` if neither resolves.

## PRD Gaps Fixed
- schema_version field + validation on all state loads
- resolve_model() validates against models.yaml, uses fallback, returns model_id
- ModelInfo.model_id field for mapping logical names to OpenCode model IDs
- RoleConfig.fallback field on premium-model roles
- OpenCodeAgentExecutor passes resolved model_id via -m flag
- oporch models CLI command shows resolved model IDs per role

## Tests (84 passing)
- Config: load/save, resolve_model, fallback chain, schema versioning
- State machine: transitions, history, terminal detection
- Run state: CRUD, worker output persistence
- Work unit graph: DAG validation, topological sort, ready detection
- Event log: record, filter, persistence
- Decision ledger: append, search, find by question
- Doctor: all health checks
- Executor: FakeAgentExecutor call tracking
- Orchestrator: plan milestone, planner prompt, state transitions
- Validate: JSON repair, planner output validation

## Next Big Step
Implement real agent dispatch in `run` and `resume` commands
(OpenCodeAgentExecutor is wired up but not called from orchestrator yet).
