"""Tests for atlas schema module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from atlas.schema import (
    CmdlineSubcommandGuard,
    DetectionConfig,
    Schema,
    SchemaError,
    SyncConfig,
    ValidateConfig,
    discover_schemas,
    get_sync_files,
    load_all_schemas,
    load_schema,
    resolve_schema,
)

MINIMAL_SCHEMA_DATA = {
    "name": "test-schema",
    "version": "1.0",
    "description": "A test schema",
    "sync": {
        "include": ["dist/**"],
        "exclude": ["node_modules/**"],
    },
}

FULL_SCHEMA_DATA = {
    "name": "full-test-schema",
    "version": "1.0",
    "description": "A full test schema",
    "detection": {
        "markers": ["dist/config.json", ".workspace-stamp"],
        "landmark": ".workspace-stamp",
        "landmark_type": "dir",
        "unit_depth": 1,
    },
    "sync": {
        "include": ["dist/**"],
        "exclude": [".workspace-stamp/**", "_*", "*.map"],
    },
    "validate": {
        "required": ["dist/config.json"],
        "required_any": ["dist/packages/*/bundle.js", "dist/bundle.js"],
        "required_dirs": ["dist/packages"],
        "warn_if_missing": ["dist/report.html"],
        "fail_on": ["_errors", "*.error"],
        "min_size_mb": 1.0,
        "max_size_gb": 500.0,
        "min_file_count": 10,
        "max_file_count": 100000,
        "filename_pattern": None,
    },
    "key_outputs": {
        "pkg_bundle": "dist/packages/core/bundle.js",
        "report": "dist/report.html",
    },
}


class TestDetectionConfig:
    """Tests for DetectionConfig model."""

    def test_defaults(self):
        """DetectionConfig defaults: no landmark, empty markers, unit_depth 1."""
        config = DetectionConfig()
        assert config.markers == []
        assert config.landmark is None
        assert config.landmark_type == "any"
        assert config.landmark_parent is None
        assert config.unit_depth == 1
        assert config.require_any_glob == []
        assert config.exclude_if_markers == []
        assert config.exclude_if_cmdline_subcommand is None
        assert config.unit_is_directory_stage is False

    def test_markers_stored(self):
        """DetectionConfig stores markers."""
        config = DetectionConfig(markers=["dist/config.json", ".workspace-stamp"])
        assert config.markers == ["dist/config.json", ".workspace-stamp"]

    def test_landmark_fields(self):
        """DetectionConfig stores the landmark seed and its constraints."""
        config = DetectionConfig(landmark="archive.json", landmark_type="file", unit_depth=2)
        assert config.landmark == "archive.json"
        assert config.landmark_type == "file"
        assert config.unit_depth == 2

    def test_cmdline_guard(self):
        """DetectionConfig parses the cmdline-subcommand exclusion guard."""
        config = DetectionConfig(
            landmark="dist/app.js",
            exclude_if_cmdline_subcommand={"file": "_buildmeta", "tool": "bundler", "subcommand": "workspace"},
        )
        assert config.exclude_if_cmdline_subcommand is not None
        assert config.exclude_if_cmdline_subcommand.subcommand == "workspace"


class TestSyncConfig:
    """Tests for SyncConfig model."""

    def test_defaults(self):
        """SyncConfig defaults to empty include and exclude."""
        config = SyncConfig()
        assert config.include == []
        assert config.exclude == []

    def test_include_exclude(self):
        """SyncConfig stores include and exclude lists."""
        config = SyncConfig(include=["dist/**"], exclude=["node_modules/**"])
        assert config.include == ["dist/**"]
        assert config.exclude == ["node_modules/**"]


class TestValidateConfig:
    """Tests for ValidateConfig model."""

    def test_defaults(self):
        """ValidateConfig has sensible defaults for all fields."""
        config = ValidateConfig()
        assert config.required == []
        assert config.required_any == []
        assert config.required_dirs == []
        assert config.warn_if_missing == []
        assert config.fail_on == []
        assert config.min_size_mb is None
        assert config.max_size_gb is None
        assert config.min_file_count is None
        assert config.max_file_count is None
        assert config.filename_pattern is None

    def test_all_fields(self):
        """ValidateConfig stores all validation fields."""
        config = ValidateConfig(
            required=["dist/config.json"],
            required_any=["dist/packages/*/bundle.js", "dist/bundle.js"],
            required_dirs=["dist/packages"],
            warn_if_missing=["optional.json"],
            fail_on=["_errors"],
            min_size_mb=1.0,
            max_size_gb=500.0,
            min_file_count=10,
            max_file_count=100000,
            filename_pattern=r"^[A-Z]{2}-[A-Z]{2}-[A-Z0-9]+$",
        )
        assert config.required == ["dist/config.json"]
        assert config.required_any == ["dist/packages/*/bundle.js", "dist/bundle.js"]
        assert config.required_dirs == ["dist/packages"]
        assert config.min_size_mb == 1.0
        assert config.max_size_gb == 500.0
        assert config.min_file_count == 10
        assert config.max_file_count == 100000
        assert config.filename_pattern == r"^[A-Z]{2}-[A-Z]{2}-[A-Z0-9]+$"

    def test_invalid_filename_pattern_rejected_at_load(self):
        """A filename_pattern that is not a valid regex fails validation early."""
        with pytest.raises(ValidationError, match="invalid filename_pattern regex"):
            ValidateConfig(filename_pattern="[unterminated")


class TestStrictSchemaConfig:
    """Schema authoring mistakes fail at load time, close to their source."""

    def test_unknown_fields_rejected_by_every_model(self):
        cases = [
            (CmdlineSubcommandGuard, {"file": "cmd", "tool": "tool", "subcommand": "run", "typo": True}),
            (DetectionConfig, {"landmark": "hit", "typo": True}),
            (SyncConfig, {"include": ["**/*"], "typo": True}),
            (ValidateConfig, {"required": ["file.txt"], "typo": True}),
            (Schema, {"name": "test", "typo": True}),
        ]
        for model, values in cases:
            with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
                model(**values)

    @pytest.mark.parametrize(
        "value",
        ["", "   ", "bad\0path", "/outside", "../outside", r"\outside", r"C:\outside"],
    )
    def test_unsafe_sync_paths_rejected(self, value: str):
        with pytest.raises(ValidationError):
            SyncConfig(include=[value])

    def test_unsafe_paths_rejected_across_sections(self):
        with pytest.raises(ValidationError):
            CmdlineSubcommandGuard(file="../cmd", tool="tool", subcommand="run")
        with pytest.raises(ValidationError):
            DetectionConfig(landmark="hit", markers=["../marker"])
        with pytest.raises(ValidationError):
            ValidateConfig(required=["/outside"])
        with pytest.raises(ValidationError):
            Schema(name="test", key_outputs={"result": "../outside"})

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: DetectionConfig(landmark="bad**glob"),
            lambda: DetectionConfig(landmark="hit", require_any_glob=["bad**glob"]),
            lambda: SyncConfig(include=["bad**glob"]),
            lambda: ValidateConfig(required_any=["bad**glob"]),
            lambda: ValidateConfig(fail_on=["bad**glob"]),
            lambda: Schema(name="test", key_outputs={"result": "bad**glob"}),
        ],
    )
    def test_malformed_globs_rejected(self, factory):
        with pytest.raises(ValidationError, match=r"\*\*.*entire path component"):
            factory()

    @pytest.mark.parametrize(
        "values",
        [
            {"min_size_mb": -1},
            {"max_size_gb": float("inf")},
            {"min_size_mb": float("nan")},
            {"min_file_count": -1},
            {"max_file_count": -1},
            {"min_file_count": 2, "max_file_count": 1},
            {"min_size_mb": 1025, "max_size_gb": 1},
        ],
    )
    def test_invalid_validation_bounds_rejected(self, values: dict[str, object]):
        with pytest.raises(ValidationError):
            ValidateConfig(**values)

    def test_negative_unit_depth_rejected(self):
        with pytest.raises(ValidationError):
            DetectionConfig(unit_depth=-1)


class TestSchemaModel:
    """Tests for Schema Pydantic model."""

    def test_minimal_schema(self):
        """Schema with only required fields is valid."""
        schema = Schema(name="test")
        assert schema.name == "test"
        assert schema.version == "1.0"
        assert schema.description == ""
        assert schema.key_outputs == {}

    def test_no_validate_section(self):
        """Schema with no validate section is valid (validation optional)."""
        schema = Schema(name="test", sync=SyncConfig(include=["**/*"]))
        assert schema.validation is None

    def test_validate_alias(self):
        """Schema uses 'validate' alias for validate_ field."""
        data = {
            "name": "test",
            "validate": {
                "required": ["file.txt"],
            },
        }
        schema = Schema(**data)
        assert schema.validation is not None
        assert schema.validation.required == ["file.txt"]

    def test_full_schema(self):
        """Schema with all fields is valid."""
        schema = Schema(**FULL_SCHEMA_DATA)
        assert schema.name == "full-test-schema"
        assert schema.detection.markers == ["dist/config.json", ".workspace-stamp"]
        assert schema.sync.include == ["dist/**"]
        assert schema.sync.exclude == [".workspace-stamp/**", "_*", "*.map"]
        assert schema.validation is not None
        assert schema.validation.required == ["dist/config.json"]
        assert schema.validation.required_any == ["dist/packages/*/bundle.js", "dist/bundle.js"]
        assert schema.key_outputs["pkg_bundle"] == "dist/packages/core/bundle.js"

    def test_key_outputs_dict(self):
        """key_outputs is a dict of string keys and values."""
        schema = Schema(
            name="test",
            key_outputs={"bundle": "dist/app.js", "report": "dist/report.html"},
        )
        assert schema.key_outputs == {"bundle": "dist/app.js", "report": "dist/report.html"}


class TestLoadSchema:
    """Tests for load_schema function."""

    def test_load_from_yaml(self, tmp_path: Path):
        """load_schema loads a schema from a YAML file."""
        schema_path = tmp_path / "test-schema.yaml"
        schema_path.write_text(yaml.dump(MINIMAL_SCHEMA_DATA))

        schema = load_schema(schema_path)

        assert schema.name == "test-schema"
        assert schema.sync.include == ["dist/**"]
        assert schema.sync.exclude == ["node_modules/**"]

    def test_load_full_schema(self, tmp_path: Path):
        """load_schema loads a schema with all fields."""
        schema_path = tmp_path / "full-schema.yaml"
        schema_path.write_text(yaml.dump(FULL_SCHEMA_DATA))

        schema = load_schema(schema_path)

        assert schema.name == "full-test-schema"
        assert schema.detection.markers == ["dist/config.json", ".workspace-stamp"]
        assert schema.validation is not None
        assert schema.key_outputs["report"] == "dist/report.html"

    def test_load_missing_file(self):
        """load_schema raises SchemaError for missing file."""
        with pytest.raises(SchemaError, match="not found"):
            load_schema(Path("/nonexistent/schema.yaml"))

    def test_load_invalid_yaml(self, tmp_path: Path):
        """load_schema raises SchemaError for malformed YAML."""
        schema_path = tmp_path / "bad.yaml"
        schema_path.write_text("{{invalid yaml}}")

        with pytest.raises(SchemaError, match=r"[Yy]AML"):
            load_schema(schema_path)

    def test_load_missing_name(self, tmp_path: Path):
        """load_schema raises SchemaError when name field is missing."""
        schema_path = tmp_path / "noname.yaml"
        schema_path.write_text(yaml.dump({"version": "1.0", "sync": {"include": ["**"]}}))

        with pytest.raises(SchemaError, match="name"):
            load_schema(schema_path)

    def test_load_accepts_string_path(self, tmp_path: Path):
        """load_schema accepts a string path."""
        schema_path = tmp_path / "test.yaml"
        schema_path.write_text(yaml.dump(MINIMAL_SCHEMA_DATA))

        schema = load_schema(str(schema_path))
        assert schema.name == "test-schema"

    def test_load_no_validate_section(self, tmp_path: Path):
        """load_schema with no validate section yields validate_=None."""
        data = {"name": "test", "sync": {"include": ["**"]}}
        schema_path = tmp_path / "no-validate.yaml"
        schema_path.write_text(yaml.dump(data))

        schema = load_schema(schema_path)
        assert schema.validation is None

    def test_duplicate_yaml_key_rejected(self, tmp_path: Path):
        schema_path = tmp_path / "duplicate.yaml"
        schema_path.write_text("name: first\nname: second\n", encoding="utf-8")

        with pytest.raises(SchemaError, match="duplicate key 'name'"):
            load_schema(schema_path)

    def test_utf8_schema_content(self, tmp_path: Path):
        schema_path = tmp_path / "unicode.yaml"
        schema_path.write_text("name: unicode\ndescription: café 📦\n", encoding="utf-8")

        assert load_schema(schema_path).description == "café 📦"

    def test_invalid_utf8_is_schema_error(self, tmp_path: Path):
        schema_path = tmp_path / "invalid-utf8.yaml"
        schema_path.write_bytes(b"name: \xff\n")

        with pytest.raises(SchemaError, match="Cannot read schema file"):
            load_schema(schema_path)


class TestResolveSchema:
    """Tests for resolve_schema function."""

    def test_resolve_builtin_by_name(self, tmp_path: Path):
        """resolve_schema finds built-in schemas by name."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert schema.name == "monorepo-build"

    def test_resolve_project_local(self, tmp_path: Path):
        """resolve_schema searches project-local ./schemas/ first."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        local_schema = {
            "name": "monorepo-build",
            "version": "2.0",
            "description": "project override",
            "sync": {"include": ["custom/**"]},
        }
        (schemas_dir / "monorepo-build.yaml").write_text(yaml.dump(local_schema))

        schema = resolve_schema("monorepo-build", tmp_path)

        # Project-local overrides built-in
        assert schema.version == "2.0"
        assert schema.sync.include == ["custom/**"]

    def test_resolve_user_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_schema searches user ~/.atlas/schemas/ second."""
        user_schemas = tmp_path / "user_home" / ".atlas" / "schemas"
        user_schemas.mkdir(parents=True)
        user_schema = {
            "name": "custom-schema",
            "version": "1.0",
            "sync": {"include": ["data/**"]},
        }
        (user_schemas / "custom-schema.yaml").write_text(yaml.dump(user_schema))

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "user_home")

        project_root = tmp_path / "project"
        project_root.mkdir()

        schema = resolve_schema("custom-schema", project_root)
        assert schema.name == "custom-schema"

    def test_resolve_project_over_user(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """VAL-CONFIG-003: Project-local schema overrides user schema."""
        # User schema
        user_schemas = tmp_path / "user_home" / ".atlas" / "schemas"
        user_schemas.mkdir(parents=True)
        user_data = {"name": "my-schema", "version": "1.0", "sync": {"include": ["user/**"]}}
        (user_schemas / "my-schema.yaml").write_text(yaml.dump(user_data))

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "user_home")

        # Project schema (same name, different version)
        project_root = tmp_path / "project"
        project_schemas = project_root / "schemas"
        project_schemas.mkdir(parents=True)
        proj_data = {"name": "my-schema", "version": "2.0", "sync": {"include": ["project/**"]}}
        (project_schemas / "my-schema.yaml").write_text(yaml.dump(proj_data))

        schema = resolve_schema("my-schema", project_root)
        assert schema.version == "2.0"
        assert schema.sync.include == ["project/**"]

    def test_resolve_absolute_path(self, tmp_path: Path):
        """resolve_schema loads absolute file paths directly."""
        schema_path = tmp_path / "custom.yaml"
        schema_path.write_text(yaml.dump(MINIMAL_SCHEMA_DATA))

        schema = resolve_schema(str(schema_path), tmp_path)
        assert schema.name == "test-schema"

    def test_resolve_relative_path(self, tmp_path: Path):
        """resolve_schema loads relative file paths directly."""
        schema_path = tmp_path / "custom.yaml"
        schema_path.write_text(yaml.dump(MINIMAL_SCHEMA_DATA))

        # Use a relative-like path that has .yaml extension
        schema = resolve_schema(str(schema_path), tmp_path)
        assert schema.name == "test-schema"

    def test_resolve_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_schema raises SchemaError when schema not found anywhere."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty_home")

        with pytest.raises(SchemaError, match="not found"):
            resolve_schema("nonexistent-schema", tmp_path)

    def test_resolve_path_with_yaml_extension(self, tmp_path: Path):
        """resolve_schema treats name ending in .yaml/.yml as a file path."""
        schema_path = tmp_path / "my-schema.yaml"
        schema_path.write_text(yaml.dump(MINIMAL_SCHEMA_DATA))

        schema = resolve_schema(str(schema_path), tmp_path)
        assert schema.name == "test-schema"

    def test_resolve_relative_yaml_against_project_root(self, tmp_path: Path):
        """resolve_schema resolves relative .yaml paths against project_root, not CWD."""
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        schema_path = project_root / "custom-schema.yaml"
        schema_path.write_text(yaml.dump(MINIMAL_SCHEMA_DATA))

        # Use a relative path — should resolve against project_root
        schema = resolve_schema("custom-schema.yaml", project_root)
        assert schema.name == "test-schema"

    def test_resolve_relative_yaml_not_against_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_schema does NOT resolve relative .yaml paths against CWD."""
        project_root = tmp_path / "myproject"
        project_root.mkdir()

        # Place schema in project_root only
        schema_path = project_root / "my.yaml"
        schema_path.write_text(yaml.dump(MINIMAL_SCHEMA_DATA))

        # Change CWD to a different directory that does NOT have the schema
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        monkeypatch.chdir(other_dir)

        # Should find it via project_root, not CWD
        schema = resolve_schema("my.yaml", project_root)
        assert schema.name == "test-schema"

    def test_resolve_named_yml_schema(self, tmp_path: Path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        (schemas_dir / "custom.yml").write_text("name: custom\n", encoding="utf-8")

        assert resolve_schema("custom", tmp_path).name == "custom"

    def test_discover_yml_schema(self, tmp_path: Path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        (schemas_dir / "custom.yml").write_text("name: custom\n", encoding="utf-8")

        assert "custom" in {schema.name for schema in discover_schemas(tmp_path)}

    def test_ambiguous_yaml_extensions_rejected(self, tmp_path: Path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        for suffix in (".yaml", ".yml"):
            (schemas_dir / f"custom{suffix}").write_text("name: custom\n", encoding="utf-8")

        with pytest.raises(SchemaError, match="Ambiguous schema 'custom'"):
            resolve_schema("custom", tmp_path)
        with pytest.raises(SchemaError, match="Ambiguous schema 'custom'"):
            discover_schemas(tmp_path)

    def test_builtin_resolution_returns_deep_copy(self, tmp_path: Path):
        first = resolve_schema("web-build", tmp_path)
        first.description = "mutated"
        first.detection.markers.append("mutated-marker")

        second = resolve_schema("web-build", tmp_path)
        assert second.description != "mutated"
        assert "mutated-marker" not in second.detection.markers

    def test_parent_traversing_schema_name_rejected(self, tmp_path: Path):
        with pytest.raises(SchemaError, match="Invalid schema name"):
            resolve_schema("../outside", tmp_path)

    def test_installed_filename_must_match_declared_name(self, tmp_path: Path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        (schemas_dir / "expected.yaml").write_text("name: different\n", encoding="utf-8")

        with pytest.raises(SchemaError, match="does not match installed filename"):
            resolve_schema("expected", tmp_path)
        with pytest.raises(SchemaError, match="does not match installed filename"):
            discover_schemas(tmp_path)

    def test_malformed_override_fails_closed(self, tmp_path: Path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        (schemas_dir / "web-build.yaml").write_text("name: web-build\nsnyc: {}\n", encoding="utf-8")

        with pytest.raises(SchemaError, match="snyc"):
            discover_schemas(tmp_path)


class TestGetSyncFiles:
    """Tests for get_sync_files function."""

    def _create_unit(self, unit_path: Path) -> None:
        """Create a realistic unit directory structure."""
        # Create dist/ structure
        dist = unit_path / "dist"
        dist.mkdir(parents=True)
        (dist / "config.json").write_text("config")
        (dist / "report.html").write_text("report")

        core = dist / "packages" / "core"
        core.mkdir(parents=True)
        (core / "bundle.js").write_text("bundle")
        (core / "manifest.json").write_text("manifest")

        # Create node_modules/ (excluded)
        node_modules = unit_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "dep.js").write_text("dep")

        # Create _buildmeta (excluded by _* pattern)
        (unit_path / "_buildmeta").write_text("meta")

        # Create .map (excluded)
        (unit_path / "app.js.map").write_text("map")

    def test_include_only(self, tmp_path: Path):
        """get_sync_files returns files matching include patterns."""
        unit_path = tmp_path / "unit1"
        self._create_unit(unit_path)

        schema = Schema(
            name="test",
            sync=SyncConfig(include=["dist/**"]),
        )
        files = get_sync_files(unit_path, schema)

        # All files under dist/ should be included
        rel_paths = {f.relative_to(unit_path).as_posix() for f in files}
        assert "dist/config.json" in rel_paths
        assert "dist/report.html" in rel_paths
        assert "dist/packages/core/bundle.js" in rel_paths

        # Files NOT under dist/ should NOT be included
        assert "node_modules/dep.js" not in rel_paths
        assert "_buildmeta" not in rel_paths

    def test_exclude_removes_files(self, tmp_path: Path):
        """get_sync_files excludes files matching exclude patterns."""
        unit_path = tmp_path / "unit1"
        self._create_unit(unit_path)

        schema = Schema(
            name="test",
            sync=SyncConfig(
                include=["**/*"],
                exclude=["node_modules/**", "_*", "*.map"],
            ),
        )
        files = get_sync_files(unit_path, schema)

        rel_paths = {f.relative_to(unit_path).as_posix() for f in files}
        # node_modules should be excluded
        assert "node_modules/dep.js" not in rel_paths
        # _buildmeta should be excluded
        assert "_buildmeta" not in rel_paths
        # .map should be excluded
        assert "app.js.map" not in rel_paths
        # But dist files should remain
        assert "dist/config.json" in rel_paths

    def test_include_and_exclude_combined(self, tmp_path: Path):
        """VAL-PUSH-012: Only files matching include and NOT exclude are returned."""
        unit_path = tmp_path / "unit1"
        self._create_unit(unit_path)

        schema = Schema(
            name="test",
            sync=SyncConfig(
                include=["dist/**"],
                exclude=["node_modules/**", "_*", "*.map"],
            ),
        )
        files = get_sync_files(unit_path, schema)

        rel_paths = {f.relative_to(unit_path).as_posix() for f in files}
        # Included (dist/**)
        assert "dist/config.json" in rel_paths
        assert "dist/report.html" in rel_paths
        assert "dist/packages/core/bundle.js" in rel_paths
        # Excluded
        assert "node_modules/dep.js" not in rel_paths
        assert "_buildmeta" not in rel_paths
        assert "app.js.map" not in rel_paths

    def test_exclude_precedence_over_include(self, tmp_path: Path):
        """VAL-SCHEMA-001: Exclude takes precedence over include for overlapping patterns."""
        unit_path = tmp_path / "unit1"
        data_dir = unit_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "keep.txt").write_text("keep")
        (data_dir / "remove.log").write_text("remove")
        (data_dir / "important.log").write_text("also remove")

        schema = Schema(
            name="test",
            sync=SyncConfig(
                include=["data/**"],
                exclude=["**/*.log"],
            ),
        )
        files = get_sync_files(unit_path, schema)

        rel_paths = {f.relative_to(unit_path).as_posix() for f in files}
        assert "data/keep.txt" in rel_paths
        assert "data/remove.log" not in rel_paths
        assert "data/important.log" not in rel_paths

    def test_empty_include_returns_all(self, tmp_path: Path):
        """Empty include list returns all files (no filter)."""
        unit_path = tmp_path / "unit1"
        unit_path.mkdir()
        (unit_path / "file1.txt").write_text("a")
        (unit_path / "file2.txt").write_text("b")

        schema = Schema(
            name="test",
            sync=SyncConfig(include=[], exclude=[]),
        )
        files = get_sync_files(unit_path, schema)

        rel_paths = {f.relative_to(unit_path).as_posix() for f in files}
        assert "file1.txt" in rel_paths
        assert "file2.txt" in rel_paths

    def test_no_files_returns_empty(self, tmp_path: Path):
        """get_sync_files returns empty list for empty directory."""
        unit_path = tmp_path / "empty"
        unit_path.mkdir()

        schema = Schema(name="test", sync=SyncConfig(include=["**/*"]))
        files = get_sync_files(unit_path, schema)

        assert files == []

    def test_nested_directory_matching(self, tmp_path: Path):
        """get_sync_files handles nested directory patterns."""
        unit_path = tmp_path / "unit"
        deep = unit_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "deep.txt").write_text("deep")

        schema = Schema(name="test", sync=SyncConfig(include=["a/**"]))
        files = get_sync_files(unit_path, schema)

        rel_paths = {f.relative_to(unit_path).as_posix() for f in files}
        assert "a/b/c/deep.txt" in rel_paths

    def test_returns_sorted_paths(self, tmp_path: Path):
        """get_sync_files returns paths in sorted order."""
        unit_path = tmp_path / "unit"
        unit_path.mkdir()
        (unit_path / "c.txt").write_text("c")
        (unit_path / "a.txt").write_text("a")
        (unit_path / "b.txt").write_text("b")

        schema = Schema(name="test", sync=SyncConfig(include=["**/*"]))
        files = get_sync_files(unit_path, schema)

        assert files == sorted(files)

    def test_returns_only_files(self, tmp_path: Path):
        """get_sync_files returns only files, not directories."""
        unit_path = tmp_path / "unit"
        subdir = unit_path / "subdir"
        subdir.mkdir(parents=True)
        (subdir / "file.txt").write_text("data")

        schema = Schema(name="test", sync=SyncConfig(include=["**/*"]))
        files = get_sync_files(unit_path, schema)

        for f in files:
            assert f.is_file()

    def test_nonexistent_unit_path(self, tmp_path: Path):
        """get_sync_files returns empty list for nonexistent path."""
        schema = Schema(name="test", sync=SyncConfig(include=["**/*"]))
        files = get_sync_files(tmp_path / "nonexistent", schema)
        assert files == []

    def test_relative_unit_returns_absolute_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        unit = tmp_path / "unit"
        unit.mkdir()
        (unit / "inside.txt").write_text("data")
        monkeypatch.chdir(tmp_path)

        files = get_sync_files("unit", Schema(name="test"))
        assert files == [unit / "inside.txt"]
        assert all(path.is_absolute() for path in files)

    def test_symlink_to_file_outside_unit_is_not_synced(self, tmp_path: Path):
        unit = tmp_path / "unit"
        unit.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        (unit / "outside-link.txt").symlink_to(outside)
        (unit / "inside.txt").write_text("data")

        files = get_sync_files(unit, Schema(name="test"))
        assert files == [unit / "inside.txt"]


class TestBuiltinMonorepoBuild:
    """Tests for built-in monorepo-build schema."""

    def test_loads_without_error(self, tmp_path: Path):
        """monorepo-build.yaml loads without error."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert schema.name == "monorepo-build"

    def test_has_detection_markers(self, tmp_path: Path):
        """monorepo-build has detection markers."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert len(schema.detection.markers) > 0
        assert "dist/config.json" in schema.detection.markers

    def test_has_sync_include(self, tmp_path: Path):
        """monorepo-build has sync include rules."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert len(schema.sync.include) > 0
        assert "dist/**/*" in schema.sync.include

    def test_has_sync_exclude(self, tmp_path: Path):
        """monorepo-build recursively excludes source maps."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert schema.sync.exclude == ["**/*.map"]

    def test_has_validate_config(self, tmp_path: Path):
        """monorepo-build has validation configuration."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert schema.validation is not None
        assert len(schema.validation.required) > 0
        assert schema.validation.required_dirs == []
        assert schema.validation.required_any == ["dist/packages/*/bundle.js", "dist/bundle.js"]

    def test_has_fail_on(self, tmp_path: Path):
        """monorepo-build has fail_on patterns."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert schema.validation is not None
        assert len(schema.validation.fail_on) > 0

    def test_has_key_outputs(self, tmp_path: Path):
        """monorepo-build has key_outputs mapping."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert len(schema.key_outputs) > 0
        assert "pkg_bundle" in schema.key_outputs
        assert "config" in schema.key_outputs

    def test_placeholder_key_output(self, tmp_path: Path):
        """monorepo-build has a {placeholder} template key output."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert "{package}" in schema.key_outputs["pkg_bundle"]

    def test_has_size_bounds(self, tmp_path: Path):
        """monorepo-build has size bounds in validate."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert schema.validation is not None
        assert schema.validation.min_size_mb is not None
        assert schema.validation.max_size_gb is not None

    def test_has_file_count_bounds(self, tmp_path: Path):
        """monorepo-build has file count bounds in validate."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert schema.validation is not None
        assert schema.validation.min_file_count is not None
        assert schema.validation.max_file_count is not None

    def test_layouts_are_alternatives_not_warnings(self, tmp_path: Path):
        """Neither supported layout is mislabeled as an optional warning."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert schema.validation is not None
        assert schema.validation.warn_if_missing == []

    def test_exclude_patterns_match_spec(self, tmp_path: Path):
        """monorepo-build excludes nested source maps from included dist files."""
        schema = resolve_schema("monorepo-build", tmp_path)
        assert schema.sync.exclude == ["**/*.map"]

    def test_filtering_with_builtin(self, tmp_path: Path):
        """monorepo-build schema correctly filters a realistic directory."""
        schema = resolve_schema("monorepo-build", tmp_path)

        unit_path = tmp_path / "web-portal"
        dist = unit_path / "dist"
        dist.mkdir(parents=True)
        (dist / "config.json").write_text("config")

        core = dist / "packages" / "core"
        core.mkdir(parents=True)
        (core / "bundle.js").write_text("data")
        (core / "bundle.js.map").write_text("source map")

        # Excluded items
        stamp = unit_path / ".workspace-stamp"
        stamp.mkdir()
        (stamp / "pipeline.js").write_text("code")
        (unit_path / "_buildmeta").write_text("meta")
        (unit_path / "test.map").write_text("map")

        files = get_sync_files(unit_path, schema)
        rel_paths = {f.relative_to(unit_path).as_posix() for f in files}

        # Included
        assert "dist/config.json" in rel_paths
        assert "dist/packages/core/bundle.js" in rel_paths
        assert "dist/packages/core/bundle.js.map" not in rel_paths

        # Excluded
        assert ".workspace-stamp/pipeline.js" not in rel_paths
        assert "_buildmeta" not in rel_paths
        assert "test.map" not in rel_paths


