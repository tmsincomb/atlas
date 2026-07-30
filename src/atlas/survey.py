"""Gather and render schema surveys — the renderer-agnostic core of `atlas tui`.

A *survey* pairs a schema's anatomy (what a valid data unit looks like) with
the structured validation results for real data units (what this input got
right or wrong).  This module only gathers state and renders plain text; the
interactive Textual shell in :mod:`atlas.tui_app` is a thin layer on top, so
the same survey renders identically in pipes (``--once``) and on screen.

Deliberately imports neither ``textual`` nor ``rich`` — see ADR-0002.
"""

# ruff: noqa: UP045

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, cast

from pydantic import BaseModel, Field

from atlas.detect import detect
from atlas.schema import Schema, discover_schemas, resolve_schema
from atlas.validate import RuleResult, Severity, validate_data_unit

Section = Literal["detection", "sync", "validate", "key_outputs"]

SECTION_ORDER: tuple[Section, ...] = ("detection", "sync", "validate", "key_outputs")

STATUS_GLYPHS: dict[Optional[str], str] = {"ok": "✓", "warn": "⚠", "fail": "✗", None: "·"}

_SEVERITY_RANK: dict[str, int] = {"fail": 0, "warn": 1, "ok": 2}


class RuleNode(BaseModel):
    """One displayable schema rule, optionally carrying validation results.

    ``rule_id`` matches :class:`atlas.validate.RuleResult.rule_id` for rules
    in the ``validate`` section, which is how results are joined back onto
    the schema tree.  Rules with per-file findings (``filename_pattern``)
    collect several results under one node.
    """

    section: Section
    rule_id: str
    label: str
    detail: str
    """Learn-mode explanation: what the rule expects and what happens on violation."""
    results: list[RuleResult] = Field(default_factory=list)

    @property
    def status(self) -> Optional[Severity]:
        """Worst severity across attached results, or ``None`` when unvalidated."""
        if not self.results:
            return None
        statuses: list[Severity] = [result.severity for result in self.results]
        return min(statuses, key=lambda status: _SEVERITY_RANK[cast(Severity, status)])


class UnitReport(BaseModel):
    """Survey of one data unit: the schema rule tree with results attached."""

    unit_path: Path
    schema_name: str
    nodes: list[RuleNode]
    passed: bool
    error_count: int
    warning_count: int
    sync_files: list[Path]
    total_bytes: int

    @property
    def verdict(self) -> str:
        if self.passed:
            return "PASS" if self.warning_count == 0 else f"PASS ({self.warning_count} warning(s))"
        return f"FAIL ({self.error_count} error(s), {self.warning_count} warning(s))"


class SurveyState(BaseModel):
    """Everything the TUI (or the static renderer) needs for one survey.

    ``units`` empty with no ``target`` means learn mode: browse
    ``schema_nodes`` without any input data.  ``schema_`` is ``None`` only
    in directory mode, where each detected unit names its own schema.
    """

    schema_: Optional[Schema] = None
    schema_origin: str = ""
    target: Optional[Path] = None
    schema_nodes: list[RuleNode] = Field(default_factory=list)
    units: list[UnitReport] = Field(default_factory=list)

    @property
    def title(self) -> str:
        if self.schema_ is not None:
            origin = f", {self.schema_origin}" if self.schema_origin else ""
            return f"schema {self.schema_.name} v{self.schema_.version}{origin}"
        return f"survey of {self.target}"


def _detection_nodes(schema: Schema) -> list[RuleNode]:
    cfg = schema.detection
    if cfg.landmark is None:
        return [
            RuleNode(
                section="detection",
                rule_id="detection:none",
                label="validation-only (no detection)",
                detail="This schema declares no landmark, so `atlas detect` skips it. Use it explicitly with --schema.",
            )
        ]
    nodes = [
        RuleNode(
            section="detection",
            rule_id="detection:landmark",
            label=f"landmark: {cfg.landmark}",
            detail=(
                f"Detection seeds on this rglob pattern (type: {cfg.landmark_type}"
                + (f", parent dir must be '{cfg.landmark_parent}'" if cfg.landmark_parent else "")
                + f"), then walks up {cfg.unit_depth} level(s) to the unit directory."
            ),
        )
    ]
    nodes += [
        RuleNode(
            section="detection",
            rule_id=f"detection:marker:{m}",
            label=f"marker: {m}",
            detail="All markers must exist under the unit directory for the unit to match this schema.",
        )
        for m in cfg.markers
    ]
    if cfg.require_any_glob:
        nodes.append(
            RuleNode(
                section="detection",
                rule_id="detection:require_any_glob",
                label=f"require any of: {', '.join(cfg.require_any_glob)}",
                detail="At least one of these globs must match a file under the unit directory.",
            )
        )
    nodes += [
        RuleNode(
            section="detection",
            rule_id=f"detection:exclude:{m}",
            label=f"exclude if present: {m}",
            detail="A unit containing this path is ruled out — it belongs to a different schema.",
        )
        for m in cfg.exclude_if_markers
    ]
    return nodes


