"""Tests for atlas.detect — on-disk detection of known data types.

Detection is driven entirely by each schema's declarative ``detection`` block,
so these tests build small on-disk trees that match the built-in *agnostic*
example schemas (web-build, monorepo-build, csv-dataset, photo-import,
site-archive) and assert what ``atlas.detect(root)`` reports.  ``detect``
returns ``Detection`` with ``.stage_path`` ABSOLUTE (resolved), so stage-path
assertions compare against ``stage_path.relative_to(root.resolve())``.
"""

from __future__ import annotations

from pathlib import Path

from atlas import Detection, detect, extract_unit_ids
from atlas.detect import _cmdline_subcommand
from atlas.schema import CmdlineSubcommandGuard, DetectionConfig, Schema


def _rel(det: Detection, root: Path) -> Path:
    """Stage path relative to the (resolved) scan root."""
    return det.stage_path.relative_to(root.resolve())


# --- Fixture builders: minimal trees matching the built-in example schemas ---


def _build_monorepo_build(stage_dir: Path, unit_name: str) -> Path:
    """A monorepo-build unit (also carries the web-build landmark to prove
    the disambiguation guards suppress web-build)."""
    unit = stage_dir / unit_name
    unit.mkdir(parents=True)
    (unit / ".workspace-stamp").mkdir()
    (unit / "_buildmeta").write_text("bundler workspace")
    dist = unit / "dist"
    dist.mkdir()
    (dist / "config.json").write_text("{}")
    (dist / "app.js").write_text("//app")  # web-build landmark, must be suppressed here
    pkg = dist / "packages" / "core"
    pkg.mkdir(parents=True)
    (pkg / "bundle.js").write_bytes(b"bundle")
    return unit


def _build_web_build(stage_dir: Path, unit_name: str, cmdline: str = "bundler build") -> Path:
    unit = stage_dir / unit_name
    unit.mkdir(parents=True)
    (unit / "_buildmeta").write_text(cmdline)
    dist = unit / "dist"
    dist.mkdir()
    (dist / "app.js").write_bytes(b"//app")
    (dist / "index.html").write_text("<html/>")
    return unit


def _build_photo_import(stage_dir: Path, shoot_name: str) -> Path:
    raw = stage_dir / shoot_name / "MediaLibrary" / "RawPhotos"
    raw.mkdir(parents=True)
    (raw / "IMG_0001.jpg").write_bytes(b"JPG")
    (raw / "IMG_0002.jpg").write_bytes(b"JPG")
    return stage_dir / shoot_name


def _build_site_archive(stage_dir: Path, name: str) -> Path:
    site = stage_dir / name
    site.mkdir(parents=True)
    (site / "archive.json").write_text("{}")
    (site / "content" / "pages").mkdir(parents=True)
    return site


def _build_csv_dataset(stage_dir: Path, name: str) -> Path:
    export = stage_dir / name
    export.mkdir(parents=True)
    exports = export / "Exports"
    exports.mkdir()
    (export / "Logs").mkdir()
    (exports / "records_2024.csv").write_text("a,b,c\n1,2,3\n")
    return export


class TestDetectEmpty:
    def test_empty_directory(self, tmp_path):
        assert detect(tmp_path) == []

    def test_nonexistent_directory(self, tmp_path):
        assert detect(tmp_path / "nonexistent") == []

    def test_accepts_string_path(self, tmp_path):
        assert detect(str(tmp_path)) == []

    def test_landmark_symlink_to_outside_root_is_ignored(self, tmp_path):
        outside = tmp_path.parent / "outside-landmark.json"
        outside.write_text("{}")
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "manifest.json").symlink_to(outside)
        schema = Schema(
            name="safe",
            detection=DetectionConfig(landmark="manifest.json", landmark_type="file", unit_depth=1),
        )

        assert detect(tmp_path, schemas=[schema]) == []

    def test_required_glob_symlink_to_outside_unit_is_ignored(self, tmp_path):
        outside = tmp_path / "outside.csv"
        outside.write_text("a,b\n1,2\n")
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "manifest.json").write_text("{}")
        (unit / "linked.csv").symlink_to(outside)
        schema = Schema(
            name="safe",
            detection=DetectionConfig(
                landmark="manifest.json",
                landmark_type="file",
                unit_depth=1,
                require_any_glob=["*.csv"],
            ),
        )

        assert detect(unit, schemas=[schema]) == []


