# Runbook: schema not found

## Symptom

`atlas show` or `atlas validate` aborts with a click error like:

```
Error: Schema 'sample-run' not found. Searched: project (…/schemas/sample-run.yaml|yml), user (~/.atlas/schemas/sample-run.yaml|yml), built-in package data
```

or, when you pass an explicit path:

```
Error: Schema file not found: /abs/path/to/custom.yaml
```

Both come from `resolve_schema` in `src/atlas/schema.py`, surfaced as a
`click.ClickException`.

## Diagnosis

Resolution order is: explicit path → `{project_root}/schemas/{name}.yaml` or
`{project_root}/schemas/{name}.yml` → `~/.atlas/schemas/{name}.yaml` or
`~/.atlas/schemas/{name}.yml` → built-in package data. `project_root` is the
current working directory for the CLI, so where you run the command matters.

1. Confirm the name is spelled like a built-in:

   ```bash
   atlas schemas
   ```

2. If you expected a project-local schema, check you are in the project root
   and the file exists at the searched path:

   ```bash
   ls schemas/sample-run.yaml
   ```

3. If you passed a path, note the rule: a value ending in `.yaml`/`.yml` (or an
   absolute path) is treated as a file. A **relative** path resolves against
   `project_root`, not an arbitrary CWD subdir — so `--schema ./sub/x.yaml`
   looks under the project root's `sub/`.

4. A path that is well-formed but missing yields `Schema file not found:`,
   while a bare name that matches nowhere yields the `Schema '…' not found.
   Searched: …` message. The message tells you exactly which paths were tried.

5. If the file exists but errors, the message will instead be
   `Invalid YAML in …`, `Schema file is empty: …`, or `Invalid schema in …` —
   that is a content problem, not a resolution one.

## Resolution

- Use a name that `atlas schemas` lists, or drop your YAML at one of the
  searched locations: `{project_root}/schemas/{name}.yaml` or
  `{project_root}/schemas/{name}.yml` (project override), or
  `~/.atlas/schemas/{name}.yaml` or `~/.atlas/schemas/{name}.yml` (user-wide).
- Or point directly at the file: `atlas show ./path/to/schema.yaml`.
- Run the command from the intended project root so `project_root` is correct.
