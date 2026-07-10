// VolumeSelection.js
import React, { useEffect, useMemo, useState } from 'react';
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
  // ✅ MorphoVolc 2.0: only scenario selection
  const [baseScenario, setBaseScenario] = useState(''); // island | continental
  const [isLoading, setIsLoading] = useState(false);

  // output UI (kept for debug, but we won't render inline anymore)
  const [resultText, setResultText] = useState('');
  const [effectivePid, setEffectivePid] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [manifest, setManifest] = useState(null);

  // ⏱️ timing
  const [calcWallMs, setCalcWallMs] = useState(null);
  const [serverPhasesCalc, setServerPhasesCalc] = useState(null);

  // Sett 3-4: GVP context propagated from step 1 (read-only badge).
  const [gvpContext, setGvpContext] = useState(null);

  useEffect(() => {
    const pid = processId || localStorage.getItem('lastProcessId');
    if (!pid) return;

    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/outputs/${pid}/meta.json`, { cache: 'no-store' });
        if (!r.ok) return;
        const meta = await r.json();
        if (cancelled) return;
        if (meta && (meta.vnum || meta.gvp_type)) {
          setGvpContext({
            vnum: String(meta.vnum || ''),
            gvp_type: String(meta.gvp_type || ''),
            gvp_name: String(meta.gvp_name || ''),
            gvp_resolution: String(meta.gvp_resolution || ''),
          });
        }
      } catch {
        /* silent: meta.json may not exist yet */
      }
    })();

    return () => { cancelled = true; };
  }, [processId]);

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

  const handleScenarioSelect = (scenario) => {
    setBaseScenario(scenario);
    resetOutputs();
  };

  // ✅ Reset selections on this screen
  const handleReset = () => {
    setBaseScenario('');
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

    // Keep demFile upload for backward compatibility.
    // Server will prefer dem_working.tif in OUTPUTS_DIR/<processId>/ when present.
    if (demFile) formData.append('demFile', demFile);

    // ✅ MorphoVolc 2.0: only baseProfile is required for unified model
    formData.append('baseProfile', baseScenario);

    const originalFileName =
      (demFile && demFile.name) ||
      localStorage.getItem('lastOriginalFileName') ||
      'Unknown';
    formData.append('originalFileName', originalFileName);

    const pidFromProp = processId || localStorage.getItem('lastProcessId');
    if (pidFromProp) formData.append('processId', String(pidFromProp));

    // Sett 3-4: propagate vnum for idempotency. Backend also falls back to
    // meta.json, so this is belt-and-suspenders — never harmful.
    if (gvpContext && gvpContext.vnum) {
      formData.append('vnum', gvpContext.vnum);
    }

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

      // moduleKey fallback (unified)
      const fallbackModuleKey = `unified_${baseScenario || 'unknown'}`;

      // costruisci payload compatibile con VolumeResultsViewer
      const run = {
        processId: pid,
        status: vr?.status || response?.data?.status || 'completed',
        moduleKey: response?.data?.moduleKey || vr?.moduleKey || fallbackModuleKey,
        result: vr?.result || response?.data?.result || null,
        images: vr?.images || response?.data?.images || [],
        selection: { baseScenario }, // keep only what exists in 2.0 UI
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
    const hh = metrics?.height_model;
    if (!mm && !vv && !hh) return [];

    const rows = [];
    if (mm) {
      rows.push(['Base area (m²)', mm.A_base_m2]);
      rows.push(['Base perimeter (m)', mm.P_base_m]);
      rows.push(['Base span (m)', mm.D_base_m]);
      rows.push(['Caldera area (m²)', mm.A_caldera_m2]);
      rows.push(['Caldera perimeter (m)', mm.P_caldera_m]);
      rows.push(['Caldera span (m)', mm.D_caldera_m]);
    }
    if (hh) {
      rows.push(['Height used (m)', hh.h_max_m]);
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

  const instructionText = useMemo(() => {
    if (!baseScenario) return "Choose volcano scenario";
    return "Ready to calculate volume";
  }, [baseScenario]);

  const canCalculate = Boolean(baseScenario && !isLoading);

  return (
    <Box className="volume-selection-container">
      <Typography variant="h5" className="success-message">First processing successful</Typography>

      {gvpContext && (
        <Box
          className="gvp-context-badge"
          sx={{
            mx: 'auto', mt: 1, mb: 1,
            px: 2, py: 1,
            maxWidth: 640,
            border: '1px solid #cfd8dc',
            borderRadius: 1,
            background: '#f5faff',
            textAlign: 'left',
          }}
        >
          <Typography variant="body2">
            <b>GVP context:</b>{' '}
            {gvpContext.gvp_name || '(unnamed)'}{' '}
            (vnum <code>{gvpContext.vnum || '-'}</code>
            {gvpContext.gvp_type ? <>, type <code>{gvpContext.gvp_type}</code></> : null})
            {gvpContext.gvp_resolution && gvpContext.gvp_resolution !== 'resolved' ? (
              <> — <i>{gvpContext.gvp_resolution}</i></>
            ) : null}
          </Typography>
        </Box>
      )}

      <Typography variant="h6" className="instruction-message">
        {instructionText}
      </Typography>

      {/* Optional: reset arrow (clears selections) */}
      {baseScenario && (
        <IconButton onClick={handleReset} className="back-arrow" aria-label="Reset selections">
          <ArrowBackIcon />
        </IconButton>
      )}

      {/* ===== SCENARIO (2 cards) ===== */}
      <Box className="main-layout">
        <Box className="card-container">
          <CardSelection
            title="Island volcano (simple base)"
            description="Recommended when the edifice is isolated and the base contour is clear (e.g., island volcanoes)."
            onClick={() => handleScenarioSelect('island')}
            imageSrc="/images/IslandBase.png"
            isSelected={baseScenario === 'island'}
          />
          <CardSelection
            title="Continental volcano (complex base)"
            description="Recommended when the edifice merges with surrounding topography and the base contour is ambiguous."
            onClick={() => handleScenarioSelect('continental')}
            imageSrc="/images/ContinentalBase.png"
            isSelected={baseScenario === 'continental'}
          />
        </Box>
      </Box>

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