class TestDetectMonorepoBuild:
    def test_detects_monorepo_build(self, tmp_path):
        _build_monorepo_build(tmp_path / "output" / "builds", "web-portal")

        results = detect(tmp_path)
        monorepo = [r for r in results if r.schema_name == "monorepo-build"]
        assert len(monorepo) == 1
        det = monorepo[0]
        assert isinstance(det, Detection)
        assert _rel(det, tmp_path) == Path("output/builds")
        assert det.stage_path.is_absolute()
        assert det.stage_path == (tmp_path / "output" / "builds").resolve()
        assert "web-portal" in det.unit_ids
        assert det.sync_by == "subdirectory"

    def test_detects_multiple_units(self, tmp_path):
        stage_dir = tmp_path / "output" / "builds"
        for name in ["web-portal", "admin-ui", "docs-site"]:
            _build_monorepo_build(stage_dir, name)

        results = detect(tmp_path)
        monorepo = [r for r in results if r.schema_name == "monorepo-build"]
        assert len(monorepo) == 1
        assert sorted(monorepo[0].unit_ids) == ["admin-ui", "docs-site", "web-portal"]

    def test_app_bundle_present_still_only_monorepo(self, tmp_path):
        # The unit carries dist/app.js (the web-build landmark); the workspace
        # markers + `bundler workspace` cmdline must suppress web-build.
        _build_monorepo_build(tmp_path / "output" / "builds", "web-portal")

        results = detect(tmp_path)
        assert {r.schema_name for r in results} == {"monorepo-build"}


class TestDetectPhotoImport:
    def test_detects_photo_import(self, tmp_path):
        _build_photo_import(tmp_path / "imports", "shoot1")

        results = detect(tmp_path)
        assert len(results) == 1
        assert results[0].schema_name == "photo-import"
        assert "shoot1" in results[0].unit_ids

    def test_multiple_shoots(self, tmp_path):
        for name in ["shoot1", "shoot2"]:
            _build_photo_import(tmp_path / "imports", name)

        results = detect(tmp_path)
        assert len(results) == 1
        assert sorted(results[0].unit_ids) == ["shoot1", "shoot2"]

    def test_uppercase_jpeg_extension_detects(self, tmp_path):
        raw = tmp_path / "imports" / "shoot1" / "MediaLibrary" / "RawPhotos"
        raw.mkdir(parents=True)
        (raw / "IMG_0001.JPG").write_bytes(b"JPG")

        results = detect(tmp_path)
        assert [result.schema_name for result in results] == ["photo-import"]


class TestDetectSiteArchive:
    def test_detects_site_archive(self, tmp_path):
        _build_site_archive(tmp_path / "archives", "site-2401-A")

        results = detect(tmp_path)
        assert len(results) == 1
        assert results[0].schema_name == "site-archive"
        assert "site-2401-A" in results[0].unit_ids
        assert results[0].unit_is_directory_stage is True

    def test_multiple_archives(self, tmp_path):
        for name in ["site-a", "site-b"]:
            _build_site_archive(tmp_path / "archives", name)

        results = detect(tmp_path)
        assert len(results) == 1
        assert sorted(results[0].unit_ids) == ["site-a", "site-b"]


class TestDetectCsvDataset:
    def test_detects_csv_dataset(self, tmp_path):
        _build_csv_dataset(tmp_path / "exports", "dataset1")

        results = detect(tmp_path)
        assert len(results) == 1
        assert results[0].schema_name == "csv-dataset"
        assert "dataset1" in results[0].unit_ids

    def test_detects_nested_csv(self, tmp_path):
        export = tmp_path / "exports" / "run1"
        export.mkdir(parents=True)
        (export / "Exports").mkdir()
        (export / "Logs").mkdir()
        region = export / "Exports" / "region-west"
        region.mkdir()
        (region / "rows.csv").write_text("x\n1\n")

        results = detect(tmp_path)
        assert [r.schema_name for r in results] == ["csv-dataset"]


