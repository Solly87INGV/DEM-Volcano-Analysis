// server.js (aggiornato)
const express = require('express');
const cors = require('cors');
const multer = require('multer');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs'); // <-- aggiunto
const { v4: uuidv4 } = require('uuid');
const { performance } = require('perf_hooks'); // <-- per timing lato server

const app = express();
app.use(cors());

// Assicurati che esistano le directory fondamentali
const uploadsDir = path.join(__dirname, 'uploads');
const outputsDir = path.join(__dirname, 'outputs');
fs.mkdirSync(uploadsDir, { recursive: true });
fs.mkdirSync(outputsDir, { recursive: true });

// Servi anche gli output (PNG/PDF) come statici
app.use('/outputs', express.static(outputsDir));

// Configurazione di multer per gestire l'upload (usa percorso assoluto)
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, uploadsDir),
  filename: (req, file, cb) => cb(null, file.originalname) // preserva il nome originale
});
const upload = multer({ storage });

// Percorso Python (adatta se necessario)
const pythonPath = 'C:\\Users\\solly\\AppData\\Local\\Programs\\Python\\Python312\\python.exe';

// Stato elaborazioni
const processingStatus = {};

// ========== Primo processamento del DEM ==========
app.post('/process', upload.single('demFile'), (req, res) => {
  const t0 = performance.now();
  const file = req.file;
  if (!file) {
    console.error("[ERROR] Nessun file caricato.");
    return res.status(400).json({ error: "Nessun file caricato." });
  }

  const scriptPath = path.join(__dirname, 'scripts', 'complete_dem_analysis.py');
  const originalFileName = req.body.originalFileName
    ? req.body.originalFileName.split('.')[0]
    : "Unknown";

  // id run
  const processId = uuidv4();
  processingStatus[processId] = { status: 'processing' };

  // Path assoluto del file caricato
  const absFilePath = path.resolve(file.path);

  // AVVIO PYTHON **NON** DETACHED + piping degli stream
  const child = spawn(
    pythonPath,
    [scriptPath, absFilePath, originalFileName, processId],
    {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1', PROCESS_ID: processId } // <-- passa PROCESS_ID
    }
  );

  // Log stdout/stderr Python nella console di Node
  child.stdout.on('data', d => {
    const line = d.toString().trim();
    if (line) console.log(`[PY ${processId}] ${line}`);
  });

  child.stderr.on('data', d => {
    const line = d.toString().trim();
    if (line) console.error(`[PY ${processId} ERR] ${line}`);
  });

  child.on('close', code => {
    const dt = (performance.now() - t0).toFixed(1);
    console.log(`[PY ${processId}] exited with code ${code} (server elapsed ${dt} ms)`);
    // NB: lo stato "completed" lo aggiorna comunque lo script via POST /processComplete/:id
    // Qui NON lo forziamo, così resti fedele al tuo flusso attuale.
  });

  // Rispondi subito con l'id
  res.json({ message: 'Processing started', processId });
});

// Stato
app.get('/processStatus/:processId', (req, res) => {
  const { processId } = req.params;
  const statusInfo = processingStatus[processId];
  if (statusInfo) {
    res.json({ status: statusInfo.status });
  } else {
    res.status(404).json({ error: 'Process ID not found' });
  }
});

// Notifica di completamento inviata dallo script Python
app.post('/processComplete/:processId', (req, res) => {
  const { processId } = req.params;
  if (processingStatus[processId]) {
    processingStatus[processId].status = 'completed';
    console.log(`[INFO] process ${processId} marked as completed by Python callback`);
    res.json({ message: 'Process status updated to completed' });
  } else {
    res.status(404).json({ error: 'Process ID not found' });
  }
});