class TestBuiltinWebBuild:
    """Tests for built-in web-build schema."""

    def test_loads_without_error(self, tmp_path: Path):
        """web-build.yaml loads via resolve_schema without error."""
        schema = resolve_schema("web-build", tmp_path)
        assert schema.name == "web-build"

    def test_detection_markers(self, tmp_path: Path):
        """web-build has expected detection markers."""
        schema = resolve_schema("web-build", tmp_path)
        assert "dist/app.js" in schema.detection.markers
        assert "_buildmeta" in schema.detection.markers

    def test_detection_landmark(self, tmp_path: Path):
        """web-build seeds detection on the app bundle file."""
        schema = resolve_schema("web-build", tmp_path)
        assert schema.detection.landmark == "dist/app.js"
        assert schema.detection.landmark_type == "file"
        assert schema.detection.unit_depth == 2

    def test_detection_excludes_monorepo(self, tmp_path: Path):
        """web-build excludes monorepo-build look-alikes."""
        schema = resolve_schema("web-build", tmp_path)
        assert ".workspace-stamp" in schema.detection.exclude_if_markers
        guard = schema.detection.exclude_if_cmdline_subcommand
        assert guard is not None and guard.subcommand == "workspace"

    def test_sync_include(self, tmp_path: Path):
        """web-build includes dist/**."""
        schema = resolve_schema("web-build", tmp_path)
        assert "dist/**/*" in schema.sync.include

    def test_sync_exclude(self, tmp_path: Path):
        """web-build recursively excludes source maps."""
        schema = resolve_schema("web-build", tmp_path)
        assert schema.sync.exclude == ["**/*.map"]

    def test_validate_required(self, tmp_path: Path):
        """web-build requires the app bundle and index."""
        schema = resolve_schema("web-build", tmp_path)
        assert schema.validation is not None
        assert "dist/app.js" in schema.validation.required
        assert "dist/index.html" in schema.validation.required

    def test_required_files_make_dist_dir_rule_redundant(self, tmp_path: Path):
        """Required files already prove that dist is a directory."""
        schema = resolve_schema("web-build", tmp_path)
        assert schema.validation is not None
        assert schema.validation.required_dirs == []

    def test_validate_fail_on(self, tmp_path: Path):
        """web-build has fail_on patterns for error markers."""
        schema = resolve_schema("web-build", tmp_path)
        assert schema.validation is not None
        assert "_errors" in schema.validation.fail_on
        assert "*.error" in schema.validation.fail_on

    def test_validate_size_bounds(self, tmp_path: Path):
        """web-build has size bounds."""
        schema = resolve_schema("web-build", tmp_path)
        assert schema.validation is not None
        assert schema.validation.min_size_mb is not None
        assert schema.validation.max_size_gb is not None

    def test_validate_file_count_bounds(self, tmp_path: Path):
        """web-build has file count bounds."""
        schema = resolve_schema("web-build", tmp_path)
        assert schema.validation is not None
        assert schema.validation.min_file_count is not None
        assert schema.validation.max_file_count is not None

    def test_validate_warn_if_missing(self, tmp_path: Path):
        """web-build has warn_if_missing entries."""
        schema = resolve_schema("web-build", tmp_path)
        assert schema.validation is not None
        assert len(schema.validation.warn_if_missing) > 0

    def test_no_filename_pattern(self, tmp_path: Path):
        """web-build has no filename_pattern."""
        schema = resolve_schema("web-build", tmp_path)
        assert schema.validation is not None
        assert schema.validation.filename_pattern is None

    def test_key_outputs(self, tmp_path: Path):
        """web-build has key_outputs for major outputs."""
        schema = resolve_schema("web-build", tmp_path)
        assert "bundle" in schema.key_outputs
        assert "index" in schema.key_outputs
        assert "report" in schema.key_outputs
        assert "stats" in schema.key_outputs

    def test_filtering_with_builtin(self, tmp_path: Path):
        """web-build schema correctly filters a realistic directory."""
        schema = resolve_schema("web-build", tmp_path)

        unit_path = tmp_path / "site1"
        dist = unit_path / "dist"
        dist.mkdir(parents=True)
        (dist / "app.js").write_text("data")
        (dist / "index.html").write_text("html")
        (dist / "report.html").write_text("report")
        (dist / "stats.json").write_text("stats")
        (dist / "app.js.map").write_text("source map")

        # Excluded items
        (unit_path / "_buildmeta").write_text("meta")
        (unit_path / "archive.map").write_text("map")

        files = get_sync_files(unit_path, schema)
        rel_paths = {f.relative_to(unit_path).as_posix() for f in files}

        assert "dist/app.js" in rel_paths
        assert "dist/index.html" in rel_paths
        assert "dist/app.js.map" not in rel_paths
        assert "_buildmeta" not in rel_paths
        assert "archive.map" not in rel_paths


