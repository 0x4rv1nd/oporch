# PRD — Multi-Agent Orchestration System for OpenCode ("oporch")

**Version:** 2.0 (rewrite for AI coding-agent handoff)
**Status:** Ready for M0 implementation
**Author context:** This is a generic orchestration layer. It will later host a CPU-first PDF parsing project, but must not contain any PDF-specific logic. Treat that future use only as a reason to keep the design domain-agnostic — not as a reason to build toward it now.

---

## 0. How to use this document (read first)

This PRD is written for an AI coding agent operating inside OpenCode. Before writing any code:

1. Read Sections 1–3 (scope, architecture, non-goals).
2. Execute the **Repository Inspection Checklist** (Section 4).
3. Produce the **Required Kickoff Report** (Section 20) as your first response.
4. Only then implement **M0** (Section 18) — nothing beyond it.

If any instruction below conflicts with what you actually find in the repository (existing conventions, package manager, test framework, OpenCode capabilities), **the real environment wins**. Document the deviation in the Decision Ledger (Section 10) instead of silently picking one or the other.

---

## 1. Objective

Build a reusable, local, CLI-driven orchestration system ("oporch") with:

- One **Head Orchestrator** with sole authority to decompose work, evaluate evidence, and declare completion.
- A set of **specialized worker roles** (Planner, Architect, Builder, Reviewer, Tester, Debugger, Researcher, Benchmark Analyst) that execute narrow tasks and never talk to the user directly.
- A durable, resumable **state machine** driving milestones through planning → execution → review → testing → validation → completion.
- A **model-agnostic execution layer** so any role can be pointed at any configured OpenCode model without touching orchestration code.

The system must work for arbitrary software projects. It is not aware of, and must not reference, the future PDF-parsing use case.

### 1.1 Non-Goals (explicit — do not implement)

- No PDF parsing, document extraction, or Docling integration of any kind.
- No deployment automation (no CD, no infra provisioning).
- No multi-repository or cross-project orchestration — one repo per run.
- No GUI/dashboard. CLI + JSON/markdown artifacts only.
- No autonomous execution beyond what a milestone's approval mode allows (Section 15).
- No parallel work-unit execution in M0 (see Section 6.3) — sequential only, with the DAG designed so parallelism can be added later without a schema change.

---

## 2. Architecture

```
USER
 │
 ▼
HEAD ORCHESTRATOR ──────────────────────────────────────────┐
 │                                                           │
 ▼                                                           │
TASK ANALYZER → PLANNER → WORK UNIT GRAPH                    │
 │                                                           │
 ▼                                                           │
┌─────────────────────────────────────────────┐              │
│ SPECIALIZED WORKERS                          │              │
│  Architect · Builder · Researcher            │              │
│  Reviewer · Tester · Debugger                │              │
│  Benchmark Analyst                           │              │
└─────────────────────────────────────────────┘              │
 │                                                           │
 ▼                                                           │
EVIDENCE COLLECTOR ────────────────────────────────────────► │
 │                                                           │
 ▼                                                           │
HEAD ORCHESTRATOR DECISION:
  ACCEPT · REVISE · RETRY · REPLAN · ESCALATE_TO_USER · COMPLETE
```

**Non-negotiable rule:** workers never message the user. A worker question always routes: `Worker → Head Orchestrator → (answer from PRD/decisions/repo conventions) OR (escalate to user)`. See Section 9 for the exact routing algorithm.

---

## 3. Design Principles

1. **Inspect before changing.** Never assume repo/tooling state — verify it.
2. **Evidence before claims.** A worker's "it works" is not accepted without a command, exit code, and output summary (Section 11).
3. **Root cause before fixes.** Debugger reproduces and traces before patching (Section 8.7).
4. **Minimal interruption, no invention.** The orchestrator answers what's derivable from the PRD/decisions/conventions; it escalates genuine ambiguity rather than guessing at product requirements.
5. **No infinite loops.** Bounded retries, then replan/escalate/fail (Section 12).
6. **No silent failure or hidden benchmark changes.**
7. **Anti-overengineering.** New abstractions require a stated current problem and current caller (Section 13).
8. **Preserve user changes.** Git-safe by default (Section 14).
9. **Stop at milestone boundary.** No future-milestone work, ever, without explicit approval.

---

