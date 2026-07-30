# Schema file reference

A schema is a single YAML file describing one data type. The annotated
example below shows the common fields; the tables that follow give the full
surface, types, and defaults.

Schema files are strict: unknown and duplicate keys are errors. Filesystem
paths and globs must be non-empty, unit-relative, and must not contain `..`;
numeric depths, sizes, and counts must be non-negative and coherent.

```yaml
name: sample-run                # required, unique identifier
version: "1.0"
description: Generic processing run with raw data and reports

# How to recognise this data layout on disk. Fully drives `atlas detect`.
detection:
  landmark: "raw_data/manifest.csv"   # rglob seed; omit for a validation-only schema
  landmark_type: file           # file | dir | any (default any)
  unit_depth: 2                 # .parent hops from the landmark to the unit dir
                                # (manifest.csv -> raw_data/ -> the unit dir)
  markers:                      # ALL must exist under the unit dir to match
    - "raw_data/manifest.csv"
  require_any_glob:             # optional: >=1 of these (relative to unit) must match a file
    - "raw_data/*.csv"

# Which files travel when the unit is synced. exclude wins over include.
sync:
  include:
    - "raw_data/**/*"
    - "reports/**/*"
    - "outputs/**/*"
  exclude:
    - "**/*.tmp"
    - "**/.DS_Store"

# Validation rules (all optional; omit the section to skip validation).
validate:
  required:                     # files that must exist (relative to unit)
    - "raw_data/manifest.csv"
  required_any:                 # at least one alternative glob must match a file
    - "outputs/final.csv"
    - "outputs/final.parquet"
  required_dirs:                # directories that must exist
    - "raw_data"
  warn_if_missing:              # missing -> warning, not error
    - "reports/summary.pdf"
  fail_on:                      # any glob match -> error (processing failed)
    - "_errors"
    - "failed"
  min_size_mb: 0.1              # size/count bounds run on the post-sync file list
  max_size_gb: 50.0
  min_file_count: 1
  max_file_count: 10000
  filename_pattern: "^.*\\.(?i:csv|json|txt|pdf)$"   # non-match -> warning

# Named outputs. Plain path, or a {placeholder}/* template globbed at locate time.
key_outputs:
  summary_report: "reports/summary.pdf"
  output_tables: "outputs/*.csv"

manifest:
  records:
    - name: result
      glob: "outputs/*.csv"
      asset_key:
        field: result_filename
        source: filename
      extractors:
        - source: filename
          regex: "^(?P<participant_id>G\\d+-\\d+)_(?P<visit_id>V\\d+)\\.csv$"
      casts:
        participant_id: string
    - name: report
      glob: "reports/*.pdf"
      extractors:
        - source: filename
          regex: "^(?P<report_participant_id>G\\d+-\\d+)_(?P<report_visit_id>V\\d+)\\.pdf$"
  relationships:
    - name: result_report
      left:
        record_type: result
        fields: [participant_id, visit_id]
        required: true
      right:
        record_type: report
        fields: [report_participant_id, report_visit_id]
        required: false
      cardinality: one_to_one
  tables:
    - name: samples
      glob: "metadata/samples.xlsx"
      optional: true
      join:
        left: [participant_id]
        right: [participant_id]
        cardinality: many_to_one
```

## Top-level fields

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | str | **required** | Unique identifier; how the schema is resolved by name. |
| `version` | str | `"1.0"` | Free-form version string. |
| `description` | str | `""` | One-line description shown by `atlas schemas`. |
| `detection` | mapping | empty | Omit (or omit `landmark`) for a validation-only schema. |
| `sync` | mapping | empty | Include/exclude globs for the travelling file set. |
| `validate` | mapping | absent | Omit the whole section to skip validation. |
| `key_outputs` | mapping | `{}` | `output_name: path-or-glob-template`. |
| `manifest` | mapping | empty | Path record extraction, companion relationships, and optional table enrichment. |

## `detection`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `landmark` | str | absent | rglob seed under the scanned root. No landmark → never detected. |
| `landmark_type` | `file` \| `dir` \| `any` | `any` | What the landmark must be. |
| `landmark_parent` | str | absent | Require the landmark's parent directory to have this name. |
| `unit_depth` | int | `1` | `.parent` hops from the landmark to the unit dir. |
| `markers` | list[str] | `[]` | ALL must exist under the unit dir to match. |
| `require_any_glob` | list[str] | `[]` | At least one glob (relative to unit) must match a file. |
| `exclude_if_markers` | list[str] | `[]` | Skip the candidate when any listed path exists. |
| `exclude_if_cmdline_subcommand` | mapping | absent | Skip by a recorded command line (file + subcommand). |
| `sync_by` | str | `"subdirectory"` | How units are enumerated under the stage. |
| `unit_is_directory_stage` | bool | `false` | Treat the unit dir itself as the stage. |

## `sync`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `include` | list[str] | `[]` | Globs relative to the unit dir; empty means every file. |
| `exclude` | list[str] | `[]` | Globs removed from the include set — **exclude wins**. |

## `validate`

All fields optional. Size, count, and filename checks run on the post-sync
file list (include minus exclude).

| Field | Type | Severity | Notes |
| --- | --- | --- | --- |
| `required` | list[str] | error | Files that must exist (relative to unit). |
| `required_any` | list[str] | error | At least one listed file glob must match; useful for alternative layouts or formats. |
| `required_dirs` | list[str] | error | Directories that must exist. |
| `warn_if_missing` | list[str] | warning | Missing → warning, not error. |
| `fail_on` | list[str] | error | Any glob match → error (failure marker). |
| `min_size_mb` / `max_size_gb` | float | error | Total-size bounds. |
| `min_file_count` / `max_file_count` | int | error | File-count bounds. |
| `filename_pattern` | str (regex) | warning | Checked against each synced file's name; a bad regex is rejected at schema load time. |

