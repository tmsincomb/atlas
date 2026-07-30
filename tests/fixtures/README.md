# Schema fixture trees

Committed, real on-disk folder trees for verifying six generic built-in schema
templates in `src/atlas/schemas/`. Each listed schema has one tree under `valid/`
and one under `invalid/`; `tests/test_fixture_trees.py` runs atlas's real
`validate_data_unit` and `detect` against them. Because the trees are real
(including full-size filler payloads to satisfy each schema's `min_size_mb`
floor), they also work standalone:

```sh
atlas validate tests/fixtures/valid/csv-dataset --schema csv-dataset   # OK
atlas validate tests/fixtures/invalid/csv-dataset --schema csv-dataset # error
atlas detect tests/fixtures/valid                                      # 5 units
```

Trees were generated deterministically; filler files (`orders.csv`,
`bundle.js`, `app.js`, `payload.bin`, image/PDF payloads) are repeated-content
padding whose only job is to clear the schema's size floor.

## `valid/` — pass validation with zero errors and zero warnings

| Fixture | What it proves |
| --- | --- |
| `csv-dataset` | Directory landmark + scoped `require_any_glob` detection; `Stats/summary.json` satisfies the warn item. |
| `monorepo-build` | Dir landmark (`.workspace-stamp`) + all-markers detection; per-package **and** flat layouts prove the alternatives can coexist; `_buildmeta` records `bundler workspace`, proving web-build's cmdline guard suppresses cross-detection. |
| `photo-import` | `landmark_parent: MediaLibrary` + `unit_depth: 2`; every filename matches the case-insensitive `filename_pattern`, so no warnings. |
| `report-bundle` | Validation-only schema (no `detection`/`sync` blocks) — never detected, validated explicitly. |
| `site-archive` | File landmark (`archive.json`) + `unit_is_directory_stage`; `assets/payload.bin` clears the 10 MB size floor. |
| `web-build` | Nested-path file landmark (`dist/app.js`, `unit_depth: 2`); `_buildmeta` records `bundler build`, so the `workspace` cmdline guard does not fire. |

## `invalid/` — each fails a *different* validation check

| Fixture | Deliberate defect | Expected error |
| --- | --- | --- |
| `csv-dataset` | Top-level `_errors` file | `fail_on` literal marker |
| `monorepo-build` | Flat layout plus top-level `_errors` marker | `fail_on` literal marker |
| `photo-import` | `RawPhotos/` holds only a sync-excluded `.tmp` file (also not detected: no `*.jpg`) | File count and size below minimum |
| `report-bundle` | No `report.pdf` | Missing required file |
| `site-archive` | Structurally complete but ~1 MB of content | Below `min_size_mb: 10.0` |
| `web-build` | Top-level `build.error` file | `fail_on` wildcard (`*.error`) |

Together the invalid trees exercise every error-producing check in
`src/atlas/validate.py`: required files, required dirs, both `fail_on`
pattern styles, `min_size_mb`, and `min_file_count`.

Note: this directory carries its own `.gitignore` because the repo root
ignores `dist/` (build output) at any depth, which would otherwise swallow
the web-build and monorepo-build fixtures.
