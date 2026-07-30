"""Tests for the atlas CLI (atlas.cli:main) via click's CliRunner."""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from atlas.cli import main
from atlas.schema import Schema, load_all_schemas

BUILTINS = [
    "10x-bcl-demux",
    "10x-cellranger-count",
    "10x-cellranger-multi",
    "csv-dataset",
    "facs-sort",
    "facs-sort-diva",
    "illumina-bcl-run",
    "monorepo-build",
    "photo-import",
    "report-bundle",
    "site-archive",
    "web-build",
]


def _valid_photo_unit(base: Path) -> Path:
    """A photo-import unit that passes validation (dir present, >0.1 MB, >=1 file)."""
    unit = base / "shoot1"
    raw = unit / "MediaLibrary" / "RawPhotos"
    raw.mkdir(parents=True)
    (raw / "IMG_0001.jpg").write_bytes(b"\0" * (256 * 1024))
    return unit


def _monorepo_unit(base: Path) -> Path:
    """A monorepo-build unit under a stage dir; returns the unit directory."""
    unit = base / "builds" / "web-portal"
    unit.mkdir(parents=True)
    (unit / ".workspace-stamp").mkdir()
    (unit / "_buildmeta").write_text("bundler workspace")
    dist = unit / "dist"
    dist.mkdir()
    (dist / "config.json").write_text("{}")
    (dist / "packages" / "core").mkdir(parents=True)
    (dist / "packages" / "core" / "bundle.js").write_bytes(b"bundle")
    return unit


class TestSchemas:
    def test_lists_all_builtins(self):
        result = CliRunner().invoke(main, ["schemas"])
        assert result.exit_code == 0
        for name in BUILTINS:
            assert name in result.output


class TestShow:
    def test_show_monorepo(self):
        result = CliRunner().invoke(main, ["show", "monorepo-build"])
        assert result.exit_code == 0
        assert "monorepo-build" in result.output
        assert "version" in result.output
        assert "key_outputs" in result.output
        assert "pkg_bundle" in result.output

    def test_show_bogus_exits_nonzero(self):
        result = CliRunner().invoke(main, ["show", "bogus"])
        assert result.exit_code != 0

    def test_default_output_is_unchanged(self):
        result = CliRunner().invoke(main, ["show", "report-bundle"])

        assert (
            result.output
            == """name:        report-bundle
version:     1.0
description: Validation-only report bundle (no detection; validate an assembled report)
detection.markers:
sync.include:
validate:
  required: ['report.pdf']
  required_dirs: ['figures']
  warn_if_missing: ['summary.md', 'data']
  fail_on: ['_errors']
  size: min 0.01 MB, max 10.0 GB
  file_count: min 1, max 10000
key_outputs:
  figures: figures/*.png
  report: report.pdf
  summary: summary.md
"""
        )

    def test_yaml_round_trips_all_builtins(self):
        builtins = {schema.name: schema for schema in load_all_schemas()}

        for name, expected in builtins.items():
            result = CliRunner().invoke(main, ["show", name, "--yaml"])
            assert result.exit_code == 0, result.output

            data = yaml.safe_load(result.output)
            assert Schema.model_validate(data) == expected
            assert "validate" in data
            assert "validation" not in data

    def test_yaml_includes_hidden_fields_and_omits_default_noise(self):
        result = CliRunner().invoke(main, ["show", "web-build", "--yaml"])
        assert result.exit_code == 0, result.output

        data = yaml.safe_load(result.output)
        detection = data["detection"]
        assert detection["landmark"] == "dist/app.js"
        assert detection["landmark_type"] == "file"
        assert detection["unit_depth"] == 2
        assert detection["exclude_if_cmdline_subcommand"]["subcommand"] == "workspace"
        assert "require_any_glob" not in detection
        assert "sync_by" not in detection
        assert "unit_is_directory_stage" not in detection
        assert "version" not in data
        assert "filename_pattern" not in data["validate"]

    def test_yaml_preserves_nondefault_values(self):
        result = CliRunner().invoke(main, ["show", "site-archive", "--yaml"])
        assert result.exit_code == 0, result.output

        data = yaml.safe_load(result.output)
        assert data["detection"]["unit_is_directory_stage"] is True

    def test_yaml_bogus_schema_exits_nonzero(self):
        result = CliRunner().invoke(main, ["show", "bogus", "--yaml"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_yaml_malformed_schema_exits_nonzero(self, tmp_path: Path):
        schema_path = tmp_path / "malformed.yaml"
        schema_path.write_text("name: [")

        result = CliRunner().invoke(main, ["show", str(schema_path), "--yaml"])
        assert result.exit_code != 0
        assert "Invalid YAML" in result.output
        assert "received: 'name: ['" in result.output
        assert "expected: syntactically valid YAML mapping" in result.output
        assert "generated examples:" in result.output
        assert '"name: sample-run"' in result.output

    def test_yaml_typo_reports_nested_field_path(self, tmp_path: Path):
        schema_path = tmp_path / "typo.yaml"
        schema_path.write_text("name: typo\nvalidate:\n  min_files_count: 1\n")

        result = CliRunner().invoke(main, ["show", str(schema_path), "--yaml"])

        assert result.exit_code != 0
        assert "validate.min_files_count" in result.output
        assert "Extra inputs are not permitted" in result.output
        assert "received: 1" in result.output
        assert "expected: recognized field" in result.output
        assert "min_file_count: 0" in result.output

    def test_yaml_restricted_set_reports_allowed_examples(self, tmp_path: Path):
        schema_path = tmp_path / "invalid-set.yaml"
        schema_path.write_text("name: invalid-set\ndetection:\n  landmark_type: folder\n")

        result = CliRunner().invoke(main, ["show", str(schema_path), "--yaml"])

        assert result.exit_code != 0
        assert "received: 'folder'" in result.output
        assert "expected: one of: 'file', 'dir', 'any'" in result.output
        assert '      - "file"' in result.output
        assert '      - "dir"' in result.output
        assert '      - "any"' in result.output


class TestDetect:
    def test_detect_built_tree(self, tmp_path):
        _monorepo_unit(tmp_path)

        result = CliRunner().invoke(main, ["detect", str(tmp_path)])
        assert result.exit_code == 0
        assert "monorepo-build" in result.output
        assert "web-portal" in result.output

    def test_malformed_project_schema_fails_closed(self, tmp_path: Path, monkeypatch):
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "web-build.yaml").write_text("name: web-build\nsnyc: {}\n")
        data = tmp_path / "data"
        data.mkdir()
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, ["detect", str(data)])

        assert result.exit_code != 0
        assert "snyc" in result.output
        assert "Extra inputs are not permitted" in result.output

    def test_detect_empty_dir(self, tmp_path):
        result = CliRunner().invoke(main, ["detect", str(tmp_path)])
        assert result.exit_code == 0
        assert "No known data types detected." in result.output

    def test_detect_missing_path_exits_nonzero(self, tmp_path):
        result = CliRunner().invoke(main, ["detect", str(tmp_path / "nope")])
        assert result.exit_code != 0


