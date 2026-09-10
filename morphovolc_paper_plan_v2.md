MorphoVolc 2.0 — Handoff aggiornato + Piano operativo per paper
Documento da incollare all'inizio di ogni nuova chat sul progetto. Sostituisce
morphovolc2_handoff.md.

Parte A — Stato attuale del progetto
A.1 Cos'è MorphoVolc 2.0
Web app per analisi morfometrica e volumetrica di edifici vulcanici e caldere a
partire da DEM.

Frontend: React
Backend: Node/Express
Core scientifico: Python (volume_unified.py)
Viewer: Leaflet

A.2 Cosa funziona

Workflow UI unificato in modalità auto.
Backend e Python supportano il profilo auto (con il caveat sotto).
Rim editor human-in-the-loop attivo: visualizzare, modificare, salvare,
reset to auto, rilanciare il recalculate.
Uso corretto di caldera_rim_auto.geojson e caldera_rim_edited.geojson.
Dopo il recalculate, metrics.json e volume_results.json sono aggiornati
correttamente; rim_source e rim_file tracciati.

A.3 Output prodotti da un run

metrics.json, metrics.csv, volume_results.json
final_doublet_base_vs_caldera.png
caldera_rim_auto.geojson (+ eventuale caldera_rim_edited.geojson)
dem_preview.png, dem_preview.json

A.4 Bug strutturale identificato (diagnosi conclusa)
Il profilo auto non è adattativo. In _normalize_base_profile il fallback
è deterministico:
pythonreturn s or "continental"
Nessuna feature morfologica del DEM viene calcolata prima della selezione dei
parametri. Il preset continental viene applicato a tutti i casi, sia per la
base che per il rim, con parametri:

slope_q = 86.0
roi_dilate_px = 8
min_area_frac = 0.01, max_area_frac = 0.50
center_mode = "depression"
inner_buffer_px = 14

Conseguenza attesa (e coerente con il benchmark):

Scudi oceanici (Piton, Erta Ale, Nyiragongo): rim troppo largo, cattura
break di pendenza sui fianchi anziché caldere reali. center_mode="depression"
su edifici senza vera depressione centrale porta a componenti fuori posto.
Caldere insulari piccole (Banda Api, Chaitén): parametri troppo aggressivi
rispetto alla dimensione reale; scattano i fallback interni
(_relaxed_pass, _barnie_pass), geometrie strane o fallimenti.
Caldere continentali classiche (Valles, Okmok): i casi più vicini al
"buono" perché il preset è tarato per loro.

A.5 Benchmark corrente
8 vulcani: Piton de la Fournaise, Okmok, Chaitén, Nyiragongo, Banda Api,
Erta Ale, Fentale, Valles. Esito primo giro: 2 failed, 1 buono, 5 wrong.

Parte B — Decisione strategica
B.1 Direzione scelta
Non si procede con:

Scenario 1 puro (archivio globale + ML supervisionato su morfologia): il ML
è debole su N piccolo e diventa cosmesi.
Scenario 2 puro (DEM differencing multi-sensore): scientificamente forte ma
richiede 6+ mesi e infrastruttura pesante (co-registrazione, PRISMA, InSAR).
Scenario 3 puro (Volcano Explorer come UI): senza contenuto scientifico
sotto è un wrapper.

Si procede con un mix S1+S3 con angolo metodologico, in cui il GVP fa da
prior esogeno al posto del ML supervisionato, e il contributo scientifico
principale sta in due elementi aggiunti:

Benchmark quantitativo con ground truth disegnata da F. Galetto
(co-autore), su 12-15 vulcani, con metriche oggettive (IoU rim, errore
relativo volume).
Uncertainty quantification sul volume finale via perturbazione Monte
Carlo della rim polyline entro una banda di tolleranza definita dalla
risoluzione DEM + variabilità inter-operatore.

B.2 Contributo scientifico target
Il paper argomenta tre cose, in ordine di forza:

Che una selezione del profilo di rim/base detection informata dalla
classificazione GVP (esogena, trasparente, riproducibile) supera
significativamente un profilo fisso continental-like sul benchmark.
Che una quantificazione dell'incertezza del volume via perturbazione
stocastica della rim polyline è fattibile in tempo interattivo e produce
bande di errore realistiche.
Che il flusso human-in-the-loop (auto → edit esperto → ricalcolo con
banda di incertezza) è un framework operativo per analisi morfometrica
su singolo caso.

B.3 Target rivista (da confermare con Galetto)