class TestBuiltinCsvDataset:
    """Tests for built-in csv-dataset schema."""

    def test_loads_without_error(self, tmp_path: Path):
        """csv-dataset.yaml loads via resolve_schema without error."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert schema.name == "csv-dataset"

    def test_detection_markers(self, tmp_path: Path):
        """csv-dataset has expected detection markers."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert "Exports" in schema.detection.markers
        assert "Logs" in schema.detection.markers

    def test_detection_landmark_and_require_glob(self, tmp_path: Path):
        """csv-dataset requires an Exports directory containing a CSV."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert schema.detection.landmark == "Exports"
        assert schema.detection.landmark_type == "dir"
        assert schema.detection.require_any_glob == ["Exports/**/*.csv"]

    def test_sync_include(self, tmp_path: Path):
        """csv-dataset includes Exports and Stats."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert "Exports/**/*" in schema.sync.include
        assert "Stats/**/*" in schema.sync.include

    def test_sync_exclude(self, tmp_path: Path):
        """csv-dataset excludes temporary files from included trees."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert schema.sync.exclude == ["**/*.tmp"]

    def test_validate_required_dirs(self, tmp_path: Path):
        """csv-dataset requires Exports directory."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert schema.validation is not None
        assert "Exports" in schema.validation.required_dirs

    def test_validate_required_empty(self, tmp_path: Path):
        """csv-dataset needs no additional required files."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert schema.validation is not None
        assert schema.validation.required == []

    def test_validate_fail_on(self, tmp_path: Path):
        """csv-dataset has fail_on patterns."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert schema.validation is not None
        assert len(schema.validation.fail_on) > 0

    def test_validate_size_bounds(self, tmp_path: Path):
        """csv-dataset has size bounds."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert schema.validation is not None
        assert schema.validation.min_size_mb is not None
        assert schema.validation.max_size_gb is not None

    def test_validate_file_count_bounds(self, tmp_path: Path):
        """csv-dataset has file count bounds."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert schema.validation is not None
        assert schema.validation.min_file_count is not None
        assert schema.validation.max_file_count is not None

    def test_validate_warn_if_missing(self, tmp_path: Path):
        """csv-dataset warns if the summary is missing."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert schema.validation is not None
        assert len(schema.validation.warn_if_missing) > 0

    def test_no_filename_pattern(self, tmp_path: Path):
        """csv-dataset has no filename_pattern."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert schema.validation is not None
        assert schema.validation.filename_pattern is None

    def test_key_outputs(self, tmp_path: Path):
        """csv-dataset has key_outputs for tables, summary, and logs."""
        schema = resolve_schema("csv-dataset", tmp_path)
        assert "tables" in schema.key_outputs
        assert "summary" in schema.key_outputs
        assert "logs" in schema.key_outputs