class TestValidate:
    def test_validate_passes(self, tmp_path):
        unit = _valid_photo_unit(tmp_path)
        result = CliRunner().invoke(main, ["validate", str(unit), "--schema", "photo-import"])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output

    def test_validate_fails_with_error_line(self, tmp_path):
        # Empty dir: missing required dir, below min size, below min file count.
        unit = tmp_path / "empty_unit"
        unit.mkdir()
        result = CliRunner().invoke(main, ["validate", str(unit), "--schema", "photo-import"])
        assert result.exit_code != 0
        assert "error:" in result.output
        assert "received: missing" in result.output
        assert "expected: MediaLibrary/RawPhotos" in result.output
        assert "generated examples:" in result.output
        assert "- MediaLibrary/RawPhotos" in result.output

    def test_validate_filename_warning_shows_generated_regex_examples(self, tmp_path: Path):
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "bad.txt").write_text("data")
        schema_path = tmp_path / "schema.yaml"
        schema_path.write_text('name: test\nvalidate:\n  filename_pattern: "^good\\\\.csv$"\n')

        result = CliRunner().invoke(main, ["validate", str(unit), "--schema", str(schema_path)])

        assert result.exit_code == 0, result.output
        assert "warning: Filename 'bad.txt'" in result.output
        assert "received: bad.txt" in result.output
        assert r"expected: ^good\.csv$" in result.output
        assert "generated examples:" in result.output
        assert "- good.csv" in result.output

    def test_validate_unknown_schema_exits_nonzero(self, tmp_path):
        unit = _valid_photo_unit(tmp_path)
        result = CliRunner().invoke(main, ["validate", str(unit), "--schema", "no-such-schema"])
        assert result.exit_code != 0
