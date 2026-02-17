// server.js (docker-ready: env paths + serve React build + python path via env)
const express = require('express');
const cors = require('cors');
const multer = require('multer');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const { v4: uuidv4 } = require('uuid');
const { performance } = require('perf_hooks');

const app = express();
app.use(cors());

// ✅ utile per callback/endpoint futuri che leggono JSON
app.use(express.json({ limit: '10mb' }));

// ---------------------------
// Env / Paths (docker-friendly)
// ---------------------------
const PORT = Number(process.env.PORT || 5000);

// Python executable inside container (or local)
const PYTHON_BIN = process.env.PYTHON_BIN || process.env.PYTHON_PATH || 'python3';

// Use env dirs if provided (compose sets them), fallback to local dev defaults
const uploadsDir = process.env.UPLOADS_DIR
  ? path.resolve(process.env.UPLOADS_DIR)
  : path.join(__dirname, 'uploads');

const outputsDir = process.env.OUTPUTS_DIR
  ? path.resolve(process.env.OUTPUTS_DIR)
  : path.join(__dirname, 'outputs');

// Ensure dirs exist
fs.mkdirSync(uploadsDir, { recursive: true });
fs.mkdirSync(outputsDir, { recursive: true });

// Serve outputs (PNG/PDF/JSON) as static
app.use('/outputs', express.static(outputsDir));

