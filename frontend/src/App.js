// App.js
import React, { useState, useEffect } from "react";
import "./App.css";

import Header from "./components/Header";
import Description from "./components/Description";
import UploadForm from "./components/UploadForm";
import Footer from "./components/Footer";

import AnalysisResultsViewer from "./components/AnalysisResultsViewer";

// ⬇️ questi due li devi avere in frontend/src/components/
// (puoi usare quelli della vecchia app che mi hai allegato)
import VolumeSelection from "./components/VolumeSelection";
import VolumeResultsViewer from "./components/VolumeResultsViewer";

function App() {
  const [demFile, setDemFile] = useState(null);
  const [processId, setProcessId] = useState(null);

  // step: upload -> analysisResults -> volumeSelection -> volumeResults
  const [step, setStep] = useState("upload");

  // contiene il payload finale del volume (result/images/moduleKey/pdfUrl ecc.)
  const [volumeRun, setVolumeRun] = useState(null);

  const resetAll = () => {
    setDemFile(null);
    setProcessId(null);
    setVolumeRun(null);
    setStep("upload");
  };

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.ctrlKey && event.key === "0") {
        event.preventDefault();
        console.log("Ctrl + 0 premuto: comportamento prevenuto per stabilità dell'app.");
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <div className="App">
      <Header />

      <div className="content-container">
        {step === "upload" && (
          <>
            <Description />
            <UploadForm
              setDemFile={setDemFile}
              setProcessId={setProcessId}
              processId={processId}
              // ✅ quando complete_dem_analysis finisce, vai a "analysisResults"
              setShowAnalysisResults={(v) => {
                if (v) setStep("analysisResults");
              }}
            />
          </>
        )}

        {step === "analysisResults" && (
          <div style={{ maxWidth: 1200, margin: "0 auto", width: "100%", paddingBottom: 80 }}>
            <h2 style={{ marginTop: 0 }}>Complete DEM Analysis — Results</h2>

            {!processId ? (
              <div style={{ color: "crimson" }}>processId mancante: impossibile caricare i risultati.</div>
            ) : (
              <AnalysisResultsViewer processId={processId} />
            )}

            {/* footer azioni */}
            <div
              style={{
                position: "sticky",
                bottom: 0,
                background: "#fff",
                borderTop: "1px solid #ddd",
                padding: "12px 0",
                marginTop: 16,
                display: "flex",
                gap: 12,
                justifyContent: "flex-end",
              }}
            >
              <button onClick={resetAll}>Back to upload</button>
              <button
                onClick={() => setStep("volumeSelection")}
                disabled={!demFile || !processId}
                title={!demFile ? "demFile non pronto" : !processId ? "processId non pronto" : ""}
              >
                Continue
              </button>
            </div>
          </div>
        )}

        {step === "volumeSelection" && (
          <VolumeSelection
            demFile={demFile}
            processId={processId}
            // torna ai risultati DEM
            onBack={() => setStep("analysisResults")}
            // quando parte il volume, puoi opzionalmente impostare step interno
            setStep={(s) => {
              // compat: se la tua vecchia VolumeSelection usa "setStep('results')"
              if (s === "results") setStep("volumeResults");
              if (s === "selection") setStep("volumeSelection");
            }}
            // quando ricevi il JSON dal backend /calculateVolume
            setVolumeRun={(run) => {
              setVolumeRun(run);
              setStep("volumeResults");
            }}
          />
        )}

        {step === "volumeResults" && (
          <VolumeResultsViewer
            processId={processId}
            volumeRun={volumeRun}
            // torna alla selection (per cambiare modulo/approx)
            onBack={() => setStep("volumeSelection")}
            // se vuoi un “Restart” vero
            onRestart={resetAll}
          />
        )}
      </div>

      <Footer />
    </div>
  );
}

export default App;
