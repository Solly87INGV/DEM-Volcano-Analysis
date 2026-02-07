// frontend/src/components/VolumeResultsViewer.js
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Button, Typography, CircularProgress, Divider } from '@mui/material';
import axios from 'axios';

function normalizeImages(processId, images) {
  const base = processId ? `/outputs/${processId}` : '';
  const arr = Array.isArray(images) ? images : [];

  return arr.map((it) => {
    // accetta stringhe o oggetti
    if (typeof it === 'string') {
      return { filename: it, title: it, url: base ? `${base}/${it}` : null };
    }

    const filename = it?.filename || it?.file || it?.name || null;
    const title = it?.title || filename || 'image';
    const url = it?.url || (filename && base ? `${base}/${filename}` : null);

    return { ...it, filename, title, url };
  });
}

// ---------- NEW: nicer numeric formatting for results ----------
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

    result.h_max_m != null
      ? { label: 'Max elevation (h_max)', value: fmt(result.h_max_m, 1), unit: 'm' }
      : null,
    result.pixel_size_m != null
      ? { label: 'Pixel size', value: fmt(result.pixel_size_m, 0), unit: 'm' }
      : null,
  ].filter(Boolean);
}
// ------------------------------------------------------------

export default function VolumeResultsViewer({
  volumeRun, // { processId, status, result, images, outputsBaseUrl? }
  processId: processIdProp,
  onBack,
  onBackToUpload, // opzionale
}) {
  const [processId, setProcessId] = useState(processIdProp || volumeRun?.processId || null);
  const [status, setStatus] = useState(volumeRun?.status || 'unknown');
  const [result, setResult] = useState(volumeRun?.result || null);
  const [images, setImages] = useState(normalizeImages(volumeRun?.processId, volumeRun?.images));
  const [slideIdx, setSlideIdx] = useState(0);
  const [loading, setLoading] = useState(false);

  const SLIDE_FRAME_H = '70vh';
  const IMG_STYLE = {
    width: '100%',
    height: '100%',
    maxHeight: '100%',
    objectFit: 'contain',
    borderRadius: 12,
    border: '1px solid #eee',
    display: 'block',
    background: '#fafafa',
  };

  // Se arriva volumeRun nuovo via props, aggiorna subito UI
  useEffect(() => {
    if (!volumeRun) return;
    const pid = volumeRun.processId || processIdProp || processId;
    setProcessId(pid);
    setStatus(volumeRun.status || 'unknown');
    setResult(volumeRun.result || null);
    setImages(normalizeImages(pid, volumeRun.images));
  }, [volumeRun, processIdProp]); // eslint-disable-line react-hooks/exhaustive-deps

  const slides = useMemo(() => {
    const arr = images || [];
    return arr.map((img) => ({ type: 'img', img }));
  }, [images]);

  const current = slides[slideIdx] || null;
  const prevDisabled = slideIdx <= 0;
  const nextDisabled = slideIdx >= slides.length - 1;

  // clamp slideIdx quando cambiano slides
  useEffect(() => {
    setSlideIdx((i) => {
      const max = Math.max(0, slides.length - 1);
      return Math.min(i, max);
    });
  }, [slides.length]);

  // Polling: se non ho volumeRun completo, leggo dal JSON su outputs
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

        // IMPORTANT: supporta sia wrapper {result:{...}} che JSON "nudo" (vecchia forma)
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

    // se ho già completed, non pollare
    const shouldPoll = status !== 'completed' && status !== 'failed';

    fetchOnce();
    if (shouldPoll) {
      timer = setInterval(fetchOnce, 1500);
    }

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [processId, status]); // status per stoppare quando diventa completed/failed

  const rows = useMemo(() => buildResultRows(result), [result]);

  const renderSlide = () => {
    if (!current?.img) {
      return (
        <Box
          sx={{
            border: '1px solid #ddd',
            borderRadius: 2,
            p: 2,
            height: { xs: 'auto', md: SLIDE_FRAME_H },
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Typography sx={{ opacity: 0.7 }}>No images available yet.</Typography>
        </Box>
      );
    }

    const { url, title } = current.img;
    return (
      <Box
        sx={{
          border: '1px solid #ddd',
          borderRadius: 2,
          p: 1,
          height: { xs: 'auto', md: SLIDE_FRAME_H },
          display: 'flex',
          flexDirection: 'column',
          gap: 1,
        }}
      >
        <Typography variant="subtitle2" sx={{ opacity: 0.85 }}>
          {title || 'Image'}
        </Typography>

        <Box sx={{ flex: 1, minHeight: 0, display: 'flex', alignItems: 'center' }}>
          {url ? (
            <img alt="volume_slide" src={url + '?t=' + Date.now()} style={IMG_STYLE} />
          ) : (
            <Typography sx={{ opacity: 0.7 }}>Not available yet.</Typography>
          )}
        </Box>
      </Box>
    );
  };

  return (
    <Box
      sx={{
        width: '100%',
        pt: { xs: 8, md: 10 },
        px: { xs: 1, md: 2 },
      }}
    >
      {/* Titolo */}
      <Box sx={{ textAlign: 'center', mb: 2 }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>
          Volume results
        </Typography>
        <Typography variant="body2" sx={{ opacity: 0.75 }}>
          processId: <code>{processId || '-'}</code> — status: <b>{status || 'unknown'}</b>
        </Typography>
      </Box>

      {/* Pulsanti */}
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 1,
          flexWrap: 'wrap',
          mb: 2,
          alignItems: 'center',
        }}
      >
        <Button variant="outlined" onClick={onBack}>
          Back
        </Button>

        {onBackToUpload ? (
          <Button variant="outlined" onClick={onBackToUpload}>
            Back to Upload
          </Button>
        ) : null}

        <Box
          sx={{
            width: 28,
            height: 28,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {loading ? <CircularProgress size={20} /> : null}
        </Box>
      </Box>

      <Divider sx={{ mb: 2 }} />

      {/* Slider */}
      <Box sx={{ mb: 2 }}>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            gap: 1,
            flexWrap: 'wrap',
            alignItems: 'center',
            mb: 1,
          }}
        >
          <Typography variant="subtitle2">Images</Typography>

          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
            <Button
              variant="outlined"
              onClick={() => setSlideIdx((i) => Math.max(0, i - 1))}
              disabled={prevDisabled}
            >
              Prev
            </Button>

            <Button
              variant="outlined"
              onClick={() => setSlideIdx((i) => Math.min(slides.length - 1, i + 1))}
              disabled={nextDisabled}
            >
              Next
            </Button>

            <Typography variant="body2" sx={{ opacity: 0.7 }}>
              {slides.length === 0 ? '0/0' : `${slideIdx + 1}/${slides.length}`}
            </Typography>
          </Box>
        </Box>

        {renderSlide()}
      </Box>

      {/* Results */}
      <Divider sx={{ mb: 2 }} />
      <Typography variant="h6" sx={{ mb: 1, textAlign: 'center' }}>
        Results
      </Typography>

      {/* ✅ NEW: pretty, centered results (instead of raw JSON only) */}
      <Box
        sx={{
          border: '1px solid #ddd',
          borderRadius: 2,
          p: 2,
          maxWidth: 720,
          mx: 'auto', // centered
          background: '#fff',
        }}
      >
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
                <Typography variant="body2" sx={{ opacity: 0.8 }}>
                  {r.label}
                </Typography>

                <Typography variant="body1" sx={{ fontWeight: 700 }}>
                  {r.value}{' '}
                  <span style={{ fontWeight: 400, opacity: 0.8 }}>{r.unit}</span>
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
