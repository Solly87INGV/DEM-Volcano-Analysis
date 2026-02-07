// UploadForm.js
import React, { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { Box, Button, Typography, CircularProgress, Divider } from '@mui/material';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import './UploadForm.css';

const UploadForm = ({
  setDemFile,
  setProcessId,
  setStep, // NECESSARIO per passare a "analysis"
}) => {
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadMessage, setUploadMessage] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);

  // diagnostica tempi
  const [processWallMs, setProcessWallMs] = useState(null);
  const [pollingWallMs, setPollingWallMs] = useState(null);

  // evita doppio submit / doppio polling
  const pollingRef = useRef(null);
  const didStartRef = useRef(false);

  const stopPolling = () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

  useEffect(() => {
    // cleanup se smonti il componente
    return () => stopPolling();
  }, []);

  const goToAnalysisStep = (pid) => {
    // vai allo step analysis automaticamente
    setStep('analysis');

    // aggiorna querystring così è “navigabile” e ripristinabile
    const qs = new URLSearchParams(window.location.search);
    qs.set('processId', pid);
    qs.set('step', 'analysis');
    window.history.replaceState({}, '', '/?' + qs.toString());
  };

  const startPollingUntilCompleted = (pid) => {
    stopPolling();
    const tPollStart = performance.now();

    pollingRef.current = setInterval(async () => {
      try {
        const response = await axios.get(`/processStatus/${pid}`, {
          headers: { 'Cache-Control': 'no-cache' },
        });

        const st = response?.data?.status;

        if (st === 'completed') {
          stopPolling();

          // salva file selezionato (serve dopo in VolumeSelection se ti serve)
          setDemFile(selectedFile);
          setIsProcessing(false);

          // ✅ step interno react (nessun click)
          goToAnalysisStep(pid);

          // timing polling
          const tPollEnd = performance.now();
          setPollingWallMs(tPollEnd - tPollStart);
          return;
        }

        if (st === 'failed' || st === 'error') {
          stopPolling();
          setUploadMessage('Error during processing.');
          setIsProcessing(false);
          didStartRef.current = false; // consenti retry
        }
        // altrimenti continua...
      } catch (err) {
        stopPolling();
        console.error('Error checking processing status:', err);
        setUploadMessage('Error checking processing status.');
        setIsProcessing(false);
        didStartRef.current = false; // consenti retry
      }
    }, 1200); // reattivo
  };

  const handleFileChange = (event) => {
    const file = event.target.files?.[0];
    if (file) {
      setSelectedFile(file);
      setUploadMessage('');
      // consenti nuovo run se cambi file
      didStartRef.current = false;
      stopPolling();
    }
  };

  const handleDragOver = (event) => {
    event.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => setIsDragOver(false);

  const handleDrop = (event) => {
    event.preventDefault();
    setIsDragOver(false);
    const file = event.dataTransfer.files?.[0];
    if (file) {
      setSelectedFile(file);
      setUploadMessage('');
      didStartRef.current = false;
      stopPolling();
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();

    if (isProcessing) return;
    if (!selectedFile) {
      setUploadMessage('Please select or drag a DEM file before proceeding.');
      return;
    }

    // evita doppio start (doppio click)
    if (didStartRef.current) return;
    didStartRef.current = true;

    setIsProcessing(true);
    setUploadMessage('');
    setProcessWallMs(null);
    setPollingWallMs(null);

    const formData = new FormData();
    formData.append('demFile', selectedFile);
    formData.append('originalFileName', selectedFile.name);

    try {
      const t0 = performance.now();

      // relativo: single-port docker ok
      const response = await axios.post('/process', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      const t1 = performance.now();
      setProcessWallMs(t1 - t0);

      if (response.status === 200 && response.data?.processId) {
        const pid = response.data.processId;

        setProcessId(pid);

        // polling fino a completed, poi vai allo step analysis
        startPollingUntilCompleted(pid);
        return;
      }

      setUploadMessage(`Unexpected response status: ${response.status}`);
      setIsProcessing(false);
      didStartRef.current = false;
    } catch (error) {
      if (error.response) {
        console.error('[ERROR] Server responded:', error.response.status, error.response.data);
        setUploadMessage(`Error: Server responded with status ${error.response.status}`);
      } else if (error.request) {
        console.error('[ERROR] No response received.', error.request);
        setUploadMessage('Error: No response received from server.');
      } else {
        console.error('[ERROR] Request setup error:', error.message);
        setUploadMessage(`Error: ${error.message}`);
      }
      setIsProcessing(false);
      didStartRef.current = false;
    }
  };

  return (
    <Box className="upload-form-container">
      <Typography variant="h4" className="form-title">Upload your DEM</Typography>

      <div
        className={`drag-drop-area ${isDragOver ? 'drag-over' : ''}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <input
          type="file"
          id="upload-input"
          className="upload-input"
          accept=".tif,.tiff,.dem"
          onChange={handleFileChange}
          style={{ display: 'none' }}
          disabled={isProcessing}
        />
        <label htmlFor="upload-input" className="drag-drop-label">
          <CloudUploadIcon className="upload-icon" />
          Drag & Drop your DEM file here, or browse file to upload
        </label>
      </div>

      {selectedFile && (
        <div className="selected-file-container">
          <Typography className="selected-file">Selected File: {selectedFile.name}</Typography>
        </div>
      )}

      {uploadMessage && (
        <Typography className="upload-message">{uploadMessage}</Typography>
      )}

      <div className="process-button-container">
        <Button
          className="process-button"
          onClick={handleSubmit}
          disabled={isProcessing || !selectedFile}
        >
          {isProcessing ? <CircularProgress size={24} /> : 'Process DEM file'}
        </Button>
      </div>

      {isProcessing && (
        <Typography className="processing-message">
          Processing... please wait (analysis will open automatically).
        </Typography>
      )}

      {(processWallMs != null || pollingWallMs != null) && (
        <>
          <Divider sx={{ my: 2 }} />
          <Typography variant="h6" sx={{ mb: 1 }}>Diagnostics</Typography>
          {processWallMs != null && (
            <Typography variant="body2">
              POST /process — wall-time: <b>{processWallMs.toFixed(1)} ms</b>
            </Typography>
          )}
          {pollingWallMs != null && (
            <Typography variant="body2">
              /processStatus polling → completed: <b>{pollingWallMs.toFixed(1)} ms</b>
            </Typography>
          )}
        </>
      )}
    </Box>
  );
};

export default UploadForm;