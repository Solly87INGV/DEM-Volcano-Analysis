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
});

// ---------------------------
// Multer storage (preserve name)
// ---------------------------
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, uploadsDir),
  filename: (req, file, cb) => cb(null, file.originalname),
});
const upload = multer({ storage });

// Processing status map
const processingStatus = {};

// ---------------------------
// DEM preprocess
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
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        PROCESS_ID: processId,
        ORIGINAL_FILE_NAME: originalFileNameRaw,
        ORIGINAL_FILE_STEM: originalFileStem,
        // ensure python can discover where outputs should go (if scripts use env)
        UPLOADS_DIR: uploadsDir,
        OUTPUTS_DIR: outputsDir,
      },
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
    // keep argv schema: (path, stem)
    [scriptPath, absFilePath, originalFileStem],
    {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        PROCESS_ID: processId,
        ORIGINAL_FILE_NAME: originalFileNameRaw,
        ORIGINAL_FILE_STEM: originalFileStem,
        UPLOADS_DIR: uploadsDir,
        OUTPUTS_DIR: outputsDir,
      },
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
      try {
        const parsed = JSON.parse(resultData);
        if (parsed && (parsed.result || parsed.images)) return res.json(parsed);
      } catch (e) {
        // not json -> fallback
      }
      return res.json({ result: resultData });
    }
    res.status(500).json({ error: 'Error calculating volume' });
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
    env: { ...process.env, PYTHONUNBUFFERED: '1', OUTPUTS_DIR: outputsDir, UPLOADS_DIR: uploadsDir },
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
    env: { ...process.env, PYTHONUNBUFFERED: '1', OUTPUTS_DIR: outputsDir, UPLOADS_DIR: uploadsDir },
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
    env: { ...process.env, PYTHONUNBUFFERED: '1', OUTPUTS_DIR: outputsDir, UPLOADS_DIR: uploadsDir },
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
