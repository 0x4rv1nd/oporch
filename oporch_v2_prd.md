# oporch v2 — Dynamic Multi-Agent Orchestration PRD

## 0. What changes vs current M0

| Area | oporch v1 (today) | oporch v2 (this PRD) |
|---|---|---|
| Roles | Fixed enum: Planner→Builder→Reviewer→Tester→Debugger | **Dynamic team roster** — Planner proposes N roles (5–6) per plan |
| Input | Single `--objective` string | **15-phase implementation plan doc** (md/PRD) as primary input |
| Execution | Sequential, one WU at a time (run/resume still `🔜`) | **Parallel dispatch**, bounded concurrency per agent, real asyncio executor over `opencode` CLI |
| State | JSON files + JSONL logs (`run_state.py`, `event_log.py`, `decision_ledger.py`) | **SQLite** (`.opencode-orchestrator/oporch.db`), WAL mode, JSON files deprecated |
| Memory | None — each run is a fresh planner prompt | **Persistent agent memory table**, queryable across runs (facts, decisions, gotchas per role/project) |
| Visibility | `oporch status` static print | **`oporch view`** — live Textual TUI, tree of agents × work units, refreshes off DB |
| Team sizing | N/A | Planner outputs a roster sized to the plan's phase/domain spread, no hard ceiling — scales up with phase count (see §7) |

Everything below assumes your existing modules (`work_unit.py` DAG, `state_machine.py`, `validate.py`, `doctor.py`) are **kept** — they're solid and don't change shape. We're replacing the storage layer, adding a team-composition stage before planning, adding a real parallel dispatcher, and adding a dashboard.

---

## 1. New Flow

```
oporch plan <plan.md>            # 15-phase doc in, not just --objective
     │
     ▼
┌─────────────────────┐
│  ANALYZING           │  scan repo + parse plan.md into phases
└─────────┬────────────┘
          ▼
┌─────────────────────┐
│  COMPOSING_TEAM (new)│  Planner-agent proposes 5–8 roles based on
│                       │  the domains present in the 15 phases
│                       │  (e.g. backend, frontend, db-migration,
│                       │  infra/devops, qa, docs) + model per role
└─────────┬────────────┘
          ▼  user approves roster (or edits via CLI prompt)
┌─────────────────────┐
│  PLANNING             │  Planner emits WorkUnitGraph, each WU tagged
│                       │  with an `assigned_role` from the approved
│                       │  roster (not the old fixed enum)
└─────────┬────────────┘
          ▼  user approves plan (existing AWAITING_PLAN_APPROVAL step, unchanged)
┌─────────────────────┐
│  EXECUTING (parallel) │  Dispatcher pulls all WUs with status=READY,
│                       │  groups by assigned_role, spawns one opencode
│                       │  subprocess per WU up to role.max_workers,
│                       │  runs them concurrently via asyncio.gather
└─────────┬────────────┘
          ▼
   REVIEWING → TESTING → VALIDATING → COMPLETED   (existing state machine, unchanged)
```

`oporch view` runs alongside `oporch run` in a second terminal, reading the same SQLite db — no coupling to the running process.

---

## 2. Team Composer (new module: `team_composer.py`)

Replaces the fixed `AgentRole` enum-only model with a **dynamic roster** resolved at plan time.

```python
class TeamRole(BaseModel):
    key: str                    # "backend", "db_migration", "frontend", etc — free-form slug
    description: str
    model: str                  # logical model key, resolved via existing config.resolve_model()
    fallback: str | None
    max_workers: int = 2
    domains: list[str]          # keywords used to route WUs to this role, e.g. ["api","auth","fastapi"]

class TeamRoster(BaseModel):
    run_id: str
    roles: list[TeamRole]       # scales with phase count, no hard ceiling (see §7)
    rationale: str              # planner's explanation, shown to user for approval
```

