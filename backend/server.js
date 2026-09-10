// server.js (MorphoVolc 2.0 — unified-only)
const express = require('express');
const cors = require('cors');
const multer = require('multer');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const { v4: uuidv4 } = require('uuid');
const { performance } = require('perf_hooks');

// ✅ Load backend/.env reliably (DEV)
try {
  require('dotenv').config({ path: path.join(__dirname, '.env') });
} catch (e) {
  // dotenv not installed or not wanted -> ok
}

// ---------------------------
// GVP client (Sett 3-4)
// ---------------------------
const gvpClient = require('./gvp/client');
const gvpDatasetPath = process.env.GVP_DATASET_PATH
  ? path.resolve(process.env.GVP_DATASET_PATH)
  : path.join(__dirname, 'data', 'gvp_holocene.json');
gvpClient.loadGvpDataset(gvpDatasetPath);

const app = express();
app.use(cors());
app.use(express.json({ limit: '10mb' }));

// ---------------------------
// Env / Paths (docker-friendly)
// ---------------------------
const PORT = Number(process.env.PORT || 5000);
const DEBUG_CALC = String(process.env.DEBUG_CALC || '').trim() === '1';

const PYTHON_BIN_RAW = (process.env.PYTHON_BIN || process.env.PYTHON_PATH || '').trim();

const uploadsDir = process.env.UPLOADS_DIR
  ? path.resolve(process.env.UPLOADS_DIR)
  : path.join(__dirname, 'uploads');

const outputsDir = process.env.OUTPUTS_DIR
  ? path.resolve(process.env.OUTPUTS_DIR)
  : path.join(__dirname, 'outputs');

fs.mkdirSync(uploadsDir, { recursive: true });
fs.mkdirSync(outputsDir, { recursive: true });

// Serve outputs (PNG/PDF/JSON) as static
app.use('/outputs', express.static(outputsDir));

// ---------------------------
// Python command resolution
// ---------------------------
function resolvePythonCommand() {
  const isWin = process.platform === 'win32';

  if (PYTHON_BIN_RAW) {
    const trimmed = PYTHON_BIN_RAW;

    if (
      (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
      (trimmed.startsWith("'") && trimmed.endsWith("'"))
    ) {
      return { cmd: trimmed.slice(1, -1), prefix: [] };
    }

    if (trimmed.includes(' ')) {
      const parts = trimmed.split(/\s+/).filter(Boolean);
      return { cmd: parts[0], prefix: parts.slice(1) };
    }

    return { cmd: trimmed, prefix: [] };
  }

  if (isWin) return { cmd: 'py', prefix: ['-3'] };
  return { cmd: 'python3', prefix: [] };
}

const PY = resolvePythonCommand();

console.log('[SERVER] config:', {
  PORT,
  PYTHON_BIN_RAW: PYTHON_BIN_RAW || '(not set)',
  PYTHON_CMD: PY.cmd,
  PYTHON_PREFIX: PY.prefix,
  uploadsDir,
  outputsDir,
  FRONTEND_BUILD_DIR: process.env.FRONTEND_BUILD_DIR || '(not set)',
  HEADLESS: process.env.HEADLESS,
  PROJ_LIB: process.env.PROJ_LIB,
  PROJ_DATA: process.env.PROJ_DATA,
  DEBUG_CALC,
});

// ---------------------------
// Multer storage (preserve name)
// ---------------------------
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, uploadsDir),
  filename: (req, file, cb) => cb(null, file.originalname),
});
const upload = multer({ storage });

// Processing status map (per complete_dem_analysis)
const processingStatus = {};

// ---------------------------
// Python env helper
// ---------------------------
function buildPyEnv(extraEnv = {}) {
  const env = {
    ...process.env,
    ...extraEnv,
    PYTHONUNBUFFERED: '1',

    // Headless defaults
    HEADLESS: process.env.HEADLESS || '1',
    MPLBACKEND: process.env.MPLBACKEND || 'Agg',
    QT_QPA_PLATFORM: process.env.QT_QPA_PLATFORM || 'offscreen',
  };

  // Linux/Docker: avoid host PROJ mismatch
  if (process.platform !== 'win32') {
    if (env.PROJ_LIB) {
      console.log(`[SERVER] clearing PROJ_LIB for non-Windows run (was: ${env.PROJ_LIB})`);
      delete env.PROJ_LIB;
    }
    if (env.PROJ_DATA) {
      console.log(`[SERVER] clearing PROJ_DATA for non-Windows run (was: ${env.PROJ_DATA})`);
      delete env.PROJ_DATA;
    }
  }

  return env;
}

