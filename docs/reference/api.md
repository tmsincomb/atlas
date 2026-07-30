# Python API

atlas is a library first — the CLI is a thin wrapper over the functions
below. Everything is importable from the top-level package:

```python
import atlas
```

The public surface is deliberately small and standalone (see
[ADR-0001](../adr/0001-atlas-is-standalone.md)): other tools import atlas;
atlas imports nothing back.

## Detection

```python
detections = atlas.detect("/data/project")    # -> list[Detection]
for d in detections:
    d.schema_name   # e.g. "csv-dataset"
    d.stage_path    # ABSOLUTE, resolved Path to the stage directory
    d.unit_ids      # child unit directory names
    d.sync_by       # "subdirectory"
```

`detect(root, schemas=None)` runs every detector over `root` and returns one
`Detection` per matched stage. Pass `schemas=atlas.discover_schemas(project_root=...)`
to include project-local and user schemas the way the CLI does; the default
is the built-ins.

```python
atlas.extract_unit_ids(stage_path)
```

Returns the unit IDs of a stage directory (sorted child directory names).

## Schema loading & resolution

```python
schema  = atlas.resolve_schema("csv-dataset", project_root="/data/project")
schemas = atlas.load_all_schemas()             # all packaged schemas
schema  = atlas.load_schema("/path/to/custom.yaml")
schemas = atlas.discover_schemas(project_root="/data/project")
```

- `resolve_schema(name, project_root)` — resolves by explicit path →
  project-local `schemas/` → user `~/.atlas/schemas/` → built-in; first hit
  wins. Raises `SchemaError` when nothing matches.
- `load_all_schemas()` — every packaged built-in.
- `load_schema(path)` — one YAML file; validation errors raise `SchemaError`.
- `discover_schemas(project_root)` — built-ins merged with project and user
  schemas, name collisions resolved in the project → user → built-in order.

The `Schema` pydantic model (and its parts `DetectionConfig`, `SyncConfig`,
`ValidateConfig`) mirrors the [schema file reference](schemas.md).

## Metadata manifests

```python
frame = atlas.load_dataframe(
    "/data/project/sorting",
    schema="facs-sort-diva",
    project_root="/data/project",
    context_root="/data/project",
    record_groups={"fcs"},
    strict=True,
)

manifest = atlas.AtlasManifest("facs-sort-diva", project_root="/data/project")
record = manifest.record("/data/project/sorting/run/ClinicalSamples/DataFilesFromDV/sample.fcs")
record.metadata       # path-derived fields

manifest.record_types          # tuple[str, ...]
manifest.record_groups         # tuple[str, ...]
manifest.tags                  # tuple[ManifestTagCapability, ...]
manifest.relationships         # tuple[ManifestRelationshipCapability, ...]
capabilities = manifest.capabilities
```

`load_dataframe()` is the concise public API for DataFrame-only consumers. It
accepts a schema name or `Schema` object and delegates to the equivalent
`AtlasManifest(...).dataframe(...)` call, so its selectors, result, and errors
are identical. Instantiate `AtlasManifest` when advanced code also needs
`record()`, which finds the nearest matching data unit and returns one
`ManifestRecord`. Both DataFrame APIs accept a file, unit, stage, or higher root
and return one row per schema-selected file. Optional schema-declared CSV, TSV,
or XLSX tables are found under `context_root` and merged into the records.

`AtlasManifest.capabilities` describes a schema without exposing its nested
configuration models. The immutable `ManifestCapabilities` result contains
deterministically ordered `records`, `record_types`, `record_groups`, `tags`,
`relationships`, and `output_fields`; the latter includes stable provenance columns, named regex
captures, constants, derived fields, and explicitly declared table rename,
cast, and join fields. Each immutable `ManifestRecordCapability` provides the
groups, tag name/value descriptors, and path-declared output fields for one
record type; `asset_key` identifies any schema-declared external asset key.
The direct properties shown above mirror their aggregate
capability values. Empty and populated custom schemas use exactly the same API
as built-in schemas.

### Declarative record relationships

