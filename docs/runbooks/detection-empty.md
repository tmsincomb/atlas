# Runbook: detection finds nothing

## Symptom

`atlas detect PATH` prints:

```
No known data types detected.
```

even though you expect a known layout under `PATH`.

## Diagnosis

`detect` (`src/atlas/detect.py`) runs each built-in detector over the tree.
A detector fires only when it finds its **landmark** via `rglob` and then ALL
of the schema's `detection.markers` exist under the computed unit directory
(`_check_all_markers` requires every marker to be present). Miss one marker and
the unit is skipped silently.

Detector landmarks and unit depth (how many `.parent` hops from the landmark to
the unit dir) for the built-in schemas:

- `10x-bcl-demux` — landmark `Reports`, depth 1; also requires `Logs` and at
  least one FASTQ.
- `10x-cellranger-count` — landmark `outs/filtered_feature_bc_matrix.h5`,
  depth 2; excluded when Cell Ranger multi markers are present.
- `10x-cellranger-multi` — landmark `outs/config.csv`, depth 2; accepts a
  v8+ per-sample output or the legacy flat matrix.
- `facs-sort` — landmark `DataFilesFromMelody`, depth 2, directly under
  `ClinicalSamples`; requires loose FCS data or a ZIP archive.
- `facs-sort-diva` — landmark `PopulationSummaryFilesFromDV`, depth 2,
  directly under `ClinicalSamples`; requires a summary CSV.
- `illumina-bcl-run` — landmark `RunInfo.xml`, depth 1; requires the
  `Data/Intensities/BaseCalls` directory.
- `monorepo-build` — landmark `.workspace-stamp`, depth 1.
- `web-build` — landmark `dist/app.js`, depth 2, and skipped if the unit looks
  like a monorepo build (via `exclude_if_markers` / the `bundler workspace`
  cmdline guard).
- `photo-import` — landmark `RawPhotos`, depth 2; also requires the parent to be
  named `MediaLibrary` and at least one `*.jpg` sibling.
- `site-archive` — landmark `archive.json`, depth 1.
- `csv-dataset` — landmark `Exports`, depth 1; requires a `*.csv` somewhere
  under the parent.
- `report-bundle` — validation-only (no `detection` block), so it is never
  surfaced by `detect`; use it with `atlas validate ... --schema report-bundle`.

## Diagnosis steps

1. Confirm `PATH` is a directory — `detect` returns `[]` for a non-directory.

2. See which markers each schema wants:

   ```bash
   atlas show web-build --yaml   # prints the complete detection configuration
   ```

3. Check the landmark actually exists under the tree:

   ```bash
   find PATH -name archive.json
   find PATH -name app.js
   ```

4. Verify every marker from `atlas show` exists at the expected unit dir
   (landmark walked up by the depth above). A single missing marker means no
   match.

5. Watch the extra guards: `photo-import` needs the `MediaLibrary` parent name
   plus a `.jpg` file; `csv-dataset` needs a `.csv` under the parent;
   `web-build` is suppressed when monorepo-specific markers
   (`.workspace-stamp`, `dist/packages`) or a `bundler workspace` `_buildmeta`
   are present.

## Resolution

- Point `detect` at the directory that actually contains the landmark and its
  markers (often a parent of where you first ran it).
- If a real layout is missing an expected marker file, that is a data problem —
  restore/regenerate the marker.
- For a genuinely new layout, add a schema YAML with matching
  `detection.markers` (see the README "Adding a custom schema" section);
  detection is marker-driven, so no marker means no detection.