class TestDetectMultipleTypes:
    def test_multiple_schema_types(self, tmp_path):
        _build_monorepo_build(tmp_path / "output" / "builds", "web-portal")
        _build_site_archive(tmp_path / "archives", "site-a")

        results = detect(tmp_path)
        schema_names = {r.schema_name for r in results}
        assert schema_names == {"monorepo-build", "site-archive"}

    def test_no_duplicate_stage_paths(self, tmp_path):
        _build_monorepo_build(tmp_path / "output" / "builds", "web-portal")

        results = detect(tmp_path)
        paths = [str(r.stage_path) for r in results]
        assert len(paths) == len(set(paths))

    def test_all_stage_paths_absolute(self, tmp_path):
        _build_monorepo_build(tmp_path / "output" / "builds", "web-portal")

        for det in detect(tmp_path):
            assert det.stage_path.is_absolute()


class TestDetectPartialMarkers:
    """Partial trees with missing markers are NOT detected."""

    def test_monorepo_no_buildmeta(self, tmp_path):
        unit = tmp_path / "output" / "builds" / "web-portal"
        unit.mkdir(parents=True)
        (unit / ".workspace-stamp").mkdir()
        (unit / "dist").mkdir()
        (unit / "dist" / "config.json").write_text("{}")

        results = detect(tmp_path)
        assert [r for r in results if r.schema_name == "monorepo-build"] == []

    def test_monorepo_no_config_json(self, tmp_path):
        unit = tmp_path / "output" / "builds" / "web-portal"
        unit.mkdir(parents=True)
        (unit / ".workspace-stamp").mkdir()
        (unit / "_buildmeta").write_text("bundler workspace")
        (unit / "dist").mkdir()

        results = detect(tmp_path)
        assert [r for r in results if r.schema_name == "monorepo-build"] == []

    def test_monorepo_only_stamp(self, tmp_path):
        unit = tmp_path / "output" / "builds" / "web-portal"
        unit.mkdir(parents=True)
        (unit / ".workspace-stamp").mkdir()

        results = detect(tmp_path)
        assert [r for r in results if r.schema_name == "monorepo-build"] == []

    def test_web_no_buildmeta(self, tmp_path):
        unit = tmp_path / "output" / "apps" / "site1"
        unit.mkdir(parents=True)
        dist = unit / "dist"
        dist.mkdir()
        (dist / "app.js").write_bytes(b"//app")

        results = detect(tmp_path)
        assert [r for r in results if r.schema_name == "web-build"] == []


class TestWebVsMonorepo:
    """web-build vs monorepo-build disambiguation (shared on-disk shape)."""

    def test_workspace_markers_suppress_web(self, tmp_path):
        unit = tmp_path / "output" / "builds" / "web-portal"
        unit.mkdir(parents=True)
        (unit / "_buildmeta").write_text("bundler build")
        dist = unit / "dist"
        dist.mkdir()
        (dist / "app.js").write_bytes(b"//app")
        (dist / "packages").mkdir()  # workspace marker present -> not a single-app build

        results = detect(tmp_path)
        assert [r for r in results if r.schema_name == "web-build"] == []

    def test_workspace_stamp_suppresses_web(self, tmp_path):
        unit = tmp_path / "output" / "builds" / "web-portal"
        unit.mkdir(parents=True)
        (unit / "_buildmeta").write_text("bundler build")
        (unit / ".workspace-stamp").mkdir()
        dist = unit / "dist"
        dist.mkdir()
        (dist / "app.js").write_bytes(b"//app")

        results = detect(tmp_path)
        assert [r for r in results if r.schema_name == "web-build"] == []

    def test_workspace_cmdline_suppresses_web(self, tmp_path):
        # No workspace markers, but the cmdline records `bundler workspace`.
        unit = tmp_path / "output" / "builds" / "web-portal"
        unit.mkdir(parents=True)
        (unit / "_buildmeta").write_text("bundler workspace --all")
        dist = unit / "dist"
        dist.mkdir()
        (dist / "app.js").write_bytes(b"//app")

        results = detect(tmp_path)
        assert [r for r in results if r.schema_name == "web-build"] == []

    def test_real_web_with_cmdline_detected(self, tmp_path):
        _build_web_build(tmp_path / "output" / "apps", "site1", "bundler build --out dist")

        results = detect(tmp_path)
        assert len([r for r in results if r.schema_name == "web-build"]) == 1

    def test_real_web_detected(self, tmp_path):
        _build_web_build(tmp_path / "output" / "apps", "site1", "bundler build")

        results = detect(tmp_path)
        assert len([r for r in results if r.schema_name == "web-build"]) == 1

    def test_web_with_workspace_in_name_detected_as_web(self, tmp_path):
        _build_web_build(tmp_path / "output" / "apps", "workspace-site", "bundler build --id=workspace-site")

        results = detect(tmp_path)
        assert len([r for r in results if r.schema_name == "web-build"]) == 1

    def test_complete_monorepo_detected(self, tmp_path):
        _build_monorepo_build(tmp_path / "output" / "builds", "web-portal")

        results = detect(tmp_path)
        assert len([r for r in results if r.schema_name == "monorepo-build"]) == 1


