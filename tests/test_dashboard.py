"""Dashboard TUI tests (PRD §6 phase 6) using Textual's Pilot harness."""

from __future__ import annotations

import pytest

from oporch.dashboard import OporchDashboard
from oporch.db import OporchDB

pytest.importorskip("textual")


def _seed_run(db: OporchDB, run_id: str) -> None:
    db.upsert_run(run_id, milestone_id="M1", state="EXECUTING")
    db.save_roster(
        run_id,
        [
            {"role_key": "backend", "description": "", "model": "m",
             "max_workers": 2, "domains": ["api"]},
            {"role_key": "frontend", "description": "", "model": "m",
             "max_workers": 1, "domains": ["ui"]},
        ],
        rationale="test roster",
    )
    db.save_work_units(
        run_id,
        [
            {"id": "WU-001", "title": "Auth API", "status": "IN_PROGRESS",
             "assigned_role": "backend", "attempt": 2},
            {"id": "WU-002", "title": "Login page", "status": "COMPLETED",
             "assigned_role": "frontend", "attempt": 1, "finished_at": "now"},
            {"id": "WU-003", "title": "JWT store", "status": "PENDING",
             "assigned_role": "backend"},
            {"id": "WU-004", "title": "Broken thing", "status": "MERGE_CONFLICT",
             "assigned_role": "frontend"},
        ],
    )
    db.append_event(run_id, "WORK_UNIT_STARTED", role="backend", wu_id="WU-001")
    db.append_event(run_id, "WORK_UNIT_COMPLETED", wu_id="WU-002")
    db.append_event(
        run_id, "REVIEW_FAILED", role="reviewer", wu_id="WU-004",
        payload={"error": "conflict on shared.txt"}, level="warn",
    )


@pytest.fixture()
def seeded_db():
    import uuid

    db = OporchDB()
    run_id = "tuidbg" + str(uuid.uuid4())[:6]
    _seed_run(db, run_id)
    yield db, run_id
    db.close()


class TestSnapshotLogic:
    def test_snapshot_shape(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        snap = app.snapshot()
        assert len(snap["roles"]) == 2
        assert len(snap["wus"]) == 4
        assert snap["counts"]["COMPLETED"] == 1
        assert len(snap["events"]) == 3

    def test_render_progress_bar(self, seeded_db):
        db, run_id = seeded_db
        snap = OporchDashboard(run_id, db=db).snapshot()
        line = OporchDashboard.render_progress(snap)
        assert "EXECUTING" in line
        assert "1/4" in line
        assert "25%" in line
        assert "█" in line and "░" in line

    def test_group_wus_by_role(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        grouped = app.group_wus_by_role(app.snapshot()["wus"])
        assert set(grouped.keys()) == {"backend", "frontend"}
        assert len(grouped["backend"]) == 2

    def test_render_events_tail(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        text = app.render_events(app.snapshot()["events"])
        assert "Recent events" in text
        assert "WORK_UNIT_STARTED" in text


class TestDashboardApp:
    @pytest.mark.asyncio
    async def test_app_renders_roles_and_events(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        async with app.run_test(size=(120, 40)) as pilot:
            app.refresh_from_db()
            await pilot.pause(0.2)
            columns = app.query_one("#columns")
            joined = "\n".join(str(c.content) for c in columns.children)
            assert "backend" in joined
            assert "WU-001" in joined and "▶" in joined
            assert "✓" in joined and "WU-002" in joined
            assert "⚡" in joined and "WU-004" in joined
            events_text = str(app.query_one("#events-inner").content)
            assert "WORK_UNIT_COMPLETED" in events_text

    @pytest.mark.asyncio
    async def test_pause_toggle_writes_control_row(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("p")
            await pilot.pause(0.1)
            assert db.get_control("pause") == "1"
            await pilot.press("p")
            await pilot.pause(0.1)
            assert db.get_control("pause") == "0"

    @pytest.mark.asyncio
    async def test_detail_drilldown(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        async with app.run_test(size=(110, 36)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("d")
            await pilot.pause(0.2)
            detail = app.query_one("#detail")
            assert detail.display is True
            text = str(detail.content)
            assert "WU-001" in text
            assert "Event trail" in text
            await pilot.press("d")
            await pilot.pause(0.1)
            assert detail.display is False

    @pytest.mark.asyncio
    async def test_quit_key(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("q")
        assert not app.is_running


class TestViewerIsolation:
    def test_viewer_never_mutates_run_state(self, seeded_db):
        """Opening/closing the dashboard must not affect the run."""
        db, run_id = seeded_db
        before = db.get_run(run_id)
        app = OporchDashboard(run_id, db=db)
        snap = app.snapshot()
        after = db.get_run(run_id)
        assert before == after
        assert snap["run"]["state"] == "EXECUTING"
