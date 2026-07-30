# 1. Atlas is standalone (no forest dependency)

Date: 2026-07-01

## Status

Accepted

The dependency-list portion was amended by [ADR-0003](0003-pandas-manifests.md);
the standalone/no-forest boundary remains in force.

## Context

Atlas was extracted from biostore alongside forest (see forest ADR-0014). Atlas
owns the schemas (the backbones that define what a valid data tree/stage looks
like), validation, and on-disk detection. For atlas to be independently useful —
importable and testable without the data-management engine, and reusable by
tools other than forest — the dependency must point one way only.

## Decision

Atlas depends on nothing from forest. At the time of this decision its
third-party dependencies were `pydantic`, `pyyaml`, and `click` (for the CLI).
The public surface was:

- `Schema`, `SchemaError`, and the config models
- `load_schema`, `load_all_schemas`, `resolve_schema`
- `get_sync_files`, `resolve_key_output`
- `validate_data_unit`, `ValidationResult`
- `detect`, `Detection`, `extract_unit_ids`
- a thin CLI: `atlas schemas | show | detect | validate | tui`

These APIs are available to forest or any other consumer; atlas imports
nothing back. Forest's current main branch is self-contained and does not
import atlas, so this is an available integration boundary rather than a live
runtime dependency. A test
(`tests/test_standalone.py`) asserts that importing `atlas` pulls in no
`forest` module, in-process and via a fresh subprocess.

## Consequences

- Atlas can be installed and exercised on its own; its test suite passes with
  forest absent.
- Domain knowledge (on-disk layouts, detection markers, validation rules) is
  concentrated here, so adding a data type is an atlas-only change.
- Schema resolution order is atlas's: explicit path → `{project_root}/schemas/`
  → `~/.atlas/schemas/` → built-in package data.