class TestCmdlineSubcommand:
    """Unit tests for _cmdline_subcommand() token parsing.

    The web-build schema uses this via its ``exclude_if_cmdline_subcommand``
    guard to reject monorepo (``bundler workspace``) output;
    ``_cmdline_subcommand`` returns the subcommand token that the guard then
    compares against ``workspace``.
    """

    _GUARD = CmdlineSubcommandGuard(file="_buildmeta", tool="bundler", subcommand="workspace")

    def _cmdline(self, tmp_path, text: str | None) -> Path:
        unit = tmp_path / "unit"
        unit.mkdir()
        if text is not None:
            (unit / "_buildmeta").write_text(text)
        return unit

    def _sub(self, tmp_path, text: str | None) -> str | None:
        return _cmdline_subcommand(self._cmdline(tmp_path, text), self._GUARD)

    def test_workspace_subcommand(self, tmp_path):
        assert self._sub(tmp_path, "bundler workspace --all") == "workspace"

    def test_build_with_workspace_in_args(self, tmp_path):
        assert self._sub(tmp_path, "bundler build --out workspace-dir") == "build"

    def test_full_path_bundler_workspace(self, tmp_path):
        assert self._sub(tmp_path, "/usr/local/bin/bundler workspace") == "workspace"

    def test_empty_cmdline(self, tmp_path):
        assert self._sub(tmp_path, "") is None

    def test_missing_cmdline(self, tmp_path):
        assert self._sub(tmp_path, None) is None

    def test_bare_workspace_subcommand(self, tmp_path):
        assert self._sub(tmp_path, "bundler workspace") == "workspace"

    def test_build_bare(self, tmp_path):
        assert self._sub(tmp_path, "bundler build") == "build"

    def test_unrelated_command(self, tmp_path):
        assert self._sub(tmp_path, "some-other-tool workspace") is None


