# CLAUDE.md — MorphoVolc 2.0 project context

This file is the persistent context for Claude Code (and any other assistant
working on this repository). It is the **single source of truth** on the
project's shape, current state, working principles, and known constraints.
Read it fully before proposing any change.

---

## 1. What this project is

MorphoVolc 2.0 is a web application for morphometric and volumetric analysis
of volcanic edifices and calderas from DEMs. It has three components:

- **Backend**: Node.js/Express (`backend/server.js`) — routes, orchestration,
  spawns Python child processes with env vars, writes `meta.json` per run.
- **Computation core**: Python (`backend/scripts/volume_unified.py`,
  ~1800 lines, plus `complete_dem_analysis.py` for step 1) — DEM
  preprocessing, base contour, caldera rim detection, volume integration.
- **Frontend**: React (`frontend/src/...`) — DEM upload, scenario/preset
  selection, results viewer.

Runtime is a single Docker container on Windows. Files are moved into the
running container via `docker cp`. Python inside the container is
`/opt/venv/bin/python`.

## 2. Why this project exists

A scientific paper co-authored with **Federico Galetto** (volcanologist), on
a ~5-month timeline, targeting *Computers & Geosciences* or *Journal of
Volcanology and Geothermal Research*. **Reproducibility is a first-class
concern**: the same DEM + same inputs must yield the same output today,
tomorrow, and one year from now.

The core scientific contribution is:

1. **GVP-informed preset selection** — replacing the previous supervised-ML
   prior for base/rim detection with a rule-based mapping driven by the
   GVP primary volcano type.
2. **Quantitative benchmark** — against manual ground truth polylines drawn
   by Galetto on a set of ~8 benchmark volcanoes.
3. **Uncertainty quantification** — Monte Carlo perturbation of the rim
   polyline to produce confidence intervals on the reported volumes.

## 3. Working principles — READ CAREFULLY

These are non-negotiable and override any local instinct to move faster.

### 3.1 Diagnose before coding

Before proposing any change, verify the actual state of the relevant files
against what the plan/mapping documents assume. A version mismatch between
"documented state" and "on-disk state" has already caused one wasted session
in April 2026 (patched vs. pre-patch `volume_unified.py`). Never skip this.

Concretely: when a task involves modifying a file, `read` it fully first,
and cross-check the presence/absence of the constructs the plan expects.
If mismatched, **stop and surface the discrepancy** — do not "fix it
silently on the way".

### 3.2 Propose → confirm → execute

For non-trivial changes (anything beyond typo fixes, one-line log tweaks,
or trivial code motion), first state:

- What you understand needs to happen and why.
- Which files will be touched and how.
- Any design choice with more than one reasonable option — surface it,
  don't pick silently.

Then wait for confirmation. **Do not autonomously generate multi-file
patches** on architectural decisions. The user's methodology is deliberate
and structured; premature execution costs more than the round-trip.

Trivial changes (typos, log strings, comments, formatting inside one
function) can be done directly.

### 3.3 `volume_unified.py` is protected outside designated sprint windows

Per §7 of `gvp_profile_mapping_v2.md`, `volume_unified.py` **must not be
modified outside its designated sprint windows** in the paper plan. This
is architectural discipline, not superstition:

- The Python side is the authoritative source of preset selection logic.
- Changes there invalidate previously-computed benchmarks.
- The GVP↔preset mapping mirror in `backend/gvp/client.js` depends on
  staying in sync with this file — silent drift breaks the UI hint.

If a task seems to require touching `volume_unified.py` and the current
sprint doesn't authorize it (see §5 below), stop and raise it.

### 3.4 All architectural decisions must be traceable

Every non-trivial decision must be traceable to one of the governing
documents (see §4). If a proposed change has no such anchor, either the
document needs updating first, or the change should not happen.

### 3.5 Communication style

The user prefers dense, direct communication. Skip preambles that
paraphrase the request. Do not ask clarifying questions that could be
answered by looking at project files — go look. Prefer giving several
commands in one batch over sequentially one at a time.

Language of interaction: **Italian**.

## 4. Governing documents

Both live at repo root and are authoritative:

- **`morphovolc_paper_plan_v2.md`** — operational plan with numbered
  weekly sprints (Sett 1–14). Part C-bis records the advancement state
  and known empirical observations. Update it at sprint boundaries only.
