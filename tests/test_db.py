import json
from pathlib import Path

import pytest

from oporch import config as cfg
from oporch.constants import AgentRole, EventType, WorkUnitStatus
from oporch.db import OporchDB, migrate_legacy_files
from oporch.decision_ledger import DecisionLedger
from oporch.event_log import EventLog
from oporch.models import OrchestratorDecision, WorkUnit
from oporch.run_state import PersistentRunState, create_run_state


class TestOporchDB:
    def setup_method(self, tmp_path=None):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())
        self.db = OporchDB(self.tmp / "test.db")

    def teardown_method(self):
        self.db.close()

    def test_wal_mode(self):
        mode = self.db._query("PRAGMA journal_mode")[0][0]
        assert str(mode).lower() == "wal"

    def test_upsert_and_get_run(self):
        self.db.upsert_run("run1", milestone_id="M1", state="IDLE")
        row = self.db.get_run("run1")
        assert row["milestone_id"] == "M1"
        self.db.set_run_state("run1", "EXECUTING")
        assert self.db.get_run("run1")["state"] == "EXECUTING"

    def test_save_and_load_roster(self):
        roles = [
            {
                "role_key": "backend",
                "description": "Backend dev",
                "model": "deepseek-v4-flash",
                "fallback": None,
                "max_workers": 2,
                "domains": ["api", "auth"],
            },
            {
                "role_key": "reviewer",
                "description": "Quality gate",
                "model": "nemotron-ultra",
                "max_workers": 1,
                "domains": [],
            },
        ]
        self.db.save_roster("run1", roles, rationale="test roster")
        active = self.db.get_roster("run1")
        assert len(active) == 2
        assert active[0]["role_key"] == "backend"
        assert active[0]["max_workers"] == 2
        assert "api" in active[0]["domains"]
        history = self.db.get_roster_history("run1")
        assert len(history) == 2

    def test_retire_role(self):
        self.db.save_roster(
            "run1",
            [{"role_key": "backend", "description": "", "model": "m",
              "max_workers": 1, "domains": []}],
        )
        assert len(self.db.get_roster("run1")) == 1
        assert self.db.retire_role("run1", "backend") is True
        assert len(self.db.get_roster("run1")) == 0
        # History still shows it
        assert len(self.db.get_roster_history("run1")) == 1

    def test_resize_role(self):
        self.db.save_roster(
            "run1",
            [{"role_key": "backend", "description": "", "model": "m",
              "max_workers": 2, "domains": []}],
        )
        assert self.db.resize_role("run1", "backend", 4) is True
        assert self.db.get_roster("run1")[0]["max_workers"] == 4

    def test_work_units_roundtrip_lossless(self):
        wu = WorkUnit(
            id="WU-001",
            title="Do thing",
            objective="Objective text",
            dependencies=["WU-000"],
            acceptance_criteria=["criterion a"],
            files_likely_affected=["src/x.py"],
        )
        self.db.save_work_units("run1", [wu])
        rows = self.db.load_work_unit_rows("run1")
        assert len(rows) == 1
        blob = json.loads(rows[0]["data"])
        assert blob["objective"] == "Objective text"
        assert blob["acceptance_criteria"] == ["criterion a"]

    def test_record_wu_result_updates_columns(self):
        self.db.save_work_units(
            "run1",
            [{"id": "WU-001", "title": "t", "status": "PENDING"}],
        )
        self.db.record_wu_result(
            "WU-001", run_id="run1", status="COMPLETED", attempt=2,
            result_summary="done ok", finished=True,
        )
        row = self.db.load_work_unit_rows("run1")[0]
        assert row["status"] == "COMPLETED"
        assert row["attempt"] == 2
        assert row["result_summary"] == "done ok"
        assert row["finished_at"] is not None

    def test_count_wu_by_status(self):
        self.db.save_work_units(
            "run1",
            [
                {"id": "WU-001", "title": "a", "status": "PENDING"},
                {"id": "WU-002", "title": "b", "status": "COMPLETED"},
                {"id": "WU-003", "title": "c", "status": "COMPLETED"},
            ],
        )
        counts = self.db.count_wu_by_status("run1")
        assert counts["COMPLETED"] == 2
        assert counts["PENDING"] == 1

    def test_append_and_tail_events(self):
        self.db.append_event("run1", "WORK_UNIT_STARTED", role="builder", wu_id="WU-001")
        self.db.append_event(
            "run1", "WORK_UNIT_COMPLETED", wu_id="WU-001",
            duration_ms=123.5, tokens_in=100, tokens_out=50,
        )
        tail = self.db.tail_events("run1")
        assert len(tail) == 2
        assert tail[0]["event_type"] == "WORK_UNIT_STARTED"
        assert tail[1]["duration_ms"] == 123.5
        per_wu = self.db.events_for_wu("run1", "WU-001")
        assert len(per_wu) == 2

    def test_events_redact_secrets(self):
        self.db.append_event(
            "run1", "DEBUG", payload={"note": "token is sk-abcdefghij0123456789abcdefghij"}
        )
        payload = self.db.tail_events("run1")[0]["payload"]
        assert "sk-abcdefghij0123456789abcdefghij" not in payload
        assert "[REDACTED]" in payload

    def test_decisions_append_search(self):
        self.db.append_decision("run1", "Use sqlite or json?", "sqlite", "planner")
        found = self.db.search_decisions("sqlite")
        assert len(found) == 1
        assert found[0]["answer"] == "sqlite"

    def test_memory_remember_recall_forget(self):
        mid = self.db.remember("/proj", "builder", "gotcha", "always run migrations before tests")
        hits = self.db.recall("/proj", role_key="builder", query="migrations")
        assert len(hits) == 1
        assert hits[0]["content"] == "always run migrations before tests"
        assert self.db.forget(mid) is True
        assert self.db.recall("/proj") == []

    def test_memory_redacted_on_write(self):
        self.db.remember("/proj", "builder", "fact", "key: AKIAIOSFODNN7EXAMPLE more")
        content = self.db.recall("/proj")[0]["content"]
        assert "AKIAIOSFODNN7EXAMPLE" not in content

    def test_memory_keyword_match_filters_nonmatching(self):
        self.db.remember("/proj", "builder", "fact", "use uv for deps")
        self.db.remember("/proj", "builder", "fact", "tests live in tests/")
        hits = self.db.recall("/proj", query="uv deps")
        assert len(hits) == 1

    def test_memory_boost_and_decay(self):
        m1 = self.db.remember("/proj", "builder", "fact", "one")
        self.db.boost_memory([m1], amount=0.5)
        assert self.db.recall("/proj")[0]["relevance_score"] == 1.5
        self.db.decay_project_memory("/proj", factor=0.5)
        assert self.db.recall("/proj")[0]["relevance_score"] == 0.75

    def test_memory_export_import_roundtrip(self):
        self.db.remember("/projA", "builder", "fact", "memory one")
        self.db.remember("/projA", "tester", "gotcha", "memory two")
        out = self.tmp / "export.jsonl"
        assert self.db.export_memory(out) == 2
        other = OporchDB(self.tmp / "other.db")
        try:
            n = other.import_memory(out)
            assert n == 2
            assert len(other.recall("/projA")) >= 2
        finally:
            other.close()

    def test_control_channel(self):
        assert self.db.get_control("pause") is None
        self.db.set_control("pause", "1")
        assert self.db.get_control("pause") == "1"
        self.db.set_control("pause", "0")
        assert self.db.get_control("pause") == "0"


