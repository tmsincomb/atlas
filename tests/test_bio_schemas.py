"""Synthetic detection and validation coverage for the bioinformatics schemas."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from atlas.detect import detect
from atlas.schema import Schema, resolve_schema
from atlas.validate import validate_data_unit

MIB = 1024 * 1024


def _sparse(path: Path, size: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def _text(path: Path, content: str = "data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _bcl_demux(root: Path) -> Path:
    unit = root / "demux" / "run-a"
    (unit / "Reports").mkdir(parents=True)
    (unit / "Logs").mkdir()
    _text(unit / "Reports" / "report.html")
    _text(unit / "Stats" / "DemultiplexingStats.xml")
    _sparse(unit / "sample" / "Sample_S1_L001_R1_001.fastq.gz", 11 * MIB)
    _text(unit / "sample" / "Undetermined_S0_L001_R1_001.fastq.gz")
    return unit


def _cellranger_count(root: Path) -> Path:
    unit = root / "count" / "sample-a"
    _text(unit / "_cmdline", "cellranger count")
    _sparse(unit / "outs" / "filtered_feature_bc_matrix.h5", 2 * MIB)
    for name in ("raw_feature_bc_matrix.h5", "web_summary.html", "metrics_summary.csv", "possorted_genome_bam.bam"):
        _text(unit / "outs" / name)
    return unit


def _cellranger_multi(root: Path, *, legacy: bool) -> Path:
    unit = root / ("multi-legacy" if legacy else "multi-current") / "sample-a"
    (unit / "SC_MULTI_CS").mkdir(parents=True)
    _text(unit / "_cmdline", "cellranger multi")
    _text(unit / "outs" / "config.csv")
    matrix = (
        unit / "outs" / "filtered_feature_bc_matrix.h5"
        if legacy
        else unit / "outs" / "per_sample_outs" / "sample-a" / "count" / "sample_filtered_feature_bc_matrix.h5"
    )
    _sparse(matrix, 2 * MIB)
    for index in range(8):
        _text(unit / "outs" / "support" / f"part-{index}.txt")
    return unit


def _illumina_bcl_run(root: Path) -> Path:
    unit = root / "illumina" / "run-a"
    _text(unit / "RunInfo.xml")
    _text(unit / "RunParameters.xml")
    _text(unit / "RTAComplete.txt")
    _sparse(unit / "Data" / "Intensities" / "BaseCalls" / "L001" / "C1.1", 101 * MIB)
    _text(unit / "InterOp" / "TileMetricsOut.bin")
    for index in range(6):
        _text(unit / "Data" / "Intensities" / "BaseCalls" / f"support-{index}.bin")
    return unit


def _schema(name: str, project_root: Path) -> Schema:
    return resolve_schema(name, project_root)


@pytest.mark.parametrize(
    ("schema_name", "builder"),
    [
        ("10x-bcl-demux", _bcl_demux),
        ("10x-cellranger-count", _cellranger_count),
        ("10x-cellranger-multi", lambda root: _cellranger_multi(root, legacy=False)),
        ("illumina-bcl-run", _illumina_bcl_run),
    ],
)
def test_bio_schema_detects_and_validates_cleanly(
    tmp_path: Path, schema_name: str, builder: Callable[[Path], Path]
) -> None:
    unit = builder(tmp_path)
    schema = _schema(schema_name, tmp_path)

    detections = detect(tmp_path, schemas=[schema])
    result = validate_data_unit(unit, schema)

    assert len(detections) == 1
    assert detections[0].schema_name == schema_name
    assert detections[0].stage_path == unit.parent.resolve()
    assert detections[0].unit_ids == [unit.name]
    assert result.errors == []
    assert result.warnings == []
    assert result.passed is True


def test_bcl_demux_excludes_undetermined_fastq(tmp_path: Path) -> None:
    unit = _bcl_demux(tmp_path)
    result = validate_data_unit(unit, _schema("10x-bcl-demux", tmp_path))

    assert all("Undetermined_" not in path.name for path in result.sync_files)


def test_legacy_multi_validates_and_is_not_cross_detected_as_count(tmp_path: Path) -> None:
    unit = _cellranger_multi(tmp_path, legacy=True)
    count = _schema("10x-cellranger-count", tmp_path)
    multi = _schema("10x-cellranger-multi", tmp_path)

    detections = detect(tmp_path, schemas=[count, multi])
    result = validate_data_unit(unit, multi)

    assert [detection.schema_name for detection in detections] == ["10x-cellranger-multi"]
    assert detections[0].unit_ids == [unit.name]
    assert result.errors == []
    assert result.warnings == []
    assert result.passed is True
