# Runbook: interpreting validation output

## Symptom

`atlas validate PATH --schema NAME` prints `warning:` and/or `error:` lines to
stderr and exits non-zero, e.g.:

```
warning: Optional file missing: reports/summary.pdf
  received: missing
  expected: reports/summary.pdf
  generated examples:
    - reports/summary.pdf
error: Missing required file: raw_data/manifest.csv
  received: missing
  expected: raw_data/manifest.csv
  generated examples:
    - raw_data/manifest.csv
Error: /data/run failed validation against 'sample-run' (1 error(s)).
```

A passing run instead prints `OK: /data/run is valid against 'sample-run'.`
and exits 0.

## Diagnosis

Warnings and errors both go to stderr (`warning: …`, `error: …` from
`cli.py`). Only **errors** make validation fail; warnings never change the exit
code. `validate_data_unit` in `src/atlas/validate.py` collects all issues
(not fail-fast). Common error strings and what they mean:

The indented `received`, `expected`, and `generated examples` lines are
diagnostic additions to the stable summary string. Generated examples are
best-effort values derived from the active schema constraint. Regex examples
are accepted only after Atlas checks them with the same compiled expression
used for validation.

- `Missing required file: <path>` — a `validate.required` entry does not exist
  under the unit dir.
- `Missing required directory: <path>` — a `validate.required_dirs` entry.
- `Missing required alternative (expected at least one of: …)` — none of the
  `validate.required_any` file globs matched.
- `Pipeline failure marker detected: … matched pattern '…' — pipeline may not
  have completed successfully` — a `validate.fail_on` glob matched (e.g.
  `_errors`, `failed`).
- `Total size … MB is below minimum of …` / `Total size … GB exceeds maximum of
  …` — size bounds on the post-sync file list.
- `File count … is below minimum of …` / `File count … exceeds maximum of …`.

Warning-only strings (do not fail the run):

- `Optional file missing: <path>` — a `warn_if_missing` entry.
- `Filename '…' does not match expected pattern '…'` — a `filename_pattern`
  non-match.

Size and file-count checks run against `get_sync_files`, i.e. the
post-include/exclude list, not the raw tree.

## Diagnosis steps

1. Inspect the rules the schema actually enforces:

   ```bash
   atlas show sample-run
   ```

2. Re-run and separate errors from warnings:

   ```bash
   atlas validate /data/run --schema sample-run
   echo "exit: $?"
   ```

3. For a `Pipeline failure marker detected` error, the message names the
   matched files and the pattern — check whether the pipeline really failed or
   the `fail_on` glob is too broad.

4. For size/count errors, remember exclude wins over include; files filtered
   out by `sync.exclude` do not count toward totals.

## Resolution

- Fix the underlying data (add the required file/dir, remove the failure
  marker) — that is the intended path.
- If a warning is noise, adjust `warn_if_missing` / `filename_pattern` in the
  schema; warnings alone never fail CI.
- Exit code is what scripts should branch on: non-zero means at least one
  `error:` line, and the final message reports the error count.