- **`gvp_profile_mapping_v2.md`** — governs all architectural decisions
  on preset selection. §2 is the canonical GVP-type→preset mapping.
  §6bis is the retrocompatibility contract. §7 is the protection clause
  on `volume_unified.py`.

If these two documents disagree with each other or with the code, that
disagreement is a bug and must be surfaced, not glossed over.

## 5. Current state (as of July 2026)

### Sett 1–2 — COMPLETED

- Patched `volume_unified.py` with a three-preset system
  (`island`, `continental`, `shield`).
- Added `_GVP_TYPE_TO_PRESETS` mapping table and `_normalize_gvp_type`
  normalizer.
- Added `resolve_presets(base_profile_env, gvp_type)` which produces a
  `preset_selection` debug object, now written to `metrics.json` under
  `meta.preset_selection`.
- `GVP_TYPE` env var is consumed by the Python side.
- Root bug fixed: previously, `_normalize_base_profile` collapsed
  unknown inputs deterministically to `"continental"` with no
  DEM-derived adaptation.

### Sett 3–4 — COMPLETED (implementation staged, tests pending)

Deliverables produced:

- `backend/data/gvp_holocene.json` — bootstrap snapshot (8 benchmark
  volcanoes) with a `_meta` block and `type_verified` per record.
- `backend/data/README.md` — provenance and refresh procedure.
- `backend/gvp/client.js` — Node module: dataset loader,
  `resolveByVnum`, `searchByName`, and a **JS mirror of the Python
  preset mapping** (`normalizeGvpType`, `resolvePresetsForType`) used
  only for the UI hint. Includes a standalone smoke test
  (`node backend/gvp/client.js`).
- `backend/scripts/gvp_snapshot_ingest.py` — script to ingest the real
  GVP xlsx export into the JSON snapshot. Deterministic output.
- Patched `backend/server.js`:
  - Loads the GVP dataset at startup (path override via
    `GVP_DATASET_PATH` env var).
  - New endpoints: `GET /api/gvp/status`, `GET /api/gvp/search?q=`,
    `GET /api/gvp/:vnum`.
  - `/process` and `/calculateVolume` now accept an optional `vnum`
    form field, persist it in `meta.json`, and pass `GVP_TYPE` (raw
    GVP primary type string) in the env of the Python child.
  - Body `vnum` takes priority over `meta.json` on `/calculateVolume`
    (idempotency + override).
- Patched `frontend/src/UploadForm.js` — optional `vnum` numeric input +
  Verify button + resolved GVP info display (name, type, preset hint).
- Patched `frontend/src/VolumeSelection.js` — reads `meta.json` on
  mount, shows a read-only GVP context badge, forwards `vnum` in
  form-data (belt-and-suspenders).

**Not yet done**: docker deploy of the staged files, end-to-end test on
the 4 target volcanoes, update of the Sett 3-4 row in Parte C-bis.

### The 3 parked empirical issues (Sett 5–8)

1. **`min_area_frac` for shield edifices** — the current default
   excludes the real Erta Ale caldera because the ROI is very large
   relative to the caldera. Needs a preset-aware default or an override
   mechanism when `rim_preset == "shield"`.
2. **Nyiragongo DEM crop** — the input DEM crop is too tight for
   proper edifice-plus-context analysis. Must be re-cropped before it
   can be used as a benchmark case.
3. **Banda Api morphological ambiguity** — historic caldera vs. active
   summit crater. Requires an explicit definition agreement with
   Galetto before ground truth annotation.

Do not attempt to "fix" these outside Sett 5–8. Surface them if they
become blocking.

## 6. Immediate horizon (Sett 5–8)

- **Sett 5–6**: benchmark dataset construction and ground truth
  polyline annotation with Galetto. Resolve the Banda Api ambiguity.
  Fix the Nyiragongo crop. Address `min_area_frac` for shields.
- **Sett 7–8**: Monte Carlo uncertainty quantification on the rim
  polyline. This will require rapid iteration on `volume_unified.py`
  (authorized sprint window) and possibly a new orchestration path in
  `server.js` for batch runs.

## 7. Repository layout

Approximate — verify against the real tree before assuming paths:

```
<repo root>
├── CLAUDE.md                       ← this file
├── morphovolc_paper_plan_v2.md
├── gvp_profile_mapping_v2.md
├── backend/
│   ├── server.js
│   ├── data/
│   │   ├── gvp_holocene.json
│   │   └── README.md
│   ├── gvp/
│   │   └── client.js
│   └── scripts/
│       ├── complete_dem_analysis.py     ← step 1: DEM preprocessing
│       ├── volume_unified.py            ← step 2: base/rim/volume (PROTECTED)
│       └── gvp_snapshot_ingest.py
├── frontend/
│   └── src/
│       ├── UploadForm.js
│       ├── VolumeSelection.js
│       ├── RimMapModal.js
│       └── ...
├── outputs/                        ← per-run output (gitignored)
└── uploads/                        ← per-run input (gitignored)
```

## 8. Runtime — Docker workflow

Standard patterns the user runs (Windows host, container name varies):

```bash
# Substitute a file into the running container
docker cp <local_path> <container>:<container_path>

# Run Python inside the container
docker exec <container> /opt/venv/bin/python <script> [args...]

# Restart the backend after server.js changes
docker restart <container>

# Rebuild frontend after React changes (depends on your setup)
docker exec <container> sh -c "cd /app/frontend && npm run build"
```

The Python interpreter path inside the container is `/opt/venv/bin/python`,
**not** `python3`. Do not run `pip install` inside the container casually
— the venv is versioned.

## 9. Benchmark volcanoes

Current bootstrap set (see `backend/data/gvp_holocene.json`):

| vnum   | Name                    | GVP type       | Notes                                     |
| ------ | ----------------------- | -------------- | ----------------------------------------- |
| 233020 | Piton de la Fournaise   | Shield         | Primary target — verified                 |
| 221080 | Erta Ale                | Shield         | Primary target — verified, `min_area_frac` issue |
| 223030 | Nyiragongo              | Stratovolcano  | DEM crop issue, `type_verified: false`    |
| 311290 | Okmok                   | Caldera        | verified                                  |
| 358041 | Chaitén                 | Caldera        | Primary target — `type_verified: false`   |
| 268060 | Banda Api               | Caldera        | Primary target — morphological ambiguity, `type_verified: false` |
| 221270 | Fentale                 | Stratovolcano  | `type_verified: false`                    |
| 327010 | Valles                  | Caldera        | verified                                  |

The `type_verified: false` entries must be checked against the real GVP
export when the snapshot is refreshed (Sett 5).

## 10. Anti-patterns — what NOT to do

- **Do not modify `volume_unified.py` outside its authorized sprint
  window**, even for "harmless" refactors or logging.
- **Do not scrape** `volcano.si.edu` at runtime. The GVP static snapshot
  is a deliberate reproducibility choice (see `backend/data/README.md`).
- **Do not add ML models or heuristics** for preset selection. The whole
  point of Sett 1–2 was replacing ML with the rule-based GVP mapping.
- **Do not add UI features not in the plan.** MVP discipline. If a
  feature seems useful, propose it separately — do not sneak it in.
- **Do not silently update the JS↔Python mapping mirror on one side
  only.** Any change to `_GVP_TYPE_TO_PRESETS` requires updating both
  `volume_unified.py` and `backend/gvp/client.js` in the same commit,
  and re-running the client smoke test.
- **Do not commit `outputs/` or `uploads/` contents.**
- **Do not fabricate GVP data.** If a vnum is not in the snapshot,
  say so and stop; do not guess a volcano type.

## 11. First-session checklist for a new Claude Code session

When starting a fresh session:

1. Read this file end-to-end. Do not skim.
2. Read `morphovolc_paper_plan_v2.md` Parte C-bis for latest sprint state.
3. Read `gvp_profile_mapping_v2.md` §2, §6bis, §7 at minimum.
4. Verify the on-disk state of `volume_unified.py` matches what §5
   above claims (grep for `resolve_presets`, `_GVP_TYPE_TO_PRESETS`).
   If mismatched, stop and surface it.
5. Ask what sprint the current session should advance, or what
   specific task to tackle. Do not assume.

---

*Last updated: 2026-07-10 — end of Sett 3–4 staging, pre-deploy.*
*When the state of the project changes, update §5 first, then anything
else that follows from it.*