class TestStorageAdapters:
    def setup_method(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())

    def test_event_log_dual_write_and_reload(self, tmp_path=None):
        import uuid

        run_id = "evt" + str(uuid.uuid4())[:8]
        log = EventLog(run_id)
        log.record(EventType.RUN_CREATED)
        # JSONL mirror exists
        from oporch.run_state import RUNS_DIR

        assert (RUNS_DIR / run_id / "events.jsonl").exists()
        # SQLite row exists
        db = EventLog(run_id)._db
        assert len(db.all_events(run_id)) == 1
        # New instance reloads (from file)
        log2 = EventLog(run_id)
        assert log2.count() == 1
        db.close()

    def test_event_log_hydrates_from_db_when_no_file(self):
        run_id = "evtdbonly"
        db = OporchDB(self.tmp / "hydr.db")
        db.append_event(run_id, EventType.PLAN_CREATED.value, ts="2026-01-01T00:00:00+00:00")
        log = EventLog(run_id, db=db)
        assert log.count() == 1
        db.close()

    def test_decision_ledger_dual_write(self):
        ledger = DecisionLedger()
        before = ledger.count()
        d = OrchestratorDecision(
            decision_id="DEC-0001",
            timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            run_id="r1",
            milestone_id="M1",
            question="q dual write unique marker?",
            decision="a",
        )
        ledger.append(d)
        assert ledger.count() == before + 1
        db = DecisionLedger()._db
        assert len(db.search_decisions("dual write unique marker")) >= 1
        db.close()

    def test_run_state_write_through(self):
        prs = PersistentRunState()
        rs = create_run_state("MX", "obj")
        prs.save_run(rs)
        db = PersistentRunState()._db
        row = db.get_run(rs.run_id)
        assert row is not None
        assert row["milestone_id"] == "MX"

        units = [
            WorkUnit(id="WU-001", title="A", objective="o1"),
            WorkUnit(id="WU-002", title="B", objective="o2", dependencies=["WU-001"]),
        ]
        prs.save_work_units(rs.run_id, units)
        loaded = prs.load_work_units(rs.run_id)
        assert len(loaded) == 2
        assert loaded[0].objective == "o1"
        assert loaded[1].dependencies == ["WU-001"]

        # Live status column overrides snapshot blob
        db.record_wu_result("WU-001", status="COMPLETED", attempt=1)
        loaded = prs.load_work_units(rs.run_id)
        assert loaded[0].status == WorkUnitStatus.COMPLETED
        db.close()