function spawnPython({ args, processId, extraEnv = {} }) {
  const fullArgs = [...PY.prefix, ...args];

  console.log(
    `[SERVER] spawnPython pid=${processId || '-'} ->`,
    PY.cmd,
    fullArgs.slice(0, 6).join(' '),
    '...'
  );

  return spawn(PY.cmd, fullArgs, {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: buildPyEnv({
      PROCESS_ID: processId || '',
      OUTPUTS_DIR: outputsDir,
      UPLOADS_DIR: uploadsDir,
      ...extraEnv,
    }),
    shell: false,
  });
}

// ---------------------------
// Helpers
// ---------------------------
function normalizeBaseProfile(v) {
  const s = String(v || '').trim().toLowerCase();
  if (s === 'island' || s === 'simple' || s === 'simple_base') return 'island';
  if (s === 'continental' || s === 'complex' || s === 'complex_base') return 'continental';
  return s;
}

function deriveUnifiedModuleKey(baseProfile) {
  const bp = normalizeBaseProfile(baseProfile);
  if (bp === 'island' || bp === 'continental') return `unified_${bp}`;
  return 'unified';
}

// ---------------------------
// GVP helpers (Sett 3-4)
// ---------------------------
// Extract a raw vnum from either the request body (form-data) or the
// meta.json already written by a previous step. Returns a trimmed string
// or '' if not set. The body takes priority so that a run can override
// what was previously stored in meta.
function extractVnum(req, procDir) {
  const rawFromBody =
    (req && req.body && req.body.vnum != null) ? String(req.body.vnum).trim() : '';
  if (rawFromBody) return rawFromBody;

  try {
    const metaPath = path.join(procDir, 'meta.json');
    if (fs.existsSync(metaPath)) {
      const meta = JSON.parse(fs.readFileSync(metaPath, 'utf-8'));
      if (meta && meta.vnum != null) {
        const v = String(meta.vnum).trim();
        if (v) return v;
      }
    }
  } catch {
    // fall through
  }
  return '';
}

// Resolve vnum -> { record, gvp_type, gvp_type_env, resolution } for logging
// and to build the GVP_TYPE env var passed to Python. Never throws.
function resolveVnumForRun(vnum) {
  const raw = String(vnum || '').trim();
  const out = {
    vnum: raw,
    record: null,
    gvp_type: '',        // raw type string, e.g. "Shield"
    gvp_type_env: '',    // exact string to feed as GVP_TYPE (raw, not normalized)
    gvp_lat: '',         // string for GVP_LAT env (empty if absent/invalid)
    gvp_lon: '',         // string for GVP_LON env (empty if absent/invalid)
    resolution: 'none',
    preset_hint: null,   // { matched, base_preset, rim_preset, reason }
  };
  if (!raw) return out;

  const rec = gvpClient.resolveByVnum(raw);
  if (!rec) {
    out.resolution = 'vnum_not_found';
    return out;
  }
  out.record = rec;
  out.gvp_type = String(rec.primary_volcano_type || '');
  out.gvp_type_env = out.gvp_type;
  // Approccio 3: expose GVP coordinates so they can be passed to Python as
  // GVP_LAT/GVP_LON and used to anchor the caldera center (continental preset).
  if (rec.latitude != null && Number.isFinite(Number(rec.latitude))) {
    out.gvp_lat = String(Number(rec.latitude));
  }
  if (rec.longitude != null && Number.isFinite(Number(rec.longitude))) {
    out.gvp_lon = String(Number(rec.longitude));
  }
  out.preset_hint = gvpClient.resolvePresetsForType(out.gvp_type);
  out.resolution = out.preset_hint && out.preset_hint.matched
    ? 'resolved'
    : 'type_unmapped';
  return out;
}

// write meta.json (report titles / traceability)
function writeProcessMeta(procDir, metaPatch) {
  try {
    fs.mkdirSync(procDir, { recursive: true });

    const metaPath = path.join(procDir, 'meta.json');
    let existing = {};
    if (fs.existsSync(metaPath)) {
      try {
        existing = JSON.parse(fs.readFileSync(metaPath, 'utf-8'));
      } catch {
        existing = {};
      }
    }

    const merged = { ...existing, ...metaPatch };
    fs.writeFileSync(metaPath, JSON.stringify(merged, null, 2), 'utf-8');
  } catch (e) {
    console.warn('[WARN] Could not write meta.json:', e);
  }
}

function patchVolumeResultsFile(procDir, moduleKey, baseProfile, rimSource = null, rimFile = null) {
  try {
    const p = path.join(procDir, 'volume_results.json');
    if (!fs.existsSync(p)) return;

    const raw = fs.readFileSync(p, 'utf-8');
    let obj = null;
    try {
      obj = JSON.parse(raw);
    } catch {
      return;
    }

    obj.moduleKey = moduleKey;
    obj.baseProfile = baseProfile || '';
    if (rimSource) obj.rim_source = rimSource;
    if (rimFile) obj.rim_file = rimFile;

    fs.writeFileSync(p, JSON.stringify(obj, null, 2), 'utf-8');
  } catch (e) {
    console.warn('[WARN] patchVolumeResultsFile failed:', e);
  }
}

