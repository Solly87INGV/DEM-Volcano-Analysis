// App.js
import React, { useState, useEffect } from 'react';
import './App.css';
import Header from './components/Header';
import Description from './components/Description';
import UploadForm from './components/UploadForm';
import VolumeSelection from './components/VolumeSelection';
import Footer from './components/Footer';
import AnalysisResultsViewer from './components/AnalysisResultsViewer';

function App() {
  const [demFile, setDemFile] = useState(null);

  // ✅ fase "finale" (per i moduli volume)
  const [processingSuccess, setProcessingSuccess] = useState(false);

  // ✅ fase intermedia: risultati complete_dem_analysis (pagina pulita)
  const [showAnalysisResults, setShowAnalysisResults] = useState(false);

  const [processId, setProcessId] = useState(null);

  const handleBack = () => {
    setProcessingSuccess(false);
    setShowAnalysisResults(false);
    setProcessId(null);
    setDemFile(null);
  };

  // ✅ torna all’upload (dalla pagina risultati)
  const handleBackToUpload = () => {
    setShowAnalysisResults(false);
    setProcessingSuccess(false);
    setProcessId(null);
    setDemFile(null);
  };

  // ✅ vai avanti (dalla pagina risultati) verso VolumeSelection
  const handleContinueToVolumes = () => {
    // difensivo: se manca demFile, non andare avanti (evita pagina vuota)
    if (!demFile) {
      console.warn('[App] Continue blocked: demFile is null');
      return;
    }
    setShowAnalysisResults(false);
    setProcessingSuccess(true);
  };

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.ctrlKey && event.key === '0') {
        event.preventDefault();
        console.log("Ctrl + 0 premuto: comportamento prevenuto per stabilità dell'app.");
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // ✅ RENDER LOGIC:
  // 1) Upload (default)
  // 2) Results (complete_dem_analysis)
  // 3) VolumeSelection (step successivo)
  return (
    <div className="App">
      <Header />
      <div className="content-container">
        {!showAnalysisResults && !processingSuccess ? (
          <>
            <Description />
            <UploadForm
              setDemFile={setDemFile}
              setProcessingSuccess={setProcessingSuccess} // lo lasciamo, ma in questa fase NON lo usiamo
              setShowAnalysisResults={setShowAnalysisResults} // ✅ nuovo
              setProcessId={setProcessId}
              processId={processId}
            />
          </>
) : showAnalysisResults ? (
  <div style={{ maxWidth: 1200, margin: "0 auto", width: "100%" }}>
    {!processId ? (
      <div style={{ color: "crimson" }}>
        processId mancante: impossibile caricare i risultati.
      </div>
    ) : (
      <AnalysisResultsViewer
        processId={processId}
        onBack={handleBackToUpload}
        onContinue={handleContinueToVolumes}
        requireCompleted={true}
      />
    )}
  </div>
) : (
          <VolumeSelection demFile={demFile} onBack={handleBack} />
        )}
      </div>
      <Footer />
    </div>
  );
}

export default App;
