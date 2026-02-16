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

// Serve outputs (PNG/PDF) as static
app.use('/outputs', express.static(outputsDir));

// Python executable (docker sets PYTHON_BIN=/opt/venv/bin/python)
const pythonPath = process.env.PYTHON_BIN || 'python3';

// Optional: log startup config once
console.log('[SERVER] config:', {
  PORT,
  pythonPath,
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
function buildPyEnv(extraEnv = {}) {
  const env = { ...process.env, ...extraEnv, PYTHONUNBUFFERED: '1' };

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

function pickPdfPath(procDir, moduleKey) {
  if (!fs.existsSync(procDir)) return null;

  const files = fs.readdirSync(procDir).filter((f) => f.toLowerCase().endsWith('.pdf'));

  if (files.length === 0) return null;

  // 1) se NON c'è moduleKey: preferisci report.pdf
  if (!moduleKey) {
    const reportPdf = files.find((f) => f.toLowerCase() === 'report.pdf');
    if (reportPdf) return path.join(procDir, reportPdf);
  }

  // 2) se c'è moduleKey: preferisci report_{moduleKey}_*.pdf
  if (moduleKey) {
    const pref = `report_${String(moduleKey)}_`.toLowerCase();
    const candidate = files.find((f) => f.toLowerCase().startsWith(pref));
    if (candidate) return path.join(procDir, candidate);
  }

  // 3) fallback: ultimo PDF modificato
  let best = null;
  let bestMtime = -1;
  for (const f of files) {
    const full = path.join(procDir, f);
    const st = fs.statSync(full);
    if (st.mtimeMs > bestMtime) {
      bestMtime = st.mtimeMs;
      best = full;
    }
  }
  return best;
}

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

  const absFilePath = path.resolve(file.path);

  const child = spawn(
    pythonPath,
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
  });

  res.json({ message: 'Processing started', processId });
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

  let scriptPath;
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

  const absFilePath = path.resolve(file.path);

  const child = spawn(
    pythonPath,
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

        // normalizza sempre processId + status + images.url (compat VolumeResultsViewer vecchio)
        const normalized = {
          processId: parsed.processId || processId,
          status: parsed.status || 'completed',
          result: parsed.result ?? parsed,
          images: normalizeImages(parsed.processId || processId, parsed.images || []),
          ...parsed,
        };

        // se parsed ha già result/images bene; altrimenti result diventa parsed
        if (!('result' in parsed)) normalized.result = parsed;

        return res.json(normalized);
      } catch (e) {
        return res.json({ processId, status: 'completed', result: resultData, images: [] });
      }
    }

    res.status(500).json({ error: 'Error calculating volume' });
  });
});

// ---------------------------
// ✅ PDF Report endpoint (serve per VolumeResultsViewer vecchio)
// GET /api/report/:processId?moduleKey=circular_approx1 (o simile)
// ---------------------------
app.get('/api/report/:processId', (req, res) => {
  const { processId } = req.params;
  const moduleKey = req.query.moduleKey || req.query.module || '';

  const scriptPath = path.join(__dirname, 'scripts', 'build_pdf_report.py');
  const procDir = path.join(outputsDir, processId);

  // lancia python che genera il PDF dentro outputs/{processId}
  const args = [scriptPath, processId];
  if (moduleKey) args.push(String(moduleKey));

  const child = spawn(pythonPath, args, {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: buildPyEnv({
      PROCESS_ID: processId,
      OUTPUTS_DIR: outputsDir,
      UPLOADS_DIR: uploadsDir,
    }),
  });

  let stderr = '';
  child.stdout.on('data', (d) => {
    const line = d.toString().trim();
    if (line) console.log(`[PY REPORT ${processId}] ${line}`);
  });
  child.stderr.on('data', (d) => {
    const line = d.toString();
    stderr += line;
    const t = line.trim();
    if (t) console.error(`[PY REPORT ${processId} ERR] ${t}`);
  });

  child.on('close', (code) => {
    if (code !== 0) {
      return res.status(500).json({
        error: 'Error generating PDF report',
        details: stderr ? String(stderr).slice(0, 2000) : `exit code ${code}`,
      });
    }

    const pdfPath = pickPdfPath(procDir, moduleKey);
    if (!pdfPath) {
      return res.status(404).json({ error: 'PDF not found after generation' });
    }

    const filename = path.basename(pdfPath);
    res.setHeader('Content-Type', 'application/pdf');
    // inline così si apre in tab; download se preferisci: attachment
    res.setHeader('Content-Disposition', `inline; filename="${filename}"`);

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

  const child = spawn(pythonPath, [scriptPath, absFilePath], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: buildPyEnv({ OUTPUTS_DIR: outputsDir, UPLOADS_DIR: uploadsDir }),
  });

  child.on('close', (code) => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore Shaded Relief. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: 'Errore durante la generazione dello Shaded Relief.' });
    }
    res.json({ message: 'Shaded Relief generated successfully' });
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

  const child = spawn(pythonPath, [scriptPath, absFilePath], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: buildPyEnv({ OUTPUTS_DIR: outputsDir, UPLOADS_DIR: uploadsDir }),
  });

  child.on('close', (code) => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore slopes. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: 'Errore durante la generazione delle due pendenze.' });
    }
    res.json({ message: 'Slope calculation successful' });
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

  const child = spawn(pythonPath, [scriptPath, absFilePath], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: buildPyEnv({ OUTPUTS_DIR: outputsDir, UPLOADS_DIR: uploadsDir }),
  });

  child.on('close', (code) => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore curvature. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: 'Errore durante la generazione delle curvature.' });
    }
    res.json({ message: 'Curvature calcolate con successo' });
  });
});

// ---------------------------
// Serve React build (Docker prod)
// ---------------------------
const frontendBuildDir = process.env.FRONTEND_BUILD_DIR
  ? path.resolve(process.env.FRONTEND_BUILD_DIR)
  : null;

if (frontendBuildDir && fs.existsSync(frontendBuildDir)) {
  app.use(express.static(frontendBuildDir));

  // Catch-all -> index.html (for React Router or deep links)
  app.get('*', (req, res) => {
    res.sendFile(path.join(frontendBuildDir, 'index.html'));
  });

  console.log('[SERVER] serving frontend build from:', frontendBuildDir);
} else {
  console.log('[SERVER] frontend build dir not found, API-only mode');
}

app.listen(PORT, () => {
  console.log(`[SERVER] listening on http://localhost:${PORT}`);
});
