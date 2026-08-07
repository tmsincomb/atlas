"""Schema models and loaders for atlas data type definitions."""

# ruff: noqa: UP045

from __future__ import annotations

import functools
import json
import math
import re
import string
from difflib import get_close_matches
from importlib.resources import files
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Optional, Union

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from atlas.log import get_logger

logger = get_logger(__name__)


class SchemaError(Exception):
    """Raised for schema loading or validation errors."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[object, object]:
        if not isinstance(node, MappingNode):  # pragma: no cover - PyYAML calls this for mappings only
            raise ConstructorError(None, None, f"expected a mapping node, got {node.id}", node.start_mark)

        self.flatten_mapping(node)
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from None
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class _SchemaModel(BaseModel):
    """Strict base for user-authored schema configuration."""

    model_config = ConfigDict(extra="forbid")


def _short_repr(value: object, limit: int = 160) -> str:
    """Render an input value on one bounded line for a CLI diagnostic."""
    rendered = repr(value).replace("\n", "\\n")
    return rendered if len(rendered) <= limit else f"{rendered[: limit - 3]}..."


def _resolve_json_schema_node(root: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Resolve local refs and select the useful non-null branch of ``anyOf``."""
    while "$ref" in node:
        target: Any = root
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    options = node.get("anyOf")
    if isinstance(options, list):
        useful = [option for option in options if option.get("type") != "null"]
        if useful:
            return _resolve_json_schema_node(root, useful[0])
    return node


def _json_schema_at(root: dict[str, Any], location: tuple[object, ...]) -> tuple[dict[str, Any], Optional[str]]:
    """Find the JSON Schema node for an error location and any unknown key."""
    node = root
    for part in location:
        node = _resolve_json_schema_node(root, node)
        if isinstance(part, int):
            item = node.get("items")
            if not isinstance(item, dict):
                return node, None
            node = item
            continue
        properties = node.get("properties")
        if not isinstance(properties, dict) or part not in properties:
            return node, str(part)
        child = properties[part]
        if not isinstance(child, dict):
            return node, None
        node = child
    return _resolve_json_schema_node(root, node), None


def _schema_type(node: dict[str, Any], root: dict[str, Any]) -> str:
    """Describe the useful type and declarative bounds of a JSON Schema node."""
    node = _resolve_json_schema_node(root, node)
    enum = node.get("enum")
    if isinstance(enum, list):
        return "one of: " + ", ".join(_short_repr(value) for value in enum)
    type_name = node.get("type", "valid value")
    if type_name == "array" and isinstance(node.get("items"), dict):
        type_name = f"array of {_schema_type(node['items'], root)} values"
    minimum = node.get("minimum")
    maximum = node.get("maximum")
    if minimum is not None:
        type_name = f"{type_name} greater than or equal to {minimum}"
    if maximum is not None:
        type_name = f"{type_name} less than or equal to {maximum}"
    return str(type_name)


def _json_example(value: object) -> str:
    """Render a generated value in compact YAML-compatible JSON syntax."""
    return json.dumps(value, ensure_ascii=True)


def _node_examples(node: dict[str, Any], root: dict[str, Any], limit: int = 3) -> list[str]:
    """Generate small values from enum, type, and bound information."""
    node = _resolve_json_schema_node(root, node)
    enum = node.get("enum")
    if isinstance(enum, list):
        return [_json_example(value) for value in enum[:limit]]
    type_name = node.get("type")
    values: list[object]
    if type_name == "integer":
        integer_start = math.ceil(float(node.get("minimum", 0)))
        values = [integer_start, integer_start + 1]
    elif type_name == "number":
        number_start = float(node.get("minimum", 0.0))
        values = [number_start, number_start + 1.0]
    elif type_name == "boolean":
        values = [True, False]
    elif type_name == "array" and isinstance(node.get("items"), dict):
        item_examples = _node_examples(node["items"], root, 1)
        values = [[json.loads(item_examples[0])]] if item_examples else [[]]
    elif type_name == "object":
        values = [{}]
    else:
        values = ["example"]
    return [_json_example(value) for value in values[:limit]]