## 4. Repository Inspection Checklist (Phase 0 — mandatory, before any code)

Produce findings for each item; do not proceed on assumption where a finding is missing.

| Area | What to determine |
|---|---|
| Repo structure | Layout, existing `AGENTS.md`, monorepo vs single package |
| Package manager | npm/pnpm/yarn/pip/poetry/uv — whichever is authoritative |
| OpenCode config | Version, config file location, supported agent/plugin mechanisms |
| Model/provider config | Actually configured model identifiers (do not invent IDs — see 5.3) |
| Git state | Current branch, uncommitted changes, remotes |
| Test framework | Runner, existing test commands, coverage tooling |
| CI | Existing pipelines that must not be broken |
| CLI conventions | Existing CLI patterns/naming to stay consistent with |

Output of this phase feeds directly into Section 20 (Kickoff Report).

---

## 5. Model & Role Strategy

### 5.1 Role → model intent (starting policy, not a hard mapping)

| Role | Preferred | Fallback |
|---|---|---|
| Head Orchestrator | fast/cheap general model | any configured general model |
| Planner | fast/cheap general model | same as orchestrator |
| Builder | fast/cheap general model | same as orchestrator |
| Reviewer | strongest reasoning model available | fast/cheap general model |
| Tester | strongest reasoning model available | secondary reasoning model |
| Debugger | secondary reasoning model | fast/cheap general model |
| Researcher | configurable, no default | — |
| Benchmark Analyst | strongest reasoning model available | fast/cheap general model |

This table encodes **intent** (cheap-and-fast for high-volume roles, strongest-reasoning for adversarial/verification roles), not literal model names — resolve names in 5.3.

### 5.2 Config shape

```yaml
# .opencode-orchestrator/config/roles.yaml
roles:
  orchestrator: { model: <resolved-id>, fallback: <resolved-id|null> }
  planner:      { model: <resolved-id>, fallback: <resolved-id|null> }
  builder:      { model: <resolved-id>, fallback: <resolved-id|null> }
  reviewer:     { model: <resolved-id>, fallback: <resolved-id|null> }
  tester:       { model: <resolved-id>, fallback: <resolved-id|null> }
  debugger:     { model: <resolved-id>, fallback: <resolved-id|null> }
  researcher:   { model: <resolved-id|null>, fallback: null }
  benchmark_analyst: { model: <resolved-id>, fallback: <resolved-id|null> }
```

### 5.3 Resolution rule (fills a gap in the original spec)

1. Read actual OpenCode model/provider configuration.
2. Map each role to a **valid, currently-configured** model ID.
3. If a preferred model isn't available, use the role's `fallback`.
4. If neither resolves, write `model: null` with a `# TODO: unresolved — see kickoff report` comment; the role must fail loudly (not silently default to some hardcoded guess) if invoked before this is fixed.
5. Never invent identifiers. Config must be changeable without touching orchestration code — the code reads `roles.yaml`, full stop.

---

## 6. Work Model

### 6.1 State machine

States: `IDLE, ANALYZING, PLANNING, AWAITING_PLAN_APPROVAL, EXECUTING, REVIEWING, TESTING, DEBUGGING, REPLANNING, AWAITING_USER_INPUT, VALIDATING, COMPLETED, FAILED, CANCELLED`

Allowed transitions:

```
IDLE → ANALYZING → PLANNING → AWAITING_PLAN_APPROVAL → EXECUTING
EXECUTING → REVIEWING
REVIEWING → TESTING | EXECUTING | FAILED
TESTING → VALIDATING | DEBUGGING
DEBUGGING → EXECUTING | REPLANNING | AWAITING_USER_INPUT
VALIDATING → COMPLETED | EXECUTING | REPLANNING
(any state) → CANCELLED   # explicit user cancel only
```

Implement as an explicit state machine (enum + transition table), not an unstructured loop. Invalid transitions must raise, not be silently coerced.

### 6.2 Work Unit schema

```python
class WorkUnit(BaseModel):
    id: str                      # "WU-001"
    title: str
    objective: str
    status: Literal["pending","ready","in_progress","blocked",
                     "review","testing","complete","failed"]
    dependencies: list[str]       # other WU ids
    assigned_role: str
    assigned_model: str
    input_context: dict           # pointers, not full blobs — see Section 9
    acceptance_criteria: list[str]
    attempts: int = 0
    max_attempts: int = 3
    evidence: list[EvidenceItem] = []
    output: dict | None = None
    blockers: list[str] = []
```

