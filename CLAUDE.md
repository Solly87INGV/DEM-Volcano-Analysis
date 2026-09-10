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

## 5. Current state (as of August 2026)

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

### Sett 3–4 — COMPLETED (deployed, end-to-end tested)

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
- Patched `frontend/src/components/UploadForm.js` — optional `vnum` numeric input +
  Verify button + resolved GVP info display (name, type, preset hint).
- Patched `frontend/src/components/VolumeSelection.js` — reads `meta.json` on
  mount, shows a read-only GVP context badge, forwards `vnum` in
  form-data (belt-and-suspenders).

Docker deploy done (2026-07-11, `docker compose build && up -d` — no
pre-existing container/image on the machine, full rebuild). End-to-end
test via real HTTP endpoints (not manual docker exec) run on the 4
target volcanoes: Piton, Erta Ale, Banda Api OK; Chaitén FAIL (see
issue 4 below). One-line additive hotfix applied to `volume_unified.py`
(`meta.preset_selection` was being silently dropped when
`metrics["meta"]` was rebuilt — see Parte C-bis for the full diff and
verification). Sett 3-4 row in Parte C-bis updated.

### The 4 parked empirical issues (Sett 5–8)

1. **[ARCHIVED — reserve, not an active benchmark case] `min_area_frac`
   for shield edifices** — the current default excludes the real Erta
   Ale caldera because the ROI is very large relative to the caldera.
   Needs a preset-aware default or an override mechanism when
   `rim_preset == "shield"`.
   **Status (confirmed 2026-08-03): Erta Ale (221080) is deliberately
   held outside the 12-volcano benchmark set** (`gvp_holocene.json`,
   refreshed 2026-07-21, see §9) as a reserve/edge case by design — not
   a dropped record. The empirical finding remains a valid observation
   (measured directly off the DEM/ROI in Sett 1–2) and stays relevant if
   Erta Ale is ever promoted into the active set, but it is no longer an
   active blocking issue for Sett 5–8 work on the 12-volcano benchmark.
2. **Nyiragongo DEM crop** — the input DEM crop is too tight for
   proper edifice-plus-context analysis. Must be re-cropped before it
   can be used as a benchmark case.
   (Unchanged — Nyiragongo, 223030, is still present in the current
   dataset, see §9.)
3. **[ARCHIVED — reserve, not an active benchmark case] Banda Api
   morphological ambiguity** — historic caldera vs. active summit
   crater. Requires an explicit definition agreement with Galetto
   before ground truth annotation.
   **Status (confirmed 2026-08-03): Banda Api (268060) was not selected
   as one of the three `island_complex` representatives in the
   12-volcano benchmark** (Ambrym, Karthala, Lewotolok cover that
   family instead, see §9) — a deliberate stratified-selection choice,
   not a dropped record. The morphological ambiguity remains a valid,
   documented observation (`gvp_profile_mapping_v2.md` §6bis) and stays
   relevant if Banda Api is ever added back, but it is no longer an
   active blocking issue for Sett 5–8 ground-truth annotation on the
   12-volcano benchmark.
4. **Chaitén DEM/preset failure** — the continental preset (resolved
   correctly from GVP type `Caldera`) raises `ValueError: No rim
   component meets constraints` on the current DEM. Never produced a
   valid `metrics.json`, not even under manual docker-exec testing in
   Sett 1–2 — surfaced only during the Sett 3–4 end-to-end HTTP test
   (2026-07-11). Untested hypotheses: DEM crop issue analogous to
   Nyiragongo, GVP-type/morphology mismatch, or continental preset
   parameters unsuited to this edifice. `type_verified: false` was true
   at the time this issue was opened; the current snapshot now marks
   Chaitén (358041) `type_verified: true`, so the GVP-type-mismatch
   hypothesis can likely be dropped, but the underlying `ValueError`
   itself has not been re-tested since.

Do not attempt to "fix" these outside Sett 5–8. Surface them if they
become blocking.

