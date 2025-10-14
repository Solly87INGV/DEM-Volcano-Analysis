// VolumeSelection.js
import React, { useState } from 'react';
import { Box, Typography, Button, IconButton, CircularProgress, Divider } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AssessmentIcon from '@mui/icons-material/Assessment';
import CardSelection from './CardSelection';
import axios from 'axios';
import './VolumeSelection.css';
import DownloadResults from './DownloadResults';

const VolumeSelection = ({ demFile, onBack }) => {
  const [volumeType, setVolumeType] = useState('');
  const [approximationType, setApproximationType] = useState('');
  const [selectedApproximation, setSelectedApproximation] = useState('');
  const [result, setResult] = useState('');
  const [infoImage, setInfoImage] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // ⏱️ stato per timing calcolo volume
  const [calcWallMs, setCalcWallMs] = useState(null);
  const [serverPhasesCalc, setServerPhasesCalc] = useState(null);

  const handleVolumeSelect = (type) => {
    setVolumeType(type);
    setApproximationType('');
    setSelectedApproximation('');
    setInfoImage('');
    setResult('');
    setCalcWallMs(null);
    setServerPhasesCalc(null);
  };

  const handleApproximationSelect = (type) => {
    setApproximationType(type);
    setSelectedApproximation(type);
    if (volumeType === 'circular') {
      setInfoImage(type === 'approximation1' ? '/images/1_Circ.png' : '/images/2_Circ.png');
    } else if (volumeType === 'elliptical') {
      setInfoImage(type === 'approximation1' ? '/images/1_Ellipt.png' : '/images/2_Ellipt.png');
    }
  };

  const handleBack = () => {
    setVolumeType('');
    setSelectedApproximation('');
    setInfoImage('');
    setResult('');
    setCalcWallMs(null);
    setServerPhasesCalc(null);
  };

  const handleSubmitVolumeCalculation = async () => {
    setIsLoading(true);
    const formData = new FormData();
    formData.append('demFile', demFile);
    formData.append('volumeType', volumeType);
    formData.append('approximationType', approximationType);

    try {
      // ⏱️ start cronometro /calculateVolume
      const t0 = performance.now();

      const response = await axios.post('http://localhost:5000/calculateVolume', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      // ⏱️ stop cronometro
      const t1 = performance.now();
      const dt = t1 - t0;
      setCalcWallMs(dt);
      console.info(`[TIMING] POST /calculateVolume end-to-end: ${dt.toFixed(1)} ms`);

      // prova a leggere fasi server (se il backend le fornisce come header)
      const phaseHeader = response?.headers?.['x-server-phase'];
      if (phaseHeader) {
        try {
          const phasesObj = JSON.parse(phaseHeader);
          setServerPhasesCalc(phasesObj);
          console.info('[PHASES][/calculateVolume]', phasesObj);
        } catch {
          /* header non JSON */
        }
      }

      setResult(response.data.result);
    } catch (error) {
      console.error('Error calculating volume:', error);
      alert('Si è verificato un errore durante il calcolo del volume.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Box className="volume-selection-container">
      <Typography variant="h5" className="success-message">First processing successful</Typography>

      <Typography variant="h6" className="instruction-message">
        {volumeType ? "Choose approximation method" : "Choose the type of volcano"}
      </Typography>

      {volumeType ? (
        <>
          <IconButton onClick={handleBack} className="back-arrow">
            <ArrowBackIcon />
          </IconButton>
          <Box className="main-layout">
            <Box className="card-container">
              {volumeType === 'circular' ? (
                <>
                  <CardSelection
                    title="Circular Approximation 1"
                    description="semi-sphere"
                    onClick={() => handleApproximationSelect('approximation1')}
                    imageSrc="/images/Approx1Circ.png"
                    isSelected={selectedApproximation === 'approximation1'}
                  />
                  <CardSelection
                    title="Circular Approximation 2"
                    description="cylinder"
                    onClick={() => handleApproximationSelect('approximation2')}
                    imageSrc="/images/Approx2Circ.png"
                    isSelected={selectedApproximation === 'approximation2'}
                  />
                </>
              ) : (
                <>
                  <CardSelection
                    title="Elliptical Approximation 1"
                    description="semi-ellipsoid of rotation"
                    onClick={() => handleApproximationSelect('approximation1')}
                    imageSrc="/images/Ellipt1Approx.png"
                    isSelected={selectedApproximation === 'approximation1'}
                  />
                  <CardSelection
                    title="Elliptical Approximation 2"
                    description="cylinder with elliptical bases"
                    onClick={() => handleApproximationSelect('approximation2')}
                    imageSrc="/images/Ellipt2Approx.png"
                    isSelected={selectedApproximation === 'approximation2'}
                  />
                </>
              )}
            </Box>
            {infoImage && (
              <Box className="info-image-container">
                <img src={infoImage} alt="Description" />
              </Box>
            )}
          </Box>
        </>
      ) : (
        <Box className="card-container">
          <CardSelection
            title="Circular Volcano"
            description="A volcanic edifice with a base and caldera both of approximately circular shape is approximated to a truncated cone."
            onClick={() => handleVolumeSelect('circular')}
            imageSrc="/images/Circular.png"
          />
          <CardSelection
            title="Elliptical Volcano"
            description="A volcanic edifice with a base and caldera both of approximately elliptical shape is approximated to a truncated cone with elliptical bases."
            onClick={() => handleVolumeSelect('elliptical')}
            imageSrc="/images/Elliptical.png"
          />
        </Box>
      )}

      {volumeType && approximationType && (
        <Box className="button-group">
          {isLoading && (
            <Box display="flex" justifyContent="center" alignItems="center" mb={2}>
              <CircularProgress />
            </Box>
          )}
          {/* 🔁 Ordine invertito: Back prima, Calculate dopo */}
          <Button variant="contained" color="secondary" onClick={onBack} startIcon={<ArrowBackIcon />}>
            Back to Upload
          </Button>
          <Button
            variant="contained"
            color="primary"
            onClick={handleSubmitVolumeCalculation}
            startIcon={<AssessmentIcon />}
            disabled={isLoading}
          >
            Calculate Volume
          </Button>
        </Box>
      )}

      {/* Risultati + diagnostica tempi */}
      {(result || calcWallMs != null || serverPhasesCalc) && (
        <>
          <Divider sx={{ my: 2 }} />
          {result && (
            <Box className="results-container">
              <Typography variant="h6">Summary of Results</Typography>
              <Typography sx={{ mt: 1, whiteSpace: 'pre-line' }}>{result}</Typography>
              <DownloadResults result={result} className="download-button" />
            </Box>
          )}
          {(calcWallMs != null || serverPhasesCalc) && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="h6" sx={{ mb: 1 }}>Diagnostics (client-side timings)</Typography>
              {calcWallMs != null && (
                <Typography variant="body2">POST /calculateVolume — wall-time: <b>{calcWallMs.toFixed(1)} ms</b></Typography>
              )}
              {serverPhasesCalc && (
                <Typography variant="body2" sx={{ mt: 1 }}>
                  Server phases (/calculateVolume): <code>{JSON.stringify(serverPhasesCalc)}</code>
                </Typography>
              )}
            </Box>
          )}
        </>
      )}
    </Box>
  );
};

export default VolumeSelection;
