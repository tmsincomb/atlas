# Contributing to atlas

## Dev setup

```bash
make setup      # pip install -e ".[dev]" + pre-commit install
```

## Tests and lints

```bash
make test       # python3 -m pytest -q
make lint       # ruff check . && ruff format --check . && mypy src/atlas
make fmt        # ruff format . && ruff check --fix .
```

Run `make lint` and `make test` before pushing; CI runs the same commands.

## Code conventions

- **Naming**: PEP 8, enforced via ruff `pep8-naming` (`N`) rules.
- **Complexity**: mccabe budget of 10 (`C901`); split functions that exceed it.
- **Types**: strict `mypy` on `src/atlas` — no untyped defs, no implicit `Any`.
- **Format**: ruff, line-length 120, double quotes.
- **TODOs** must reference a tracking issue: `TODO(#123): short description`.
  A bare `TODO` is not allowed.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) —
`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, etc.
release-please consumes these to compute versions and generate the changelog,
so the type and any `!`/`BREAKING CHANGE:` markers matter.

## Pull requests

- Fill out the PR template.
- CI must be green (tests, ruff, mypy).
- pre-commit hooks must pass (they run on `make setup` install).
- Keep changes focused; update `AGENTS.md` if you touch the CLI surface
  (`tests/test_docs.py` enforces that they stay in sync).
