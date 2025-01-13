const express = require('express');
const cors = require('cors');
const multer = require('multer');
const { spawn } = require('child_process');
const path = require('path');
const { v4: uuidv4 } = require('uuid'); // Importa uuidv4

const app = express();
app.use(cors());

// Configurazione di multer per gestire l'upload
const storage = multer.diskStorage({
    destination: 'uploads/',
    filename: function (req, file, cb) {
        cb(null, file.originalname); // Preserva il nome originale del file
    }
});

const upload = multer({ storage: storage });

// Percorso Python (modificalo se necessario)
const pythonPath = 'C:\\Users\\solly\\AppData\\Local\\Programs\\Python\\Python312\\python.exe';

// Oggetto per tenere traccia dello stato delle elaborazioni
const processingStatus = {};

// Primo processamento del DEM usando lo script completo
app.post('/process', upload.single('demFile'), (req, res) => {
    const file = req.file;
    if (!file) {
        console.error("[ERROR] Nessun file caricato.");
        return res.status(400).json({ error: "Nessun file caricato." });
    }

    const scriptPath = path.join(__dirname, 'scripts', 'complete_dem_analysis.py');
    const originalFileName = req.body.originalFileName
        ? req.body.originalFileName.split('.')[0]
        : "Unknown";

    // Genera un identificatore univoco per questa elaborazione
    const processId = uuidv4(); // Usa uuidv4() per generare un UUID
    // Salva lo stato iniziale dell'elaborazione
    processingStatus[processId] = { status: 'processing' };

    // Avvia il processo Python, passando processId come argomento
    const pythonProcess = spawn(pythonPath, [scriptPath, file.path, originalFileName, processId], {
        detached: true,
        stdio: 'ignore' // Ignora gli output per evitare che il processo rimanga collegato
    });

    // Disconnette il processo figlio
    pythonProcess.unref();

    // Rispondi immediatamente al client con il processId
    res.json({ message: 'Processing started', processId });

    // Non aspettiamo più l'evento 'close' del processo Python per aggiornare lo stato
});

// Endpoint per controllare lo stato dell'elaborazione
app.get('/processStatus/:processId', (req, res) => {
    const { processId } = req.params;
    const statusInfo = processingStatus[processId];

    if (statusInfo) {
        res.json({ status: statusInfo.status });
    } else {
        res.status(404).json({ error: 'Process ID not found' });
    }
});

// Endpoint per ricevere la notifica di completamento dal processo Python
app.post('/processComplete/:processId', (req, res) => {
    const { processId } = req.params;
    if (processingStatus[processId]) {
        processingStatus[processId].status = 'completed';
        res.json({ message: 'Process status updated to completed' });
    } else {
        res.status(404).json({ error: 'Process ID not found' });
    }
});

// Nuovo endpoint per il calcolo del volume
app.post('/calculateVolume', upload.single('demFile'), (req, res) => {
    const file = req.file;
    const volumeType = req.body.volumeType;
    const approximationType = req.body.approximationType;
    const originalFileName = req.body.originalFileName
        ? req.body.originalFileName.split('.')[0]
        : "Unknown";

    // Messaggi di debug

    if (!file || !volumeType || !approximationType || !originalFileName) {
        console.error("[ERROR] Missing required fields.");
        return res.status(400).json({ error: "Missing required fields." });
    }

    // Decide which script to run based on volumeType and approximationType
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
    } else {
        return res.status(400).json({ error: 'Invalid volumeType or approximationType' });
    }

    // Spawn the Python process
    const pythonProcess = spawn(pythonPath, [scriptPath, file.path, originalFileName]);

    let resultData = '';
    pythonProcess.stdout.on('data', (data) => {
        resultData += data.toString();
    });

    pythonProcess.stderr.on('data', (data) => {
        console.error("[ERROR]", data.toString());
    });

    pythonProcess.on('close', (code) => {
        if (code === 0) {
            res.json({ result: resultData });
        } else {
            res.status(500).json({ error: 'Error calculating volume' });
        }
    });
});