Computers & Geosciences — taglio methods + software, ottimo fit.
Journal of Volcanology and Geothermal Research — se si vuole enfasi
vulcanologica sui casi studio.
Earth Science Informatics — taglio infrastruttura + metodo.

B.4 Cosa esplicitamente non entra nel paper
Per proteggere lo scope a 5 mesi:

DEM differencing multi-temporale.
Integrazione PRISMA / InSAR / termico.
ML supervisionato su feature morfometriche.
Anomaly detection su archivio.
"Suggerimento del modello volumetrico" via ML.

Queste possono essere menzionate come future work nella discussione.

Parte C — Piano operativo (20 settimane)
Ogni settimana ha uno o due deliverable concreti. Le date sono orientative;
l'ordine di dipendenza è invece rigido: le settimane successive
presuppongono che quelle precedenti abbiano prodotto il loro deliverable.
Mese 1 — Fondamenta tecniche (Sett 1-4)
Sett 1-2: Fix del profilo auto (Tappa 0).

Progettare la mappatura tipo-GVP → preset (shield / stratovolcano /
caldera / complex → parametri).
Riscrivere _normalize_base_profile per accettare un vnum GVP opzionale.
Modifiche mirate a select_base_contour e
find_caldera_contour_morphological per usare il preset selezionato dal
GVP quando disponibile, con fallback all'attuale se il vnum manca.
Deliverable: patch chirurgica su volume_unified.py, mappa preset
documentata in un file dedicato (gvp_profile_mapping.md).

Sett 3-4: GVP client + integrazione backend.

Modulo per fetch metadati GVP dato vnum (cache locale).
Estensione UploadForm per inserire/cercare il vnum del vulcano.
Passaggio del vnum dal frontend al Python attraverso il backend.
Deliverable: run completo con vnum-driven preset selection funzionante
sui 4 casi peggiori del benchmark (Piton, Erta Ale, Banda Api, Chaitén).

Mese 2 — Benchmark quantitativo con ground truth (Sett 5-8)
Sett 5-6: Ground truth manuale.

Selezionare 4-7 casi aggiuntivi per portare il benchmark a 12-15.
Criterio: coprire bene shield / stratovolcano / caldera continentale /
caldera insulare piccola / morfologia giovane.
Galetto disegna rim e base contour manuali su tutti i casi
(formato GeoJSON, protocollo scritto per uniformità).
Deliverable: cartella ground_truth/ con GeoJSON validati.

Sett 7-8: Metriche e prima valutazione comparativa.

Implementare metriche: IoU rim, IoU base, errore relativo volume,
hausdorff distance rim.
Script di valutazione batch che gira l'app in due modalità (fixed
continental vs GVP-informed) su tutto il benchmark e produce una tabella
di confronto.
Deliverable: tabella risultati + un primo grafico riassuntivo.
Questo è il primo momento di verità del paper.

Mese 3 — Uncertainty quantification (Sett 9-12)
Sett 9-10: Perturbazione Monte Carlo della rim polyline.

Definire il modello di perturbazione: rumore normale sui vertici della
polyline con σ funzione di (risoluzione DEM, variabilità inter-operatore).
Stimare σ inter-operatore dal confronto rim auto vs rim Galetto sui 12-15
casi.
Generare N=100-500 perturbazioni per run, ricalcolare volume su ciascuna,
produrre banda di confidenza (5°-95° percentile).
Deliverable: script rim_uncertainty.py + integrazione nel calcolo
volume.

Sett 11-12: Integrazione UI e output.

Estensione volume_results.json con campi volume_p05, volume_p50,
volume_p95, volume_std.
Visualizzazione della banda nella UI risultati.
Deliverable: run completo end-to-end con volume + banda di incertezza.

Mese 4 — Volcano Explorer e polishing (Sett 13-16)
Sett 13-14: SQLite + Volcano Explorer minimale.

Schema SQLite leggero: volcano (vnum, metadati GVP), morphovolc_run
(processId, vnum, params, output, timestamp).
Schermata Explorer per singolo vulcano: doublet MorphoVolc, scheda GVP,
timeline eruttiva sull'asse dei tempi.
Deliverable: Explorer funzionante su almeno 3 vulcani del benchmark.

Sett 15-16: k-NN vulcani simili + polishing generale.

Nearest-neighbour su feature morfometriche filtrato per tipo GVP.
Test di tenuta del sistema, fix bug residui, documentazione tecnica.
Deliverable: tool congelato per la fase paper, tag versione.

