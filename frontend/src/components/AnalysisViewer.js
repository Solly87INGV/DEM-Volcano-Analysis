// AnalysisViewer.js
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Button, Typography, CircularProgress, Divider, Paper } from '@mui/material';
import axios from 'axios';

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

export default function AnalysisViewer({ processId, onBack, onContinue, requireCompleted = true }) {
  const SHOW_DIAGNOSTICS = false;

  const [status, setStatus] = useState('unknown');
  const [images, setImages] = useState([]);
  const [stats, setStats] = useState(null);
  const [slideIdx, setSlideIdx] = useState(0);
  const [loading, setLoading] = useState(true);

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

  // ✅ come in VolumeResultsViewer: rende le double_XX.png "colonna" su desktop
  const SINGLE_INNER_SX = {
    width: { xs: '100%', md: 'calc(50% - 8px)' },
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
  };

  const dem = useMemo(() => pickByName(images, 'dem_overview'), [images]);
  const asp = useMemo(() => pickByName(images, 'aspect_overview'), [images]);

  const doubles = useMemo(() => {
    return (images || [])
      .filter((it) => getName(it).includes('double_'))
      .sort((a, b) => getName(a).localeCompare(getName(b), undefined, { numeric: true }));
  }, [images]);

  const slides = useMemo(() => {
    const arr = [];
    arr.push({ type: 'overview' });
    for (const d of doubles) arr.push({ type: 'double', img: d });
    return arr;
  }, [doubles]);

  const canContinue = !requireCompleted || status === 'completed';

  useEffect(() => {
    if (!processId) return;

    let timer = null;
    let cancelled = false;

    const tick = async () => {
      try {
        setLoading(true);
        const r = await axios.get(`/processStatus/${processId}`, {
          headers: { 'Cache-Control': 'no-cache' },
        });
        if (cancelled) return;

        setStatus(r.data.status || 'unknown');
        setImages(r.data.images || []);

        if (r.data.statsUrl) {
          const rs = await axios.get(r.data.statsUrl, {
            headers: { 'Cache-Control': 'no-cache' },
          });
          if (!cancelled) setStats(rs.data);
        } else {
          if (!cancelled) setStats(null);
        }
      } catch (e) {
        if (!cancelled) setStatus('error');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    tick();
    timer = setInterval(tick, 1500);

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [processId]);

  useEffect(() => {
    setSlideIdx((i) => {
      const max = Math.max(0, slides.length - 1);
      return Math.min(i, max);
    });
  }, [slides.length]);

  const current = slides[slideIdx] || { type: 'overview' };
  const prevDisabled = slideIdx <= 0;
  const nextDisabled = slideIdx >= slides.length - 1;

  const renderCurrentSlide = () => {
    if (current.type === 'overview') {
      return (
        <FramePaper title="Overview (DEM + Aspect)">
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
              {dem?.url ? (
                <img alt="dem_overview" src={dem.url + '?t=' + Date.now()} style={IMG_STYLE} />
              ) : (
                <Typography sx={{ opacity: 0.85, color: '#eaeaea', py: 2 }}>
                  DEM not available yet.
                </Typography>
              )}
            </Box>

            <Box sx={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
              {asp?.url ? (
                <img alt="aspect_overview" src={asp.url + '?t=' + Date.now()} style={IMG_STYLE} />
              ) : (
                <Typography sx={{ opacity: 0.85, color: '#eaeaea', py: 2 }}>
                  Aspect not available yet.
                </Typography>
              )}
            </Box>
          </Box>
        </FramePaper>
      );
    }

    const url = current?.img?.url;
    const title = current?.img?.filename || 'Double panel';

    return (
      <FramePaper title={title}>
        <Box sx={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
          <Box sx={SINGLE_INNER_SX}>
            {url ? (
              <img alt="double_panel" src={url + '?t=' + Date.now()} style={IMG_STYLE} />
            ) : (
              <Typography sx={{ opacity: 0.85, color: '#eaeaea', py: 2 }}>
                Not available yet.
              </Typography>
            )}
          </Box>
        </Box>
      </FramePaper>
    );
  };

  return (
    <Box sx={{ width: '100%', pt: { xs: 8, md: 10 }, px: { xs: 1, md: 2 } }}>
      <Box sx={{ textAlign: 'center', mb: 2 }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>
          DEM analysis
        </Typography>
        <Typography variant="body2" sx={{ opacity: 0.75 }}>
          processId: <code>{processId || '-'}</code> — status: <b>{status}</b>
        </Typography>
      </Box>

      <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, flexWrap: 'wrap', mb: 2, alignItems: 'center' }}>
        <Button variant="outlined" onClick={onBack}>Back</Button>
        <Button variant="contained" onClick={onContinue} disabled={!canContinue}>Continue</Button>

        <Box sx={{ width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {loading ? <CircularProgress size={20} /> : null}
        </Box>
      </Box>

      <Divider sx={{ mb: 2 }} />

      <Box sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 1, flexWrap: 'wrap', alignItems: 'center', mb: 1 }}>
          <Typography variant="subtitle2">{current.type === 'overview' ? 'Overview' : 'Double panels'}</Typography>

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

        {renderCurrentSlide()}
      </Box>

      {SHOW_DIAGNOSTICS && (
        <>
          <Divider sx={{ mb: 2 }} />
          <Typography variant="h6" sx={{ mb: 1 }}>Statistics</Typography>
          <Box sx={{ border: '1px solid #ddd', borderRadius: 2, p: 1 }}>
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', opacity: stats ? 1 : 0.7 }}>
              {stats ? JSON.stringify(stats, null, 2) : 'Not available yet.'}
            </pre>
          </Box>
        </>
      )}
    </Box>
  );
}
