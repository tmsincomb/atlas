"""Tests for schema-driven metadata manifests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from atlas import (
    ManifestAssetKeyCapability,
    ManifestAssetKeyConfig,
    ManifestEnrichmentInput,
    ManifestJoinCardinality,
    ManifestRelationshipEndpointCapability,
    ManifestTagCapability,
    attach_dataframe,
    load_dataframe,
    register_manifest_enricher,
    unregister_manifest_enricher,
)
from atlas.manifest import AtlasManifest, ManifestError
from atlas.schema import (
    DetectionConfig,
    ManifestConfig,
    ManifestExtractorConfig,
    ManifestJoinConfig,
    ManifestRecordConfig,
    ManifestRelationshipConfig,
    ManifestRelationshipEndpointConfig,
    ManifestTableConfig,
    Schema,
)


def _schema(
    *,
    table: ManifestTableConfig | None = None,
    asset_key: ManifestAssetKeyConfig | None = None,
) -> Schema:
    return Schema(
        name="samples",
        version="2.0",
        detection=DetectionConfig(markers=["data"], landmark="data", landmark_type="dir", unit_depth=1),
        manifest=ManifestConfig(
            records=[
                ManifestRecordConfig(
                    name="sample_file",
                    glob="data/*.txt",
                    groups=["samples", "text"],
                    tags={"media_type": "text/plain", "is_binary": False},
                    asset_key=asset_key,
                    extractors=[
                        ManifestExtractorConfig(
                            source="unit_name",
                            regex=r"^Run_(?P<run_date>\d{6})$",
                        ),
                        ManifestExtractorConfig(
                            source="filename",
                            regex=r"^(?P<participant_id>G\d+-\d+)_(?P<pool>\d+)\.txt$",
                        ),
                    ],
                    constants={"source_type": "path", "selected": True},
                    derive={"pool_number": "P{pool}", "source_label": "{source_type}"},
                    casts={"run_date": "date", "pool": "integer"},
                    date_formats={"run_date": "%y%m%d"},
                )
            ],
            tables=[table] if table is not None else [],
        ),
    )


def _query_schema() -> Schema:
    schema = _schema()
    schema.manifest.records.append(
        ManifestRecordConfig(
            name="broken_file",
            glob="data/*.bad",
            groups=["broken", "text"],
            tags={"media_type": "application/x-bad", "is_binary": True},
            constants={"source_type": "broken", "selected": False},
            extractors=[ManifestExtractorConfig(source="filename", regex=r"^(?P<name>good)\.bad$")],
        )
    )
    return schema


def _unit(root: Path, name: str = "Run_230101") -> Path:
    unit = root / name
    (unit / "data").mkdir(parents=True)
    return unit


def _relationship_schema(
    cardinality: ManifestJoinCardinality = "one_to_one",
    *,
    left_required: bool = True,
    right_required: bool = True,
) -> Schema:
    return Schema(
        name="related",
        detection=DetectionConfig(markers=["data"], landmark="data", landmark_type="dir", unit_depth=1),
        manifest=ManifestConfig(
            records=[
                ManifestRecordConfig(
                    name="anchor",
                    glob="data/*.left",
                    extractors=[
                        ManifestExtractorConfig(
                            source="filename",
                            regex=r"^(?P<left_id>S\d+)(?:_[a-z])?\.left$",
                        )
                    ],
                ),
                ManifestRecordConfig(
                    name="companion",
                    glob="data/*.right",
                    extractors=[
                        ManifestExtractorConfig(
                            source="filename",
                            regex=r"^(?P<right_id>S\d+)(?:_[a-z])?\.right$",
                        )
                    ],
                ),
            ],
            relationships=[
                ManifestRelationshipConfig(
                    name="anchor_companion",
                    left=ManifestRelationshipEndpointConfig(
                        record_type="anchor",
                        fields=["left_id"],
                        required=left_required,
                    ),
                    right=ManifestRelationshipEndpointConfig(
                        record_type="companion",
                        fields=["right_id"],
                        required=right_required,
                    ),
                    cardinality=cardinality,
                )
            ],
        ),
    )


def test_record_extracts_and_casts_path_metadata(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    sample = unit / "data" / "G002-004_5.txt"
    sample.write_text("payload")

    record = AtlasManifest(_schema()).record(sample)

    assert record.parse_status == "ok"
    assert record.metadata["participant_id"] == "G002-004"
    assert record.metadata["pool"] == 5
    assert record.metadata["pool_number"] == "P5"
    assert record.metadata["run_date"] == pd.Timestamp("2023-01-01")
    assert record.metadata["selected"] is True
    assert record.metadata["source_label"] == "path"


def test_dataframe_scans_units_in_deterministic_order(tmp_path: Path) -> None:
    second = _unit(tmp_path, "Run_230102")
    first = _unit(tmp_path, "Run_230101")
    (second / "data" / "G002-002_2.txt").write_text("two")
    (first / "data" / "G002-001_1.txt").write_text("one")

    frame = AtlasManifest(_schema()).dataframe(tmp_path)

    assert frame["participant_id"].tolist() == ["G002-001", "G002-002"]
    assert frame["record_type"].tolist() == ["sample_file", "sample_file"]
    assert str(frame["pool"].dtype) == "Int64"
    assert frame["path"].map(Path).map(Path.is_absolute).all()


def test_dataframe_selects_record_types_before_strict_validation(tmp_path: Path) -> None:
    schema = _schema()
    schema.manifest.records.append(
        ManifestRecordConfig(
            name="broken_file",
            glob="data/*.bad",
            groups=["broken"],
            extractors=[ManifestExtractorConfig(source="filename", regex=r"^(?P<name>good)\.bad$")],
        )
    )
    unit = _unit(tmp_path)
    (unit / "data" / "G002-001_1.txt").write_text("one")
    (unit / "data" / "unexpected.bad").write_text("bad")

    frame = AtlasManifest(schema).dataframe(tmp_path, record_types={"sample_file"}, strict=True)

    assert frame["record_type"].tolist() == ["sample_file"]


def test_dataframe_selects_record_groups_before_strict_validation(tmp_path: Path) -> None:
    schema = _schema()
    schema.manifest.records.append(
        ManifestRecordConfig(
            name="broken_file",
            glob="data/*.bad",
            groups=["broken", "text"],
            extractors=[ManifestExtractorConfig(source="filename", regex=r"^(?P<name>good)\.bad$")],
        )
    )
    unit = _unit(tmp_path)
    sample = unit / "data" / "G002-001_1.txt"
    broken = unit / "data" / "unexpected.bad"
    sample.write_text("one")
    broken.write_text("bad")

    frame = AtlasManifest(schema).dataframe(tmp_path, record_groups={"samples"}, strict=True)
    assert frame["record_type"].tolist() == ["sample_file"]
    assert "media_type" not in frame

    union = AtlasManifest(schema).dataframe(tmp_path, record_groups={"samples", "broken"})
    assert set(union["record_type"]) == {"sample_file", "broken_file"}

    intersection = AtlasManifest(schema).dataframe(
        tmp_path,
        record_types={"sample_file"},
        record_groups={"text"},
        strict=True,
    )
    assert intersection["record_type"].tolist() == ["sample_file"]

    excluded_file = AtlasManifest(schema).dataframe(broken, record_groups={"samples"}, strict=True)
    assert excluded_file.empty


def test_dataframe_rejects_unknown_record_type(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="unknown manifest record types: missing"):
        AtlasManifest(_schema()).dataframe(tmp_path, record_types={"missing"})


def test_dataframe_rejects_unknown_record_group(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="unknown manifest record groups: missing"):
        AtlasManifest(_schema()).dataframe(tmp_path, record_groups={"missing"})


def test_dataframe_where_filters_before_strict_validation(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    sample = unit / "data" / "G002-001_1.txt"
    broken = unit / "data" / "unexpected.bad"
    sample.write_text("one")
    broken.write_text("bad")
    manifest = AtlasManifest(_query_schema())

    queries = [
        {"media_type": "text/plain"},
        {"record_group": "samples"},
        {"extension": "TXT"},
        {"selected": True},
        {"media_type": "text/plain", "extension": ".txt", "selected": True},
    ]
    for where in queries:
        frame = manifest.dataframe(tmp_path, where=where, strict=True)
        assert frame["record_type"].tolist() == ["sample_file"]

    excluded_file = manifest.dataframe(broken, where={"media_type": "text/plain"}, strict=True)
    assert excluded_file.empty


def test_dataframe_where_can_match_rules_with_no_files(tmp_path: Path) -> None:
    _unit(tmp_path)

    frame = AtlasManifest(_query_schema()).dataframe(
        tmp_path,
        where={"media_type": "text/plain"},
        strict=True,
    )

    assert frame.empty
    assert "record_type" in frame


def test_dataframe_where_rejects_invalid_and_contradictory_predicates(tmp_path: Path) -> None:
    manifest = AtlasManifest(_query_schema())

    with pytest.raises(ManifestError, match="unknown manifest query field 'missing'"):
        manifest.dataframe(tmp_path, where={"missing": "value"})

    with pytest.raises(ManifestError, match=r"unknown manifest extension query value '\.csv'"):
        manifest.dataframe(tmp_path, where={"extension": ".csv"})

    with pytest.raises(ManifestError, match="unknown manifest query value 'application/json'"):
        manifest.dataframe(tmp_path, where={"media_type": "application/json"})

    with pytest.raises(ManifestError, match="query field 'selected' expects bool, got int"):
        manifest.dataframe(tmp_path, where={"selected": 1})

    with pytest.raises(ManifestError, match="where predicates are contradictory"):
        manifest.dataframe(tmp_path, where={"media_type": "text/plain", "extension": ".bad"})

    with pytest.raises(ManifestError, match="manifest selectors are contradictory"):
        manifest.dataframe(
            tmp_path,
            record_types={"broken_file"},
            where={"media_type": "text/plain"},
        )

    with pytest.raises(ManifestError, match="where query must be a mapping"):
        manifest.dataframe(tmp_path, where=[("extension", ".txt")])  # type: ignore[arg-type]


def test_dataframe_where_rejects_ambiguous_declared_metadata(tmp_path: Path) -> None:
    schema = _query_schema()
    schema.manifest.records[1].tags["selected"] = False

    with pytest.raises(ManifestError, match="declared as both a tag and a constant"):
        AtlasManifest(schema).dataframe(tmp_path, where={"selected": False})


def test_load_dataframe_matches_class_api_and_selectors(tmp_path: Path) -> None:
    schema = _query_schema()
    unit = _unit(tmp_path)
    (unit / "data" / "G002-001_1.txt").write_text("one")
    (unit / "data" / "unexpected.bad").write_text("bad")
    options = {
        "context_root": tmp_path,
        "record_types": {"sample_file"},
        "record_groups": {"text"},
        "where": {"media_type": "text/plain"},
        "strict": True,
    }

    expected = AtlasManifest(schema).dataframe(tmp_path, **options)
    actual = load_dataframe(tmp_path, schema=schema, **options)

    pd.testing.assert_frame_equal(actual, expected)


def test_load_dataframe_matches_class_errors(tmp_path: Path) -> None:
    schema = _query_schema()

    with pytest.raises(ManifestError) as class_query_error:
        AtlasManifest(schema).dataframe(tmp_path, where={"missing": "value"})
    with pytest.raises(ManifestError) as function_query_error:
        load_dataframe(tmp_path, schema=schema, where={"missing": "value"})
    assert str(function_query_error.value) == str(class_query_error.value)

    unit = _unit(tmp_path)
    (unit / "data" / "unexpected.bad").write_text("bad")
    with pytest.raises(ManifestError) as class_strict_error:
        AtlasManifest(schema).dataframe(tmp_path, where={"media_type": "application/x-bad"}, strict=True)
    with pytest.raises(ManifestError) as function_strict_error:
        load_dataframe(tmp_path, schema=schema, where={"media_type": "application/x-bad"}, strict=True)
    assert str(function_strict_error.value) == str(class_strict_error.value)

    with pytest.raises(ManifestError) as class_schema_error:
        AtlasManifest("missing", project_root=tmp_path)
    with pytest.raises(ManifestError) as function_schema_error:
        load_dataframe(tmp_path, schema="missing", project_root=tmp_path)
    assert str(function_schema_error.value) == str(class_schema_error.value)


def test_enrich_records_calls_only_selected_rows_with_immutable_inputs(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    sample = unit / "data" / "G002-001_1.txt"
    broken = unit / "data" / "good.bad"
    sample.write_text("sample")
    broken.write_text("broken")
    manifest = AtlasManifest(_query_schema())
    records = manifest.dataframe(tmp_path)
    seen: list[ManifestEnrichmentInput] = []

    def inspect_file(record: ManifestEnrichmentInput) -> dict[str, object]:
        seen.append(record)
        with pytest.raises(TypeError):
            record.metadata["participant_id"] = "changed"  # type: ignore[index]
        with pytest.raises(FrozenInstanceError):
            record.filename = "changed"  # type: ignore[misc]
        return {"zeta": record.path.stat().st_size, "alpha": record.metadata["participant_id"]}

    enriched = manifest.enrich_records(records, inspect_file, record_groups={"samples"})

    assert [record.filename for record in seen] == [sample.name]
    assert seen[0].path == sample.resolve()
    assert seen[0].relative_path == "data/G002-001_1.txt"
    assert enriched["record_type"].tolist() == ["sample_file", "broken_file"]
    assert enriched["enrichment_status"].tolist() == ["ok", "not_selected"]
    assert enriched["enrichment_name"].tolist() == ["inspect_file", "inspect_file"]
    assert enriched.loc[0, "alpha"] == "G002-001"
    assert pd.isna(enriched.loc[1, "alpha"])
    assert enriched.columns[-5:].tolist() == [
        "alpha",
        "zeta",
        "enrichment_name",
        "enrichment_status",
        "enrichment_error",
    ]


def test_enrich_records_retains_partial_failures_and_strict_is_fail_fast(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    for filename in ("G002-001_1.txt", "G002-002_2.txt", "G002-003_3.txt"):
        (unit / "data" / filename).write_text(filename)
    manifest = AtlasManifest(_schema())
    records = manifest.dataframe(tmp_path)
    calls: list[str] = []

    def sometimes_fails(record: ManifestEnrichmentInput) -> dict[str, object]:
        calls.append(record.filename)
        if record.filename == "G002-002_2.txt":
            raise RuntimeError("unreadable payload")
        return {"content_length": record.path.stat().st_size}

    enriched = manifest.enrich_records(records, sometimes_fails)

    assert enriched["filename"].tolist() == [
        "G002-001_1.txt",
        "G002-002_2.txt",
        "G002-003_3.txt",
    ]
    assert enriched["enrichment_status"].tolist() == ["ok", "error", "ok"]
    assert enriched.loc[1, "enrichment_error"] == "RuntimeError: unreadable payload"
    assert pd.isna(enriched.loc[1, "content_length"])
    assert enriched.loc[1, "path"] == records.loc[1, "path"]

    calls.clear()
    with pytest.raises(ManifestError, match=r"sometimes_fails.*G002-002_2\.txt.*RuntimeError: unreadable payload"):
        manifest.enrich_records(records, sometimes_fails, strict=True)
    assert calls == ["G002-001_1.txt", "G002-002_2.txt"]


@pytest.mark.parametrize("field", ["filename", "enrichment_status"])
def test_enrich_records_rejects_output_column_collisions_per_row(tmp_path: Path, field: str) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "G002-001_1.txt").write_text("sample")
    manifest = AtlasManifest(_schema())
    records = manifest.dataframe(tmp_path)

    enriched = manifest.enrich_records(records, lambda record: {field: record.filename})

    assert enriched.loc[0, "enrichment_status"] == "error"
    assert enriched.loc[0, "enrichment_error"] == (
        f"ValueError: enricher output field {field!r} would overwrite a manifest record column"
    )
    assert enriched.loc[0, "filename"] == "G002-001_1.txt"

    with pytest.raises(ManifestError, match=f"output field {field!r} would overwrite"):
        manifest.enrich_records(records, lambda record: {field: record.filename}, strict=True)


def test_enrich_records_registry_is_explicit_and_plugins_are_isolated(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "G002-001_1.txt").write_text("sample")
    manifest = AtlasManifest(_schema())
    records = manifest.dataframe(tmp_path)
    calls: list[str] = []

    def registered(record: ManifestEnrichmentInput) -> dict[str, object]:
        calls.append(record.filename)
        return {"plugin_value": "first"}

    def replacement(record: ManifestEnrichmentInput) -> dict[str, object]:
        return {"plugin_value": "replacement"}

    register_manifest_enricher("tests.example", registered)
    try:
        assert calls == []
        with pytest.raises(ManifestError, match="already registered"):
            register_manifest_enricher("tests.example", replacement)
        enriched = manifest.enrich_records(records, "tests.example")
        assert calls == ["G002-001_1.txt"]
        assert enriched.loc[0, "plugin_value"] == "first"
        assert enriched.loc[0, "enrichment_name"] == "tests.example"

        register_manifest_enricher("tests.example", replacement, replace=True)
        replaced = manifest.enrich_records(records, "tests.example")
        assert replaced.loc[0, "plugin_value"] == "replacement"
    finally:
        unregister_manifest_enricher("tests.example")

    with pytest.raises(ManifestError, match=r"unknown manifest enricher 'tests\.example'.*none"):
        manifest.enrich_records(records, "tests.example")


def test_enrich_records_validates_input_and_enricher_outputs(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "G002-001_1.txt").write_text("sample")
    manifest = AtlasManifest(_schema())
    records = manifest.dataframe(tmp_path)

    with pytest.raises(ManifestError, match="reserved enrichment columns: enrichment_status"):
        manifest.enrich_records(records.assign(enrichment_status="ok"), lambda record: {})
    with pytest.raises(ManifestError, match="missing required provenance columns: unit_id"):
        manifest.enrich_records(records.drop(columns="unit_id"), lambda record: {})

    invalid = manifest.enrich_records(records, lambda record: [record.filename])  # type: ignore[return-value]
    assert invalid.loc[0, "enrichment_status"] == "error"
    assert invalid.loc[0, "enrichment_error"] == "TypeError: enricher output must be a mapping, got list"


def test_related_records_returns_deterministic_one_to_one_bundles_by_unit(tmp_path: Path) -> None:
    second = _unit(tmp_path, "Run_230102")
    first = _unit(tmp_path, "Run_230101")
    for unit in (second, first):
        (unit / "data" / "S1.left").write_text("anchor")
        (unit / "data" / "S1.right").write_text("companion")
    manifest = AtlasManifest(_relationship_schema())
    records = manifest.dataframe(tmp_path, strict=True)
    records = pd.concat([records, records], ignore_index=True).sample(frac=1, random_state=7)

    result = manifest.related_records(records, "anchor_companion", strict=True)

    assert result.passed is True
    assert [bundle.unit_id for bundle in result.bundles] == ["Run_230101", "Run_230102"]
    assert [bundle.key for bundle in result.bundles] == [("S1",), ("S1",)]
    assert [bundle.left[0].path.name for bundle in result.bundles] == ["S1.left", "S1.left"]
    assert [bundle.right[0].path.name for bundle in result.bundles] == ["S1.right", "S1.right"]
    assert result.bundles[0].left[0].values["left_id"] == "S1"
    with pytest.raises(TypeError):
        result.bundles[0].left[0].values["left_id"] = "changed"  # type: ignore[index]

    relationship = manifest.relationships[0]
    assert relationship.name == "anchor_companion"
    assert relationship.left == ManifestRelationshipEndpointCapability("anchor", ("left_id",), True)
    assert relationship.right == ManifestRelationshipEndpointCapability("companion", ("right_id",), True)
    assert relationship.cardinality == "one_to_one"


def test_related_records_supports_one_to_many_companions(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "S1.left").write_text("anchor")
    (unit / "data" / "S1_b.right").write_text("second")
    (unit / "data" / "S1_a.right").write_text("first")
    manifest = AtlasManifest(_relationship_schema("one_to_many"))

    result = manifest.related_records(manifest.dataframe(tmp_path, strict=True), "anchor_companion")

    assert result.passed is True
    assert len(result.bundles) == 1
    assert [record.path.name for record in result.bundles[0].left] == ["S1.left"]
    assert [record.path.name for record in result.bundles[0].right] == ["S1_a.right", "S1_b.right"]


def test_related_records_reports_missing_and_honors_optional_endpoints(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "S1.left").write_text("anchor")
    records = AtlasManifest(_relationship_schema()).dataframe(tmp_path, strict=True)
    required_manifest = AtlasManifest(_relationship_schema())

    required = required_manifest.related_records(records, "anchor_companion")

    assert required.passed is False
    assert [issue.code for issue in required.issues] == ["manifest.relationship.missing_companion"]
    assert required.issues[0].endpoint == "right"
    assert required.issues[0].record_type == "companion"
    assert required.issues[0].paths[0].name == "S1.left"
    assert "missing required right companion" in required.issues[0].message
    with pytest.raises(ManifestError, match=r"manifest\.relationship\.missing_companion.*S1"):
        required_manifest.related_records(records, "anchor_companion", strict=True)

    optional_manifest = AtlasManifest(_relationship_schema(right_required=False))
    optional = optional_manifest.related_records(records, "anchor_companion", strict=True)
    assert optional.passed is True
    assert optional.bundles[0].right == ()


def test_related_records_distinguishes_duplicate_and_ambiguous_companions(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "S1_a.left").write_text("first anchor")
    (unit / "data" / "S1_b.left").write_text("second anchor")
    (unit / "data" / "S1.right").write_text("companion")
    manifest = AtlasManifest(_relationship_schema("one_to_many"))

    result = manifest.related_records(manifest.dataframe(tmp_path, strict=True), "anchor_companion")

    assert {issue.code for issue in result.issues} == {
        "manifest.relationship.duplicate_key",
        "manifest.relationship.ambiguous_companion",
    }
    duplicate = next(issue for issue in result.issues if issue.code.endswith("duplicate_key"))
    ambiguous = next(issue for issue in result.issues if issue.code.endswith("ambiguous_companion"))
    assert duplicate.endpoint == "left"
    assert [path.name for path in duplicate.paths] == ["S1_a.left", "S1_b.left"]
    assert "requires at most one" in duplicate.message
    assert ambiguous.endpoint == "left"
    assert "ambiguous left companions" in ambiguous.message


def test_related_records_reports_invalid_keys_and_rejects_bad_inputs(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "S1.left").write_text("anchor")
    manifest = AtlasManifest(_relationship_schema())
    records = manifest.dataframe(tmp_path, strict=True)

    invalid = manifest.related_records(records.assign(left_id=None), "anchor_companion")
    assert invalid.issues[0].code == "manifest.relationship.invalid_key"
    assert "key field 'left_id' is null" in invalid.issues[0].message

    with pytest.raises(ManifestError, match="unknown manifest relationship 'missing'"):
        manifest.related_records(records, "missing")
    with pytest.raises(ManifestError, match=r"missing left relationship fields.*left_id"):
        manifest.related_records(records.drop(columns="left_id"), "anchor_companion")


def test_manifest_capabilities_are_deterministic_and_immutable() -> None:
    manifest = AtlasManifest(_query_schema())
    capabilities = manifest.capabilities

    assert manifest.record_types == ("broken_file", "sample_file")
    assert manifest.record_groups == ("broken", "samples", "text")
    assert manifest.tags == (
        ManifestTagCapability("is_binary", False),
        ManifestTagCapability("is_binary", True),
        ManifestTagCapability("media_type", "application/x-bad"),
        ManifestTagCapability("media_type", "text/plain"),
    )
    assert capabilities == manifest.capabilities

    records = {record.record_type: record for record in capabilities.records}
    assert records["sample_file"].record_groups == ("samples", "text")
    assert records["sample_file"].tags == (
        ManifestTagCapability("is_binary", False),
        ManifestTagCapability("media_type", "text/plain"),
    )
    assert records["sample_file"].output_fields[-7:] == (
        "participant_id",
        "pool",
        "pool_number",
        "run_date",
        "selected",
        "source_label",
        "source_type",
    )
    assert manifest.output_fields == capabilities.output_fields
    assert "name" in capabilities.output_fields

    with pytest.raises(FrozenInstanceError):
        capabilities.record_types = ()
    with pytest.raises(FrozenInstanceError):
        records["sample_file"].record_groups = ()


def test_manifest_capabilities_cover_empty_and_table_declarations() -> None:
    empty = AtlasManifest(Schema(name="empty"))
    assert empty.record_types == ()
    assert empty.record_groups == ()
    assert empty.tags == ()
    assert empty.relationships == ()
    assert empty.capabilities.records == ()
    assert empty.output_fields == (
        "schema_name",
        "schema_version",
        "record_type",
        "unit_id",
        "path",
        "relative_path",
        "filename",
        "extension",
        "parse_status",
        "parse_error",
    )

    table = ManifestTableConfig(
        name="clinical",
        glob="clinical.csv",
        rename={"Participant": "clinical_id"},
        casts={"visit_number": "integer"},
        join=ManifestJoinConfig(left=["pool_number"], right=["external_pool"]),
    )
    output_fields = AtlasManifest(_schema(table=table)).output_fields
    assert {"clinical_id", "visit_number", "pool_number", "external_pool"} <= set(output_fields)
    assert "Participant" not in output_fields


def test_external_asset_key_is_extracted_and_joined(tmp_path: Path) -> None:
    asset_key = ManifestAssetKeyConfig(field="asset_filename", source="filename")
    manifest = AtlasManifest(_schema(asset_key=asset_key))
    unit = _unit(tmp_path)
    sample = unit / "data" / "G002-004_5.txt"
    sample.write_text("payload")

    records = manifest.dataframe(tmp_path, strict=True)
    external = pd.DataFrame(
        {
            "flowkit_sample_id": [sample.name],
            "event_count": [2500],
        }
    )
    joined = manifest.join_external_assets(
        records,
        external,
        record_type="sample_file",
        external_key="flowkit_sample_id",
    )

    assert records.loc[0, "asset_filename"] == sample.name
    assert joined.loc[0, "participant_id"] == "G002-004"
    assert joined.loc[0, "event_count"] == 2500
    assert "flowkit_sample_id" not in joined
    capability = manifest.capabilities.records[0]
    assert capability.asset_key == ManifestAssetKeyCapability("asset_filename", "filename")
    assert "asset_filename" in capability.output_fields

    relative_records = AtlasManifest(
        _schema(asset_key=ManifestAssetKeyConfig(field="asset_relative_path", source="relative_path"))
    ).dataframe(tmp_path, strict=True)
    assert relative_records.loc[0, "asset_relative_path"] == "data/G002-004_5.txt"


def test_external_asset_join_cardinality_is_explicit() -> None:
    manifest = AtlasManifest(_schema(asset_key=ManifestAssetKeyConfig(field="asset_filename")))
    one_record = pd.DataFrame({"record_type": ["sample_file"], "asset_filename": ["a.txt"], "biological_id": ["G001"]})
    duplicate_records = pd.concat([one_record, one_record], ignore_index=True)
    one_external = pd.DataFrame({"external_id": ["a.txt"], "value": [1]})
    duplicate_external = pd.DataFrame({"external_id": ["a.txt", "a.txt"], "value": [1, 2]})

    assert (
        len(
            manifest.join_external_assets(
                duplicate_records,
                one_external,
                record_type="sample_file",
                external_key="external_id",
                cardinality="many_to_one",
            )
        )
        == 2
    )
    one_to_many = manifest.join_external_assets(
        one_record,
        duplicate_external,
        record_type="sample_file",
        external_key="external_id",
        cardinality="one_to_many",
    )
    assert one_to_many["value"].tolist() == [1, 2]
    many_to_many = manifest.join_external_assets(
        duplicate_records,
        duplicate_external,
        record_type="sample_file",
        external_key="external_id",
        cardinality="many_to_many",
    )
    assert many_to_many["value"].tolist() == [1, 2, 1, 2]

    with pytest.raises(ManifestError, match=r"requires unique record key.*duplicates: 'a\.txt'"):
        manifest.join_external_assets(
            duplicate_records,
            one_external,
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match=r"requires unique external key.*duplicates: 'a\.txt'"):
        manifest.join_external_assets(
            one_record,
            duplicate_external,
            record_type="sample_file",
            external_key="external_id",
        )


def test_external_asset_join_rejects_key_and_column_errors() -> None:
    manifest = AtlasManifest(_schema(asset_key=ManifestAssetKeyConfig(field="asset_filename")))
    records = pd.DataFrame({"record_type": ["sample_file"], "asset_filename": ["a.txt"], "biological_id": ["G001"]})
    external = pd.DataFrame({"external_id": ["a.txt"], "value": [1]})

    with pytest.raises(ManifestError, match="does not declare an external asset key"):
        AtlasManifest(_schema()).join_external_assets(
            records,
            external,
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match="unknown manifest record type 'missing'"):
        manifest.join_external_assets(records, external, record_type="missing", external_key="external_id")
    with pytest.raises(ManifestError, match="missing required 'record_type'"):
        manifest.join_external_assets(
            records.drop(columns="record_type"),
            external,
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match="must contain only record type 'sample_file'"):
        manifest.join_external_assets(
            records.assign(record_type="broken_file"),
            external,
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match="'record_type' column contains null values"):
        manifest.join_external_assets(
            records.assign(record_type=None),
            external,
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match=r"manifest records is missing.*'asset_filename'"):
        manifest.join_external_assets(
            records.drop(columns="asset_filename"),
            external,
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match=r"external assets is missing.*'external_id'"):
        manifest.join_external_assets(
            records,
            external.drop(columns="external_id"),
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match=r"record key 'asset_filename': 'a\.txt'"):
        manifest.join_external_assets(
            records,
            external.assign(external_id="other.txt"),
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match="would overwrite record columns: biological_id"):
        manifest.join_external_assets(
            records,
            external.assign(biological_id="external"),
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match="contains null values"):
        manifest.join_external_assets(
            records.assign(asset_filename=None),
            external,
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match="must contain only strings"):
        manifest.join_external_assets(
            records,
            external.assign(external_id=1),
            record_type="sample_file",
            external_key="external_id",
        )
    duplicate_columns = pd.concat([external, external[["value"]]], axis="columns")
    with pytest.raises(ManifestError, match="external assets has duplicate columns: value"):
        manifest.join_external_assets(
            records,
            duplicate_columns,
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match="external assets column names must be strings"):
        manifest.join_external_assets(
            records,
            external.rename(columns={"value": 1}),
            record_type="sample_file",
            external_key="external_id",
        )
    with pytest.raises(ManifestError, match="unknown external asset join cardinality"):
        manifest.join_external_assets(
            records,
            external,
            record_type="sample_file",
            external_key="external_id",
            cardinality="invalid",  # type: ignore[arg-type]
        )


def test_attach_dataframe_one_to_one_preserves_manifest_order_and_provenance(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "G002-002_2.txt").write_text("second")
    (unit / "data" / "G002-001_1.txt").write_text("first")
    manifest = AtlasManifest(_schema())
    records = manifest.dataframe(tmp_path, strict=True)
    records.index = [10, 20]
    attachment = pd.DataFrame(
        {
            "external_id": ["G002-002", "G002-001"],
            "external_pool": [2, 1],
            "quality_score": [92, 97],
        }
    )

    attached = attach_dataframe(
        records,
        attachment,
        left_on=["participant_id", "pool"],
        right_on=["external_id", "external_pool"],
        cardinality="one_to_one",
    )
    through_manifest = manifest.attach_dataframe(
        records,
        attachment,
        left_on=["participant_id", "pool"],
        right_on=["external_id", "external_pool"],
        cardinality="one_to_one",
    )

    assert attached["participant_id"].tolist() == ["G002-001", "G002-002"]
    assert attached["quality_score"].tolist() == [97, 92]
    assert attached.index.tolist() == [0, 1]
    assert attached.columns[-1] == "quality_score"
    assert "external_id" not in attached
    assert "external_pool" not in attached
    assert attached["path"].tolist() == records["path"].tolist()
    assert "quality_score" not in records
    pd.testing.assert_frame_equal(attached, through_manifest)


def test_attach_dataframe_many_to_one_and_one_to_many_are_deterministic(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "G002-002_1.txt").write_text("second")
    (unit / "data" / "G002-001_1.txt").write_text("first")
    records = AtlasManifest(_schema()).dataframe(tmp_path, strict=True)

    many_to_one = attach_dataframe(
        records,
        {"external_pool": [1], "batch": ["A"]},
        left_on="pool",
        right_on="external_pool",
        cardinality="many_to_one",
    )
    assert many_to_one["participant_id"].tolist() == ["G002-001", "G002-002"]
    assert many_to_one["batch"].tolist() == ["A", "A"]

    one_to_many = attach_dataframe(
        records.iloc[[0]],
        [
            {"external_id": "G002-001", "result": "second"},
            {"external_id": "G002-001", "result": "first"},
        ],
        left_on="participant_id",
        right_on="external_id",
        cardinality="one_to_many",
    )
    assert one_to_many["result"].tolist() == ["second", "first"]
    assert one_to_many["path"].nunique() == 1


def test_attach_dataframe_validates_keys_nulls_and_cardinality(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "G002-002_1.txt").write_text("second")
    (unit / "data" / "G002-001_1.txt").write_text("first")
    records = AtlasManifest(_schema()).dataframe(tmp_path, strict=True)
    attachment = pd.DataFrame({"external_pool": [1], "value": [10]})

    with pytest.raises(ManifestError, match="manifest records are missing attachment keys: missing"):
        attach_dataframe(records, attachment, left_on="missing", right_on="external_pool")
    with pytest.raises(ManifestError, match="attachment is missing attachment keys: missing"):
        attach_dataframe(records, attachment, left_on="pool", right_on="missing")
    with pytest.raises(ManifestError, match="left and right keys must have equal lengths"):
        attach_dataframe(records, attachment, left_on=["pool", "unit_id"], right_on="external_pool")
    with pytest.raises(ManifestError, match="manifest records key columns contain null values: pool"):
        attach_dataframe(records.assign(pool=None), attachment, left_on="pool", right_on="external_pool")
    with pytest.raises(ManifestError, match="attachment key columns contain null values: external_pool"):
        attach_dataframe(
            records,
            attachment.assign(external_pool=None),
            left_on="pool",
            right_on="external_pool",
        )
    with pytest.raises(ManifestError, match=r"requires unique manifest keys.*\(1,\)"):
        attach_dataframe(
            records,
            attachment,
            left_on="pool",
            right_on="external_pool",
            cardinality="one_to_many",
        )
    with pytest.raises(ManifestError, match=r"requires unique attachment keys.*\(1,\)"):
        attach_dataframe(
            records.iloc[[0]],
            pd.concat([attachment, attachment], ignore_index=True),
            left_on="pool",
            right_on="external_pool",
            cardinality="many_to_one",
        )


def test_attach_dataframe_validates_collisions_and_explicit_suffixes(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "G002-001_1.txt").write_text("first")
    records = AtlasManifest(_schema()).dataframe(tmp_path, strict=True)
    attachment = pd.DataFrame(
        {
            "external_id": ["G002-001"],
            "source_type": ["analysis"],
        }
    )

    with pytest.raises(ManifestError, match="would overwrite manifest columns: source_type"):
        attach_dataframe(records, attachment, left_on="participant_id", right_on="external_id")
    with pytest.raises(ManifestError, match="reserved manifest columns: path"):
        attach_dataframe(
            records,
            attachment.assign(path="external"),
            left_on="participant_id",
            right_on="external_id",
            attachment_suffix="_analysis",
        )

    suffixed = attach_dataframe(
        records,
        attachment,
        left_on="participant_id",
        right_on="external_id",
        attachment_suffix="_analysis",
    )
    assert suffixed.loc[0, "source_type"] == "path"
    assert suffixed.loc[0, "source_type_analysis"] == "analysis"
    assert "source_type_x" not in suffixed
    assert "source_type_y" not in suffixed

    with pytest.raises(ManifestError, match="suffix creates duplicate attachment columns: source_type_analysis"):
        attach_dataframe(
            records,
            attachment.assign(source_type_analysis="existing"),
            left_on="participant_id",
            right_on="external_id",
            attachment_suffix="_analysis",
        )
    with pytest.raises(ManifestError, match="suffix must be a non-empty string"):
        attach_dataframe(
            records,
            attachment,
            left_on="participant_id",
            right_on="external_id",
            attachment_suffix="",
        )


def test_attach_dataframe_unmatched_policy_is_explicit(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "G002-002_2.txt").write_text("second")
    (unit / "data" / "G002-001_1.txt").write_text("first")
    records = AtlasManifest(_schema()).dataframe(tmp_path, strict=True)
    attachment = pd.DataFrame(
        {
            "external_id": ["G002-001", "unused"],
            "value": [10, 99],
        }
    )

    with pytest.raises(ManifestError, match=r"no match for manifest keys.*G002-002"):
        attach_dataframe(records, attachment, left_on="participant_id", right_on="external_id")

    kept = attach_dataframe(
        records,
        attachment,
        left_on="participant_id",
        right_on="external_id",
        unmatched="keep",
    )
    assert kept["participant_id"].tolist() == ["G002-001", "G002-002"]
    assert kept.loc[0, "value"] == 10
    assert pd.isna(kept.loc[1, "value"])
    assert 99 not in kept["value"].tolist()

    kept_without_attachments = attach_dataframe(
        records,
        attachment.iloc[0:0],
        left_on="participant_id",
        right_on="external_id",
        unmatched="keep",
    )
    assert len(kept_without_attachments) == 2
    assert kept_without_attachments["value"].isna().all()


def test_attach_dataframe_rejects_bad_frames_and_options(tmp_path: Path) -> None:
    unit = _unit(tmp_path)
    (unit / "data" / "G002-001_1.txt").write_text("first")
    records = AtlasManifest(_schema()).dataframe(tmp_path, strict=True)
    attachment = pd.DataFrame({"external_id": ["G002-001"], "value": [10]})

    with pytest.raises(ManifestError, match="missing required provenance columns: schema_name"):
        attach_dataframe(
            records.drop(columns="schema_name"), attachment, left_on="participant_id", right_on="external_id"
        )
    duplicate_columns = pd.concat([attachment, attachment[["value"]]], axis="columns")
    with pytest.raises(ManifestError, match="attachment has duplicate columns: value"):
        attach_dataframe(records, duplicate_columns, left_on="participant_id", right_on="external_id")
    with pytest.raises(ManifestError, match="attachment column names must be strings"):
        attach_dataframe(
            records,
            attachment.rename(columns={"value": 1}),
            left_on="participant_id",
            right_on="external_id",
        )
    with pytest.raises(ManifestError, match="unknown attachment cardinality"):
        attach_dataframe(
            records,
            attachment,
            left_on="participant_id",
            right_on="external_id",
            cardinality="invalid",  # type: ignore[arg-type]
        )
    with pytest.raises(ManifestError, match="unknown unmatched attachment policy"):
        attach_dataframe(
            records,
            attachment,
            left_on="participant_id",
            right_on="external_id",
            unmatched="invalid",  # type: ignore[arg-type]
        )


def test_bad_path_is_retained_and_strict_mode_raises(tmp_path: Path) -> None:
    unit = _unit(tmp_path, "not-a-run")
    sample = unit / "data" / "unexpected.txt"
    sample.write_text("payload")

    record = AtlasManifest(_schema()).record(sample)
    assert record.parse_status == "partial"
    assert "did not match" in (record.parse_error or "")

    with pytest.raises(ManifestError, match="did not match"):
        AtlasManifest(_schema()).record(sample, strict=True)


def test_file_outside_a_schema_unit_is_rejected(tmp_path: Path) -> None:
    sample = tmp_path / "G002-004_5.txt"
    sample.write_text("payload")

    with pytest.raises(ManifestError, match="could not locate"):
        AtlasManifest(_schema()).record(sample)


def test_csv_enrichment_explodes_one_file_to_multiple_entities(tmp_path: Path) -> None:
    table = ManifestTableConfig(
        name="sequencing",
        glob="metadata.csv",
        rename={"ptid": "sequenced_participant"},
        casts={"pool_number": "string"},
        join=ManifestJoinConfig(left=["pool_number"], right=["pool_number"], cardinality="one_to_many"),
    )
    unit = _unit(tmp_path)
    (unit / "data" / "G002-004_5.txt").write_text("payload")
    (tmp_path / "metadata.csv").write_text("ptid,pool_number\nG002-004,P5\nCONTROL,P5\n")

    frame = AtlasManifest(_schema(table=table)).dataframe(tmp_path, strict=True)

    assert frame["sequenced_participant"].tolist() == ["G002-004", "CONTROL"]
    assert frame["path"].nunique() == 1


def test_xlsx_enrichment_is_supported(tmp_path: Path) -> None:
    table = ManifestTableConfig(
        name="workbook",
        glob="metadata.xlsx",
        join=ManifestJoinConfig(left=["pool_number"], right=["pool_number"]),
    )
    unit = _unit(tmp_path)
    (unit / "data" / "G002-004_5.txt").write_text("payload")
    pd.DataFrame({"pool_number": ["P5"], "cells": [9000]}).to_excel(tmp_path / "metadata.xlsx", index=False)

    frame = AtlasManifest(_schema(table=table)).dataframe(tmp_path, strict=True)

    assert frame.loc[0, "cells"] == 9000


def test_missing_optional_table_keeps_base_record(tmp_path: Path) -> None:
    table = ManifestTableConfig(
        name="optional",
        glob="missing.csv",
        join=ManifestJoinConfig(left=["pool_number"], right=["pool_number"]),
    )
    unit = _unit(tmp_path)
    (unit / "data" / "G002-004_5.txt").write_text("payload")

    frame = AtlasManifest(_schema(table=table)).dataframe(tmp_path, strict=True)

    assert frame.loc[0, "parse_status"] == "ok"


def test_schema_rejects_invalid_manifest_declarations() -> None:
    descriptors = ManifestRecordConfig(
        name="described",
        glob="*.txt",
        groups=["samples", "text"],
        tags={"media_type": "text/plain", "indexed": True, "priority": 1},
    )
    assert descriptors.groups == ["samples", "text"]
    assert descriptors.tags == {"media_type": "text/plain", "indexed": True, "priority": 1}

    asset_key = ManifestAssetKeyConfig(field="asset_filename", source="relative_path")
    assert ManifestRecordConfig(name="asset", glob="*.txt", asset_key=asset_key).asset_key == asset_key

    with pytest.raises(ValidationError, match="invalid manifest extractor regex"):
        ManifestExtractorConfig(source="filename", regex="[")

    with pytest.raises(ValidationError, match="unknown fields"):
        ManifestRecordConfig(
            name="bad",
            glob="*.txt",
            derive={"participant_id": "{missing}"},
        )

    with pytest.raises(ValidationError, match="constant field names"):
        ManifestRecordConfig(name="bad", glob="*.txt", constants={"": "value"})

    with pytest.raises(ValidationError, match="reserved provenance"):
        ManifestRecordConfig(name="bad", glob="*.txt", constants={"parse_status": "ok"})

    with pytest.raises(ValidationError, match="duplicate manifest record groups"):
        ManifestRecordConfig(name="bad", glob="*.txt", groups=["fcs", "fcs"])

    with pytest.raises(ValidationError, match="group names must not be empty"):
        ManifestRecordConfig(name="bad", glob="*.txt", groups=[""])

    with pytest.raises(ValidationError, match="group names must not have surrounding whitespace"):
        ManifestRecordConfig(name="bad", glob="*.txt", groups=[" fcs"])

    with pytest.raises(ValidationError, match="tag names must not be empty"):
        ManifestRecordConfig(name="bad", glob="*.txt", tags={"": "value"})

    with pytest.raises(ValidationError):
        ManifestRecordConfig(name="bad", glob="*.txt", tags={"media_type": ["text/plain"]})  # type: ignore[list-item]

    with pytest.raises(ValidationError, match="must not have surrounding whitespace"):
        ManifestAssetKeyConfig(field=" asset_filename")

    with pytest.raises(ValidationError):
        ManifestAssetKeyConfig(field="asset_filename", source="path")  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="reserved provenance field: filename"):
        ManifestRecordConfig(
            name="bad",
            glob="*.txt",
            asset_key=ManifestAssetKeyConfig(field="filename"),
        )

    with pytest.raises(ValidationError, match="collides with another record field"):
        ManifestRecordConfig(
            name="bad",
            glob="*.txt",
            asset_key=ManifestAssetKeyConfig(field="sample_id"),
            extractors=[ManifestExtractorConfig(source="filename", regex=r"(?P<sample_id>.+)")],
        )

    with pytest.raises(ValidationError, match="equal lengths"):
        ManifestJoinConfig(left=["one"], right=["one", "two"])

    left = ManifestRelationshipEndpointConfig(record_type="left", fields=["sample_id"])
    right = ManifestRelationshipEndpointConfig(record_type="right", fields=["sample_id"])
    with pytest.raises(ValidationError, match="different record types"):
        ManifestRelationshipConfig(name="bad", left=left, right=left)
    with pytest.raises(ValidationError, match="equal lengths"):
        ManifestRelationshipConfig(
            name="bad",
            left=left,
            right=ManifestRelationshipEndpointConfig(record_type="right", fields=["sample_id", "visit_id"]),
        )

    records = [
        ManifestRecordConfig(
            name="left",
            glob="*.left",
            extractors=[ManifestExtractorConfig(source="filename", regex=r"(?P<sample_id>.+)\.left")],
        ),
        ManifestRecordConfig(
            name="right",
            glob="*.right",
            extractors=[ManifestExtractorConfig(source="filename", regex=r"(?P<sample_id>.+)\.right")],
        ),
    ]
    relationship = ManifestRelationshipConfig(name="pair", left=left, right=right)
    with pytest.raises(ValidationError, match="duplicate manifest relationship names: pair"):
        ManifestConfig(records=records, relationships=[relationship, relationship])
    with pytest.raises(ValidationError, match="references unknown record type 'missing'"):
        ManifestConfig(
            records=records,
            relationships=[
                ManifestRelationshipConfig(
                    name="bad",
                    left=ManifestRelationshipEndpointConfig(record_type="missing", fields=["sample_id"]),
                    right=right,
                )
            ],
        )
    with pytest.raises(ValidationError, match=r"fields not emitted.*visit_id"):
        ManifestConfig(
            records=records,
            relationships=[
                ManifestRelationshipConfig(
                    name="bad",
                    left=ManifestRelationshipEndpointConfig(record_type="left", fields=["visit_id"]),
                    right=right,
                )
            ],
        )


def test_builtin_diva_extracts_g002_filename_metadata(tmp_path: Path) -> None:
    unit = tmp_path / "Sort_RunDate230502_UploadDate230524"
    data = unit / "ClinicalSamples" / "DataFilesFromDV"
    summaries = unit / "ClinicalSamples" / "PopulationSummaryFilesFromDV"
    data.mkdir(parents=True)
    summaries.mkdir(parents=True)
    sample = data / "Sort_230502_S6C_G002-091_V401_eODGT8_PBMC_HT04_DV_Primary_T1_P02_a.fcs"
    sample.write_text("fcs")
    (summaries / "Sort_230502_S6C_G002-091_V401_eODGT8_PBMC_HT04_DV_Summary_T1_P02_a.csv").write_text("summary")

    record = AtlasManifest("facs-sort-diva", project_root=tmp_path).record(sample, strict=True)

    assert record.metadata["pubID"] == "G002-091"
    assert record.metadata["visit_id"] == "V401"
    assert record.metadata["pool_number"] == "P02"
    assert record.metadata["sort_date"] == pd.Timestamp("2023-05-02")


def test_facs_and_cellranger_manifests_join_through_sequencing_table(tmp_path: Path) -> None:
    facs_unit = tmp_path / "sorting" / "Sort_RunDate230808_UploadDate230808"
    melody = facs_unit / "ClinicalSamples" / "DataFilesFromMelody"
    melody.mkdir(parents=True)
    facs_file = melody / "KWTRPG003_230808_M2_G003-08-004_V257_eODGT8_PBMC_Chorus_Data_T2_P5a.fcs"
    facs_file.write_text("fcs")

    cellranger = tmp_path / "output" / "multi" / "SI-TT-B3"
    (cellranger / "outs").mkdir(parents=True)
    (cellranger / "outs" / "config.csv").write_text("[gene-expression]\nreference,/refs/gex\n")
    (cellranger / "outs" / "filtered_feature_bc_matrix.h5").write_text("matrix")
    (cellranger / "SC_MULTI_CS").mkdir()
    (cellranger / "_cmdline").write_text("cellranger multi")

    sequencing = tmp_path / "sequencing"
    sequencing.mkdir()
    (sequencing / "sequencing_manifest.csv").write_text(
        "ptid,timepoint,sorted_date,cells,hto,vdj_index,cso_index,pool_number,run_id\n"
        "G003-021,V257,230808,9000,C0251,SI-TT-B3,SI-TN-A2,P5,run-1\n"
        "RAMOS,na,230808,100,C0256,SI-TT-B3,SI-TN-A2,P1,run-1\n"
    )

    facs = AtlasManifest("facs-sort", project_root=tmp_path).dataframe(facs_file, strict=True)
    tenx = AtlasManifest("10x-cellranger-multi", project_root=tmp_path).dataframe(
        cellranger, context_root=tmp_path, strict=True
    )
    join_on = ["sort_date", "visit_id", "pool_number"]
    tenx_samples = tenx.loc[tenx["record_type"] == "config"]
    joined = facs.merge(tenx_samples, on=join_on, how="inner", validate="many_to_one")

    assert "participant_id" not in facs
    assert "participant_id" not in tenx
    assert len(tenx) == 4  # two selected files x clinical/control table rows
    # Both planes now name the participant `pubID`, so the join suffixes them.
    # The join key is [sort_date, visit_id, pool_number], not identity, so
    # nothing makes the two agree — and in this fixture they do not. Asserting
    # both sides keeps that visible; two different column names used to hide it.
    assert joined.loc[0, "pubID_x"] == "G003-08-004"  # parsed from the FCS name
    assert joined.loc[0, "pubID_y"] == "G003-021"  # from sequencing_manifest
    assert joined.loc[0, "vdj_index"] == "SI-TT-B3"


def test_builtin_facs_sort_exposes_native_consumer_records(tmp_path: Path) -> None:
    unit = tmp_path / "Sort_RunDate230807_UploadDate230827"
    melody = unit / "ClinicalSamples" / "DataFilesFromMelody"
    flowjo = unit / "ClinicalSamples" / "DataFilesFromFlowJo"
    stats = unit / "ClinicalSamples" / "DataStats"
    reports = unit / "ClinicalSamples" / "SortReports"
    for directory in (melody, flowjo, stats, reports):
        directory.mkdir(parents=True)

    (melody / "KWTRPG003_230807_M2_G003-072_V257_eODGT8_PBMC_Chorus_Data_T2_P3a.FCS").write_text("fcs")
    (flowjo / "KWTRPG003_230807_M2_G003-072_V257..wsp").write_text("workspace")
    (stats / "KWTRPG003_230807_M2_G003-072_V257_eODGT8_PBMC_Chorus_Summary_T2_P3a.xlsx").write_text("stats")
    (reports / "KWTRPG003_230807_M2_G003-072_V257_eODGT8_PBMC_Chorus_Data_T2_P3a..pdf").write_text("report")

    frame = AtlasManifest("facs-sort", project_root=tmp_path).dataframe(tmp_path, strict=True)

    assert set(frame["record_type"]) == {
        "data_stats_sample",
        "flowjo_workspace",
        "melody_fcs",
        "sort_report_sample",
    }
    fcs = frame.loc[frame["record_type"] == "melody_fcs"].iloc[0]
    assert fcs["pubID"] == "G003-072"
    assert fcs["fcs_filename"] == fcs["filename"]
    assert fcs["instrument"] == "M2"
    assert fcs["probe"] == "eODGT8"
    assert fcs["specimen"] == "PBMC"
    assert fcs["tube_id"] == "T2"
    assert fcs["pool_number"] == "P3"
    assert fcs["replicate"] == "a"
    assert fcs["run_date"] == pd.Timestamp("2023-08-07")
    assert fcs["upload_date"] == pd.Timestamp("2023-08-27")
    assert fcs["sort_date"] == pd.Timestamp("2023-08-07")
    assert not {"ptid", "sort_id", "sort_probe", "sample_type", "file_type", "pool_subset", "full_path"} & set(frame)

    manifest = AtlasManifest("facs-sort", project_root=tmp_path)
    assert manifest.dataframe(tmp_path, record_groups={"fcs"}, strict=True)["record_type"].tolist() == ["melody_fcs"]
    assert manifest.dataframe(tmp_path, record_groups={"workspaces"}, strict=True)["record_type"].tolist() == [
        "flowjo_workspace"
    ]
    assert set(manifest.dataframe(tmp_path, record_groups={"data_stats"}, strict=True)["record_type"]) == {
        "data_stats_sample"
    }
    assert manifest.dataframe(
        tmp_path,
        where={"media_type": "application/vnd.isac.fcs"},
        strict=True,
    )["record_type"].tolist() == ["melody_fcs"]
    assert manifest.dataframe(tmp_path, where={"extension": ".WSP"}, strict=True)["record_type"].tolist() == [
        "flowjo_workspace"
    ]
    pd.testing.assert_frame_equal(
        load_dataframe(
            tmp_path,
            schema="facs-sort",
            project_root=tmp_path,
            where={"media_type": "application/vnd.isac.fcs"},
            strict=True,
        ),
        manifest.dataframe(tmp_path, where={"media_type": "application/vnd.isac.fcs"}, strict=True),
    )
    capabilities = manifest.capabilities
    melody = next(record for record in capabilities.records if record.record_type == "melody_fcs")
    assert ManifestTagCapability("media_type", "application/vnd.isac.fcs") in melody.tags
    assert melody.asset_key == ManifestAssetKeyCapability("fcs_filename", "filename")
    assert {"pubID", "sort_date", "pool_number", "fcs_filename"} <= set(melody.output_fields)

    relationship = manifest.related_records(frame, "workspace_fcs", strict=True)
    assert relationship.passed is True
    assert len(relationship.bundles) == 1
    assert relationship.bundles[0].key == ("G003-072", "V257")
    assert relationship.bundles[0].left[0].record_type == "flowjo_workspace"
    assert relationship.bundles[0].right[0].record_type == "melody_fcs"

    fcs_records = manifest.dataframe(
        tmp_path,
        where={"media_type": "application/vnd.isac.fcs"},
        strict=True,
    )
    # The external key deliberately collides with a column the records already
    # carry, so the join must not produce _x/_y suffixes.
    flowkit = pd.DataFrame({"filename": [fcs["filename"]], "event_count": [10_000]})
    joined = manifest.join_external_assets(
        fcs_records,
        flowkit,
        record_type="melody_fcs",
        external_key="filename",
    )
    assert joined.loc[0, "pubID"] == "G003-072"
    assert joined.loc[0, "fcs_filename"] == fcs["filename"]
    assert joined.loc[0, "event_count"] == 10_000
    assert not {"filename_x", "filename_y"} & set(joined)