Mese 5 — Paper (Sett 17-20)
Sett 17-18: Figure e esperimenti finali.

Figure principali del paper: mappa benchmark, tabella metriche
comparative, esempi qualitativi (doublet fixed vs GVP-informed vs ground
truth), grafici uncertainty.
Esperimenti aggiuntivi se referee-proof (es. sensitivity al numero di
perturbazioni Monte Carlo).
Deliverable: cartella figures/ completa + notebooks riproducibili.

Sett 19-20: Scrittura draft e revisione con Galetto.

Struttura: Intro / Method (GVP-informed profile + uncertainty) /
Benchmark / Results / Discussion / Conclusion.
Iterazione con Galetto sulla parte vulcanologica.
Deliverable: draft v1 pronto per submission.


Parte C-bis — Stato di avanzamento
Sett 1-2 — COMPLETATA (2026-07-07)
Deliverable raggiunti:

Patch chirurgica su volume_unified.py: implementate resolve_presets,
_normalize_gvp_type, _GVP_TYPE_TO_PRESETS; nuovo preset shield con
parametri motivati; env var GVP_TYPE opzionale; campo
meta.preset_selection in metrics.json per tracciabilità.
Documento gvp_profile_mapping.md (v2) con mappatura tipi GVP → preset,
motivazione parametro per parametro del preset shield, protocollo di test
senza GVP client, e osservazioni empiriche dai run.

Test eseguiti (docker exec, DEM copiati nel container via docker cp):
RunGVP_TYPEPreset risoltoEsitotest_erta_ale_shieldShieldbase=island, rim=shieldPreset shield attivato, rim più compatto del baseline ma ancora sovradimensionatotest_erta_ale_baseline(assente)base=continental, rim=continentalRetrocompat OK, comportamento pre-patch identicotest_nyiragongo_stratovolcanoStratovolcanobase=continental, rim=continentalRetrocompat OK ma DEM croppato troppo stretto → risultato compromessotest_bandaapi_calderaCalderabase=continental, rim=continentalRetrocompat OK, rim frammentato per ambiguità morfologica
Verdetti:

Meccanica GVP-informed preset selection: ✅ funziona.
Retrocompatibilità: ✅ verificata su tre casi indipendenti.
Preset shield v1: meccanicamente corretto, parametricamente da rifinire su
ground truth in Sett 7-8.

Tre osservazioni preziose da portare in Sett 5-8 (dettaglio completo in
gvp_profile_mapping.md §6bis):

Erta Ale: min_area_frac=0.005 nel preset shield è troppo restrittivo.
La caldera reale (~1100 px) viene esclusa dal filtro. In Sett 7-8 valutare
min_area_frac ∈ [0.0005, 0.002] o sostituire con vincolo assoluto in
pixel.
Nyiragongo: il DEM ha un crop insufficiente (mancano ~2/3 dell'edificio,
p02 = 1833 m vs vetta 3049 m). Va rifatto prima di essere usato nel
benchmark quantitativo. Utile in Discussion del paper come esempio di
limite del tipo GVP da solo.
Banda Api: ambiguità di definizione tra caldera insulare storica e
cratere sommitale attivo. Da discutere con Galetto prima che disegni la
ground truth. Impatta anche Chaitén e casi analoghi (Miyakejima, Krakatau).

Sett 3-4 — COMPLETATA (2026-07-11)
Deliverable raggiunti: modulo GVP client (fetch metadati da vnum, cache
locale, backend/gvp/client.js), estensione UploadForm.js con campo vnum
+ Verify, propagazione vnum → GVP_TYPE dal backend Node allo script Python
(server.js, priorità body su meta.json). Deploy locale Docker completato
(docker compose build && up -d, rebuild da zero — nessun container/immagine
preesistente sulla macchina). Run end-to-end via endpoint HTTP reale
(non docker exec manuale) sui 4 casi target:

RunGVP_TYPEPreset risoltoEsitoPiton de la Fournaise (full pipeline
/process → /calculateVolume, vnum ripreso da meta.json)Shieldbase=island,
rim=shieldOK — base 162.38 km², caldera 1.77 km²Erta AleShieldbase=island,
rim=shieldOK — numeri identici al test Sett 1-2 (caldera 32.66 km²,
roi_px 974528)Banda ApiCalderabase=continental, rim=continentalOK —
caldera 0.95 km², coerente con ambiguità morfologica notaChaiténCaldera
base=continental, rim=continentalFAIL — ValueError: No rim component
meets constraints. Mai prodotto metrics.json valido nemmeno in Sett 1-2.
Aggiunto come quarto issue empirico parcheggiato (dettaglio in
gvp_profile_mapping_v2.md §6bis e CLAUDE.md §5.3).

