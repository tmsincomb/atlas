# 🗺️ atlas <a href="#anatomy-of-a-schema-yaml"><img align="right" width="42%" src="https://raw.githubusercontent.com/tmsincomb/atlas/main/docs/assets/hero.svg" alt="Animated atlas hero: a parchment schema sheet projects a holographic directory tree; a scan sweep detects data units, checkmarks validate them, and gold pins locate key outputs"></a>

**schema-driven cartography for project data — detect, validate, locate, extract.**

[![CI](https://github.com/tmsincomb/atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/tmsincomb/atlas/actions/workflows/ci.yml)
[![Release](https://github.com/tmsincomb/atlas/actions/workflows/release.yml/badge.svg)](https://github.com/tmsincomb/atlas/actions/workflows/release.yml)
[![PyPI](https://img.shields.io/pypi/v/atlas-manifest?logo=pypi&logoColor=white)](https://pypi.org/project/atlas-manifest/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue)](https://mypy-lang.org/)

**atlas** maps data layouts. It owns the *schemas*, compact YAML specs that
define what a valid data unit or stage looks like on disk, then uses them to
**detect**, **validate**, and **locate outputs** in project data.

A schema answers four questions about one data layout, for example a raw data
import, processing run, or report bundle:

- **What does it look like?** Detection markers that identify it on disk.
- **What is valid?** Required files/dirs, size and file-count bounds, failure markers.
- **Where are useful outputs?** Named `key_outputs` paths.
- **What metadata is encoded in paths?** Manifest record and table rules.

atlas is **standalone**. Its runtime stack is `click`, `pydantic`, `pyyaml`,
`pandas`, and `openpyxl`,
and it does **not** depend on any data-management engine. Other tools may call
atlas as a library; atlas never calls them.

## Install

```bash
pip install atlas-manifest          # library + CLI
pip install "atlas-manifest[tui]"   # + the interactive `atlas tui` survey (textual)
```

This installs the `atlas` Python package and the `atlas` console script.

## CLI

```bash
atlas schemas                              # list the built-in schemas
atlas show NAME [--yaml]                   # inspect a schema; --yaml is copyable
atlas detect PATH                          # find known data types under PATH
atlas validate PATH --schema NAME          # validate a data unit against a schema
atlas tui [PATH] [--schema NAME]           # interactive schema/validation survey
```

- `atlas schemas`: one line per built-in: name, version, description.
- `atlas show NAME`: prints a compact human-readable summary. Add `--yaml`
  for the complete resolved schema as normalized, copyable YAML. `NAME` may
  be a built-in name, a project/user schema name, or a path to a `.yaml` file.
- `atlas detect PATH`: runs every detector over `PATH` and prints, per stage,
  `schema_name`, the stage path relative to `PATH`, and its unit IDs. Prints
  `No known data types detected.` when nothing matches.
- `atlas validate PATH --schema NAME`: validates the unit directory `PATH`.
  Warnings go to stderr as `warning: …`, errors as `error: …`. Exits non-zero
  when validation fails.

### Built-in schemas

Run `atlas schemas` to list packaged schemas available in this install. Output
shows each schema's name, version, and description.

### Interactive TUI

`atlas tui` (requires `pip install 'atlas-manifest[tui]'`) is a split-pane survey for
learning a schema and debugging an input against it: the left pane is the
schema's rule tree with a ✓/⚠/✗ glyph per rule, the right pane explains what
the highlighted rule expects and what the input actually contains.

```bash
atlas tui --schema report-bundle           # learn mode: browse what the schema expects
atlas tui ./run42 --schema report-bundle   # mark every rule pass/warn/fail for one unit
atlas tui ./data                           # detect units, survey each against its schema
```

`Enter` drills into a rule's findings, `n` jumps to the next failing rule,
`r` re-validates from disk, `q` quits. With `--once` (or piped) it prints one
static text report instead — no extra required, exit code always `0`; the
scriptable gate remains `atlas validate`.

## Library API

```python
import atlas

# Detection, runs all built-in detectors over a root directory.
detections = atlas.detect("/data/project")    # -> list[Detection]
for d in detections:
    d.schema_name   # e.g. "sample-run"
    d.stage_path    # ABSOLUTE, resolved Path to the stage directory
    d.unit_ids      # child unit directory names
    d.sync_by       # "subdirectory"

# Unit IDs of a stage directory (sorted child dir names).
atlas.extract_unit_ids(stage_path)

# Schema loading / resolution.
schema  = atlas.resolve_schema("sample-run", project_root="/data/project")
schemas = atlas.load_all_schemas()             # all packaged schemas
schema  = atlas.load_schema("/path/to/custom.yaml")

# Sync file list (include/exclude applied; exclude wins).
files = atlas.get_sync_files(unit_path, schema)   # -> sorted list[Path]

# Key outputs, resolve a named output to unit-relative POSIX paths.
# Globs {placeholder}/* templates against unit_dir; returns the literal
# path when nothing matches or unit_dir is None. Raises SchemaError for
# an unknown output name.
paths = atlas.resolve_key_output(schema, "summary_report", unit_dir)

# Validation.
result = atlas.validate_data_unit(unit_path, schema)
result.passed        # bool
result.errors        # list[str]
result.warnings      # list[str]
result.sync_files    # list[Path] the checks ran against
result.checks        # list[RuleResult]: one per declared rule, ok/warn/fail,
                     # with rule_id, expected, and actual for rich reporting

# Metadata manifests.
sorting = atlas.AtlasManifest("facs-sort-diva")
record = sorting.record("/data/sorting/run/ClinicalSamples/DataFilesFromDV/sample.fcs")
frame = atlas.load_dataframe("/data/sorting", schema="facs-sort-diva")
sorting.record_types   # stable, immutable discovery tuple
sorting.capabilities  # groups, tags, records, relationships, and declared fields

# Schemas can bundle companion records and validate their cardinality.
facs_manifest = atlas.AtlasManifest("facs-sort")
facs_records = facs_manifest.dataframe("/data/G003/sorting", strict=True)
workspace_fcs = facs_manifest.related_records(facs_records, "workspace_fcs")
workspace_fcs.passed
workspace_fcs.bundles  # deterministic FlowJo workspace/FCS groups

# FACS paths supply the minimum biological join key.
join_on = ["sort_date", "visit_id", "pool_number"]
facs = atlas.load_dataframe(
    "/data/G003/sorting",
    schema="facs-sort",
    where={"media_type": "application/vnd.isac.fcs"},
    strict=True,
)
# `fcs_filename` is schema-owned; an external tool's `sample_id` can be its join key
# without replacing Atlas's biological `sample_id`.
facs = atlas.AtlasManifest("facs-sort").join_external_assets(
    facs,
    flowkit_metadata,
    record_type="melody_fcs",
    external_key="sample_id",
)

# Attach any table-like result through explicit keys and cardinality.
facs = atlas.attach_dataframe(
    facs,
    analysis_results,
    left_on=["sample_id", "visit_id"],
    right_on=["analysis_sample", "analysis_visit"],
    cardinality="many_to_one",
)

# Read additional fields from selected files with a typed per-record callable.
def file_size(record: atlas.ManifestEnrichmentInput) -> dict[str, object]:
    return {"payload_bytes": record.path.stat().st_size}


facs = atlas.AtlasManifest("facs-sort").enrich_records(
    facs,
    file_size,
    record_groups={"fcs"},
)

# context_root lets the sequencing table enrich 10x outputs with that same key.
tenx = atlas.load_dataframe(
    "/data/G003/output",
    schema="10x-cellranger-multi",
    context_root="/data/G003",
)
tenx_samples = tenx.loc[tenx["record_type"] == "config"]

# Many FACS technical files map to one sequencing sample for each composite key.
joined = facs.merge(tenx_samples, on=join_on, how="inner", validate="many_to_one")
```

`atlas.SchemaError` is raised for unknown schemas/outputs and bad schema files.

## Anatomy of a schema YAML

A schema is a single YAML file describing one data type. Fields:

```yaml
name: sample-run                # required, unique identifier
version: "1.0"
description: Generic processing run with raw data and reports

# How to recognise this data layout on disk. Fully drives `atlas detect`.
detection:
  landmark: "raw_data/manifest.csv"   # rglob seed; omit for a validation-only schema
  landmark_type: file           # file | dir | any (default any)
  unit_depth: 2                 # .parent hops from the landmark to the unit dir
  markers:                      # ALL must exist under the unit dir to match
    - "raw_data/manifest.csv"
  require_any_glob:             # optional: >=1 of these (relative to unit) must match a file
    - "raw_data/*.csv"
  # Optional extras: landmark_parent (require the landmark's parent dir name),
  # exclude_if_markers (skip when any listed path exists),
  # exclude_if_cmdline_subcommand (skip by a recorded command line),
  # sync_by, unit_is_directory_stage.

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

# Optional path metadata records and tabular enrichment.
manifest:
  records:
    - name: result
      glob: "outputs/*.csv"
      extractors:
        - source: filename
          regex: "^(?P<participant_id>G\\d+-\\d+)_(?P<visit_id>V\\d+)\\.csv$"
  tables:
    - name: samples
      glob: "metadata/samples.csv"
      optional: true
      join:
        left: [participant_id]
        right: [participant_id]
        cardinality: many_to_one
```

### One YAML, zero Python

Everything a data type needs lives in its single schema YAML, and every field
is enforced. Unknown or duplicate keys, unsafe paths, malformed globs, and
incoherent numeric bounds fail when the schema loads:

- **`detection`** drives `atlas detect`. A generic engine reads the declarative
  rules above — landmark, depth, markers, and guards — so **adding a new data
  type needs only a new YAML, no code change**.
- **`validate`** drives `atlas validate`: file/dir existence, size and
  file-count bounds, fail-on patterns, and the `filename_pattern` regex
  (checked against each synced file's name; a bad regex is rejected at load
  time). Omit the section to skip validation.
- **`sync`** and **`key_outputs`** define which files travel and where the
  useful outputs are.

## Schema resolution order

`resolve_schema(name, project_root)` searches, in order:

1. **Explicit path**: `name` is absolute, or ends in `.yaml`/`.yml` (relative
   paths resolve against `project_root`, not the CWD).
2. **Project-local**: `{project_root}/schemas/{name}.yaml` or
   `{project_root}/schemas/{name}.yml`
3. **User-wide**: `~/.atlas/schemas/{name}.yaml` or
   `~/.atlas/schemas/{name}.yml`
4. **Built-in**: packaged `atlas.schemas` data.

The first hit wins, so a project- or user-local schema **overrides** a built-in
of the same name. `SchemaError` is raised if nothing matches.

## Adding a custom schema

1. Write `sample-run.yaml` following the anatomy above (`name` is required),
   or export a concise built-in as a starting point:

   ```bash
   mkdir -p schemas
   atlas show csv-dataset --yaml > schemas/sample-run.yaml
   # Edit name, description, paths, and rules for your data layout.
   ```

2. Drop it in one of the searched locations:
   - `{project_root}/schemas/sample-run.yaml` (project override), or
   - `~/.atlas/schemas/sample-run.yaml` (available to all your projects).
3. Use it by name:

   ```bash
   atlas show sample-run --yaml
   atlas validate /data/project/generic-run --schema sample-run
   ```

   ```python
   schema = atlas.resolve_schema("sample-run", project_root="/data/project")
   ```

Or point directly at a file without installing it anywhere:

```bash
atlas validate /data/project/generic-run --schema ./path/to/sample-run.yaml
```

A schema with a `detection` block is also picked up automatically by
`atlas detect` — project (`./schemas`) and user (`~/.atlas/schemas`) schemas are
discovered alongside the built-ins, so a new data type is detectable with no
code change (a project schema overrides a built-in of the same name).

## Development

```bash
make setup    # fresh clone -> dev env; then `make test` / `make lint`
```

See [AGENTS.md](AGENTS.md) for repo layout and commands, and
[CONTRIBUTING.md](CONTRIBUTING.md) for conventions, commit style, and PR flow.

## Troubleshooting

Common failures (schema resolution, validation output, empty detection) are
covered in [docs/runbooks/](docs/runbooks/).