`AtlasManifest.relationships` exposes immutable
`ManifestRelationshipCapability` values declared by the schema. To group a
manifest DataFrame by one relationship, pass its name to `related_records()`:

```python
manifest = atlas.AtlasManifest("facs-sort")
records = manifest.dataframe("/data/G003/sorting", strict=True)
result = manifest.related_records(records, "workspace_fcs")

for bundle in result.bundles:
    print(bundle.unit_id, bundle.key)
    print([record.path for record in bundle.left])   # FlowJo workspaces
    print([record.path for record in bundle.right])  # FCS files
```

Every immutable `ManifestRelationshipBundle` contains one unit, one declared
key, deterministically path-sorted `left` and `right` records, and that bundle's
validation issues. A `ManifestRelatedRecord` exposes its `record_type`, `Path`,
and a detached read-only `values` mapping of the complete source row. Bundles
are ordered by `unit_id` and typed key representation; the API ignores record
types outside the two endpoints. Matching is always scoped by `unit_id`, then
compares declared key values by both type and value, preventing records in
separate units—or values such as `True` and `1`—from matching accidentally.
Repeated DataFrame rows for the same unit, endpoint, and file path collapse to
one related file; inconsistent keys on repeated path rows are invalid.

`ManifestRelationshipResult.passed` is false whenever `issues` is non-empty.
Issues have stable codes and deterministic actionable messages:

- `manifest.relationship.invalid_key` — a key value is null or unhashable.
- `manifest.relationship.missing_companion` — a required endpoint has no match.
- `manifest.relationship.duplicate_key` — an endpoint violates the declared cardinality.
- `manifest.relationship.ambiguous_companion` — a counterpart has multiple candidates where cardinality permits one.

Endpoint `required` flags are directional: `right.required: true` means each
left key requires at least one right record, while `left.required: true` means
each right key requires a left record. `one_to_one` requires both endpoint keys
unique, `one_to_many` requires only the left unique, `many_to_one` only the
right, and `many_to_many` neither. Missing optional companions still produce a
bundle with an empty endpoint and no issue. Pass `strict=True` to raise
`ManifestError` with the first stable issue code/message after deterministic
validation.

The built-in `facs-sort` `workspace_fcs` relationship pairs
`flowjo_workspace` and `melody_fcs` records through schema-extracted
`sample_id`/`visit_id` fields. No consumer filename parsing is required; one
workspace may relate to multiple FCS files.

Pass `record_types` to scan only named manifest record rules; unknown names
raise `ManifestError`. Selection happens before strict validation, so malformed
unrelated record kinds do not block a focused manifest.
Pass `record_groups` to select stable schema-declared groups instead of record
implementation names. Multiple groups form a union; when `record_types` is also
provided, Atlas returns their intersection. Unknown group names raise
`ManifestError`, and group selection also happens before discovery and strict
validation. For example, the built-in `facs-sort` schema supports
`record_groups={"fcs"}`, `{"workspaces"}`, and `{"data_stats"}`.

Pass `where` for an exact-match query over schema-declared record attributes:

```python
fcs = atlas.load_dataframe(
    "/data/G003/sorting",
    schema="facs-sort",
    where={"media_type": "application/vnd.isac.fcs"},
    strict=True,
)
```

Supported fields are `record_group`, `extension`, any record `tags` key, and
any record `constants` key. Extensions are case-insensitive final suffixes
declared unambiguously by a record glob, so both `"fcs"` and `".FCS"` select
`.fcs` rules. Query values are scalar exact matches; fields within `where` are
combined with AND. `record_types`, `record_groups`, and `where` also intersect.
Atlas validates fields, value types, known values, and contradictory selectors
before file discovery. Regex captures and derived fields are intentionally not
queryable because their values do not exist until a path has been parsed;
runtime asset-key values follow the same rule.

Records may declare one external `asset_key` sourced from their complete
`filename` or unit-relative `relative_path`. Atlas emits it under the
schema-owned field name. The built-in FACS rule uses `fcs_filename`, leaving
biological `sample_id` unchanged even when FlowKit uses its own `sample_id`
column for the filename:

