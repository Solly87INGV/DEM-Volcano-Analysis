// server.js (docker-ready)
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

// ====== Runtime config (Docker-friendly) ======
const PORT = Number(process.env.PORT || 5000);

// Python executable inside container (or local)
// e.g. in Docker: PYTHON_BIN=python3
const PYTHON_BIN = process.env.PYTHON_BIN || process.env.PYTHON_PATH || 'python3';

// Persisted folders
const uploadsDir = process.env.UPLOADS_DIR
  ? path.resolve(process.env.UPLOADS_DIR)
  : path.join(__dirname, 'uploads');

const outputsDir = process.env.OUTPUTS_DIR
  ? path.resolve(process.env.OUTPUTS_DIR)
  : path.join(__dirname, 'outputs');

// Ensure base dirs exist
fs.mkdirSync(uploadsDir, { recursive: true });
fs.mkdirSync(outputsDir, { recursive: true });

// Serve outputs as static (PNG/PDF/JSON) -> http://localhost:5000/outputs/<processId>/...
app.use('/outputs', express.static(outputsDir));

// ====== Serve frontend build (single-port) ======
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
      req.path.startsWith('/analysis') ||          // <-- IMPORTANTISSIMO: non mandare /analysis a index.html
      req.path.startsWith('/health')
    ) {
      return next(); // <-- QUESTA È LA CHIAVE
    }
    return res.sendFile(path.join(frontendBuildDir, 'index.html'));
  });
}

// Optional: simple healthcheck endpoint
app.get('/health', (req, res) => res.json({ ok: true }));

// ====== Multer upload config ======
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, uploadsDir),
  filename: (req, file, cb) => cb(null, file.originalname),
});
const upload = multer({ storage });

// In-memory processing status
const processingStatus = {};

// ====== Helper: spawn python with safe env ======
function spawnPython({ args, processId }) {
  const env = {
    ...process.env,
    PYTHONUNBUFFERED: '1',
    PROCESS_ID: processId || '',
    OUTPUTS_DIR: outputsDir,

    // Headless flags for Docker / CI / servers
    HEADLESS: process.env.HEADLESS || '1',
    MPLBACKEND: process.env.MPLBACKEND || 'Agg',
    QT_QPA_PLATFORM: process.env.QT_QPA_PLATFORM || 'offscreen',
  };

  return spawn(PYTHON_BIN, args, {
    stdio: ['ignore', 'pipe', 'pipe'],
    env,
  });
}

// metti vicino agli altri const
const analysisTplPath = path.join(__dirname, 'views', 'analysis_viewer.html');

// ====== HTML Viewer (template file) ======
app.get('/analysis/:processId', (req, res) => {
  const { processId } = req.params;

  try {
    const tpl = fs.readFileSync(analysisTplPath, 'utf-8');
    const html = tpl.replaceAll('__PROCESS_ID__', processId);

    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    return res.status(200).send(html);
  } catch (e) {
    console.error('[ERROR] cannot read analysis_viewer.html:', e);
    return res.status(500).send('Analysis viewer template not found.');
  }
});

// ========== DEM preprocessing ==========
app.post('/process', upload.single('demFile'), (req, res) => {
  const t0 = performance.now();

  const file = req.file;
  if (!file) {
    console.error('[ERROR] Nessun file caricato.');
    return res.status(400).json({ error: 'Nessun file caricato.' });
  }

  const scriptPath = path.join(__dirname, 'scripts', 'complete_dem_analysis.py');

  const originalFileName = req.body.originalFileName
    ? req.body.originalFileName.split('.')[0]
    : 'Unknown';

  const processId = uuidv4();
  processingStatus[processId] = { status: 'processing' };

  const absFilePath = path.resolve(file.path);

  const child = spawnPython({
    processId,
    args: [scriptPath, absFilePath, originalFileName, processId],
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

    // Non forzare lo status: lo aggiorna /processComplete/:id
    // Però in caso di crash, puoi segnare failed:
    if (code !== 0 && processingStatus[processId]) {
      processingStatus[processId].status = 'failed';
    }
  });

  // Reply immediately
  return res.json({ message: 'Processing started', processId });
});

