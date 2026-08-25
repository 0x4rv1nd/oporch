"""SQLite storage layer for oporch v2.

Single database at ``.opencode-orchestrator/oporch.db`` (WAL mode) replacing
the JSON/JSONL storage of v1. Existing public APIs in ``run_state.py``,
``event_log.py`` and ``decision_ledger.py`` are preserved; their internals
write through to this database while mirroring to the legacy files.

Every text payload is passed through :func:`oporch.redact.redact_secrets`
before it touches persistent storage.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redact import redact_secrets

ORCHESTRATOR_DIR = Path(".opencode-orchestrator")
DB_PATH = ORCHESTRATOR_DIR / "oporch.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    milestone_id TEXT,
    plan_source_path TEXT,
    state TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS roster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    role_key TEXT NOT NULL,
    description TEXT,
    model TEXT,
    fallback TEXT,
    max_workers INTEGER DEFAULT 2,
    domains TEXT,
    allowed_paths TEXT,
    rationale TEXT,
    active_from TEXT,
    active_until TEXT
);
CREATE INDEX IF NOT EXISTS idx_roster_run ON roster(run_id);

CREATE TABLE IF NOT EXISTS work_units (
    -- NOTE: PRD §4 sketches `id TEXT PRIMARY KEY`, but planner-generated
    -- ids ("WU-001") repeat across runs; scope identity to the run.
    id TEXT NOT NULL,
    run_id TEXT,
    phase INTEGER,
    title TEXT,
    assigned_role TEXT,
    status TEXT,
    depends_on TEXT,
    attempt INTEGER DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    result_summary TEXT,
    evidence TEXT,
    seq INTEGER DEFAULT 0,
    data TEXT,
    PRIMARY KEY (run_id, id)
);
CREATE INDEX IF NOT EXISTS idx_wu_run ON work_units(run_id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ts TEXT,
    event_type TEXT,
    role TEXT,
    wu_id TEXT,
    level TEXT DEFAULT 'info',
    duration_ms REAL,
    model_used TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_wu ON events(run_id, wu_id);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ts TEXT,
    question TEXT,
    answer TEXT,
    asked_by_role TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions(run_id);

-- Durable cross-run agent memory (facts/gotchas/conventions/failure_patterns).
CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT,
    role_key TEXT,
    memory_type TEXT,
    content TEXT,
    source_run_id TEXT,
    created_at TEXT,
    relevance_score REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_memory_project_role
    ON agent_memory(project_path, role_key);

-- Cooperative control channel (e.g. dispatcher pause flag polled by runner).
CREATE TABLE IF NOT EXISTS control (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OporchDB:
    """Thin synchronous wrapper over the oporch SQLite database.

    A single connection guarded by a re-entrant lock; WAL journal mode lets
    other processes (e.g. ``oporch view``) read concurrently while this one
    writes.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    @staticmethod
    def _redact_payload(payload: Any) -> str:
        if payload is None:
            return "{}"
        if not isinstance(payload, str):
            payload = json.dumps(payload, default=str)
        return redact_secrets(payload)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # runs
    # ------------------------------------------------------------------
    def upsert_run(
        self,
        run_id: str,
        milestone_id: str | None = None,
        plan_source_path: str | None = None,
        state: str | None = None,
        created_at: str | None = None,
    ) -> None:
        now = utc_now_iso()
        self._execute(
            """
            INSERT INTO runs (id, milestone_id, plan_source_path, state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                milestone_id = COALESCE(excluded.milestone_id, milestone_id),
                plan_source_path = COALESCE(excluded.plan_source_path, plan_source_path),
                state = COALESCE(excluded.state, state),
                updated_at = excluded.updated_at
            """,
            (run_id, milestone_id, plan_source_path, state, created_at or now, now),
        )

    def set_run_state(self, run_id: str, state: str) -> None:
        self._execute(
            "UPDATE runs SET state = ?, updated_at = ? WHERE id = ?",
            (state, utc_now_iso(), run_id),
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM runs WHERE id = ?", (run_id,))
        return dict(rows[0]) if rows else None

    def list_runs(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM runs ORDER BY created_at")]

    # ------------------------------------------------------------------
    # roster
    # ------------------------------------------------------------------
    def save_roster(
        self,
        run_id: str,
        roles: list[dict[str, Any]],
        rationale: str = "",
    ) -> None:
        """Insert roster rows. Each dict: role_key, description, model,
        fallback, max_workers, domains(list), allowed_paths(list|None)."""
        now = utc_now_iso()
        for r in roles:
            self._execute(
                """
                INSERT INTO roster (run_id, role_key, description, model, fallback,
                                    max_workers, domains, allowed_paths, rationale,
                                    active_from, active_until)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    run_id,
                    r["role_key"],
                    r.get("description", ""),
                    r.get("model"),
                    r.get("fallback"),
                    int(r.get("max_workers", 2)),
                    self._redact_payload(r.get("domains") or []),
                    self._redact_payload(r.get("allowed_paths")) if r.get("allowed_paths") is not None else None,
                    rationale,
                    now,
                ),
            )

    def get_roster(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM roster WHERE run_id = ? AND active_until IS NULL",
            (run_id,),
        )
        out = []
        for row in rows:
            d = dict(row)
            d["domains"] = json.loads(d["domains"]) if d["domains"] else []
            d["allowed_paths"] = json.loads(d["allowed_paths"]) if d["allowed_paths"] else None
            out.append(d)
        return out

    def get_roster_history(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM roster WHERE run_id = ? ORDER BY active_from",
            (run_id,),
        )
        out = []
        for row in rows:
            d = dict(row)
            d["domains"] = json.loads(d["domains"]) if d["domains"] else []
            out.append(d)
        return out

    def retire_role(self, run_id: str, role_key: str) -> bool:
        cur = self._execute(
            "UPDATE roster SET active_until = ? WHERE run_id = ? AND role_key = ? "
            "AND active_until IS NULL",
            (utc_now_iso(), run_id, role_key),
        )
        return cur.rowcount > 0

    def resize_role(self, run_id: str, role_key: str, new_max_workers: int) -> bool:
        cur = self._execute(
            "UPDATE roster SET max_workers = ? WHERE run_id = ? AND role_key = ? "
            "AND active_until IS NULL",
            (int(new_max_workers), run_id, role_key),
        )
        return cur.rowcount > 0

    def respawn_role(self, run_id: str, role_key: str, max_workers: int) -> bool:
        """Re-open an earlier-retired role (keeps original config)."""
        rows = self._query(
            "SELECT * FROM roster WHERE run_id = ? AND role_key = ? "
            "ORDER BY active_from DESC LIMIT 1",
            (run_id, role_key),
        )
        if not rows:
            return False
        src = dict(rows[0])
        self.save_roster(
            run_id,
            [
                {
                    "role_key": src["role_key"],
                    "description": src["description"],
                    "model": src["model"],
                    "fallback": src["fallback"],
                    "max_workers": max_workers,
                    "domains": json.loads(src["domains"]) if src["domains"] else [],
                }
            ],
            rationale=src.get("rationale") or "",
        )
        return True

    # ------------------------------------------------------------------
    # work units
    # ------------------------------------------------------------------
    @staticmethod
    def _role_str(role: Any) -> str:
        if hasattr(role, "value"):
            return str(role.value)
        return str(role) if role is not None else ""

    def save_work_units(self, run_id: str, units: list[Any]) -> None:
        """Persist work unit models (pydantic WorkUnit or dicts)."""
        for seq, u in enumerate(units):
            if isinstance(u, dict):
                d = u
                deps = d.get("dependencies") or []
                evidence = d.get("evidence")
            else:
                d = u.model_dump(mode="json")
                deps = d.get("dependencies") or []
                evidence = d.get("evidence")
            self._execute(
                """
                INSERT INTO work_units (id, run_id, phase, title, assigned_role, status,
                                        depends_on, attempt, started_at, finished_at,
                                        result_summary, evidence, seq, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, id) DO UPDATE SET
                    phase = excluded.phase,
                    title = excluded.title,
                    assigned_role = excluded.assigned_role,
                    status = excluded.status,
                    depends_on = excluded.depends_on,
                    attempt = excluded.attempt,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    result_summary = excluded.result_summary,
                    evidence = excluded.evidence,
                    seq = excluded.seq,
                    data = COALESCE(excluded.data, data)
                """,
                (
                    d["id"],
                    run_id,
                    d.get("phase"),
                    d.get("title", ""),
                    self._role_str(d.get("assigned_role")),
                    self._role_str(d.get("status")) or "PENDING",
                    json.dumps(deps),
                    int(d.get("attempts") or d.get("attempt") or 0),
                    d.get("started_at"),
                    d.get("finished_at"),
                    d.get("result_summary"),
                    self._redact_payload(evidence) if evidence is not None else None,
                    seq,
                    redact_secrets(json.dumps(d, default=str)),
                ),
            )

    def load_work_unit_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM work_units WHERE run_id = ? ORDER BY seq, id",
            (run_id,),
        )
        out = []
        for row in rows:
            d = dict(row)
            d["depends_on"] = json.loads(d["depends_on"]) if d["depends_on"] else []
            out.append(d)
        return out

    def record_wu_result(
        self,
        wu_id: str,
        *,
        run_id: str | None = None,
        status: str | None = None,
        attempt: int | None = None,
        result_summary: str | None = None,
        evidence: Any | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        sets = ["result_summary = COALESCE(?, result_summary)"]
        params: list[Any] = [
            redact_secrets(result_summary) if result_summary is not None else None,
        ]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if attempt is not None:
            sets.append("attempt = ?")
            params.append(int(attempt))
        if evidence is not None:
            sets.append("evidence = ?")
            params.append(self._redact_payload(evidence))
        if started:
            sets.append("started_at = COALESCE(started_at, ?)")
            params.append(utc_now_iso())
        if finished:
            sets.append("finished_at = ?")
            params.append(utc_now_iso())
        sql = f"UPDATE work_units SET {', '.join(sets)} WHERE id = ?"
        params.append(wu_id)
        if run_id is not None:
            sql += " AND (run_id = ? OR run_id IS NULL)"
            params.append(run_id)
        cur = self._execute(sql, tuple(params))
        if cur.rowcount == 0 and run_id is not None:
            self._execute(
                "INSERT INTO work_units (id, run_id, status, title) VALUES (?, ?, ?, ?)",
                (wu_id, run_id, status or "PENDING", ""),
            )

    def count_wu_by_status(self, run_id: str) -> dict[str, int]:
        rows = self._query(
            "SELECT status, COUNT(*) AS n FROM work_units WHERE run_id = ? GROUP BY status",
            (run_id,),
        )
        return {r["status"]: r["n"] for r in rows}

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def append_event(
        self,
        run_id: str,
        event_type: str,
        role: str | None = None,
        wu_id: str | None = None,
        payload: Any | None = None,
        level: str = "info",
        duration_ms: float | None = None,
        model_used: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        ts: str | None = None,
    ) -> int:
        cur = self._execute(
            """
            INSERT INTO events (run_id, ts, event_type, role, wu_id, level,
                                duration_ms, model_used, tokens_in, tokens_out, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                ts or utc_now_iso(),
                self._role_str(event_type),
                self._role_str(role) if role else None,
                wu_id,
                level,
                duration_ms,
                model_used,
                tokens_in,
                tokens_out,
                self._redact_payload(payload),
            ),
        )
        return cur.lastrowid

    def tail_events(self, run_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM events WHERE run_id = ? ORDER BY id DESC LIMIT ?",
            (run_id, limit),
        )
        return [dict(r) for r in reversed(rows)]

    def events_for_wu(self, run_id: str, wu_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM events WHERE run_id = ? AND wu_id = ? ORDER BY id",
            (run_id, wu_id),
        )
        return [dict(r) for r in rows]

    def all_events(self, run_id: str) -> list[dict[str, Any]]:
        return self.tail_events(run_id, limit=10_000_000)

    # ------------------------------------------------------------------
    # decisions
    # ------------------------------------------------------------------
    def append_decision(
        self,
        run_id: str,
        question: str,
        answer: str,
        asked_by_role: str | None = None,
        ts: str | None = None,
    ) -> int:
        cur = self._execute(
            "INSERT INTO decisions (run_id, ts, question, answer, asked_by_role)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                run_id,
                ts or utc_now_iso(),
                redact_secrets(question),
                redact_secrets(answer),
                asked_by_role,
            ),
        )
        return cur.lastrowid

    def search_decisions(self, query: str) -> list[dict[str, Any]]:
        q = f"%{query.lower()}%"
        rows = self._query(
            "SELECT * FROM decisions WHERE LOWER(question) LIKE ? OR LOWER(answer) LIKE ?"
            " ORDER BY id",
            (q, q),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # agent memory
    # ------------------------------------------------------------------
    def remember(
        self,
        project_path: str,
        role_key: str,
        memory_type: str,
        content: str,
        source_run_id: str | None = None,
    ) -> int:
        cur = self._execute(
            "INSERT INTO agent_memory (project_path, role_key, memory_type, content,"
            " source_run_id, created_at, relevance_score) VALUES (?, ?, ?, ?, ?, ?, 1.0)",
            (
                project_path,
                role_key,
                memory_type,
                redact_secrets(content),
                source_run_id,
                utc_now_iso(),
            ),
        )
        return cur.lastrowid

    def forget(self, memory_id: int) -> bool:
        cur = self._execute("DELETE FROM agent_memory WHERE id = ?", (int(memory_id),))
        return cur.rowcount > 0

    def recall(
        self,
        project_path: str,
        role_key: str | None = None,
        query: str | None = None,
        limit: int = 5,
        min_relevance: float = 0.0,
    ) -> list[dict[str, Any]]:
        sql = ("SELECT * FROM agent_memory WHERE project_path = ?"
               " AND relevance_score >= ?")
        params: list[Any] = [project_path, min_relevance]
        if role_key is not None:
            sql += " AND (role_key = ? OR role_key IN ('reviewer','tester','supervisor'))"
            params.append(role_key)
        keywords: list[str] = []
        if query:
            keywords = [w for w in query.lower().split() if len(w) > 2]
        rows = self._query(sql + " ORDER BY relevance_score DESC, id DESC", params)
        items = [dict(r) for r in rows]
        if keywords:
            def score(item: dict[str, Any]) -> int:
                content = item["content"].lower()
                return sum(1 for k in keywords if k in content)

            items = [i for i in items if score(i) > 0]
            items.sort(key=lambda i: (-score(i), -i["relevance_score"], -i["id"]))
        return items[:limit]

    def boost_memory(self, memory_ids: list[int], amount: float = 0.1) -> None:
        if not memory_ids:
            return
        marks = ",".join("?" for _ in memory_ids)
        self._execute(
            f"UPDATE agent_memory SET relevance_score = MIN(relevance_score + ?, 10.0)"
            f" WHERE id IN ({marks})",
            [amount, *memory_ids],
        )

    def decay_project_memory(self, project_path: str, factor: float = 0.99) -> None:
        self._execute(
            "UPDATE agent_memory SET relevance_score = relevance_score * ?"
            " WHERE project_path = ?",
            (factor, project_path),
        )

    def export_memory(self, out_path: Path | str) -> int:
        rows = self._query("SELECT * FROM agent_memory ORDER BY id")
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(dict(r), default=str) for r in rows]
        p.write_text("\n".join(lines), encoding="utf-8")
        return len(lines)

    def import_memory(self, in_path: Path | str, project_path: str | None = None) -> int:
        p = Path(in_path)
        if not p.exists():
            return 0
        count = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            self.remember(
                project_path or d.get("project_path", ""),
                d.get("role_key", ""),
                d.get("memory_type", "fact"),
                d.get("content", ""),
                d.get("source_run_id"),
            )
            count += 1
        return count

    # ------------------------------------------------------------------
    # control channel
    # ------------------------------------------------------------------
    def set_control(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO control (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def get_control(self, key: str) -> str | None:
        rows = self._query("SELECT value FROM control WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None


def migrate_legacy_files(db: OporchDB | None = None) -> dict[str, int]:
    """One-off backfill of legacy runs/*/ JSON + JSONL files into SQLite.

    Legacy files are archived in place (kept, never deleted). Returns counts.
    """
    own_db = db is None
    db = db or OporchDB()
    counts = {"runs": 0, "work_units": 0, "events": 0, "decisions": 0}
    try:
        runs_dir = ORCHESTRATOR_DIR / "runs"
        if runs_dir.exists():
            for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
                run_id = run_dir.name
                rs_path = run_dir / "run_state.json"
                if rs_path.exists():
                    data = json.loads(rs_path.read_text(encoding="utf-8"))
                    if db.get_run(run_id) is None:
                        db.upsert_run(
                            run_id,
                            milestone_id=data.get("milestone_id"),
                            state=data.get("state"),
                            created_at=data.get("created_at"),
                        )
                        counts["runs"] += 1
                wu_path = run_dir / "work_units.json"
                if wu_path.exists():
                    units = json.loads(wu_path.read_text(encoding="utf-8"))
                    existing = {r["id"] for r in db.load_work_unit_rows(run_id)}
                    fresh = [u for u in units if u.get("id") not in existing]
                    if fresh:
                        db.save_work_units(run_id, fresh)
                        counts["work_units"] += len(fresh)
                ev_path = run_dir / "events.jsonl"
                if ev_path.exists():
                    have = db._query(
                        "SELECT COUNT(*) AS n FROM events WHERE run_id = ?", (run_id,)
                    )[0]["n"]
                    lines = [ln for ln in ev_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
                    if have < len(lines):
                        for ln in lines[have:]:
                            try:
                                e = json.loads(ln)
                            except json.JSONDecodeError:
                                continue
                            db.append_event(
                                run_id,
                                e.get("event", "UNKNOWN"),
                                role=e.get("agent_role"),
                                wu_id=e.get("work_unit_id"),
                                payload=e.get("details") or {},
                                ts=e.get("timestamp"),
                            )
                            counts["events"] += 1
        dec_path = ORCHESTRATOR_DIR / "state" / "decisions.jsonl"
        if dec_path.exists():
            have = db._query("SELECT COUNT(*) AS n FROM decisions")[0]["n"]
            lines = [ln for ln in dec_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if have < len(lines):
                for ln in lines[have:]:
                    try:
                        d = json.loads(ln)
                    except json.JSONDecodeError:
                        continue
                    db.append_decision(
                        d.get("run_id", ""),
                        d.get("question", ""),
                        d.get("decision", ""),
                        asked_by_role=d.get("basis") and None,
                        ts=d.get("timestamp"),
                    )
                    counts["decisions"] += 1
        db.set_control("migrated_from_json", utc_now_iso())
    finally:
        if own_db:
            db.close()
    return counts
