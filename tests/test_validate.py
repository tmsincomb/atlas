"""Tests for atlas validate module."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.schema import Schema, SyncConfig, ValidateConfig, resolve_schema
from atlas.validate import RuleResult, ValidationResult, validate_data_unit


class TestValidationResult:
    """Tests for the ValidationResult model."""

    def test_no_errors_passes(self):
        """ValidationResult with no errors has passed=True."""
        result = ValidationResult()
        assert result.passed is True
        assert result.errors == []
        assert result.warnings == []

    def test_errors_fail(self):
        """ValidationResult with errors has passed=False."""
        result = ValidationResult(errors=["missing file"])
        assert result.passed is False

    def test_warnings_only_still_pass(self):
        """ValidationResult with only warnings has passed=True."""
        result = ValidationResult(warnings=["optional file missing"])
        assert result.passed is True

    def test_errors_and_warnings(self):
        """ValidationResult with both errors and warnings has passed=False."""
        result = ValidationResult(
            errors=["missing required file"],
            warnings=["optional file missing"],
        )
        assert result.passed is False


class TestSchemaNull:
    """Tests for schema=None (null schema) behaviour."""

    def test_none_schema_returns_pass(self, tmp_path: Path):
        """Schema=None returns pass with no checks."""
        result = validate_data_unit(tmp_path, schema=None)
        assert result.passed is True
        assert result.errors == []
        assert result.warnings == []

    def test_no_validate_section_returns_pass(self, tmp_path: Path):
        """Schema with no validate section still returns its sync inventory."""
        (tmp_path / "keep.txt").write_text("data")
        (tmp_path / "skip.tmp").write_text("temporary")
        schema = Schema(name="no-validate", sync=SyncConfig(include=["**/*"], exclude=["*.tmp"]))
        assert schema.validation is None
        result = validate_data_unit(tmp_path, schema)
        assert result.passed is True
        assert result.errors == []
        assert result.warnings == []
        assert [path.name for path in result.sync_files] == ["keep.txt"]


class TestRequiredFiles:
    """Tests for required file validation."""

    def test_missing_required_file_fails(self, tmp_path: Path):
        """Missing required files produce FAIL with each file listed."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(required=["dist/config.json"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert len(result.errors) == 1
        assert "dist/config.json" in result.errors[0]

    def test_existing_required_file_passes(self, tmp_path: Path):
        """Existing required files do not produce errors."""
        unit = tmp_path / "unit"
        (unit / "dist").mkdir(parents=True)
        (unit / "dist" / "config.json").write_text("data")

        schema = Schema(
            name="test",
            validate=ValidateConfig(required=["dist/config.json"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True

    def test_multiple_missing_required_files(self, tmp_path: Path):
        """All missing required files are listed, not just the first."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(required=["dist/config.json", "dist/bundle.js", "dist/report.html"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert len(result.errors) == 3
        assert any("dist/config.json" in e for e in result.errors)
        assert any("dist/bundle.js" in e for e in result.errors)
        assert any("dist/report.html" in e for e in result.errors)

    def test_partial_required_files(self, tmp_path: Path):
        """Only missing required files produce errors."""
        unit = tmp_path / "unit"
        (unit / "dist").mkdir(parents=True)
        (unit / "dist" / "config.json").write_text("data")

        schema = Schema(
            name="test",
            validate=ValidateConfig(required=["dist/config.json", "dist/missing.js"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert len(result.errors) == 1
        assert "dist/missing.js" in result.errors[0]


class TestRequiredDirs:
    """Tests for required directory validation."""

    def test_missing_required_dir_fails(self, tmp_path: Path):
        """Missing required directories produce FAIL with each listed."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(required_dirs=["dist/packages"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert len(result.errors) == 1
        assert "dist/packages" in result.errors[0]

    def test_existing_required_dir_passes(self, tmp_path: Path):
        """Existing required directories do not produce errors."""
        unit = tmp_path / "unit"
        (unit / "dist" / "packages").mkdir(parents=True)

        schema = Schema(
            name="test",
            validate=ValidateConfig(required_dirs=["dist/packages"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True

    def test_multiple_missing_dirs(self, tmp_path: Path):
        """All missing required dirs are listed."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(required_dirs=["dist/packages", "dist/assets"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert len(result.errors) == 2


class TestFailOn:
    """Tests for fail_on pattern matching."""

    def test_fail_on_pattern_match(self, tmp_path: Path):
        """Files matching fail_on glob patterns cause FAIL."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "_errors").write_text("error log")

        schema = Schema(
            name="test",
            validate=ValidateConfig(fail_on=["_errors"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert len(result.errors) == 1
        assert "_errors" in result.errors[0]

    def test_fail_on_glob_pattern(self, tmp_path: Path):
        """fail_on with glob pattern matches files."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "pipeline.error").write_text("error")

        schema = Schema(
            name="test",
            validate=ValidateConfig(fail_on=["*.error"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert "*.error" in result.errors[0] or "pipeline.error" in result.errors[0]

    def test_fail_on_no_match_passes(self, tmp_path: Path):
        """No matching fail_on patterns means no error."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "good_file.txt").write_text("ok")

        schema = Schema(
            name="test",
            validate=ValidateConfig(fail_on=["_errors", "*.error"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True

    def test_fail_on_multiple_patterns(self, tmp_path: Path):
        """Multiple fail_on patterns each produce separate errors."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "_errors").write_text("log")
        (unit / "run.error").write_text("err")

        schema = Schema(
            name="test",
            validate=ValidateConfig(fail_on=["_errors", "*.error"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert len(result.errors) >= 2

    def test_fail_on_hint_message(self, tmp_path: Path):
        """fail_on errors include helpful hints."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "_errors").write_text("failure")

        schema = Schema(
            name="test",
            validate=ValidateConfig(fail_on=["_errors"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        error_msg = result.errors[0].lower()
        assert "pipeline" in error_msg or "fail" in error_msg or "error" in error_msg

    def test_fail_on_matches_are_sorted(self, tmp_path: Path):
        unit = tmp_path / "unit"
        unit.mkdir()
        for name in ("z.error", "a.error"):
            (unit / name).write_text("error")

        schema = Schema(name="test", validate=ValidateConfig(fail_on=["*.error"]))
        result = validate_data_unit(unit, schema)
        assert "a.error, z.error" in result.errors[0]


class TestSizeBounds:
    """Tests for size bounds validation (uses post-filter files)."""

    def _make_unit_with_files(self, tmp_path: Path, file_sizes: dict[str, int]) -> Path:
        """Create a unit with files of specific sizes."""
        unit = tmp_path / "unit"
        unit.mkdir()
        for name, size in file_sizes.items():
            parts = name.rsplit("/", 1)
            if len(parts) == 2:
                (unit / parts[0]).mkdir(parents=True, exist_ok=True)
            (unit / name).write_bytes(b"\0" * size)
        return unit

    def test_below_min_size_fails(self, tmp_path: Path):
        """Total size below min_size_mb fails with actual size and threshold."""
        unit = self._make_unit_with_files(tmp_path, {"small.txt": 100})

        schema = Schema(
            name="test",
            validate=ValidateConfig(min_size_mb=1.0),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert len(result.errors) == 1
        assert "min" in result.errors[0].lower() or "size" in result.errors[0].lower()

    def test_above_max_size_fails(self, tmp_path: Path):
        """Total size above max_size_gb fails with actual size and threshold."""
        # Use a very small max to trigger easily
        unit = self._make_unit_with_files(tmp_path, {"big.txt": 1024 * 1024})

        schema = Schema(
            name="test",
            validate=ValidateConfig(max_size_gb=0.0001),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert len(result.errors) == 1
        assert "max" in result.errors[0].lower() or "size" in result.errors[0].lower()

    def test_within_size_bounds_passes(self, tmp_path: Path):
        """Total size within bounds produces no errors."""
        unit = self._make_unit_with_files(tmp_path, {"file.txt": 2 * 1024 * 1024})

        schema = Schema(
            name="test",
            validate=ValidateConfig(min_size_mb=1.0, max_size_gb=1.0),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True

    def test_size_uses_post_filter_files(self, tmp_path: Path):
        """VAL-SCHEMA-004: Size bounds are computed against post-filter files."""
        unit = tmp_path / "unit"
        dist = unit / "dist"
        dist.mkdir(parents=True)
        (dist / "small.txt").write_bytes(b"\0" * (2 * 1024 * 1024))

        # Large excluded file
        node_modules = unit / "node_modules"
        node_modules.mkdir()
        (node_modules / "huge.dat").write_bytes(b"\0" * (10 * 1024 * 1024))

        schema = Schema(
            name="test",
            sync=SyncConfig(include=["dist/**"], exclude=["node_modules/**"]),
            validate=ValidateConfig(min_size_mb=1.0, max_size_gb=0.005),
        )
        result = validate_data_unit(unit, schema)
        # Included files only: 2MB, within [1MB, 5MB]
        assert result.passed is True

    def test_size_error_shows_actual_and_threshold(self, tmp_path: Path):
        """Size error messages include the actual size and the threshold."""
        unit = self._make_unit_with_files(tmp_path, {"tiny.txt": 10})

        schema = Schema(
            name="test",
            validate=ValidateConfig(min_size_mb=100.0),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        # Should mention both the threshold and actual
        error = result.errors[0]
        assert "100" in error or "min" in error.lower()

    def test_no_size_bounds_skips_check(self, tmp_path: Path):
        """When min/max size are None, size check is skipped."""
        unit = self._make_unit_with_files(tmp_path, {"file.txt": 1})

        schema = Schema(
            name="test",
            validate=ValidateConfig(min_size_mb=None, max_size_gb=None),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True


class TestFileCountBounds:
    """Tests for file count bounds validation (uses post-filter files)."""

    def test_below_min_count_fails(self, tmp_path: Path):
        """File count below min_file_count fails."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "only_one.txt").write_text("data")

        schema = Schema(
            name="test",
            validate=ValidateConfig(min_file_count=5),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert any("count" in e.lower() or "file" in e.lower() for e in result.errors)

    def test_above_max_count_fails(self, tmp_path: Path):
        """File count above max_file_count fails."""
        unit = tmp_path / "unit"
        unit.mkdir()
        for i in range(10):
            (unit / f"file_{i}.txt").write_text(f"data {i}")

        schema = Schema(
            name="test",
            validate=ValidateConfig(max_file_count=3),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False

    def test_within_count_bounds_passes(self, tmp_path: Path):
        """File count within bounds produces no errors."""
        unit = tmp_path / "unit"
        unit.mkdir()
        for i in range(5):
            (unit / f"file_{i}.txt").write_text(f"data {i}")

        schema = Schema(
            name="test",
            validate=ValidateConfig(min_file_count=3, max_file_count=10),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True

    def test_count_uses_post_filter_files(self, tmp_path: Path):
        """VAL-SCHEMA-004: File count uses post-filter file list."""
        unit = tmp_path / "unit"
        dist = unit / "dist"
        dist.mkdir(parents=True)
        for i in range(5):
            (dist / f"file_{i}.txt").write_text(f"data {i}")

        # Excluded files (should not count)
        node_modules = unit / "node_modules"
        node_modules.mkdir()
        for i in range(20):
            (node_modules / f"excluded_{i}.txt").write_text(f"data {i}")

        schema = Schema(
            name="test",
            sync=SyncConfig(include=["dist/**"], exclude=["node_modules/**"]),
            validate=ValidateConfig(min_file_count=3, max_file_count=10),
        )
        result = validate_data_unit(unit, schema)
        # Only 5 included files, within [3, 10]
        assert result.passed is True

    def test_count_error_shows_actual_and_threshold(self, tmp_path: Path):
        """File count error messages include actual count and threshold."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "one.txt").write_text("data")

        schema = Schema(
            name="test",
            validate=ValidateConfig(min_file_count=10),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        error = result.errors[0]
        assert "1" in error and "10" in error

    def test_no_count_bounds_skips_check(self, tmp_path: Path):
        """When min/max file count are None, count check is skipped."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "file.txt").write_text("data")

        schema = Schema(
            name="test",
            validate=ValidateConfig(min_file_count=None, max_file_count=None),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True


class TestWarnIfMissing:
    """Tests for warn_if_missing validation."""

    def test_missing_optional_file_warns(self, tmp_path: Path):
        """Missing warn_if_missing files produce WARN, not FAIL."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(warn_if_missing=["dist/optional.json"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True
        assert len(result.warnings) == 1
        assert "dist/optional.json" in result.warnings[0]

    def test_existing_optional_file_no_warning(self, tmp_path: Path):
        """Existing warn_if_missing files produce no warnings."""
        unit = tmp_path / "unit"
        (unit / "dist").mkdir(parents=True)
        (unit / "dist" / "optional.json").write_text("data")

        schema = Schema(
            name="test",
            validate=ValidateConfig(warn_if_missing=["dist/optional.json"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True
        assert result.warnings == []

    def test_multiple_missing_optional_files(self, tmp_path: Path):
        """All missing optional files listed as warnings."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(warn_if_missing=["opt1.json", "opt2.json"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True
        assert len(result.warnings) == 2


class TestRequiredAny:
    """Alternative layouts can satisfy one grouped required rule."""

    @pytest.mark.parametrize("path", ["dist/bundle.js", "dist/packages/core/bundle.js"])
    def test_either_alternative_passes(self, tmp_path: Path, path: str):
        unit = tmp_path / "unit"
        target = unit / path
        target.parent.mkdir(parents=True)
        target.write_text("bundle")
        schema = Schema(
            name="test",
            validate=ValidateConfig(required_any=["dist/bundle.js", "dist/packages/*/bundle.js"]),
        )

        result = validate_data_unit(unit, schema)

        assert result.errors == []
        assert result.checks[0].rule_id == "required_any"
        assert result.checks[0].severity == "ok"

    def test_no_alternative_fails_once(self, tmp_path: Path):
        unit = tmp_path / "unit"
        unit.mkdir()
        schema = Schema(
            name="test",
            validate=ValidateConfig(required_any=["dist/bundle.js", "dist/packages/*/bundle.js"]),
        )

        result = validate_data_unit(unit, schema)

        assert result.errors == [
            "Missing required alternative (expected at least one of: dist/bundle.js, dist/packages/*/bundle.js)"
        ]
        assert result.checks[0].actual == "no matches"

    @pytest.mark.parametrize(
        "config",
        [
            ValidateConfig(required=["report.pdf"]),
            ValidateConfig(required_any=["*.pdf"]),
        ],
    )
    def test_symlink_target_outside_unit_does_not_satisfy_required_rule(self, tmp_path: Path, config: ValidateConfig):
        outside = tmp_path / "outside.pdf"
        outside.write_text("external")
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "report.pdf").symlink_to(outside)

        result = validate_data_unit(unit, Schema(name="test", validate=config))

        assert result.passed is False
        assert result.sync_files == []


class TestRequiredPathContainment:
    def test_symlink_loop_returns_structured_failure(self, tmp_path: Path):
        unit = tmp_path / "loop"
        unit.symlink_to(unit)

        result = validate_data_unit(
            unit,
            Schema(name="test", validate=ValidateConfig(required=["report.pdf"])),
        )

        assert result.errors == ["Missing required file: report.pdf"]
        assert result.sync_files == []

    def test_symlinked_required_directory_outside_unit_fails(self, tmp_path: Path):
        outside = tmp_path / "outside-dir"
        outside.mkdir()
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "data").symlink_to(outside, target_is_directory=True)

        result = validate_data_unit(
            unit,
            Schema(name="test", validate=ValidateConfig(required_dirs=["data"])),
        )

        assert result.passed is False
        assert result.sync_files == []


class TestFilenamePattern:
    """Tests for filename_pattern regex validation."""

    def test_mismatch_warns_not_fails(self, tmp_path: Path):
        """Filename pattern mismatch produces WARN, not FAIL."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "bad-name.txt").write_text("data")

        schema = Schema(
            name="test",
            validate=ValidateConfig(filename_pattern=r"^[A-Z]{2}-[A-Z]{2}-[A-Z0-9]+$"),
        )
        result = validate_data_unit(unit, schema)
        # Should warn but still pass
        assert result.passed is True
        assert len(result.warnings) >= 1

    def test_matching_pattern_no_warning(self, tmp_path: Path):
        """Filenames matching the pattern produce no warnings."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "AB-CD-01").write_text("data")

        schema = Schema(
            name="test",
            validate=ValidateConfig(filename_pattern=r"^AB-CD-01$"),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True
        assert result.warnings == []

    def test_null_pattern_skips(self, tmp_path: Path):
        """filename_pattern=None skips the check."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "any-name.txt").write_text("data")

        schema = Schema(
            name="test",
            validate=ValidateConfig(filename_pattern=None),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True
        assert result.warnings == []

    def test_pattern_checks_post_filter_files(self, tmp_path: Path):
        """Filename pattern applies to post-filter files only."""
        unit = tmp_path / "unit"
        dist = unit / "dist"
        dist.mkdir(parents=True)
        (dist / "good_file.csv").write_text("data")

        # Excluded file with bad name
        node_modules = unit / "node_modules"
        node_modules.mkdir()
        (node_modules / "bad_pipeline_file.py").write_text("code")

        schema = Schema(
            name="test",
            sync=SyncConfig(include=["dist/**"], exclude=["node_modules/**"]),
            validate=ValidateConfig(filename_pattern=r"^.*\.csv$"),
        )
        result = validate_data_unit(unit, schema)
        # Only dist/good_file.csv is checked (post-filter), and it matches
        assert result.passed is True
        assert result.warnings == []


class TestPhotoImportFilenamePattern:
    """Regression tests: the photo-import filename_pattern accepts images, video, and PDFs."""

    def _photo_unit(self, tmp_path: Path) -> Path:
        unit = tmp_path / "shoot1"
        raw = unit / "MediaLibrary" / "RawPhotos"
        raw.mkdir(parents=True)
        return unit

    def _pattern_warnings(self, result: ValidationResult) -> list[str]:
        return [w for w in result.warnings if "filename" in w.lower() and "pattern" in w.lower()]

    def test_photo_import_pdf_no_warning(self, tmp_path: Path):
        """PDF contact-sheet files do not trigger filename_pattern warnings."""
        from atlas.schema import resolve_schema

        schema = resolve_schema("photo-import", tmp_path)
        unit = self._photo_unit(tmp_path)
        raw = unit / "MediaLibrary" / "RawPhotos"
        (raw / "IMG_0001.jpg").write_text("JPG data")
        (raw / "contact_sheet.pdf").write_text("PDF")

        result = validate_data_unit(unit, schema)
        assert self._pattern_warnings(result) == []

    def test_photo_import_video_no_warning(self, tmp_path: Path):
        """MOV video files do not trigger filename_pattern warnings."""
        from atlas.schema import resolve_schema

        schema = resolve_schema("photo-import", tmp_path)
        unit = self._photo_unit(tmp_path)
        raw = unit / "MediaLibrary" / "RawPhotos"
        (raw / "IMG_0001.jpg").write_text("JPG data")
        (raw / "clip.mov").write_text("MOV data")

        result = validate_data_unit(unit, schema)
        assert self._pattern_warnings(result) == []

    def test_photo_import_unexpected_type_warns(self, tmp_path: Path):
        """Unexpected file types still trigger filename_pattern warnings."""
        from atlas.schema import resolve_schema

        schema = resolve_schema("photo-import", tmp_path)
        unit = self._photo_unit(tmp_path)
        raw = unit / "MediaLibrary" / "RawPhotos"
        (raw / "IMG_0001.jpg").write_text("JPG data")
        (raw / "readme.txt").write_text("text")

        result = validate_data_unit(unit, schema)
        warnings = self._pattern_warnings(result)
        assert len(warnings) == 1
        assert "readme.txt" in warnings[0]

    def test_photo_import_mixed_files_only_unknown_warns(self, tmp_path: Path):
        """Only unexpected file types warn; JPG, PNG, and PDF are all accepted."""
        from atlas.schema import resolve_schema

        schema = resolve_schema("photo-import", tmp_path)
        unit = self._photo_unit(tmp_path)
        raw = unit / "MediaLibrary" / "RawPhotos"
        (raw / "IMG_0001.jpg").write_text("JPG data")
        (raw / "IMG_0002.png").write_text("PNG data")
        (raw / "contact_sheet.pdf").write_text("PDF")
        (raw / "notes.docx").write_text("doc")

        result = validate_data_unit(unit, schema)
        warnings = self._pattern_warnings(result)
        assert len(warnings) == 1
        assert "notes.docx" in warnings[0]


class TestAllChecksCollected:
    """Tests that all errors are collected (not fail-fast)."""

    def test_multiple_check_types_all_collected(self, tmp_path: Path):
        """Errors from multiple check types are all collected."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "_errors").write_text("pipeline failure")

        schema = Schema(
            name="test",
            validate=ValidateConfig(
                required=["dist/config.json"],
                required_dirs=["dist/packages"],
                fail_on=["_errors"],
                min_file_count=100,
            ),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        # Should have errors from: required file, required dir, fail_on, min_file_count
        assert len(result.errors) >= 4

    def test_errors_and_warnings_coexist(self, tmp_path: Path):
        """Both errors and warnings are collected."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(
                required=["dist/config.json"],
                warn_if_missing=["dist/optional.json"],
            ),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert len(result.errors) == 1
        assert len(result.warnings) == 1


class TestHelpfulHints:
    """Tests that error messages include helpful hints."""

    def test_required_file_hint(self, tmp_path: Path):
        """Required file error includes helpful context."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(required=["dist/config.json"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        # Error should be descriptive
        error = result.errors[0]
        assert "required" in error.lower() or "missing" in error.lower()

    def test_fail_on_pipeline_hint(self, tmp_path: Path):
        """fail_on error mentions pipeline failure possibility."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "_errors").write_text("error log")

        schema = Schema(
            name="test",
            validate=ValidateConfig(fail_on=["_errors"]),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
        error = result.errors[0]
        assert "pipeline" in error.lower() or "fail" in error.lower() or "error" in error.lower()


class TestBuiltinSchemaValidation:
    """Tests using the built-in monorepo-build schema."""

    def _create_valid_unit(self, tmp_path: Path) -> Path:
        """Create a unit that passes validation for monorepo-build."""
        unit = tmp_path / "web-portal"
        dist = unit / "dist"
        dist.mkdir(parents=True)
        (dist / "config.json").write_text("config data")

        core = dist / "packages" / "core"
        core.mkdir(parents=True)
        # >1 MB so the size floor passes.
        (core / "bundle.js").write_bytes(b"\0" * (1024 * 1024))
        (core / "manifest.json").write_text("{}")

        # Flat-layout legacy bundle (a warn_if_missing target).
        (dist / "bundle.js").write_bytes(b"\0" * (256 * 1024))

        # Enough extra files to comfortably exceed min_file_count of 3.
        for i in range(5):
            (dist / f"chunk_{i}.js").write_bytes(b"\0" * (50 * 1024))

        return unit

    def test_valid_unit_passes(self, tmp_path: Path):
        """A complete monorepo-build unit passes validation."""
        from atlas.schema import resolve_schema

        schema = resolve_schema("monorepo-build", tmp_path)
        unit = self._create_valid_unit(tmp_path)

        result = validate_data_unit(unit, schema)
        assert result.passed is True, f"Expected PASS but got errors: {result.errors}"

    def test_missing_required_file_fails(self, tmp_path: Path):
        """monorepo-build fails when required file is missing."""
        from atlas.schema import resolve_schema

        schema = resolve_schema("monorepo-build", tmp_path)
        unit = self._create_valid_unit(tmp_path)

        # Remove a required file
        (unit / "dist" / "config.json").unlink()

        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert any("config.json" in e for e in result.errors)

    def test_pipeline_failure_marker(self, tmp_path: Path):
        """monorepo-build fails when _errors file present."""
        from atlas.schema import resolve_schema

        schema = resolve_schema("monorepo-build", tmp_path)
        unit = self._create_valid_unit(tmp_path)

        # Add a failure marker
        (unit / "_errors").write_text("pipeline crashed")

        result = validate_data_unit(unit, schema)
        assert result.passed is False
        assert any("_errors" in e for e in result.errors)

    def test_per_package_layout_passes_without_flat_bundle(self, tmp_path: Path):
        """The per-package layout does not require or warn about the flat layout."""
        from atlas.schema import resolve_schema

        schema = resolve_schema("monorepo-build", tmp_path)
        unit = self._create_valid_unit(tmp_path)

        (unit / "dist" / "bundle.js").unlink()

        result = validate_data_unit(unit, schema)
        assert result.passed is True
        assert result.warnings == []

    def test_flat_layout_passes_without_packages(self, tmp_path: Path):
        """The claimed flat layout works independently of dist/packages."""
        schema = resolve_schema("monorepo-build", tmp_path)
        unit = self._create_valid_unit(tmp_path)
        (unit / "dist" / "bundle.js").write_bytes(b"\0" * (2 * 1024 * 1024))
        packages = unit / "dist" / "packages"
        for path in sorted(packages.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        packages.rmdir()

        result = validate_data_unit(unit, schema)
        assert result.passed is True
        assert result.warnings == []

    def test_missing_both_layout_bundles_fails(self, tmp_path: Path):
        schema = resolve_schema("monorepo-build", tmp_path)
        unit = self._create_valid_unit(tmp_path)
        (unit / "dist" / "bundle.js").unlink()
        (unit / "dist" / "packages" / "core" / "bundle.js").unlink()

        result = validate_data_unit(unit, schema)
        assert any("Missing required alternative" in error for error in result.errors)


class TestMessageStringParity:
    """Locks the exact error/warning strings emitted by every check.

    These strings are consumed by forest and asserted verbatim so the
    structured-results refactor cannot drift them.
    """

    def test_all_failure_strings_verbatim(self, tmp_path: Path):
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "_errors").write_text("boom")
        (unit / "bad.txt").write_text("x")

        schema = Schema(
            name="test",
            validate=ValidateConfig(
                required=["dist/config.json"],
                required_dirs=["dist/packages"],
                fail_on=["_errors"],
                min_size_mb=1.0,
                min_file_count=5,
                warn_if_missing=["summary.md"],
                filename_pattern=r"^good\.csv$",
            ),
        )
        result = validate_data_unit(unit, schema)
        assert result.errors == [
            "Missing required file: dist/config.json",
            "Missing required directory: dist/packages",
            "Pipeline failure marker detected: _errors matched pattern '_errors' "
            "— pipeline may not have completed successfully",
            "Total size 0.00 MB is below minimum of 1.0 MB",
            "File count 2 is below minimum of 5",
        ]
        assert result.warnings == [
            "Optional file missing: summary.md",
            "Filename '_errors' does not match expected pattern '^good\\.csv$'",
            "Filename 'bad.txt' does not match expected pattern '^good\\.csv$'",
        ]

    def test_max_bound_strings_verbatim(self, tmp_path: Path):
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "a.bin").write_bytes(b"\0" * (1024 * 1024))
        (unit / "b.bin").write_bytes(b"\0" * 1024)

        schema = Schema(
            name="test",
            validate=ValidateConfig(max_size_gb=0.0001, max_file_count=1),
        )
        result = validate_data_unit(unit, schema)
        assert result.errors == [
            "Total size 0.0010 GB exceeds maximum of 0.0001 GB",
            "File count 2 exceeds maximum of 1",
        ]
        assert result.warnings == []


class TestStructuredChecks:
    """Tests for the structured RuleResult checklist on ValidationResult."""

    def _by_id(self, result: ValidationResult, rule_id: str) -> list[RuleResult]:
        return [c for c in result.checks if c.rule_id == rule_id]

    def test_every_declared_rule_emits_a_result(self, tmp_path: Path):
        """Passing rules emit ok results too, giving a complete checklist."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "report.pdf").write_text("data")
        (unit / "figures").mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(
                required=["report.pdf"],
                required_any=["report.pdf", "reports/*.pdf"],
                required_dirs=["figures"],
                fail_on=["_errors"],
                min_size_mb=0.0,
                max_size_gb=1.0,
                min_file_count=1,
                max_file_count=10,
                warn_if_missing=["summary.md"],
                filename_pattern=r".",
            ),
        )
        result = validate_data_unit(unit, schema)
        ids = [c.rule_id for c in result.checks]
        assert ids == [
            "required:report.pdf",
            "required_any",
            "required_dirs:figures",
            "fail_on:_errors",
            "size:min",
            "size:max",
            "file_count:min",
            "file_count:max",
            "warn_if_missing:summary.md",
            "filename_pattern",
        ]
        severities = {c.rule_id: c.severity for c in result.checks}
        assert severities["warn_if_missing:summary.md"] == "warn"
        assert all(s == "ok" for rid, s in severities.items() if rid != "warn_if_missing:summary.md")

    def test_undeclared_rules_emit_nothing(self, tmp_path: Path):
        """Rules absent from the config produce no checklist entries."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(name="test", validate=ValidateConfig(required=["a.txt"]))
        result = validate_data_unit(unit, schema)
        assert [c.rule_id for c in result.checks] == ["required:a.txt"]

    def test_fail_result_carries_expected_actual_and_message(self, tmp_path: Path):
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(name="test", validate=ValidateConfig(required=["report.pdf"]))
        result = validate_data_unit(unit, schema)
        (check,) = self._by_id(result, "required:report.pdf")
        assert check.rule == "required"
        assert check.severity == "fail"
        assert check.expected == "report.pdf"
        assert check.actual == "missing"
        assert check.message == "Missing required file: report.pdf"

    def test_ok_results_have_empty_message(self, tmp_path: Path):
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "report.pdf").write_text("data")

        schema = Schema(name="test", validate=ValidateConfig(required=["report.pdf"]))
        result = validate_data_unit(unit, schema)
        (check,) = self._by_id(result, "required:report.pdf")
        assert check.severity == "ok"
        assert check.actual == "present"
        assert check.message == ""

    def test_bound_results_show_expected_vs_actual(self, tmp_path: Path):
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "f.txt").write_text("x")

        schema = Schema(name="test", validate=ValidateConfig(min_size_mb=1.0, min_file_count=5))
        result = validate_data_unit(unit, schema)
        (size,) = self._by_id(result, "size:min")
        assert size.expected == ">= 1.0 MB"
        assert size.actual == "0.00 MB"
        (count,) = self._by_id(result, "file_count:min")
        assert count.expected == ">= 5 files"
        assert count.actual == "1 files"

    def test_filename_pattern_one_result_per_violation(self, tmp_path: Path):
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "bad_one.txt").write_text("x")
        (unit / "bad_two.txt").write_text("x")

        schema = Schema(name="test", validate=ValidateConfig(filename_pattern=r"^good\.csv$"))
        result = validate_data_unit(unit, schema)
        violations = self._by_id(result, "filename_pattern")
        assert [v.actual for v in violations] == ["bad_one.txt", "bad_two.txt"]
        assert all(v.severity == "warn" for v in violations)
        assert all(v.examples == ["good.csv"] for v in violations)

    def test_derived_lists_match_checks(self, tmp_path: Path):
        """errors/warnings are exactly the fail/warn messages from checks, in order."""
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "_errors").write_text("boom")

        schema = Schema(
            name="test",
            validate=ValidateConfig(
                required=["report.pdf"],
                fail_on=["_errors"],
                warn_if_missing=["summary.md"],
            ),
        )
        result = validate_data_unit(unit, schema)
        assert result.errors == [c.message for c in result.checks if c.severity == "fail"]
        assert result.warnings == [c.message for c in result.checks if c.severity == "warn"]

    def test_no_schema_has_empty_checks(self, tmp_path: Path):
        assert validate_data_unit(tmp_path, None).checks == []


class TestEdgeCases:
    """Tests for edge cases."""

    def test_nonexistent_unit_path(self, tmp_path: Path):
        """Non-existent unit path produces errors for required items."""
        schema = Schema(
            name="test",
            validate=ValidateConfig(
                required=["file.txt"],
                required_dirs=["data"],
            ),
        )
        result = validate_data_unit(tmp_path / "nonexistent", schema)
        assert result.passed is False

    def test_empty_validate_config(self, tmp_path: Path):
        """Schema with empty validate section (all defaults) passes."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is True

    def test_project_root_parameter(self, tmp_path: Path):
        """project_root parameter is accepted (used for context)."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(),
        )
        result = validate_data_unit(unit, schema, project_root=tmp_path)
        assert result.passed is True

    def test_size_check_empty_file_list(self, tmp_path: Path):
        """Size check with no files (empty unit) and min_size_mb fails."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(min_size_mb=1.0),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False

    def test_count_check_empty_file_list(self, tmp_path: Path):
        """File count check with no files and min_file_count fails."""
        unit = tmp_path / "unit"
        unit.mkdir()

        schema = Schema(
            name="test",
            validate=ValidateConfig(min_file_count=1),
        )
        result = validate_data_unit(unit, schema)
        assert result.passed is False