```python
manifest = atlas.AtlasManifest("facs-sort")
fcs = manifest.dataframe("/data/G003/sorting", record_groups={"fcs"}, strict=True)
joined = manifest.join_external_assets(
    fcs,
    flowkit_metadata,
    record_type="melody_fcs",
    external_key="sample_id",
    cardinality="one_to_one",
)
```

`join_external_assets()` consumes the external key for matching rather than
emitting it, then appends only non-key external columns. Inputs must have
unique string column names, non-null string keys, and exactly the requested
manifest `record_type`; every record key must match, while unused external rows
are ignored. `one_to_one` requires both keys unique, `one_to_many` only the
record key, `many_to_one` only the external key, and `many_to_many` neither.
Record order is preserved, one-to-many matches follow external input order,
and the result receives a fresh range index. Missing/null/unmatched keys,
disallowed duplicates, and any external payload column that would overwrite a
record column raise `ManifestError`; the helper never creates suffix columns.

### Reusable DataFrame attachments

Use the integration-neutral `attach_dataframe()` operation when a parsed table
or analysis result already has explicit keys. The top-level function and
`AtlasManifest.attach_dataframe()` method are equivalent:

```python
attached = atlas.attach_dataframe(
    fcs,
    analysis_results,
    left_on=["sample_id", "visit_id"],
    right_on=["analysis_sample", "analysis_visit"],
    cardinality="many_to_one",
)
```

`records` must retain all standard Atlas provenance columns. The attachment may
be a DataFrame, a column mapping, or a sequence of row mappings. `left_on` and
`right_on` accept one key or equal-length ordered key sequences; right key
columns are consumed and are not emitted. Keys must exist, be unique within
each sequence, and contain no nulls. `one_to_one` requires both sides unique,
`one_to_many` only the manifest side, `many_to_one` only the attachment side,
and `many_to_many` neither. Cardinality failures identify the side and show
bounded duplicate-key examples in `ManifestError`.

Attachment is always left-preserving. By default, every manifest key must
match; `unmatched="keep"` instead retains unmatched manifest rows with null
attachment values. Attachment-only rows are ignored. Original manifest row
order is preserved, multiple matches follow attachment input order, attachment
payload fields retain their input column order, and the result has a fresh
range index. Neither input is mutated.

Right payload fields may never use reserved manifest provenance or standardized
enrichment names. Any other payload field that collides with a manifest column
raises by default. Set an explicit `attachment_suffix` to rename only the
colliding attachment fields while leaving every manifest field unchanged:

```python
attached = atlas.attach_dataframe(
    fcs,
    analysis_results,
    left_on="sample_id",
    right_on="analysis_sample",
    attachment_suffix="_analysis",
)
```

Atlas validates that the suffix is non-empty and that each resulting name is
unique and still does not collide. It never creates pandas `_x`/`_y` columns or
silently overwrites provenance. Missing columns, null keys, duplicate keys,
unmatched manifest rows, reserved names, unsafe suffixes, and conversion/merge
failures all raise actionable `ManifestError` messages.

Use `AtlasManifest.enrich_records()` when fields must be read from each file
rather than joined from an existing table. An enricher accepts one immutable
`ManifestEnrichmentInput` and returns a mapping of new column names to values:

```python
def read_header(record: atlas.ManifestEnrichmentInput) -> dict[str, object]:
    return {
        "payload_bytes": record.path.stat().st_size,
        "path_sample_id": record.metadata.get("sample_id"),
    }

enriched = manifest.enrich_records(
    fcs,
    read_header,
    record_groups={"fcs"},
)
```

The input exposes frozen schema, record, unit, path, filename, extension, and
parse provenance. `path` is a `Path`; `metadata` is a detached, read-only
mapping of every non-provenance input field. Atlas calls the enricher only for
rows matched by `record_types`, `record_groups`, and `where`, using the same
selector validation and intersection rules as `dataframe()`. Source row order
is preserved. New fields are appended in sorted order, followed by stable
`enrichment_name`, `enrichment_status`, and `enrichment_error` columns.
Unselected rows receive status `not_selected`.