def _sync_nodes(schema: Schema) -> list[RuleNode]:
    cfg = schema.sync
    if not cfg.include and not cfg.exclude:
        return [
            RuleNode(
                section="sync",
                rule_id="sync:all",
                label="include: (all files)",
                detail="No sync filters: every file in the unit counts for size, file-count, and filename checks.",
            )
        ]
    nodes = [
        RuleNode(
            section="sync",
            rule_id=f"sync:include:{p}",
            label=f"include: {p}",
            detail="Files matching this glob are synced and count toward size/file-count/filename checks.",
        )
        for p in cfg.include
    ]
    nodes += [
        RuleNode(
            section="sync",
            rule_id=f"sync:exclude:{p}",
            label=f"exclude: {p}",
            detail="Files matching this glob are filtered out before syncing and validation counting.",
        )
        for p in cfg.exclude
    ]
    return nodes


def _validate_bound_nodes(cfg_value: Optional[float], rule: str, bound: str, label: str, detail: str) -> list[RuleNode]:
    if cfg_value is None:
        return []
    return [RuleNode(section="validate", rule_id=f"{rule}:{bound}", label=label, detail=detail)]


def _validate_nodes(schema: Schema) -> list[RuleNode]:
    v = schema.validation
    if v is None:
        return [
            RuleNode(
                section="validate",
                rule_id="validate:none",
                label="no validation rules",
                detail="This schema has no `validate` section; every unit passes.",
            )
        ]
    nodes: list[RuleNode] = []
    nodes += [
        RuleNode(
            section="validate",
            rule_id=f"required:{r}",
            label=f"required file: {r}",
            detail="This file must exist in the unit, or validation fails.",
        )
        for r in v.required
    ]
    if v.required_any:
        alternatives = ", ".join(v.required_any)
        nodes.append(
            RuleNode(
                section="validate",
                rule_id="required_any",
                label=f"required alternative: {alternatives}",
                detail="At least one file matching these alternative globs must exist, or validation fails.",
            )
        )
    nodes += [
        RuleNode(
            section="validate",
            rule_id=f"required_dirs:{r}",
            label=f"required dir: {r}",
            detail="This directory must exist in the unit, or validation fails.",
        )
        for r in v.required_dirs
    ]
    nodes += [
        RuleNode(
            section="validate",
            rule_id=f"fail_on:{p}",
            label=f"fail on: {p}",
            detail="Any file matching this glob fails validation — it marks an incomplete or failed pipeline run.",
        )
        for p in v.fail_on
    ]
    nodes += _validate_bound_nodes(
        v.min_size_mb,
        "size",
        "min",
        f"min size: {v.min_size_mb} MB",
        "Total size of sync-filtered files must reach this floor, or validation fails.",
    )
    nodes += _validate_bound_nodes(
        v.max_size_gb,
        "size",
        "max",
        f"max size: {v.max_size_gb} GB",
        "Total size of sync-filtered files must stay under this ceiling, or validation fails.",
    )
    nodes += _validate_bound_nodes(
        v.min_file_count,
        "file_count",
        "min",
        f"min file count: {v.min_file_count}",
        "The sync-filtered file count must reach this floor, or validation fails.",
    )
    nodes += _validate_bound_nodes(
        v.max_file_count,
        "file_count",
        "max",
        f"max file count: {v.max_file_count}",
        "The sync-filtered file count must stay under this ceiling, or validation fails.",
    )
    nodes += [
        RuleNode(
            section="validate",
            rule_id=f"warn_if_missing:{o}",
            label=f"optional: {o}",
            detail="Missing this path only warns; the unit still passes.",
        )
        for o in v.warn_if_missing
    ]
    if v.filename_pattern is not None:
        nodes.append(
            RuleNode(
                section="validate",
                rule_id="filename_pattern",
                label=f"filename pattern: {v.filename_pattern}",
                detail="Every sync-filtered filename should match this regex; each mismatch warns.",
            )
        )
    return nodes


def _key_output_nodes(schema: Schema) -> list[RuleNode]:
    return [
        RuleNode(
            section="key_outputs",
            rule_id=f"key_outputs:{name}",
            label=f"{name}: {path}",
            detail="Named output that consumers resolve to concrete files via resolve_key_output().",
        )
        for name, path in sorted(schema.key_outputs.items())
    ]


def schema_rule_nodes(schema: Schema) -> list[RuleNode]:
    """Flatten a schema into displayable rule nodes, grouped by section."""
    return _detection_nodes(schema) + _sync_nodes(schema) + _validate_nodes(schema) + _key_output_nodes(schema)


def attach_results(nodes: list[RuleNode], checks: list[RuleResult]) -> list[RuleNode]:
    """Join validation results onto rule nodes by ``rule_id``.

    Returns new nodes; the input list is not mutated, so one schema's node
    tree can be reused across many units.
    """
    by_id: dict[str, list[RuleResult]] = {}
    for check in checks:
        by_id.setdefault(check.rule_id, []).append(check)
    return [node.model_copy(update={"results": by_id.get(node.rule_id, [])}) for node in nodes]


