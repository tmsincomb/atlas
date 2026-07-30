# Design decisions (ADRs)

atlas's architecture decisions are recorded as Architecture Decision Records,
committed in `docs/adr/` next to the code they govern. Each ADR states its
status, the context that forced a decision, the decision itself, and its
consequences.

- **[1. Atlas is standalone (no forest dependency)](0001-atlas-is-standalone.md)**
  — the dependency points one way: other tools may import atlas; atlas
  imports nothing back. Enforced by `tests/test_standalone.py`.
- **[2. Textual TUI as an optional extra](0002-textual-tui-as-optional-extra.md)**
  — `atlas tui` ships behind `pip install 'atlas-manifest[tui]'`; the survey core
  never imports textual/rich, keeping ADR-0001's dependency surface intact.
- **[3. Pandas DataFrames are the manifest interchange](0003-pandas-manifests.md)**
  — schema-driven path metadata is exposed through pandas; openpyxl supports
  Excel metadata tables while Atlas remains independent of forest.

New ADRs are numbered sequentially (`0004-…`) and follow the same
Status / Context / Decision / Consequences format.
