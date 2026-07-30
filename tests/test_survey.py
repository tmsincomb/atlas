"""Tests for atlas survey module (the renderer-agnostic TUI core)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from atlas.schema import Schema, SchemaError, ValidateConfig, resolve_schema
from atlas.survey import (
    SurveyState,
    attach_results,
    format_bytes,
    gather_survey,
    render_report,
    schema_rule_nodes,
)
from atlas.validate import validate_data_unit

FIXTURES = Path(__file__).parent / "fixtures"


class TestSchemaRuleNodes:
    """Tests for flattening a schema into displayable rule nodes."""

    def test_report_bundle_sections(self, tmp_path: Path):
        schema = resolve_schema("report-bundle", tmp_path)
        nodes = schema_rule_nodes(schema)
        sections = {n.section for n in nodes}
        assert sections == {"detection", "sync", "validate", "key_outputs"}

    def test_validation_only_schema_says_so(self, tmp_path: Path):
        """report-bundle has no detection block -> explicit validation-only node."""
        schema = resolve_schema("report-bundle", tmp_path)
        nodes = [n for n in schema_rule_nodes(schema) if n.section == "detection"]
        assert len(nodes) == 1
        assert "validation-only" in nodes[0].label

    def test_validate_rule_ids_match_check_rule_ids(self, tmp_path: Path):
        """Every validate-section node id appears in validate_data_unit's checks."""
        schema = resolve_schema("report-bundle", tmp_path)
        unit = FIXTURES / "invalid" / "report-bundle"
        node_ids = {n.rule_id for n in schema_rule_nodes(schema) if n.section == "validate"}
        check_ids = {c.rule_id for c in validate_data_unit(unit, schema).checks}
        assert node_ids == check_ids

    def test_no_validate_section_gets_placeholder(self):
        schema = Schema(name="bare")
        nodes = [n for n in schema_rule_nodes(schema) if n.section == "validate"]
        assert len(nodes) == 1
        assert nodes[0].rule_id == "validate:none"

    def test_every_node_has_learn_detail(self, tmp_path: Path):
        schema = resolve_schema("monorepo-build", tmp_path)
        assert all(n.detail for n in schema_rule_nodes(schema))