def _survey_unit(unit_path: Path, schema: Schema) -> UnitReport:
    result = validate_data_unit(unit_path, schema)
    total_bytes = 0
    for f in result.sync_files:
        try:
            total_bytes += f.stat().st_size
        except OSError:
            continue
    return UnitReport(
        unit_path=unit_path,
        schema_name=schema.name,
        nodes=attach_results(schema_rule_nodes(schema), result.checks),
        passed=result.passed,
        error_count=len(result.errors),
        warning_count=len(result.warnings),
        sync_files=result.sync_files,
        total_bytes=total_bytes,
    )


def _schema_origin(name: str, project_root: Path) -> str:
    """Which of resolve_schema's four sources *name* resolves from."""
    if Path(name).is_absolute() or name.endswith((".yaml", ".yml")):
        return "file"
    if any((project_root / "schemas" / f"{name}{suffix}").exists() for suffix in (".yaml", ".yml")):
        return "project"
    if any((Path.home() / ".atlas" / "schemas" / f"{name}{suffix}").exists() for suffix in (".yaml", ".yml")):
        return "user"
    return "built-in"


def _detected_unit_dirs(stage_path: Path, unit_ids: list[str]) -> list[Path]:
    return [stage_path / uid for uid in unit_ids] if unit_ids else [stage_path]


def gather_survey(
    path: Optional[Path] = None,
    schema_name: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> SurveyState:
    """Gather a survey for the three `atlas tui` modes.

    - *schema_name* only → learn mode: the schema's rule tree, no results.
    - *path* + *schema_name* → validate that one unit directory.
    - *path* only → detect units under *path* (built-ins plus project/user
      schemas) and validate each against the schema that matched it.

    Raises :class:`atlas.schema.SchemaError` when *schema_name* cannot be
    resolved.  At least one of *path* / *schema_name* must be given.
    """
    if path is None and schema_name is None:
        raise ValueError("gather_survey needs a path, a schema name, or both")
    root = Path(project_root) if project_root is not None else Path.cwd()

    if schema_name is not None:
        schema = resolve_schema(schema_name, root)
        state = SurveyState(
            schema_=schema,
            schema_origin=_schema_origin(schema_name, root),
            target=path,
            schema_nodes=schema_rule_nodes(schema),
        )
        if path is not None:
            state.units = [_survey_unit(path, schema)]
        return state

    assert path is not None  # narrowed by the guard above
    schemas = discover_schemas(project_root=root)
    by_name = {s.name: s for s in schemas}
    units = [
        _survey_unit(unit_dir, by_name[det.schema_name])
        for det in detect(path, schemas=schemas)
        for unit_dir in _detected_unit_dirs(det.stage_path, det.unit_ids)
    ]
    units.sort(key=lambda u: (u.schema_name, str(u.unit_path)))
    return SurveyState(target=path, units=units)


def format_bytes(size: int) -> str:
    """Human-readable byte size (binary units, one decimal)."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"  # pragma: no cover - loop always returns


def _render_nodes(lines: list[str], nodes: list[RuleNode], show_findings: bool) -> None:
    for section in SECTION_ORDER:
        section_nodes = [n for n in nodes if n.section == section]
        if not section_nodes:
            continue
        lines.append(f"  {section}:")
        for node in section_nodes:
            lines.append(f"    {STATUS_GLYPHS[node.status]} {node.label}")
            if show_findings:
                for result in node.results:
                    if result.severity != "ok":
                        lines.append(f"        {result.severity}: {result.message}")


def _render_unit(lines: list[str], unit: UnitReport) -> None:
    lines.append(f"{unit.unit_path} [{unit.schema_name}] — {unit.verdict}")
    lines.append(f"  sync files: {len(unit.sync_files)} ({format_bytes(unit.total_bytes)})")
    _render_nodes(lines, unit.nodes, show_findings=True)


def render_report(state: SurveyState, width: int = 80) -> str:
    """Render a survey as a plain-text frame (the ``--once`` / pipe output).

    Learn mode prints each rule's explanation; unit mode prints the per-rule
    checklist with findings under the failing rules.
    """
    lines = [f"atlas survey — {state.title}", "=" * min(width, 78)]
    if state.schema_ is not None and state.schema_.description:
        lines.insert(1, state.schema_.description)

    if state.units:
        for i, unit in enumerate(state.units):
            if i:
                lines.append("")
            _render_unit(lines, unit)
    elif state.target is not None:
        lines.append(f"No known data types detected under {state.target}.")
    else:
        lines.append("(learn mode — no input data; showing what the schema expects)")
        for section in SECTION_ORDER:
            section_nodes = [n for n in state.schema_nodes if n.section == section]
            if not section_nodes:
                continue
            lines.append(f"  {section}:")
            for node in section_nodes:
                lines.append(f"    · {node.label}")
                lines.append(f"        {node.detail}")
    return "\n".join(lines) + "\n"