// Nuovo endpoint per la visualizzazione dello Shaded Relief
app.post('/shadedRelief', upload.single('demFile'), (req, res) => {
    const file = req.file;
    if (!file) {
        return res.status(400).send('Nessun file caricato.');
    }

    // Percorso allo script Python per la generazione dello shaded relief
    const scriptPath = path.join(__dirname, 'scripts', 'generate_shaded_relief.py');  // Presupponendo che lo script sia salvato in questa posizione

    // Esegui lo script Python con il file caricato
    const pythonProcess = spawn(pythonPath, [scriptPath, file.path]);

    let output = '';
    pythonProcess.stdout.on('data', (data) => {
        output += data.toString();
    });

    pythonProcess.stderr.on('data', (data) => {
        fs.appendFile('error_log.txt', data.toString(), (err) => {
            if (err) {
                console.error('Errore durante la scrittura del log:', err);
            }
        });
    });

    pythonProcess.on('close', (code) => {
        if (code !== 0) {
            fs.appendFile('error_log.txt', `Errore durante la generazione dello Shaded Relief. Codice di uscita: ${code}\n`, (err) => {
                if (err) {
                    console.error('Errore durante la scrittura del log:', err);
                }
            });
            return res.status(500).json({ error: "Errore durante la generazione dello Shaded Relief." });
        }

        // Restituisci l'immagine generata o un messaggio di conferma
        res.json({ message: 'Shaded Relief generated successfully' });
    });
});

app.listen(5000, () => {
});

// Nuovo endpoint per la visualizzazione delle due pendenze (slope1 e slope2)
app.post('/calculateSlopes', upload.single('demFile'), (req, res) => {
    const file = req.file;
    if (!file) {
        return res.status(400).send('Nessun file caricato.');
    }

    // Percorso allo script Python per la generazione delle due pendenze
    const scriptPath = path.join(__dirname, 'scripts', 'generate_slopes.py');  // Presupponendo che lo script sia salvato in questa posizione

    // Esegui lo script Python con il file caricato
    const pythonProcess = spawn(pythonPath, [scriptPath, file.path]);

    let output = '';
    pythonProcess.stdout.on('data', (data) => {
        output += data.toString();
    });

    pythonProcess.stderr.on('data', (data) => {
        fs.appendFile('error_log.txt', data.toString(), (err) => {
            if (err) {
                console.error('Errore durante la scrittura del log:', err);
            }
        });
    });

    pythonProcess.on('close', (code) => {
        if (code !== 0) {
            fs.appendFile('error_log.txt', `Errore durante la generazione delle due pendenze. Codice di uscita: ${code}\n`, (err) => {
                if (err) {
                    console.error('Errore durante la scrittura del log:', err);
                }
            });
            return res.status(500).json({ error: "Errore durante la generazione delle due pendenze." });
        }

        res.json({ message: 'Slope calculation successful' });
    });
});

// Endpoint per il calcolo e visualizzazione delle curvature
app.post('/calculateCurvatures', upload.single('demFile'), (req, res) => {
    const file = req.file;
    if (!file) {
        return res.status(400).send('Nessun file caricato.');
    }

    // Percorso allo script Python per la generazione delle curvature
    const scriptPath = path.join(__dirname, 'scripts', 'calculate_curvatures.py');

    // Esegui lo script Python con il file caricato
    const pythonProcess = spawn(pythonPath, [scriptPath, file.path]);

    let output = '';
    pythonProcess.stdout.on('data', (data) => {
        output += data.toString();
    });

    pythonProcess.stderr.on('data', (data) => {
        fs.appendFile('error_log.txt', data.toString(), (err) => {
            if (err) {
                console.error('Errore durante la scrittura del log:', err);
            }
        });
    });

    pythonProcess.on('close', (code) => {
        if (code !== 0) {
            fs.appendFile('error_log.txt', `Errore durante la generazione delle curvature. Codice di uscita: ${code}\n`, (err) => {
                if (err) {
                    console.error('Errore durante la scrittura del log:', err);
                }
            });
            return res.status(500).json({ error: "Errore durante la generazione delle curvature." });
        }

        res.json({ message: 'Curvature calcolate con successo', output });
    });
});

