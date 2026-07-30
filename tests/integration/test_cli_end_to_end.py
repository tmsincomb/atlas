"""End-to-end tests driving the atlas CLI entry point (atlas.cli:main).

Builds a temp tree matching the ``photo-import`` built-in schema, then exercises
schemas / show / detect / validate through click's CliRunner — the real entry
point a human or agent would use.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from atlas.cli import main


def _build_photo_tree(root: Path) -> Path:
    """Create a photo-import unit under *root*; return the unit directory."""
    unit = root / "SHOOT_01"
    raw = unit / "MediaLibrary" / "RawPhotos"
    raw.mkdir(parents=True)
    # >0.1 MB so the size floor passes; .jpg so the filename pattern matches.
    (raw / "IMG_240101_0001.jpg").write_bytes(b"\0" * (256 * 1024))
    return unit


def test_schemas_lists_photo_import():
    result = CliRunner().invoke(main, ["schemas"])
    assert result.exit_code == 0, result.output
    assert "photo-import" in result.output


def test_show_photo_import():
    result = CliRunner().invoke(main, ["show", "photo-import"])
    assert result.exit_code == 0, result.output
    assert "name:        photo-import" in result.output
    assert "detection.markers:" in result.output
    assert "MediaLibrary/RawPhotos" in result.output


def test_detect_finds_photo_unit(tmp_path):
    _build_photo_tree(tmp_path)
    result = CliRunner().invoke(main, ["detect", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "photo-import" in result.output
    assert "SHOOT_01" in result.output


def test_validate_passes_for_built_unit(tmp_path):
    unit = _build_photo_tree(tmp_path)
    result = CliRunner().invoke(main, ["validate", str(unit), "--schema", "photo-import"])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    assert "photo-import" in result.output


def test_validate_reports_errors_for_empty_unit(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = CliRunner().invoke(main, ["validate", str(empty), "--schema", "photo-import"])
    assert result.exit_code != 0
    assert "error:" in result.output
