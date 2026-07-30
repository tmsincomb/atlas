"""Tests for the Textual survey app (atlas tui interactive mode)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("textual")

from atlas.survey import RuleNode, gather_survey
from atlas.tui_app import (
    FindingDetail,
    RuleDetail,
    SurveyApp,
    SurveyTree,
    _rule_detail_text,
    _unit_detail_text,
)

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).parent / "fixtures"


def _learn_app() -> SurveyApp:
    return SurveyApp(gather_survey(schema_name="report-bundle"), schema_name="report-bundle")


def _unit_app(unit: Path) -> SurveyApp:
    state = gather_survey(path=unit, schema_name="report-bundle")
    return SurveyApp(state, path=unit, schema_name="report-bundle")


async def test_learn_mode_shows_rule_tree() -> None:
    app = _learn_app()
    async with app.run_test(size=(100, 30)):
        tree = app.query_one(SurveyTree)
        labels = [str(line.node.label) for line in tree._tree_lines]
        assert any("validate" in label for label in labels)
        assert any("required file: report.pdf" in label for label in labels)


async def test_navigation_updates_detail_pane() -> None:
    app = _learn_app()
    async with app.run_test(size=(100, 30)) as pilot:
        tree = app.query_one(SurveyTree)
        await pilot.press("down", "j")
        assert tree.cursor_line == 2
        await pilot.press("k")
        assert tree.cursor_line == 1
        # The detail pane exists and tracked the highlight without error.
        assert app.query_one(RuleDetail)


async def test_n_jumps_to_next_failing_rule() -> None:
    app = _unit_app(FIXTURES / "invalid" / "report-bundle")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("n")
        tree = app.query_one(SurveyTree)
        node = tree.cursor_node
        assert node is not None
        assert isinstance(node.data, RuleNode)
        assert node.data.status == "fail"
        assert node.data.rule_id == "required:report.pdf"

        # Cycles through the warn rules and wraps around.
        await pilot.press("n", "n", "n")
        node = tree.cursor_node
        assert node is not None and isinstance(node.data, RuleNode)
        assert node.data.rule_id == "required:report.pdf"


async def test_n_in_learn_mode_notifies_no_findings() -> None:
    app = _learn_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("n")  # no results attached: must not crash
        tree = app.query_one(SurveyTree)
        assert tree.cursor_node is tree.root


async def test_enter_opens_and_escape_closes_finding_detail() -> None:
    app = _unit_app(FIXTURES / "invalid" / "report-bundle")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("n", "enter")
        assert isinstance(app.screen, FindingDetail)
        table = app.screen.query_one("DataTable")
        assert table.row_count == 1  # the one missing-file finding

        await pilot.press("escape")
        assert not isinstance(app.screen, FindingDetail)


async def test_enter_on_unit_lists_sync_files() -> None:
    app = _unit_app(FIXTURES / "valid" / "report-bundle")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("down", "enter")  # root -> unit node
        assert isinstance(app.screen, FindingDetail)
        table = app.screen.query_one("DataTable")
        assert table.row_count == len(app._state.units[0].sync_files)
        await pilot.press("q")
        assert not isinstance(app.screen, FindingDetail)


async def test_refresh_picks_up_fixed_unit(tmp_path: Path) -> None:
    unit = tmp_path / "report-bundle"
    shutil.copytree(FIXTURES / "invalid" / "report-bundle", unit)

    app = _unit_app(unit)
    async with app.run_test(size=(100, 30)) as pilot:
        assert app._state.units[0].passed is False

        (unit / "report.pdf").write_text("the report")
        await pilot.press("r")
        await pilot.pause()

        assert app._state.units[0].passed is True
        by_id = {n.rule_id: n for n in app._state.units[0].nodes}
        assert by_id["required:report.pdf"].status == "ok"


async def test_quit_keys_exit_cleanly() -> None:
    for key in ("q", "ctrl+c"):
        app = _learn_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press(key)
        assert app.return_code in (0, None)


async def test_tiny_terminal_renders_without_crashing() -> None:
    app = _unit_app(FIXTURES / "invalid" / "report-bundle")
    async with app.run_test(size=(44, 12)) as pilot:
        await pilot.press("n", "enter", "escape", "j", "k")
        assert app.query_one(SurveyTree).cursor_node is not None


async def test_detail_text_helpers() -> None:
    state = gather_survey(path=FIXTURES / "invalid" / "report-bundle", schema_name="report-bundle")
    unit = state.units[0]
    by_id = {n.rule_id: n for n in unit.nodes}

    failing = _rule_detail_text(by_id["required:report.pdf"])
    assert "Missing required file: report.pdf" in failing.plain
    assert "expected: report.pdf" in failing.plain
    assert "actual:   missing" in failing.plain

    unvalidated = _rule_detail_text(by_id["key_outputs:report"])
    assert "not validated" in unvalidated.plain

    unit_text = _unit_detail_text(unit)
    assert "FAIL" in unit_text.plain
    assert "sync files" in unit_text.plain