`compose_team(plan_phases: list[Phase], repo_summary: str) -> TeamRoster` calls the Planner agent with a new prompt (`prompts/team_composer.md`) that:
- reads the parsed 15-phase plan
- clusters phases by technical domain (backend/API, frontend/UI, data/db, infra/CI, testing, docs — collapse if the plan is smaller/narrower than that)
- always keeps `reviewer` and `tester` as thin cross-cutting roles regardless of domain clustering (quality gate agents, not domain agents)
- sizes the roster to the plan's phase count (see §7 sizing bands — roughly 3–4 agents for short plans up to 9+ for large ones), then only splits further within that budget if the plan clearly spans that many distinct domains (e.g. mobile + web + backend + infra + db + docs)

CLI:
```bash
oporch plan phase15.md              # parses phases, runs analyzing→composing_team→planning
oporch team show                    # print current roster
oporch team edit                    # interactive: rename/merge/split roles before approval
```

`WorkUnit.assigned_role` becomes a plain string (roster key) instead of `AgentRole` enum — `constants.AgentRole` is kept only for the fixed cross-cutting roles (`orchestrator`, `planner`, `reviewer`, `tester`, `debugger`).

---

## 3. Parallel Dispatcher (rewrite `runner.py`)

Current `runner.py` (479 lines) already has the retry/escalation logic (`attempt_2_receives_review`, `attempt_3_uses_debugger` from `policies.yaml`) — keep that. What's missing is concurrency.

```python
class ParallelDispatcher:
    def __init__(self, roster: TeamRoster, executor: AgentExecutor, db: OporchDB):
        self.semaphores = {role.key: asyncio.Semaphore(role.max_workers) for role in roster.roles}

    async def run_ready_wave(self, graph: WorkUnitGraph) -> list[AgentResult]:
        ready = graph.get_ready_work_units()          # already exists
        tasks = [self._run_one(wu) for wu in ready]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_one(self, wu: WorkUnit) -> AgentResult:
        async with self.semaphores[wu.assigned_role]:
            result = await self.executor.run_async(wu.assigned_role, wu, context)
            self.db.record_wu_result(wu.id, result)   # writes to SQLite, view picks it up live
            return result
```

Main loop: `while graph.has_pending(): await dispatcher.run_ready_wave(graph); graph.mark_completed(...)` — repeats waves until DAG drains, same topo/cycle logic as today, just batched instead of single-WU.

`OpenCodeAgentExecutor` needs an `run_async` wrapper around the existing subprocess call (`asyncio.create_subprocess_exec` instead of `subprocess.run`) — mechanical change, same prompt-building code.

---

## 4. SQLite Memory Layer (new module: `db.py`, replaces `run_state.py` + `event_log.py` + `decision_ledger.py` storage, keeps their public APIs)

```sql
-- .opencode-orchestrator/oporch.db

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    milestone_id TEXT,
    plan_source_path TEXT,
    state TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE roster (
    run_id TEXT, role_key TEXT, description TEXT, model TEXT,
    fallback TEXT, max_workers INTEGER, domains TEXT,  -- json
    PRIMARY KEY (run_id, role_key)
);

CREATE TABLE work_units (
    id TEXT PRIMARY KEY, run_id TEXT, phase INTEGER, title TEXT,
    assigned_role TEXT, status TEXT, depends_on TEXT,  -- json list
    attempt INTEGER DEFAULT 0, started_at TEXT, finished_at TEXT,
    result_summary TEXT, evidence TEXT                  -- json
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts TEXT,
    event_type TEXT, role TEXT, wu_id TEXT, payload TEXT  -- json
);

CREATE TABLE decisions (                                 -- was decision_ledger.jsonl
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts TEXT,
    question TEXT, answer TEXT, asked_by_role TEXT
);

-- THE NEW PIECE: durable cross-run memory
CREATE TABLE agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT,          -- scoped per-repo, not just per-run
    role_key TEXT,               -- which agent learned this
    memory_type TEXT,            -- 'fact' | 'gotcha' | 'convention' | 'failure_pattern'
    content TEXT,
    source_run_id TEXT,
    created_at TEXT,
    relevance_score REAL DEFAULT 1.0   -- decays / boosted on reuse, simple heuristic not embeddings
);
CREATE INDEX idx_memory_project_role ON agent_memory(project_path, role_key);
```