class TestAttachResults:
    """Tests for joining validation results onto rule nodes."""

    def test_results_attach_by_rule_id(self, tmp_path: Path):
        schema = resolve_schema("report-bundle", tmp_path)
        unit = FIXTURES / "invalid" / "report-bundle"
        result = validate_data_unit(unit, schema)
        nodes = attach_results(schema_rule_nodes(schema), result.checks)

        by_id = {n.rule_id: n for n in nodes}
        assert by_id["required:report.pdf"].status == "fail"
        assert by_id["required_dirs:figures"].status == "ok"

    def test_input_nodes_not_mutated(self, tmp_path: Path):
        schema = resolve_schema("report-bundle", tmp_path)
        nodes = schema_rule_nodes(schema)
        result = validate_data_unit(FIXTURES / "invalid" / "report-bundle", schema)
        attach_results(nodes, result.checks)
        assert all(n.results == [] for n in nodes)

    def test_unvalidated_sections_have_no_status(self, tmp_path: Path):
        schema = resolve_schema("report-bundle", tmp_path)
        result = validate_data_unit(FIXTURES / "valid" / "report-bundle", schema)
        nodes = attach_results(schema_rule_nodes(schema), result.checks)
        assert all(n.status is None for n in nodes if n.section == "key_outputs")

    def test_worst_severity_wins(self, tmp_path: Path):
        """A node with both warn and fail results reports fail."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "bad name.txt").write_text("x")
        schema = Schema(name="t", validate=ValidateConfig(filename_pattern=r"^\S+$", min_file_count=5))
        result = validate_data_unit(unit, schema)
        nodes = attach_results(schema_rule_nodes(schema), result.checks)
        by_id = {n.rule_id: n for n in nodes}
        assert by_id["filename_pattern"].status == "warn"
        assert by_id["file_count:min"].status == "fail"


class TestGatherSurvey:
    """Tests for the three gather modes."""

    def test_learn_mode(self):
        state = gather_survey(schema_name="report-bundle")
        assert state.schema_ is not None
        assert state.schema_.name == "report-bundle"
        assert state.schema_origin == "built-in"
        assert state.units == []
        assert state.target is None
        assert state.schema_nodes

    def test_project_yml_origin(self, tmp_path: Path):
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "custom.yml").write_text("name: custom\n", encoding="utf-8")

        state = gather_survey(schema_name="custom", project_root=tmp_path)

        assert state.schema_origin == "project"

    def test_single_unit_mode_valid(self):
        state = gather_survey(path=FIXTURES / "valid" / "report-bundle", schema_name="report-bundle")
        assert len(state.units) == 1
        unit = state.units[0]
        assert unit.passed is True
        assert unit.error_count == 0

    def test_single_unit_mode_invalid(self):
        state = gather_survey(path=FIXTURES / "invalid" / "report-bundle", schema_name="report-bundle")
        unit = state.units[0]
        assert unit.passed is False
        assert unit.error_count >= 1
        assert "FAIL" in unit.verdict

    def test_directory_mode_detects_units(self):
        state = gather_survey(path=FIXTURES / "valid")
        assert state.schema_ is None
        names = {u.schema_name for u in state.units}
        # Detectable built-ins present in the valid fixture tree.
        assert {"csv-dataset", "monorepo-build", "photo-import", "site-archive", "web-build"} <= names

    def test_directory_mode_no_detections(self, tmp_path: Path):
        state = gather_survey(path=tmp_path)
        assert state.units == []
        assert state.target == tmp_path

    def test_unknown_schema_raises(self, tmp_path: Path):
        with pytest.raises(SchemaError):
            gather_survey(schema_name="no-such-schema", project_root=tmp_path)

    def test_no_args_raises(self):
        with pytest.raises(ValueError):
            gather_survey()


class TestRenderReport:
    """Tests for the plain-text static frame."""

    def test_learn_mode_shows_rules_and_details(self):
        state = gather_survey(schema_name="report-bundle")
        text = render_report(state)
        assert "schema report-bundle" in text
        assert "learn mode" in text
        assert "required file: report.pdf" in text
        assert "This file must exist in the unit" in text

    def test_invalid_unit_shows_fail_glyph_and_finding(self):
        state = gather_survey(path=FIXTURES / "invalid" / "report-bundle", schema_name="report-bundle")
        text = render_report(state)
        assert "FAIL" in text
        assert "✗ required file: report.pdf" in text
        assert "Missing required file: report.pdf" in text

    def test_valid_unit_shows_pass(self):
        state = gather_survey(path=FIXTURES / "valid" / "report-bundle", schema_name="report-bundle")
        text = render_report(state)
        assert "PASS" in text
        assert "✓ required file: report.pdf" in text
        assert "✗" not in text

    def test_empty_directory_message(self, tmp_path: Path):
        text = render_report(gather_survey(path=tmp_path))
        assert "No known data types detected" in text

    def test_multi_unit_render_names_each_unit(self):
        state = gather_survey(path=FIXTURES / "invalid")
        text = render_report(state)
        for unit in state.units:
            assert str(unit.unit_path) in text


class TestFormatBytes:
    def test_scales(self):
        assert format_bytes(0) == "0 B"
        assert format_bytes(512) == "512 B"
        assert format_bytes(2048) == "2.0 KiB"
        assert format_bytes(3 * 1024 * 1024) == "3.0 MiB"
        assert format_bytes(5 * 1024 * 1024 * 1024) == "5.0 GiB"


class TestNoTuiDependency:
    """survey must stay importable without the [tui] extra (ADR-0002)."""

    def test_survey_never_imports_textual_or_rich(self):
        code = (
            "import atlas.survey, sys; "
            "leaked = [m for m in sys.modules if m.split('.')[0] in ('textual', 'rich')]; "
            "assert not leaked, leaked; "
            "print('ok')"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"


class TestSurveyStateTitle:
    def test_schema_title_includes_origin(self):
        state = gather_survey(schema_name="report-bundle")
        assert state.title == "schema report-bundle v1.0, built-in"

    def test_directory_title_names_target(self, tmp_path: Path):
        state = SurveyState(target=tmp_path)
        assert str(tmp_path) in state.title