// metrics picker (unified). I file ora si chiamano metrics_<stem>.json/csv
// (naming col nome del vulcano, richiesta 2026-08-22). Si cerca prima il
// nome canonico legacy (metrics.json/csv) per retrocompatibilita' con run
// gia' presenti in cartella, poi il pattern metrics_*.<ext>. La cartella
// contiene una sola run, quindi al piu' un file per estensione.
function pickMetricsPath(procDir, ext /* 'csv'|'json' */) {
  try {
    const wantedExt = String(ext || '').toLowerCase() === 'csv' ? 'csv' : 'json';

    // 1) nome legacy canonico
    const legacy = path.join(procDir, `metrics.${wantedExt}`);
    if (fs.existsSync(legacy)) return legacy;

    // 2) nome col nome del vulcano: metrics_<stem>.<ext>
    let entries = [];
    try {
      entries = fs.readdirSync(procDir);
    } catch {
      return null;
    }
    const re = new RegExp(`^metrics_.+\\.${wantedExt}$`, 'i');
    const match = entries.find((f) => re.test(f));
    return match ? path.join(procDir, match) : null;
  } catch {
    return null;
  }
}

function isSafeProcessId(processId) {
  return /^[A-Za-z0-9_-]+$/.test(String(processId || ''));
}

function getProcessDir(processId) {
  return path.join(outputsDir, String(processId));
}

function getAutoRimPath(processId) {
  return path.join(getProcessDir(processId), 'caldera_rim_auto.geojson');
}

function getEditedRimPath(processId) {
  return path.join(getProcessDir(processId), 'caldera_rim_edited.geojson');
}

function pickAvailableRim(processId) {
  const editedPath = getEditedRimPath(processId);
  if (fs.existsSync(editedPath)) {
    return {
      rimSource: 'edited',
      rimFile: 'caldera_rim_edited.geojson',
      rimPath: editedPath,
    };
  }

  const autoPath = getAutoRimPath(processId);
  if (fs.existsSync(autoPath)) {
    return {
      rimSource: 'auto',
      rimFile: 'caldera_rim_auto.geojson',
      rimPath: autoPath,
    };
  }

  return null;
}

function ensureLinearRingClosed(coords) {
  if (!Array.isArray(coords) || coords.length < 4) return false;

  const first = coords[0];
  const last = coords[coords.length - 1];

  if (
    !Array.isArray(first) || first.length < 2 ||
    !Array.isArray(last) || last.length < 2
  ) {
    return false;
  }

  return Number(first[0]) === Number(last[0]) && Number(first[1]) === Number(last[1]);
}

function isValidPolygonGeometry(geometry) {
  if (!geometry || geometry.type !== 'Polygon' || !Array.isArray(geometry.coordinates)) return false;
  if (geometry.coordinates.length < 1) return false;

  for (const ring of geometry.coordinates) {
    if (!Array.isArray(ring) || ring.length < 4) return false;
    for (const pt of ring) {
      if (!Array.isArray(pt) || pt.length < 2) return false;
      const x = Number(pt[0]);
      const y = Number(pt[1]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
    }
    if (!ensureLinearRingClosed(ring)) return false;
  }

  return true;
}

function extractSinglePolygonFeature(payload) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('Request body must be a valid GeoJSON object.');
  }

  let feature = null;

  if (payload.type === 'Feature') {
    feature = payload;
  } else if (payload.type === 'FeatureCollection') {
    if (!Array.isArray(payload.features) || payload.features.length !== 1) {
      throw new Error('FeatureCollection must contain exactly one feature.');
    }
    feature = payload.features[0];
  } else {
    throw new Error('GeoJSON must be a Feature or a FeatureCollection.');
  }

  if (!feature || feature.type !== 'Feature') {
    throw new Error('GeoJSON payload must contain a valid Feature.');
  }

  if (!isValidPolygonGeometry(feature.geometry)) {
    throw new Error('GeoJSON geometry must be a valid Polygon with closed rings.');
  }

  return {
    type: 'Feature',
    geometry: feature.geometry,
    properties: {
      ...(feature.properties && typeof feature.properties === 'object' ? feature.properties : {}),
      source: 'edited',
      editing_crs: 'EPSG:4326',
      saved_at: new Date().toISOString(),
    },
  };
}

// ---------------------------
// Healthcheck
// ---------------------------
app.get('/health', (req, res) => res.json({ ok: true }));

