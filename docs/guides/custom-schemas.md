# Writing a custom schema

Adding a new data type to atlas is "one YAML, zero Python": write a schema
file, drop it in a searched location, and every command — including
`atlas detect` — picks it up with no code change.

## 1. Write the YAML

Only `name` is required; every other field has a sensible default. A small
but complete example:

```yaml
name: sample-run
version: "1.0"
description: Generic processing run with raw data and reports

detection:
  landmark: "raw_data/manifest.csv"   # rglob seed
  landmark_type: file
  unit_depth: 2                       # manifest.csv -> raw_data/ -> the unit dir
  markers:
    - "raw_data/manifest.csv"
  require_any_glob:
    - "raw_data/*.csv"

sync:
  include:
    - "raw_data/**/*"
    - "reports/**/*"
  exclude:
    - "**/*.tmp"

validate:
  required:
    - "raw_data/manifest.csv"
  required_dirs:
    - "raw_data"
  warn_if_missing:
    - "reports/summary.pdf"
  fail_on:
    - "_errors"
  min_file_count: 1

key_outputs:
  summary_report: "reports/summary.pdf"
  output_tables: "outputs/*.csv"
```

Every field is documented in the
[schema file reference](../reference/schemas.md). Unknown or duplicate keys,
unsafe unit-relative paths, malformed globs, and incoherent bounds are
rejected when the file loads.

The twelve built-ins are also copyable starting points. Canonical YAML contains
the full resolved behavior while removing default-valued noise:

```console
$ mkdir -p schemas
$ atlas show csv-dataset --yaml > schemas/sample-run.yaml
# Edit `name` and adapt the exported paths and rules before using it.
```

## 2. Drop it in a searched location

- `{project_root}/schemas/sample-run.yaml` — project-local, and it
  **overrides** a built-in of the same name;
- `~/.atlas/schemas/sample-run.yaml` — available to all your projects.

## 3. Use it by name

```console
$ atlas show sample-run --yaml
$ atlas validate /data/project/generic-run --schema sample-run
```

```python
schema = atlas.resolve_schema("sample-run", project_root="/data/project")
```

Or point directly at the file without installing it anywhere:

```console
$ atlas validate /data/project/generic-run --schema ./path/to/sample-run.yaml
```

!!! warning "Relative schema paths"
    A relative `.yaml`/`.yml` path resolves against **`project_root`**, not
    your current working directory. Pass an absolute path when in doubt.

## Make it detectable

A schema with a `detection` block (specifically, a `landmark`) is picked up
automatically by `atlas detect` — project and user schemas are discovered
alongside the built-ins on every run:

```console
$ atlas detect /data/project
sample-run	runs	units: run-001, run-002
```

Omit `detection` (like the built-in `report-bundle`) for a validation-only
schema you invoke explicitly with `--schema`.

## Checklist before shipping

- [ ] `atlas show sample-run --yaml` prints the complete schema you expect.
- [ ] `atlas detect <root>` finds your stage — if not, work through the
      [detection runbook](../runbooks/detection-empty.md) (landmark, type,
      depth, markers, guards — in that order).
- [ ] `atlas validate <unit> --schema sample-run` passes on a known-good
      unit and fails on a known-bad one
      ([validation runbook](../runbooks/validation-failures.md)).
