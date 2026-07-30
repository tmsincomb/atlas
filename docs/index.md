# atlas

**schema-driven cartography for project data — detect, validate, locate.**

atlas maps data layouts. It owns the *schemas*, compact YAML specs that define
what a valid data unit or stage looks like on disk, then uses them to
**detect**, **validate**, and **locate outputs** in project data. A schema
answers four questions about one data layout:

- **What does it look like?** Detection markers that identify it on disk.
- **What is valid?** Required files/dirs, size and file-count bounds, failure markers.
- **Where are useful outputs?** Named `key_outputs` paths.
- **What metadata is encoded in paths?** Manifest record and table rules.

<div class="atl-hero">
<div class="atl-terminal">
  <div class="atl-terminal-bar"><span></span><span></span><span></span><em>~/data</em></div>
  <pre><code><span class="atl-cmd">$ atlas schemas</span>                                   <span class="atl-dim"># list the built-ins</span>
<span class="atl-cmd">$ atlas show csv-dataset</span>                          <span class="atl-dim"># one schema's anatomy</span>
<span class="atl-cmd">$ atlas detect .</span>                                  <span class="atl-dim"># find known data types</span>
<span class="atl-cmd">$ atlas validate ./export --schema csv-dataset</span>    <span class="atl-dim"># check a data unit</span>
<span class="atl-ok">OK: export is valid against 'csv-dataset'.</span></code></pre>
</div>
</div>

atlas is **standalone**. It uses `click`, `pydantic`, `pyyaml`, `pandas`, and
`openpyxl`,
and it does **not** depend on any data-management engine. Other tools may call
atlas as a library; atlas never calls them.

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Up and running in minutes**

    ---

    Install from a source checkout and run the whole CLI — four read-only
    commands — against the repo's own test fixtures.

    [:octicons-arrow-right-24: Quick start](getting-started/quickstart.md)

-   :material-map:{ .lg .middle } **Schemas & the data model**

    ---

    One YAML per data layout: how detection seeds on a landmark, hops to the
    unit directory, and confirms markers before validation runs.

    [:octicons-arrow-right-24: Concepts](concepts/schemas.md)

-   :material-file-document-outline:{ .lg .middle } **Every schema field**

    ---

    The full YAML anatomy — `detection`, `sync`, `validate`, `key_outputs`, `manifest` —
    plus a table of all twelve built-in schemas.

    [:octicons-arrow-right-24: Schema file reference](reference/schemas.md)

-   :material-console:{ .lg .middle } **Reference, when you need it**

    ---

    Every command with its output format and exit codes, the Python API,
    and all environment variables in one place.

    [:octicons-arrow-right-24: CLI reference](reference/cli.md)

</div>

## Why atlas?

- **One YAML, zero Python.** Everything a data type needs lives in its single
  schema file, and every field is enforced — adding a new data type needs only
  a new YAML, no code change.
- **Standalone by design.** The dependency points one way: other tools import
  atlas; atlas imports nothing back. See
  [ADR-0001](adr/0001-atlas-is-standalone.md).
- **Overridable resolution.** Schemas resolve explicit path → project-local
  `./schemas/` → user-wide `~/.atlas/schemas/` → built-in, so a project or
  user schema overrides a built-in of the same name.
- **Quiet by default.** Logging, metrics, analytics, and error tracking are
  all opt-in via [environment variables](reference/environment.md); atlas
  sends nothing anywhere unless configured.

<figure markdown="span">
  ![Animated atlas hero: a parchment schema sheet projects a holographic directory tree; a scan sweep detects data units, checkmarks validate them, and gold pins locate key outputs](assets/hero.svg){ width="70%" }
</figure>

!!! tip "Bring your own data type"
    A schema with a `detection` block is picked up automatically by
    `atlas detect` — no registration step, no code change.
    [Write a custom schema →](guides/custom-schemas.md)
