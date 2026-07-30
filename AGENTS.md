# AGENTS.md

atlas is a standalone Python CLI/library that maps data layouts: compact YAML
*schemas* describe a data unit, and atlas uses them to detect, validate, and
locate outputs in data trees, and extract path metadata as pandas manifests. It
depends on `click`, `pydantic`, `pyyaml`, `pandas`, and `openpyxl`;
the optional `[tui]` extra adds `textual`/`rich` for `atlas tui` (ADR-0002).

## Repo layout

- `src/atlas/` — package source:
  - `cli.py` — click group `main`, the five commands below.
  - `schema.py` — `Schema` models, loaders, `resolve_schema`, `SchemaError`.
  - `detect.py` — on-disk detection by schema markers.
  - `validate.py` — `validate_data_unit`, `ValidationResult`, `RuleResult`.
  - `survey.py` — schema/validation survey gathering + static text rendering
    (the renderer-agnostic core of `atlas tui`; never imports textual/rich).
  - `tui_app.py` — Textual app for interactive `atlas tui` (needs the `[tui]` extra).
  - `log.py` — JSON structured logging (`run_id`, `RedactionFilter`).
  - `flags.py` — env-driven feature flags (`ATLAS_FLAG_<NAME>`).
  - `metrics.py` — per-run counters emitted as one JSON log line.
  - `analytics.py` — opt-in local usage events (JSONL).
  - `track.py` — optional Sentry error tracking (`SENTRY_DSN`).
  - `manifest.py` — `AtlasManifest`, file records, DataFrames, table enrichment.
  - `schemas/` — packaged built-in schema YAML files.
- `tests/` — pytest suite (`test_cli_atlas.py`, `test_detect.py`, `test_schema.py`,
  `test_standalone.py`, `test_validate.py`, `test_manifest.py`, `test_survey.py`, `test_tui_app.py`,
  `test_cli_tui.py`, `test_docs.py`).
- `docs/` — MkDocs site content (`mkdocs.yml` at the repo root, Material theme).
  `docs/adr/` — architecture decision records. `docs/runbooks/` — troubleshooting.

## Setup, test, lint, types

```bash
make setup            # fresh clone -> dev env (pip install -e ".[dev]", pre-commit)
make test             # python3 -m pytest -q
make lint             # ruff check . && ruff format --check . && mypy src/atlas (strict)
make docs             # uv run --group docs mkdocs build --strict
make docs-serve       # uv run --group docs mkdocs serve (local preview)
```

## CLI commands

```bash
atlas schemas                       # list built-in schemas: name, version, description
atlas show NAME [--yaml]            # inspect a schema; --yaml emits complete copyable YAML
atlas detect PATH                   # detect known data types under PATH, with unit IDs
atlas validate PATH --schema NAME   # validate a data unit directory; non-zero exit on failure
atlas tui [PATH] [--schema NAME]    # interactive schema survey (viewer; needs the [tui] extra)
```

## Conventions

- Strict `mypy` on `src/atlas`.
- Naming: PEP 8, enforced via ruff `N` (pep8-naming) rules.
- Complexity budget: mccabe `C901 <= 10`.
- Format: ruff, line-length 120, double quotes.
- TODOs must reference an issue: `TODO(#123): ...`.

## More

- Contributing workflow and commit/PR expectations: [CONTRIBUTING.md](CONTRIBUTING.md).
- Security policy and secrets handling: [SECURITY.md](SECURITY.md).
- Troubleshooting: [docs/runbooks/](docs/runbooks/).

`tests/test_docs.py` fails CI if this file drifts from the CLI, so keep the
`schemas`, `show`, `detect`, `validate`, and `tui` commands documented here.