// ---------------------------
// DEM preprocess (complete_dem_analysis)
// ---------------------------
app.post('/process', upload.single('demFile'), (req, res) => {
  const t0 = performance.now();
  const file = req.file;

  if (!file) {
    console.error('[ERROR] Nessun file caricato.');
    return res.status(400).json({ error: 'Nessun file caricato.' });
  }

  const scriptPath = path.join(__dirname, 'scripts', 'complete_dem_analysis.py');

  const originalFileNameRaw =
    (req.body.originalFileName && String(req.body.originalFileName).trim())
      ? String(req.body.originalFileName).trim()
      : file.originalname;

  const originalFileStem = path.parse(originalFileNameRaw).name || 'Unknown';

  const processId = uuidv4();
  processingStatus[processId] = { status: 'processing' };

  const procDir = path.join(outputsDir, processId);
  const inputDemName = file?.originalname ? String(file.originalname) : (originalFileNameRaw || `${processId}.tif`);

  // Sett 3-4: capture vnum on step 1 (optional) so that step 2 can retrieve
  // it from meta.json without re-entry.
  const vnumRaw = req.body.vnum != null ? String(req.body.vnum).trim() : '';
  const gvpRes = resolveVnumForRun(vnumRaw);
  if (vnumRaw) {
    console.log(
      `[GVP] /process pid=${processId} vnum=${vnumRaw} ` +
      `resolution=${gvpRes.resolution} type='${gvpRes.gvp_type}'`
    );
  }

  writeProcessMeta(procDir, {
    input_dem_name: inputDemName,
    original_file_stem: String(originalFileStem || ''),
    processId: String(processId || ''),
    step: 'complete_dem_analysis',
    updated_at: new Date().toISOString(),
    // GVP fields — always written (empty strings when absent) so downstream
    // code can rely on the schema.
    vnum: gvpRes.vnum,
    gvp_type: gvpRes.gvp_type,
    gvp_resolution: gvpRes.resolution,
    gvp_name: gvpRes.record ? String(gvpRes.record.name || '') : '',
  });

  const absFilePath = path.resolve(file.path);

  const child = spawnPython({
    processId,
    args: [scriptPath, absFilePath, originalFileStem, processId],
    extraEnv: {
      ORIGINAL_FILE_NAME: originalFileNameRaw,
      ORIGINAL_FILE_STEM: originalFileStem,
      UPLOADS_DIR: uploadsDir,
      OUTPUTS_DIR: outputsDir,
      // GVP_TYPE is currently only consumed by volume_unified.py in step 2;
      // exporting it here too is harmless and keeps the two steps symmetric.
      GVP_TYPE: gvpRes.gvp_type_env,
      GVP_LAT: gvpRes.gvp_lat,
      GVP_LON: gvpRes.gvp_lon,
    },
  });

  child.stdout.on('data', (d) => {
    const line = d.toString().trim();
    if (line) console.log(`[PY ${processId}] ${line}`);
  });

  child.stderr.on('data', (d) => {
    const line = d.toString().trim();
    if (line) console.error(`[PY ${processId} ERR] ${line}`);
  });

  child.on('close', (code) => {
    const dt = (performance.now() - t0).toFixed(1);
    console.log(`[PY ${processId}] exited with code ${code} (server elapsed ${dt} ms)`);
    if (code !== 0 && processingStatus[processId]) processingStatus[processId].status = 'failed';
  });

  return res.json({ message: 'Processing started', processId });
});

// Status
app.get('/processStatus/:processId', (req, res) => {
  const { processId } = req.params;
  const statusInfo = processingStatus[processId];
  if (statusInfo) return res.json({ status: statusInfo.status });
  res.status(404).json({ error: 'Process ID not found' });
});

// Python callback
app.post('/processComplete/:processId', (req, res) => {
  const { processId } = req.params;
  if (processingStatus[processId]) {
    processingStatus[processId].status = 'completed';
    console.log(`[INFO] process ${processId} marked as completed by Python callback`);
    return res.json({ message: 'Process status updated to completed' });
  }
  res.status(404).json({ error: 'Process ID not found' });
});