class TestMigration:
    def test_migrate_legacy_files(self):
        # Legacy layout under the real .opencode-orchestrator dir
        import uuid

        from oporch.db import ORCHESTRATOR_DIR

        run_id = "legacy" + str(uuid.uuid4())[:8]
        run_dir = ORCHESTRATOR_DIR / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run_state.json").write_text(json.dumps({
            "schema_version": 1,
            "run_id": run_id,
            "milestone_id": "OLD",
            "objective": "old objective",
            "state": "COMPLETED",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "approval_mode": "SUPERVISED",
        }), encoding="utf-8")

        db = OporchDB()
        try:
            counts = migrate_legacy_files(db)
            assert counts["runs"] >= 1
            row = db.get_run(run_id)
            assert row["milestone_id"] == "OLD"
            # Idempotent-ish: second migration doesn't duplicate runs
            counts2 = migrate_legacy_files(db)
            assert counts2["runs"] == 0
        finally:
            db.close()

    def test_migrate_db_cli_registered(self):
        from typer.testing import CliRunner

        from oporch.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "migrate-db" in result.output
        assert "memory" in result.output


class TestMemoryCLI:
    def setup_method(self):
        from typer.testing import CliRunner

        self.runner = CliRunner()

    def test_add_list_forget(self):
        from oporch.cli import app
        from oporch.db import OporchDB

        add = self.runner.invoke(
            app, ["memory", "add", "--role", "builder", "cli test memory xyzzy"]
        )
        assert add.exit_code == 0, add.output
        lst = self.runner.invoke(app, ["memory", "list"])
        assert lst.exit_code == 0
        assert "xyzzy" in lst.output

        db = OporchDB()
        rows = db._query(
            "SELECT id FROM agent_memory WHERE content LIKE '%xyzzy%'"
        )
        db.close()
        mid = rows[-1]["id"]
        forget = self.runner.invoke(app, ["memory", "forget", str(mid)])
        assert forget.exit_code == 0

    def test_export_import(self):
        from oporch.cli import app
        from oporch.db import ORCHESTRATOR_DIR

        out = ORCHESTRATOR_DIR / "test_export.jsonl"
        exp = self.runner.invoke(app, ["memory", "export", "--out", str(out)])
        assert exp.exit_code == 0
        if out.exists():
            imp = self.runner.invoke(app, ["memory", "import", str(out)])
            assert imp.exit_code == 0
