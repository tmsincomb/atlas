"""Schema-driven metadata records and pandas manifests."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal, Optional, Protocol, Union
from typing import cast as type_cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from atlas.detect import detect
from atlas.schema import (
    ManifestAssetKeyConfig,
    ManifestRecordConfig,
    ManifestRelationshipConfig,
    ManifestRelationshipEndpointConfig,
    ManifestTableConfig,
    Schema,
    SchemaError,
    resolve_schema,
)


class ManifestError(Exception):
    """Raised when manifest discovery, extraction, or enrichment cannot complete."""


ManifestQueryValue = Optional[Union[str, int, float, bool]]
"""One exact-match scalar accepted by :meth:`AtlasManifest.dataframe` queries."""

ManifestTagValue = Union[str, int, float, bool]
"""One scalar value declared by a manifest record tag."""

ManifestJoinCardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]
"""Supported manifest join and relationship cardinalities."""

ManifestAttachmentUnmatched = Literal["error", "keep"]
"""Policies for manifest rows with no matching DataFrame attachment row."""

ManifestTableLike = Union[
    pd.DataFrame,
    Mapping[str, Collection[object]],
    Collection[Mapping[str, object]],
]
"""DataFrame and common record/column mappings accepted as attachment input."""

ManifestEnrichmentStatus = Literal["ok", "error", "not_selected"]
"""Per-row outcome emitted by :meth:`AtlasManifest.enrich_records`."""

ManifestRelationshipIssueCode = Literal[
    "manifest.relationship.invalid_key",
    "manifest.relationship.missing_companion",
    "manifest.relationship.duplicate_key",
    "manifest.relationship.ambiguous_companion",
]
"""Stable validation codes emitted by declarative manifest relationships."""

_MANIFEST_BASE_FIELDS = (
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

_MANIFEST_ENRICHMENT_FIELDS = ("enrichment_name", "enrichment_status", "enrichment_error")
_MANIFEST_ENRICHERS: dict[str, ManifestEnricher] = {}


@dataclass(frozen=True)
class ManifestEnrichmentInput:
    """Immutable path provenance and metadata passed to one file enricher."""

    schema_name: str
    schema_version: str
    record_type: str
    unit_id: str
    path: Path
    relative_path: str
    filename: str
    extension: str
    parse_status: str
    parse_error: str | None
    metadata: Mapping[str, object]


class ManifestEnricher(Protocol):
    """Callable contract for producing additional fields for one manifest record."""

    def __call__(self, record: ManifestEnrichmentInput, /) -> Mapping[str, object]:
        """Return new fields for ``record`` without mutating it."""


def _validated_enricher_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ManifestError("manifest enricher names must be non-empty strings without surrounding whitespace")
    return name


def register_manifest_enricher(name: str, enricher: ManifestEnricher, *, replace: bool = False) -> None:
    """Register an enricher under an explicit process-local name."""
    validated_name = _validated_enricher_name(name)
    if not callable(enricher):
        raise ManifestError(f"manifest enricher {validated_name!r} must be callable")
    if validated_name in _MANIFEST_ENRICHERS and not replace:
        raise ManifestError(f"manifest enricher {validated_name!r} is already registered")
    _MANIFEST_ENRICHERS[validated_name] = enricher


def unregister_manifest_enricher(name: str) -> None:
    """Remove one explicitly registered process-local enricher."""
    validated_name = _validated_enricher_name(name)
    if validated_name not in _MANIFEST_ENRICHERS:
        raise ManifestError(f"unknown manifest enricher {validated_name!r}")
    del _MANIFEST_ENRICHERS[validated_name]


@dataclass(frozen=True)
class ManifestAssetKeyCapability:
    """One immutable schema-declared external asset key."""

    field: str
    source: Literal["filename", "relative_path"]


@dataclass(frozen=True)
class ManifestTagCapability:
    """One immutable schema-declared manifest tag."""

    name: str
    value: ManifestTagValue


@dataclass(frozen=True)
class ManifestRecordCapability:
    """Immutable discovery metadata for one manifest record type."""

    record_type: str
    record_groups: tuple[str, ...] = ()
    tags: tuple[ManifestTagCapability, ...] = ()
    output_fields: tuple[str, ...] = ()
    asset_key: ManifestAssetKeyCapability | None = None


@dataclass(frozen=True)
class ManifestRelationshipEndpointCapability:
    """Immutable discovery metadata for one relationship endpoint."""

    record_type: str
    fields: tuple[str, ...]
    required: bool


@dataclass(frozen=True)
class ManifestRelationshipCapability:
    """Immutable discovery metadata for one schema-declared relationship."""

    name: str
    left: ManifestRelationshipEndpointCapability
    right: ManifestRelationshipEndpointCapability
    cardinality: ManifestJoinCardinality


@dataclass(frozen=True)
class ManifestCapabilities:
    """Immutable aggregate discovery metadata for one manifest schema."""

    records: tuple[ManifestRecordCapability, ...] = ()
    record_types: tuple[str, ...] = ()
    record_groups: tuple[str, ...] = ()
    tags: tuple[ManifestTagCapability, ...] = ()
    output_fields: tuple[str, ...] = ()
    relationships: tuple[ManifestRelationshipCapability, ...] = ()


@dataclass(frozen=True)
class ManifestRelatedRecord:
    """One immutable manifest row included in a related-record bundle."""

    record_type: str
    path: Path
    values: Mapping[str, object]


@dataclass(frozen=True)
class ManifestRelationshipIssue:
    """One stable validation finding for a schema-declared relationship."""

    code: ManifestRelationshipIssueCode
    relationship: str
    endpoint: Literal["left", "right"]
    record_type: str
    unit_id: str
    key: tuple[object, ...]
    paths: tuple[Path, ...]
    message: str


@dataclass(frozen=True)
class ManifestRelationshipBundle:
    """Deterministically grouped records for one unit and relationship key."""

    relationship: str
    unit_id: str
    key: tuple[object, ...]
    left: tuple[ManifestRelatedRecord, ...]
    right: tuple[ManifestRelatedRecord, ...]
    issues: tuple[ManifestRelationshipIssue, ...] = ()


@dataclass(frozen=True)
class ManifestRelationshipResult:
    """Bundles and validation issues produced for one relationship."""

    relationship: str
    bundles: tuple[ManifestRelationshipBundle, ...] = ()
    issues: tuple[ManifestRelationshipIssue, ...] = ()

    @property
    def passed(self) -> bool:
        """Whether every required companion and cardinality rule passed."""
        return not self.issues


class ManifestRecord(BaseModel):
    """One path-derived record before optional tabular enrichment."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    schema_name: str
    schema_version: str
    record_type: str
    unit_id: str
    path: str
    relative_path: str
    filename: str
    extension: str
    parse_status: Literal["ok", "partial", "unmatched"] = "ok"
    parse_error: Optional[str] = None  # noqa: UP045 - Pydantic evaluates this annotation on Python 3.9
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_flat_dict(self) -> dict[str, Any]:
        """Return provenance and extracted metadata as one dataframe-ready mapping."""
        data = self.model_dump(exclude={"metadata"})
        data.update(self.metadata)
        return data


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _matches_unit(unit: Path, schema: Schema) -> bool:
    """Apply the path-based detection guards that identify one unit directory."""
    cfg = schema.detection
    if not unit.is_dir():
        return False
    if any(not _is_within(unit / marker, unit) or not (unit / marker).exists() for marker in cfg.markers):
        return False
    if any(_is_within(unit / marker, unit) and (unit / marker).exists() for marker in cfg.exclude_if_markers):
        return False
    return not cfg.require_any_glob or any(
        match.is_file() and _is_within(match, unit) for pattern in cfg.require_any_glob for match in unit.glob(pattern)
    )


def _units_for_directory(path: Path, schema: Schema) -> list[Path]:
    resolved = path.resolve()
    if _matches_unit(resolved, schema) or schema.detection.landmark is None:
        return [resolved]
    units: set[Path] = set()
    for found in detect(resolved, schemas=[schema]):
        for unit_id in found.unit_ids:
            unit = (found.stage_path / unit_id).resolve()
            if _is_within(unit, resolved) and _matches_unit(unit, schema):
                units.add(unit)
    return sorted(units, key=lambda item: item.as_posix())