- Use plain SQLite + `sqlite3` stdlib, WAL mode (`PRAGMA journal_mode=WAL`) so `oporch view` can read concurrently while `oporch run` writes.
- No vector DB needed at this scale — `agent_memory` is retrieved by `(project_path, role_key)` + simple keyword match against `content`, injected into the Builder's context pack before each WU (extends existing `context_builder.py`). If you later want semantic recall, this is the seam where you'd swap in your AgentBrain/LanceDB setup instead of reinventing it here — same schema, different retrieval function.
- Migration: `oporch migrate-db` one-off command reads old `runs/*/state.json`, `events.jsonl`, `decisions.jsonl` and backfills into `oporch.db`, then those files are just archived, not deleted.

CLI additions:
```bash
oporch memory list --role builder                # show what builder has learned on this project
oporch memory add --role builder "always run migrations before tests"
oporch memory forget <id>
```

---

## 5. Live View (new module: `dashboard.py`, Textual)

`oporch view` — a read-only TUI polling `oporch.db` every ~500ms (SQLite handles concurrent readers fine in WAL mode, no IPC needed).

Layout:
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

- One column per roster role, live WU status (▶ running / ✓ done / ✗ failed / ⏸ queued), attempt count.
- Bottom pane tails the `events` table.
- Keybinds: `q` quit, `d` drill into a WU's full agent output, `p` pause dispatcher (writes a `pause` row the running dispatcher polls for — simple cooperative pause, no signals needed).
- This is purely a viewer process — safe to open/close anytime without affecting the run.