**[ARCHIVED — reserve, not an active benchmark case] Fentale (221270)**
— carried no numbered issue above, but was part of the old 8-volcano
bootstrap (`type_verified: false`). **Status (confirmed 2026-08-03):**
deliberately held outside the 12-volcano benchmark set as a
reserve/edge case by design, same as Erta Ale — not a dropped record.
No other observation is attached to it beyond this.

### Merge interfaccia unified GVP-driven (2026-08-03) — COMPLETED

Prima di questo merge esistevano due filoni di frontend mai unificati:
`UploadForm.js` con integrazione GVP (filone A, in uso) ma
`VolumeSelection.js` ancora con le due card manuali Island/Continental;
e una vecchia versione unified di `VolumeSelection.js` senza card ma
senza GVP (filone B, non in uso, mai deployata). Il disallineamento è
stato chiuso in questa sessione:

- **`backend/server.js`** (`/calculateVolume`): rimosso il gate 400 che
  richiedeva `baseProfile` esattamente `island`/`continental`. Introdotta
  `baseProfileEffective`: usa il valore fornito se è `island`/`continental`
  (retrocompatibilità con chiamanti legacy), altrimenti default deliberato
  `'continental'` — fallback conservativo, sempre sovrascritto da
  `GVP_TYPE` dentro `resolve_presets()` (Python) quando il vnum risolve.
  Propagata al posto di `baseProfile` in `deriveUnifiedModuleKey`,
  `writeProcessMeta`, env `BASE_PROFILE`, `patchVolumeResultsFile` e
  nella risposta JSON. `/process` e la logica GVP non toccate.
- **`frontend/src/components/VolumeSelection.js`**: rimosse le due
  card `CardSelection` (Island/Continental), lo stato `baseScenario`, i
  relativi handler (`handleScenarioSelect`, `handleReset`) e il
  reset-arrow. Non invia più `baseProfile` nel form-data di
  `/calculateVolume` — la scelta del preset è interamente delegata al
  GVP. Mantenuti intatti: badge GVP read-only, `useEffect` di lettura
  `meta.json`, propagazione `vnum`.
- **`frontend/src/components/UploadForm.js`**: nessuna modifica — era
  già nello stato corretto (campo vnum + Verify + badge).
- `volume_unified.py` **non toccato** (fuori sprint window, §3.3).

Risultato: l'utente inserisce solo il vnum; il GVP determina il preset
via `resolve_presets` (Python); un solo pulsante "Calculate Volume"
avvia il calcolo. Nessuna scelta manuale di card residua nell'interfaccia.
`frontend/src/components/CardSelection.js` resta su disco ma è ora
**orfano** (nessun import residuo in `frontend/src`) — non cancellato,
in attesa di decisione esplicita (MVP discipline, §10).

Non ancora verificato in questa sessione: rebuild Docker e test di
accettazione end-to-end (badge GVP visibile, nessuna card, un solo
pulsante, `metrics.json` con `meta.preset_selection.preset_source ==
"gvp_mapping"`). Da eseguire prima di avviare i 12 run di benchmark
(§6, §9).

### Apertura anticipata finestra `volume_unified.py` — Livello 1 robustezza rim detector (2026-08-04)

**Deroga esplicita a §3.3.** La finestra di modifica su `volume_unified.py`
era autorizzata solo per Sett 7-8 (Monte Carlo, §6). Aperta in anticipo il
2026-08-04 su autorizzazione esplicita dell'utente, non come hotfix mirato
a un singolo campo (schema Sett 3-4 / Ambrym) ma come intervento a scope
più ampio, perché il blocco è reale e non rimandabile: 9 dei 12 casi del
benchmark falliscono (diagnosi tecnica di Marco,
`MorphoVolc_diagnosi_detector.docx`, 4 agosto 2026) — 5 con crash fatale
(`raise ValueError` in `find_caldera_contour_morphological`: Ambrym,
Lewotolok, Aniakchak, Puyehue, Chaitén), 2 con volume nullo scritto
silenziosamente come `status: "completed"` per base contour degenere
(Valles, Long Valley — `select_base_contour`, fallback continental senza
validazione). Senza intervenire, Sett 5-6 (costruzione benchmark, ground
truth) non può procedere su 9/12 casi.