class TestBuiltinSiteArchive:
    """Tests for built-in site-archive schema."""

    def test_loads_without_error(self, tmp_path: Path):
        """site-archive.yaml loads via resolve_schema without error."""
        schema = resolve_schema("site-archive", tmp_path)
        assert schema.name == "site-archive"

    def test_detection_markers(self, tmp_path: Path):
        """site-archive has expected detection markers."""
        schema = resolve_schema("site-archive", tmp_path)
        assert "archive.json" in schema.detection.markers
        assert "content/pages" in schema.detection.markers

    def test_detection_landmark(self, tmp_path: Path):
        """site-archive seeds on archive.json as a directory-stage unit."""
        schema = resolve_schema("site-archive", tmp_path)
        assert schema.detection.landmark == "archive.json"
        assert schema.detection.landmark_type == "file"
        assert schema.detection.unit_is_directory_stage is True

    def test_sync_include(self, tmp_path: Path):
        """site-archive includes everything."""
        schema = resolve_schema("site-archive", tmp_path)
        assert "**/*" in schema.sync.include

    def test_sync_exclude(self, tmp_path: Path):
        """site-archive excludes cache and tmp."""
        schema = resolve_schema("site-archive", tmp_path)
        assert "cache/**" in schema.sync.exclude
        assert "tmp/**" in schema.sync.exclude

    def test_validate_required(self, tmp_path: Path):
        """site-archive requires archive.json."""
        schema = resolve_schema("site-archive", tmp_path)
        assert schema.validation is not None
        assert "archive.json" in schema.validation.required

    def test_validate_required_dirs(self, tmp_path: Path):
        """site-archive requires content/pages."""
        schema = resolve_schema("site-archive", tmp_path)
        assert schema.validation is not None
        assert "content/pages" in schema.validation.required_dirs

    def test_validate_fail_on(self, tmp_path: Path):
        """site-archive has fail_on patterns."""
        schema = resolve_schema("site-archive", tmp_path)
        assert schema.validation is not None
        assert len(schema.validation.fail_on) > 0

    def test_validate_size_bounds(self, tmp_path: Path):
        """site-archive has size bounds."""
        schema = resolve_schema("site-archive", tmp_path)
        assert schema.validation is not None
        assert schema.validation.min_size_mb is not None
        assert schema.validation.max_size_gb is not None

    def test_validate_file_count_bounds(self, tmp_path: Path):
        """site-archive has file count bounds."""
        schema = resolve_schema("site-archive", tmp_path)
        assert schema.validation is not None
        assert schema.validation.min_file_count is not None
        assert schema.validation.max_file_count is not None

    def test_validate_warn_if_missing(self, tmp_path: Path):
        """site-archive warns if metadata.json or assets missing."""
        schema = resolve_schema("site-archive", tmp_path)
        assert schema.validation is not None
        assert len(schema.validation.warn_if_missing) > 0

    def test_no_filename_pattern(self, tmp_path: Path):
        """site-archive has no filename_pattern."""
        schema = resolve_schema("site-archive", tmp_path)
        assert schema.validation is not None
        assert schema.validation.filename_pattern is None

    def test_key_outputs(self, tmp_path: Path):
        """site-archive has key_outputs for manifest, metadata, pages, assets."""
        schema = resolve_schema("site-archive", tmp_path)
        assert "manifest" in schema.key_outputs
        assert "metadata" in schema.key_outputs
        assert "pages" in schema.key_outputs
        assert "assets" in schema.key_outputs

    def test_filtering_with_builtin(self, tmp_path: Path):
        """site-archive schema correctly filters a realistic directory."""
        schema = resolve_schema("site-archive", tmp_path)

        unit_path = tmp_path / "site-2401-A"
        unit_path.mkdir()
        (unit_path / "archive.json").write_text("{}")
        (unit_path / "metadata.json").write_text("{}")

        pages = unit_path / "content" / "pages"
        pages.mkdir(parents=True)
        (pages / "index.html").write_text("<html/>")

        assets = unit_path / "assets"
        assets.mkdir()
        (assets / "style.css").write_text("css")

        # Excluded
        cache = unit_path / "cache"
        cache.mkdir()
        (cache / "tmp.bin").write_text("cache")
        tmp = unit_path / "tmp"
        tmp.mkdir()
        (tmp / "scratch.txt").write_text("scratch")

        files = get_sync_files(unit_path, schema)
        rel_paths = {f.relative_to(unit_path).as_posix() for f in files}

        assert "archive.json" in rel_paths
        assert "content/pages/index.html" in rel_paths
        assert "assets/style.css" in rel_paths
        assert "cache/tmp.bin" not in rel_paths
        assert "tmp/scratch.txt" not in rel_paths


