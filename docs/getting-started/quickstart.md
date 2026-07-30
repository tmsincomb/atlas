# Quick start

The whole CLI is four read-only commands. Every block below is runnable from
a clone of the repo — no credentials, no setup beyond
[installation](installation.md) — because it targets the test fixtures
committed under `tests/fixtures/`.

## 1. List the built-in schemas

```console
$ atlas schemas
10x-bcl-demux         1.0     BCL demultiplexing output (bcl2fastq / bcl-convert)
10x-cellranger-count  1.0     10x Genomics Cell Ranger count pipeline output
10x-cellranger-multi  1.1     10x Genomics Cell Ranger multi pipeline output (supports both pre-v8 and v8+ layouts)
csv-dataset           1.0     Tabular data export (a directory of CSV extracts)
facs-sort             2.5     FACS cell sorting data from BD FACSMelody
facs-sort-diva        1.0     FACS cell sorting data exported by BD FACSDiva
illumina-bcl-run      1.0     Illumina sequencer BCL run directory
monorepo-build        1.1     Monorepo workspace build output (supports both flat and per-package layouts)
photo-import          2.0     Photo import from a camera card into a media library
report-bundle         1.0     Validation-only report bundle (no detection; validate an assembled report)
site-archive          1.0     Static website archive (a self-contained snapshot directory)
web-build             1.0     Single-page web app build output (bundler build)
```

One line per schema: name, version, description. These ship inside the
package (`atlas.schemas` package data), so they're available in every install.

## 2. Inspect one schema's anatomy

```console
$ atlas show csv-dataset
name:        csv-dataset
version:     1.0
description: Tabular data export (a directory of CSV extracts)
detection.markers:
  - Exports
  - Logs
sync.include:
  - Exports/**/*
  - Stats/**/*
sync.exclude:
  - **/*.tmp
validate:
  required_dirs: ['Exports']
  warn_if_missing: ['Stats/summary.json']
  fail_on: ['_errors', '*.error']
  size: min 0.1 MB, max 100.0 GB
  file_count: min 1, max 500000
key_outputs:
  logs: Logs/
  summary: Stats/summary.json
  tables: Exports/*.csv
```

`NAME` may be a built-in name, a project/user schema name, or a path to a
`.yaml` file. The sections mirror the
[schema file anatomy](../reference/schemas.md): how it's detected, which
files travel on sync, what validation checks, and where the useful outputs
live.

## 3. Detect known data types

```console
$ atlas detect tests/fixtures/valid
csv-dataset	.	units: csv-dataset
monorepo-build	.	units: monorepo-build
photo-import	.	units: photo-import
site-archive	.	units: site-archive
web-build	.	units: web-build
```

Each line is `schema_name`, the stage path relative to the scanned root, and
the unit IDs found there. Two things worth noticing:

- `report-bundle` never appears — it has no `detection` block, so it is
  validation-only.
- Detection also discovers project-local (`./schemas/`) and user
  (`~/.atlas/schemas/`) schemas alongside the built-ins, so
  [a new data type needs only a new YAML](../guides/custom-schemas.md).

If nothing matches, atlas prints `No known data types detected.` — see the
[detection runbook](../runbooks/detection-empty.md) for diagnosis.

## 4. Validate a data unit

```console
$ atlas validate tests/fixtures/valid/csv-dataset --schema csv-dataset
OK: tests/fixtures/valid/csv-dataset is valid against 'csv-dataset'.
```

Now the broken fixture:

```console
$ atlas validate tests/fixtures/invalid/csv-dataset --schema csv-dataset
error: Pipeline failure marker detected: _errors matched pattern '_errors' — pipeline may not have completed successfully
  received: matched: _errors
  expected: no matches
  generated examples:
    - no path matching '_errors'
Error: tests/fixtures/invalid/csv-dataset failed validation against 'csv-dataset' (1 error(s)).
$ echo $?
1
```

Warnings print to stderr as `warning: …`, errors as `error: …`, and the exit
code is non-zero when validation fails — script-friendly by design. The
[validation runbook](../runbooks/validation-failures.md) explains how to read
each message.

## Where to next

- [Schemas & the data model](../concepts/schemas.md) — how detection and
  validation actually work.
- [Writing a custom schema](../guides/custom-schemas.md) — add your own data
  type with one YAML.
- [CLI reference](../reference/cli.md) — every command, option, and exit code.