## `key_outputs`

Each entry maps an output name to either a plain unit-relative path or a
`{placeholder}/*` template. `resolve_key_output` globs templates against the
unit dir at locate time and returns the literal path when nothing matches —
see the [Python API](api.md#key-outputs).

## `manifest`

`records` is an ordered list. Each record has a unique `name`, a unit-relative
file `glob`, and zero or more regex `extractors`. Optional `groups` provide
stable names for selecting related record kinds, while `tags` attach semantic
string, integer, float, or boolean descriptors for `dataframe(where=...)`
queries. Group names must be non-empty and unique within a record; tag names
must be non-empty. Groups and tags describe record rules and are not emitted as
DataFrame columns. A rule's unambiguous final `glob` suffix also supplies its
queryable `extension`; `constants` supply exact-match declared metadata.
An optional `asset_key` maps either the complete `filename` or unit-relative
`relative_path` to a non-reserved schema-owned output `field`. The field must
not collide with tags, captures, constants, derived fields, or provenance.
Extractor `source` is one of `unit_name`, `relative_path`, or `filename`; named
regex groups become DataFrame columns. `derive` adds format-string fields,
`casts` supports `string`, `integer`, `float`, `boolean`, and `date`, and date
casts may declare a `date_formats` entry. `constants` adds schema-owned scalar
values before derived fields and casts are evaluated.

`tables` optionally enrich records from CSV, TSV, or XLSX files beneath the
caller's `context_root`. A table may declare `format`, `sheet`, `header`,
`rename`, `casts`, and `date_formats`. Its `join.left` and `join.right` key
lists must have equal lengths; `cardinality` is `one_to_one`, `one_to_many`,
`many_to_one`, or `many_to_many`. Missing optional tables are ignored, while
one-to-many matches explode file rows into scalar metadata entities.

`relationships` declares companion record types without merging their rows.
Each uniquely named relationship has typed `left` and `right` endpoints. An
endpoint names one declared `record_type`, a non-empty ordered list of emitted
`fields`, and whether that endpoint is `required` as a companion for records at
the opposite endpoint. Left and right field lists must have equal lengths but
may use different names. Referenced fields must be emitted by their record
rules; tags and table-only fields are unavailable. `cardinality` is
`one_to_one`, `one_to_many`, `many_to_one`, or `many_to_many`, with left/right
uniqueness following the usual join convention. Relationship matching is
always scoped by `unit_id` in addition to the declared fields, so equal sample
keys in separate units never form a bundle. See
[`related_records()`](api.md#declarative-record-relationships) for bundle and
validation behavior.

## Built-in schemas

Twelve schemas ship inside the package (`src/atlas/schemas/*.yaml`):

| Name | Version | Description | Landmark (type) | `unit_depth` | Notable guards |
| --- | --- | --- | --- | --- | --- |
| `10x-bcl-demux` | 1.0 | BCL demultiplexing output | `Reports` (dir) | 1 | Requires `Logs` and at least one FASTQ. |
| `10x-cellranger-count` | 1.0 | Cell Ranger count pipeline output | `outs/filtered_feature_bc_matrix.h5` (file) | 2 | Requires `_cmdline`; excludes multi runs. |
| `10x-cellranger-multi` | 1.1 | Cell Ranger multi pipeline output | `outs/config.csv` (file) | 2 | Accepts v8+ per-sample or legacy flat outputs. |
| `csv-dataset` | 1.0 | Tabular data export (a directory of CSV extracts) | `Exports` (dir) | 1 | `require_any_glob: Exports/**/*.csv` |
| `facs-sort` | 2.5 | BD FACSMelody sorting data | `DataFilesFromMelody` (dir) | 2 | Covers FCS/ZIP, FlowJo WSP, DataStats XLSX, reports, and controls. |
| `facs-sort-diva` | 1.0 | BD FACSDiva sorting data | `PopulationSummaryFilesFromDV` (dir) | 2 | `landmark_parent: ClinicalSamples`; requires a population-summary CSV. |
| `illumina-bcl-run` | 1.0 | Illumina sequencer BCL run | `RunInfo.xml` (file) | 1 | Requires `Data/Intensities/BaseCalls`. |
| `monorepo-build` | 1.1 | Monorepo workspace build output (flat and per-package layouts) | `.workspace-stamp` (dir) | 1 | `required_any` accepts either bundle layout. |
| `photo-import` | 2.0 | Photo import from a camera card into a media library | `RawPhotos` (dir) | 2 | `landmark_parent: MediaLibrary`, `require_any_glob: *.jpg` |
| `report-bundle` | 1.0 | Validation-only report bundle | — (no detection) | — | Never detected; validate-only. |
| `site-archive` | 1.0 | Static website archive (self-contained snapshot) | `archive.json` (file) | 1 | — |
| `web-build` | 1.0 | Single-page web app build output (bundler build) | `dist/app.js` (file) | 2 | `exclude_if_markers`, `exclude_if_cmdline_subcommand` (skips monorepo builds) |

Inspect any of them with `atlas show NAME --yaml`, or read the YAML sources —
each is a worked example of the fields above. Per-schema landmark/depth detail
also lives in the [detection runbook](../runbooks/detection-empty.md).