// Status endpoint (enriched: returns manifest/stats/images when completed)
app.get('/processStatus/:processId', (req, res) => {
  const { processId } = req.params;
  const statusInfo = processingStatus[processId];

  if (!statusInfo) {
    return res.status(404).json({ error: 'Process ID not found' });
  }

  const status = statusInfo.status;
  const payload = { processId, status };

  if (status === 'completed') {
    const procDir = path.join(outputsDir, processId);
    const manifestFs = path.join(procDir, 'analysis_images.json');
    const statsFs = path.join(procDir, 'output_statistics.json');

    payload.outputsBaseUrl = `/outputs/${processId}`;
    payload.manifestUrl = fs.existsSync(manifestFs) ? `/outputs/${processId}/analysis_images.json` : null;
    payload.statsUrl = fs.existsSync(statsFs) ? `/outputs/${processId}/output_statistics.json` : null;

    // Optional: include images with ready-to-use URLs
    if (payload.manifestUrl) {
      try {
        const manifest = JSON.parse(fs.readFileSync(manifestFs, 'utf-8'));
        const imgs = manifest.images || manifest || [];
        payload.images = (Array.isArray(imgs) ? imgs : []).map((img) => {
          // accetta sia stringhe che oggetti
          if (typeof img === 'string') {
            return { filename: img, url: `/outputs/${processId}/${img}` };
          }
          const filename = img.filename || img.file || img.name;
          return {
            ...img,
            url: filename ? `/outputs/${processId}/${filename}` : null,
          };
        });
      } catch (e) {
        payload.images = null;
      }
    }
  }

  return res.json(payload);
});

// Completion callback from Python
app.post('/processComplete/:processId', (req, res) => {
  const { processId } = req.params;
  if (processingStatus[processId]) {
    processingStatus[processId].status = 'completed';
    console.log(`[INFO] process ${processId} marked as completed by Python callback`);
    return res.json({ message: 'Process status updated to completed' });
  }
  return res.status(404).json({ error: 'Process ID not found' });
});

// ========== Volume calculation ==========
app.post('/calculateVolume', upload.single('demFile'), (req, res) => {
  const t0 = performance.now();

  const file = req.file;
  const volumeType = req.body.volumeType;
  const approximationType = req.body.approximationType;

  const originalFileName = req.body.originalFileName
    ? req.body.originalFileName.split('.')[0]
    : 'Unknown';

  const processId = req.body.processId ? String(req.body.processId) : uuidv4();

  if (!file || !volumeType || !approximationType || !originalFileName) {
    console.error('[ERROR] Missing required fields.');
    return res.status(400).json({ error: 'Missing required fields.' });
  }

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

  const absFilePath = path.resolve(file.path);

  const child = spawnPython({
    processId,
    // NOTA: gli script volume leggono PROCESS_ID da env
    args: [scriptPath, absFilePath, originalFileName],
  });

  let stdoutData = '';
  let stderrData = '';

  child.stdout.on('data', (d) => {
    const s = d.toString();
    stdoutData += s;
    s.split(/\r?\n/).forEach((line) => {
      if (line.trim()) console.log(`[PY VOL ${processId}] ${line.trim()}`);
    });
  });

  child.stderr.on('data', (d) => {
    const s = d.toString();
    stderrData += s;
    s.split(/\r?\n/).forEach((line) => {
      if (line.trim()) console.error(`[PY VOL ${processId} ERR] ${line.trim()}`);
    });
  });

  // helper locale (solo qui, patch minimale)
  function tryParseJson(s) {
    try {
      return JSON.parse(String(s || '').trim());
    } catch {
      return null;
    }
  }

  child.on('close', (code) => {
    const dt = (performance.now() - t0).toFixed(1);
    console.log(`[TIMING][SERVER] /calculateVolume finished in ${dt} ms (code=${code})`);

    const parsed = tryParseJson(stdoutData);

    // === SUCCESS ===
    if (code === 0) {
      // Se stdout è JSON valido: arricchisci e rispondi
      if (parsed && typeof parsed === 'object') {
        const pid = parsed.processId || processId;
        const outputsBaseUrl = `/outputs/${pid}`;

        // >>> QUI: mapping images -> url
        const images = Array.isArray(parsed.images) ? parsed.images : [];
        const imagesWithUrl = images.map((img) => {
          const filename =
            (img && typeof img === 'object' && (img.filename || img.file || img.name)) ||
            (typeof img === 'string' ? img : null);

          if (!filename) return { ...(typeof img === 'object' ? img : {}), url: null };

          return {
            ...(typeof img === 'object' ? img : { filename }),
            filename,
            url: `${outputsBaseUrl}/${filename}`,
          };
        });

        return res.json({
          ...parsed,
          processId: pid,
          outputsBaseUrl,
          images: imagesWithUrl,
        });
      }

      // Fallback: stdout non-JSON
      return res.json({ result: stdoutData });
    }

    // === ERROR ===
    // Richiesta: se code!=0 -> 500 + log utile
    const stdoutPreview = String(stdoutData || '').trim().slice(0, 2000);
    const stderrPreview = String(stderrData || '').trim().slice(0, 4000);

    console.error(
      `[ERROR][SERVER] Volume script failed (processId=${processId}, code=${code}).`
    );
    if (stderrPreview) console.error(`[ERROR][SERVER] stderr preview:\n${stderrPreview}`);
    if (stdoutPreview) console.error(`[ERROR][SERVER] stdout preview:\n${stdoutPreview}`);

    // Se stdout era JSON anche in errore, restituiscilo (arricchito) ma con 500
    if (parsed && typeof parsed === 'object') {
      const pid = parsed.processId || processId;
      const outputsBaseUrl = `/outputs/${pid}`;

      const images = Array.isArray(parsed.images) ? parsed.images : [];
      const imagesWithUrl = images.map((img) => {
        const filename =
          (img && typeof img === 'object' && (img.filename || img.file || img.name)) ||
          (typeof img === 'string' ? img : null);

        if (!filename) return { ...(typeof img === 'object' ? img : {}), url: null };

        return {
          ...(typeof img === 'object' ? img : { filename }),
          filename,
          url: `${outputsBaseUrl}/${filename}`,
        };
      });

      return res.status(500).json({
        ...parsed,
        processId: pid,
        outputsBaseUrl,
        images: imagesWithUrl,
        code,
      });
    }

    return res.status(500).json({
      error: 'Error calculating volume',
      processId,
      code,
      stderr: stderrPreview || null,
      stdout: stdoutPreview || null,
    });
  });
});

