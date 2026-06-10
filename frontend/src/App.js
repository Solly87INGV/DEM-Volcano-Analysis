// App.js
import React, { useEffect, useState } from 'react';
import './App.css';
import Header from './components/Header';
import Description from './components/Description';
import UploadForm from './components/UploadForm';
import VolumeSelection from './components/VolumeSelection';
import Footer from './components/Footer';
import AnalysisViewer from './components/AnalysisViewer';
import VolumeResultsViewer from './components/VolumeResultsViewer';

function App() {
  const [demFile, setDemFile] = useState(null);
  const [processId, setProcessId] = useState(null);

  // upload | analysis | volume | volumeResults
  const [step, setStep] = useState('upload');

  // risultato calcolo volume (per viewer)
  const [volumeRun, setVolumeRun] = useState(null);

  const handleBackToUpload = () => {
    setStep('upload');
    setProcessId(null);
    setDemFile(null);
    setVolumeRun(null);
    window.history.replaceState({}, '', '/');
  };

  const handleContinueToVolume = () => {
    setStep('volume');
    const qs = new URLSearchParams(window.location.search);
    qs.set('step', 'volume');
    if (processId) qs.set('processId', processId);
    window.history.replaceState({}, '', '/?' + qs.toString());
  };

  const handleBackToAnalysis = () => {
    setStep('analysis');
    const qs = new URLSearchParams(window.location.search);
    qs.set('step', 'analysis');
    if (processId) qs.set('processId', processId);
    window.history.replaceState({}, '', '/?' + qs.toString());
  };

  const handleBackToVolumeSelection = () => {
    setStep('volume');
    const qs = new URLSearchParams(window.location.search);
    qs.set('step', 'volume');
    if (processId) qs.set('processId', processId);
    window.history.replaceState({}, '', '/?' + qs.toString());
  };

  useEffect(() => {
    // Ctrl+0 guard
    const handleKeyDown = (event) => {
      if (event.ctrlKey && event.key === '0') {
        event.preventDefault();
        console.log("Ctrl + 0 premuto: comportamento prevenuto per stabilità dell'app.");
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    // bootstrap da querystring
    const qs = new URLSearchParams(window.location.search);
    const pid = qs.get('processId');
    const s = qs.get('step');

    if (pid) setProcessId(pid);

    if (s === 'analysis' || s === 'volume' || s === 'upload' || s === 'volumeResults') {
      setStep(s);
    }

    // restore volumeRun da localStorage (utile se refresh su volumeResults)
    try {
      const raw = localStorage.getItem('lastVolumeRun');
      if (raw) setVolumeRun(JSON.parse(raw));
    } catch {
      /* ignore */
    }
  }, []);

  return (
    <div className="App">
      <Header />
      <div className="content-container">
        {step === 'upload' && (
          <>
            <Description />
            <UploadForm
              setDemFile={setDemFile}
              setProcessId={setProcessId}
              processId={processId}
              setStep={setStep}
            />
          </>
        )}

        {step === 'analysis' && (
          <AnalysisViewer
            processId={processId}
            onBack={handleBackToUpload}
            onContinue={handleContinueToVolume}
            requireCompleted={true}
          />
        )}

        {step === 'volume' && (
          <VolumeSelection
            demFile={demFile}
            onBack={handleBackToAnalysis} // ✅ come prima, coerente
            processId={processId}
            setStep={setStep}
            setVolumeRun={setVolumeRun}
          />
        )}

        {step === 'volumeResults' && (
          <VolumeResultsViewer
            processId={processId}
            volumeRun={volumeRun}
            onBack={handleBackToVolumeSelection}
            onBackToUpload={handleBackToUpload}
            requireCompleted={true}
          />
        )}
      </div>
      <Footer />
    </div>
  );
}

export default App;
