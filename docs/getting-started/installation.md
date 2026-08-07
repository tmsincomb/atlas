# Installation

atlas requires **Python 3.9+** and nothing else — no external binaries, no
credentials, no configuration files.

=== "pip"

    ```bash
    pip install atlas-manifest
    ```

    This installs the `atlas` Python package and the `atlas` console script.
    Runtime dependencies are `click`, `pydantic`, `pyyaml`, `pandas`, and
    `openpyxl` (for CSV/TSV/XLSX metadata manifests).

=== "With Sentry error tracking"

    ```bash
    pip install "atlas-manifest[sentry]"
    ```

    Adds `sentry-sdk`. Error tracking stays off until you set `SENTRY_DSN` —
    see [environment variables](../reference/environment.md#error-tracking).

=== "Development setup"

    ```bash
    make setup    # pip install -e ".[dev]" + pre-commit install
    make test     # pytest
    make lint     # ruff + mypy
    ```

    See [CONTRIBUTING.md](https://github.com/tmsincomb/atlas/blob/main/CONTRIBUTING.md)
    for conventions, commit style, and PR flow.

## Verify the install

```console
$ atlas --version
atlas, version 0.1.1

$ atlas schemas
10x-bcl-demux         1.0     BCL demultiplexing output (bcl2fastq / bcl-convert)
10x-cellranger-count  1.0     10x Genomics Cell Ranger count pipeline output
10x-cellranger-multi  1.1     10x Genomics Cell Ranger multi pipeline output (supports both pre-v8 and v8+ layouts)
csv-dataset           1.0     Tabular data export (a directory of CSV extracts)
facs-sort             2.5     FACS cell sorting data from BD FACSMelody
facs-sort-diva        1.0     FACS cell sorting data exported by BD FACSDiva
illumina-bcl-run      1.0     Illumina sequencer BCL run directory
monorepo-build        1.1     Monorepo workspace build output (supports both flat and per-package layouts)
photo-import          2.0     Photo import from a camera card into a media library
report-bundle         1.0     Validation-only report bundle (no detection; validate an assembled report)
site-archive          1.0     Static website archive (a self-contained snapshot directory)
web-build             1.0     Single-page web app build output (bundler build)
```

If both commands print, you're ready.

Next: the [quick start](quickstart.md) runs all four commands against the
repo's own test fixtures.