### 6.3 Concurrency note (gap fix)

M0 executes work units **sequentially in dependency order**. The DAG structure is nonetheless designed so a future milestone can run independent branches concurrently without a schema migration — do not build the concurrent executor now, but don't design `WorkUnit`/state files in a way that assumes strict single-threaded access either (e.g., avoid a single mutable in-memory list with no per-unit locking concept).

---

## 7. Persistent State Layout

```
.opencode-orchestrator/
├── config/
│   ├── roles.yaml
│   ├── policies.yaml          # approval mode, retry limits, escalation thresholds
│   └── models.yaml
├── state/
│   ├── schema_version.txt     # gap fix: enables safe migrations later
│   ├── current_run.json
│   ├── milestones.json
│   ├── work_units.json
│   └── decisions.jsonl
├── context/
│   ├── project_summary.md
│   ├── architecture.md
│   ├── constraints.md
│   └── prd_index.json
├── runs/
│   └── <run_id>/
│       ├── plan.json
│       ├── events.jsonl
│       ├── worker_outputs/
│       ├── reviews/
│       ├── tests/
│       ├── decisions/
│       └── final_report.md
└── locks/
```

Rules:
- Adapt paths to repo conventions if they conflict.
- **Never store secrets or API keys in this tree.** Redact before writing any log or event.
- Every state JSON file carries a `schema_version` field; the loader must reject or migrate unknown versions rather than guessing.

---

## 8. Agent Roles

Each role below has: responsibilities, explicit prohibitions, and output contract.

### 8.1 Head Orchestrator
Receives milestone objective → loads PRD/architecture/prior summaries → decomposes work → assigns workers → answers derivable worker questions → enforces scope → evaluates evidence → triggers retry/replan → escalates genuine ambiguity → is the **only** role that can declare completion.
Must never accept an unsupported success claim (see Section 11).

### 8.2 Planner
Reads objective + repo state → produces atomic work units with dependencies, acceptance criteria, likely risk, likely files touched, required tests. **Cannot modify code.**

```json
{
  "milestone_id": "M2",
  "objective": "...",
  "work_units": [
    {"id": "WU-001", "title": "...", "dependencies": [], "acceptance_criteria": []}
  ]
}
```

### 8.3 Architect
Reviews architectural impact, boundary violations, unnecessary coupling, future-maintenance risk. Proposes interfaces only when a current need exists (ties to Section 13). Cannot invent speculative abstractions.

### 8.4 Builder
Implements exactly one assigned work unit. Inspects relevant code first, makes the smallest coherent change, adds/updates tests, runs targeted tests, reports exact files changed and unresolved issues.
Must not: touch future milestones, silently change architecture, weaken tests to pass, alter benchmark expectations without evidence, or claim success without execution evidence.

### 8.5 Reviewer
Adversarial, evidence-based review of the actual diff for correctness, hidden assumptions, hardcoded behavior, regression risk, overfitting, missing edge cases, poor error handling, silent failure, unnecessary complexity, security issues, performance risk, test weakness. **Cannot modify code.**

```json
{
  "verdict": "APPROVE | REQUEST_CHANGES | BLOCK",
  "findings": [
    {"severity": "critical|high|medium|low", "file": "...", "location": "...",
     "issue": "...", "evidence": "...", "recommended_action": "..."}
  ]
}
```

### 8.6 Tester
Independently executes validation — does not trust Builder's summary. Checks acceptance criteria against implementation, runs targeted + regression tests, attempts edge cases, reports exact command/exit-code/output evidence.

### 8.7 Debugger
Required sequence: `FAILURE → REPRODUCTION → TRACE → EARLIEST FAILURE STAGE → ROOT CAUSE → GENERALIZED FIX → REGRESSION RISK`. Must not patch symptoms without completing the sequence.

### 8.8 Researcher
Investigates libraries/docs/repos, compares alternatives, checks license and maintenance status, estimates integration cost. Output must separate **verified fact / inference / recommendation**. Cannot directly change code.

### 8.9 Benchmark Analyst
Compares before/after metrics, verifies benchmark identity, flags drift and misleading claims (e.g. a "100% accuracy" claim not backed by ground truth). Must explicitly distinguish `detected_count` from `ground_truth_count`.

