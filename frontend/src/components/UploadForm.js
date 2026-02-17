// UploadForm.js
import React, { useState, useEffect } from "react";
import axios from "axios";
import { Box, Button, Typography, CircularProgress, Divider } from "@mui/material";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import "./UploadForm.css";

const UploadForm = ({
  setDemFile,
  setProcessingSuccess, // lasciato per compatibilità ma NON lo useremo qui
  setShowAnalysisResults, // ✅ nuovo
  setProcessId,
  processId
}) => {
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadMessage, setUploadMessage] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);

  // ⏱️ stati per i tempi misurati lato client
  const [processWallMs, setProcessWallMs] = useState(null);
  const [pollingWallMs, setPollingWallMs] = useState(null);
  const [serverPhasesProcess, setServerPhasesProcess] = useState(null);
  const [serverPhasesPolling, setServerPhasesPolling] = useState(null);

  useEffect(() => {
    if (!processId) return;

    let timer = null;
    let stopped = false;
    let inFlight = false;

    // base polling + backoff in caso di errore
    let delay = 2000;
    const maxDelay = 15000;

    const tPollStart = performance.now();
    const controller = new AbortController();

    setIsProcessing(true);

    const stop = () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      try {
        controller.abort();
      } catch {
        /* ignore */
      }
    };

    const schedule = (ms) => {
      if (stopped) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(tick, ms);
    };

    const tick = async () => {
      if (stopped) return;

      // evita overlap (inFlight) e non accumula richieste
      if (inFlight) return schedule(delay);

      inFlight = true;
      try {
        const response = await axios.get(`/processStatus/${processId}`, {
          signal: controller.signal,
          timeout: 8000,
          headers: { "Cache-Control": "no-cache" },
        });

        const phaseHeader = response?.headers?.["x-server-phase"];
        if (phaseHeader) {
          try {
            const phasesObj = JSON.parse(phaseHeader);
            setServerPhasesPolling(phasesObj);
            console.info("[PHASES][POLLING]", phasesObj);
          } catch {
            /* ignore */
          }
        }

        const st = response?.data?.status;

        if (st === "completed") {
          stop();

          // ✅ passa alla pagina risultati (pulita)
          setDemFile(selectedFile);
          setShowAnalysisResults(true);

          setIsProcessing(false);

          const tPollEnd = performance.now();
          const dt = tPollEnd - tPollStart;
          setPollingWallMs(dt);
          console.info(`[TIMING] /processStatus polling → completed: ${dt.toFixed(1)} ms`);
          return;
        }

        if (st === "error") {
          stop();
          setUploadMessage("Error during processing.");
          setIsProcessing(false);
          return;
        }

        // successo (ma non finito): reset backoff
        delay = 2000;
      } catch (error) {
        // abort/cleanup: uscita pulita
        if (error?.name === "CanceledError" || error?.name === "AbortError") {
          inFlight = false;
          return;
        }

        console.error("Error checking processing status:", error);

        // NON fermiamo tutto al primo errore: backoff e riprova
        // così evitiamo di piantare la UI per una micro-interruzione.
        delay = Math.min(maxDelay, Math.round(delay * 1.8));

        setUploadMessage("Temporary network issue while checking status. Retrying...");
      } finally {
        inFlight = false;
        schedule(delay);
      }
    };

    // avvio immediato
    tick();

    return () => {
      stop();
    };
  }, [processId, selectedFile, setDemFile, setShowAnalysisResults]);

  const handleFileChange = (event) => {
    const file = event.target.files?.[0];
    if (file) {
      setSelectedFile(file);
      setUploadMessage("");
    }
  };

  const handleDragOver = (event) => {
    event.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setIsDragOver(false);
    const file = event.dataTransfer.files?.[0];
    if (file) {
      setSelectedFile(file);
      setUploadMessage("");
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();

    if (!selectedFile) {
      setUploadMessage("Please select or drag a DEM file before proceeding.");
      return;
    }

    // reset per nuovo run
    setUploadMessage("");
    setProcessWallMs(null);
    setPollingWallMs(null);
    setServerPhasesProcess(null);
    setServerPhasesPolling(null);

    const formData = new FormData();
    formData.append("demFile", selectedFile);
    formData.append("originalFileName", selectedFile.name);

    try {
      const t0 = performance.now();

      const response = await axios.post("/process", formData, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 600000, // 10 min (upload+inizio processing) - evita timeout strani su file grossi
      });

      const t1 = performance.now();
      const dt = t1 - t0;
      setProcessWallMs(dt);
      console.info(`[TIMING] POST /process end-to-end: ${dt.toFixed(1)} ms`);

      const phaseHeader = response?.headers?.["x-server-phase"];
      if (phaseHeader) {
        try {
          const phasesObj = JSON.parse(phaseHeader);
          setServerPhasesProcess(phasesObj);
          console.info("[PHASES][/process]", phasesObj);
        } catch {
          /* ignore */
        }
      }

      if (response.status === 200) {
        const receivedProcessId = response.data.processId;
        setProcessId(receivedProcessId);
      } else {
        setUploadMessage(`Unexpected response status: ${response.status}`);
      }
    } catch (error) {
      if (error.response) {
        console.error("[ERROR] Server responded with error:", error.response.status);
        console.error("[ERROR] Response data:", error.response.data);
        setUploadMessage(`Error: Server responded with status ${error.response.status}`);
      } else if (error.request) {
        console.error("[ERROR] No response received from server.");
        setUploadMessage("Error: No response received from server.");
      } else {
        console.error("[ERROR] Error setting up request:", error.message);
        setUploadMessage(`Error: ${error.message}`);
      }
    }
  };

  return (
    <Box className="upload-form-container">
      <Typography variant="h4" className="form-title">
        Upload your DEM
      </Typography>

      <div
        className={`drag-drop-area ${isDragOver ? "drag-over" : ""}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <input
          type="file"
          id="upload-input"
          className="upload-input"
          accept=".tif,.dem"
          onChange={handleFileChange}
          style={{ display: "none" }}
          disabled={isProcessing}
        />
        <label htmlFor="upload-input" className="drag-drop-label">
          <CloudUploadIcon className="upload-icon" />
          Drag &amp; Drop your DEM file here, or browse file to upload
        </label>
      </div>

      {selectedFile && (
        <div className="selected-file-container">
          <Typography className="selected-file">Selected File: {selectedFile.name}</Typography>
        </div>
      )}

      {uploadMessage && <Typography className="upload-message">{uploadMessage}</Typography>}

      <div className="process-button-container">
        <Button className="process-button" onClick={handleSubmit} disabled={isProcessing || !selectedFile}>
          {isProcessing ? <CircularProgress size={24} /> : "Process DEM file"}
        </Button>
      </div>

      {isProcessing && (
        <Typography className="processing-message">Processing... Please wait.</Typography>
      )}

      {(processWallMs != null || pollingWallMs != null) && (
        <>
          <Divider sx={{ my: 2 }} />
          <Typography variant="h6" sx={{ mb: 1 }}>
            Diagnostics (client-side timings)
          </Typography>

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

          {serverPhasesProcess && (
            <Typography variant="body2" sx={{ mt: 1 }}>
              Server phases (/process): <code>{JSON.stringify(serverPhasesProcess)}</code>
            </Typography>
          )}

          {serverPhasesPolling && (
            <Typography variant="body2" sx={{ mt: 1 }}>
              Server phases (polling): <code>{JSON.stringify(serverPhasesPolling)}</code>
            </Typography>
          )}
        </>
      )}
    </Box>
  );
};

export default UploadForm;