def _unit_for_file(path: Path, schema: Schema) -> Path | None:
    resolved = path.resolve()
    for parent in resolved.parents:
        if _matches_unit(parent, schema):
            return parent
    return None


def _cast_scalar(value: Any, cast: str, date_format: str | None) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if cast == "string":
        return str(value)
    if cast == "integer":
        return int(value)
    if cast == "float":
        return float(value)
    if cast == "boolean":
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
        raise ValueError(f"cannot cast {value!r} to boolean")
    if cast == "date":
        return pd.to_datetime(value, format=date_format, errors="raise")
    raise ValueError(f"unknown manifest cast {cast!r}")


def _extract_metadata(path: Path, unit: Path, rule: ManifestRecordConfig) -> tuple[dict[str, Any], list[str]]:
    relative = path.relative_to(unit).as_posix()
    sources = {
        "unit_name": unit.name,
        "relative_path": relative,
        "filename": path.name,
    }
    metadata: dict[str, Any] = dict(rule.constants)
    if rule.asset_key is not None:
        metadata[rule.asset_key.field] = sources[rule.asset_key.source]
    errors: list[str] = []
    for extractor in rule.extractors:
        match = re.fullmatch(extractor.regex, sources[extractor.source])
        if match is None:
            errors.append(f"{extractor.source} did not match {extractor.regex!r}")
            continue
        metadata.update(match.groupdict())

    for field_name, template in rule.derive.items():
        try:
            metadata[field_name] = template.format_map(metadata)
        except (KeyError, ValueError) as exc:
            errors.append(f"could not derive {field_name}: {exc}")

    for field_name, cast in rule.casts.items():
        if field_name not in metadata:
            continue
        try:
            metadata[field_name] = _cast_scalar(metadata[field_name], cast, rule.date_formats.get(field_name))
        except (TypeError, ValueError) as exc:
            errors.append(f"could not cast {field_name}: {exc}")
    return metadata, errors


def _record_for(path: Path, unit: Path, schema: Schema, rule: ManifestRecordConfig | None) -> ManifestRecord:
    relative = path.relative_to(unit).as_posix()
    if rule is None:
        return ManifestRecord(
            schema_name=schema.name,
            schema_version=schema.version,
            record_type="unmatched",
            unit_id=unit.name,
            path=str(path.resolve()),
            relative_path=relative,
            filename=path.name,
            extension=path.suffix.lower(),
            parse_status="unmatched",
            parse_error="file does not match any manifest record glob",
        )
    metadata, errors = _extract_metadata(path, unit, rule)
    return ManifestRecord(
        schema_name=schema.name,
        schema_version=schema.version,
        record_type=rule.name,
        unit_id=unit.name,
        path=str(path.resolve()),
        relative_path=relative,
        filename=path.name,
        extension=path.suffix.lower(),
        parse_status="partial" if errors else "ok",
        parse_error="; ".join(errors) if errors else None,
        metadata=metadata,
    )


def _rule_for_file(
    path: Path,
    unit: Path,
    schema: Schema,
    record_types: frozenset[str] | None = None,
) -> ManifestRecordConfig | None:
    target = path.resolve()
    for rule in schema.manifest.records:
        if record_types is not None and rule.name not in record_types:
            continue
        if any(match.is_file() and match.resolve() == target for match in unit.glob(rule.glob)):
            return rule
    return None


def _records_for_unit(
    unit: Path,
    schema: Schema,
    record_types: frozenset[str] | None = None,
) -> list[ManifestRecord]:
    records: list[ManifestRecord] = []
    seen: set[Path] = set()
    for rule in schema.manifest.records:
        if record_types is not None and rule.name not in record_types:
            continue
        for path in sorted(unit.glob(rule.glob), key=lambda item: item.as_posix()):
            resolved = path.resolve()
            if not path.is_file() or not _is_within(path, unit) or resolved in seen:
                continue
            seen.add(resolved)
            records.append(_record_for(path, unit, schema, rule))
    return records


def _record_output_fields(rule: ManifestRecordConfig) -> tuple[str, ...]:
    declared = set(rule.constants) | set(rule.derive)
    if rule.asset_key is not None:
        declared.add(rule.asset_key.field)
    for extractor in rule.extractors:
        declared.update(re.compile(extractor.regex).groupindex)
    return _MANIFEST_BASE_FIELDS + tuple(sorted(declared - set(_MANIFEST_BASE_FIELDS)))


def _record_capability(rule: ManifestRecordConfig) -> ManifestRecordCapability:
    tags = tuple(ManifestTagCapability(name, value) for name, value in sorted(rule.tags.items()))
    asset_key = (
        ManifestAssetKeyCapability(rule.asset_key.field, rule.asset_key.source) if rule.asset_key is not None else None
    )
    return ManifestRecordCapability(
        record_type=rule.name,
        record_groups=tuple(sorted(rule.groups)),
        tags=tags,
        output_fields=_record_output_fields(rule),
        asset_key=asset_key,
    )


def _relationship_endpoint_capability(
    endpoint: ManifestRelationshipEndpointConfig,
) -> ManifestRelationshipEndpointCapability:
    return ManifestRelationshipEndpointCapability(
        record_type=endpoint.record_type,
        fields=tuple(endpoint.fields),
        required=endpoint.required,
    )


def _relationship_capability(relationship: ManifestRelationshipConfig) -> ManifestRelationshipCapability:
    return ManifestRelationshipCapability(
        name=relationship.name,
        left=_relationship_endpoint_capability(relationship.left),
        right=_relationship_endpoint_capability(relationship.right),
        cardinality=relationship.cardinality,
    )


def _table_output_fields(table: ManifestTableConfig) -> set[str]:
    return set(table.rename.values()) | set(table.casts) | set(table.join.left) | set(table.join.right)


def _manifest_capabilities(schema: Schema) -> ManifestCapabilities:
    records = tuple(
        sorted((_record_capability(rule) for rule in schema.manifest.records), key=lambda item: item.record_type)
    )
    record_groups = tuple(sorted({group for record in records for group in record.record_groups}))
    tags_by_key = {
        (tag.name, type(tag.value).__name__, repr(tag.value)): tag for record in records for tag in record.tags
    }
    tags = tuple(tags_by_key[key] for key in sorted(tags_by_key))
    output_fields = set(_MANIFEST_BASE_FIELDS)
    output_fields.update(field for record in records for field in record.output_fields)
    for table in schema.manifest.tables:
        output_fields.update(_table_output_fields(table))
    trailing_fields = tuple(sorted(output_fields - set(_MANIFEST_BASE_FIELDS)))
    relationships = tuple(
        sorted(
            (_relationship_capability(relationship) for relationship in schema.manifest.relationships),
            key=lambda item: item.name,
        )
    )
    return ManifestCapabilities(
        records=records,
        record_types=tuple(record.record_type for record in records),
        record_groups=record_groups,
        tags=tags,
        output_fields=_MANIFEST_BASE_FIELDS + trailing_fields,
        relationships=relationships,
    )


def _glob_extension(pattern: str) -> str | None:
    """Return one literal final extension declared by a record glob, when unambiguous."""
    name = PurePosixPath(pattern).name
    _, separator, suffix = name.rpartition(".")
    if not separator or not suffix:
        return None

    extension: list[str] = []
    index = 0
    while index < len(suffix):
        character = suffix[index]
        if character.isalnum() or character in {"_", "+", "-"}:
            extension.append(character.lower())
            index += 1
            continue
        if character != "[":
            return None
        closing = suffix.find("]", index + 1)
        if closing == -1:
            return None
        options = suffix[index + 1 : closing]
        folded = {option.lower() for option in options}
        if not options or len(folded) != 1:
            return None
        extension.append(folded.pop())
        index = closing + 1
    return "." + "".join(extension)


def _query_type_name(value: object) -> str:
    return "null" if value is None else type(value).__name__


def _typed_equal(left: ManifestQueryValue, right: ManifestQueryValue) -> bool:
    return type(left) is type(right) and left == right


def _normalize_extension_query(value: ManifestQueryValue) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"manifest query field 'extension' expects str, got {_query_type_name(value)}")
    normalized = value.strip().lower()
    if not normalized.startswith("."):
        normalized = "." + normalized
    if re.fullmatch(r"\.[a-z0-9][a-z0-9_+-]*", normalized) is None:
        raise ManifestError(f"invalid manifest extension query value: {value!r}")
    return normalized