def _custom_error_expectation(message: str, location: tuple[object, ...]) -> tuple[Optional[str], list[str]]:
    """Explain constraints implemented by Atlas validators, not JSON Schema."""
    lowered = message.lower()
    if "filename_pattern regex" in lowered:
        return "valid Python regular expression", [_json_example(r"^.*\.csv$"), _json_example(r"^(file|dir|any)$")]
    if "glob" in lowered:
        return (
            "relative glob with '**' only as a complete path component",
            [_json_example("**/*.csv"), _json_example("outputs/**/*.json")],
        )
    if any(term in lowered for term in ("relative to", "parent traversal", "nul bytes", "must not be empty")):
        leaf = str(location[-1]) if location else ""
        if leaf == "name" or "schema name" in lowered:
            return "non-empty schema name without path separators", [_json_example("sample-run")]
        return (
            "safe relative path within the data unit",
            [_json_example("data/file.txt"), _json_example("outputs/report.pdf")],
        )
    if "path separators" in lowered:
        return "schema name without path separators", [_json_example("sample-run")]
    if "min_file_count must be less" in lowered:
        return (
            "min_file_count less than or equal to max_file_count",
            [_json_example({"min_file_count": 1, "max_file_count": 10})],
        )
    if "min_size_mb must be less" in lowered:
        return (
            "min_size_mb less than or equal to max_size_gb after conversion to MiB",
            [_json_example({"min_size_mb": 1.0, "max_size_gb": 1.0})],
        )
    return None, []


def _validation_error_detail(exc: ValidationError) -> str:
    """Append received, expected, and generated examples to Pydantic text."""
    root = Schema.model_json_schema()
    lines = ["Validation details:"]
    for error in exc.errors(include_url=False):
        location = tuple(error["loc"])
        path = ".".join(str(part) for part in location) or "schema"
        node, unknown = _json_schema_at(root, location)
        message = str(error["msg"])
        expected, examples = _custom_error_expectation(message, location)

        if error["type"] == "extra_forbidden" and unknown is not None:
            properties = node.get("properties", {})
            names = sorted(properties) if isinstance(properties, dict) else []
            expected = "recognized field; one of: " + ", ".join(names)
            suggestions = get_close_matches(unknown, names, n=3, cutoff=0.55)
            examples = []
            for suggestion in suggestions:
                child = properties[suggestion]
                if isinstance(child, dict):
                    child_examples = _node_examples(child, root, 1)
                    if child_examples:
                        examples.append(f"{suggestion}: {child_examples[0]}")
        if expected is None:
            expected = _schema_type(node, root)
        if not examples:
            examples = _node_examples(node, root)

        lines += [
            f"  {path}:",
            f"    received: {_short_repr(error.get('input'))}",
            f"    expected: {expected}",
            "    generated examples:",
        ]
        lines += [f"      - {example}" for example in examples]
    return "\n".join(lines)


def _yaml_error_detail(text: str, exc: yaml.YAMLError) -> str:
    """Give malformed YAML the same structured diagnostic shape."""
    source_lines = text.splitlines()
    mark = getattr(exc, "problem_mark", None)
    line_number = mark.line if mark is not None else len(source_lines) - 1
    while line_number >= 0 and (line_number >= len(source_lines) or not source_lines[line_number].strip()):
        line_number -= 1
    received = source_lines[line_number] if line_number >= 0 else text
    return "\n".join(
        [
            "Validation details:",
            f"  received: {_short_repr(received)}",
            "  expected: syntactically valid YAML mapping",
            "  generated examples:",
            '    - "name: sample-run"',
            '    - "validate: {min_file_count: 1}"',
        ]
    )


def _check_relative_path(value: str, label: str) -> str:
    """Reject paths that are empty, invalid, absolute, or escape their unit."""
    if not value.strip():
        raise ValueError(f"{label} must not be empty")
    if "\0" in value:
        raise ValueError(f"{label} must not contain NUL bytes")

    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.anchor:
        raise ValueError(f"{label} must be relative to the data unit")
    if ".." in posix.parts or ".." in windows.parts:
        raise ValueError(f"{label} must not contain parent traversal ('..')")
    return value


def _check_glob(value: str, label: str) -> str:
    """Validate a safe pathlib glob without touching the filesystem."""
    _check_relative_path(value, label)
    components = re.split(r"[/\\]+", value)
    if any("**" in component and component != "**" for component in components):
        raise ValueError(f"invalid {label} glob {value!r}: '**' must be an entire path component")
    return value


def _relative_path(value: str) -> str:
    return _check_relative_path(value, "path")


def _relative_glob(value: str) -> str:
    return _check_glob(value, "glob")


