# 3. Pandas DataFrames are the manifest interchange

Date: 2026-07-15

## Status

Accepted

## Context

Biological identifiers, visits, pools, and technical replicates are often
encoded in folder and file names rather than file contents. Atlas schemas
already own those layout conventions, but callers previously had to duplicate
filename parsing before joining sorting and sequencing data. Multiplexed 10x
outputs also need enrichment from CSV or Excel sample manifests.

## Decision

Schemas may declare `manifest.records` for regex-based path extraction and
`manifest.tables` for CSV, TSV, or XLSX enrichment. `AtlasManifest` exposes a
single-file `ManifestRecord` and a pandas DataFrame for directory scans.
Pandas and openpyxl are required runtime dependencies. One-to-many enrichment
emits one row per source file and matched metadata entity.

This amends ADR-0001's original three-package dependency list but does not
change its architectural boundary: Atlas still imports nothing from forest or
another data-management engine.

## Consequences

- FACS and Cell Ranger manifests use ordinary pandas joins and integrate with
  the scientific Python ecosystem.
- CSV, TSV, and XLSX metadata sources share one declarative schema model.
- Minimal installations are larger because pandas, NumPy, and openpyxl are now
  runtime requirements.
- Partial path parses remain auditable through `parse_status` and
  `parse_error`; strict callers can fail immediately.
