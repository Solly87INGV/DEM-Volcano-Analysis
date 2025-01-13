// VolumeSelection.js
import React, { useState } from 'react';
import { Box, Typography, Button, IconButton, CircularProgress } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AssessmentIcon from '@mui/icons-material/Assessment'; // Importazione aggiunta
import CardSelection from './CardSelection';
import axios from 'axios';
import './VolumeSelection.css';
// Opzione B: Mantieni l'importazione se utilizzi DownloadResults
import DownloadResults from './DownloadResults';

const VolumeSelection = ({ demFile, onBack }) => {
  const [volumeType, setVolumeType] = useState('');
  const [approximationType, setApproximationType] = useState('');
  const [selectedApproximation, setSelectedApproximation] = useState('');
  const [result, setResult] = useState('');
  const [infoImage, setInfoImage] = useState('');
  const [isLoading, setIsLoading] = useState(false); // Stato di caricamento

  const handleVolumeSelect = (type) => {
    setVolumeType(type);
    setApproximationType('');
    setSelectedApproximation('');
    setInfoImage('');
    setResult(''); // Reset del risultato
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
    setResult(''); // Reset del risultato
  };

  const handleSubmitVolumeCalculation = async () => {
    setIsLoading(true); // Avvia il caricamento
    const formData = new FormData();
    formData.append('demFile', demFile);
    formData.append('volumeType', volumeType);
    formData.append('approximationType', approximationType);

    try {
      const response = await axios.post('http://localhost:5000/calculateVolume', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setResult(response.data.result); // Imposta i risultati direttamente
    } catch (error) {
      console.error('Error calculating volume:', error);
      alert("Si è verificato un errore durante il calcolo del volume.");
    } finally {
      setIsLoading(false); // Termina il caricamento
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
          <Button 
            variant="contained" 
            color="primary" 
            onClick={handleSubmitVolumeCalculation} 
            startIcon={<AssessmentIcon />} 
            disabled={isLoading} // Disabilita il pulsante durante il caricamento
          >
            Calculate Volume
          </Button>
          <Button variant="contained" color="secondary" onClick={onBack} startIcon={<ArrowBackIcon />}>
            Back to Upload
          </Button>
        </Box>
      )}

      {/* Se scegli di mantenere DownloadResults, posizionalo qui */}
      {result && (
        <Box className="results-container">
          <Typography variant="h6">Summary of Results</Typography>
          <Typography sx={{ mt: 1, whiteSpace: 'pre-line' }}>{result}</Typography>

          {/* Se DownloadResults è ancora necessario, usa questo componente */}
          <DownloadResults result={result} className="download-button" />
        </Box>
      )}
    </Box>
  );
};

export default VolumeSelection;