class TestBuiltinPhotoImport:
    """Tests for built-in photo-import schema."""

    def test_loads_without_error(self, tmp_path: Path):
        """photo-import.yaml loads via resolve_schema without error."""
        schema = resolve_schema("photo-import", tmp_path)
        assert schema.name == "photo-import"

    def test_detection_markers(self, tmp_path: Path):
        """photo-import has expected detection markers."""
        schema = resolve_schema("photo-import", tmp_path)
        assert "MediaLibrary/RawPhotos" in schema.detection.markers

    def test_detection_landmark(self, tmp_path: Path):
        """photo-import seeds on RawPhotos under MediaLibrary."""
        schema = resolve_schema("photo-import", tmp_path)
        assert schema.detection.landmark == "RawPhotos"
        assert schema.detection.landmark_parent == "MediaLibrary"
        assert any("[jJ]" in p for p in schema.detection.require_any_glob)

    def test_sync_include(self, tmp_path: Path):
        """photo-import includes MediaLibrary/**."""
        schema = resolve_schema("photo-import", tmp_path)
        assert "MediaLibrary/**/*" in schema.sync.include

    def test_sync_exclude(self, tmp_path: Path):
        """photo-import excludes tmp files and .DS_Store."""
        schema = resolve_schema("photo-import", tmp_path)
        assert "**/*.tmp" in schema.sync.exclude
        assert "**/.DS_Store" in schema.sync.exclude

    def test_validate_required_dirs(self, tmp_path: Path):
        """photo-import requires MediaLibrary/RawPhotos directory."""
        schema = resolve_schema("photo-import", tmp_path)
        assert schema.validation is not None
        assert "MediaLibrary/RawPhotos" in schema.validation.required_dirs

    def test_validate_fail_on(self, tmp_path: Path):
        """photo-import has fail_on patterns."""
        schema = resolve_schema("photo-import", tmp_path)
        assert schema.validation is not None
        assert len(schema.validation.fail_on) > 0

    def test_validate_size_bounds(self, tmp_path: Path):
        """photo-import has size bounds."""
        schema = resolve_schema("photo-import", tmp_path)
        assert schema.validation is not None
        assert schema.validation.min_size_mb is not None
        assert schema.validation.max_size_gb is not None

    def test_validate_file_count_bounds(self, tmp_path: Path):
        """photo-import has file count bounds."""
        schema = resolve_schema("photo-import", tmp_path)
        assert schema.validation is not None
        assert schema.validation.min_file_count is not None
        assert schema.validation.max_file_count is not None

    def test_has_filename_pattern(self, tmp_path: Path):
        """photo-import has filename_pattern validation for image/video files."""
        schema = resolve_schema("photo-import", tmp_path)
        assert schema.validation is not None
        assert schema.validation.filename_pattern is not None
        assert "jpg" in schema.validation.filename_pattern
        assert "png" in schema.validation.filename_pattern
        assert "mov" in schema.validation.filename_pattern

    def test_key_outputs(self, tmp_path: Path):
        """photo-import has key_outputs for photos and contact_sheets."""
        schema = resolve_schema("photo-import", tmp_path)
        assert "photos" in schema.key_outputs
        assert "contact_sheets" in schema.key_outputs

    def test_filtering_with_builtin(self, tmp_path: Path):
        """photo-import schema correctly filters a realistic directory."""
        schema = resolve_schema("photo-import", tmp_path)

        unit_path = tmp_path / "shoot1"
        raw = unit_path / "MediaLibrary" / "RawPhotos"
        raw.mkdir(parents=True)
        (raw / "IMG_0001.jpg").write_text("JPG data")
        (raw / "IMG_0002.jpg").write_text("JPG data")
        (raw / "contact_sheet.pdf").write_text("PDF")

        # Excluded items
        (raw / "temp.tmp").write_text("temporary")
        (unit_path / "MediaLibrary" / ".DS_Store").write_text("ds_store")

        files = get_sync_files(unit_path, schema)
        rel_paths = {f.relative_to(unit_path).as_posix() for f in files}

        assert "MediaLibrary/RawPhotos/IMG_0001.jpg" in rel_paths
        assert "MediaLibrary/RawPhotos/IMG_0002.jpg" in rel_paths
        assert "MediaLibrary/RawPhotos/contact_sheet.pdf" in rel_paths
        assert "MediaLibrary/RawPhotos/temp.tmp" not in rel_paths
        assert "MediaLibrary/.DS_Store" not in rel_paths


