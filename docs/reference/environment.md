# Environment variables

atlas requires no secrets or credentials. Every variable below is optional
and everything is **off (or quiet) by default** — atlas sends nothing
anywhere unless configured. The repo ships a commented template in
`.env.example` in the [Atlas repository](https://github.com/tmsincomb/atlas/blob/main/.env.example)
(`.env` files are gitignored).

| Variable | Default | Effect |
| --- | --- | --- |
| `ATLAS_LOG_LEVEL` | `WARNING` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `ATLAS_LOG_FORMAT` | `text` | `text` or `json`; JSON lines carry a per-process `run_id`. |
| `ATLAS_FLAG_<NAME>` | per-flag | Feature flags; truthy values are `1`/`true`/`yes`/`on` (case-insensitive). Known flags: `analytics` (default off). |
| `ATLAS_ANALYTICS_FILE` | `~/.atlas/analytics.jsonl` | Where opt-in usage events append (JSONL). |
| `SENTRY_DSN` | unset | Enables Sentry error tracking; requires `pip install "atlas-manifest[sentry]"`. |
| `SENTRY_ENVIRONMENT` | `local` | Environment tag attached to Sentry events. |

## Logging

Structured, stdlib-only logging in text or JSON. A per-process `run_id` ties
every line of one invocation together, and a redaction filter masks
secret-looking values (`token=`, `password:`, api keys, long hex/base64
runs) before they reach any handler.

```console
$ ATLAS_LOG_LEVEL=DEBUG ATLAS_LOG_FORMAT=json atlas detect .
```

## Metrics

Every CLI run emits exactly one metrics summary — a JSON payload logged at
INFO on the `atlas.metrics` logger with the command, duration, outcome, and
counters:

```console
$ ATLAS_LOG_LEVEL=INFO ATLAS_LOG_FORMAT=json atlas detect tests/fixtures/valid
...
{"timestamp": "...", "level": "INFO", "logger": "atlas.metrics", "message": "{\"event\": \"metrics\", \"command\": \"detect\", \"duration_ms\": 13.8, \"outcome\": \"success\", \"counters\": {\"detections\": 5}}", "run_id": "301d901716d644c48438874ec21d7769"}
```

At the default `WARNING` level the line is invisible — metrics cost nothing
unless you ask for them.

## Analytics

Opt-in, **local-only** usage events: one JSONL line per CLI run appended to
`ATLAS_ANALYTICS_FILE`. Nothing ever leaves the machine.

```console
$ ATLAS_FLAG_ANALYTICS=1 atlas schemas
$ tail -1 ~/.atlas/analytics.jsonl
```

## Error tracking

A no-op unless `SENTRY_DSN` is set **and** `sentry-sdk` is installed
(`pip install "atlas-manifest[sentry]"`). When active, events are tagged with the
release (`atlas@<version>`), the `run_id`, and `SENTRY_ENVIRONMENT`. If the
DSN is set but the SDK is missing, atlas logs a warning and continues.
