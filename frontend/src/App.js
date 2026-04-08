// App.js
import React, { useEffect, useState } from "react";
import "./App.css";

import Header from "./components/Header";
import Description from "./components/Description";
import UploadForm from "./components/UploadForm";
import Footer from "./components/Footer";

import AnalysisResultsViewer from "./components/AnalysisResultsViewer";
import VolumeSelection from "./components/VolumeSelection";
import VolumeResultsViewer from "./components/VolumeResultsViewer";

function App() {
  const [demFile, setDemFile] = useState(null);
  const [processId, setProcessId] = useState(null);

  // step: upload -> analysisResults -> volumeSelection -> volumeResults
  const [step, setStep] = useState("upload");

  // contiene il payload finale del volume
  const [volumeRun, setVolumeRun] = useState(null);

  // ---------------------------
  // Helpers: URL sync
  // ---------------------------
  const syncUrl = (nextStep, nextProcessId) => {
    try {
      const qs = new URLSearchParams(window.location.search);

      if (nextStep) qs.set("step", nextStep);
      else qs.delete("step");

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
      localStorage.removeItem("lastVolumeRun");
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
  // Bootstrap da querystring + restore volumeRun
  // ---------------------------
  useEffect(() => {
    try {
      const qs = new URLSearchParams(window.location.search);
      const pid = qs.get("processId");
      const s = qs.get("step");

      if (pid) setProcessId(pid);

      const allowed = new Set(["upload", "analysisResults", "volumeSelection", "volumeResults"]);
      if (s && allowed.has(s)) {
        setStep(s);
      }

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
  // Persist volumeRun
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
  // Sync URL
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
              }}
              processId={processId}
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
              <div style={{ color: "crimson" }}>
                processId mancante: impossibile caricare i risultati.
              </div>
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
            onBack={() => setStep("analysisResults")}
            setStep={(s) => {
              if (s === "results") setStep("volumeResults");
              if (s === "selection") setStep("volumeSelection");
            }}
            setVolumeRun={(run) => {
              setVolumeRun(run);
              if (run?.processId && run.processId !== processId) setProcessId(run.processId);
              setStep("volumeResults");
            }}
          />
        )}

        {step === "volumeResults" && (
          <VolumeResultsViewer
            processId={processId}
            volumeRun={volumeRun}
            onBack={() => setStep("volumeSelection")}
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