**Scope autorizzato: solo "Livello 1 — Robustezza" del piano di Marco.**
I 4 `raise` fatali in `find_caldera_contour_morphological` diventano
esito gestito (`partial: true` con confidenza, o fallimento onesto senza
inventare geometria) invece di crash; il fallback continental in
`select_base_contour` valida che la base non sia degenere (area minima,
non a ridosso del bordo del raster) e lo marca in `dbg` invece di
restituirla come valida senza controllo; `run_unified` aggrega tutto in
due campi **additivi**, `run_quality` (`"ok"`/`"degenerate"`) e
`run_issues`, scritti in `metrics.json` (`meta.run_quality/run_issues`) e
`volume_results.json`. **`status` resta sempre `"completed"`** quando il
run non crasha — non viene toccato — perché `VolumeResultsViewer.js`
(`canDownloadPdf`, `canOpenMap`) lo usa come gate stretto, e la
rifinitura manuale del rim via RimMapModal su Valles/Long Valley resta
parte del metodo (vedi HANDOFF, §4), non un ripiego da disabilitare.
Formula del volume (prismoid + depth-integrated) e preset
island/continental/shield: valori invariati.

**Fuori scope, esplicitamente non autorizzato ora:** Livello 2 (rim
tratteggiato per confidenza-segmento, si aggancia a RimMapModal) e
Livello 3 (analisi multi-criterio: curvatura + gradiente direzionale,
il salto metodologico per il paper) restano proposte del documento di
Marco da valutare con Galetto, non parte di questa deroga.

**Il Monte Carlo di Sett 7-8 resta un cantiere separato**, non
anticipato né sostituito da questo intervento: riguarda la
perturbazione della rim polyline per l'uncertainty quantification, non
la robustezza del detector. Nessuna sovrapposizione di scope.