// Optional: log startup config once
console.log('[SERVER] config:', {
  PORT,
  PYTHON_BIN,
  uploadsDir,
  outputsDir,
  FRONTEND_BUILD_DIR: process.env.FRONTEND_BUILD_DIR || '(not set)',
  HEADLESS: process.env.HEADLESS,
  PROJ_LIB: process.env.PROJ_LIB,
  PROJ_DATA: process.env.PROJ_DATA,
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
// - In Docker/Linux do NOT force PROJ_LIB to pyproj. If PROJ_LIB/PROJ_DATA are set (e.g., from host),
//   they can break rasterio/GDAL with "DATABASE.LAYOUT.VERSION.MINOR" mismatch.
// - On Windows we keep them (some setups need pyproj PROJ db to override PostGIS/OSGeo clashes).
// - Also set headless defaults (Agg/offscreen) for reproducible server runs.
// ---------------------------
function buildPyEnv(extraEnv = {}) {
  const env = {
    ...process.env,
    ...extraEnv,
    PYTHONUNBUFFERED: '1',

    // Headless flags (safe defaults)
    HEADLESS: process.env.HEADLESS || '1',
    MPLBACKEND: process.env.MPLBACKEND || 'Agg',
    QT_QPA_PLATFORM: process.env.QT_QPA_PLATFORM || 'offscreen',
  };

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
  return spawn(PYTHON_BIN, args, {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: buildPyEnv({
      PROCESS_ID: processId || '',
      OUTPUTS_DIR: outputsDir,
      UPLOADS_DIR: uploadsDir,
      ...extraEnv,
    }),
  });
}

// ---------------------------
// Helpers
// ---------------------------
function normalizeImages(processId, images) {
  if (!Array.isArray(images)) return [];
  return images.map((img) => {
    // se è stringa, la trasformo
    if (typeof img === 'string') {
      const filename = img;
      return { filename, url: `/outputs/${processId}/${filename}` };
    }

    const filename = img.filename || img.file || img.name;
    const url =
      img.url ||
      img.public_path ||
      (filename ? `/outputs/${processId}/${filename}` : undefined);

    return { ...img, filename, url };
  });
}

// ====== Helper: write meta.json for report titles / traceability ======
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

// ====== Helper: derive moduleKey from request ======
function deriveModuleKey(volumeType, approximationType) {
  if (volumeType === 'circular') {
    if (approximationType === 'approximation1') return 'circular_approx1';
    if (approximationType === 'approximation2') return 'circular_approx2';
  }
  if (volumeType === 'elliptical') {
    if (approximationType === 'approximation1') return 'elliptical_approx1';
    if (approximationType === 'approximation2') return 'elliptical_approx2';
  }
  return '';
}

// ✅ NEW: map moduleKey -> metrics tag naming (per match dei file legacy)
function moduleKeyToMetricsTag(moduleKey) {
  const mk = String(moduleKey || '').toLowerCase().trim();
  if (mk === 'circular_approx1') return 'circ_a1';
  if (mk === 'circular_approx2') return 'circ_a2';
  if (mk === 'elliptical_approx1') return 'ell_a1';
  if (mk === 'elliptical_approx2') return 'ell_a2';
  return '';
}

// ✅ NEW: pick best metrics file in procDir for (moduleKey, ext)
function pickMetricsPath(procDir, moduleKey, ext /* 'csv'|'json' */) {
  try {
    if (!fs.existsSync(procDir)) return null;

    const wantedExt = String(ext || '').toLowerCase();
    const files = fs.readdirSync(procDir);

    const candidates = files
      .filter((f) => f.toLowerCase().endsWith('.' + wantedExt))
      .filter((f) => f.toLowerCase().includes('metrics')) // keep only metrics*
      .map((f) => ({ f, p: path.join(procDir, f) }))
      .filter((x) => fs.existsSync(x.p));

    if (!candidates.length) return null;

    const tag = moduleKeyToMetricsTag(moduleKey);
    if (tag) {
      // prefer metrics_<tag>*.<ext> (es: metrics_circ_a2.json)
      const pref = `metrics_${tag}`.toLowerCase();
      const tagged = candidates
        .filter((x) => x.f.toLowerCase().startsWith(pref))
        .sort((a, b) => fs.statSync(b.p).mtimeMs - fs.statSync(a.p).mtimeMs);
      if (tagged.length) return tagged[0].p;

      // secondary: any file that contains tag
      const contains = candidates
        .filter((x) => x.f.toLowerCase().includes(tag))
        .sort((a, b) => fs.statSync(b.p).mtimeMs - fs.statSync(a.p).mtimeMs);
      if (contains.length) return contains[0].p;
    }

    // fallback: newest metrics*.ext
    candidates.sort((a, b) => fs.statSync(b.p).mtimeMs - fs.statSync(a.p).mtimeMs);
    return candidates[0].p;
  } catch (e) {
    console.warn('[WARN] pickMetricsPath failed:', e);
    return null;
  }
}

// ---------------------------
// Optional: simple healthcheck endpoint (useful for Docker)
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

  // Write meta.json early (used by build_pdf_report.py for titles)
  const procDir = path.join(outputsDir, processId);
  const inputDemName = (file && file.originalname)
    ? String(file.originalname)
    : (originalFileNameRaw ? String(originalFileNameRaw) : `${processId}.tif`);

  writeProcessMeta(procDir, {
    input_dem_name: inputDemName,
    original_file_stem: String(originalFileStem || ''),
    processId: String(processId || ''),
    step: 'complete_dem_analysis',
    updated_at: new Date().toISOString(),
  });

  const absFilePath = path.resolve(file.path);

  const child = spawn(
    PYTHON_BIN,
    [scriptPath, absFilePath, originalFileStem, processId],
    {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: buildPyEnv({
        PROCESS_ID: processId,
        ORIGINAL_FILE_NAME: originalFileNameRaw,
        ORIGINAL_FILE_STEM: originalFileStem,
        UPLOADS_DIR: uploadsDir,
        OUTPUTS_DIR: outputsDir,
      }),
    }
  );

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

    if (code !== 0 && processingStatus[processId]) {
      processingStatus[processId].status = 'failed';
    }
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
// Volume
// ---------------------------
app.post('/calculateVolume', upload.single('demFile'), (req, res) => {
  const t0 = performance.now();

  const file = req.file;
  const volumeType = req.body.volumeType;
  const approximationType = req.body.approximationType;

  if (!file || !volumeType || !approximationType) {
    console.error('[ERROR] Missing required fields.');
    return res.status(400).json({ error: 'Missing required fields.' });
  }

  const originalFileNameRaw =
    (req.body.originalFileName && String(req.body.originalFileName).trim())
      ? String(req.body.originalFileName).trim()
      : file.originalname;

  const originalFileStem = path.parse(originalFileNameRaw).name || 'Unknown';

  // ✅ fondamentale: usa SEMPRE il processId che arriva dall’analisi, se c’è
  const processId = req.body.processId ? String(req.body.processId) : uuidv4();

  // Derive moduleKey (used by meta.json + report title)
  const moduleKey = deriveModuleKey(volumeType, approximationType);

  let scriptPath = null;
  if (volumeType === 'circular') {
    if (approximationType === 'approximation1') {
      scriptPath = path.join(__dirname, 'scripts', 'CircularVolcano_Approx1.py');
    } else if (approximationType === 'approximation2') {
      scriptPath = path.join(__dirname, 'scripts', 'CircularVolcano_Approx2.py');
    }
  } else if (volumeType === 'elliptical') {
    if (approximationType === 'approximation1') {
      scriptPath = path.join(__dirname, 'scripts', 'EllipticalVolcano_Approx1.py');
    } else if (approximationType === 'approximation2') {
      scriptPath = path.join(__dirname, 'scripts', 'EllipticalVolcano_Approx2.py');
    }
  }

  if (!scriptPath) {
    return res.status(400).json({ error: 'Invalid volumeType or approximationType' });
  }

  // Write meta.json (aligned with build_pdf_report.py expectations)
  const procDir = path.join(outputsDir, processId);
  const inputDemName = (file && file.originalname)
    ? String(file.originalname)
    : (originalFileNameRaw ? String(originalFileNameRaw) : `${processId}.tif`);

  writeProcessMeta(procDir, {
    input_dem_name: inputDemName,
    original_file_stem: String(originalFileStem || ''),
    processId: String(processId || ''),
    moduleKey: String(moduleKey || ''), // IMPORTANT: build_pdf_report.py reads meta.moduleKey
    volumeType: String(volumeType || ''),
    approximationType: String(approximationType || ''),
    step: 'calculate_volume',
    updated_at: new Date().toISOString(),
  });

  const absFilePath = path.resolve(file.path);

  const child = spawn(
    PYTHON_BIN,
    // keep argv schema: (path, stem) -> stem ignorabile se lo script non lo usa
    [scriptPath, absFilePath, originalFileStem],
    {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: buildPyEnv({
        PROCESS_ID: processId,
        ORIGINAL_FILE_NAME: originalFileNameRaw,
        ORIGINAL_FILE_STEM: originalFileStem,
        UPLOADS_DIR: uploadsDir,
        OUTPUTS_DIR: outputsDir,
      }),
    }
  );

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

    if (code === 0) {
      // ✅ prova JSON, altrimenti fallback
      try {
        const parsed = JSON.parse(resultData);

        // normalizza sempre processId + status + images.url
        const normalized = {
          processId: parsed.processId || processId,
          status: parsed.status || 'completed',
          result: parsed.result ?? parsed,
          images: normalizeImages(parsed.processId || processId, parsed.images || []),
          ...parsed,
        };

        if (!('result' in parsed)) normalized.result = parsed;

        return res.json(normalized);
      } catch (e) {
        return res.json({ processId, status: 'completed', result: resultData, images: [] });
      }
    }

    return res.status(500).json({ error: 'Error calculating volume' });
  });
});

