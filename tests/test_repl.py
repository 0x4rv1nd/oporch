"""Tests for the interactive REPL (repl.py)."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: build a REPL with mocked console I/O
# ---------------------------------------------------------------------------


def _make_repl(inputs: list[str] | None = None):
    """Create an OporchREPL with patched console.input()."""
    from oporch.repl import OporchREPL

    repl = OporchREPL(executor_type="fake")
    if inputs is not None:
        # Each call to console.input() pops from the front.
        _iter = iter(inputs)

        def _mock_input(prompt: str = "") -> str:
            try:
                return next(_iter)
            except StopIteration:
                raise EOFError

        repl.console.input = _mock_input  # type: ignore[assignment]
    return repl


# ---------------------------------------------------------------------------
# Slash commands registry
# ---------------------------------------------------------------------------


class TestDispatch:
    """Verify slash command routing calls the right handler."""

    def test_unknown_command(self, capsys):
        repl = _make_repl()
        repl._dispatch_command("/foobar")
        # Should not raise; just prints an error.

    def test_help_runs(self):
        repl = _make_repl()
        repl._cmd_help("")  # Should not raise.

    def test_quit_sets_stop(self):
        repl = _make_repl()
        assert repl._stop is False
        repl._cmd_quit("")
        assert repl._stop is True

    def test_dispatch_routes_to_quit(self):
        repl = _make_repl()
        repl._dispatch_command("/quit")
        assert repl._stop is True

    def test_dispatch_routes_to_q(self):
        repl = _make_repl()
        repl._dispatch_command("/q")
        assert repl._stop is True


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------


class TestInputParsing:
    """Verify multi-line and single-line input behaviour."""

    def test_slash_returns_immediately(self):
        """A slash command on the first line returns without multi-line prompt."""
        repl = _make_repl(["/status"])
        result = repl._read_input()
        assert result == "/status"

    def test_empty_line_returns_empty(self):
        """An empty first line returns empty string."""
        repl = _make_repl([""])
        result = repl._read_input()
        assert result == ""

    def test_multiline_accumulates_until_blank(self):
        """Non-slash text accumulates lines until an empty line."""
        repl = _make_repl([
            "## Phase 1: Setup",
            "- Create DB schema",
            "",  # empty line signals submit
        ])
        result = repl._read_input()
        assert "Phase 1" in result
        assert "Create DB schema" in result

    def test_multiline_end_marker(self):
        """--end terminates multi-line input."""
        repl = _make_repl([
            "## Phase 1: Setup",
            "--end",
        ])
        result = repl._read_input()
        assert result == "## Phase 1: Setup"


# ---------------------------------------------------------------------------
# Plan parsing integration
# ---------------------------------------------------------------------------


class TestPlanInput:
    """Verify that pasted plan text triggers phase detection."""

    def test_detects_phases(self):
        from oporch.context_builder import parse_plan_doc

        text = (
            "## Phase 1: Database Schema\n"
            "- Create users table\n"
            "## Phase 2: API Endpoints\n"
            "- POST /register\n"
        )
        phases = parse_plan_doc(text)
        assert len(phases) == 2
        assert phases[0].title == "Database Schema"
        assert phases[1].title == "API Endpoints"

    def test_empty_text_returns_no_phases(self):
        from oporch.context_builder import parse_plan_doc

        assert parse_plan_doc("") == []
        assert parse_plan_doc("   \n\n  ") == []


# ---------------------------------------------------------------------------
# Status when no run is active
# ---------------------------------------------------------------------------


class TestStatusNoRun:
    """Status command when no run is active should not crash."""

    @patch("oporch.repl._current_run_id", return_value=None)
    def test_status_no_run(self, mock_run):
        repl = _make_repl()
        repl._cmd_status("")  # Should print "[yellow]No active run[/yellow]" without crashing.


# ---------------------------------------------------------------------------
# Command table completeness
# ---------------------------------------------------------------------------


class TestCommandTable:
    """Ensure every handler in dispatch map is callable."""

    def test_all_handlers_are_methods(self):
        from oporch.repl import OporchREPL

        repl = OporchREPL()
        dispatch = {
            "/plan": repl._cmd_plan,
            "/build": repl._cmd_build,
            "/resume": repl._cmd_resume,
            "/status": repl._cmd_status,
            "/view": repl._cmd_view,
            "/team": repl._cmd_team,
            "/memory": repl._cmd_memory,
            "/remember": repl._cmd_remember,
            "/forget": repl._cmd_forget,
            "/replay": repl._cmd_replay,
            "/report": repl._cmd_report,
            "/models": repl._cmd_models,
            "/logs": repl._cmd_logs,
            "/cancel": repl._cmd_cancel,
            "/doctor": repl._cmd_doctor,
            "/help": repl._cmd_help,
            "/quit": repl._cmd_quit,
            "/q": repl._cmd_quit,
        }
        for cmd, handler in dispatch.items():
            assert callable(handler), f"{cmd} handler is not callable"

    def test_slash_commands_dict_matches_handlers(self):
        """The _SLASH_COMMANDS help dict should cover all dispatchable commands."""
        from oporch.repl import _SLASH_COMMANDS

        # At minimum these must be present
        required = {"/plan", "/build", "/status", "/help", "/quit", "/q",
                    "/team", "/memory", "/cancel", "/view"}
        for cmd in required:
            assert cmd in _SLASH_COMMANDS, f"{cmd} missing from _SLASH_COMMANDS"


# ---------------------------------------------------------------------------
# REPL run loop exits cleanly
# ---------------------------------------------------------------------------


class TestREPLLoop:
    """The run() loop should exit on /quit and on EOFError."""

    def test_quit_exits_loop(self):
        repl = _make_repl(["/quit"])
        with patch.object(repl, "_auto_init"):
            with patch.object(repl, "_print_banner"):
                repl.run()
        assert repl._stop is True

    def test_eof_exits_loop(self):
        """EOFError on first input should exit cleanly."""
        repl = _make_repl([])  # Empty input list → immediate EOFError
        with patch.object(repl, "_auto_init"):
            with patch.object(repl, "_print_banner"):
                repl.run()
