GVP-informed preset mapping — v1
Documento tecnico che accompagna la patch di Sett 1-2 su volume_unified.py.
Definisce la mappatura tra tipo primario GVP e preset di base/rim detection, e
motiva parametro per parametro il nuovo preset shield.
Questa è la v1 della mappatura: i valori sono ragionati morfologicamente ma
non ancora ottimizzati su ground truth. L'ottimizzazione avviene in Sett 7-8
sulle metriche IoU rim + errore volume calcolate contro le ground truth
disegnate da F. Galetto.

1. Come funziona a runtime
La selezione dei preset è controllata da due variabili d'ambiente indipendenti:

BASE_PROFILE: comportamento legacy (island | continental). Continua a
funzionare come prima.
GVP_TYPE: opzionale. Se presente e riconosciuto, override della logica
legacy secondo la tabella nella §2.

Retrocompatibilità: se GVP_TYPE è assente, vuoto, o non riconosciuto,
BASE_PROFILE guida sia il base preset che il rim preset esattamente come
prima della patch.
La logica di risoluzione è implementata da resolve_presets in
volume_unified.py e restituisce (base_preset, rim_preset, debug). Il campo
debug viene serializzato in metrics.json sotto meta.preset_selection per
tracciabilità completa.

2. Mappatura tipo GVP → preset
Tipo GVPPreset basePreset rimNoteShield(s)islandshield (nuovo)Scudi oceanici e continentaliCaldera(s)continentalcontinentalPreset tarato originariamente per questeStratovolcano(es)continentalcontinentalConi compositi con caldera sommitale possibileComplex, Compound volcanocontinentalcontinentalStrutture compositeSomma volcanocontinentalcontinentalSomma + edificio centralePyroclastic cone(s)islandislandConi isolati piccoliTuff cone(s)islandislandIdemLava dome(s)islandislandDomi isolatiMaar(s)islandislandCratere semplice
Tipi non mappati (Volcanic field, Submarine, Fissure vent, ecc.): fallback
al BASE_PROFILE esistente. Registrato in meta.preset_selection.reason come
gvp_type_unknown per essere facilmente estraibile in analisi di copertura.
Alias accettati per robustezza sui GVP strings: Shield = Shields =
Shield(s) = shield volcano; analoghi per gli altri tipi. La
normalizzazione avviene in _normalize_gvp_type.

3. Preset shield — valori v1 e motivazione
Il preset shield è progettato per il caso morfologico più tipico che oggi
fallisce: caldera sommitale su edificio a scudo (Piton, Erta Ale come casi
canonici del benchmark).
Ipotesi morfologiche:

La caldera è al vertice, non sui fianchi → non serve espandere la ROI oltre
la base contour.
I fianchi degli scudi hanno break di pendenza netti che confondono la rim
detection continental → serve una soglia slope molto selettiva.
Le caldere di scudo sono compatte rispetto all'edificio → max_area_frac
basso.
Molti scudi non hanno una vera depressione centrale netta (o hanno pit
crateri marginali che possono catturare erroneamente center_mode="depression").

3.1 Parametri
Parametroislandcontinentalshield v1Motivazioneroi_dilate_px483ROI stretta: il rim è al vertice, non oltre la basesmooth_sigma1.01.21.2Come continental, smoothing sufficienteslope_q90.086.093.0Molto selettivi: solo pixel con slope realmente alta, evita break di flanksmin_component_px400600200Compensare max_area_frac più stretto: componenti piccole ammessemin_area_frac0.020.010.005Idemclosing_iterations233Come continental, chiudere gap del rimextra_dilate_px011Come continentalmax_area_frac0.350.500.15Caldere di scudo sono piccole vs edificioinner_buffer_px10146Buffer interna ridotta, coerente con caldera piccolacenter_bias_weight18.022.015.0Peso medio del bias centraleoverlap_modereward_innerreward_innerreward_innerCome baselinepost_close_iterations222post_fill_holesTrueTrueTruecenter_modecentroiddepressionadaptivePassa a centroid se depressione lontana o eligible troppo piccolodep_to_centroid_max_px(default 25)(default 25)12.0Soglia stretta: se il minimo DEM è lontano dal centroide, non lo si accetta come centro caldera
Gli altri campi (center_border_margin_px, min_eligible_frac_for_depression,
soglie del barnie_pass) restano ai default esistenti perché già ragionevoli
per il preset shield.
3.2 Comportamento atteso su casi canonici