def _declared_query_values(schema: Schema, field: str) -> list[ManifestQueryValue]:
    tag_values: list[ManifestQueryValue] = [rule.tags[field] for rule in schema.manifest.records if field in rule.tags]
    constant_values: list[ManifestQueryValue] = [
        rule.constants[field] for rule in schema.manifest.records if field in rule.constants
    ]
    if tag_values and constant_values:
        raise ManifestError(f"ambiguous manifest query field {field!r}: declared as both a tag and a constant")
    if not tag_values and not constant_values:
        known = {"extension", "record_group"}
        known.update(name for rule in schema.manifest.records for name in rule.tags)
        known.update(name for rule in schema.manifest.records for name in rule.constants)
        raise ManifestError(f"unknown manifest query field {field!r}; known fields: {', '.join(sorted(known))}")
    return tag_values if tag_values else constant_values


def _normalized_reserved_query(
    schema: Schema,
    field: str,
    value: ManifestQueryValue,
) -> str | None:
    if field == "record_group":
        known_groups = {group for rule in schema.manifest.records for group in rule.groups}
        if not isinstance(value, str):
            raise ManifestError(f"manifest query field 'record_group' expects str, got {_query_type_name(value)}")
        if value not in known_groups:
            raise ManifestError(f"unknown manifest record group query value {value!r}")
        return value
    if field != "extension":
        return None

    extension = _normalize_extension_query(value)
    known_extensions = {
        declared for rule in schema.manifest.records if (declared := _glob_extension(rule.glob)) is not None
    }
    if extension not in known_extensions:
        raise ManifestError(f"unknown manifest extension query value {extension!r}")
    return extension


def _normalized_descriptor_query(
    schema: Schema,
    field: str,
    value: ManifestQueryValue,
) -> ManifestQueryValue:
    declared_values = _declared_query_values(schema, field)
    declared_types = {type(declared) for declared in declared_values}
    if type(value) not in declared_types:
        expected = ", ".join(sorted({_query_type_name(declared) for declared in declared_values}))
        raise ManifestError(f"manifest query field {field!r} expects {expected}, got {_query_type_name(value)}")
    if not any(_typed_equal(value, declared) for declared in declared_values):
        expected_values = ", ".join(sorted({repr(declared) for declared in declared_values}))
        raise ManifestError(
            f"unknown manifest query value {value!r} for field {field!r}; known values: {expected_values}"
        )
    return value


def _normalized_query(
    schema: Schema,
    where: Mapping[str, ManifestQueryValue] | None,
) -> dict[str, ManifestQueryValue]:
    if where is None:
        return {}
    if not isinstance(where, Mapping):
        raise ManifestError("manifest where query must be a mapping")

    normalized: dict[str, ManifestQueryValue] = {}
    reserved = {"extension", "record_group"}
    descriptor_names = {name for rule in schema.manifest.records for name in set(rule.tags) | set(rule.constants)}
    for field, value in where.items():
        if not isinstance(field, str) or not field.strip() or field != field.strip():
            raise ManifestError("manifest query field names must be non-empty strings without surrounding whitespace")
        if type(value) not in {str, int, float, bool, type(None)}:
            raise ManifestError(
                f"manifest query field {field!r} requires a scalar value, got {_query_type_name(value)}"
            )
        if field in reserved and field in descriptor_names:
            raise ManifestError(f"ambiguous manifest query field {field!r}: reserved and schema-declared")
        reserved_value = _normalized_reserved_query(schema, field, value)
        if reserved_value is not None:
            normalized[field] = reserved_value
            continue
        normalized[field] = _normalized_descriptor_query(schema, field, value)
    return normalized


def _rule_matches_query(rule: ManifestRecordConfig, where: Mapping[str, ManifestQueryValue]) -> bool:
    for field, expected in where.items():
        if field == "record_group":
            if expected not in rule.groups:
                return False
            continue
        if field == "extension":
            if _glob_extension(rule.glob) != expected:
                return False
            continue
        actual: ManifestQueryValue
        if field in rule.tags:
            actual = rule.tags[field]
        elif field in rule.constants:
            actual = rule.constants[field]
        else:
            return False
        if not _typed_equal(actual, expected):
            return False
    return True


def _query_record_types(
    schema: Schema,
    where: Mapping[str, ManifestQueryValue] | None,
) -> frozenset[str] | None:
    normalized = _normalized_query(schema, where)
    if not normalized:
        return None
    selected = frozenset(rule.name for rule in schema.manifest.records if _rule_matches_query(rule, normalized))
    if not selected:
        raise ManifestError("manifest where predicates are contradictory; no record type matches all predicates")
    return selected


def _selected_record_types(
    schema: Schema,
    record_types: Collection[str] | None,
    record_groups: Collection[str] | None,
    where: Mapping[str, ManifestQueryValue] | None,
) -> frozenset[str] | None:
    known = {rule.name for rule in schema.manifest.records}
    selections: list[frozenset[str]] = []
    if record_types is not None:
        selected_types = frozenset(record_types)
        unknown_types = selected_types - known
        if unknown_types:
            raise ManifestError(f"unknown manifest record types: {', '.join(sorted(unknown_types))}")
        selections.append(selected_types)

    if record_groups is not None:
        selected_groups = frozenset(record_groups)
        known_groups = {group for rule in schema.manifest.records for group in rule.groups}
        unknown_groups = selected_groups - known_groups
        if unknown_groups:
            raise ManifestError(f"unknown manifest record groups: {', '.join(sorted(unknown_groups))}")
        selections.append(
            frozenset(rule.name for rule in schema.manifest.records if selected_groups.intersection(rule.groups))
        )

    query_types = _query_record_types(schema, where)
    if query_types is not None:
        selections.append(query_types)
    if not selections:
        return None

    selected = frozenset.intersection(*selections)
    if not selected and all(selections):
        raise ManifestError("manifest selectors are contradictory; no record type matches all selectors")
    return selected


def _asset_key_config(schema: Schema, record_type: str) -> ManifestAssetKeyConfig:
    rule = next((candidate for candidate in schema.manifest.records if candidate.name == record_type), None)
    if rule is None:
        known = ", ".join(sorted(candidate.name for candidate in schema.manifest.records))
        raise ManifestError(f"unknown manifest record type {record_type!r}; known record types: {known}")
    if rule.asset_key is None:
        raise ManifestError(f"manifest record type {record_type!r} does not declare an external asset key")
    return rule.asset_key


def _join_frame_columns(frame: pd.DataFrame, label: str) -> None:
    if any(not isinstance(column, str) for column in frame.columns):
        raise ManifestError(f"{label} column names must be strings")
    duplicates = sorted(str(column) for column in frame.columns[frame.columns.duplicated()])
    if duplicates:
        raise ManifestError(f"{label} has duplicate columns: {', '.join(duplicates)}")


def _join_key_values(frame: pd.DataFrame, key: str, label: str) -> pd.Series[Any]:
    if key not in frame:
        raise ManifestError(f"{label} is missing external asset join key {key!r}")
    values: pd.Series[Any] = frame[key]
    if bool(values.isna().any()):
        raise ManifestError(f"{label} external asset join key {key!r} contains null values")
    if any(not isinstance(value, str) for value in values.tolist()):
        raise ManifestError(f"{label} external asset join key {key!r} must contain only strings")
    return values


def _join_key_summary(values: pd.Series[Any]) -> str:
    by_identity = {(type(value).__name__, repr(value)): repr(value) for value in values.tolist()}
    ordered = [by_identity[key] for key in sorted(by_identity)]
    displayed = ordered[:5]
    if len(ordered) > len(displayed):
        displayed.append(f"... ({len(ordered) - len(displayed)} more)")
    return ", ".join(displayed)


def _duplicate_join_keys(values: pd.Series[Any]) -> pd.Series[Any]:
    return type_cast("pd.Series[Any]", values[values.duplicated(keep=False)])