// ---------------------------
// Volume (Unified ONLY)
// ---------------------------
app.post('/calculateVolume', upload.single('demFile'), (req, res) => {
  const t0 = performance.now();

  const file = req.file;
  const baseProfileRaw = req.body.baseProfile;
  const baseProfile = normalizeBaseProfile(baseProfileRaw);

  // Unified GVP-driven workflow: baseProfile non è più una scelta manuale
  // obbligatoria. Se arriva un valore island/continental valido (chiamante
  // legacy) lo si onora; altrimenti si usa 'continental' come fallback
  // conservativo. Questo default è deliberato e viene normalmente
  // sovrascritto da GVP_TYPE dentro resolve_presets() quando il vnum
  // risolve — vedi gvp_profile_mapping_v2.md.
  const baseProfileEffective =
    (baseProfile === 'island' || baseProfile === 'continental') ? baseProfile : 'continental';

  if (DEBUG_CALC) {
    console.log('[SERVER] /calculateVolume req:', {
      baseProfileRaw,
      baseProfile,
      baseProfileEffective,
      processId: req.body.processId,
      hasFile: !!file,
    });
  }

  const originalFileNameRaw =
    (req.body.originalFileName && String(req.body.originalFileName).trim())
      ? String(req.body.originalFileName).trim()
      : (file ? file.originalname : '');

  const originalFileStem = (originalFileNameRaw && path.parse(originalFileNameRaw).name)
    ? path.parse(originalFileNameRaw).name
    : 'Unknown';

  // Use processId from step1 if present
  const processId = req.body.processId ? String(req.body.processId) : uuidv4();

  const procDir = path.join(outputsDir, processId);
  fs.mkdirSync(procDir, { recursive: true });

  const editedRimPath = getEditedRimPath(processId);
  const hasEditedRim = fs.existsSync(editedRimPath);
  const rimSource = hasEditedRim ? 'edited' : 'auto';
  const rimFile = hasEditedRim ? 'caldera_rim_edited.geojson' : 'caldera_rim_auto.geojson';

  // Manifest-first DEM selection
  const demWorkingPath = path.join(procDir, 'dem_working.tif');
  let demInputPath = null;

  if (fs.existsSync(demWorkingPath)) demInputPath = demWorkingPath;
  else if (file && file.path) demInputPath = path.resolve(file.path);

  if (!demInputPath) {
    return res.status(400).json({
      error: 'Missing DEM input. Provide processId with dem_working.tif or upload a DEM file.',
      processId,
    });
  }

  const moduleKey = deriveUnifiedModuleKey(baseProfileEffective);

  // Sett 3-4: resolve vnum. Body takes priority over meta.json (idempotency).
  const gvpRes = resolveVnumForRun(extractVnum(req, procDir));
  if (gvpRes.vnum) {
    console.log(
      `[GVP] /calculateVolume pid=${processId} vnum=${gvpRes.vnum} ` +
      `resolution=${gvpRes.resolution} type='${gvpRes.gvp_type}'`
    );
  }

  writeProcessMeta(procDir, {
    input_dem_name: (demInputPath === demWorkingPath) ? 'dem_working.tif' : (file?.originalname || originalFileNameRaw || `${processId}.tif`),
    original_file_stem: String(originalFileStem || ''),
    processId: String(processId || ''),
    moduleKey: String(moduleKey || ''),
    baseProfile: String(baseProfileEffective || ''),
    volumeType: 'unified',
    approximationType: 'unified',
    dem_input_path: demInputPath ? String(demInputPath) : '',
    rim_source: rimSource,
    rim_file: rimFile,
    step: 'calculate_volume',
    updated_at: new Date().toISOString(),
    vnum: gvpRes.vnum,
    gvp_type: gvpRes.gvp_type,
    gvp_resolution: gvpRes.resolution,
    gvp_name: gvpRes.record ? String(gvpRes.record.name || '') : '',
  });

  const scriptPath = path.join(__dirname, 'scripts', 'volume_unified.py');
  if (!fs.existsSync(scriptPath)) {
    return res.status(500).json({
      error: 'volume_unified.py not found',
      expectedPath: scriptPath,
    });
  }

  const child = spawnPython({
    processId,
    args: [scriptPath, demInputPath, originalFileStem],
    extraEnv: {
      ORIGINAL_FILE_NAME: originalFileNameRaw,
      ORIGINAL_FILE_STEM: originalFileStem,
      UPLOADS_DIR: uploadsDir,
      OUTPUTS_DIR: outputsDir,

      MODULE_KEY: moduleKey,
      BASE_PROFILE: baseProfileEffective,
      VOLUME_TYPE: 'unified',
      APPROXIMATION_TYPE: 'unified',
      DEM_INPUT: demInputPath,

      USE_EDITED_RIM: hasEditedRim ? '1' : '0',
      RIM_PATH: hasEditedRim ? editedRimPath : '',
      RIM_SOURCE: rimSource,

      // GVP-informed preset selection (see gvp_profile_mapping.md).
      // Raw GVP type string; volume_unified.py normalizes it internally.
      // Empty string preserves the pre-patch retrocompatibility path.
      GVP_TYPE: gvpRes.gvp_type_env,
      GVP_LAT: gvpRes.gvp_lat,
      GVP_LON: gvpRes.gvp_lon,
    },
  });

  let resultData = '';

  child.stdout.on('data', (d) => {
    const s = d.toString();
    resultData += s;
    s.split(/\r?\n/).forEach((line) => {
      if (line.trim()) console.log(`[PY VOL ${processId}] ${line.trim()}`);
    });
  });

  child.stderr.on('data', (d) => {
    const s = d.toString();
    s.split(/\r?\n/).forEach((line) => {
      if (line.trim()) console.error(`[PY VOL ${processId} ERR] ${line.trim()}`);
    });
  });

  child.on('close', (code) => {
    const dt = (performance.now() - t0).toFixed(1);
    console.log(`[TIMING][SERVER] /calculateVolume finished in ${dt} ms (code=${code})`);

    if (code !== 0) {
      return res.status(500).json({ error: 'Error calculating volume' });
    }

    patchVolumeResultsFile(procDir, moduleKey, baseProfileEffective, rimSource, rimFile);

    // Preferred: serve the on-disk truth
    try {
      const vrPath = path.join(procDir, 'volume_results.json');
      if (fs.existsSync(vrPath)) {
        const vr = JSON.parse(fs.readFileSync(vrPath, 'utf-8'));
        vr.moduleKey = moduleKey;
        vr.baseProfile = baseProfileEffective || '';
        vr.rim_source = rimSource;
        vr.rim_file = rimFile;
        return res.json(vr);
      }
    } catch (e) {
      console.warn('[WARN] Could not read volume_results.json:', e);
    }

    // Last fallback: parse stdout if it was clean JSON
    try {
      const parsed = JSON.parse(resultData);
      parsed.moduleKey = moduleKey;
      parsed.baseProfile = baseProfileEffective || '';
      parsed.rim_source = rimSource;
      parsed.rim_file = rimFile;
      return res.json(parsed);
    } catch {
      return res.json({
        processId,
        status: 'completed',
        moduleKey,
        baseProfile: baseProfileEffective,
        rim_source: rimSource,
        rim_file: rimFile,
        result: resultData,
        images: [],
      });
    }
  });
});