// ========== Shaded Relief ==========
app.post('/shadedRelief', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'generate_shaded_relief.py');
  const absFilePath = path.resolve(file.path);

  const child = spawnPython({
    processId: uuidv4(),
    args: [scriptPath, absFilePath],
  });

  child.stderr.on('data', (d) => {
    fs.appendFile('error_log.txt', d.toString(), () => {});
  });

  child.on('close', (code) => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore Shaded Relief. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: 'Errore durante la generazione dello Shaded Relief.' });
    }
    return res.json({ message: 'Shaded Relief generated successfully' });
  });
});

// ========== Slopes ==========
app.post('/calculateSlopes', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'generate_slopes.py');
  const absFilePath = path.resolve(file.path);

  const child = spawnPython({
    processId: uuidv4(),
    args: [scriptPath, absFilePath],
  });

  child.stderr.on('data', (d) => {
    fs.appendFile('error_log.txt', d.toString(), () => {});
  });

  child.on('close', (code) => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore slopes. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: 'Errore durante la generazione delle due pendenze.' });
    }
    return res.json({ message: 'Slope calculation successful' });
  });
});

// ========== Curvatures ==========
app.post('/calculateCurvatures', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'calculate_curvatures.py');
  const absFilePath = path.resolve(file.path);

  const child = spawnPython({
    processId: uuidv4(),
    args: [scriptPath, absFilePath],
  });

  let output = '';
  child.stdout.on('data', (d) => (output += d.toString()));
  child.stderr.on('data', (d) => {
    fs.appendFile('error_log.txt', d.toString(), () => {});
  });

  child.on('close', (code) => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore curvature. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: 'Errore durante la generazione delle curvature.' });
    }
    return res.json({ message: 'Curvature calcolate con successo', output });
  });
});

app.listen(PORT, () => {
  console.log(`[SERVER] listening on http://localhost:${PORT}`);
  console.log(`[SERVER] PYTHON_BIN=${PYTHON_BIN}`);
  console.log(`[SERVER] UPLOADS_DIR=${uploadsDir}`);
  console.log(`[SERVER] OUTPUTS_DIR=${outputsDir}`);
});