def _validate_join_cardinality(
    record_values: pd.Series[Any],
    external_values: pd.Series[Any],
    record_key: str,
    external_key: str,
    cardinality: ManifestJoinCardinality,
) -> None:
    if cardinality in {"one_to_one", "one_to_many"}:
        duplicates = _duplicate_join_keys(record_values)
        if not duplicates.empty:
            raise ManifestError(
                f"external asset join cardinality {cardinality!r} requires unique record key {record_key!r}; "
                f"duplicates: {_join_key_summary(duplicates)}"
            )
    if cardinality in {"one_to_one", "many_to_one"}:
        duplicates = _duplicate_join_keys(external_values)
        if not duplicates.empty:
            raise ManifestError(
                f"external asset join cardinality {cardinality!r} requires unique external key {external_key!r}; "
                f"duplicates: {_join_key_summary(duplicates)}"
            )


def _join_external_asset_frames(
    records: pd.DataFrame,
    external: pd.DataFrame,
    *,
    record_type: str,
    record_key: str,
    external_key: str,
    cardinality: ManifestJoinCardinality,
) -> pd.DataFrame:
    supported_cardinalities = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
    if cardinality not in supported_cardinalities:
        raise ManifestError(f"unknown external asset join cardinality: {cardinality!r}")
    _join_frame_columns(records, "manifest records")
    _join_frame_columns(external, "external assets")

    if "record_type" not in records:
        raise ManifestError("manifest records are missing required 'record_type' column")
    record_type_values: pd.Series[Any] = records["record_type"]
    if bool(record_type_values.isna().any()):
        raise ManifestError("manifest records 'record_type' column contains null values")
    if any(not isinstance(value, str) for value in record_type_values.tolist()):
        raise ManifestError("manifest records 'record_type' column must contain only strings")
    actual_record_types = sorted(set(record_type_values.tolist()))
    if actual_record_types and actual_record_types != [record_type]:
        raise ManifestError(
            f"manifest records must contain only record type {record_type!r}; found: {', '.join(actual_record_types)}"
        )

    record_values = _join_key_values(records, record_key, "manifest records")
    external_values = _join_key_values(external, external_key, "external assets")
    _validate_join_cardinality(record_values, external_values, record_key, external_key, cardinality)

    missing = record_values[~record_values.isin(external_values)]
    if not missing.empty:
        raise ManifestError(
            f"external assets have no match for record key {record_key!r}: {_join_key_summary(missing)}"
        )

    payload_columns = set(external.columns) - {external_key}
    collisions = sorted(set(records.columns) & payload_columns)
    if collisions:
        raise ManifestError(f"external asset join would overwrite record columns: {', '.join(collisions)}")

    try:
        indexed_external = external.set_index(external_key, drop=True)
        joined = records.join(indexed_external, on=record_key, how="left", sort=False, validate=cardinality)
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"external asset join failed: {exc}") from None
    return joined.reset_index(drop=True)


def _attachment_frame(attachment: ManifestTableLike) -> pd.DataFrame:
    if isinstance(attachment, pd.DataFrame):
        return attachment.copy()
    constructor_value: Any = attachment
    try:
        return pd.DataFrame(constructor_value)
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"could not convert attachment to a DataFrame: {exc}") from None


def _normalized_attachment_keys(keys: str | Sequence[str], label: str) -> tuple[str, ...]:
    values: tuple[str, ...]
    if isinstance(keys, str):
        values = (keys,)
    elif isinstance(keys, Sequence):
        values = tuple(keys)
    else:
        raise ManifestError(f"attachment {label} keys must be a string or ordered sequence of strings")
    if not values:
        raise ManifestError(f"attachment {label} keys must not be empty")
    if any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in values):
        raise ManifestError(f"attachment {label} keys must be non-empty strings without surrounding whitespace")
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ManifestError(f"attachment {label} keys contain duplicates: {', '.join(duplicates)}")
    return values


def _validate_attachment_columns(
    records: pd.DataFrame,
    attachment: pd.DataFrame,
    left_keys: tuple[str, ...],
    right_keys: tuple[str, ...],
) -> None:
    _join_frame_columns(records, "manifest records")
    _join_frame_columns(attachment, "attachment")
    missing_provenance = sorted(set(_MANIFEST_BASE_FIELDS) - set(records.columns))
    if missing_provenance:
        raise ManifestError(
            f"manifest records are missing required provenance columns: {', '.join(missing_provenance)}"
        )
    missing_left = sorted(set(left_keys) - set(records.columns))
    missing_right = sorted(set(right_keys) - set(attachment.columns))
    if missing_left:
        raise ManifestError(f"manifest records are missing attachment keys: {', '.join(missing_left)}")
    if missing_right:
        raise ManifestError(f"attachment is missing attachment keys: {', '.join(missing_right)}")


def _validate_attachment_key_values(
    frame: pd.DataFrame,
    keys: tuple[str, ...],
    label: str,
) -> None:
    null_keys = [key for key in keys if bool(frame[key].isna().any())]
    if null_keys:
        raise ManifestError(f"{label} key columns contain null values: {', '.join(null_keys)}")
    for key in keys:
        for value in frame[key].tolist():
            try:
                hash(value)
            except TypeError:
                raise ManifestError(
                    f"{label} key column {key!r} contains unhashable {type(value).__name__} value"
                ) from None


def _attachment_key_summary(frame: pd.DataFrame, keys: tuple[str, ...]) -> str:
    values = {repr(tuple(row)) for row in frame.loc[:, list(keys)].drop_duplicates().to_numpy().tolist()}
    ordered = sorted(values)
    displayed = ordered[:5]
    if len(ordered) > len(displayed):
        displayed.append(f"... ({len(ordered) - len(displayed)} more)")
    return ", ".join(displayed)


def _validate_attachment_cardinality(
    records: pd.DataFrame,
    attachment: pd.DataFrame,
    left_keys: tuple[str, ...],
    right_keys: tuple[str, ...],
    cardinality: ManifestJoinCardinality,
) -> None:
    supported = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
    if cardinality not in supported:
        raise ManifestError(f"unknown attachment cardinality: {cardinality!r}")
    if cardinality in {"one_to_one", "one_to_many"}:
        duplicates = records.loc[records.duplicated(list(left_keys), keep=False)]
        if not duplicates.empty:
            raise ManifestError(
                f"attachment cardinality {cardinality!r} requires unique manifest keys {left_keys!r}; "
                f"duplicates: {_attachment_key_summary(duplicates, left_keys)}"
            )
    if cardinality in {"one_to_one", "many_to_one"}:
        duplicates = attachment.loc[attachment.duplicated(list(right_keys), keep=False)]
        if not duplicates.empty:
            raise ManifestError(
                f"attachment cardinality {cardinality!r} requires unique attachment keys {right_keys!r}; "
                f"duplicates: {_attachment_key_summary(duplicates, right_keys)}"
            )


def _attachment_payload_renames(
    records: pd.DataFrame,
    attachment: pd.DataFrame,
    right_keys: tuple[str, ...],
    attachment_suffix: str | None,
) -> tuple[list[str], dict[str, str]]:
    payload = [column for column in attachment.columns if column not in right_keys]
    reserved_fields = set(_MANIFEST_BASE_FIELDS) | set(_MANIFEST_ENRICHMENT_FIELDS)
    reserved = sorted(set(payload) & reserved_fields)
    if reserved:
        raise ManifestError(f"attachment payload uses reserved manifest columns: {', '.join(reserved)}")
    collisions = sorted(set(payload) & set(records.columns))
    if attachment_suffix is not None and (
        not isinstance(attachment_suffix, str)
        or not attachment_suffix
        or attachment_suffix != attachment_suffix.strip()
    ):
        raise ManifestError("attachment suffix must be a non-empty string without surrounding whitespace")
    if collisions and attachment_suffix is None:
        raise ManifestError(f"attachment payload would overwrite manifest columns: {', '.join(collisions)}")
    renames = {column: f"{column}{attachment_suffix}" for column in collisions}
    output_payload = [renames.get(column, column) for column in payload]
    duplicate_outputs = sorted({column for column in output_payload if output_payload.count(column) > 1})
    record_collisions = sorted(set(output_payload) & set(records.columns))
    key_collisions = sorted(set(output_payload) & set(right_keys))
    suffixed_reserved = sorted(set(output_payload) & reserved_fields)
    if duplicate_outputs or record_collisions or key_collisions or suffixed_reserved:
        details: list[str] = []
        if duplicate_outputs:
            details.append(f"duplicate attachment columns: {', '.join(duplicate_outputs)}")
        if record_collisions:
            details.append(f"manifest column collisions: {', '.join(record_collisions)}")
        if key_collisions:
            details.append(f"attachment key collisions: {', '.join(key_collisions)}")
        if suffixed_reserved:
            details.append(f"reserved manifest columns: {', '.join(suffixed_reserved)}")
        raise ManifestError("attachment suffix creates " + " and ".join(details))
    return output_payload, renames