_RelativePath = Annotated[str, AfterValidator(_relative_path)]
_RelativeGlob = Annotated[str, AfterValidator(_relative_glob)]


class CmdlineSubcommandGuard(_SchemaModel):
    """Exclude a unit when a recorded command line names a given subcommand.

    Reads *file* under the candidate unit directory, tokenises it, finds the
    token whose basename equals *tool*, and compares the following token to
    *subcommand*.  Used to tell a single-app ``bundler build`` output apart from
    a ``bundler workspace`` (monorepo) output that shares the same on-disk shape.
    """

    file: _RelativePath
    tool: str = Field(min_length=1)
    subcommand: str = Field(min_length=1)


class DetectionConfig(_SchemaModel):
    """Declarative rules for auto-detecting a data type on disk.

    The generic engine in :mod:`atlas.detect` reads these fields — no
    per-schema Python is needed.  Detection seeds on ``landmark`` (an
    ``rglob`` pattern), walks up ``unit_depth`` parents to the unit
    directory, then confirms it with ``markers`` and the optional guards.
    A schema with ``landmark = None`` is validation-only and is skipped by
    ``detect``.
    """

    markers: list[_RelativePath] = Field(default_factory=list)
    """Paths (relative to the unit dir) that must ALL exist for a match."""
    landmark: Optional[_RelativeGlob] = None
    """``rglob`` seed pattern; ``None`` means the schema is validation-only."""
    landmark_type: Literal["file", "dir", "any"] = "any"
    """Filesystem type the landmark hit must be."""
    landmark_parent: Optional[str] = None
    """When set, the landmark's immediate parent directory must have this name."""
    unit_depth: int = Field(default=1, ge=0)
    """Number of ``.parent`` hops from the landmark to the unit directory."""
    require_any_glob: list[_RelativeGlob] = Field(default_factory=list)
    """When non-empty, at least one glob (relative to the unit dir) must match a file."""
    exclude_if_markers: list[_RelativePath] = Field(default_factory=list)
    """Skip the unit when ANY of these paths exist under it."""
    exclude_if_cmdline_subcommand: Optional[CmdlineSubcommandGuard] = None
    """Skip the unit when a recorded command line names this subcommand."""
    sync_by: str = "subdirectory"
    """How the consuming tool syncs units of this stage."""
    unit_is_directory_stage: bool = False
    """True when each unit is itself a complete directory dataset."""

    @field_validator("landmark_parent")
    @classmethod
    def _check_landmark_parent(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        _check_relative_path(value, "landmark parent")
        if len(PurePosixPath(value).parts) != 1 or len(PureWindowsPath(value).parts) != 1:
            raise ValueError("landmark parent must be one directory name")
        return value


class SyncConfig(_SchemaModel):
    """Include/exclude rules for file synchronisation."""

    include: list[_RelativeGlob] = Field(default_factory=list)
    exclude: list[_RelativeGlob] = Field(default_factory=list)


class ValidateConfig(_SchemaModel):
    """Validation rules applied before pushing a data unit."""

    required: list[_RelativePath] = Field(default_factory=list)
    required_any: list[_RelativeGlob] = Field(default_factory=list)
    required_dirs: list[_RelativePath] = Field(default_factory=list)
    warn_if_missing: list[_RelativePath] = Field(default_factory=list)
    fail_on: list[_RelativeGlob] = Field(default_factory=list)
    min_size_mb: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    max_size_gb: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    min_file_count: Optional[int] = Field(default=None, ge=0)
    max_file_count: Optional[int] = Field(default=None, ge=0)
    filename_pattern: Optional[str] = None

    @field_validator("filename_pattern")
    @classmethod
    def _check_pattern_compiles(cls, value: Optional[str]) -> Optional[str]:
        """Reject a ``filename_pattern`` that is not a valid regex at load time."""
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid filename_pattern regex {value!r}: {exc}") from None
        return value

    @model_validator(mode="after")
    def _check_bounds(self) -> ValidateConfig:
        if (
            self.min_file_count is not None
            and self.max_file_count is not None
            and self.min_file_count > self.max_file_count
        ):
            raise ValueError("min_file_count must be less than or equal to max_file_count")
        if self.min_size_mb is not None and self.max_size_gb is not None:
            max_size_mb = self.max_size_gb * 1024
            if self.min_size_mb > max_size_mb:
                raise ValueError("min_size_mb must be less than or equal to max_size_gb (converted to MiB)")
        return self


class ManifestExtractorConfig(_SchemaModel):
    """Extract named metadata fields from one component of a matched path."""

    source: Literal["unit_name", "relative_path", "filename"]
    regex: str

    @field_validator("regex")
    @classmethod
    def _check_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid manifest extractor regex {value!r}: {exc}") from None
        return value


_ManifestCast = Literal["string", "integer", "float", "boolean", "date"]
_ManifestConstant = Optional[Union[str, int, float, bool]]
_ManifestTag = Union[str, int, float, bool]
_MANIFEST_PROVENANCE_FIELDS = {
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
    "metadata",
}


class ManifestAssetKeyConfig(_SchemaModel):
    """Expose a stable file identifier under a schema-owned output field."""

    field: str = Field(min_length=1)
    source: Literal["filename", "relative_path"] = "filename"

    @field_validator("field")
    @classmethod
    def _check_field(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("manifest asset key field must not be empty")
        if value != value.strip():
            raise ValueError("manifest asset key field must not have surrounding whitespace")
        return value


def _manifest_constant_names(
    record_name: str,
    constants: dict[str, _ManifestConstant],
    captured: set[str],
    derived: set[str],
) -> set[str]:
    names = set(constants)
    if any(not name.strip() for name in names):
        raise ValueError("manifest constant field names must not be empty")
    reserved = names & _MANIFEST_PROVENANCE_FIELDS
    if reserved:
        raise ValueError(f"manifest constants use reserved provenance fields: {', '.join(sorted(reserved))}")
    collisions = names & (captured | derived)
    if collisions:
        raise ValueError(f"manifest record {record_name!r} defines duplicate fields: {', '.join(sorted(collisions))}")
    return names


def _check_manifest_asset_key(
    asset_key: Optional[ManifestAssetKeyConfig],
    available: set[str],
    tags: set[str],
) -> None:
    if asset_key is None:
        return
    if asset_key.field in _MANIFEST_PROVENANCE_FIELDS:
        raise ValueError(f"manifest asset key uses reserved provenance field: {asset_key.field}")
    if asset_key.field in available or asset_key.field in tags:
        raise ValueError(f"manifest asset key field {asset_key.field!r} collides with another record field")


class ManifestRecordConfig(_SchemaModel):
    """Select one file kind and extract a flat metadata record from its path."""

    name: str = Field(min_length=1)
    glob: _RelativeGlob
    groups: list[str] = Field(default_factory=list)
    tags: dict[str, _ManifestTag] = Field(default_factory=dict)
    asset_key: Optional[ManifestAssetKeyConfig] = None
    extractors: list[ManifestExtractorConfig] = Field(default_factory=list)
    constants: dict[str, _ManifestConstant] = Field(default_factory=dict)
    derive: dict[str, str] = Field(default_factory=dict)
    casts: dict[str, _ManifestCast] = Field(default_factory=dict)
    date_formats: dict[str, str] = Field(default_factory=dict)

    @field_validator("groups")
    @classmethod
    def _check_groups(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("manifest record group names must not be empty")
        if any(value != value.strip() for value in values):
            raise ValueError("manifest record group names must not have surrounding whitespace")
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ValueError(f"duplicate manifest record groups: {', '.join(duplicates)}")
        return values

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, values: dict[str, _ManifestTag]) -> dict[str, _ManifestTag]:
        if any(not name.strip() for name in values):
            raise ValueError("manifest record tag names must not be empty")
        if any(name != name.strip() for name in values):
            raise ValueError("manifest record tag names must not have surrounding whitespace")
        return values

    @model_validator(mode="after")
    def _check_fields(self) -> ManifestRecordConfig:
        captured: set[str] = set()
        for extractor in self.extractors:
            groups = set(re.compile(extractor.regex).groupindex)
            duplicate = captured & groups
            if duplicate:
                names = ", ".join(sorted(duplicate))
                raise ValueError(f"manifest record {self.name!r} captures duplicate fields: {names}")
            captured.update(groups)

        constant_names = _manifest_constant_names(self.name, self.constants, captured, set(self.derive))
        available = captured | constant_names | set(self.derive)
        for field_name, template in self.derive.items():
            if not field_name.strip():
                raise ValueError("manifest derived field names must not be empty")
            referenced = {name for _, name, _, _ in string.Formatter().parse(template) if name}
            missing = referenced - available
            if missing:
                names = ", ".join(sorted(missing))
                raise ValueError(f"manifest derived field {field_name!r} references unknown fields: {names}")
        unknown_casts = set(self.casts) - available
        unknown_dates = set(self.date_formats) - {name for name, cast in self.casts.items() if cast == "date"}
        _check_manifest_asset_key(self.asset_key, available, set(self.tags))
        if unknown_casts:
            raise ValueError(f"manifest casts reference unknown fields: {', '.join(sorted(unknown_casts))}")
        if unknown_dates:
            raise ValueError(f"manifest date_formats require date casts: {', '.join(sorted(unknown_dates))}")
        return self


class ManifestJoinConfig(_SchemaModel):
    """Relate extracted records to a tabular metadata source."""

    left: list[str] = Field(min_length=1)
    right: list[str] = Field(min_length=1)
    cardinality: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "many_to_one"

    @model_validator(mode="after")
    def _check_key_lengths(self) -> ManifestJoinConfig:
        if len(self.left) != len(self.right):
            raise ValueError("manifest table join left and right keys must have equal lengths")
        return self


class ManifestRelationshipEndpointConfig(_SchemaModel):
    """One typed endpoint in a declarative record relationship."""

    record_type: str = Field(min_length=1)
    fields: list[str] = Field(min_length=1)
    required: bool = True

    @field_validator("record_type")
    @classmethod
    def _check_record_type(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("manifest relationship record types must not have surrounding whitespace")
        return value

    @field_validator("fields")
    @classmethod
    def _check_fields(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("manifest relationship fields must not be empty")
        if any(value != value.strip() for value in values):
            raise ValueError("manifest relationship fields must not have surrounding whitespace")
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ValueError(f"duplicate manifest relationship fields: {', '.join(duplicates)}")
        return values


class ManifestRelationshipConfig(_SchemaModel):
    """Relate two manifest record types through corresponding extracted fields."""

    name: str = Field(min_length=1)
    left: ManifestRelationshipEndpointConfig
    right: ManifestRelationshipEndpointConfig
    cardinality: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "one_to_one"

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("manifest relationship names must not have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def _check_endpoints(self) -> ManifestRelationshipConfig:
        if self.left.record_type == self.right.record_type:
            raise ValueError("manifest relationship endpoints must use different record types")
        if len(self.left.fields) != len(self.right.fields):
            raise ValueError("manifest relationship left and right fields must have equal lengths")
        return self


class ManifestTableConfig(_SchemaModel):
    """Optional CSV, TSV, or XLSX metadata used to enrich file records."""

    name: str = Field(min_length=1)
    glob: _RelativeGlob
    format: Optional[Literal["csv", "tsv", "xlsx"]] = None
    sheet: Optional[Union[str, int]] = None  # noqa: UP007 - evaluated by Pydantic on Python 3.9
    header: int = Field(default=0, ge=0)
    optional: bool = True
    rename: dict[str, str] = Field(default_factory=dict)
    casts: dict[str, _ManifestCast] = Field(default_factory=dict)
    date_formats: dict[str, str] = Field(default_factory=dict)
    join: ManifestJoinConfig

    @model_validator(mode="after")
    def _check_date_casts(self) -> ManifestTableConfig:
        unknown_dates = set(self.date_formats) - {name for name, cast in self.casts.items() if cast == "date"}
        if unknown_dates:
            raise ValueError(f"manifest table date_formats require date casts: {', '.join(sorted(unknown_dates))}")
        return self


class ManifestConfig(_SchemaModel):
    """Declarative file-record, relationship, and table-enrichment rules."""

    records: list[ManifestRecordConfig] = Field(default_factory=list)
    relationships: list[ManifestRelationshipConfig] = Field(default_factory=list)
    tables: list[ManifestTableConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_names(self) -> ManifestConfig:
        for label, names in (
            ("record", [record.name for record in self.records]),
            ("relationship", [relationship.name for relationship in self.relationships]),
            ("table", [table.name for table in self.tables]),
        ):
            duplicates = sorted({name for name in names if names.count(name) > 1})
            if duplicates:
                raise ValueError(f"duplicate manifest {label} names: {', '.join(duplicates)}")
        records_by_name = {record.name: record for record in self.records}
        for relationship in self.relationships:
            for label, endpoint in (("left", relationship.left), ("right", relationship.right)):
                record = records_by_name.get(endpoint.record_type)
                if record is None:
                    raise ValueError(
                        f"manifest relationship {relationship.name!r} {label} endpoint references unknown "
                        f"record type {endpoint.record_type!r}"
                    )
                captured = {name for extractor in record.extractors for name in re.compile(extractor.regex).groupindex}
                available = (
                    (_MANIFEST_PROVENANCE_FIELDS - {"metadata"}) | captured | set(record.constants) | set(record.derive)
                )
                if record.asset_key is not None:
                    available.add(record.asset_key.field)
                missing = sorted(set(endpoint.fields) - available)
                if missing:
                    raise ValueError(
                        f"manifest relationship {relationship.name!r} {label} endpoint references fields not "
                        f"emitted by record type {endpoint.record_type!r}: {', '.join(missing)}"
                    )
        return self


class Schema(_SchemaModel):
    """An atlas data-type schema loaded from YAML."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    version: str = "1.0"
    description: str = ""
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    validation: Optional[ValidateConfig] = Field(default=None, alias="validate")
    key_outputs: dict[str, _RelativePath] = Field(default_factory=dict)
    manifest: ManifestConfig = Field(default_factory=ManifestConfig)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        _check_relative_path(value, "schema name")
        if len(PurePosixPath(value).parts) != 1 or len(PureWindowsPath(value).parts) != 1:
            raise ValueError("schema name must not contain path separators")
        return value

    @field_validator("key_outputs")
    @classmethod
    def _check_key_outputs(cls, values: dict[str, str]) -> dict[str, str]:
        for name, path in values.items():
            if not name.strip():
                raise ValueError("key output names must not be empty")
            glob_pattern = re.sub(r"\{[^{}]+\}", "*", path)
            if "{" in glob_pattern or "}" in glob_pattern:
                raise ValueError(f"key output {name!r} contains a malformed placeholder")
            _check_glob(glob_pattern, f"key output {name!r}")
        return values


def _parse_schema(text: str, source: str) -> Schema:
    """Parse YAML *text* into a :class:`Schema`, tagging errors with *source*.

    Raises :class:`SchemaError` for empty content, non-mapping content,
    invalid YAML, or model validation failures.  This is the single place
    every loader funnels through so error reporting stays consistent.
    """
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise SchemaError(f"Invalid YAML in {source}: {exc}\n{_yaml_error_detail(text, exc)}") from None

    if data is None:
        raise SchemaError(f"Schema is empty: {source}")

    if not isinstance(data, dict):
        raise SchemaError(f"Expected a mapping in {source}, got {type(data).__name__}")

    try:
        return Schema(**data)
    except ValidationError as exc:
        raise SchemaError(f"Invalid schema in {source}: {exc}\n{_validation_error_detail(exc)}") from None


def load_schema(name_or_path: str | Path) -> Schema:
    """Load a schema from a YAML file path.

    Raises SchemaError with a clear message for missing files,
    invalid YAML, or validation failures.
    """
    path = Path(name_or_path)
    if not path.exists():
        raise SchemaError(f"Schema file not found: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SchemaError(f"Cannot read schema file {path}: {exc}") from None

    return _parse_schema(raw, str(path))


@functools.lru_cache(maxsize=16)
def _load_builtin_schema(name: str) -> Schema | None:
    """Load a built-in schema by name from the package data.

    Returns ``None`` when the schema is absent.  A schema that is present
    but fails to parse is logged as a warning (rather than vanishing
    silently) and reported as ``None``.
    """
    try:
        content = files("atlas.schemas").joinpath(f"{name}.yaml").read_text(encoding="utf-8")
    except (FileNotFoundError, TypeError, OSError):
        return None

    try:
        return _parse_schema(content, f"built-in schema '{name}'")
    except SchemaError as exc:
        logger.warning("skipping malformed built-in schema '%s': %s", name, exc)
        return None


def load_all_schemas() -> list[Schema]:
    """Load all built-in schema YAML files from the package data.

    Returns the successfully loaded schemas.  A file that fails to parse is
    logged as a warning and skipped, so a single bad schema never hides the
    rest — but it also never disappears without a diagnostic.
    """
    schemas: list[Schema] = []
    try:
        schema_dir = files("atlas.schemas")
        entries = sorted(schema_dir.iterdir(), key=lambda item: getattr(item, "name", ""))
    except OSError as exc:
        logger.warning("cannot list built-in schemas: %s", exc)
        return schemas

    for item in entries:
        name = getattr(item, "name", "")
        if not name.endswith(".yaml"):
            continue
        try:
            schemas.append(_parse_schema(item.read_text(encoding="utf-8"), f"built-in schema '{name}'"))
        except (SchemaError, OSError) as exc:
            logger.warning("skipping built-in schema '%s': %s", name, exc)
    return schemas


def _resolve_named_schema_file(directory: Path, name: str) -> Path | None:
    """Resolve one extensionless schema name, rejecting unsafe or ambiguous names."""
    try:
        _check_relative_path(name, "schema name")
    except ValueError as exc:
        raise SchemaError(f"Invalid schema name {name!r}: {exc}") from None
    if len(PurePosixPath(name).parts) != 1 or len(PureWindowsPath(name).parts) != 1:
        raise SchemaError(f"Invalid schema name {name!r}: names must not contain path separators")

    candidates = [directory / f"{name}{suffix}" for suffix in (".yaml", ".yml")]
    found = [path for path in candidates if path.exists()]
    if len(found) > 1:
        raise SchemaError(f"Ambiguous schema '{name}': found both {found[0]} and {found[1]}")
    return found[0] if found else None


def _load_installed_schema(path: Path) -> Schema:
    """Load a discovered schema whose declared name must match its filename."""
    schema = load_schema(path)
    if schema.name != path.stem:
        raise SchemaError(
            f"Schema name {schema.name!r} does not match installed filename {path.name!r}; "
            f"rename the file to {schema.name}.yaml or change its name field"
        )
    return schema


def resolve_schema(name: str, project_root: str | Path) -> Schema:
    """Resolve a schema by name or file path.

    Search order:
    1. If *name* looks like a file path (absolute, or ends with .yaml/.yml),
       load it directly.
    2. Project-local: ``{project_root}/schemas/{name}.yaml`` or
       ``{project_root}/schemas/{name}.yml``
    3. User-wide: ``~/.atlas/schemas/{name}.yaml`` or
       ``~/.atlas/schemas/{name}.yml``
    4. Built-in: package ``atlas.schemas`` data.

    Raises SchemaError when the schema cannot be found anywhere.
    """
    project_root = Path(project_root)

    # Direct file path
    candidate = Path(name)
    if candidate.is_absolute() or name.endswith((".yaml", ".yml")):
        # Resolve relative paths against project_root, not CWD
        if not candidate.is_absolute():
            candidate = project_root / candidate
        if candidate.exists():
            return load_schema(candidate)
        raise SchemaError(f"Schema file not found: {candidate}")

    # Project-local ./schemas/
    project_schema = _resolve_named_schema_file(project_root / "schemas", name)
    if project_schema is not None:
        return _load_installed_schema(project_schema)

    # User ~/.atlas/schemas/
    user_schema = _resolve_named_schema_file(Path.home() / ".atlas" / "schemas", name)
    if user_schema is not None:
        return _load_installed_schema(user_schema)

    # Built-in package data
    builtin = _load_builtin_schema(name)
    if builtin is not None:
        return builtin.model_copy(deep=True)

    project_search = project_root / "schemas" / f"{name}.yaml|yml"
    user_search = Path.home() / ".atlas" / "schemas" / f"{name}.yaml|yml"
    raise SchemaError(
        f"Schema '{name}' not found. Searched: project ({project_search}), user ({user_search}), built-in package data"
    )


def _load_dir_schemas(directory: Path) -> list[Schema]:
    """Load every YAML schema in *directory* (missing dir -> empty)."""
    if not directory.is_dir():
        return []
    paths = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    by_stem: dict[str, list[Path]] = {}
    for path in paths:
        by_stem.setdefault(path.stem, []).append(path)
    ambiguous = {stem: matches for stem, matches in by_stem.items() if len(matches) > 1}
    if ambiguous:
        stem, matches = sorted(ambiguous.items())[0]
        names = ", ".join(str(path) for path in matches)
        raise SchemaError(f"Ambiguous schema '{stem}': found both {names}")

    return [_load_installed_schema(path) for path in paths]


def discover_schemas(project_root: str | Path | None = None) -> list[Schema]:
    """Return every available schema, honouring :func:`resolve_schema` precedence.

    Merges built-in schemas with user (``~/.atlas/schemas``) and, when
    *project_root* is given, project-local (``{project_root}/schemas``) ones.
    When two schemas share a ``name``, the higher-precedence source wins
    (project > user > built-in), so a project override replaces the built-in
    rather than detecting alongside it.  Sorted by name for deterministic output.
    """
    by_name: dict[str, Schema] = {}
    for schema in load_all_schemas():  # lowest precedence
        by_name[schema.name] = schema
    for schema in _load_dir_schemas(Path.home() / ".atlas" / "schemas"):
        by_name[schema.name] = schema
    if project_root is not None:
        for schema in _load_dir_schemas(Path(project_root) / "schemas"):
            by_name[schema.name] = schema
    return [by_name[name] for name in sorted(by_name)]


def _normalize_glob(pattern: str) -> str:
    """Ensure trailing ``**`` includes files, not just directories.

    Python 3.10's ``Path.glob("dir/**")`` yields only directories.
    Appending ``/*`` makes it match files recursively.
    """
    if pattern.endswith("**"):
        return pattern + "/*"
    return pattern


def _is_within(path: Path, root: Path) -> bool:
    """Whether *path* resolves inside *root* (symlinks included)."""
    try:
        path.resolve().relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _is_sync_file(path: Path, unit_path: Path) -> bool:
    """Whether *path* is a file whose resolved target stays in the unit."""
    try:
        return path.is_file() and _is_within(path, unit_path)
    except OSError:
        return False


def _collect_included(unit_path: Path, schema: Schema) -> set[Path]:
    """Files matched by the schema's include patterns (all files if none)."""
    if not schema.sync.include:
        return {path for path in unit_path.rglob("*") if _is_sync_file(path, unit_path)}
    included: set[Path] = set()
    for pattern in schema.sync.include:
        for match in unit_path.glob(_normalize_glob(pattern)):
            if _is_sync_file(match, unit_path):
                included.add(match)
    return included


def _collect_excluded(unit_path: Path, schema: Schema) -> set[Path]:
    """Files matched by the schema's exclude patterns (directories expand to files)."""
    excluded: set[Path] = set()
    for pattern in schema.sync.exclude:
        for match in unit_path.glob(_normalize_glob(pattern)):
            if _is_sync_file(match, unit_path):
                excluded.add(match)
            elif match.is_dir() and _is_within(match, unit_path):
                for f in match.rglob("*"):
                    if _is_sync_file(f, unit_path):
                        excluded.add(f)
    return excluded


def get_sync_files(unit_path: str | Path, schema: Schema) -> list[Path]:
    """Return the list of files in *unit_path* that pass the schema's sync filters.

    Include/exclude rules are applied using pathlib glob patterns.
    Exclude takes precedence over include for overlapping patterns.
    Returns a sorted list of absolute ``Path`` objects (files only).
    """
    try:
        unit_path = Path(unit_path).resolve()
    except (OSError, RuntimeError):
        return []
    if not unit_path.is_dir():
        return []
    return sorted(_collect_included(unit_path, schema) - _collect_excluded(unit_path, schema))


def resolve_key_output(schema: Schema, output_name: str, unit_dir: str | Path | None) -> list[str]:
    """Resolve a ``key_outputs`` name to unit-relative POSIX paths.

    ``schema.key_outputs[output_name]`` may be a plain relative path, or a
    template/glob containing ``{placeholder}`` tokens or ``*``.  For the
    latter, ``{placeholder}`` is turned into ``*`` and *unit_dir* is globbed;
    each hit is returned relative to *unit_dir*.  When nothing matches (or
    *unit_dir* is ``None``/absent) the literal key path is returned as the
    single element, so callers can still form a remote URL from it.

    Raises :class:`SchemaError` when *output_name* is not a defined key output.
    """
    if output_name not in schema.key_outputs:
        available = ", ".join(sorted(schema.key_outputs.keys()))
        raise SchemaError(f"unknown key output '{output_name}'. Available: {available}")

    key_path = schema.key_outputs[output_name]

    if "{" in key_path or "*" in key_path:
        glob_pattern = re.sub(r"\{[^}]+\}", "*", key_path)
        unit = Path(unit_dir) if unit_dir is not None else None
        matched = sorted(unit.glob(glob_pattern)) if unit is not None and unit.is_dir() else []
        if not matched or unit is None:
            return [key_path]
        return [m.relative_to(unit).as_posix() for m in matched]

    return [key_path]