// ---------------------------
// ✅ NEW: Metrics export endpoint
// GET /api/metrics/:processId?moduleKey=elliptical_approx2&format=json|csv
// - picks the best metrics file in outputs/<pid>/
// - sends it as attachment
// ---------------------------
app.get('/api/metrics/:processId', (req, res) => {
  const { processId } = req.params;
  const moduleKey = req.query.moduleKey ? String(req.query.moduleKey) : '';
  const format = (req.query.format ? String(req.query.format) : 'json').toLowerCase();

  if (!processId) return res.status(400).json({ error: 'Missing processId' });
  if (format !== 'json' && format !== 'csv') {
    return res.status(400).json({ error: 'Invalid format. Use format=json or format=csv' });
  }

  const procDir = path.join(outputsDir, processId);
  const metricsPath = pickMetricsPath(procDir, moduleKey, format);

  if (!metricsPath) {
    return res.status(404).json({
      error: 'Metrics file not found',
      processId,
      moduleKey: moduleKey || null,
      format,
    });
  }

  const filename = path.basename(metricsPath);
  res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
  if (format === 'json') res.setHeader('Content-Type', 'application/json');
  if (format === 'csv') res.setHeader('Content-Type', 'text/csv; charset=utf-8');

  return res.sendFile(metricsPath);
});