def _temporary_attachment_column(occupied: set[str], stem: str) -> str:
    candidate = f"__atlas_{stem}__"
    index = 2
    while candidate in occupied:
        candidate = f"__atlas_{stem}_{index}__"
        index += 1
    occupied.add(candidate)
    return candidate


def _attach_dataframe_frames(
    records: pd.DataFrame,
    attachment: pd.DataFrame,
    *,
    left_keys: tuple[str, ...],
    right_keys: tuple[str, ...],
    cardinality: ManifestJoinCardinality,
    unmatched: ManifestAttachmentUnmatched,
    attachment_suffix: str | None,
) -> pd.DataFrame:
    if unmatched not in {"error", "keep"}:
        raise ManifestError(f"unknown unmatched attachment policy: {unmatched!r}")
    _validate_attachment_columns(records, attachment, left_keys, right_keys)
    _validate_attachment_key_values(records, left_keys, "manifest records")
    _validate_attachment_key_values(attachment, right_keys, "attachment")
    _validate_attachment_cardinality(records, attachment, left_keys, right_keys, cardinality)
    output_payload, payload_renames = _attachment_payload_renames(records, attachment, right_keys, attachment_suffix)
    if records.empty:
        result = records.copy().reset_index(drop=True)
        payload_sources = [column for column in attachment.columns if column not in right_keys]
        for source, output in zip(payload_sources, output_payload):
            result[output] = pd.Series(dtype=attachment[source].dtype)
        return result
    if attachment.empty:
        if unmatched == "error":
            raise ManifestError(
                f"attachment has no match for manifest keys {left_keys!r}: "
                f"{_attachment_key_summary(records, left_keys)}"
            )
        result = records.copy().reset_index(drop=True)
        for output in output_payload:
            result[output] = pd.Series([pd.NA] * len(result), dtype=object)
        return result

    occupied = set(records.columns) | set(attachment.columns) | set(output_payload)
    left_order = _temporary_attachment_column(occupied, "left_order")
    right_order = _temporary_attachment_column(occupied, "right_order")
    match_marker = _temporary_attachment_column(occupied, "attachment_match")
    temporary_right_keys = tuple(
        _temporary_attachment_column(occupied, f"right_key_{index}") for index in range(len(right_keys))
    )

    left_frame = records.copy().reset_index(drop=True)
    left_frame[left_order] = range(len(left_frame))
    right_frame = attachment.rename(columns=payload_renames).copy().reset_index(drop=True)
    right_frame[right_order] = range(len(right_frame))
    right_frame[match_marker] = True
    right_frame = right_frame.rename(columns=dict(zip(right_keys, temporary_right_keys)))
    try:
        joined = left_frame.merge(
            right_frame,
            how="left",
            left_on=list(left_keys),
            right_on=list(temporary_right_keys),
            sort=False,
            validate=cardinality,
        )
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"DataFrame attachment failed: {exc}") from None

    unmatched_rows = type_cast(pd.DataFrame, joined.loc[joined[match_marker].isna()])
    if unmatched == "error" and not unmatched_rows.empty:
        raise ManifestError(
            f"attachment has no match for manifest keys {left_keys!r}: "
            f"{_attachment_key_summary(unmatched_rows, left_keys)}"
        )
    joined = joined.sort_values([left_order, right_order], kind="stable", na_position="last")
    output_columns = list(records.columns) + output_payload
    return type_cast(pd.DataFrame, joined.loc[:, output_columns].reset_index(drop=True))


def attach_dataframe(
    records: pd.DataFrame,
    attachment: ManifestTableLike,
    *,
    left_on: str | Sequence[str],
    right_on: str | Sequence[str],
    cardinality: ManifestJoinCardinality = "many_to_one",
    unmatched: ManifestAttachmentUnmatched = "error",
    attachment_suffix: str | None = None,
) -> pd.DataFrame:
    """Attach table-like fields to manifest rows without overwriting provenance."""
    left_keys = _normalized_attachment_keys(left_on, "left")
    right_keys = _normalized_attachment_keys(right_on, "right")
    if len(left_keys) != len(right_keys):
        raise ManifestError("attachment left and right keys must have equal lengths")
    return _attach_dataframe_frames(
        records,
        _attachment_frame(attachment),
        left_keys=left_keys,
        right_keys=right_keys,
        cardinality=cardinality,
        unmatched=unmatched,
        attachment_suffix=attachment_suffix,
    )


@dataclass(frozen=True)
class _RelationshipRow:
    ordinal: int
    unit_id: str
    key: tuple[object, ...]
    identity: tuple[object, ...]
    record: ManifestRelatedRecord


def _relationship_config(schema: Schema, name: str) -> ManifestRelationshipConfig:
    relationship = next((candidate for candidate in schema.manifest.relationships if candidate.name == name), None)
    if relationship is None:
        known = ", ".join(sorted(candidate.name for candidate in schema.manifest.relationships)) or "none"
        raise ManifestError(f"unknown manifest relationship {name!r}; known relationships: {known}")
    return relationship


def _validate_relationship_frame(records: pd.DataFrame, relationship: ManifestRelationshipConfig) -> None:
    _join_frame_columns(records, "manifest records")
    required = {"record_type", "unit_id", "path"}
    missing = sorted(required - set(records.columns))
    if missing:
        raise ManifestError(f"manifest records are missing required relationship columns: {', '.join(missing)}")
    record_types: pd.Series[Any] = records["record_type"]
    if bool(record_types.isna().any()):
        raise ManifestError("manifest records 'record_type' column contains null values")
    if any(not isinstance(value, str) for value in record_types.tolist()):
        raise ManifestError("manifest records 'record_type' column must contain only strings")
    present_types = set(record_types.tolist())
    for label, endpoint in (("left", relationship.left), ("right", relationship.right)):
        if endpoint.record_type not in present_types:
            continue
        missing_fields = sorted(set(endpoint.fields) - set(records.columns))
        if missing_fields:
            raise ManifestError(
                f"manifest records are missing {label} relationship fields for {endpoint.record_type!r}: "
                f"{', '.join(missing_fields)}"
            )


def _is_null_relationship_value(value: object) -> bool:
    candidate: Any = value
    try:
        return bool(pd.isna(candidate))
    except (TypeError, ValueError):
        return False


def _relationship_key(row: Mapping[str, object], fields: list[str]) -> tuple[tuple[object, ...], tuple[object, ...]]:
    values = tuple(row[field] for field in fields)
    for field, value in zip(fields, values):
        if _is_null_relationship_value(value):
            raise ValueError(f"key field {field!r} is null")
        try:
            hash(value)
        except TypeError:
            raise ValueError(f"key field {field!r} contains unhashable {type(value).__name__}") from None
    identity = tuple((type(value).__module__, type(value).__qualname__, value) for value in values)
    return values, identity


def _related_record(row: Mapping[str, object]) -> ManifestRelatedRecord:
    record_type = row["record_type"]
    path = row["path"]
    if not isinstance(record_type, str):
        raise TypeError("record_type must be a string")
    if not isinstance(path, str):
        raise TypeError("path must be a string")
    return ManifestRelatedRecord(record_type, Path(path), MappingProxyType(dict(row)))


def _relationship_issue(
    *,
    code: ManifestRelationshipIssueCode,
    relationship: ManifestRelationshipConfig,
    endpoint: Literal["left", "right"],
    unit_id: str,
    key: tuple[object, ...],
    paths: tuple[Path, ...],
    message: str,
) -> ManifestRelationshipIssue:
    endpoint_config = relationship.left if endpoint == "left" else relationship.right
    return ManifestRelationshipIssue(
        code=code,
        relationship=relationship.name,
        endpoint=endpoint,
        record_type=endpoint_config.record_type,
        unit_id=unit_id,
        key=key,
        paths=paths,
        message=message,
    )


