// AnalysisViewer.js
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Button, Typography, CircularProgress, Divider } from '@mui/material';
import axios from 'axios';

function pickByName(list, includesStr) {
  return (list || []).find((it) =>
    (it?.filename || it?.name || it?.file || it?.url || '').includes(includesStr)
  );
}

function getName(it) {
  return String(it?.filename || it?.name || it?.file || it?.url || '');
}

export default function AnalysisViewer({
  processId,
  onBack,
  onContinue,
  requireCompleted = true,
}) {
  const [status, setStatus] = useState('unknown');
  const [images, setImages] = useState([]);
  const [stats, setStats] = useState(null);
  const [slideIdx, setSlideIdx] = useState(0);
  const [loading, setLoading] = useState(true);

  // ✅ frame height uniforme per overview e double
  const SLIDE_FRAME_H = '70vh'; // prova anche 65vh se vuoi più compatto
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

  const dem = useMemo(() => pickByName(images, 'dem_overview'), [images]);
  const asp = useMemo(() => pickByName(images, 'aspect_overview'), [images]);

  const doubles = useMemo(() => {
    return (images || [])
      .filter((it) => getName(it).includes('double_'))
      // ordina per nome per sicurezza (double_01, double_02, ...)
      .sort((a, b) => getName(a).localeCompare(getName(b), undefined, { numeric: true }));
  }, [images]);

  // ✅ Slides:
  // 0 => overview (DEM + Aspect)
  // 1.. => double panels
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
    // clamp quando cambia slides
    setSlideIdx((i) => {
      const max = Math.max(0, slides.length - 1);
      return Math.min(i, max);
    });
  }, [slides.length]);

  const current = slides[slideIdx] || { type: 'overview' };

  const prevDisabled = slideIdx <= 0;
  const nextDisabled = slideIdx >= slides.length - 1;

  const renderCurrentSlide = () => {
    // --- OVERVIEW SLIDE (2 columns) ---
    if (current.type === 'overview') {
      return (
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
            gap: 2,
            // ✅ frame uniforme (su desktop)
            height: { xs: 'auto', md: SLIDE_FRAME_H },
          }}
        >
          <Box
            sx={{
              border: '1px solid #ddd',
              borderRadius: 2,
              p: 1,
              display: 'flex',
              flexDirection: 'column',
              minHeight: { xs: 320, md: 'auto' },
            }}
          >
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              DEM overview
            </Typography>

            {/* ✅ area immagine che occupa tutta l’altezza disponibile */}
            <Box sx={{ flex: 1, minHeight: 0, display: 'flex', alignItems: 'center' }}>
              {dem?.url ? (
                <img
                  alt="dem_overview"
                  src={dem.url + '?t=' + Date.now()}
                  style={IMG_STYLE}
                />
              ) : (
                <Typography sx={{ opacity: 0.7 }}>Not available yet.</Typography>
              )}
            </Box>
          </Box>

          <Box
            sx={{
              border: '1px solid #ddd',
              borderRadius: 2,
              p: 1,
              display: 'flex',
              flexDirection: 'column',
              minHeight: { xs: 320, md: 'auto' },
            }}
          >
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Aspect overview
            </Typography>

            <Box sx={{ flex: 1, minHeight: 0, display: 'flex', alignItems: 'center' }}>
              {asp?.url ? (
                <img
                  alt="aspect_overview"
                  src={asp.url + '?t=' + Date.now()}
                  style={IMG_STYLE}
                />
              ) : (
                <Typography sx={{ opacity: 0.7 }}>Not available yet.</Typography>
              )}
            </Box>
          </Box>
        </Box>
      );
    }

    // --- DOUBLE SLIDE ---
    const url = current?.img?.url;
    return (
      <Box
        sx={{
          border: '1px solid #ddd',
          borderRadius: 2,
          p: 1,
          // ✅ stesso frame delle overview
          height: { xs: 'auto', md: SLIDE_FRAME_H },
          display: 'flex',
          alignItems: 'center',
        }}
      >
        {url ? (
          <img
            alt="double_panel"
            src={url + '?t=' + Date.now()}
            style={IMG_STYLE}
          />
        ) : (
          <Typography sx={{ opacity: 0.7 }}>Not available yet.</Typography>
        )}
      </Box>
    );
  };

  return (
    <Box
      sx={{
        width: '100%',
        // ✅ evita che l'header copra il titolo (se header è fixed/sticky)
        pt: { xs: 8, md: 10 },
        px: { xs: 1, md: 2 },
      }}
    >
      {/* ✅ Titolo centrato e un filo più in basso */}
      <Box sx={{ textAlign: 'center', mb: 2 }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>
          DEM analysis
        </Typography>
        <Typography variant="body2" sx={{ opacity: 0.75 }}>
          processId: <code>{processId || '-'}</code> — status: <b>{status}</b>
        </Typography>
      </Box>

      {/* ✅ Pulsanti allineati a destra + anti “saltellamento” spinner */}
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

        <Button variant="contained" onClick={onContinue} disabled={!canContinue}>
          Continue
        </Button>

        {/* ✅ spazio fisso, così non balla niente ogni 1.5s */}
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

      {/* ✅ Slider unico: overview + double_* */}
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
          <Typography variant="subtitle2">
            {current.type === 'overview' ? 'Overview' : 'Double panels'}
          </Typography>

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

        {renderCurrentSlide()}
      </Box>

      {/* Stats */}
      <Divider sx={{ mb: 2 }} />
      <Typography variant="h6" sx={{ mb: 1 }}>
        Statistics
      </Typography>

      <Box sx={{ border: '1px solid #ddd', borderRadius: 2, p: 1 }}>
        <pre
          style={{
            margin: 0,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            opacity: stats ? 1 : 0.7,
          }}
        >
          {stats ? JSON.stringify(stats, null, 2) : 'Not available yet.'}
        </pre>
      </Box>
    </Box>
  );
}