// ---------------------------
// ✅ PDF Report endpoint (aligned behavior with old app)
// GET /api/report/:processId?moduleKey=circular_approx1
// ---------------------------
app.get('/api/report/:processId', (req, res) => {
  const t0 = performance.now();

  const { processId } = req.params;
  const moduleKey = req.query.moduleKey ? String(req.query.moduleKey) : (req.query.module ? String(req.query.module) : '');

  if (!processId) {
    return res.status(400).json({ error: 'Missing processId' });
  }

  const procDir = path.join(outputsDir, processId);

  const safeModuleKey = (moduleKey || 'circular_approx1').replace(/[^a-zA-Z0-9_-]/g, '');
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

        const legacy = path.join(procDir, 'report.pdf');
        if (fs.existsSync(legacy)) return legacy;
      } else {
        const legacy = path.join(procDir, 'report.pdf');
        if (fs.existsSync(legacy)) return legacy;
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
    console.error(`[ERROR][SERVER] build_pdf_report.py not found at: ${reportScriptPath}`);
    return res.status(500).json({
      error: 'Report builder script not found',
      expectedPath: reportScriptPath,
      processId,
    });
  }

  fs.mkdirSync(procDir, { recursive: true });

  console.log(`[INFO][SERVER] report missing -> building (pid=${processId}, moduleKey=${moduleKey || 'N/A'})`);

  const args = moduleKey
    ? [reportScriptPath, processId, moduleKey]
    : [reportScriptPath, processId];

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
      const stderrPreview = String(stderrData || '').trim().slice(0, 4000);
      const stdoutPreview = String(stdoutData || '').trim().slice(0, 2000);

      console.error(`[ERROR][SERVER] report build failed (pid=${processId}, code=${code})`);
      if (stderrPreview) console.error(`[ERROR][SERVER] report stderr preview:\n${stderrPreview}`);
      if (stdoutPreview) console.error(`[ERROR][SERVER] report stdout preview:\n${stdoutPreview}`);

      return res.status(500).json({
        error: 'Failed to build report',
        processId,
        code,
        stderr: stderrPreview || null,
        stdout: stdoutPreview || null,
      });
    }

    pdfPath = pickLatestReportPdf();

    if (!pdfPath || !fs.existsSync(pdfPath)) {
      console.error(`[ERROR][SERVER] build finished but PDF not found (pid=${processId})`);
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
// Shaded Relief
// ---------------------------
app.post('/shadedRelief', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'generate_shaded_relief.py');
  const absFilePath = path.resolve(file.path);

  const child = spawnPython({
    processId: uuidv4(),
    args: [scriptPath, absFilePath],
  });

  child.on('close', (code) => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore Shaded Relief. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: 'Errore durante la generazione dello Shaded Relief.' });
    }
    return res.json({ message: 'Shaded Relief generated successfully' });
  });
});

// ---------------------------
// Slopes
// ---------------------------
app.post('/calculateSlopes', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'generate_slopes.py');
  const absFilePath = path.resolve(file.path);

  const child = spawnPython({
    processId: uuidv4(),
    args: [scriptPath, absFilePath],
  });

  child.on('close', (code) => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore slopes. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: 'Errore durante la generazione delle due pendenze.' });
    }
    return res.json({ message: 'Slope calculation successful' });
  });
});

// ---------------------------
// Curvatures
// ---------------------------
app.post('/calculateCurvatures', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'calculate_curvatures.py');
  const absFilePath = path.resolve(file.path);

  const child = spawnPython({
    processId: uuidv4(),
    args: [scriptPath, absFilePath],
  });

  child.on('close', (code) => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore curvature. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: 'Errore durante la generazione delle curvature.' });
    }
    return res.json({ message: 'Curvature calcolate con successo' });
  });
});

// ---------------------------
// Serve React build (Docker prod) — aligned SPA fallback with exclusions
// ---------------------------
const frontendBuildDir = process.env.FRONTEND_BUILD_DIR
  ? path.resolve(process.env.FRONTEND_BUILD_DIR)
  : null;

if (frontendBuildDir && fs.existsSync(frontendBuildDir)) {
  app.use(express.static(frontendBuildDir));

  // Fallback SPA: qualsiasi rotta non-API torna index.html
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
      req.path.startsWith('/api/metrics') || // ✅ NEW: non farla mangiare dalla SPA
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
  console.log(`[SERVER] PYTHON_BIN=${PYTHON_BIN}`);
  console.log(`[SERVER] UPLOADS_DIR=${uploadsDir}`);
  console.log(`[SERVER] OUTPUTS_DIR=${outputsDir}`);
});