Piton de la Fournaise: slope_q=93 filtra i break di flanks, roi_dilate_px=3
non estende la ROI su fianchi, max_area_frac=0.15 esclude che venga presa
come rim una grande porzione del Dolomieu + Bory. center_mode=adaptive
dovrebbe scegliere depression se il pit sommitale è centrato, altrimenti
cadere su centroid.
Erta Ale: analogo, con il lago di lava che dovrebbe fungere da
depressione centrale ben definita. Se dep_to_centroid_max_px=12 è troppo
stretto qui, va rilassato in v2.

3.3 Rischi noti su cui vigilare in Sett 5-8

Se slope_q=93 è troppo alto: la rim potrebbe essere vuota o
frammentata, scattando _relaxed_pass. Nel debug metrics.json questo
appare come fallback_relaxed_cfg. Se scatta sistematicamente sugli scudi,
scendere a 91-92.
Se max_area_frac=0.15 è troppo stretto: alcuni scudi con caldera
proporzionalmente più grande (Erta Ale in configurazioni certi anni)
potrebbero non trovare componenti valide. In quel caso scattano i fallback
interni. Da monitorare nei debug.
dep_to_centroid_max_px=12 in unità pixel: dipende dalla risoluzione
DEM. A 30 m/pixel (SRTM) sono ~360 m; a 10 m/pixel sono ~120 m. Per DEM ad
alta risoluzione andrà rivisto.


4. Mapping GVP dei casi del benchmark
Tipi GVP effettivi (da verificare puntualmente sul GVP database in Sett 3-4
quando ci sarà il client). Alcune classificazioni GVP possono sorprendere
rispetto all'intuizione morfologica: es. Nyiragongo è classificato come
"Stratovolcano" ma comportamentalmente ha caratteristiche di scudo con lago
di lava sommitale. Sono casi da discutere con Galetto.
VulcanoTipo GVP (atteso)(base, rim) risoltoPiton de la FournaiseShield(island, shield)Erta AleShield(island, shield)NyiragongoStratovolcano (da verificare)(continental, continental)OkmokCaldera(continental, continental)ChaiténCaldera(continental, continental)Banda ApiCaldera(continental, continental)FentaleStratovolcano (da verificare)(continental, continental)VallesCaldera(continental, continental)
I casi in cui il tipo GVP diverge dall'intuizione morfologica (Nyiragongo in
particolare) sono preziosi: se dopo la patch Nyiragongo continua a fallire
mentre Piton ed Erta Ale migliorano, abbiamo evidenza che il tipo GVP puro
non basta e serve un secondo livello (per esempio combinare tipo GVP con una
metrica morfometrica leggera dal DEM). Questo è materiale per la Discussion
del paper.

5. Come testare in Sett 1-2 senza GVP client
Fino a quando il GVP client (Sett 3-4) non fornisce automaticamente GVP_TYPE,
lo si setta a mano via env variable prima di lanciare un run:
bashexport GVP_TYPE="Shield"
export BASE_PROFILE="continental"   # legacy, ignorato quando GVP_TYPE mappa
python volume_unified.py path/to/piton_dem.tif
Il debug in metrics.json mostrerà:
json"preset_selection": {
  "preset_source": "gvp_mapping",
  "gvp_type_raw": "Shield",
  "gvp_type_normalized": "shield",
  "base_preset": "island",
  "rim_preset": "shield",
  "base_profile_env": "continental"
}
Con GVP_TYPE non settato, invece:
json"preset_selection": {
  "preset_source": "base_profile_env",
  "gvp_type_raw": "",
  "gvp_type_normalized": "",
  "base_preset": "continental",
  "rim_preset": "continental",
  "base_profile_env": "continental",
  "reason": "gvp_type_absent"
}
Questo campo è la fonte di verità per ogni analisi di ablation nel paper.

6. Cosa NON è cambiato
Per chiarezza esplicita, elenco cosa la patch non tocca, così eventuali
regressioni si diagnosticano subito:

Formula del volume (prismoid/frustum) e integrazione depth-integrated della
caldera: invariate.
Preset island e continental: valori numerici invariati byte-per-byte.
Fallback _relaxed_pass e _barnie_pass: logica invariata.
Rim editor UI e workflow human-in-the-loop: invariati.
Nomi dei file output, schema di metrics.json (nuovo campo aggiunto, nessuno
rimosso), schema di volume_results.json: invariati.
Chiamata di select_base_contour: firma invariata (accetta base_profile
come stringa; il valore passato è quello risolto da resolve_presets).
Chiamata di find_caldera_contour_morphological: firma invariata (accetta
preset come stringa; il valore passato è quello risolto da
resolve_presets).



