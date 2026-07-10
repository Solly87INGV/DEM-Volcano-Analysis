# GVP Holocene snapshot

Static, versioned snapshot of the Smithsonian Global Volcanism Program (GVP)
Holocene volcano list. Used by MorphoVolc 2.0 to resolve a vnum entered by the
operator into a primary volcano type, which in turn drives the
GVP-informed preset selection in `backend/scripts/volume_unified.py` (see
`gvp_profile_mapping.md` §2).

## Why a static snapshot

- Reproducibility is a first-class concern of the accompanying paper. A run
  performed today must yield the same preset resolution one year from now
  regardless of upstream GVP changes.
- GVP Holocene classifications change on a scale of years, not weeks.
- The full Holocene list is small (~1500 records), so a JSON snapshot fits
  well under 1 MB and loads into memory in milliseconds at server startup.
- Offline / air-gapped Docker deployments are supported by default.

## File layout

`gvp_holocene.json` is a single JSON object with two top-level keys:

- `_meta` — snapshot metadata (source URL, snapshot date, record count,
  version, notes).
- `records` — array of volcano records. Each record has:
  - `vnum` (string, GVP volcano number, always used as string to preserve
    leading zeros)
  - `name`
  - `primary_volcano_type` (raw GVP string, e.g. `"Shield"`, `"Caldera(s)"`,
    `"Stratovolcano"`)
  - `country`, `region`, `subregion`
  - `latitude`, `longitude`, `elevation_m`
  - `type_verified` (bool) — `true` when the classification has been
    cross-checked against the current GVP export; `false` for bootstrap
    entries that must still be verified.

## Current state

The file currently checked in is the **bootstrap snapshot** listing the 8
volcanoes of the initial benchmark
(Piton de la Fournaise, Erta Ale, Nyiragongo, Okmok, Chaitén, Banda Api,
Fentale, Valles). It exists purely to unblock the Sett 3-4 end-to-end tests.

Entries with `type_verified: false` are informed guesses based on
`gvp_profile_mapping.md` §4 and public knowledge of each edifice, and must
be verified against the real GVP export before being used in the paper
benchmark.

## Refreshing from the real GVP export

1. Download the latest Holocene volcano list from
   <https://volcano.si.edu/list_volcano_holocene.cfm> (Excel export).
2. Run the ingest script (from the backend/ directory):

   ```bash
   python scripts/gvp_snapshot_ingest.py \
       --input path/to/GVP_Volcano_List_Holocene.xlsx \
       --output data/gvp_holocene.json
   ```

3. Commit the updated `gvp_holocene.json` and update `_meta.snapshot_date`
   and `_meta.snapshot_version` (semver).
4. Re-run the benchmark: any change in `primary_volcano_type` for a
   benchmark volcano will surface in `metrics.json` under
   `meta.preset_selection` and must be reviewed.

## Integrity note

The mirror of the Python type→preset mapping lives in
`backend/gvp/client.js` (function `resolvePresetsForType`) and must be
kept in sync with `_GVP_TYPE_TO_PRESETS` and `_normalize_gvp_type` in
`volume_unified.py`. The client.js function is used **only** to return
a UI hint via `GET /api/gvp/:vnum`; the actual preset selection at
compute time is always performed by the Python code from the `GVP_TYPE`
env var.
