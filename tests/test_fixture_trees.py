"""Verify the generic example schemas against the committed fixture trees.

Unlike the other test modules, which build trees in ``tmp_path``, these
tests run detection and validation against the real folder structures
committed under ``tests/fixtures/`` — one valid and one invalid tree per
fixture-backed schema (see ``tests/fixtures/README.md``).
"""

from pathlib import Path

import pytest

from atlas.detect import detect
from atlas.schema import load_all_schemas
from atlas.validate import validate_data_unit

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMAS = {schema.name: schema for schema in load_all_schemas()}

SCHEMA_NAMES = [
    "csv-dataset",
    "monorepo-build",
    "photo-import",
    "report-bundle",
    "site-archive",
    "web-build",
]

# report-bundle has no detection block, so it can never be detected.
DETECTABLE_NAMES = [name for name in SCHEMA_NAMES if name != "report-bundle"]

# Each invalid tree fails a different validation check; map fixture name to
# the substrings its error list must contain.
EXPECTED_INVALID_ERRORS = {
    "csv-dataset": ["Pipeline failure marker detected: _errors"],
    "monorepo-build": ["Pipeline failure marker detected: _errors"],
    "photo-import": [
        "Total size 0.00 MB is below minimum of 0.1 MB",
        "File count 0 is below minimum of 1",
    ],
    "report-bundle": ["Missing required file: report.pdf"],
    "site-archive": ["is below minimum of 10.0 MB"],
    "web-build": ["Pipeline failure marker detected: build.error"],
}


class TestValidFixtureTrees:
    @pytest.mark.parametrize("name", SCHEMA_NAMES)
    def test_validates_clean(self, name):
        result = validate_data_unit(FIXTURES / "valid" / name, SCHEMAS[name])
        assert result.errors == []
        assert result.warnings == []
        assert result.passed is True

    @pytest.mark.parametrize("name", SCHEMA_NAMES)
    def test_has_sync_files(self, name):
        result = validate_data_unit(FIXTURES / "valid" / name, SCHEMAS[name])
        assert result.sync_files, "a valid unit must have post-filter files"


class TestInvalidFixtureTrees:
    @pytest.mark.parametrize("name", SCHEMA_NAMES)
    def test_fails_validation(self, name):
        result = validate_data_unit(FIXTURES / "invalid" / name, SCHEMAS[name])
        assert result.passed is False

    @pytest.mark.parametrize(("name", "expected"), sorted(EXPECTED_INVALID_ERRORS.items()))
    def test_fails_for_the_intended_reason(self, name, expected):
        result = validate_data_unit(FIXTURES / "invalid" / name, SCHEMAS[name])
        for substring in expected:
            assert any(substring in error for error in result.errors), (
                f"expected an error containing {substring!r}, got {result.errors}"
            )
        # No unintended failures: every deliberate defect is accounted for.
        assert len(result.errors) == len(expected)


class TestDetectionOverFixtures:
    def test_valid_trees_detect_exactly_once_each(self):
        found = detect(FIXTURES / "valid")
        by_name = {d.schema_name: d for d in found}
        assert sorted(by_name) == DETECTABLE_NAMES
        assert len(found) == len(DETECTABLE_NAMES)
        for name, detection in by_name.items():
            assert detection.stage_path == (FIXTURES / "valid").resolve()
            assert detection.unit_ids == [name]

    def test_monorepo_guards_suppress_web_build(self):
        # The monorepo fixture contains a dist/ tree and a _buildmeta that
        # records `bundler workspace`; web-build must not claim it.
        found = detect(FIXTURES / "valid" / "monorepo-build")
        assert [d.schema_name for d in found] == ["monorepo-build"]

    def test_invalid_trees_detection(self):
        # photo-import is undetectable (require_any_glob finds no .jpg);
        # report-bundle is never detectable.  The rest keep their detection
        # markers and fail only at validation time.
        found = detect(FIXTURES / "invalid")
        names = sorted(d.schema_name for d in found)
        assert names == ["csv-dataset", "monorepo-build", "site-archive", "web-build"]