// ---------------------------
// Metrics export
// GET /api/metrics/:processId?format=json|csv
// ---------------------------
app.get('/api/metrics/:processId', (req, res) => {
  const { processId } = req.params;
  const format = (req.query.format ? String(req.query.format) : 'json').toLowerCase();

  if (!processId) return res.status(400).json({ error: 'Missing processId' });
  if (format !== 'json' && format !== 'csv') {
    return res.status(400).json({ error: 'Invalid format. Use format=json or format=csv' });
  }

  const procDir = path.join(outputsDir, processId);
  const metricsPath = pickMetricsPath(procDir, format);

  if (!metricsPath) {
    return res.status(404).json({ error: 'Metrics file not found', processId, format });
  }

  const filename = path.basename(metricsPath);
  res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
  if (format === 'json') res.setHeader('Content-Type', 'application/json');
  if (format === 'csv') res.setHeader('Content-Type', 'text/csv; charset=utf-8');

  return res.sendFile(metricsPath);
});

// ---------------------------
// Rim endpoints
// GET /api/rim/:processId
// POST /api/rim/:processId
// ---------------------------
app.get('/api/rim/:processId', (req, res) => {
  const { processId } = req.params;

  if (!processId || !isSafeProcessId(processId)) {
    return res.status(400).json({ error: 'Invalid processId' });
  }

  const picked = pickAvailableRim(processId);
  if (!picked) {
    return res.status(404).json({
      error: 'No rim available for this processId',
      processId,
      expectedFiles: ['caldera_rim_auto.geojson', 'caldera_rim_edited.geojson'],
    });
  }

  try {
    const geojson = JSON.parse(fs.readFileSync(picked.rimPath, 'utf-8'));
    return res.json({
      processId,
      rim_source: picked.rimSource,
      rim_file: picked.rimFile,
      geojson,
    });
  } catch (e) {
    return res.status(500).json({
      error: 'Failed to read rim GeoJSON',
      processId,
      rim_file: picked.rimFile,
      detail: String(e.message || e),
    });
  }
});

app.post('/api/rim/:processId', (req, res) => {
  const { processId } = req.params;

  if (!processId || !isSafeProcessId(processId)) {
    return res.status(400).json({ error: 'Invalid processId' });
  }

  let feature = null;
  try {
    feature = extractSinglePolygonFeature(req.body);
  } catch (e) {
    return res.status(400).json({
      error: String(e.message || e),
      processId,
    });
  }

  const procDir = getProcessDir(processId);
  const editedPath = getEditedRimPath(processId);

  try {
    fs.mkdirSync(procDir, { recursive: true });
    fs.writeFileSync(editedPath, JSON.stringify(feature, null, 2), 'utf-8');

    return res.status(201).json({
      ok: true,
      processId,
      rim_source: 'edited',
      rim_file: 'caldera_rim_edited.geojson',
      saved: true,
    });
  } catch (e) {
    return res.status(500).json({
      error: 'Failed to save edited rim GeoJSON',
      processId,
      detail: String(e.message || e),
    });
  }
});