---

## 9. Worker Question Routing

Worker output when blocked:

```json
{
  "type": "QUESTION",
  "question_id": "QST-001",
  "question": "...",
  "why_needed": "...",
  "blocking": true,
  "options": [],
  "evidence_checked": []
}
```

Orchestrator algorithm (in order): search PRD → search decision ledger → inspect repo convention → inspect milestone constraints → inspect prior accepted work. If answerable, answer and log a decision (Section 10). If not, and it meets an escalation trigger, escalate to user.

**Escalate when:** product behavior materially changes; a destructive action is required; credentials/secrets are needed; requirements genuinely conflict; multiple valid choices carry major, hard-to-reverse consequences.
**Do not escalate:** routine implementation choices with no material product impact.

---

## 10. Decision Ledger

Append-only. Purpose: prevent repeated questions and give every non-obvious choice a paper trail.

```json
{
  "decision_id": "DEC-0001",
  "timestamp": "...",
  "run_id": "...",
  "milestone_id": "...",
  "question": "...",
  "decision": "...",
  "basis": ["PRD section 4", "existing repository convention", "test evidence"],
  "confidence": 0.91,
  "escalated_to_user": false
}
```

---

## 11. Context Packs (per role — do not broadcast full repo/PRD to everyone)

| Role | Receives |
|---|---|
| Builder | work unit, relevant files, relevant PRD sections, architecture constraints, accepted dependency outputs |
| Reviewer | acceptance criteria, actual diff, relevant tests, architecture constraints |
| Tester | acceptance criteria, changed files, test commands, benchmark definitions |
| Debugger | failure evidence, logs, relevant trace, changed diff |

This is a hard requirement for context/token efficiency, not a suggestion.

---

## 12. Evidence Contract

Every claim is classified as `CLAIM | EVIDENCE | INFERENCE | UNKNOWN`.

```json
{
  "claim": "Unit tests pass",
  "evidence": {"command": "pytest tests/unit -q", "exit_code": 0, "summary": "42 passed"}
}
```

An unsupported success claim (no command/exit code/output) must be rejected by the Head Orchestrator regardless of which role made it.

---

## 13. Retry Policy

Per work unit, `max_attempts: 3`:

- Attempt 1 — normal Builder pass.
- Attempt 2 — Builder receives reviewer/test failure evidence.
- Attempt 3 — Debugger performs root-cause analysis first, then Builder retries with that diagnosis.
- After max attempts: `REPLAN` or `ESCALATE_TO_USER` or `FAIL` — never a silent 4th attempt. Persist full attempt history in the work unit.

---

## 14. Anti-Overengineering Gate

Reject: abstractions with no current caller, future-milestone implementation, duplicate wrappers, unnecessary service layers, speculative plugin systems, unused config, broad refactors unrelated to the milestone.

Before adding any new abstraction, require and log:

```json
{"current_problem": "...", "current_callers": [], "why_existing_structure_is_insufficient": "..."}
```

---

## 15. Git & Command Safety

**Git:** inspect status first, never overwrite unrelated user changes. Flow: `milestone branch → work unit changes → tests → review → validation → commit`. Never force-push, delete branches, reset user changes, or rewrite history without explicit permission.

**Command classification:** `SAFE_READ | SAFE_TEST | WRITE_LOCAL | DESTRUCTIVE | EXTERNAL_SIDE_EFFECT`. Examples: `ls`, `git diff`, `pytest` → safe. `rm -rf`, migrations, deploys, force-push → require policy check or explicit user approval. If OpenCode doesn't expose enough interception hooks to actually enforce this, **say so explicitly in the kickoff report** — do not claim enforcement that isn't real.

---

## 16. OpenCode Integration Strategy

Determine what's genuinely available: subagents, commands, skills, plugins, hooks, MCP, tool permissions, session APIs, CLI invocation, model routing. Do not assume unverified features.

Preference order:
1. Native OpenCode agent/subagent capabilities
2. OpenCode-supported plugin/extension mechanism
3. Thin local orchestration controller invoking OpenCode sessions/commands
4. Shell-based fallback (last resort)

Document the chosen strategy and why, in the kickoff report.

---

## 17. CLI

