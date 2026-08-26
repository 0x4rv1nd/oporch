"""Dashboard TUI tests (PRD §6 phase 6) using Textual's Pilot harness."""

from __future__ import annotations

import pytest

from oporch.dashboard import OporchDashboard, WUDetailScreen
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


def _visible_text(app) -> str:
    """Collect rendered text from every Static in the app."""
    from textual.widgets import Static

    parts = []
    for node in app.query(Static):
        try:
            parts.append(str(node.content))
        except Exception:
            continue
    return "\n".join(parts)


class TestDashboardApp:
    @pytest.mark.asyncio
    async def test_app_renders_roles_and_events(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            joined = _visible_text(app)
            assert "backend" in joined
            assert "WU-001" in joined and "▶" in joined
            assert "✓" in joined and "WU-002" in joined
            assert "⚡" in joined and "WU-004" in joined
            assert "WORK_UNIT_COMPLETED" in joined

    @pytest.mark.asyncio
    async def test_status_chips_rendered(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            chips = str(app.query_one("#chips").content)
            assert "done:1" in chips or "completed:1" in chips
            assert "active:1" in chips
            assert "conflict:1" in chips

    @pytest.mark.asyncio
    async def test_pause_toggle_writes_control_row(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("p")
            await pilot.pause(0.2)
            assert db.get_control("pause") == "1"
            topbar = str(app.query_one("#topbar").content)
            assert "PAUSED" in topbar
            await pilot.press("p")
            await pilot.pause(0.2)
            assert db.get_control("pause") == "0"

    @pytest.mark.asyncio
    async def test_detail_drilldown_modal(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        async with app.run_test(size=(110, 36)) as pilot:
            await pilot.pause(0.4)
            before = type(app.screen).__name__
            await pilot.press("d")
            await pilot.pause(0.3)
            assert isinstance(app.screen, WUDetailScreen)
            body = str(app.screen.query_one("#detail-body").content)
            assert "WU-001" in body
            assert "Event trail" in body
            assert "WORK_UNIT_STARTED" in body
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert type(app.screen).__name__ == before

    @pytest.mark.asyncio
    async def test_keyboard_navigation_moves_selection(self, seeded_db):
        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        async with app.run_test(size=(110, 36)) as pilot:
            await pilot.pause(0.4)
            first_selected = app._flat[app._cursor]["id"]
            await pilot.press("j")
            await pilot.pause(0.1)
            second_selected = app._flat[app._cursor]["id"]
            assert second_selected != first_selected
            await pilot.press("k")
            await pilot.pause(0.1)
            assert app._flat[app._cursor]["id"] == first_selected

    @pytest.mark.asyncio
    async def test_click_selects_wu_card(self, seeded_db):
        from oporch.dashboard import WUCard

        db, run_id = seeded_db
        app = OporchDashboard(run_id, db=db)
        async with app.run_test(size=(110, 36)) as pilot:
            await pilot.pause(0.4)
            cards = list(app.query(WUCard))
            target = next(c for c in cards if c.wu_id == "WU-002")
            await pilot.click(target)
            await pilot.pause(0.2)
            selected = app._flat[app._cursor]["id"]
            assert selected == "WU-002"

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