app.delete('/api/rim/:processId', (req, res) => {
  const { processId } = req.params;

  if (!processId || !isSafeProcessId(processId)) {
    return res.status(400).json({ error: 'Invalid processId' });
  }

  const procDir = getProcessDir(processId);
  const editedPath = getEditedRimPath(processId);
  const autoPath = getAutoRimPath(processId);

  try {
    if (fs.existsSync(editedPath)) {
      fs.unlinkSync(editedPath);
    }

    if (!fs.existsSync(autoPath)) {
      return res.status(404).json({
        error: 'Auto rim not found for this processId',
        processId,
        expectedFile: 'caldera_rim_auto.geojson',
      });
    }

    return res.json({
      ok: true,
      processId,
      rim_source: 'auto',
      rim_file: 'caldera_rim_auto.geojson',
      reset: true,
      deletedEdited: true,
    });
  } catch (e) {
    return res.status(500).json({
      error: 'Failed to reset rim to auto',
      processId,
      detail: String(e.message || e),
    });
  }
});
// ---------------------------
// PDF Report endpoint
// GET /api/report/:processId?moduleKey=...
// ---------------------------
app.get('/api/report/:processId', (req, res) => {
  const t0 = performance.now();

  const { processId } = req.params;
  const moduleKey = req.query.moduleKey ? String(req.query.moduleKey) : '';

  if (!processId) return res.status(400).json({ error: 'Missing processId' });

  const procDir = path.join(outputsDir, processId);
  const safeModuleKey = (moduleKey || 'unified').replace(/[^a-zA-Z0-9_-]/g, '');

  const pickLatestReportPdf = () => {
    try {
      if (!fs.existsSync(procDir)) return null;
      const files = fs.readdirSync(procDir);
      const candidates = files
        .filter((f) => f.toLowerCase().endsWith('.pdf'))
        .map((f) => ({ f, p: path.join(procDir, f) }))
        .filter((x) => fs.existsSync(x.p));

      if (moduleKey) {
        const pref = `report_${safeModuleKey}_`.toLowerCase();
        const mod = candidates
          .filter((x) => x.f.toLowerCase().startsWith(pref))
          .sort((a, b) => fs.statSync(b.p).mtimeMs - fs.statSync(a.p).mtimeMs);
        if (mod.length) return mod[0].p;
      }

      candidates.sort((a, b) => fs.statSync(b.p).mtimeMs - fs.statSync(a.p).mtimeMs);
      return candidates.length ? candidates[0].p : null;
    } catch (e) {
      console.warn('[WARN] Could not scan report PDFs:', e);
      return null;
    }
  };

  let pdfPath = pickLatestReportPdf();
  if (pdfPath && fs.existsSync(pdfPath)) {
    res.setHeader('Content-Type', 'application/pdf');
    return res.sendFile(pdfPath);
  }

  const reportScriptPath = path.join(__dirname, 'scripts', 'build_pdf_report.py');
  if (!fs.existsSync(reportScriptPath)) {
    return res.status(500).json({
      error: 'Report builder script not found',
      expectedPath: reportScriptPath,
      processId,
    });
  }

  fs.mkdirSync(procDir, { recursive: true });
  console.log(`[INFO][SERVER] report missing -> building (pid=${processId}, moduleKey=${moduleKey || 'N/A'})`);

  const args = moduleKey ? [reportScriptPath, processId, moduleKey] : [reportScriptPath, processId];
  const child = spawnPython({ processId, args });

  let stderrData = '';
  let stdoutData = '';

  child.stdout.on('data', (d) => {
    const s = d.toString();
    stdoutData += s;
    s.split(/\r?\n/).forEach((line) => {
      if (line.trim()) console.log(`[PY PDF ${processId}] ${line.trim()}`);
    });
  });

  child.stderr.on('data', (d) => {
    const s = d.toString();
    stderrData += s;
    s.split(/\r?\n/).forEach((line) => {
      if (line.trim()) console.error(`[PY PDF ${processId} ERR] ${line.trim()}`);
    });
  });

  child.on('close', (code) => {
    const dt = (performance.now() - t0).toFixed(1);
    console.log(`[TIMING][SERVER] /api/report build finished in ${dt} ms (pid=${processId}, code=${code})`);

    if (code !== 0) {
      return res.status(500).json({
        error: 'Failed to build report',
        processId,
        code,
        stderr: String(stderrData || '').trim().slice(0, 4000) || null,
        stdout: String(stdoutData || '').trim().slice(0, 2000) || null,
      });
    }

    pdfPath = pickLatestReportPdf();
    if (!pdfPath || !fs.existsSync(pdfPath)) {
      return res.status(500).json({
        error: 'Report build completed but PDF is missing',
        processId,
        expectedPdfPath: pdfPath,
      });
    }

    res.setHeader('Content-Type', 'application/pdf');
    return res.sendFile(pdfPath);
  });
});