class TestAllBuiltinSchemasLoad:
    """VAL-SCHEMA-002: All built-in schemas load successfully."""

    def test_all_builtin_schemas_load(self):
        """Every packaged YAML loads and declares the same name as its filename."""
        schema_dir = Path(__file__).parent.parent / "src" / "atlas" / "schemas"
        packaged_names = {path.stem for path in schema_dir.glob("*.yaml")}
        schemas = load_all_schemas()
        loaded_names = {schema.name for schema in schemas}

        assert loaded_names == packaged_names

    def test_detection_schemas_have_markers(self):
        """Every built-in schema with a landmark has at least one detection marker."""
        schemas = load_all_schemas()
        for schema in schemas:
            if schema.detection.landmark is not None:
                assert len(schema.detection.markers) > 0, f"{schema.name} missing detection markers"

    def test_detection_schemas_have_sync_rules(self):
        """Every built-in detection schema has sync include rules."""
        schemas = load_all_schemas()
        for schema in schemas:
            if schema.detection.landmark is not None:
                assert len(schema.sync.include) > 0, f"{schema.name} missing sync include"

    def test_all_schemas_have_validate(self):
        """Every built-in schema has a validate section."""
        schemas = load_all_schemas()
        for schema in schemas:
            assert schema.validation is not None, f"{schema.name} missing validate"

    def test_all_schemas_have_key_outputs(self):
        """Every built-in schema has at least one key_output."""
        schemas = load_all_schemas()
        for schema in schemas:
            assert len(schema.key_outputs) > 0, f"{schema.name} missing key_outputs"

    def test_all_schemas_have_size_bounds(self):
        """Every built-in schema has size bounds in validate."""
        schemas = load_all_schemas()
        for schema in schemas:
            assert schema.validation is not None
            assert schema.validation.min_size_mb is not None, f"{schema.name} missing min_size_mb"
            assert schema.validation.max_size_gb is not None, f"{schema.name} missing max_size_gb"

    def test_all_schemas_have_file_count_bounds(self):
        """Every built-in schema has file count bounds in validate."""
        schemas = load_all_schemas()
        for schema in schemas:
            assert schema.validation is not None
            assert schema.validation.min_file_count is not None, f"{schema.name} missing min_file_count"
            assert schema.validation.max_file_count is not None, f"{schema.name} missing max_file_count"

    def test_filename_patterns_are_declared_only_for_extension_constrained_schemas(self):
        """Only schemas with a closed set of synced file extensions declare a pattern."""
        schemas = load_all_schemas()
        expected = {"facs-sort", "facs-sort-diva", "photo-import"}
        for schema in schemas:
            if schema.name in expected:
                assert schema.validation is not None
                assert schema.validation.filename_pattern is not None
            elif schema.validation is not None:
                assert schema.validation.filename_pattern is None, f"{schema.name} should not have filename_pattern"
