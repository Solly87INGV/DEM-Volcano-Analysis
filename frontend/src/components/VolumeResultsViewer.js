// frontend/src/components/VolumeResultsViewer.js
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Button, Typography, CircularProgress, Divider, Paper } from '@mui/material';
import axios from 'axios';

function normalizeImages(processId, images) {
  const base = processId ? `/outputs/${processId}` : '';
  const arr = Array.isArray(images) ? images : [];

  return arr.map((it) => {
    if (typeof it === 'string') return { filename: it, title: it, url: base ? `${base}/${it}` : null };
    const filename = it?.filename || it?.file || it?.name || null;
    const title = it?.title || filename || 'image';
    const url = it?.url || (filename && base ? `${base}/${filename}` : null);
    return { ...it, filename, title, url };
  });
}

function fmt(n, digits = 2) {
  const x = Number(n);
  if (!Number.isFinite(x)) return '-';
  return x.toFixed(digits);
}

function buildResultRows(result) {
  if (!result || typeof result !== 'object') return [];
  return [
    { label: 'Base area', value: fmt(result.base_area_km2, 2), unit: 'km²' },
    { label: 'Base width', value: fmt(result.base_width_km, 2), unit: 'km' },
    { label: 'Caldera area', value: fmt(result.caldera_area_km2, 2), unit: 'km²' },
    { label: 'Caldera width', value: fmt(result.caldera_width_km, 2), unit: 'km' },
    { label: 'Total volume', value: fmt(result.total_volume_km3, 2), unit: 'km³' },
    { label: 'Caldera volume', value: fmt(result.caldera_volume_km3, 2), unit: 'km³' },
    { label: 'Effective volume', value: fmt(result.effective_volume_km3, 2), unit: 'km³' },
    result.h_max_m != null ? { label: 'Max elevation (h_max)', value: fmt(result.h_max_m, 1), unit: 'm' } : null,
    result.pixel_size_m != null ? { label: 'Pixel size', value: fmt(result.pixel_size_m, 0), unit: 'm' } : null,
  ].filter(Boolean);
}

function pickByName(list, includesStr) {
  return (list || []).find((it) =>
    (it?.filename || it?.name || it?.file || it?.url || '').includes(includesStr)
  );
}
function getName(it) {
  return String(it?.filename || it?.name || it?.file || it?.url || '');
}

// ✅ Option B2 (final): px/py controlla lati e top/bottom in modo uniforme
const FramePaper = ({ title, sx, children }) => (
  <Paper
    elevation={3}
    sx={{
      borderRadius: 3,
      px: 0.6,
      py: 0.6,
      border: '1px solid',
      borderColor: 'divider',
      background: 'linear-gradient(180deg, #ffffff 0%, #f6f7f9 100%)',
      position: 'relative',
      overflow: 'hidden',
      ...sx,
    }}
  >
    {title ? (
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 0.35 }}>
        <Box
          sx={{
            px: 0.9,
            py: 0.15,
            borderRadius: 999,
            border: '1px solid',
            borderColor: 'divider',
            background: '#fff',
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.7)',
          }}
        >
          <Typography variant="subtitle2" sx={{ opacity: 0.85, lineHeight: 1.05 }}>
            {title}
          </Typography>
        </Box>
      </Box>
    ) : null}

    <Box
      sx={{
        borderRadius: 2.5,
        overflow: 'hidden',
        background: 'linear-gradient(180deg, #1c1c1c 0%, #121212 100%)',
        border: '1px solid rgba(255,255,255,0.08)',
        boxShadow: 'inset 0 0 0 1px rgba(0,0,0,0.55)',
        px: 0.35,
        py: 0.35,
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
      }}
    >
      {children}
    </Box>
  </Paper>
);

