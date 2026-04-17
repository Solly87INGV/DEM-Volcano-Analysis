// VolumeSelection.js
import React, { useMemo, useState } from 'react';
import {
  Box,
  Typography,
  Button,
  CircularProgress,
  Divider,
  Paper,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AssessmentIcon from '@mui/icons-material/Assessment';
import axios from 'axios';
import './VolumeSelection.css';

// DEV+DOCKER: usa path relativi
const API_BASE = '';

// Workflow unificato: per compatibilità col backend attuale,
// passiamo ancora un baseProfile fisso.
// La distinzione non è più esposta in UI.
const UNIFIED_BASE_PROFILE = 'auto';

const VolumeSelection = ({
  demFile,
  onBack,
  processId,
  setStep,
  setVolumeRun,
}) => {
  const [isLoading, setIsLoading] = useState(false);

  // debug / diagnostica
  const [resultText, setResultText] = useState('');
  const [effectivePid, setEffectivePid] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [manifest, setManifest] = useState(null);

  const [calcWallMs, setCalcWallMs] = useState(null);
  const [serverPhasesCalc, setServerPhasesCalc] = useState(null);

  const manifestImages = useMemo(() => {
    const imgs = manifest?.images || [];
    return imgs
      .map((it) => ({
        filename: it.filename,
        publicPath: it.public_path,
        titles: it.titles || [],
        descriptions: it.descriptions || [],
      }))
      .filter((it) => !!it.publicPath);
  }, [manifest]);

  const resetOutputs = () => {
    setResultText('');
    setEffectivePid(null);
    setMetrics(null);
    setManifest(null);
    setCalcWallMs(null);
    setServerPhasesCalc(null);
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

    if (demFile) formData.append('demFile', demFile);

    // workflow unificato: valore fisso, non più selezionato dall’utente
    formData.append('baseProfile', UNIFIED_BASE_PROFILE);

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
        } catch {
          /* ignore */
        }
      }

      const pid = response?.data?.processId || pidFromProp || null;
      setEffectivePid(pid);

      const serverResult = response?.data?.result;
      if (typeof serverResult === 'string') setResultText(serverResult);

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

      let vr = null;
      if (pid) {
        const vrUrl = `${API_BASE}/outputs/${pid}/volume_results.json`;
        vr = await fetchJsonOrNull(vrUrl);
      }

      const fallbackModuleKey = response?.data?.moduleKey || vr?.moduleKey || 'unified';

      const run = {
        processId: pid,
        status: vr?.status || response?.data?.status || 'completed',
        moduleKey: fallbackModuleKey,
        result: vr?.result || response?.data?.result || null,
        images: vr?.images || response?.data?.images || [],
        baseProfile: UNIFIED_BASE_PROFILE,
        selection: {
          workflow: 'unified',
          legacyBaseProfileUsedForCompatibility: UNIFIED_BASE_PROFILE,
        },
      };

      if (setVolumeRun) setVolumeRun(run);
      if (setStep) setStep('results');
      return;
    } catch (error) {
      console.error('Error calculating volume:', error);
      alert('An error occurred during the volume calculation.');
    } finally {
      setIsLoading(false);
    }
  };

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
    return rows.filter((r) => r[1] !== undefined && r[1] !== null);
  }, [metrics]);

  const SHOW_INLINE_RESULTS = false;

  const instructionText = useMemo(() => {
    if (isLoading) return 'Running unified volume workflow...';
    return 'Unified volume workflow';
  }, [isLoading]);

  const canCalculate = !isLoading;

  return (
    <Box className="volume-selection-container">
      <Typography variant="h5" className="success-message">
        First processing successful
      </Typography>

      <Typography variant="h6" className="instruction-message">
        {instructionText}
      </Typography>

      <Box
        sx={{
          maxWidth: 920,
          mx: 'auto',
          mt: 3,
          mb: 2,
        }}
      >
        <Paper
          elevation={3}
          sx={{
            borderRadius: 4,
            p: { xs: 2.5, md: 3.5 },
            border: '1px solid',
            borderColor: 'divider',
            background: 'linear-gradient(180deg, #ffffff 0%, #f7f8fa 100%)',
          }}
        >
          <Typography variant="h5" sx={{ mb: 1.5, fontWeight: 700 }}>
            Unified volume calculation
          </Typography>

          <Typography variant="body1" sx={{ mb: 2, lineHeight: 1.7 }}>
            MorphoVolc now uses a single workflow for volume calculation.
            The current implementation keeps the internal processing aligned with the existing
            backend logic, while simplifying the user interface and preparing the codebase
            for a more adaptive calculation strategy.
          </Typography>

          <Box component="ul" sx={{ m: 0, pl: 3, mb: 2 }}>
            <li>
              <Typography variant="body2" sx={{ lineHeight: 1.7 }}>
                A single calculation path is launched from the interface.
              </Typography>
            </li>
            <li>
              <Typography variant="body2" sx={{ lineHeight: 1.7 }}>
                After the first run, the caldera rim can still be opened, edited, saved, reset to auto,
                and recalculated from the results viewer.
              </Typography>
            </li>
            <li>
              <Typography variant="body2" sx={{ lineHeight: 1.7 }}>
                This setup is intended to support upcoming testing, validation against literature,
                and future adaptive improvements in the backend logic.
              </Typography>
            </li>
          </Box>

          <Typography variant="body2" sx={{ opacity: 0.72 }}>
            Unified workflow mode: <b>{UNIFIED_BASE_PROFILE}</b>
          </Typography>
        </Paper>
      </Box>

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
          Back to DEM Results
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
                        <td
                          style={{
                            padding: '6px 8px',
                            borderBottom: '1px solid #ddd',
                            textAlign: 'right',
                          }}
                        >
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
              <Typography variant="h6" sx={{ mb: 1 }}>
                Diagnostics
              </Typography>
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