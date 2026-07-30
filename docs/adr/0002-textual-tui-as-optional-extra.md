# 2. Textual TUI as an optional extra

Date: 2026-07-04

## Status

Accepted

## Context

`atlas validate` reports failures as flat `error:`/`warning:` lines, which is
right for scripting but a poor way to *learn* a schema or debug an input: the
reader has to reconstruct which rule produced each message and what the schema
expected overall. Forest rebuilt its TUI on Textual (forest ADR-0021) and the
pattern proved out — a renderer-agnostic gather/render core plus a thin
Textual shell, testable headlessly via Pilot.

At the same time, ADR-0001 commits atlas to a minimal dependency surface
because other tools import atlas as a library; the core dependency list is
maintained by ADR-0001 and ADR-0003. Therefore,
pulling Textual's dependency tree into every consumer is not acceptable.

## Decision

Add an interactive schema survey, `atlas tui`, structured so the core never
needs Textual:

- `validate.py` emits structured `RuleResult` checks (rule id, ok/warn/fail
  severity, expected, actual) for every declared rule — passing rules
  included. The legacy `errors`/`warnings` string lists are derived from the
  checks, byte-for-byte identical to before (locked by a parity test), so
  library consumers are unaffected.
- `survey.py` gathers schema anatomy + validation results into models and
  renders a plain-text report. It imports neither `textual` nor `rich`
  (asserted by `tests/test_survey.py`).
- `tui_app.py` is the only module importing Textual, and only the `tui`
  command imports it — deferred, so every other command's import path stays
  lean.
- Textual ships in an **optional extra**: `pip install 'atlas-manifest[tui]'`
  (`textual>=8` keeps the Python 3.9 floor). Without the extra, `atlas tui`
  still works in `--once`/non-tty static mode; interactive mode exits with an
  install hint.

`atlas tui` is a viewer, not a gate: `--once` exits 0 even for failing units.
`atlas validate` remains the scriptable pass/fail contract.

## Consequences

- Library consumers see no new dependencies; ADR-0001's surface is intact.
- The same survey renders identically piped and on screen, so the static
  output is testable with `CliRunner` and the app with Pilot.
- `RuleResult.rule_id` is now a public join key between schema rules and
  validation findings; renaming rule families is a breaking change.
- The TUI validates every detected unit eagerly in directory mode; very large
  trees may want lazy per-unit validation later (forest does this for its
  stage drill-down).