Diff validato riga per riga con l'utente prima dell'applicazione:
conteggio diff sul file reale (148 righe aggiunte, 41 rimosse, netto
+107, 1831→1938 righe), 20 hunk tutti confinati a
`select_base_contour`, `find_caldera_contour_morphological`,
`run_unified`, `main()` — zero modifiche fuori scope. Verifica di
non-regressione sui 3 casi buoni (Fernandina 353010, Okmok 311290,
Nyiragongo 223030) pianificata prima di rilanciare i 9 casi falliti;
per Nyiragongo è atteso `run_quality: "degenerate"` (passa dal ramo
`continental_sweep_failed_fallback_longest`, A_base=4253 m², problema
di crop già noto e parcheggiato — parked issue #2) con numeri identici
a prima, non una regressione. Esito completo da riportare qui e in
`morphovolc_paper_plan_v2.md` Parte C-bis una volta applicato e
testato — non aprire nuovi file.

### Fix bordo-zero da reproject in `ensure_metric_dem` (2026-08-05)

**Stessa finestra Livello 1 già aperta il 2026-08-04** (§5 sopra), non
una nuova deroga: stesso tema robustezza detector, intervento distinto
dai 4 `raise` di Marco. Diagnosi (utente, confermata leggendo il codice
riga per riga prima di intervenire, §3.1): i DEM arrivano in ingresso
in EPSG:4326 con `nodata: null`. In `ensure_metric_dem`, il `reproject`
verso UTM usava `dst_nodata=src.nodata` (quindi `None`) — GDAL riempie
di conseguenza con `0` i pixel fuori dall'impronta ruotata della
sorgente, e quello `0` non viene mai dichiarato come nodata nel profilo
scritto su `dem_working.tif`. A valle, `main()` legge `nodata =
src.nodata` (resta `None`) e lo propaga a `select_base_contour`,
`find_caldera_contour_morphological`, `caldera_volume_depth_integrated`,
`height_p99_minus_p05_inside_base`: tutte e quattro trattano quegli
zeri come terreno reale, tirando centro-depressione/base/rim verso il
bordo del raster.

**Scope effettivo, verificato prima di intervenire**: le 4 funzioni a
valle hanno già la guardia `if nodata is not None: valid = valid &
(demf != float(nodata))` e ricevono già `nodata=nodata` dalle chiamate
in `main()`/`run_unified()` — non necessitavano modifica. Il fix è
risultato confinato a 3 righe dentro `ensure_metric_dem`: sentinella
esplicita `DST_NODATA = -9999.0`, `dst_nodata=DST_NODATA` nel
`reproject` (al posto di ereditare `src.nodata`), `nodata=DST_NODATA`
dichiarato nel profilo scritto su `dem_working.tif`. Nessun altro punto
del file toccato. Diff mostrato e confermato dall'utente prima
dell'applicazione (§3.2).

Non rientra in questo fix: il ramo per DEM già in CRS proiettato
(`return dem_path` prima del reproject) resta invariato — nessun rischio
per input non geografici.

**Non ancora verificato in questa sessione**: rebuild Docker e
confronto numerico sui 3 casi buoni (Fernandina 353010, Okmok 311290,
Nyiragongo 223030) — atteso invariato bit per bit per Fernandina/Okmok
(nessun bordo di zeri nel loro crop) e numeri identici per Nyiragongo
(il suo `run_quality: "degenerate"` resta legato al crop stretto,
problema distinto — parked issue #2 — non a questo fix). Rebuild ed
esecuzione dei run di verifica a carico dell'utente da terminale
(Docker Desktop/PowerShell), non eseguiti da Claude Code in questa
sessione. Esito da riportare qui una volta completato.

### Fix `center_mode` preset continental (2026-08-05)

**Stessa finestra Livello 1**, terzo intervento di robustezza dopo il
fix bordo-zero sopra. Diagnosi (utente, verificata sul codice prima di
intervenire, §3.1): sui 6 casi falliti del preset continental, il
centro usato per la ricerca del rim finiva sempre su un bordo del
raster (mare, costa, bordo altopiano, angolo basso dello scudo) perché
il preset continental usava `center_mode: "depression"` — punto più
basso in ROI, accettato incondizionatamente. Il meccanismo
`_near_border` + fallback al centroide (già presente nel file,
`find_caldera_contour_morphological`) è cablato solo dentro il ramo
`center_mode in ("auto", "adaptive")`; il ramo `"depression"` lo
bypassa del tutto.

**Fix, una riga**: preset continental, `center_mode` da `"depression"`
ad `"auto"`. Preset `island` (`"centroid"`) e `shield` (`"adaptive"`)
invariati. Nessun altro punto del file toccato.

Non ancora verificato in questa sessione: rebuild Docker e run di
verifica. Fernandina/Okmok (island) non passano dal preset continental
— attesi invariati a prescindere. Nyiragongo (continental) può
cambiare centro rispetto a prima; il suo `run_quality: "degenerate"`
resta legato al crop stretto (parked issue #2), non è un blocco per
questo fix ma va verificato. Rebuild ed esecuzione a carico
dell'utente. Esito da riportare qui una volta completato.

## 6. Immediate horizon (Sett 5–8)

- **Sett 5–6**: benchmark dataset construction and ground truth
  polyline annotation with Galetto. Resolve the Banda Api ambiguity.
  Fix the Nyiragongo crop. Address `min_area_frac` for shields.
  **Added 2026-08-04**: Livello 1 robustness fixes to the rim/base
  detector (see §5 above) — a real blocker for this sprint, not
  optional scope creep. Non-regression on the 3 good cases required
  before re-running the 9 failed ones.
- **Sett 7–8**: Monte Carlo uncertainty quantification on the rim
  polyline. This will require rapid iteration on `volume_unified.py`
  (authorized sprint window, unchanged and unaffected by the Sett 5-6
  Livello 1 work above — different scope, no overlap) and possibly a
  new orchestration path in `server.js` for batch runs.

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
│       └── components/
│           ├── UploadForm.js            ← vnum input + Verify + GVP badge
│           ├── VolumeSelection.js       ← unified, GVP-driven, no manual cards (merged 2026-08-03, see §5)
│           ├── CardSelection.js         ← orphaned since the 2026-08-03 merge, not deleted (see §5)
│           └── RimMapModal.js
│           └── ...
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

Current set (see `backend/data/gvp_holocene.json`, refreshed 2026-07-21 —
`snapshot_version: 1.0.0-benchmark12`, `snapshot_source:
gvp_holocene_export + pleistocene_schede`). Supersedes the 8-volcano
bootstrap this table used to list.

| vnum   | Name                    | GVP type             | benchmark_family        | Notes                                     |
| ------ | ----------------------- | --------------------- | ------------------------ | ----------------------------------------- |
| 233020 | Piton de la Fournaise   | Shield                | island_simple            | verified                                  |
| 353010 | Fernandina              | Shield                | island_simple            | verified                                  |
| 311290 | Okmok                   | Shield                | island_simple            | verified — was listed here as Caldera in the old bootstrap; real GVP primary type is Shield |
| 257040 | Ambrym                  | Shield(pyroclastic)   | island_complex           | verified — needed the 2026-08-03 `_normalize_gvp_type` fix (parenthetical-qualifier stripping) to resolve to a preset at all, see Sett 5-6 in the paper plan |
| 233010 | Karthala                | Shield                | island_complex           | verified                                  |
| 264230 | Lewotolok               | Stratovolcano         | island_complex           | verified                                  |
| 312090 | Aniakchak               | Caldera               | continental_no_edifice   | verified                                  |
| 223030 | Nyiragongo              | Stratovolcano         | continental_on_edifice   | verified — DEM crop issue still open (§5, parked issue #2) |
| 357150 | Puyehue-Cordon Caulle   | Stratovolcano         | continental_on_edifice   | verified                                  |
| 358041 | Chaitén                 | Caldera               | continental_on_edifice   | verified — preset resolves correctly but `volume_unified.py` still raises `ValueError: No rim component meets constraints` (§5, parked issue #4) |
| 327010 | Valles Caldera          | Caldera               | continental_no_edifice   | verified                                  |
| 323822 | Long Valley             | Caldera               | continental_no_edifice   | verified — Pleistocene caldera, added from its GVP scheda (not in the Holocene export) |

All 12 records now carry `type_verified: true`; the old bootstrap's
`type_verified: false` caveat no longer applies to this set.

**Deliberate exclusions, confirmed 2026-08-03:** the 12-volcano set
above is the final stratified benchmark selection, not an accidental
reduction from the old 8-volcano bootstrap. Erta Ale (221080) and
Fentale (221270) are reserves / edge cases held outside the main set
by design. Banda Api (268060) was not chosen as one of the three
`island_complex` representatives — Ambrym, Karthala and Lewotolok cover
that family instead. None of the three needs re-adding for the current
Sett 5–8 ground-truth work; the empirical issues they originally raised
(§5, items 1 and 3) are archived observations tied to those reserve
volcanoes, not active blockers on this benchmark.

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

*Last updated: 2026-08-05 — fix bordo-zero da reproject in
`ensure_metric_dem` e fix `center_mode` preset continental
(`"depression"` → `"auto"`) applicati (vedi §5); rebuild Docker e
verifica numerica sui casi noti ancora da fare, a carico dell'utente.
Resta aperto anche il rebuild/test end-to-end del merge GVP-driven del
2026-08-03 prima dei 12 run di benchmark.*
*When the state of the project changes, update §5 first, then anything
else that follows from it.*
