# CLI commands

```console
$ atlas --help
Usage: atlas [OPTIONS] COMMAND [ARGS]...

  Map and validate data trees against atlas schemas.

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.

Commands:
  detect    Report which schema(s) match data under PATH, with unit IDs.
  schemas   List the built-in schemas.
  show      Show a schema's anatomy, including metadata manifest rules.
  tui       Interactive schema survey: what a schema expects, and what the...
  validate  Validate a data unit directory against a schema.
```

| Command | Purpose |
| --- | --- |
| [`atlas schemas`](#atlas-schemas) | List the built-in schemas. |
| [`atlas show NAME [--yaml]`](#atlas-show) | Inspect a schema or export copyable YAML. |
| [`atlas detect PATH`](#atlas-detect) | Find known data types under a directory. |
| [`atlas validate PATH --schema NAME`](#atlas-validate) | Validate a data unit against a schema. |
| [`atlas tui [PATH] [--schema NAME]`](#atlas-tui) | Interactive survey of a schema and its findings. |

All five commands are read-only. Every run also emits one metrics summary as
a JSON log line on the `atlas.metrics` logger (visible with
`ATLAS_LOG_LEVEL=INFO`) — see
[environment variables](environment.md#metrics).

## atlas schemas

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

One aligned row per packaged schema: name, version, description, sorted by
name. Prints `No built-in schemas found.` if the package data is missing.

## atlas show

```console
$ atlas show NAME [--yaml]
```

`NAME` may be:

- a **built-in** name (`csv-dataset`),
- a **project or user schema** name (resolved from `./schemas/` or
  `~/.atlas/schemas/`), or
- a **path** to a `.yaml`/`.yml` file.

Without options, prints the existing compact human summary: header (`name`,
`version`, `description`), `detection.markers`, `sync.include`/
`sync.exclude`, the `validate` rules, `key_outputs`, and compact `manifest`
record/table summaries when declared.

`--yaml` instead prints the complete resolved schema as normalized YAML. It
includes detection fields hidden by the compact view (such as landmark,
depth, type, and guards), preserves the public `validate` key, and omits
empty/default noise. The result can be checked by loading it again or used as
a starting template:

```console
$ mkdir -p schemas
$ atlas show web-build --yaml > schemas/my-web-build.yaml
# Edit `name` and the paths/rules, then inspect the result:
$ atlas show schemas/my-web-build.yaml --yaml
```

An unknown name exits non-zero with `Error: …` — see the
[schema-not-found runbook](../runbooks/schema-not-found.md).

## atlas detect

```console
$ atlas detect PATH
```

`PATH` must be an existing directory. Every detector runs over `PATH`;
matches print one line per stage:

```console
$ atlas detect tests/fixtures/valid
csv-dataset	.	units: csv-dataset
monorepo-build	.	units: monorepo-build
photo-import	.	units: photo-import
site-archive	.	units: site-archive
web-build	.	units: web-build
```

Fields are tab-separated: `schema_name`, the stage path relative to `PATH`,
and `units: <comma-separated unit IDs>` (`units: (none)` when the stage has
no unit subdirectories).

Detection runs against the built-in schemas **plus** any project-local
(`./schemas/`) or user (`~/.atlas/schemas/`) schemas — a project schema
overrides a built-in of the same name. Schemas without a `detection` block
(e.g. `report-bundle`) never match.

Prints `No known data types detected.` when nothing matches — see the
[detection runbook](../runbooks/detection-empty.md).

## atlas validate

```console
$ atlas validate PATH --schema NAME
```

Validates the unit directory `PATH` against schema `NAME` (same resolution
as `atlas show`). `--schema` is required.

Output contract, designed for scripting:

- **Warnings** go to stderr as `warning: …` — they do not fail the run.
- **Errors** go to stderr as `error: …`. All checks run; nothing stops at
  the first failure.
- Every warning/error keeps its original summary line, followed by the value
  received, the schema expectation, and up to three generated valid examples.
  Examples are derived at runtime and are not stored in schema YAML.
- On success, stdout gets `OK: PATH is valid against 'NAME'.` and the exit
  code is `0`.
- On failure, the run exits non-zero with
  `Error: PATH failed validation against 'NAME' (N error(s)).`

```console
$ atlas validate tests/fixtures/invalid/csv-dataset --schema csv-dataset
error: Pipeline failure marker detected: _errors matched pattern '_errors' — pipeline may not have completed successfully
  received: matched: _errors
  expected: no matches
  generated examples:
    - no path matching '_errors'
Error: tests/fixtures/invalid/csv-dataset failed validation against 'csv-dataset' (1 error(s)).
```

Interpreting each message is covered in the
[validation runbook](../runbooks/validation-failures.md).

## atlas tui

```console
$ atlas tui [PATH] [--schema NAME] [--once]
```

An interactive split-pane survey of a schema and its validation findings —
a **viewer**, not a gate (for scripting use `atlas validate`). At least one
of `PATH` / `--schema` is required, giving three modes:

- `atlas tui --schema NAME` — **learn mode**: browse the schema's rule tree
  (detection, sync, validate, key_outputs) with an explanation of what each
  rule expects. No input data needed.
- `atlas tui PATH --schema NAME` — validate the unit directory `PATH` and
  mark every rule ✓ pass / ⚠ warn / ✗ fail, with expected-vs-actual detail
  for the highlighted rule.
- `atlas tui PATH` — detect units under `PATH` (as `atlas detect` would) and
  survey each against the schema that matched it.

Keys: ++up++/++down++ (or ++j++/++k++) walk the rule tree, ++enter++ opens a
drill-down table of the selected rule's findings (or a unit's sync-file
list), ++n++ jumps to the next failing rule, ++r++ re-validates from disk,
++q++ quits.

Interactive mode needs the optional TUI extra — `pip install 'atlas-manifest[tui]'` —
and a terminal. With `--once`, or whenever stdio is not a tty (pipes, CI),
the same survey renders as one static text report and exits `0` regardless
of findings; no extra is required on that path:

```console
$ atlas tui tests/fixtures/invalid/report-bundle --schema report-bundle --once
atlas survey — schema report-bundle v1.0, built-in
...
  validate:
    ✗ required file: report.pdf
        fail: Missing required file: report.pdf
    ✓ required dir: figures
```