Library: `textual` (pure Python, matches your existing Rich usage in `cli.py` — Textual is built on Rich so it's a small delta, not a new paradigm).

---

## 6. Implementation Phases (for your own agents to execute)

1. **DB layer** — `db.py` with schema above + migration script + adapt `run_state.py`/`event_log.py`/`decision_ledger.py` internals to write through SQLite while keeping their existing function signatures (minimizes blast radius on `orchestrator.py`/`cli.py` callers).
2. **Team composer** — `team_composer.py`, `prompts/team_composer.md`, new `TeamRole`/`TeamRoster` models, new `COMPOSING_TEAM` state inserted into `state_machine.py` transition table between `ANALYZING` and `PLANNING`.
3. **Plan-doc parser** — extend `context_builder.py` to parse an arbitrary N-phase markdown plan into `Phase` objects (`## Phase N: Title` headers + bullet acceptance criteria) instead of only accepting `--objective`.
4. **Parallel dispatcher** — `runner.py` rewrite to async waves + `executor.py` gets `run_async`.
5. **Git isolation + merge gate** — `git_manager.py` (worktree per WU), new `supervisor` role, `MERGE_CONFLICT` status, `require_supervisor_merge` policy flag. Must land alongside phase 4 — see §7.
6. **Dashboard** — `dashboard.py` Textual app + `oporch view` CLI command, including the merge-status column from §7.
7. **Memory retrieval wiring** — `context_builder.py` pulls top-K `agent_memory` rows into each WU's context pack; Builder/Reviewer/Debugger prompts get a `## Known project memory` section. Structured event schema (§9) lands here too, since it extends the same `db.py` work.
8. **Auto-scaling roster** (§8) — phase-boundary `RosterAdjustment` checks, `spawn_role`/`retire_role`/`resize`, roster timeline in the DB.
9. **Security hardening pass** (§10) — `redact.py` extension over persistent tables, `never_auto_merge_to` policy enforcement, restricted subprocess env/PATH. The cheap parts (redact extension, no-auto-merge-to-main policy) should actually land alongside phase 5, not wait — see §10 for which parts are cheap vs. deferrable.
10. **Docs/tests + observability CLI** — update README's roadmap table, add `tests/test_team_composer.py`, `tests/test_dispatcher.py`, `tests/test_db.py`, `tests/test_git_manager.py`, `tests/test_dashboard.py`, `tests/test_roster_scaling.py`, `tests/test_security.py`, plus the `oporch replay`/`oporch diff`/`oporch report --failures` commands from §9 (Textual has a `Pilot` test harness for headless TUI assertions).

Suggested milestone split matching this: **M1 = phases 1–3 (db + team composer + plan parser)**, **M2 = phases 4–5 (parallel execution + git worktree/merge gate — the actual concurrency-safety win)**, **M3 = phases 6–7 (view + memory/observability)**, **M4 = phases 8–10 (auto-scaling, security hardening, tooling polish)**.

---

## 7. Git Workflow — per-agent branches + supervisor merge

Right now every WU is implemented directly against the working tree — fine for one agent, unsafe once 5–9 agents are editing in parallel (file clobbering, half-finished diffs blocking each other). Standard practice for multi-agent swarms is **isolate, then gate the merge**:

- **One branch (or worktree) per work unit, not per agent.** An agent role like `backend` will touch many WUs over a run, so branching per-role would serialize its own work; branching per-WU lets independent WUs from the same role run truly in parallel. Branch name convention: `oporch/<run_id>/<wu_id>-<slug>`.
- **`git worktree add` instead of `git checkout`** — each in-flight WU gets its own worktree directory under `.opencode-orchestrator/worktrees/<wu_id>/`, so concurrent agents never share a working directory or index lock. This is the actual concurrency-safety mechanism; the asyncio dispatcher in §3 already runs agents concurrently, but without worktrees they'd all be racing on the same `.git/index`.
- **New `git_manager.py` module**: `create_worktree(wu_id, base_branch)`, `commit_wu_result(wu_id, message)`, `diff_for_review(wu_id)`, `cleanup_worktree(wu_id)`.
- **Supervisor merge gate (new role: `supervisor`, distinct from `reviewer`)**: the `reviewer` role does adversarial *code* review inside the WU's own branch (unchanged from today). The `supervisor` is a thin cross-cutting role — like `reviewer`/`tester` — that runs *after* a WU passes review+tests, and is the only actor allowed to merge into the milestone integration branch (`oporch/<run_id>/integration`). It:
  1. re-diffs the WU branch against current `integration` (catches conflicts introduced by sibling WUs merged since this one started)
  2. re-runs the acceptance criteria check from `models.py`'s existing `AgentResult.evidence` against the merged state, not just the isolated branch
  3. on clean merge: fast-forwards/squash-merges into `integration`, marks WU `COMPLETED` in the DB, deletes the worktree
  4. on conflict: flags the WU `status=MERGE_CONFLICT` (new `WorkUnitStatus` value), and either auto-routes to the `debugger` role for a conflict-resolution attempt, or escalates to `AWAITING_USER_INPUT` per `policies.yaml`'s existing escalation settings — your call which is default
- **`completion_gate` in `policies.yaml` gains a `require_supervisor_merge: true` flag** alongside the existing `require_review_approval`/`require_tests_pass`, so a WU can't reach `COMPLETED` without passing through the merge gate.
- At the end of a run, `integration` is what gets merged into your actual main/develop branch — one clean PR-sized diff, not 9 concurrent agents pushing straight to a shared branch.
- Dashboard tie-in (§5): add a `merged/pending-merge/conflict` indicator per WU column so you can see merge-gate backlog live, not just build/review/test status.

This slots into the phase plan in §6 as a new **phase 4.5** (between "parallel dispatcher" and "dashboard") since the dispatcher and worktree isolation need to land together — running agents in parallel without worktree isolation first would just move the race condition from "queue" to "filesystem."

## 8. Auto-Scaling Roster Mid-Run

Today the roster is fixed at `COMPOSING_TEAM` and stays static for the whole run. In practice, phase 1–3 of a 15-phase plan might be all-backend (needs 2 `backend` workers, 0 `frontend`), while phase 10–13 is all-UI. A static roster either over-provisions idle agents early or under-provisions later.

- **New `RosterAdjustment` event**: after each completed phase (not each WU — phase-level, so this doesn't thrash), the orchestrator re-runs a lightweight version of the team-composer prompt — not full re-planning — that only asks "given roles X are idle/starved and phases N+1..N+3 need domains Y, should the roster change?" Cheaper model than the main Planner (route it through the `benchmark_analyst` model tier, already in your `roles.yaml`, since it's a quick analytical call not a build).
- **Actions available**: `spawn_role(key, based_on_domain)`, `retire_role(key)` (only if its work-unit queue is empty — never kill a role mid-WU), `resize(key, new_max_workers)` (cheaper than spawn/retire — just widen/narrow the semaphore in §3's dispatcher for a role that's the right domain but under/over-subscribed).
- **Guardrails**: max roster size still bounded by the phase-count sizing bands from §7 (old §7, now folded into §2/§9 numbering) — auto-scaling redistributes within that budget by default; growing past it requires the same user-approval step as the initial roster did. Minimum 1 role always alive (never fully drain to zero agents while WUs remain).
- **DB**: `roster` table (§4) gets an `active_from`/`active_until` pair instead of being a static one-row-per-role table, so `oporch view` can show roster changes over time, not just a fixed column set.
- **Dashboard tie-in**: when a role is retired, its TUI column collapses; when spawned, a new column animates in. Emits a `roster_adjusted` event into the `events` table so it's visible in the event tail pane too.
- New CLI: `oporch team history` — shows the roster timeline for a run (which roles were active during which phases, useful for tuning the sizing heuristic over time).

This is phase 8 in the implementation order (§6) — depends on the dispatcher (§3) and roster/db (§2, §4) already existing, and should land after the core parallel pipeline is stable, since it adds churn on top of a system you'll want to have proven first.

## 9. Observability

Right now `events.jsonl`/`events` table (§4) is an append-only log with no structure beyond `event_type`, and there's no way to replay or diff a past run. For a 5–9 agent swarm this matters a lot more than for the current single-threaded pipeline — when something goes wrong at 2am, you need to reconstruct *what each agent was doing, in what order, against what context* without re-running the whole thing.

- **Structured event schema** — every event row gets a consistent envelope: `{run_id, wu_id, role, ts, event_type, level (debug/info/warn/error), payload, duration_ms, model_used, tokens_in, tokens_out}`. Currently `payload` is a loose JSON blob; standardizing the numeric fields (duration, tokens) makes them queryable/aggregable directly in SQL instead of parsing JSON per row.
- **`oporch replay <run_id>`** — new CLI command that reads a completed (or failed) run's full event stream from the db and prints it back chronologically, interleaved across agents but clearly labeled per-role/per-WU — effectively a "what actually happened" narrative reconstruction, without re-invoking any agent. Useful both for debugging and for writing your own postmortems.
- **`oporch replay <run_id> --wu WU-041`** — scoped replay: every event, prompt, and diff touching one work unit, in order, including its attempt history (build→review-reject→rebuild→review-pass) — this is the "why did this WU take 3 attempts" view.
- **Failure analytics** — new `failure_patterns` table (or fold into `agent_memory` with `memory_type='failure_pattern'`, §4) auto-populated when a WU fails or hits `MERGE_CONFLICT`: `{role, failure_category, wu_domain, resolution}`. `oporch report --failures` aggregates across all historical runs for a project — "reviewer rejects builder's auth-related WUs 40% of the time on first attempt" is the kind of signal this surfaces, and it's exactly the kind of thing that should also get written into `agent_memory` so future Builder prompts get a heads-up.
- **Run diffing** — `oporch diff <run_id_a> <run_id_b>` compares two runs of similar plans (e.g. before/after a prompt tweak) on duration, attempt counts, and failure rate per role — lets you actually measure whether a change to `roles.yaml` or a prompt file helped.
- **Cost/duration rollup** — even without full budget enforcement (that's the item you didn't pick), just *tracking* `tokens_in/out` and wall-clock duration per WU/role per run costs nothing extra to record now (it's already in the executor's subprocess output) and pays off later if you do want budgets — captured in the structured event schema above so you're not retrofitting it.
- **Dashboard tie-in**: a `d` keybind (already planned in §5) drills into a WU's full output — extend that view to show the structured event trail for that WU, not just its final result.

This is largely additive to §4's schema (wider event columns, one new table) rather than a new subsystem — folds into implementation phase 7 in §6 alongside the DB layer work, with `replay`/`diff`/`report --failures` as CLI additions in phase 8 (docs/tests) since they're read-only tooling over data the other phases already produce.

## 10. Security

The current design has agents running arbitrary `opencode` subprocesses against your actual working tree, with no isolation, no secrets boundary, and (per §7) merges going straight into an `integration` branch. At 5–9 concurrent agents, this is real attack surface — a bad or hallucinated agent action shouldn't be able to touch prod credentials, push to your real main branch, or leak secrets into a run log that might later get committed.

- **Secrets never enter agent context.** Audit `context_builder.py` (§2/§4 memory injection makes this more important, not less) to ensure `.env`, credential files, and anything matching common secret patterns (API keys, tokens, connection strings with passwords) are excluded from repo-summary scans and from `agent_memory` content before it's ever written to `agent_memory` or a prompt. Add a `redact.py`-style pass (you already have `redact.py` in the codebase — extend it to run over *every* payload written to the `events`/`agent_memory` tables, not just wherever it's currently called, since those are now persistent and queryable, not transient logs).
- **Sandboxed worktrees.** §7's per-WU worktrees are already filesystem-isolated from each other, but not isolated from your host — an agent subprocess still has full filesystem/network access from your machine by default. Two tiers depending on how far you want to go:
  - *Minimum*: run each `opencode` subprocess with a restricted `PATH`/env (strip anything not explicitly allowlisted — no ambient AWS/DB creds), and set the worktree's `.git/config` to disallow `push` (only the `supervisor` role's merge step, §7, ever touches `integration` — builder/reviewer/tester agents should structurally be unable to push anywhere, not just conventionally discouraged from it).
  - *Stronger*: run each worker inside a lightweight container/namespace (e.g. `bubblewrap` or a scoped Docker container mounting only that WU's worktree) so a compromised or misbehaving agent can't read siblings' worktrees, your `~/.ssh`, or other repos on the machine. Given your setup already relies on `opencode` CLI + subprocess dispatch, this is the highest-effort item in this whole PRD — reasonable to defer to a v3 unless you're running oporch against sensitive/production codebases.
- **PR-only merge to your real main/develop.** §7 already scopes the `supervisor` role's merges to a per-run `integration` branch, never `main` directly — worth stating explicitly as a hard rule: `integration` → your real base branch always goes through a human-opened PR, never an automated merge, regardless of how much you trust the supervisor role. This is a policy line in `policies.yaml` (`never_auto_merge_to: ["main", "develop"]`) enforced by `git_manager.py` refusing the operation outright, not just a convention.
- **Approval-mode interaction**: your existing `policies.yaml` already has `approval_mode: SUPERVISED | AUTONOMOUS | STRICT` — worth confirming (or tightening) that `STRICT` mode disables auto-merge entirely and requires a human "approve merge" step per WU, not just per-plan, since that's the mode you'd actually want turned on for anything touching prod-adjacent code.

This is phase 9 in §6 — mostly a hardening pass over §7 (git) and §2/§4 (context/memory), so it should land after those are implemented rather than blocking them, but the `redact.py` extension and the `never_auto_merge_to` policy line are cheap enough to do immediately alongside phase 5 (git isolation) rather than waiting.

## 11. Supervisor: Dynamic Model Selection, Self-Healing, Scoped File Access

Three related upgrades to the `supervisor` role (§7) — right now it's just a merge gate; this extends it into the thing actually deciding *which model* runs each task, *recovering* from failures without always escalating to you, and *fencing* what each agent can touch on disk.

### 11a. Dynamic model selection per task

Today `resolve_model()` (`config.py`) is a static lookup: role → logical model key → real model ID, fixed in `roles.yaml`. That's fine for "reviewer always uses nemotron-ultra," but wastes your premium model budget on trivial WUs and under-powers genuinely hard ones.

- **`supervisor.select_model(wu, attempt_history) -> model_key`** — before a WU is dispatched, the supervisor scores task complexity from signals already available in the WU/plan: acceptance-criteria count, files touched, whether it's a retry (bump model tier on attempt 2+, matching your existing `attempt_2_receives_review`/`attempt_3_uses_debugger` policy pattern), domain (e.g. `db_migration`/`auth` WUs get bumped a tier by default — higher blast radius if wrong), and any `failure_patterns` history (§9) for that role/domain combo.
- **`models.yaml` gains a `tier` field** (`fast` / `standard` / `heavy`) instead of role config hardcoding one model — `roles.yaml` now lists a *tier preference range* per role (e.g. builder: `fast..standard`, debugger: `standard..heavy`), and the supervisor picks the actual model within that range per-WU rather than per-role-globally.
- **Logged, not silent**: every model selection writes a `model_selected` event (§9's structured schema already has a `model_used` column) with the reasoning signals, so `oporch replay` shows *why* a given WU got a heavier model — this is what lets you tune the heuristic later instead of it being a black box.
- **Budget awareness**: ties into the cost tracking you deferred earlier (§9's tokens_in/out rollup) — supervisor can de-prioritize `heavy` tier once a run crosses a soft budget threshold in `policies.yaml` (`model_budget_soft_limit`), falling back toward `standard` for anything not already flagged high-risk.

### 11b. Self-healing

Currently a failed WU either retries per `policies.yaml`'s fixed 3-attempt ladder or escalates to `AWAITING_USER_INPUT`. Self-healing means the supervisor tries a *broader* set of recovery strategies before waking you up, informed by what's actually stored in `agent_memory`/`failure_patterns` (§9).

- **Recovery strategy ladder** (supervisor picks, not a fixed sequence): `retry_same_model` → `retry_with_model_bump` (11a) → `retry_with_narrower_scope` (split the WU into smaller sub-WUs if the DAG allows — new `graph.split_work_unit()` in `work_unit.py`) → `retry_with_debugger_prefix` (debugger diagnoses before builder re-attempts, not after) → `rollback_and_reassign` (discard the worktree, hand the WU to a *different* agent instance of the same role — rules out a bad run vs. a genuinely hard task) → **only then** `AWAITING_USER_INPUT`.
- **Self-healing is capped, not unlimited** — `policies.yaml` gets `self_heal_max_strategies` (default 3 strategies tried before forced escalation) so it can't silently loop forever burning tokens on a WU that genuinely needs a human call.
- **Health checks beyond the WU level**: supervisor periodically (once per phase boundary, same cadence as §8's roster check) verifies the *run itself* isn't degrading — e.g. rising failure rate across a role, a role stuck idle despite ready WUs (deadlock smell), worktree disk usage runaway — and can trigger `resize`/`retire_role` (§8) or flag `AWAITING_USER_INPUT` at the run level, not just per-WU.
- **Every self-heal attempt is a `failure_patterns` write** (§9) — so the system gets better at *avoiding* the failure next time, not just recovering from it this time.

### 11c. Scoped file/folder access per agent

Ties directly into §10's worktree isolation and the "restricted env/PATH" minimum tier — this is the concrete access-control layer on top of that.

- **`TeamRole` (§2) gains an `allowed_paths: list[str]` field** — glob patterns the role is permitted to read/write, set either explicitly in `roles.yaml` or inferred by the team composer from each role's `domains` (e.g. `frontend` role defaults to `src/frontend/**`, `*.tsx`, `*.css`; `db_migration` role defaults to `migrations/**`, `src/db/**`). A role never gets `allowed_paths: ["**"]` by default — cross-cutting roles (`reviewer`, `tester`, `supervisor`) are the explicit exception since they need to see everything to do their job.
- **Enforcement, not just declaration**: the supervisor validates each `AgentResult`'s file diff (already available from `git_manager.diff_for_review`, §7) against the WU's role's `allowed_paths` *before* accepting the result — a builder trying to edit `.github/workflows/*` or `.env.example` when its `allowed_paths` don't cover that gets bounced back as a policy violation, not silently merged. This catches both hallucinated scope creep and genuinely malicious/compromised agent behavior.
- **Worktree-level enforcement (belt and suspenders)**: where the OS/filesystem allows it, the worktree in §10's sandboxed tier is created with those paths as the *only* writable paths (everything else read-only or unmounted) — so it's not just a post-hoc diff check but a structural constraint, for whichever tier of §10 you end up running.
- **Violations feed §9's failure_patterns too** — a role repeatedly trying to touch out-of-scope paths is itself a signal (either the role's domain inference was too narrow, or something's actually wrong) — surfaced in `oporch report --failures`.
- **Dashboard tie-in**: `oporch team show`/`oporch team history` (§8) display each role's `allowed_paths` alongside its model tier, so the roster view doubles as an access-control audit view.

These three land as **phase 11** in §6, after the supervisor role already exists from phase 5 (§7's git/merge work) — 11a and 11c can be built in parallel since they're mostly independent config/enforcement additions; 11b (self-healing) should come last since it depends on the recovery strategies having somewhere real to route to (debugger, resize, split) that earlier phases establish.

## 12. Open questions before implementation starts

- ~~Roster cap: is 8 a hard ceiling, or should very large plans (20+ phases) be allowed more agents?~~ **Resolved: no hard ceiling — roster size scales with phase count.** Suggested bands: 1–6 phases → 3–4 agents, 7–12 phases → 5–6 agents, 13–20 phases → 7–9 agents, 20+ phases → keep adding roughly one agent per 3 extra phases, but cap `max_workers` per role rather than uncapping role count indefinitely (more roles than distinct domains just causes idle agents waiting on the DAG). `compose_team()` should take phase count as an explicit input to its sizing heuristic instead of a fixed 5–8 range.
- ~~Should `agent_memory` be committed to git (so a teammate cloning the repo inherits learned conventions) or stay local/gitignored per machine?~~ **Resolved: stays in SQLite, local and gitignored.** `oporch.db` (including `agent_memory`) is a binary file that merges/diffs terribly in git, so it should never be committed directly — add `.opencode-orchestrator/*.db*` to `.gitignore`. If you want memory to travel with the repo for teammates, add an `oporch memory export` / `oporch memory import` pair that dumps `agent_memory` rows to a plain JSONL file (`.opencode-orchestrator/memory_export.jsonl`) which *is* git-friendly and safe to commit; SQLite stays the source of truth locally, JSONL is just the portable snapshot.
- Pause/resume semantics for the parallel dispatcher — abort in-flight subprocesses/worktrees or let the current wave finish before pausing?
- Merge-conflict default: auto-route `MERGE_CONFLICT` WUs to `debugger` for a resolution attempt, or always escalate to `AWAITING_USER_INPUT`?
- Roster auto-scaling (§8): should adjustments require your approval every time (safer, more interruptions) or only when *growing* the roster (auto-approve shrink/resize, gate only spawn)?
- Security (§10): is the "minimum" tier (restricted env/PATH + push-disabled worktrees) sufficient for now, or do you want the containerized/sandboxed tier scoped into an earlier milestone given what QRPress/RankSarathi-scale codebases this might run against?