// ---------------------------
// GVP API (Sett 3-4)
// GET /api/gvp/status         -> loader status
// GET /api/gvp/search?q=...   -> up to 10 name matches
// GET /api/gvp/:vnum          -> record + preset hint
// ---------------------------
app.get('/api/gvp/status', (req, res) => {
  return res.json(gvpClient.getStatus());
});

app.get('/api/gvp/search', (req, res) => {
  const q = String(req.query.q || '').trim();
  const limit = Number(req.query.limit) || 10;
  if (!q) return res.json({ query: q, results: [] });
  const results = gvpClient.searchByName(q, limit);
  return res.json({ query: q, limit, count: results.length, results });
});

app.get('/api/gvp/:vnum', (req, res) => {
  const vnum = String(req.params.vnum || '').trim();
  if (!vnum) return res.status(400).json({ error: 'Missing vnum' });

  const rec = gvpClient.resolveByVnum(vnum);
  if (!rec) {
    return res.status(404).json({
      error: 'vnum not found in GVP snapshot',
      vnum,
      hint: 'Verify the volcano number on https://volcano.si.edu/ or refresh the snapshot.',
    });
  }
  const hint = gvpClient.resolvePresetsForType(rec.primary_volcano_type);
  return res.json({
    vnum: rec.vnum,
    name: rec.name,
    primary_volcano_type: rec.primary_volcano_type,
    country: rec.country || '',
    region: rec.region || '',
    subregion: rec.subregion || '',
    latitude: rec.latitude,
    longitude: rec.longitude,
    elevation_m: rec.elevation_m,
    type_verified: rec.type_verified === true,
    preset_hint: hint,
  });
});

// ---------------------------
// Other legacy endpoints (kept)
// ---------------------------
app.post('/shadedRelief', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'generate_shaded_relief.py');
  const absFilePath = path.resolve(file.path);

  const child = spawnPython({ processId: uuidv4(), args: [scriptPath, absFilePath] });

  child.on('close', (code) => {
    if (code !== 0) return res.status(500).json({ error: 'Errore durante la generazione dello Shaded Relief.' });
    return res.json({ message: 'Shaded Relief generated successfully' });
  });
});

app.post('/calculateSlopes', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'generate_slopes.py');
  const absFilePath = path.resolve(file.path);

  const child = spawnPython({ processId: uuidv4(), args: [scriptPath, absFilePath] });

  child.on('close', (code) => {
    if (code !== 0) return res.status(500).json({ error: 'Errore durante la generazione delle due pendenze.' });
    return res.json({ message: 'Slope calculation successful' });
  });
});

app.post('/calculateCurvatures', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'calculate_curvatures.py');
  const absFilePath = path.resolve(file.path);

  const child = spawnPython({ processId: uuidv4(), args: [scriptPath, absFilePath] });

  child.on('close', (code) => {
    if (code !== 0) return res.status(500).json({ error: 'Errore durante la generazione delle curvature.' });
    return res.json({ message: 'Curvature calcolate con successo' });
  });
});

// ---------------------------
// Serve React build (Docker prod) — SPA fallback with exclusions
// ---------------------------
const frontendBuildDir = process.env.FRONTEND_BUILD_DIR ? path.resolve(process.env.FRONTEND_BUILD_DIR) : null;

if (frontendBuildDir && fs.existsSync(frontendBuildDir)) {
  app.use(express.static(frontendBuildDir));

  app.get('*', (req, res, next) => {
    if (
      req.path.startsWith('/outputs') ||
      req.path.startsWith('/process') ||
      req.path.startsWith('/calculateVolume') ||
      req.path.startsWith('/processStatus') ||
      req.path.startsWith('/processComplete') ||
      req.path.startsWith('/shadedRelief') ||
      req.path.startsWith('/calculateSlopes') ||
      req.path.startsWith('/calculateCurvatures') ||
      req.path.startsWith('/analysis') ||
      req.path.startsWith('/api/report') ||
      req.path.startsWith('/api/metrics') ||
      req.path.startsWith('/api/rim') ||
      req.path.startsWith('/api/gvp') ||
      req.path.startsWith('/health')
    ) {
      return next();
    }
    return res.sendFile(path.join(frontendBuildDir, 'index.html'));
  });

  console.log('[SERVER] serving frontend build from:', frontendBuildDir);
} else {
  console.log('[SERVER] frontend build dir not found, API-only mode');
}

app.listen(PORT, () => {
  console.log(`[SERVER] listening on http://localhost:${PORT}`);
  console.log(`[SERVER] PYTHON_CMD=${PY.cmd} PREFIX=${JSON.stringify(PY.prefix)}`);
  console.log(`[SERVER] UPLOADS_DIR=${uploadsDir}`);
  console.log(`[SERVER] OUTPUTS_DIR=${outputsDir}`);
});