def _endpoint_rows(
    records: pd.DataFrame,
    relationship: ManifestRelationshipConfig,
    endpoint: ManifestRelationshipEndpointConfig,
    label: Literal["left", "right"],
) -> tuple[list[_RelationshipRow], list[ManifestRelationshipIssue]]:
    columns = [str(column) for column in records.columns]
    rows: list[_RelationshipRow] = []
    issues: list[ManifestRelationshipIssue] = []
    for ordinal, values in enumerate(records.itertuples(index=False)):
        row = dict(zip(columns, values))
        if row["record_type"] != endpoint.record_type:
            continue
        unit_id = row["unit_id"]
        if not isinstance(unit_id, str):
            raise ManifestError(f"relationship record {row['path']!r} has non-string unit_id")
        try:
            record = _related_record(row)
            key, identity = _relationship_key(row, endpoint.fields)
        except (TypeError, ValueError) as exc:
            path = Path(str(row["path"]))
            key = tuple(row[field] for field in endpoint.fields)
            message = f"relationship {relationship.name!r} {label} record {str(path)!r} has invalid key: {exc}"
            issues.append(
                _relationship_issue(
                    code="manifest.relationship.invalid_key",
                    relationship=relationship,
                    endpoint=label,
                    unit_id=unit_id,
                    key=key,
                    paths=(path,),
                    message=message,
                )
            )
            continue
        rows.append(_RelationshipRow(ordinal, unit_id, key, identity, record))
    rows.sort(key=lambda item: (item.unit_id, item.record.path.as_posix(), item.ordinal))
    unique_rows: list[_RelationshipRow] = []
    by_path: dict[tuple[str, Path], _RelationshipRow] = {}
    for relationship_row in rows:
        path_identity = (relationship_row.unit_id, relationship_row.record.path)
        previous = by_path.get(path_identity)
        if previous is None:
            by_path[path_identity] = relationship_row
            unique_rows.append(relationship_row)
            continue
        if previous.identity != relationship_row.identity:
            message = (
                f"relationship {relationship.name!r} {label} record {str(relationship_row.record.path)!r} has "
                f"inconsistent duplicate-row keys {previous.key!r} and {relationship_row.key!r}"
            )
            issues.append(
                _relationship_issue(
                    code="manifest.relationship.invalid_key",
                    relationship=relationship,
                    endpoint=label,
                    unit_id=relationship_row.unit_id,
                    key=relationship_row.key,
                    paths=(relationship_row.record.path,),
                    message=message,
                )
            )
    return unique_rows, issues


def _group_relationship_rows(
    rows: list[_RelationshipRow],
) -> dict[tuple[str, tuple[object, ...]], tuple[tuple[object, ...], list[ManifestRelatedRecord]]]:
    grouped: dict[tuple[str, tuple[object, ...]], tuple[tuple[object, ...], list[ManifestRelatedRecord]]] = {}
    for row in rows:
        group_identity = (row.unit_id, row.identity)
        if group_identity not in grouped:
            grouped[group_identity] = (row.key, [])
        grouped[group_identity][1].append(row.record)
    return grouped


def _relationship_key_text(relationship: ManifestRelationshipConfig, key: tuple[object, ...]) -> str:
    values = ", ".join(f"{field}={value!r}" for field, value in zip(relationship.left.fields, key))
    return f"({values})"


def _duplicate_relationship_issue(
    relationship: ManifestRelationshipConfig,
    endpoint: Literal["left", "right"],
    unit_id: str,
    key: tuple[object, ...],
    records: tuple[ManifestRelatedRecord, ...],
) -> ManifestRelationshipIssue:
    endpoint_config = relationship.left if endpoint == "left" else relationship.right
    message = (
        f"relationship {relationship.name!r} {endpoint} endpoint {endpoint_config.record_type!r} has "
        f"{len(records)} records for unit {unit_id!r} key {_relationship_key_text(relationship, key)}; "
        f"cardinality {relationship.cardinality!r} requires at most one"
    )
    return _relationship_issue(
        code="manifest.relationship.duplicate_key",
        relationship=relationship,
        endpoint=endpoint,
        unit_id=unit_id,
        key=key,
        paths=tuple(record.path for record in records),
        message=message,
    )


def _ambiguous_relationship_issue(
    relationship: ManifestRelationshipConfig,
    endpoint: Literal["left", "right"],
    unit_id: str,
    key: tuple[object, ...],
    records: tuple[ManifestRelatedRecord, ...],
    counterpart: tuple[ManifestRelatedRecord, ...],
) -> ManifestRelationshipIssue:
    endpoint_config = relationship.left if endpoint == "left" else relationship.right
    message = (
        f"relationship {relationship.name!r} has ambiguous {endpoint} companions: {len(records)} "
        f"{endpoint_config.record_type!r} records match {len(counterpart)} counterpart record(s) for unit "
        f"{unit_id!r} key {_relationship_key_text(relationship, key)}"
    )
    return _relationship_issue(
        code="manifest.relationship.ambiguous_companion",
        relationship=relationship,
        endpoint=endpoint,
        unit_id=unit_id,
        key=key,
        paths=tuple(record.path for record in records),
        message=message,
    )


def _missing_relationship_issue(
    relationship: ManifestRelationshipConfig,
    endpoint: Literal["left", "right"],
    unit_id: str,
    key: tuple[object, ...],
    counterpart: tuple[ManifestRelatedRecord, ...],
) -> ManifestRelationshipIssue:
    endpoint_config = relationship.left if endpoint == "left" else relationship.right
    message = (
        f"relationship {relationship.name!r} is missing required {endpoint} companion "
        f"{endpoint_config.record_type!r} for {len(counterpart)} record(s) in unit {unit_id!r} "
        f"with key {_relationship_key_text(relationship, key)}"
    )
    return _relationship_issue(
        code="manifest.relationship.missing_companion",
        relationship=relationship,
        endpoint=endpoint,
        unit_id=unit_id,
        key=key,
        paths=tuple(record.path for record in counterpart),
        message=message,
    )


def _bundle_relationship_issues(
    relationship: ManifestRelationshipConfig,
    unit_id: str,
    key: tuple[object, ...],
    left: tuple[ManifestRelatedRecord, ...],
    right: tuple[ManifestRelatedRecord, ...],
) -> tuple[ManifestRelationshipIssue, ...]:
    issues: list[ManifestRelationshipIssue] = []
    left_unique = relationship.cardinality in {"one_to_one", "one_to_many"}
    right_unique = relationship.cardinality in {"one_to_one", "many_to_one"}
    if left_unique and len(left) > 1:
        issues.append(_duplicate_relationship_issue(relationship, "left", unit_id, key, left))
        if right:
            issues.append(_ambiguous_relationship_issue(relationship, "left", unit_id, key, left, right))
    if right_unique and len(right) > 1:
        issues.append(_duplicate_relationship_issue(relationship, "right", unit_id, key, right))
        if left:
            issues.append(_ambiguous_relationship_issue(relationship, "right", unit_id, key, right, left))
    if not left and relationship.left.required:
        issues.append(_missing_relationship_issue(relationship, "left", unit_id, key, right))
    if not right and relationship.right.required:
        issues.append(_missing_relationship_issue(relationship, "right", unit_id, key, left))
    return tuple(issues)


def _relationship_sort_key(identity: tuple[str, tuple[object, ...]]) -> tuple[str, tuple[str, ...]]:
    unit_id, values = identity
    return unit_id, tuple(repr(value) for value in values)


def _related_records(
    records: pd.DataFrame,
    relationship: ManifestRelationshipConfig,
) -> ManifestRelationshipResult:
    _validate_relationship_frame(records, relationship)
    left_rows, invalid_left = _endpoint_rows(records, relationship, relationship.left, "left")
    right_rows, invalid_right = _endpoint_rows(records, relationship, relationship.right, "right")
    left_groups = _group_relationship_rows(left_rows)
    right_groups = _group_relationship_rows(right_rows)
    identities = sorted(set(left_groups) | set(right_groups), key=_relationship_sort_key)
    bundles: list[ManifestRelationshipBundle] = []
    issues = invalid_left + invalid_right
    for identity in identities:
        unit_id = identity[0]
        left_group = left_groups.get(identity)
        right_group = right_groups.get(identity)
        key = left_group[0] if left_group is not None else right_group[0]  # type: ignore[index]
        left = tuple(left_group[1]) if left_group is not None else ()
        right = tuple(right_group[1]) if right_group is not None else ()
        bundle_issues = _bundle_relationship_issues(relationship, unit_id, key, left, right)
        issues.extend(bundle_issues)
        bundles.append(ManifestRelationshipBundle(relationship.name, unit_id, key, left, right, bundle_issues))
    ordered_issues = tuple(
        sorted(issues, key=lambda issue: (issue.unit_id, repr(issue.key), issue.code, issue.message))
    )
    return ManifestRelationshipResult(relationship.name, tuple(bundles), ordered_issues)


