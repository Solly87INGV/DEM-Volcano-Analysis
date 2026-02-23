// VolumeSelection.js
import React, { useMemo, useState } from 'react';
import { Box, Typography, Button, IconButton, CircularProgress, Divider } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AssessmentIcon from '@mui/icons-material/Assessment';
import CardSelection from './CardSelection';
import axios from 'axios';
import './VolumeSelection.css';

// ✅ DEV+DOCKER: usa path relativi (/calculateVolume, /outputs/...)
// - in DEV: CRA proxy inoltra a http://localhost:5000
// - in DOCKER/PROD: sei già su http://localhost:5000
const API_BASE = '';

const VolumeSelection = ({
  demFile,
  onBack,
  processId,
  setStep,        // ✅ NEW (from App wrapper)
  setVolumeRun,   // ✅ NEW (from App)
}) => {
  const [volumeType, setVolumeType] = useState('');                 // circular | elliptical
  const [baseScenario, setBaseScenario] = useState('');             // island | continental  ✅ NEW (no preselect)
  const [approximationType, setApproximationType] = useState('');   // approximation1 | approximation2
  const [selectedApproximation, setSelectedApproximation] = useState('');
  const [infoImage, setInfoImage] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // output UI (kept for debug, but we won't render inline anymore)
  const [resultText, setResultText] = useState('');
  const [effectivePid, setEffectivePid] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [manifest, setManifest] = useState(null);

  // ⏱️ timing
  const [calcWallMs, setCalcWallMs] = useState(null);
  const [serverPhasesCalc, setServerPhasesCalc] = useState(null);

  const manifestImages = useMemo(() => {
    const imgs = manifest?.images || [];
    return imgs
      .map(it => ({
        filename: it.filename,
        publicPath: it.public_path,
        titles: it.titles || [],
        descriptions: it.descriptions || []
      }))
      .filter(it => !!it.publicPath);
  }, [manifest]);

  const resetOutputs = () => {
    setResultText('');
    setEffectivePid(null);
    setMetrics(null);
    setManifest(null);
    setCalcWallMs(null);
    setServerPhasesCalc(null);
  };

  // ✅ single-screen: geometry selection does NOT move to another screen
  const handleVolumeSelect = (type) => {
    setVolumeType(type);

    // reset dependent selections
    setBaseScenario('');
    setApproximationType('');
    setSelectedApproximation('');
    setInfoImage('');

    resetOutputs();
  };

  const handleBaseScenarioSelect = (scenario) => {
    setBaseScenario(scenario);
    resetOutputs();
  };

  const handleApproximationSelect = (type) => {
    setApproximationType(type);
    setSelectedApproximation(type);

    if (volumeType === 'circular') {
      setInfoImage(type === 'approximation1' ? '/images/1_Circ.png' : '/images/2_Circ.png');
    } else if (volumeType === 'elliptical') {
      setInfoImage(type === 'approximation1' ? '/images/1_Ellipt.png' : '/images/2_Ellipt.png');
    }
    resetOutputs();
  };

  // ✅ Back arrow now means: clear selections on this screen (not "go to previous screen")
  const handleBack = () => {
    setVolumeType('');
    setBaseScenario('');
    setApproximationType('');
    setSelectedApproximation('');
    setInfoImage('');
    resetOutputs();
  };

  async function fetchJsonOrNull(url) {
    try {
      const r = await fetch(url, { cache: 'no-store' });
      if (!r.ok) return null;
      return await r.json();
    } catch {
      return null;
    }
  }

  const handleSubmitVolumeCalculation = async () => {
    setIsLoading(true);
    resetOutputs();

    const formData = new FormData();
    formData.append('demFile', demFile);
    formData.append('volumeType', volumeType);
    formData.append('approximationType', approximationType);

    // ⚠️ NOTE: baseScenario is UI-only for this step (no backend changes yet).
    // When we do the backend bridge, we'll append:
    // formData.append('baseScenario', baseScenario);

    const originalFileName =
      (demFile && demFile.name) ||
      localStorage.getItem('lastOriginalFileName') ||
      'Unknown';
    formData.append('originalFileName', originalFileName);

    const pidFromProp = processId || localStorage.getItem('lastProcessId');
    if (pidFromProp) formData.append('processId', String(pidFromProp));

    try {
      const t0 = performance.now();
      const response = await axios.post(`${API_BASE}/calculateVolume`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const t1 = performance.now();
      setCalcWallMs(t1 - t0);

      const phaseHeader = response?.headers?.['x-server-phase'];
      if (phaseHeader) {
        try {
          setServerPhasesCalc(JSON.parse(phaseHeader));
        } catch { /* ignore */ }
      }

      // 🔑 PID definitivo
      const pid = response?.data?.processId || pidFromProp || null;
      setEffectivePid(pid);

      // fallback: se server ti ha dato result string, mettila
      const serverResult = response?.data?.result;
      if (typeof serverResult === 'string') setResultText(serverResult);

      // (debug fetch: metrics/manifest) — non serve alla UI finale, ma utile se vuoi controllare
      if (pid) {
        const metricsUrl = `${API_BASE}/outputs/${pid}/metrics.json`;
        const manifestUrl = `${API_BASE}/outputs/${pid}/analysis_images.json`;
        const [m, man] = await Promise.all([
          fetchJsonOrNull(metricsUrl),
          fetchJsonOrNull(manifestUrl),
        ]);
        setMetrics(m);
        setManifest(man);
      }

      // ✅ CARICA volume_results.json (se presente) e passa tutto al viewer separato
      let vr = null;
      if (pid) {
        const vrUrl = `${API_BASE}/outputs/${pid}/volume_results.json`;
        vr = await fetchJsonOrNull(vrUrl);
      }

      // costruisci payload compatibile con VolumeResultsViewer
      const run = {
        processId: pid,
        status: vr?.status || response?.data?.status || 'completed',
        moduleKey: vr?.moduleKey || response?.data?.moduleKey || `${volumeType}_${approximationType}`,
        // VolumeResultsViewer gestisce result sia come "result" che come "root object"
        result: vr?.result || vr?.numbers || response?.data?.result || null,
        images: vr?.images || response?.data?.images || [],
        // (optional: keep UI selection metadata locally if you ever want it in results)
        selection: { volumeType, baseScenario, approximationType },
      };

      if (setVolumeRun) setVolumeRun(run);
      if (setStep) setStep('results'); // App wrapper -> volumeResults
      return;
    } catch (error) {
      console.error('Error calculating volume:', error);
      alert('Si è verificato un errore durante il calcolo del volume.');
    } finally {
      setIsLoading(false);
    }
  };

  // piccola tabella “umana” da metrics.json (debug only, we won't render inline)
  const quickRows = useMemo(() => {
    const mm = metrics?.morphometrics;
    const vv = metrics?.volumes;
    if (!mm && !vv) return [];

    const rows = [];
    if (mm) {
      rows.push(['Base area (m²)', mm.A_base_m2]);
      rows.push(['Base perimeter (m)', mm.P_base_m]);
      rows.push(['Base diameter (m)', mm.D_base_m]);
      rows.push(['Caldera area (m²)', mm.A_caldera_m2]);
      rows.push(['Caldera perimeter (m)', mm.P_caldera_m]);
      rows.push(['Caldera diameter (m)', mm.D_caldera_m]);
      rows.push(['Height used (m)', mm.h_max_m]);
    }
    if (vv) {
      rows.push(['Total volume (m³)', vv.V_total_m3]);
      rows.push(['Caldera volume (m³)', vv.V_caldera_m3]);
      rows.push(['Effective volume (m³)', vv.V_effective_m3]);
    }
    return rows.filter(r => r[1] !== undefined && r[1] !== null);
  }, [metrics]);

  // ✅ inline results OFF: questa schermata deve solo selezionare e lanciare il run
  const SHOW_INLINE_RESULTS = false;

  // ✅ instruction text based on progress
  const instructionText = useMemo(() => {
    if (!volumeType) return "Choose geometry";
    if (!baseScenario) return "Choose base scenario";
    if (!approximationType) return "Choose approximation method";
    return "Ready to calculate volume";
  }, [volumeType, baseScenario, approximationType]);

  const canCalculate = Boolean(volumeType && baseScenario && approximationType && !isLoading);

  return (
    <Box className="volume-selection-container">
      <Typography variant="h5" className="success-message">First processing successful</Typography>

      <Typography variant="h6" className="instruction-message">
        {instructionText}
      </Typography>

      {/* Optional: reset arrow (clears selections) */}
      {(volumeType || baseScenario || approximationType) && (
        <IconButton onClick={handleBack} className="back-arrow" aria-label="Reset selections">
          <ArrowBackIcon />
        </IconButton>
      )}

      {/* ===== GEOMETRY (cards) ===== */}
      <Box className="main-layout">
        <Box className="card-container">
          <CardSelection
            title="Circular Volcano"
            description="Volcanic edifice with an approximately circular base and caldera, modeled from DEM-derived contours to estimate edifice and caldera volumes."
            onClick={() => handleVolumeSelect('circular')}
            imageSrc="/images/Circular.png"
            isSelected={volumeType === 'circular'}
          />
          <CardSelection
            title="Elliptical Volcano"
            description="Volcanic edifice with an elongated (elliptical) base and caldera, modeled from DEM-derived contours to estimate edifice and caldera volumes."
            onClick={() => handleVolumeSelect('elliptical')}
            imageSrc="/images/Elliptical.png"
            isSelected={volumeType === 'elliptical'}
          />
        </Box>

        {/* right-side info image (based on approximation) */}
        {infoImage && (
          <Box className="info-image-container">
            <img src={infoImage} alt="Description" />
          </Box>
        )}
      </Box>

      {/* ===== BASE SCENARIO (cards) ===== */}
      {volumeType && (
        <>
          <Divider sx={{ my: 2 }} />

          <Typography variant="h6" className="instruction-message" sx={{ mb: 1 }}>
            Base scenario
          </Typography>

          <Box className="card-container">
            <CardSelection
              title="Island / Simple base"
              description="Recommended when the edifice is isolated and the base contour is clear (e.g., island volcanoes)."
              onClick={() => handleBaseScenarioSelect('island')}
              imageSrc="/images/IslandBase.png"
              isSelected={baseScenario === 'island'}
            />
            <CardSelection
              title="Continental / Complex base"
              description="Recommended when the edifice merges with surrounding topography and the base contour is ambiguous."
              onClick={() => handleBaseScenarioSelect('continental')}
              imageSrc="/images/ContinentalBase.png"
              isSelected={baseScenario === 'continental'}
            />
          </Box>
        </>
      )}

      {/* ===== APPROXIMATION (cards) — kept for now (backend still uses it) ===== */}
      {volumeType && baseScenario && (
        <>
          <Divider sx={{ my: 2 }} />

          <Typography variant="h6" className="instruction-message" sx={{ mb: 1 }}>
            Approximation method
          </Typography>

          <Box className="main-layout">
            <Box className="card-container">
              {volumeType === 'circular' ? (
                <>
                  <CardSelection
                    title="Circular Approximation 1"
                    description="Rim→DEM depth integration"
                    onClick={() => handleApproximationSelect('approximation1')}
                    imageSrc="/images/Approx1Circ.png"
                    isSelected={selectedApproximation === 'approximation1'}
                  />
                  <CardSelection
                    title="Circular Approximation 2"
                    description="Frustum"
                    onClick={() => handleApproximationSelect('approximation2')}
                    imageSrc="/images/Approx2Circ.png"
                    isSelected={selectedApproximation === 'approximation2'}
                  />
                </>
              ) : (
                <>
                  <CardSelection
                    title="Elliptical Approximation 1"
                    description="Elliptical frustum + DEM semi-ellipsoid"
                    onClick={() => handleApproximationSelect('approximation1')}
                    imageSrc="/images/Ellipt1Approx.png"
                    isSelected={selectedApproximation === 'approximation1'}
                  />
                  <CardSelection
                    title="Elliptical Approximation 2"
                    description="Elliptical frustum + DEM cylindrical caldera"
                    onClick={() => handleApproximationSelect('approximation2')}
                    imageSrc="/images/Ellipt2Approx.png"
                    isSelected={selectedApproximation === 'approximation2'}
                  />
                </>
              )}
            </Box>
          </Box>
        </>
      )}

      {/* ===== ACTIONS ===== */}
      <Box className="button-group" sx={{ mt: 2 }}>
        {isLoading && (
          <Box display="flex" justifyContent="center" alignItems="center" mb={2}>
            <CircularProgress />
          </Box>
        )}

        <Button
          variant="contained"
          color="secondary"
          onClick={onBack}
          startIcon={<ArrowBackIcon />}
          disabled={isLoading}
        >
          Back to Upload
        </Button>

        <Button
          variant="contained"
          color="primary"
          onClick={handleSubmitVolumeCalculation}
          startIcon={<AssessmentIcon />}
          disabled={!canCalculate}
        >
          Calculate Volume
        </Button>
      </Box>

      {SHOW_INLINE_RESULTS && (resultText || metrics || manifest || calcWallMs != null || serverPhasesCalc) && (
        <>
          <Divider sx={{ my: 2 }} />

          <Box className="results-container">
            <Typography variant="h6">Results (debug)</Typography>

            {effectivePid && (
              <Typography variant="body2" sx={{ mt: 1 }}>
                processId: <b>{effectivePid}</b>
              </Typography>
            )}

            {resultText && (
              <Typography sx={{ mt: 1, whiteSpace: 'pre-line' }}>
                {resultText}
              </Typography>
            )}

            {metrics && (
              <Box sx={{ mt: 2 }}>
                <Typography variant="h6">Numerical results (from metrics.json)</Typography>
                <Box component="table" sx={{ width: '100%', mt: 1, borderCollapse: 'collapse' }}>
                  <tbody>
                    {quickRows.map(([k, v]) => (
                      <tr key={k}>
                        <td style={{ padding: '6px 8px', borderBottom: '1px solid #ddd' }}>{k}</td>
                        <td style={{ padding: '6px 8px', borderBottom: '1px solid #ddd', textAlign: 'right' }}>
                          {typeof v === 'number' ? v.toLocaleString() : String(v)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Box>
              </Box>
            )}

            {manifestImages.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Typography variant="h6">Images (from analysis_images.json)</Typography>
                <Box sx={{ display: 'flex', gap: 2, overflowX: 'auto', py: 1 }}>
                  {manifestImages.map((it) => (
                    <Box key={it.publicPath} sx={{ minWidth: 360 }}>
                      <img
                        src={`${API_BASE}${it.publicPath}`}
                        alt={it.filename}
                        style={{ width: '100%', borderRadius: 8 }}
                      />
                      <Typography variant="body2" sx={{ mt: 1 }}>
                        <b>{it.titles?.join(' | ') || it.filename}</b>
                      </Typography>
                      {it.descriptions?.[0] && (
                        <Typography variant="caption">{it.descriptions[0]}</Typography>
                      )}
                    </Box>
                  ))}
                </Box>
              </Box>
            )}
          </Box>

          {(calcWallMs != null || serverPhasesCalc) && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="h6" sx={{ mb: 1 }}>Diagnostics</Typography>
              {calcWallMs != null && (
                <Typography variant="body2">
                  POST /calculateVolume — wall-time: <b>{calcWallMs.toFixed(1)} ms</b>
                </Typography>
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