// App.js
import React, { useState } from 'react';
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