// ========== Calcolo volume ==========
app.post('/calculateVolume', upload.single('demFile'), (req, res) => {
  const t0 = performance.now();

  const file = req.file;
  const volumeType = req.body.volumeType;
  const approximationType = req.body.approximationType;
  const originalFileName = req.body.originalFileName
    ? req.body.originalFileName.split('.')[0]
    : "Unknown";

  // Riusa il processId della fase precedente se lo ricevi dal client,
  // altrimenti creane uno (retro-compatibile).
  const processId = req.body.processId ? String(req.body.processId) : uuidv4();

  if (!file || !volumeType || !approximationType || !originalFileName) {
    console.error("[ERROR] Missing required fields.");
    return res.status(400).json({ error: "Missing required fields." });
  }

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
  if (!scriptPath) return res.status(400).json({ error: 'Invalid volumeType or approximationType' });

  // Path assoluto del file caricato
  const absFilePath = path.resolve(file.path);

  const child = spawn(
    pythonPath,
    [scriptPath, absFilePath, originalFileName],
    {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1', PROCESS_ID: processId } // <-- passa PROCESS_ID
    }
  );

  let resultData = '';

  child.stdout.on('data', d => {
    const s = d.toString();
    resultData += s;
    // Mostra comunque i log in console
    s.split(/\r?\n/).forEach(line => {
      if (line.trim()) console.log(`[PY VOL ${processId}] ${line.trim()}`);
    });
  });

  child.stderr.on('data', d => {
    const s = d.toString();
    s.split(/\r?\n/).forEach(line => {
      if (line.trim()) console.error(`[PY VOL ${processId} ERR] ${line.trim()}`);
    });
  });

  child.on('close', code => {
    const dt = (performance.now() - t0).toFixed(1);
    console.log(`[TIMING][SERVER] /calculateVolume finished in ${dt} ms (code=${code})`);
    if (code === 0) {
      // Tenta di interpretare l'output come JSON strutturato { result, images: [...] }
      try {
        const parsed = JSON.parse(resultData);
        // Se mancano campi attesi, mantieni retro-compatibilità
        if (parsed && (parsed.result || parsed.images)) {
          return res.json(parsed);
        }
      } catch (e) {
        // non è JSON -> fallback
      }
      // Fallback: vecchio comportamento testuale
      return res.json({ result: resultData });
    } else {
      res.status(500).json({ error: 'Error calculating volume' });
    }
  });
});

// ========== Shaded Relief ==========
app.post('/shadedRelief', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'generate_shaded_relief.py');
  const absFilePath = path.resolve(file.path);
  const child = spawn(pythonPath, [scriptPath, absFilePath], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' }
  });

  let output = '';
  child.stdout.on('data', d => output += d.toString());
  child.stderr.on('data', d => fs.appendFile('error_log.txt', d.toString(), () => {}));
  child.on('close', code => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore Shaded Relief. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: "Errore durante la generazione dello Shaded Relief." });
    }
    res.json({ message: 'Shaded Relief generated successfully' });
  });
});

// ========== Slopes ==========
app.post('/calculateSlopes', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'generate_slopes.py');
  const absFilePath = path.resolve(file.path);
  const child = spawn(pythonPath, [scriptPath, absFilePath], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' }
  });

  let output = '';
  child.stdout.on('data', d => output += d.toString());
  child.stderr.on('data', d => fs.appendFile('error_log.txt', d.toString(), () => {}));
  child.on('close', code => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore slopes. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: "Errore durante la generazione delle due pendenze." });
    }
    res.json({ message: 'Slope calculation successful' });
  });
});

// ========== Curvatures ==========
app.post('/calculateCurvatures', upload.single('demFile'), (req, res) => {
  const file = req.file;
  if (!file) return res.status(400).send('Nessun file caricato.');

  const scriptPath = path.join(__dirname, 'scripts', 'calculate_curvatures.py');
  const absFilePath = path.resolve(file.path);
  const child = spawn(pythonPath, [scriptPath, absFilePath], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' }
  });

  let output = '';
  child.stdout.on('data', d => output += d.toString());
  child.stderr.on('data', d => fs.appendFile('error_log.txt', d.toString(), () => {}));
  child.on('close', code => {
    if (code !== 0) {
      fs.appendFile('error_log.txt', `Errore curvature. Exit: ${code}\n`, () => {});
      return res.status(500).json({ error: "Errore durante la generazione delle curvature." });
    }
    res.json({ message: 'Curvature calcolate con successo', output });
  });
});

app.listen(5000, () => {
  console.log('[SERVER] listening on http://localhost:5000');
});
