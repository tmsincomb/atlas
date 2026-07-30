"""Tests for the `atlas tui` CLI command (static fallback and dispatch)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from atlas.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def _run(args: list[str]):
    return CliRunner().invoke(main, args)


class TestOnceMode:
    """Static report output (--once / non-tty)."""

    def test_learn_mode_renders_schema(self):
        result = _run(["tui", "--schema", "report-bundle", "--once"])
        assert result.exit_code == 0
        assert "schema report-bundle" in result.output
        assert "learn mode" in result.output
        assert "required file: report.pdf" in result.output

    def test_invalid_unit_renders_findings_but_exits_zero(self):
        """The tui is a viewer, not a gate — `atlas validate` gates."""
        unit = FIXTURES / "invalid" / "report-bundle"
        result = _run(["tui", str(unit), "--schema", "report-bundle", "--once"])
        assert result.exit_code == 0
        assert "FAIL" in result.output
        assert "Missing required file: report.pdf" in result.output

    def test_valid_unit_renders_pass(self):
        unit = FIXTURES / "valid" / "report-bundle"
        result = _run(["tui", str(unit), "--schema", "report-bundle", "--once"])
        assert result.exit_code == 0
        assert "PASS" in result.output

    def test_directory_mode_surveys_detected_units(self):
        result = _run(["tui", str(FIXTURES / "valid"), "--once"])
        assert result.exit_code == 0
        assert "monorepo-build" in result.output

    def test_non_tty_falls_back_to_static_without_once(self):
        """CliRunner stdio is not a tty, so the static path runs even without --once."""
        result = _run(["tui", "--schema", "report-bundle"])
        assert result.exit_code == 0
        assert "learn mode" in result.output


class TestArgumentErrors:
    def test_no_args_is_usage_error(self):
        result = _run(["tui"])
        assert result.exit_code == 2
        assert "PATH" in result.output and "--schema" in result.output

    def test_unknown_schema_is_clean_error(self):
        result = _run(["tui", "--schema", "no-such-schema", "--once"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_missing_path_is_usage_error(self, tmp_path: Path):
        result = _run(["tui", str(tmp_path / "nope"), "--once"])
        assert result.exit_code == 2


class TestInteractiveDispatch:
    def test_missing_tui_extra_gives_install_hint(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("atlas.cli._stdio_is_tty", lambda: True)
        monkeypatch.setitem(sys.modules, "atlas.tui_app", None)  # import raises ImportError
        result = _run(["tui", "--schema", "report-bundle"])
        assert result.exit_code == 1
        assert "pip install 'atlas-manifest[tui]'" in result.output

    def test_interactive_launches_run_app(self, monkeypatch: pytest.MonkeyPatch):
        pytest.importorskip("textual")
        calls: dict[str, object] = {}

        def fake_run_app(state, path=None, schema_name=None, project_root=None):
            calls["schema"] = state.schema_.name if state.schema_ else None
            calls["schema_name"] = schema_name

        monkeypatch.setattr("atlas.cli._stdio_is_tty", lambda: True)
        monkeypatch.setattr("atlas.tui_app.run_app", fake_run_app)
        result = _run(["tui", "--schema", "report-bundle"])
        assert result.exit_code == 0
        assert calls == {"schema": "report-bundle", "schema_name": "report-bundle"}
