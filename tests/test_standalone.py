"""Proves atlas is standalone: full public API works with no forest dependency."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

import atlas


def test_all_public_names_importable():
    """Every name in atlas.__all__ is a real attribute on the package."""
    assert atlas.__all__
    for name in atlas.__all__:
        assert hasattr(atlas, name), f"atlas.__all__ lists {name!r} but it is not importable"


def test_public_api_trivial_calls(tmp_path: Path):
    """Exercise a trivial call/construction of every public name."""
    # Models construct with defaults / minimal args.
    atlas.DetectionConfig()
    atlas.ManifestAssetKeyCapability(field="asset_filename", source="filename")
    atlas.ManifestAssetKeyConfig(field="asset_filename")
    atlas.ManifestConfig()
    atlas.ManifestCapabilities()
    enrichment_input = atlas.ManifestEnrichmentInput(
        schema_name="tmp",
        schema_version="1.0",
        record_type="files",
        unit_id="unit",
        path=tmp_path / "sample.txt",
        relative_path="sample.txt",
        filename="sample.txt",
        extension=".txt",
        parse_status="ok",
        parse_error=None,
        metadata={},
    )
    assert enrichment_input.filename == "sample.txt"
    atlas.ManifestExtractorConfig(source="filename", regex="^(?P<name>.+)$")
    atlas.ManifestJoinConfig(left=["sample"], right=["sample"])
    atlas.ManifestRecordConfig(name="files", glob="*.txt")
    atlas.ManifestRecordCapability(record_type="files")
    endpoint_config = atlas.ManifestRelationshipEndpointConfig(record_type="files", fields=["unit_id"])
    companion_config = atlas.ManifestRelationshipEndpointConfig(record_type="companions", fields=["unit_id"])
    atlas.ManifestRelationshipConfig(
        name="files_companions",
        left=endpoint_config,
        right=companion_config,
    )
    endpoint_capability = atlas.ManifestRelationshipEndpointCapability("files", ("unit_id",), True)
    companion_capability = atlas.ManifestRelationshipEndpointCapability("companions", ("unit_id",), True)
    atlas.ManifestRelationshipCapability(
        "files_companions",
        endpoint_capability,
        companion_capability,
        "one_to_one",
    )
    related_record = atlas.ManifestRelatedRecord("files", tmp_path / "sample.txt", {})
    relationship_issue = atlas.ManifestRelationshipIssue(
        code="manifest.relationship.missing_companion",
        relationship="files_companions",
        endpoint="right",
        record_type="companions",
        unit_id="unit",
        key=("unit",),
        paths=(tmp_path / "sample.txt",),
        message="missing companion",
    )
    relationship_bundle = atlas.ManifestRelationshipBundle(
        relationship="files_companions",
        unit_id="unit",
        key=("unit",),
        left=(related_record,),
        right=(),
        issues=(relationship_issue,),
    )
    relationship_result = atlas.ManifestRelationshipResult(
        relationship="files_companions",
        bundles=(relationship_bundle,),
        issues=(relationship_issue,),
    )
    assert relationship_result.passed is False
    atlas.ManifestTagCapability(name="media_type", value="text/plain")
    atlas.ManifestTableConfig(
        name="samples",
        glob="samples.csv",
        join=atlas.ManifestJoinConfig(left=["sample"], right=["sample"]),
    )
    atlas.ManifestRecord(
        schema_name="tmp",
        schema_version="1.0",
        record_type="files",
        unit_id="unit",
        path=str(tmp_path / "sample.txt"),
        relative_path="sample.txt",
        filename="sample.txt",
        extension=".txt",
    )
    atlas.AtlasManifest(atlas.Schema(name="tmp"))
    assert issubclass(atlas.ManifestError, Exception)
    atlas.SyncConfig()
    atlas.ValidateConfig()
    atlas.ValidationResult()
    atlas.RuleResult(rule="required", rule_id="required:x", severity="ok", expected="x", actual="present")
    schema = atlas.Schema(name="tmp", sync=atlas.SyncConfig(include=["**/*"]))
    atlas.Detection(schema_name="x", stage_path=tmp_path)
    assert issubclass(atlas.SchemaError, Exception)

    # Functions.
    assert atlas.detect(tmp_path) == []
    assert atlas.extract_unit_ids(tmp_path) == []
    assert atlas.get_sync_files(tmp_path, schema) == []
    empty_manifest = atlas.load_dataframe(tmp_path, schema=atlas.Schema(name="tmp"))
    assert empty_manifest.empty
    attached = atlas.attach_dataframe(
        empty_manifest,
        {"external_unit": [], "value": []},
        left_on="unit_id",
        right_on="external_unit",
    )
    assert attached.empty
    assert "value" in attached
    assert (
        atlas.AtlasManifest(atlas.Schema(name="tmp"))
        .attach_dataframe(
            empty_manifest,
            {"external_unit": [], "value": []},
            left_on="unit_id",
            right_on="external_unit",
        )
        .empty
    )
    assert atlas.validate_data_unit(tmp_path, None).passed is True

    def enricher(record: atlas.ManifestEnrichmentInput) -> dict[str, object]:
        return {"filename_length": len(record.filename)}

    atlas.register_manifest_enricher("standalone.test", enricher)
    atlas.unregister_manifest_enricher("standalone.test")

    schemas = atlas.load_all_schemas()
    assert len(schemas) == 12

    photo = atlas.resolve_schema("photo-import", tmp_path)
    assert photo.name == "photo-import"
    assert atlas.resolve_key_output(photo, "photos", None) == ["MediaLibrary/RawPhotos/*"]
    raw_photos = tmp_path / "MediaLibrary" / "RawPhotos"
    raw_photos.mkdir(parents=True)
    (raw_photos / "IMG_0001.JPG").write_bytes(b"JPG")
    assert atlas.resolve_key_output(photo, "photos", tmp_path) == ["MediaLibrary/RawPhotos/IMG_0001.JPG"]

    schema_path = tmp_path / "s.yaml"
    schema_path.write_text(yaml.dump({"name": "roundtrip", "sync": {"include": ["**/*"]}}))
    assert atlas.load_schema(schema_path).name == "roundtrip"


def test_load_all_schemas_returns_builtins():
    names = {s.name for s in atlas.load_all_schemas()}
    assert names == {
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
    }


def test_no_forest_import_in_process():
    """After importing atlas, no forest module is present in this interpreter."""
    assert not any(m == "forest" or m.startswith("forest.") for m in sys.modules)


def test_no_forest_import_fresh_subprocess():
    """A fresh `import atlas` pulls in zero forest modules (airtight subprocess check)."""
    code = (
        "import atlas, sys; "
        "leaked = [m for m in sys.modules if m == 'forest' or m.startswith('forest.')]; "
        "assert not leaked, leaked; "
        "print('ok')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_manifest_enricher_api_does_not_import_flowkit_fresh_subprocess():
    """Registration stays explicit and never discovers optional domain packages."""
    code = (
        "import atlas, sys; "
        "atlas.register_manifest_enricher('probe', lambda record: {}); "
        "atlas.unregister_manifest_enricher('probe'); "
        "leaked = [m for m in sys.modules if m == 'flowkit' or m.startswith('flowkit.')]; "
        "assert not leaked, leaked; "
        "print('ok')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
