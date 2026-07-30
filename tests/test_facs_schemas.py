"""Regression coverage for the FACS layouts verified against G002 and G003."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.detect import detect
from atlas.schema import Schema, resolve_key_output, resolve_schema
from atlas.validate import validate_data_unit


@pytest.fixture
def facs_sort_schema(tmp_path: Path) -> Schema:
    return resolve_schema("facs-sort", tmp_path)


@pytest.fixture
def facs_sort_diva_schema(tmp_path: Path) -> Schema:
    return resolve_schema("facs-sort-diva", tmp_path)


def _write_payload(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"F" * (128 * 1024))


@pytest.mark.parametrize("raw_name", ["sample.fcs", "sample.zip"])
def test_melody_detects_and_validates_loose_or_archived_fcs(
    tmp_path: Path, facs_sort_schema: Schema, raw_name: str
) -> None:
    unit = tmp_path / "sorting" / "run-001"
    _write_payload(unit / "ClinicalSamples" / "DataFilesFromMelody" / raw_name)
    _write_payload(unit / "ClinicalSamples" / "SortReports" / "layout.pdf")

    detections = detect(tmp_path / "sorting", schemas=[facs_sort_schema])
    result = validate_data_unit(unit, facs_sort_schema)

    assert len(detections) == 1
    assert detections[0].stage_path == (tmp_path / "sorting").resolve()
    assert detections[0].unit_ids == ["run-001"]
    assert result.passed is True
    assert result.warnings == []
    assert resolve_key_output(facs_sort_schema, "sort_layouts", unit) == ["ClinicalSamples/SortReports/layout.pdf"]


def test_melody_rejects_a_unit_without_raw_data(tmp_path: Path, facs_sort_schema: Schema) -> None:
    unit = tmp_path / "run-001"
    (unit / "ClinicalSamples" / "DataFilesFromMelody").mkdir(parents=True)
    _write_payload(unit / "ClinicalSamples" / "SortReports" / "layout.pdf")

    result = validate_data_unit(unit, facs_sort_schema)

    assert result.passed is False
    assert any("Missing required alternative" in error for error in result.errors)


def test_diva_detects_and_validates_summary_only_runs(tmp_path: Path, facs_sort_diva_schema: Schema) -> None:
    unit = tmp_path / "sorting" / "run-001"
    _write_payload(unit / "ClinicalSamples" / "PopulationSummaryFilesFromDV" / "Sort_summary.csv")
    _write_payload(unit / "240101_FlowManifest.xlsx")

    detections = detect(tmp_path / "sorting", schemas=[facs_sort_diva_schema])
    result = validate_data_unit(unit, facs_sort_diva_schema)

    assert len(detections) == 1
    assert detections[0].stage_path == (tmp_path / "sorting").resolve()
    assert detections[0].unit_ids == ["run-001"]
    assert result.passed is True
    assert result.warnings == []
    assert {path.relative_to(unit).as_posix() for path in result.sync_files} == {
        "240101_FlowManifest.xlsx",
        "ClinicalSamples/PopulationSummaryFilesFromDV/Sort_summary.csv",
    }


def test_diva_warns_about_unexpected_extensionless_files(tmp_path: Path, facs_sort_diva_schema: Schema) -> None:
    unit = tmp_path / "run-001"
    _write_payload(unit / "ClinicalSamples" / "PopulationSummaryFilesFromDV" / "Sort_summary.csv")
    (unit / "ControlSamples" / "PopulationSummaryFilesFromDV").mkdir(parents=True)
    (unit / "ControlSamples" / "PopulationSummaryFilesFromDV" / "unexpected").write_text("artifact")

    result = validate_data_unit(unit, facs_sort_diva_schema)

    assert result.passed is True
    assert len(result.warnings) == 1
    assert "unexpected" in result.warnings[0]


def test_diva_declares_clinical_and_control_fcs_assets(facs_sort_diva_schema: Schema) -> None:
    records = {record.name: record for record in facs_sort_diva_schema.manifest.records}

    for record_type in ("clinical_fcs", "control_fcs"):
        record = records[record_type]
        assert record.groups == ["fcs"]
        assert record.tags == {"media_type": "application/vnd.isac.fcs"}
        assert record.asset_key is not None
        assert record.asset_key.field == "fcs_filename"
        assert record.asset_key.source == "filename"