def _resolve_manifest_enricher(enricher: ManifestEnricher | str) -> tuple[str, ManifestEnricher]:
    if isinstance(enricher, str):
        name = _validated_enricher_name(enricher)
        try:
            return name, _MANIFEST_ENRICHERS[name]
        except KeyError:
            known = ", ".join(sorted(_MANIFEST_ENRICHERS)) or "none"
            raise ManifestError(f"unknown manifest enricher {name!r}; registered enrichers: {known}") from None
    if not callable(enricher):
        raise ManifestError("manifest enricher must be a callable or registered name")
    candidate_name = getattr(enricher, "__name__", None)
    name = candidate_name if isinstance(candidate_name, str) and candidate_name else type(enricher).__name__
    return name, enricher


def _validate_enrichment_frame(records: pd.DataFrame) -> None:
    _join_frame_columns(records, "manifest records")
    missing = sorted(set(_MANIFEST_BASE_FIELDS) - set(records.columns))
    if missing:
        raise ManifestError(f"manifest records are missing required provenance columns: {', '.join(missing)}")
    reserved = sorted(set(_MANIFEST_ENRICHMENT_FIELDS).intersection(records.columns))
    if reserved:
        raise ManifestError(f"manifest records already contain reserved enrichment columns: {', '.join(reserved)}")
    record_types: pd.Series[Any] = records["record_type"]
    if bool(record_types.isna().any()):
        raise ManifestError("manifest records 'record_type' column contains null values")
    if any(not isinstance(value, str) for value in record_types.tolist()):
        raise ManifestError("manifest records 'record_type' column must contain only strings")


def _required_enrichment_string(row: Mapping[str, object], field: str) -> str:
    value = row[field]
    if not isinstance(value, str):
        raise TypeError(f"provenance field {field!r} must be a string")
    return value


def _optional_enrichment_string(row: Mapping[str, object], field: str) -> str | None:
    value = row[field]
    if value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value)):
        return None
    if not isinstance(value, str):
        raise TypeError(f"provenance field {field!r} must be a string or null")
    return value


def _manifest_enrichment_input(row: Mapping[str, object]) -> ManifestEnrichmentInput:
    metadata = {field: value for field, value in row.items() if field not in _MANIFEST_BASE_FIELDS}
    return ManifestEnrichmentInput(
        schema_name=_required_enrichment_string(row, "schema_name"),
        schema_version=_required_enrichment_string(row, "schema_version"),
        record_type=_required_enrichment_string(row, "record_type"),
        unit_id=_required_enrichment_string(row, "unit_id"),
        path=Path(_required_enrichment_string(row, "path")),
        relative_path=_required_enrichment_string(row, "relative_path"),
        filename=_required_enrichment_string(row, "filename"),
        extension=_required_enrichment_string(row, "extension"),
        parse_status=_required_enrichment_string(row, "parse_status"),
        parse_error=_optional_enrichment_string(row, "parse_error"),
        metadata=MappingProxyType(metadata),
    )


def _normalized_enrichment_output(
    output: Mapping[str, object],
    occupied_fields: frozenset[str],
) -> dict[str, object]:
    if not isinstance(output, Mapping):
        raise TypeError(f"enricher output must be a mapping, got {type(output).__name__}")
    normalized: dict[str, object] = {}
    for field, value in output.items():
        if not isinstance(field, str) or not field.strip() or field != field.strip():
            raise ValueError("enricher output field names must be non-empty strings without surrounding whitespace")
        if field in occupied_fields:
            raise ValueError(f"enricher output field {field!r} would overwrite a manifest record column")
        normalized[field] = value
    return normalized


def _enrichment_error(exc: Exception) -> str:
    detail = str(exc)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _run_manifest_enricher(
    row: Mapping[str, object],
    enricher: ManifestEnricher,
    name: str,
    occupied_fields: frozenset[str],
    strict: bool,
) -> tuple[dict[str, object], ManifestEnrichmentStatus, str | None]:
    try:
        output = enricher(_manifest_enrichment_input(row))
        return _normalized_enrichment_output(output, occupied_fields), "ok", None
    except Exception as exc:
        error = _enrichment_error(exc)
        if strict:
            raise ManifestError(f"manifest enricher {name!r} failed for {row.get('path')!r}: {error}") from None
        return {}, "error", error


def _enrich_manifest_records(
    records: pd.DataFrame,
    enricher: ManifestEnricher,
    name: str,
    selected_types: frozenset[str],
    strict: bool,
) -> pd.DataFrame:
    result = records.copy().reset_index(drop=True)
    occupied_fields = frozenset(str(field) for field in records.columns) | frozenset(_MANIFEST_ENRICHMENT_FIELDS)
    rows = [dict(zip((str(field) for field in records.columns), values)) for values in records.itertuples(index=False)]
    outcomes: list[tuple[dict[str, object], ManifestEnrichmentStatus, str | None]] = []
    for row in rows:
        if row["record_type"] not in selected_types:
            outcomes.append(({}, "not_selected", None))
            continue
        outcomes.append(_run_manifest_enricher(row, enricher, name, occupied_fields, strict))

    payload_fields = sorted({field for output, _, _ in outcomes for field in output})
    for field in payload_fields:
        result[field] = pd.Series([output.get(field, pd.NA) for output, _, _ in outcomes], dtype=object)
    result["enrichment_name"] = name
    result["enrichment_status"] = [status for _, status, _ in outcomes]
    result["enrichment_error"] = pd.Series([error for _, _, error in outcomes], dtype=object)
    return result


def _table_format(path: Path, table: ManifestTableConfig) -> str:
    if table.format is not None:
        return table.format
    suffix = path.suffix.lower()
    formats = {".csv": "csv", ".tsv": "tsv", ".xlsx": "xlsx"}
    try:
        return formats[suffix]
    except KeyError:
        raise ManifestError(f"cannot infer format for manifest table {path}") from None


def _read_table(path: Path, table: ManifestTableConfig) -> pd.DataFrame:
    table_format = _table_format(path, table)
    if table_format == "csv":
        frame = pd.read_csv(path, header=table.header, dtype=object)
    elif table_format == "tsv":
        frame = pd.read_csv(path, sep="\t", header=table.header, dtype=object)
    else:
        frame = pd.read_excel(
            path,
            sheet_name=table.sheet if table.sheet is not None else 0,
            header=table.header,
            dtype=object,
        )
    frame = frame.rename(columns=table.rename)
    for field_name, cast in table.casts.items():
        if field_name not in frame:
            raise ManifestError(f"table {table.name!r} is missing cast column {field_name!r}")
        caster = partial(_cast_scalar, cast=cast, date_format=table.date_formats.get(field_name))
        frame[field_name] = frame[field_name].map(caster)
    return frame


def _enrich(frame: pd.DataFrame, context_root: Path, table: ManifestTableConfig) -> pd.DataFrame:
    matches = sorted(
        (path for path in context_root.glob(table.glob) if path.is_file() and _is_within(path, context_root)),
        key=lambda item: item.as_posix(),
    )
    if not matches:
        if table.optional:
            return frame
        raise ManifestError(f"required manifest table {table.name!r} was not found under {context_root}")
    table_frame = pd.concat([_read_table(path, table) for path in matches], ignore_index=True)
    missing_left = set(table.join.left) - set(frame.columns)
    missing_right = set(table.join.right) - set(table_frame.columns)
    if missing_left or missing_right:
        details = []
        if missing_left:
            details.append(f"record keys {sorted(missing_left)}")
        if missing_right:
            details.append(f"table keys {sorted(missing_right)}")
        raise ManifestError(f"table {table.name!r} is missing " + " and ".join(details))

    shared_nonkeys = (set(frame.columns) & set(table_frame.columns)) - set(table.join.left) - set(table.join.right)
    if shared_nonkeys:
        raise ManifestError(f"table {table.name!r} would overwrite columns: {', '.join(sorted(shared_nonkeys))}")

    if table.join.cardinality in {"one_to_one", "many_to_one"} and table_frame.duplicated(table.join.right).any():
        raise ManifestError(f"table {table.name!r} violates {table.join.cardinality} cardinality")
    if table.join.cardinality == "one_to_one" and frame.duplicated(table.join.left).any():
        raise ManifestError(f"records violate one_to_one cardinality for table {table.name!r}")

    if table.join.left == table.join.right:
        return frame.merge(table_frame, how="left", on=table.join.left, sort=False)
    return frame.merge(
        table_frame,
        how="left",
        left_on=table.join.left,
        right_on=table.join.right,
        sort=False,
    )