| Command | Purpose |
|---|---|
| `oporch init` | Create config/state directories |
| `oporch doctor` | Verify OpenCode availability, config readability, model resolution, Git availability, project writability, discoverable test commands |
| `oporch models` | Show resolved role→model mapping |
| `oporch plan <milestone>` | Generate work-unit graph without coding |
| `oporch run <milestone>` | Execute an approved milestone |
| `oporch status` | Show current state and blockers |
| `oporch resume` | Resume an interrupted run |
| `oporch review` | Surface pending review/approval gates |
| `oporch logs` | Tail/print structured events |
| `oporch cancel` | Cancel current run (explicit user action) |
| `oporch report` | Generate the evidence-backed final report |

Command names may be adapted to existing project conventions found in Section 4.

---

## 18. Human Approval Modes

| Mode | Requires approval |
|---|---|
| `AUTONOMOUS` | Only on major ambiguity or destructive actions |
| `SUPERVISED` (**default**) | After plan; before major architecture change; before completion |
| `STRICT` | Before each work unit; before write operations if configured |

---

## 19. Completion Gate

A milestone is `COMPLETE` only when **all** of the following hold — Head Orchestrator alone evaluates this:

- All required work units complete, acceptance criteria checked.
- Reviewer verdict acceptable (no unresolved `critical`; no unresolved `high` unless explicitly accepted and logged).
- Tests pass, including relevant regression tests.
- Benchmark evidence available where required.
- Git diff is understood (not just present).
- Final report generated.

No milestone is complete "because Builder finished."

---

## 20. Required Kickoff Report (before writing any code)

Return, in this order:

1. Repository assessment (Section 4 findings)
2. Detected OpenCode integration capabilities (Section 16)
3. Actual configured model identifiers, resolved per Section 5.3
4. Chosen integration strategy and why
5. Exact M0 files to create
6. Exact existing files to modify
7. Proposed dependencies
8. Risks
9. Assumptions
10. Implementation sequence

