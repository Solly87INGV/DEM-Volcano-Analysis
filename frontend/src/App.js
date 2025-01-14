// App.js
import React, { useState, useEffect } from 'react';
import './App.css';
import Header from './components/Header';
import Description from './components/Description';
import UploadForm from './components/UploadForm';
import VolumeSelection from './components/VolumeSelection';
import Footer from './components/Footer';

function App() {
  const [demFile, setDemFile] = useState(null);
  const [processingSuccess, setProcessingSuccess] = useState(false);
  const [processId, setProcessId] = useState(null);

  const handleBack = () => {
    setProcessingSuccess(false);
    setProcessId(null);
    setDemFile(null);
  };

  useEffect(() => {
    const handleKeyDown = (event) => {
      // Verifica se Ctrl + 0 è stato premuto
      if (event.ctrlKey && event.key === '0') {
        event.preventDefault(); // Previene il comportamento predefinito
        // Puoi aggiungere qui ulteriori logiche se necessario
        console.log('Ctrl + 0 premuto: comportamento prevenuto per stabilità dell\'app.');
      }
    };

    // Aggiungi il listener quando il componente è montato
    window.addEventListener('keydown', handleKeyDown);

    // Rimuovi il listener quando il componente è smontato
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  return (
    <div className="App">
      <Header />
      <div className="content-container">
        {!processingSuccess ? (
          <>
            <Description />
            <UploadForm
              setDemFile={setDemFile}
              setProcessingSuccess={setProcessingSuccess}
              setProcessId={setProcessId}
              processId={processId}
            />
          </>
        ) : (
          <VolumeSelection demFile={demFile} onBack={handleBack} />
        )}
      </div>
      <Footer />
    </div>
  );
}

export default App;
