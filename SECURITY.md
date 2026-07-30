# Security Policy

## Supported versions

atlas is pre-1.0. Only the current `0.x` release line receives security fixes.

## Reporting a vulnerability

Please report privately via **GitHub Security Advisories**
("Report a vulnerability" on the repo's Security tab), which notifies
@tmsincomb. Do not open a public issue for security reports.

## Secrets policy

- **No credentials in the repo.** Do not commit tokens, passwords, keys, or
  connection strings.
- **Configuration is env-var only.** atlas reads its config from environment
  variables (e.g. `ATLAS_LOG_LEVEL`, `ATLAS_LOG_FORMAT`); no secrets live in
  tracked files.
- `.env` is gitignored. Commit a `.env.example` template with placeholder
  values instead of real ones.
- **Log redaction.** Structured logs pass through a `RedactionFilter`
  (`src/atlas/log.py`) that masks token/password/key-shaped values before they
  are emitted, so secrets do not leak into log output.