Then: proceed directly into M0 unless a genuinely blocking decision exists (per Section 9's escalation criteria). Do not pause for approval on routine choices.

---

## 21. Final Report Template (`final_report.md`)

```markdown
# Milestone Report
## Objective
## Status
## Work Units
## Files Changed
## Tests Executed
  # per test: command, exit code, result
## Review Findings
## Benchmark Results
## Decisions Made
## User Escalations
## Known Limitations
## Unresolved Risks
## Evidence
## Recommendation
```

No "everything works" without evidence attached.

---

## 22. Observability

Structured JSONL events (`runs/<run_id>/events.jsonl`):

```json
{"timestamp": "...", "run_id": "...", "event": "WORK_UNIT_STARTED", "work_unit_id": "WU-002", "agent_role": "builder"}
```

Event vocabulary: `RUN_CREATED, PLAN_CREATED, PLAN_APPROVED, WORK_UNIT_READY, WORK_UNIT_STARTED, WORKER_QUESTION, ORCHESTRATOR_ANSWER, USER_ESCALATION, WORK_UNIT_COMPLETED, REVIEW_STARTED, REVIEW_FAILED, TEST_STARTED, TEST_FAILED, DEBUG_STARTED, REPLAN_STARTED, VALIDATION_STARTED, RUN_COMPLETED, RUN_FAILED`.

Redact any secret-shaped value before it's written to this log.

---

## 23. Implementation Stack

Inspect the repo/environment first (Section 4). If starting fresh, default to:

- **Python 3.11+** (simple subprocess orchestration, strong JSON handling, future PDF work likely Python too — but this is not a reason to add PDF-shaped code now)
- Typer (CLI), Pydantic v2 (schemas), PyYAML (config), Rich (terminal UI)

Do not add dependencies blindly — check what already exists in the repo first.

---

## 24. Testing Strategy

**Unit tests (no real model calls — use fake agent adapters):** state transitions (valid + invalid), work-dependency resolution, retry limits, question routing, decision ledger, context-pack generation, evidence validation, completion gate, resume behavior, config loading, model-role mapping, state schema versioning/migration.

**Integration tests:** fake planner output, fake builder success, reviewer rejection, tester failure, debugger loop, user escalation, interrupted-run resume, max-retry failure.

---

## 25. Model Adapter Interface

```python
class AgentExecutor(Protocol):
    def run(self, role: AgentRole, task: AgentTask, context: ContextPack) -> AgentResult: ...

class OpenCodeAgentExecutor:  # real implementation
    ...

class FakeAgentExecutor:  # test implementation
    ...
```

Keep orchestration state logic decoupled from subprocess/output parsing — the adapter boundary is what makes fake-executor testing possible.

---

## 26. Output Schemas

Define typed schemas (e.g., Pydantic) for: `PlanResult, WorkUnit, WorkerQuestion, BuilderResult, ReviewResult, TestResult, DebugResult, ResearchResult, BenchmarkResult, OrchestratorDecision, MilestoneReport`.

Invalid model output handling:
1. Preserve raw output.
2. Attempt one bounded repair pass.
3. Validate against schema.
4. Retry once if still invalid.
5. Mark as failed if still invalid — **never fabricate missing fields.**

---

## 27. M0 — Orchestrator Foundation (the only milestone to implement now)

**In scope:** repo inspection output, architecture decision record, project skeleton, typed schemas, state machine, persistent run state (with `schema_version`), work-unit DAG, decision ledger, event log, config loading, role/model mapping (Section 5.3), fake agent executor, OpenCode executor interface (may be a documented stub if capability isn't confirmed yet), CLI subset (`init`, `doctor`, `status`), unit tests.

**Explicitly out of scope for M0:** full autonomous execution loop, PDF parsing, Docling integration, research/browsing, deployment, concurrent work-unit execution.

### 27.1 M0 Acceptance Criteria

1. Project initializes cleanly.
2. State persists to disk with a schema version.
3. Invalid state transitions are rejected, not coerced.
4. Work dependencies resolve correctly; circular dependencies are detected and rejected.
5. Decisions and events append safely (no corruption on concurrent-ish writes).
6. Role/model config loads and resolves per Section 5.3, including the "unresolved" path.
7. Fake executor works end-to-end for at least one simulated work unit.
8. OpenCode executor boundary exists (real or documented stub).
9. `doctor` reports actual environment capabilities, not assumed ones.
10. Unit tests pass with zero real model calls.
11. No PDF-specific code exists anywhere in the tree.
12. No work beyond M0 scope has been implemented.

---

## 28. Governing Rules (non-negotiable)

1. Inspect before changing.
2. Evidence before claims.
3. Root cause before fixes.
4. Workers never interrupt the user directly.
5. Orchestrator answers derivable questions; escalates genuine ambiguity only.
6. No infinite retries.
7. No silent failure.
8. No hidden benchmark changes.
9. No future-milestone implementation.
10. No PDF parser implementation — not now, not incidentally.
11. Keep orchestration generic and domain-agnostic.
12. Use verified OpenCode capabilities only — never imagined APIs.
13. Preserve user changes; Git-safe by default.
14. Stop after M0 and report with evidence.

---

## 29. M0 Gap Checklist (post-implementation review)

Use this as a closeout checklist once M0 code exists — before declaring M0 `COMPLETE` per Section 19. Each item ties back to a specific section above; don't check it off on a "should work" basis — attach evidence (Section 12).

| # | Gap | Ties to | Verification needed |
|---|---|---|---|
| 1 | `resolve_model()` must validate the role's chosen model **against actually-configured models in `models.yaml`**, not just internal consistency between `roles.yaml` and itself | 5.3 | Test: role points at a model not present in `models.yaml` → resolution fails loudly, does not silently pick the fallback without logging why |
| 2 | Unresolved-model path is exercised, not just implemented | 5.3 | Test: both preferred and fallback missing → role returns `None`/raises, does **not** invent an ID |
| 3 | Schema version rejection, not just storage | 7, 22 | Test: `schema_version.txt` / state file with an unknown/future version number is rejected or triggers migration — currently only "written," not "enforced on load" |
| 4 | Circular dependency detection in work-unit DAG | 6.2, 27.1 (#4) | Test: WU-A depends on WU-B depends on WU-A → explicit rejection, not infinite loop or silent skip |
| 5 | Fake-executor **integration** path, not just unit-level schema checks | 24, 25 | Test: one full simulated work unit run produces valid `events.jsonl` + decision ledger entries on disk |
| 6 | `doctor` reports real detected capabilities, not a hardcoded checklist | 16, 27.1 (#9) | Manual check: run `doctor` against an environment where a capability is genuinely absent (e.g. OpenCode not on PATH) and confirm it reports that accurately, not a canned "OK" |
| 7 | Secret redaction in logs | 7, 22 | Test: a fake API-key-shaped string passed through context never appears verbatim in `events.jsonl` or `decisions.jsonl` |
| 8 | Git repository initialized with a deliberate `.gitignore` (not accidental) | 14 | Manual: confirm `.opencode-orchestrator/state/` and `runs/` are *intentionally* tracked or ignored, decision documented |
| 9 | Invalid state transition is rejected with a clear error, not coerced to nearest valid state | 6.1 | Test: force an illegal transition (e.g. `IDLE → TESTING`) → raises, state unchanged |
| 10 | `WorkUnit.max_attempts` boundary is enforced end-to-end, not just present as a field | 13 | Test: 3 failed attempts → next event is `REPLAN`/`ESCALATE_TO_USER`/`FAIL`, never a 4th silent attempt |

Do not mark M0 `COMPLETE` while any row above is unverified — log the row status in the Decision Ledger if intentionally deferred, per Section 9's "no invention" rule.

---

## 30. M1 — Planner Agent (first real LLM-integrated milestone)

M0 built the plumbing (schemas, state machine, DAG, ledger, event log, fake executor). M1 turns on the first real model call. Scope is deliberately narrow: **Planner only.** Builder/Reviewer/Tester/Debugger dispatch is M2+.

### 30.1 Scope

In scope:
- Real `OpenCodeAgentExecutor` call for the `planner` role only (Builder/Reviewer/etc. still use `FakeExecutor`).
- Planner context-pack construction (30.2).
- Planner prompt (30.3) loaded from a file, not hardcoded inline in `executor.py`.
- Generic **output validation-with-repair pipeline** (30.4), built once, reused by every future role.
- `oporch plan <milestone>` produces a real `PlanResult`, persists it to `runs/<run_id>/plan.json`, and moves state `ANALYZING → PLANNING → AWAITING_PLAN_APPROVAL`.
- A CLI approval flow for the plan (30.5).

Out of scope for M1: `run` actually executing work units, Reviewer/Tester/Debugger real calls, replanning logic, benchmark analysis.

### 30.2 Planner Context Pack

Do not send the whole repo or whole PRD. Build a `ContextPack` with exactly:

```python
class PlannerContextPack(BaseModel):
    milestone_id: str
    milestone_objective: str
    relevant_prd_sections: list[str]      # extracted text, not the whole PRD
    repo_summary: str                      # structure + key conventions, generated once per run, cached
    architecture_constraints: list[str]    # from context/architecture.md
    prior_decisions: list[DecisionLedgerEntry]   # only entries tagged relevant to this milestone
    prior_milestone_summaries: list[str]   # short summaries, not full reports
```

`repo_summary` should be generated once (e.g. during `oporch init` or first `doctor` run) and cached in `context/project_summary.md` rather than regenerated per call — this is the concrete mechanism behind the Section 11 context-pack requirement.

### 30.3 Planner System Prompt

Store as `prompts/planner.md` (or `.txt`), loaded at call time — never inline the prompt string in `executor.py`. Suggested content:

```
You are the PLANNER agent inside the oporch orchestration system.

ROLE BOUNDARIES (non-negotiable):
- You cannot write, edit, or delete any file.
- You cannot execute shell or test commands.
- Your entire output is a single JSON object. No prose, no markdown fences,
  no commentary before or after the JSON.

INPUT YOU WILL RECEIVE:
- milestone_id and milestone objective
- relevant PRD section excerpts
- a repository summary (structure, key files, existing conventions)
- architecture constraints
- prior decision-ledger entries relevant to this milestone
- short summaries of prior completed milestones (if any)

YOUR TASK:
1. Decompose the milestone objective into atomic, independently
   testable work units.
2. For each work unit, provide: id, title, objective, dependencies
   (by work unit id), assigned_role (one of: architect, builder,
   reviewer, tester, debugger, researcher, benchmark_analyst),
   acceptance_criteria (concrete, checkable conditions — not vague
   goals), files_likely_affected, tests_required.
3. Order and structure dependencies so the graph is acyclic. Do not
   create a dependency cycle.
4. If you had to make an assumption because the PRD/context did not
   fully specify something, record it in "assumptions" — do not
   silently invent product requirements.
5. If the milestone objective is genuinely ambiguous in a way that
   would change product behavior, do not guess. Emit a QUESTION object
   instead of a plan.

OUTPUT — return exactly one of the following two shapes:

PLAN SHAPE:
{
  "type": "PLAN",
  "milestone_id": "...",
  "objective": "...",
  "work_units": [
    {
      "id": "WU-001",
      "title": "...",
      "objective": "...",
      "dependencies": [],
      "assigned_role": "builder",
      "acceptance_criteria": ["..."],
      "files_likely_affected": ["..."],
      "tests_required": ["..."]
    }
  ],
  "assumptions": ["..."]
}

QUESTION SHAPE:
{
  "type": "QUESTION",
  "question_id": "QST-001",
  "question": "...",
  "why_needed": "...",
  "blocking": true,
  "options": [],
  "evidence_checked": ["PRD section reviewed", "prior decisions reviewed"]
}

RULES:
- Do not implement anything — you produce a plan or a question, nothing else.
- Do not reference or plan for any milestone beyond the one requested.
- Do not create work units for infrastructure the milestone doesn't need.
- Keep each work unit small enough that a Builder could plausibly finish
  it in one focused pass.
- Return nothing except the single JSON object.
```

### 30.4 Output Validation-with-Repair Pipeline (build once, reuse everywhere)

This is the concrete implementation of PRD Section 26's rule ("preserve raw → bounded repair → validate → retry once → mark failed"). Build it as a standalone function so Reviewer/Tester/Debugger reuse it unchanged in later milestones:

```python
def validate_agent_output(
    raw_text: str,
    schema: type[BaseModel],
    repair_fn: Callable[[str], str] | None = None,
) -> AgentOutputResult:
    """
    1. Store raw_text verbatim regardless of outcome.
    2. Try schema.model_validate_json(raw_text) directly.
    3. If it fails, run one bounded repair pass (e.g. strip markdown
       fences, extract the first {...} block) via repair_fn.
    4. Re-validate. If it fails again, return a FAILED result carrying
       the raw text and the validation error — never fabricate fields
       to force a pass.
    """
```

`AgentOutputResult` should carry: `status: Literal["valid","repaired","failed"]`, `raw_text`, `parsed: BaseModel | None`, `error: str | None`. Log which status occurred to `events.jsonl` (a `repaired` result is worth knowing about even when it eventually succeeds — it signals prompt drift).

### 30.5 CLI Flow — `oporch plan <milestone>`

```
$ oporch plan M1
→ state: IDLE → ANALYZING → PLANNING
→ builds PlannerContextPack
→ calls Planner (real OpenCodeAgentExecutor)
→ validates output via validate_agent_output()
  - if QUESTION: print it, do not persist a plan, stay in PLANNING, exit non-zero
  - if PLAN: persist runs/<run_id>/plan.json, print work-unit summary table
→ state: PLANNING → AWAITING_PLAN_APPROVAL
→ prompts (SUPERVISED/STRICT modes): "Approve this plan? [y/N/edit]"
  - y  → state: AWAITING_PLAN_APPROVAL → EXECUTING (next: oporch run M1)
  - N  → plan discarded, state back to IDLE, reason logged to decision ledger
  - edit → not required for M1; acceptable to say "not yet supported, re-run oporch plan with adjusted objective"
```

In `AUTONOMOUS` mode, auto-approve and log the auto-approval as a decision-ledger entry (still write the entry — silence is not allowed even when no human was asked).

### 30.6 M1 Acceptance Criteria

1. `oporch plan <milestone>` makes one real Planner call and produces a schema-valid `PlanResult` or a `QUESTION`.
2. Planner prompt lives in a file, not inline in code.
3. `validate_agent_output()` exists as a standalone, schema-agnostic function with unit tests covering all three outcomes (valid / repaired / failed) using canned fake LLM strings — no real model call required for these tests.
4. A Planner-produced cyclic dependency is caught by the existing M0 cycle detector (reuse, don't reimplement).
5. Plan approval flow works in at least `SUPERVISED` mode; auto-approval in `AUTONOMOUS` mode still writes a decision-ledger entry.
6. No Builder/Reviewer/Tester/Debugger real dispatch exists yet — `FakeExecutor` still backs those roles.
7. Repo summary is generated once and cached, not regenerated per Planner call.