class TestSchemaDrivenDetection:
    """A brand-new schema type is detectable with zero Python changes."""

    def test_custom_schema_detected(self, tmp_path):
        from atlas.schema import load_schema

        # A new data type defined purely in YAML — no detector code.
        schema_yaml = tmp_path / "widget.yaml"
        schema_yaml.write_text(
            "name: widget\n"
            "detection:\n"
            "  landmark: manifest.json\n"
            "  landmark_type: file\n"
            "  unit_depth: 1\n"
            "  markers:\n"
            "    - manifest.json\n"
            "  require_any_glob:\n"
            "    - '*.widget'\n"
        )
        schema = load_schema(schema_yaml)

        unit = tmp_path / "stage" / "widget-001"
        unit.mkdir(parents=True)
        (unit / "manifest.json").write_text("{}")
        (unit / "part.widget").write_text("x")

        results = detect(tmp_path, schemas=[schema])
        assert len(results) == 1
        assert results[0].schema_name == "widget"
        assert _rel(results[0], tmp_path) == Path("stage")
        assert results[0].unit_ids == ["widget-001"]

    def test_require_any_glob_gate(self, tmp_path):
        from atlas.schema import load_schema

        schema = load_schema(_write_widget_schema(tmp_path))
        unit = tmp_path / "stage" / "widget-001"
        unit.mkdir(parents=True)
        (unit / "manifest.json").write_text("{}")
        # No *.widget file: require_any_glob is unsatisfied -> not detected.
        assert detect(tmp_path, schemas=[schema]) == []

    def test_project_local_schema_discovered(self, tmp_path):
        from atlas.schema import discover_schemas

        # Drop a new schema into {project_root}/schemas — no code change.
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        (schemas_dir / "widget.yaml").write_text(
            "name: widget\ndetection:\n  landmark: manifest.json\n  landmark_type: file\n  markers: [manifest.json]\n"
        )
        data = tmp_path / "data" / "widget-001"
        data.mkdir(parents=True)
        (data / "manifest.json").write_text("{}")

        found = discover_schemas(project_root=tmp_path)
        results = detect(tmp_path / "data", schemas=found)
        assert [r.schema_name for r in results] == ["widget"]
        # Built-ins are still present alongside the project schema.
        assert "widget" in {s.name for s in found}
        assert len(found) >= 6

    def test_project_schema_overrides_builtin_by_name(self, tmp_path):
        from atlas.schema import discover_schemas

        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        (schemas_dir / "web-build.yaml").write_text("name: web-build\ndescription: overridden\n")
        found = {s.name: s for s in discover_schemas(project_root=tmp_path)}
        assert found["web-build"].description == "overridden"

    def test_validation_only_schema_skipped(self, tmp_path):
        from atlas.schema import load_schema

        # A schema with no landmark is validation-only and never detected.
        schema_yaml = tmp_path / "noland.yaml"
        schema_yaml.write_text("name: noland\nvalidate:\n  required_dirs: [data]\n")
        schema = load_schema(schema_yaml)
        (tmp_path / "data").mkdir()
        assert detect(tmp_path, schemas=[schema]) == []

    def test_builtin_validation_only_schema_not_detected(self, tmp_path):
        # The built-in report-bundle schema has no detection block, so even a
        # matching tree is never surfaced by detect().
        (tmp_path / "figures").mkdir()
        (tmp_path / "report.pdf").write_bytes(b"%PDF")
        assert [r.schema_name for r in detect(tmp_path) if r.schema_name == "report-bundle"] == []

    def test_unit_depth_cannot_escape_scan_root(self, tmp_path):
        (tmp_path / "landmark").write_text("x")
        schema = Schema(
            name="escape",
            detection=DetectionConfig(landmark="landmark", landmark_type="file", unit_depth=2),
        )

        assert detect(tmp_path, schemas=[schema]) == []

    def test_file_landmark_cannot_be_a_unit_directory(self, tmp_path):
        (tmp_path / "landmark").write_text("x")
        schema = Schema(
            name="file-unit",
            detection=DetectionConfig(landmark="landmark", landmark_type="file", unit_depth=0),
        )

        assert detect(tmp_path, schemas=[schema]) == []

    def test_stage_detections_are_sorted(self, tmp_path):
        schema = Schema(name="ordered", detection=DetectionConfig(landmark="manifest.json", unit_depth=1))
        for stage in ("z-stage", "a-stage"):
            unit = tmp_path / stage / "unit"
            unit.mkdir(parents=True)
            (unit / "manifest.json").write_text("{}")

        results = detect(tmp_path, schemas=[schema])
        assert [result.stage_path.relative_to(tmp_path).as_posix() for result in results] == ["a-stage", "z-stage"]


def _write_widget_schema(tmp_path: Path) -> Path:
    schema_yaml = tmp_path / "widget.yaml"
    schema_yaml.write_text(
        "name: widget\n"
        "detection:\n"
        "  landmark: manifest.json\n"
        "  landmark_type: file\n"
        "  markers: [manifest.json]\n"
        "  require_any_glob: ['*.widget']\n"
    )
    return schema_yaml


class TestExtractUnitIds:
    def test_extracts_from_directory_names(self, tmp_path):
        stage = tmp_path / "builds"
        stage.mkdir()
        for name in ["pkg-a", "pkg-b", "pkg-c"]:
            (stage / name).mkdir()

        assert extract_unit_ids(stage) == ["pkg-a", "pkg-b", "pkg-c"]

    def test_empty_directory(self, tmp_path):
        stage = tmp_path / "empty"
        stage.mkdir()
        assert extract_unit_ids(stage) == []

    def test_nonexistent_directory(self, tmp_path):
        assert extract_unit_ids(tmp_path / "nope") == []

    def test_ignores_files(self, tmp_path):
        stage = tmp_path / "data"
        stage.mkdir()
        (stage / "unit1").mkdir()
        (stage / "notes.txt").write_text("ignore me")
        assert extract_unit_ids(stage) == ["unit1"]

    def test_accepts_string_path(self, tmp_path):
        stage = tmp_path / "builds"
        stage.mkdir()
        (stage / "u1").mkdir()
        assert extract_unit_ids(str(stage)) == ["u1"]