export default function VolumeResultsViewer({ volumeRun, processId: processIdProp, onBack, onBackToUpload }) {
  const [processId, setProcessId] = useState(processIdProp || volumeRun?.processId || null);
  const [status, setStatus] = useState(volumeRun?.status || 'unknown');
  const [moduleKey, setModuleKey] = useState(volumeRun?.moduleKey || null);
  const [result, setResult] = useState(volumeRun?.result || null);
  const [images, setImages] = useState(normalizeImages(volumeRun?.processId, volumeRun?.images));
  const [slideIdx, setSlideIdx] = useState(0);
  const [loading, setLoading] = useState(false);

  // ✅ NON fissiamo più l'altezza del frame: mettiamo maxHeight sull’immagine
  const IMG_MAX_H = '70vh';

  const IMG_STYLE = {
    width: '100%',
    height: 'auto',
    maxHeight: IMG_MAX_H,
    objectFit: 'contain',
    borderRadius: 14,
    display: 'block',
    background: 'transparent',
  };

  // ✅ Key tweak: per le slide singole (double_XX.png) le rendiamo "colonna" su desktop
  // così assumono una scala visiva simile a DEM/Aspect (che stanno già in 2 colonne).
  const SINGLE_INNER_SX = {
    width: { xs: '100%', md: 'calc(50% - 8px)' },
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
  };

  useEffect(() => {
    if (!volumeRun) return;
    const pid = volumeRun.processId || processIdProp || processId;
    setProcessId(pid);
    setStatus(volumeRun.status || 'unknown');
    setModuleKey(volumeRun.moduleKey || null);
    setResult(volumeRun.result || null);
    setImages(normalizeImages(pid, volumeRun.images));
  }, [volumeRun, processIdProp]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!processId) return;

    let timer = null;
    let cancelled = false;

    const fetchOnce = async () => {
      try {
        setLoading(true);
        const url = `/outputs/${processId}/volume_results.json`;
        const r = await axios.get(url, { headers: { 'Cache-Control': 'no-cache' } });
        if (cancelled) return;

        const data = r.data || {};
        const pid = data.processId || processId;

        setProcessId(pid);
        setStatus(data.status || 'unknown');
        setModuleKey((prev) => data.moduleKey || prev || null);

        const maybeResult =
          data && typeof data === 'object' && data.result && typeof data.result === 'object'
            ? data.result
            : data;

        setResult(maybeResult || null);
        setImages(normalizeImages(pid, data.images));
      } catch (e) {
        if (!cancelled) setStatus((s) => (s === 'completed' ? s : 'unknown'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    const shouldPoll = status !== 'completed' && status !== 'failed';

    fetchOnce();
    if (shouldPoll) timer = setInterval(fetchOnce, 1500);

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [processId, status]);

  // ✅ costruiamo una slide "pair" DEM+Aspect, poi tutte le altre immagini
  const slides = useMemo(() => {
    const dem = pickByName(images, 'dem_overview');
    const asp = pickByName(images, 'aspect_overview');

    const rest = (images || [])
      .filter((it) => {
        const n = getName(it);
        return !n.includes('dem_overview') && !n.includes('aspect_overview');
      })
      .map((img) => ({ type: 'img', img }));

    const arr = [];
    if (dem?.url || asp?.url) {
      arr.push({
        type: 'pair',
        title: 'Overview (DEM + Aspect)',
        left: dem?.url ? { title: 'DEM', url: dem.url } : null,
        right: asp?.url ? { title: 'Aspect', url: asp.url } : null,
      });
    }
    return arr.concat(rest);
  }, [images]);

  const current = slides[slideIdx] || null;
  const prevDisabled = slideIdx <= 0;
  const nextDisabled = slideIdx >= slides.length - 1;

  useEffect(() => {
    setSlideIdx((i) => {
      const max = Math.max(0, slides.length - 1);
      return Math.min(i, max);
    });
  }, [slides.length]);

  const rows = useMemo(() => buildResultRows(result), [result]);

  const canDownloadPdf = Boolean(processId) && Boolean(moduleKey) && status === 'completed';

  const handleDownloadPdf = async () => {
    if (!processId) return;
    const mk = moduleKey || 'unknown_module';
    const url = `/api/report/${processId}?moduleKey=${encodeURIComponent(mk)}`;

    try {
      setLoading(true);
      const resp = await axios.get(url, {
        responseType: 'blob',
        headers: { 'Cache-Control': 'no-cache' },
      });

      const blob = new Blob([resp.data], { type: 'application/pdf' });
      const objectUrl = window.URL.createObjectURL(blob);

      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = `report_${mk}_${processId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();

      window.URL.revokeObjectURL(objectUrl);
    } catch (e) {
      console.error('PDF download failed:', e);
      alert('PDF download failed. Check server route /api/report/:processId.');
    } finally {
      setLoading(false);
    }
  };

  const renderSlide = () => {
    if (!current) {
      return (
        <FramePaper title="Images">
          <Box sx={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
            <Typography sx={{ opacity: 0.85, color: '#eaeaea', py: 2 }}>No images available yet.</Typography>
          </Box>
        </FramePaper>
      );
    }

    // ✅ nuova slide doppietta DEM + Aspect (2 colonne)
    if (current.type === 'pair') {
      return (
        <FramePaper title={current.title || 'Overview'}>
          <Box
            sx={{
              width: '100%',
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
              gap: 2,
              alignItems: 'center',
            }}
          >
            <Box sx={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
              {current.left?.url ? (
                <img alt="dem_overview" src={current.left.url + '?t=' + Date.now()} style={IMG_STYLE} />
              ) : (
                <Typography sx={{ opacity: 0.85, color: '#eaeaea', py: 2 }}>DEM not available.</Typography>
              )}
            </Box>

            <Box sx={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
              {current.right?.url ? (
                <img alt="aspect_overview" src={current.right.url + '?t=' + Date.now()} style={IMG_STYLE} />
              ) : (
                <Typography sx={{ opacity: 0.85, color: '#eaeaea', py: 2 }}>Aspect not available.</Typography>
              )}
            </Box>
          </Box>
        </FramePaper>
      );
    }

    // ✅ caso normale: immagine singola (double_XX.png ecc) ma resa "colonna" su desktop
    const title = current?.img?.title || current?.img?.filename || 'Image';
    const url = current?.img?.url;

    return (
      <FramePaper title={title}>
        <Box sx={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
          <Box sx={SINGLE_INNER_SX}>
            {url ? (
              <img alt="volume_slide" src={url + '?t=' + Date.now()} style={IMG_STYLE} />
            ) : (
              <Typography sx={{ opacity: 0.85, color: '#eaeaea', py: 2 }}>Not available yet.</Typography>
            )}
          </Box>
        </Box>
      </FramePaper>
    );
  };

  return (
    <Box sx={{ width: '100%', pt: { xs: 8, md: 10 }, px: { xs: 1, md: 2 } }}>
      <Box sx={{ textAlign: 'center', mb: 2 }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>Volume results</Typography>
        <Typography variant="body2" sx={{ opacity: 0.75 }}>
          processId: <code>{processId || '-'}</code> — status: <b>{status || 'unknown'}</b>
          {moduleKey ? <> — moduleKey: <code>{moduleKey}</code></> : null}
        </Typography>
      </Box>

      <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, flexWrap: 'wrap', mb: 2, alignItems: 'center' }}>
        <Button variant="outlined" onClick={onBack}>Back</Button>
        {onBackToUpload ? <Button variant="outlined" onClick={onBackToUpload}>Back to Upload</Button> : null}

        <Button
          variant="contained"
          onClick={handleDownloadPdf}
          disabled={!canDownloadPdf || loading}
          title={canDownloadPdf ? 'Download PDF report' : 'Available when status=completed and moduleKey is present'}
        >
          Download PDF
        </Button>

        <Box sx={{ width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {loading ? <CircularProgress size={20} /> : null}
        </Box>
      </Box>

      <Divider sx={{ mb: 2 }} />

      <Box sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 1, flexWrap: 'wrap', alignItems: 'center', mb: 1 }}>
          <Typography variant="subtitle2">Images</Typography>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
            <Button variant="outlined" onClick={() => setSlideIdx((i) => Math.max(0, i - 1))} disabled={prevDisabled}>
              Prev
            </Button>
            <Button variant="outlined" onClick={() => setSlideIdx((i) => Math.min(slides.length - 1, i + 1))} disabled={nextDisabled}>
              Next
            </Button>
            <Typography variant="body2" sx={{ opacity: 0.7 }}>
              {slides.length === 0 ? '0/0' : `${slideIdx + 1}/${slides.length}`}
            </Typography>
          </Box>
        </Box>

        {renderSlide()}
      </Box>

      <Divider sx={{ mb: 2 }} />
      <Typography variant="h6" sx={{ mb: 1, textAlign: 'center' }}>Results</Typography>

      <Box sx={{ border: '1px solid #ddd', borderRadius: 2, p: 2, maxWidth: 720, mx: 'auto', background: '#fff' }}>
        {rows.length ? (
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr', gap: 0.5 }}>
            {rows.map((r) => (
              <Box
                key={r.label}
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  gap: 2,
                  borderBottom: '1px solid #eee',
                  py: 0.8,
                }}
              >
                <Typography variant="body2" sx={{ opacity: 0.8 }}>{r.label}</Typography>
                <Typography variant="body1" sx={{ fontWeight: 700 }}>
                  {r.value} <span style={{ fontWeight: 400, opacity: 0.8 }}>{r.unit}</span>
                </Typography>
              </Box>
            ))}
          </Box>
        ) : (
          <Typography sx={{ opacity: 0.7 }}>Not available yet.</Typography>
        )}
      </Box>
    </Box>
  );
}