By default, an exception or invalid return affects only that row: Atlas keeps
the source row, reports status `error`, and stores `ExceptionType: message` in
`enrichment_error`. Successful rows report `ok`. Returned fields may not
overwrite any input or standardized enrichment column; a collision is handled
as that row's enrichment error. Pass `strict=True` to raise `ManifestError` at
the first failure in input order. Strict mode is deterministic and fail-fast,
but cannot roll back side effects a callable performed before the failure.

Applications can register an enricher under an explicit process-local name:

```python
atlas.register_manifest_enricher("my_package.fcs_text", read_header)
enriched = manifest.enrich_records(fcs, "my_package.fcs_text")
atlas.unregister_manifest_enricher("my_package.fcs_text")
```

Duplicate registration raises unless `replace=True`; unknown names also raise.
Registration never imports or discovers plugins. Domain integrations therefore
remain optional: for example, a FlowKit-backed FCS TEXT enricher belongs in the
consumer package, which imports FlowKit and explicitly registers or passes its
callable. Atlas itself never imports FlowKit.

One-to-many enrichment deliberately repeats a source path so multiplexed 10x
outputs retain scalar participant and sample keys.

Every frame begins with provenance columns (`schema_name`, `schema_version`,
`record_type`, `unit_id`, `path`, `relative_path`, `filename`, `extension`) and
`parse_status` / `parse_error`. Non-strict extraction retains partial rows;
pass `strict=True` to raise `ManifestError` instead.

The built-in `facs-sort` manifest exposes native FACSMelody nomenclature such
as `sample_id`, `instrument`, `probe`, `specimen`, `artifact`, `tube_id`,
`pool_number`, and `replicate`. It covers FCS/ZIP data, FlowJo WSP workspaces,
DataStats XLSX files, sample reports, and controls.

The built-in FACS and Cell Ranger schemas expose compatible columns. For the
G003-style sequencing manifest bridge:

```python
join_on = ["sort_date", "visit_id", "pool_number"]
facs = atlas.load_dataframe("/data/G003/sorting", schema="facs-sort")
tenx = atlas.load_dataframe(
    "/data/G003/output",
    schema="10x-cellranger-multi",
    context_root="/data/G003",
)
tenx_samples = tenx.loc[tenx["record_type"] == "config"]
joined = facs.merge(tenx_samples, on=join_on, how="inner", validate="many_to_one")
```

Here `sort_date`, `visit_id`, and `pool_number` come from the FACS path and are
the minimum composite join key. `participant_id` and `vdj_index` arrive from
the sequencing-table enrichment and remain result columns.

## Sync file selection

```python
files = atlas.get_sync_files(unit_path, schema)   # -> sorted list[Path]
```

Applies the schema's `sync.include` then removes `sync.exclude` matches
(exclude wins). This is the exact file list validation's size/count/filename
checks run against.

## Key outputs

```python
paths = atlas.resolve_key_output(schema, "summary_report", unit_dir)
```

Resolves a named output to unit-relative POSIX paths. `{placeholder}/*`
templates are globbed against `unit_dir`; the literal path is returned when
nothing matches or `unit_dir` is `None`. Raises `SchemaError` for an unknown
output name.

## Validation

```python
result = atlas.validate_data_unit(unit_path, schema)
result.passed        # bool
result.errors        # list[str]
result.warnings      # list[str]
result.sync_files    # list[Path] the checks ran against
result.checks        # list[RuleResult] with expected, actual, examples, severity
```

Runs every check and collects all findings — it never stops at the first
problem. `passed` is `True` iff `errors` is empty; warnings alone don't fail.
The legacy `errors` and `warnings` strings remain unchanged. Each structured
`RuleResult` additionally exposes generated valid witnesses through
`examples: list[str]`.

## Errors & logging

- `atlas.SchemaError` — raised for unknown schemas/outputs and bad schema
  files.
- `atlas.get_logger(name)` — the package's structured logger (JSON or text,
  secret-redacting); configured via
  [environment variables](environment.md#logging).
- `atlas.__version__` — the installed package version.

!!! note "Generated API docs"
    CI additionally generates full HTML API documentation with `pdoc` on
    every run, uploaded as the `api-docs` workflow artifact.