Verdetto: meccanica GVP-informed preset selection via HTTP reale
verificata su 3/4 casi target. Idempotenza vnum (body > meta.json)
verificata su Piton (vnum non ripassato su /calculateVolume, ripreso
correttamente da meta.json scritto da /process).

Hotfix Sett 3-4 (eccezione mirata al §3.3, autorizzata 2026-07-11):
`meta.preset_selection` non veniva mai scritto in metrics.json — il
blocco di costruzione di `metrics["meta"]` in volume_unified.py
(ex-righe 1668-1688) ricostruiva il dict `meta` da zero pescando solo
un sottoinsieme di chiavi da quello originale, senza includere
`preset_selection` prodotto da resolve_presets() a monte. Fix
puramente additivo: una riga (`"preset_selection": meta.get(
"preset_selection"),`) aggiunta al dict, nessuna riga rimossa/modificata.
Diff mostrato e approvato dall'utente prima dell'applicazione. Rerun di
verifica su Erta Ale: numeri identici bit-per-bit al run pre-fix
(base_area_km2, caldera_area_km2, total_volume_km3, h_max_m,
pixel_size_m tutti invariati); `preset_selection` ora presente e
corretto in metrics.json.

Sett 5-6 — IN CORSO

Osservazione emersa (2026-08-03): il dataset GVP è stato aggiornato a
12 vulcani di benchmark (snapshot_version 1.0.0-benchmark12,
snapshot_date 2026-07-21, snapshot_source gvp_holocene_export +
pleistocene_schede) — 4 record in più rispetto agli 8 documentati alla
chiusura di Sett 3-4, inclusi Ambrym, Karthala, Lewotolok, Aniakchak,
Puyehue-Cordon Caulle, Valles Caldera e Long Valley (quest'ultima,
323822, caldera pleistocenica non presente nell'export Holocene).
Nota anche: Okmok è Shield/Shield nel GVP reale, non Caldera come
indicato nella vecchia tabella bootstrap.

Hotfix Sett 5-6 (eccezione mirata al §3.3, autorizzata 2026-08-03):
_normalize_gvp_type in volume_unified.py normalizzava solo il suffisso
GVP thesaurus "(s)" (es. "Shield(s)" -> "shield"). Il nuovo record
Ambrym ha primary_volcano_type = "Shield(pyroclastic)", non riconosciuto
dalla normalizzazione esistente -> preset_hint.matched = false,
reason = gvp_type_unknown, nonostante Ambrym sia morfologicamente uno
shield. Fix: _normalize_gvp_type e il mirror normalizeGvpType in
backend/gvp/client.js estesi per rimuovere qualsiasi contenuto tra
parentesi tonde (non solo "(s)"), con collapse degli spazi multipli
residui. Diff mostrato e approvato dall'utente prima dell'applicazione.
Verifica di non-regressione: resolve_presets rieseguito sui 12 vulcani
reali del benchmark (dentro il container, via docker cp + docker exec).
11/12 preset invariati bit-per-bit; unico cambiamento Ambrym, da non
mappato a base=island, rim=shield. Mirror JS verificato identico via
resolvePresetsForType. Dettaglio completo in gvp_profile_mapping_v2.md
§6bis. Nota: il fix è nel sorgente e copiato nel container in
esecuzione per il test; va reso persistente con un
docker compose build && up -d quando si vorrà bakerlo nell'immagine.

Estensione Sett 5-6 (deroga a §3.3 di CLAUDE.md, apertura anticipata
della finestra su `volume_unified.py`, autorizzata 2026-08-04):
9 dei 12 casi di benchmark risultavano bloccati — 5 crash fatali in
`find_caldera_contour_morphological` (Ambrym, Lewotolok, Aniakchak,
Puyehue, Chaitén) e 2 volumi nulli silenziosi per base contour degenere
in `select_base_contour` (Valles, Long Valley), diagnosticati in
`MorphoVolc_diagnosi_detector.docx` (Marco, 4 agosto 2026). A differenza
degli hotfix precedenti (una riga, un campo), questa è una deroga a
scope più ampio ma a rischio dichiarato basso ("Livello 1 —
Robustezza" del piano a 3 livelli del documento): i 4 raise fatali
diventano esito gestito (partial + confidenza, o fallimento onesto),
il fallback base continental viene validato contro geometria degenere,
e `run_quality`/`run_issues` diventano campi additivi in metrics.json e
volume_results.json. `status` resta invariato ("completed") per non
rompere il gate `VolumeResultsViewer.js` su cui si appoggia la
rifinitura manuale del rim (Valles/Long Valley, vedi §4
dell'handoff). Livello 2 e 3 del documento di Marco (rim tratteggiato,
analisi multi-criterio con curvatura) restano fuori scope, da discutere
con Galetto. Il Monte Carlo di Sett 7-8 resta cantiere separato,
invariato.
Diff validato riga per riga con l'utente prima dell'applicazione,
incluso conteggio diff sul file reale (148 righe aggiunte, 41 rimosse,
20 hunk tutti confinati a select_base_contour,
find_caldera_contour_morphological, run_unified, main()) e verifica
sintattica (ast.parse) sul contenuto scritto su disco. Baseline "prima"
catturato dai run esistenti (cartelle output Fernandina, okmok,
Nyiragongo/77508125-...) prima di applicare la patch, per la
non-regressione. Atteso per Nyiragongo: run_quality "degenerate" (già
passa da continental_sweep_failed_fallback_longest, A_base=4253 m²,
crop noto e parcheggiato — parked issue #2) con numeri identici a
prima; non una regressione. Esito completo (non-regressione sui 3 casi
buoni + rilancio dei 9 falliti) da aggiungere qui una volta applicato e
testato.

Parte D — Filosofia di lavoro (invariata)

Diagnosi prima del codice. Se emerge un problema nuovo, prima si
capisce dove nasce, poi si tocca il codice.
No feature UI non pianificate. Ogni tentazione UI va rimandata al
post-paper.
Ordine fisso di analisi dei run: doublet → rim geojson su preview →
DEM preview → solo dopo metrics.
Debug canonico: meta.base_selection e meta.caldera_rim_detection
in metrics.json sono la fonte di verità per il diagnostico per-caso.


Parte E — Rischi principali e mitigazioni
R1: La ground truth manuale prende più di 2 settimane a Galetto.
Mitigazione: partire con 8 casi (quelli attuali) e aggiungere gli
aggiuntivi in Sett 7-8 in parallelo alla valutazione. Il paper regge già
con 8-10 casi ben fatti.
R2: Il GVP-informed profile non batte significativamente il baseline sui
casi "buoni" (Valles, Okmok).
Mitigazione: è atteso e va bene. Il claim del paper è che nei casi
morfologicamente diversi dal preset continental il metodo migliora; sui
casi vicini al preset di default, parità. Va detto esplicitamente, non
nascosto.
R3: L'uncertainty via perturbazione si rivela troppo semplice (referee).
Mitigazione: la formulazione onesta è che è una upper bound approssimata
data la variabilità della polyline, non un'incertezza fisica completa.
Framing corretto nel paper.
R4: Slippage temporale.
Mitigazione: la parte metodologica (Mesi 1-3) è la spina dorsale. Se
Mese 4 slitta, l'Explorer diventa figura in paper anziché tool finito e
si prosegue con la scrittura. Il paper regge senza Explorer completo.

Parte F — Come riprendere in una nuova chat
Nella nuova chat, allega dal progetto:

Questo documento (morphovolc_paper_plan.md v2).
gvp_profile_mapping.md v2 (contiene osservazioni empiriche Sett 2 che
servono in Sett 3-8).
Il codice sorgente aggiornato: volume_unified.py (patchato Sett 1-2),
server.js, RimMapModal.js, complete_dem_analysis.py, README.md,
e il codice frontend di UploadForm.js/VolumeSelection.js se il
backend/frontend richiedono modifiche in Sett 3-4.

Frase di apertura suggerita per la nuova chat:

Riprendiamo il lavoro su MorphoVolc 2.0. Ti allego il piano operativo
aggiornato (morphovolc_paper_plan.md v2) e il documento di mappatura
preset (gvp_profile_mapping.md v2). Sett 1-2 è chiusa: la meccanica
GVP-informed preset selection funziona e la retrocompatibilità è
verificata. Leggi entrambi i documenti prima di procedere, con
particolare attenzione alla Parte C-bis del piano (stato di avanzamento
e osservazioni empiriche) e alla §6bis del mapping. Poi partiamo con
Sett 3-4: GVP client + integrazione backend.

Se durante Sett 3-8 emergono altri fatti da tramandare in future chat,
aggiornare Parte C-bis di questo documento e la §6bis di
gvp_profile_mapping.md, non aprire nuovi file.