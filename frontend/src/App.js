// App.js
import React, { useEffect, useState } from "react";
import "./App.css";

import Header from "./components/Header";
import Description from "./components/Description";
import UploadForm from "./components/UploadForm";
import Footer from "./components/Footer";

import AnalysisResultsViewer from "./components/AnalysisResultsViewer";

// ⬇️ questi due li devi avere in frontend/src/components/
import VolumeSelection from "./components/VolumeSelection";
import VolumeResultsViewer from "./components/VolumeResultsViewer";

function App() {
  const [demFile, setDemFile] = useState(null);
  const [processId, setProcessId] = useState(null);

  // step: upload -> analysisResults -> volumeSelection -> volumeResults
  const [step, setStep] = useState("upload");

  // contiene il payload finale del volume (result/images/moduleKey/pdfUrl ecc.)
  const [volumeRun, setVolumeRun] = useState(null);

  // ---------------------------
  // Helpers: URL sync (come OLD)
  // ---------------------------
  const syncUrl = (nextStep, nextProcessId) => {
    try {
      const qs = new URLSearchParams(window.location.search);

      // step
      if (nextStep) qs.set("step", nextStep);
      else qs.delete("step");

      // processId
      if (nextProcessId) qs.set("processId", nextProcessId);
      else qs.delete("processId");

      window.history.replaceState({}, "", "/?" + qs.toString());
    } catch {
      /* ignore */
    }
  };

  const resetAll = () => {
    setDemFile(null);
    setProcessId(null);
    setVolumeRun(null);
    setStep("upload");
    try {
      window.history.replaceState({}, "", "/");
    } catch {
      /* ignore */
    }
  };

  // Ctrl+0 guard
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

  // ---------------------------
  // Bootstrap da querystring + restore volumeRun (come OLD)
  // ---------------------------
  useEffect(() => {
    try {
      const qs = new URLSearchParams(window.location.search);
      const pid = qs.get("processId");
      const s = qs.get("step");

      if (pid) setProcessId(pid);

      // accetta solo step noti
      const allowed = new Set(["upload", "analysisResults", "volumeSelection", "volumeResults"]);
      if (s && allowed.has(s)) {
        setStep(s);
      }

      // restore volumeRun da localStorage (utile se refresh su volumeResults)
      try {
        const raw = localStorage.getItem("lastVolumeRun");
        if (raw) setVolumeRun(JSON.parse(raw));
      } catch {
        /* ignore */
      }
    } catch {
      /* ignore */
    }
  }, []);

  // ---------------------------
  // Persist volumeRun (come OLD)
  // ---------------------------
  useEffect(() => {
    if (!volumeRun) return;
    try {
      localStorage.setItem("lastVolumeRun", JSON.stringify(volumeRun));
    } catch {
      /* ignore */
    }
  }, [volumeRun]);

  // ---------------------------
  // Sync URL ad ogni cambio step/processId
  // ---------------------------
  useEffect(() => {
    syncUrl(step, processId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, processId]);

  return (
    <div className="App">
      <Header />

      <div className="content-container">
        {step === "upload" && (
          <>
            <Description />
            <UploadForm
              setDemFile={setDemFile}
              setProcessId={(pid) => {
                setProcessId(pid);
                // non forzo step qui: lo fa la tua logica quando finisce l'analisi
              }}
              processId={processId}
              // ✅ quando complete_dem_analysis finisce, vai a "analysisResults"
              setShowAnalysisResults={(v) => {
                if (v) setStep("analysisResults");
              }}
            />
          </>
        )}

        {step === "analysisResults" && (
          <div style={{ maxWidth: 1200, margin: "0 auto", width: "100%" }}>
            <h2 style={{ marginTop: 0 }}>Complete DEM Analysis — Results</h2>

            {!processId ? (
              <div style={{ color: "crimson" }}>processId mancante: impossibile caricare i risultati.</div>
            ) : (
              <AnalysisResultsViewer
                processId={processId}
                onBack={resetAll}
                onContinue={() => setStep("volumeSelection")}
                requireCompleted={true}
              />
            )}
          </div>
        )}

        {step === "volumeSelection" && (
          <VolumeSelection
            demFile={demFile}
            processId={processId}
            // torna ai risultati DEM
            onBack={() => setStep("analysisResults")}
            // compat: se la tua VolumeSelection usa "setStep('results')"
            setStep={(s) => {
              if (s === "results") setStep("volumeResults");
              if (s === "selection") setStep("volumeSelection");
            }}
            // quando ricevi il JSON dal backend /calculateVolume
            setVolumeRun={(run) => {
              setVolumeRun(run);
              // se il backend ti restituisce un pid diverso, allinealo
              if (run?.processId && run.processId !== processId) setProcessId(run.processId);
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
            // ✅ questo serve davvero al componente per mostrare "Back to Upload"
            onBackToUpload={resetAll}
            requireCompleted={true}
          />
        )}
      </div>

      <Footer />
    </div>
  );
}

export default App;