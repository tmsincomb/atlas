"""Textual app for the interactive ``atlas tui`` schema survey.

A split-pane inspector over the survey gathered by :mod:`atlas.survey`: the
left pane is the schema's rule tree (with a pass/warn/fail glyph per rule
once real data is validated), the right pane explains the highlighted rule —
what it expects, what the input actually contains, and the exact finding.
Textual supplies the event loop, keyboard/mouse input, and the drill-down
modal; all data comes from the offline gather layer.

This module is the only place in atlas that imports textual/rich, and it is
only imported by the ``tui`` CLI command (deferred, behind the ``[tui]``
extra) — see ADR-0002.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import ClassVar, Union, cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.driver import Driver
from textual.drivers.linux_driver import LinuxDriver
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Static, Tree
from textual.widgets.tree import TreeNode

from atlas.schema import SchemaError
from atlas.survey import (
    SECTION_ORDER,
    STATUS_GLYPHS,
    RuleNode,
    SurveyState,
    UnitReport,
    format_bytes,
    gather_survey,
)
from atlas.validate import Severity

NodeData = Union[RuleNode, UnitReport, None]

_STATUS_STYLE: dict[str | None, str] = {"ok": "green", "warn": "yellow", "fail": "red bold", None: "dim"}

_SEVERITY_RANK: dict[str, int] = {"fail": 0, "warn": 1, "ok": 2}


class _TolerantLinuxDriver(LinuxDriver):
    """LinuxDriver that survives binary noise on the tty.

    Textual's input reader decodes stdin as strict UTF-8 and panics the app
    on an invalid byte. Garbage input (e.g. binary accidentally catted into
    the terminal) should be dropped, not crash the survey, so restart the
    reader with a fresh decoder instead.
    """

    def run_input_thread(self) -> None:
        while True:
            try:
                super().run_input_thread()
                return
            except UnicodeDecodeError:
                continue


def _worst_status(nodes: list[RuleNode]) -> Severity | None:
    statuses: list[Severity] = [status for node in nodes if (status := node.status) is not None]
    if not statuses:
        return None
    return min(statuses, key=lambda status: _SEVERITY_RANK[cast(Severity, status)])


def _status_glyph(status: Severity | None) -> Text:
    return Text(STATUS_GLYPHS[status], style=_STATUS_STYLE[status])


def _rule_label(node: RuleNode) -> Text:
    return Text.assemble(_status_glyph(node.status), " ", node.label)


def _unit_label(unit: UnitReport) -> Text:
    status: Severity = "ok" if unit.passed else "fail"
    if unit.passed and unit.warning_count:
        status = "warn"
    return Text.assemble(
        _status_glyph(status),
        " ",
        str(unit.unit_path),
        (f"  [{unit.schema_name}] ", "dim"),
        (unit.verdict, _STATUS_STYLE[status]),
    )


def _rule_detail_text(node: RuleNode) -> Text:
    text = Text()
    text.append(node.label, style="bold")
    text.append(f"\nsection: {node.section}\n\n", style="dim")
    text.append(node.detail)
    text.append("\n\n")
    if not node.results:
        text.append("not validated — pass a data unit PATH to check this rule", style="dim")
        return text
    text.append("results:\n", style="bold")
    for result in node.results:
        text.append("  ")
        text.append_text(_status_glyph(result.severity))
        text.append(f" expected: {result.expected}\n    actual:   {result.actual}\n")
        if result.message:
            text.append(f"    {result.message}\n", style=_STATUS_STYLE[result.severity])
    return text


def _unit_detail_text(unit: UnitReport) -> Text:
    text = Text()
    text.append(str(unit.unit_path), style="bold")
    text.append(f"\nschema: {unit.schema_name}\n\n")
    style = _STATUS_STYLE["ok" if unit.passed else "fail"]
    text.append(unit.verdict, style=style)
    text.append(f"\n\nsync files: {len(unit.sync_files)} ({format_bytes(unit.total_bytes)})\n")
    text.append("\npress enter to list the sync-filtered files", style="dim")
    return text


class SurveyTree(Tree[NodeData]):
    """The schema rule tree (left pane)."""

    DEFAULT_CSS = """
    SurveyTree {
        width: 40%;
        min-width: 28;
        border-right: solid $accent;
        padding: 0 1;
    }
    """


class RuleDetail(VerticalScroll):
    """Expected-vs-actual explanation for the highlighted node (right pane)."""

    DEFAULT_CSS = """
    RuleDetail {
        width: 1fr;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(Text("select a rule to see what it expects", style="dim"), id="detail-body")

    def show(self, data: NodeData) -> None:
        body = self.query_one("#detail-body", Static)
        if isinstance(data, RuleNode):
            body.update(_rule_detail_text(data))
        elif isinstance(data, UnitReport):
            body.update(_unit_detail_text(data))
        else:
            body.update(Text("select a rule to see what it expects", style="dim"))
        self.scroll_home(animate=False)


class FindingDetail(ModalScreen[None]):
    """Drill-down for one rule or unit: a scrollable table of findings/files."""

    DEFAULT_CSS = """
    FindingDetail {
        align: center middle;
    }
    FindingDetail > Vertical {
        width: 90%;
        max-width: 110;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 0 1;
    }
    FindingDetail Static {
        height: auto;
        text-style: bold;
    }
    FindingDetail DataTable {
        height: 1fr;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape,q", "dismiss_detail", "close")]

    def __init__(self, title: str, columns: list[str], rows: list[tuple[str, ...]]) -> None:
        super().__init__()
        self._title = title
        self._columns = columns
        self._rows = rows

    def compose(self) -> ComposeResult:
        caption = self._title if self._rows else f"{self._title} — nothing to list"
        with Vertical():
            yield Static(caption)
            yield DataTable()

    def on_mount(self) -> None:
        table: DataTable[str] = self.query_one(DataTable)
        table.add_columns(*self._columns)
        for row in self._rows:
            table.add_row(*row)
        table.focus()

    def action_dismiss_detail(self) -> None:
        self.dismiss(None)


class SurveyApp(App[None]):
    """Interactive schema survey: learn what a schema expects, see what failed."""

    CSS = """
    #survey-header {
        height: auto;
        padding: 0 1;
        background: $surface;
        text-style: bold;
    }
    #survey-body {
        height: 1fr;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("k", "cursor_up", "up", show=False),
        Binding("j", "cursor_down", "down", show=False),
        Binding("enter", "select_cursor", "details", show=False),
        Binding("n", "next_problem", "next finding"),
        Binding("r", "refresh_survey", "refresh"),
        Binding("q,escape", "quit", "quit"),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
    ]

    def __init__(
        self,
        state: SurveyState,
        path: Path | None = None,
        schema_name: str | None = None,
        project_root: Path | None = None,
        driver_class: type[Driver] | None = None,
    ) -> None:
        super().__init__(driver_class=driver_class)
        self._state = state
        self._path = path
        self._schema_name = schema_name
        self._project_root = project_root
        self._problem_nodes: list[TreeNode[NodeData]] = []
        self._problem_cursor = -1

    def compose(self) -> ComposeResult:
        yield Static(id="survey-header")
        with Horizontal(id="survey-body"):
            yield SurveyTree("survey")
            yield RuleDetail()
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"atlas — {self._state.title}"
        self._populate()

    @property
    def _tree(self) -> SurveyTree:
        return self.query_one(SurveyTree)

    def _header_text(self) -> Text:
        text = Text.assemble("atlas survey — ", self._state.title)
        if self._state.units:
            passed = sum(1 for u in self._state.units if u.passed)
            total = len(self._state.units)
            style = _STATUS_STYLE["ok" if passed == total else "fail"]
            text.append(f"   {passed}/{total} unit(s) pass", style=style)
        elif self._state.target is not None:
            text.append("   no units detected", style=_STATUS_STYLE[None])
        else:
            text.append("   learn mode — no input data", style=_STATUS_STYLE[None])
        return text

    def _add_sections(self, branch: TreeNode[NodeData], nodes: list[RuleNode], expand_all: bool) -> None:
        for section in SECTION_ORDER:
            section_nodes = [n for n in nodes if n.section == section]
            if not section_nodes:
                continue
            label = Text.assemble(_status_glyph(_worst_status(section_nodes)), " ", section)
            expand = expand_all or section == "validate"
            section_branch = branch.add(label, expand=expand)
            for node in section_nodes:
                leaf = section_branch.add_leaf(_rule_label(node), data=node)
                if node.status in ("fail", "warn"):
                    self._problem_nodes.append(leaf)

    def _populate(self) -> None:
        tree = self._tree
        tree.clear()
        tree.root.set_label(Text(self._state.title, style="bold"))
        tree.root.expand()
        self._problem_nodes = []
        self._problem_cursor = -1

        if self._state.units:
            single = len(self._state.units) == 1
            for unit in self._state.units:
                unit_branch = tree.root.add(_unit_label(unit), data=unit, expand=single)
                self._add_sections(unit_branch, unit.nodes, expand_all=False)
        else:
            self._add_sections(tree.root, self._state.schema_nodes, expand_all=True)

        self.query_one("#survey-header", Static).update(self._header_text())
        tree.focus()

    def action_cursor_up(self) -> None:
        self._tree.action_cursor_up()

    def action_cursor_down(self) -> None:
        self._tree.action_cursor_down()

    def action_select_cursor(self) -> None:
        self._tree.action_select_cursor()

    def action_next_problem(self) -> None:
        if not self._problem_nodes:
            self.notify("no failing or warning rules", severity="information")
            return
        self._problem_cursor = (self._problem_cursor + 1) % len(self._problem_nodes)
        node = self._problem_nodes[self._problem_cursor]
        parent = node.parent
        while parent is not None:
            parent.expand()
            parent = parent.parent
        self._tree.move_cursor(node, animate=False)

    def action_refresh_survey(self) -> None:
        try:
            self._state = gather_survey(self._path, self._schema_name, self._project_root)
        except (SchemaError, ValueError) as exc:
            self.notify(str(exc), title="refresh failed", severity="error")
            return
        self._populate()

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[NodeData]) -> None:
        self.query_one(RuleDetail).show(event.node.data)

    def on_tree_node_selected(self, event: Tree.NodeSelected[NodeData]) -> None:
        data = event.node.data
        if isinstance(data, RuleNode) and data.results:
            rows: list[tuple[str, ...]] = [(r.severity, r.expected, r.actual, r.message or "—") for r in data.results]
            self.push_screen(FindingDetail(data.label, ["status", "expected", "actual", "finding"], rows))
        elif isinstance(data, UnitReport):
            self.push_screen(FindingDetail(f"sync files — {data.unit_path}", ["file", "size"], _file_rows(data)))


def _file_rows(unit: UnitReport) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    for f in unit.sync_files:
        name = str(f)
        with suppress(ValueError):
            name = str(f.relative_to(unit.unit_path))
        try:
            size = format_bytes(f.stat().st_size)
        except OSError:
            size = "?"
        rows.append((name, size))
    return rows


def run_app(
    state: SurveyState,
    path: Path | None = None,
    schema_name: str | None = None,
    project_root: Path | None = None,
) -> None:
    """Launch the interactive survey; the gather args allow ``r`` to refresh."""
    SurveyApp(
        state,
        path=path,
        schema_name=schema_name,
        project_root=project_root,
        driver_class=_TolerantLinuxDriver,
    ).run()
