// UploadForm.js
import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Box, Button, Typography, CircularProgress } from '@mui/material';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import './UploadForm.css';

const UploadForm = ({ setDemFile, setProcessingSuccess, setProcessId, processId }) => {
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadMessage, setUploadMessage] = useState('');
  const [isDragOver, setIsDragOver] = useState(false); // Stato per il drag-and-drop
  const [isProcessing, setIsProcessing] = useState(false);

  useEffect(() => {
    let pollingInterval = null;

    if (processId) {
      setIsProcessing(true); // Inizia lo stato di elaborazione

      // Start polling
      pollingInterval = setInterval(async () => {
        try {
          const response = await axios.get(`http://localhost:5000/processStatus/${processId}`);
          if (response.data.status === 'completed') {
            clearInterval(pollingInterval);
            setProcessingSuccess(true);
            setDemFile(selectedFile);
            setIsProcessing(false);
          } else if (response.data.status === 'error') {
            clearInterval(pollingInterval);
            setUploadMessage('Error during processing.');
            setIsProcessing(false);
          }
          // else, status is 'processing', continua ad attendere
        } catch (error) {
          console.error('Error checking processing status:', error);
          clearInterval(pollingInterval);
          setUploadMessage('Error checking processing status.');
          setIsProcessing(false);
        }
      }, 5000); // Poll every 5 seconds
    }

    return () => {
      if (pollingInterval) {
        clearInterval(pollingInterval);
      }
    };
  }, [processId, selectedFile, setDemFile, setProcessingSuccess]);

  const handleFileChange = (event) => {
    const file = event.target.files[0];
    if (file) {
      setSelectedFile(file);
      setUploadMessage('');
    }
  };

  const handleDragOver = (event) => {
    event.preventDefault();
    setIsDragOver(true); // Attiva lo stato di drag
  };

  const handleDragLeave = () => {
    setIsDragOver(false); // Disattiva lo stato di drag
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setIsDragOver(false); // Disattiva lo stato di drag
    const file = event.dataTransfer.files[0];
    if (file) {
      setSelectedFile(file);
      setUploadMessage('');
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!selectedFile) {
      setUploadMessage('Please select or drag a DEM file before proceeding.');
      return;
    }

    const formData = new FormData();
    formData.append('demFile', selectedFile);
    formData.append('originalFileName', selectedFile.name); // Aggiungi il nome originale del file

    setUploadMessage('');
    try {
      const response = await axios.post('http://localhost:5000/process', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      if (response.status === 200) {
        const receivedProcessId = response.data.processId;
        setProcessId(receivedProcessId); // Imposta processId nello stato del componente genitore
      } else {
        console.warn('[DEBUG] Unexpected response status:', response.status);
        setUploadMessage(`Unexpected response status: ${response.status}`);
      }
    } catch (error) {
      if (error.response) {
        console.error('[ERROR] Server responded with error:', error.response.status);
        console.error('[ERROR] Response data:', error.response.data);
        setUploadMessage(`Error: Server responded with status ${error.response.status}`);
      } else if (error.request) {
        console.error('[ERROR] No response received from server.');
        console.error('[DEBUG] Request data:', error.request);
        setUploadMessage('Error: No response received from server.');
      } else {
        console.error('[ERROR] Error setting up request:', error.message);
        setUploadMessage(`Error: ${error.message}`);
      }
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
          accept=".tif,.dem"
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
          Processing... Please wait.
        </Typography>
      )}
    </Box>
  );
};

export default UploadForm;