6bis. Osservazioni empiriche dai test di Sett 2
Note operative dai primi run del benchmark, da tenere presenti quando arriveranno
la ground truth (Sett 5-6) e l'ottimizzazione parametri (Sett 7-8).
Erta Ale (Shield, run test_erta_ale_shield)

Meccanica del preset shield: OK, attivato correttamente e senza fallback interni.
Miglioramento vs baseline continental: presente ma marginale — rim leggermente
più compatto, ma ancora troppo grande rispetto alla caldera reale (~1 km² vera
vs ~32 km² selezionati).
Diagnosi precisa dal metrics.json: min_area_frac = 0.005 × ROI (974528 px)
= 4872 px minimo. La caldera reale di Erta Ale è ~1100 px. Viene esclusa
dal filtro prima di essere considerata come componente eligible.
Nella lista di tutte le componenti candidate ce ne sono diverse tra 500 e
2000 px (648, 730, 760, 804, 808, 1031, 2002) che sarebbero candidate
plausibili con un filtro più permissivo.
Azione in Sett 7-8: per il preset shield valutare min_area_frac ∈ [0.0005, 0.002], oppure sostituire il vincolo relativo con un vincolo
assoluto in pixel (indipendente dalla ROI). Da tunare su ground truth di
almeno 4-5 shield.

Nyiragongo (Stratovolcano, run test_nyiragongo_stratovolcano)

Retrocompatibilità: OK, preset continental applicato con parametri
pre-patch invariati.
Ma il risultato è fortemente compromesso da un problema di dominio
diverso: il DEM di Nyiragongo è croppato troppo stretto. La p02 del
DEM è 1833 m, la vetta è 3049 m — mancano i primi 2/3 dell'edificio.
Il codice ha ripiegato su continental_sweep_failed_fallback_longest con
picked_len = 127 e A_base = 4253 m² (dimensione assurda). La ROI è
collassata su tutto il DEM valido via roi_fallback.
Questo è un errore di "crop problem" nella tassonomia dell'handoff, non
un problema del preset. Prima di usare Nyiragongo nel benchmark
quantitativo di Sett 5-8 va rifatto il DEM con un crop più largo che
includa il piede dell'edificio.
Nota per il paper: questo caso è utile in Discussion come dimostrazione
che il tipo GVP da solo non basta — serve anche una sanity check sul crop
del DEM (per esempio: rapporto tra range di quota e vetta stimata).

Banda Api (Caldera, run test_bandaapi_caldera)

Retrocompatibilità: OK, preset continental applicato invariato.
Rim ragionevole ma frammentato (contour_len 287, 3 componenti candidate di
cui una sola eligible).
Ambiguità morfologica di definizione: nel caso di caldere insulari, la
"caldera" può essere interpretata come (a) l'intera struttura insulare
storica, che coincide più o meno con la base contour, o (b) il cratere
sommitale attivo del cono ricostruito interno.
Il codice trova (b), il che è coerente con la logica di rim detection ma
potrebbe non essere ciò che F. Galetto disegnerà come ground truth.
Azione in Sett 5-6: prima che Galetto inizi la ground truth, decidere
con lui quale delle due interpretazioni vogliamo che il rim rappresenti.
La scelta impatta anche Chaitén e potenzialmente altri casi analoghi
(Miyakejima, Krakatau...). Va documentata nel protocollo scritto del
ground truth.

Verdetto complessivo Sett 1-2

Meccanica GVP-informed preset selection: funziona.
Retrocompatibilità: verificata su 3 casi (baseline Erta Ale, Nyiragongo
continental, Banda Api continental).
Preset shield v1: parametricamente da rifinire, meccanicamente corretto.
Deliverable Sett 1-2: raggiunto. Si passa a Sett 3-4.


7. Prossimi passi (roadmap breve)

Sett 3-4: GVP client Node/Python che risolve vnum GVP → tipo. Popola
GVP_TYPE automaticamente. Nessuna modifica ulteriore a volume_unified.py.
Sett 5-6: ground truth Galetto sui casi del benchmark.
Sett 7-8: ottimizzazione dei parametri shield (e ritocco eventuale di
continental per stratovolcani se emerge un mismatch) usando le metriche
quantitative.