class AtlasManifest:
    """Extract schema-declared path metadata and return pandas manifests."""

    def __init__(self, schema: Schema | str, project_root: str | Path | None = None) -> None:
        if isinstance(schema, Schema):
            self.schema = schema
        else:
            try:
                self.schema = resolve_schema(schema, project_root or Path.cwd())
            except SchemaError as exc:
                raise ManifestError(str(exc)) from None

    @property
    def capabilities(self) -> ManifestCapabilities:
        """Return deterministic, immutable schema discovery metadata."""
        return _manifest_capabilities(self.schema)

    @property
    def record_types(self) -> tuple[str, ...]:
        """Return supported manifest record types in deterministic order."""
        return self.capabilities.record_types

    @property
    def record_groups(self) -> tuple[str, ...]:
        """Return supported manifest record groups in deterministic order."""
        return self.capabilities.record_groups

    @property
    def tags(self) -> tuple[ManifestTagCapability, ...]:
        """Return supported manifest tag name/value pairs in deterministic order."""
        return self.capabilities.tags

    @property
    def output_fields(self) -> tuple[str, ...]:
        """Return provenance and schema-declared DataFrame fields."""
        return self.capabilities.output_fields

    @property
    def relationships(self) -> tuple[ManifestRelationshipCapability, ...]:
        """Return schema-declared record relationships in deterministic order."""
        return self.capabilities.relationships

    def join_external_assets(
        self,
        records: pd.DataFrame,
        external: pd.DataFrame,
        *,
        record_type: str,
        external_key: str,
        cardinality: ManifestJoinCardinality = "one_to_one",
    ) -> pd.DataFrame:
        """Attach external asset fields through a record type's schema-declared key."""
        asset_key = _asset_key_config(self.schema, record_type)
        return _join_external_asset_frames(
            records,
            external,
            record_type=record_type,
            record_key=asset_key.field,
            external_key=external_key,
            cardinality=cardinality,
        )

    def attach_dataframe(
        self,
        records: pd.DataFrame,
        attachment: ManifestTableLike,
        *,
        left_on: str | Sequence[str],
        right_on: str | Sequence[str],
        cardinality: ManifestJoinCardinality = "many_to_one",
        unmatched: ManifestAttachmentUnmatched = "error",
        attachment_suffix: str | None = None,
    ) -> pd.DataFrame:
        """Attach table-like fields to manifest rows through explicit keys."""
        return attach_dataframe(
            records,
            attachment,
            left_on=left_on,
            right_on=right_on,
            cardinality=cardinality,
            unmatched=unmatched,
            attachment_suffix=attachment_suffix,
        )

    def enrich_records(
        self,
        records: pd.DataFrame,
        enricher: ManifestEnricher | str,
        *,
        record_types: Collection[str] | None = None,
        record_groups: Collection[str] | None = None,
        where: Mapping[str, ManifestQueryValue] | None = None,
        strict: bool = False,
    ) -> pd.DataFrame:
        """Run one callable for selected manifest rows and append its fields and outcome."""
        _validate_enrichment_frame(records)
        name, resolved_enricher = _resolve_manifest_enricher(enricher)
        selected_types = _selected_record_types(self.schema, record_types, record_groups, where)
        if selected_types is None:
            selected_types = frozenset(self.record_types)
        return _enrich_manifest_records(records, resolved_enricher, name, selected_types, strict)

    def related_records(
        self,
        records: pd.DataFrame,
        relationship: str,
        *,
        strict: bool = False,
    ) -> ManifestRelationshipResult:
        """Bundle related manifest records and report companion validation issues."""
        config = _relationship_config(self.schema, relationship)
        result = _related_records(records, config)
        if strict and result.issues:
            issue = result.issues[0]
            raise ManifestError(f"{issue.code}: {issue.message}")
        return result

    def record(self, file_path: str | Path, *, strict: bool = False) -> ManifestRecord:
        """Extract one path record; tabular enrichment is applied by :meth:`dataframe`."""
        path = Path(file_path)
        if not path.is_file():
            raise ManifestError(f"manifest record input is not a file: {path}")
        unit = _unit_for_file(path, self.schema)
        if unit is None:
            raise ManifestError(f"could not locate a '{self.schema.name}' data unit containing {path}")
        record = _record_for(path.resolve(), unit, self.schema, _rule_for_file(path, unit, self.schema))
        if strict and record.parse_status != "ok":
            raise ManifestError(record.parse_error or f"could not parse {path}")
        return record

    def dataframe(
        self,
        path: str | Path,
        *,
        context_root: str | Path | None = None,
        record_types: Collection[str] | None = None,
        record_groups: Collection[str] | None = None,
        where: Mapping[str, ManifestQueryValue] | None = None,
        strict: bool = False,
    ) -> pd.DataFrame:
        """Return records for one file or all matching units beneath a directory.

        Multiple groups are combined as a union. When both selectors are supplied,
        ``record_types`` intersects that union. Exact-match ``where`` predicates
        select schema-declared rule attributes and intersect either selector. Atlas
        resolves all selectors before discovery and strict validation.
        """
        selected_types = _selected_record_types(self.schema, record_types, record_groups, where)

        source = Path(path)
        if source.is_file():
            unit = _unit_for_file(source, self.schema)
            if unit is None:
                raise ManifestError(f"could not locate a '{self.schema.name}' data unit containing {source}")
            rule = _rule_for_file(source, unit, self.schema, selected_types)
            records = (
                []
                if selected_types is not None and rule is None
                else [_record_for(source.resolve(), unit, self.schema, rule)]
            )
            default_context = unit
        elif source.is_dir():
            units = _units_for_directory(source, self.schema)
            records = [record for unit in units for record in _records_for_unit(unit, self.schema, selected_types)]
            default_context = source.resolve()
        else:
            raise ManifestError(f"manifest input does not exist: {source}")

        if strict:
            failed = [record for record in records if record.parse_status != "ok"]
            if failed:
                raise ManifestError(failed[0].parse_error or f"could not parse {failed[0].path}")

        columns = list(_MANIFEST_BASE_FIELDS)
        frame = pd.DataFrame.from_records([record.as_flat_dict() for record in records])
        if frame.empty:
            frame = pd.DataFrame(columns=columns)
        context = Path(context_root).resolve() if context_root is not None else default_context
        try:
            for table in self.schema.manifest.tables:
                frame = _enrich(frame, context, table)
        except (ManifestError, OSError, ValueError) as exc:
            if strict:
                raise ManifestError(str(exc)) from None
            frame["parse_status"] = frame["parse_status"].where(frame["parse_status"] != "ok", "partial")
            frame["parse_error"] = frame["parse_error"].fillna(str(exc))

        leading = [name for name in columns if name in frame]
        trailing = sorted(name for name in frame.columns if name not in leading)
        return type_cast(pd.DataFrame, frame.loc[:, leading + trailing].convert_dtypes().reset_index(drop=True))


def load_dataframe(
    path: str | Path,
    schema: Schema | str,
    *,
    project_root: str | Path | None = None,
    context_root: str | Path | None = None,
    record_types: Collection[str] | None = None,
    record_groups: Collection[str] | None = None,
    where: Mapping[str, ManifestQueryValue] | None = None,
    strict: bool = False,
) -> pd.DataFrame:
    """Load a manifest DataFrame through :class:`AtlasManifest`."""
    return AtlasManifest(schema, project_root=project_root).dataframe(
        path,
        context_root=context_root,
        record_types=record_types,
        record_groups=record_groups,
        where=where,
        strict=strict,
    )
