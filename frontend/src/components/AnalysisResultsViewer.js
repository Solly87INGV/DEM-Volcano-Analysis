// AnalysisResultsViewer.js
import React, { useEffect, useMemo, useState } from "react";
import { Box, Button, Typography, CircularProgress, Divider, Paper } from "@mui/material";
import axios from "axios";

function pickByName(list, includesStr) {
  return (list || []).find((it) =>
    String(it?.filename || it?.name || it?.file || it?.url || "").includes(includesStr)
  );
}
function getName(it) {
  return String(it?.filename || it?.name || it?.file || it?.url || "");
}

// Frame stile "vecchio" (Paper + cornice scura interna)
const FramePaper = ({ title, sx, children }) => (
  <Paper
    elevation={3}
    sx={{
      borderRadius: 3,
      px: 0.6,
      py: 0.6,
      border: "1px solid",
      borderColor: "divider",
      background: "linear-gradient(180deg, #ffffff 0%, #f6f7f9 100%)",
      position: "relative",
      overflow: "hidden",
      ...sx,
    }}
  >
    {title ? (
      <Box sx={{ display: "flex", alignItems: "center", mb: 0.35 }}>
        <Box
          sx={{
            px: 0.9,
            py: 0.15,
            borderRadius: 999,
            border: "1px solid",
            borderColor: "divider",
            background: "#fff",
            boxShadow: "inset 0 1px 0 rgba(255,255,255,0.7)",
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
        overflow: "hidden",
        background: "linear-gradient(180deg, #1c1c1c 0%, #121212 100%)",
        border: "1px solid rgba(255,255,255,0.08)",
        boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.55)",
        // ✅ meno padding = più spazio utile per le immagini
        px: 0.15,
        py: 0.15,
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      {children}
    </Box>
  </Paper>
);

export default function AnalysisResultsViewer({
  processId,
  onBack,
  onContinue,
  requireCompleted = true,
}) {
  const [status, setStatus] = useState("unknown");
  const [manifest, setManifest] = useState(null);
  const [err, setErr] = useState("");
  const [slideIdx, setSlideIdx] = useState(0);
  const [loading, setLoading] = useState(true);

  // ✅ default per le slide "double_*.png"
  const IMG_MAX_H = "70vh";
  // ✅ più generoso SOLO per l’overview (DEM + Aspect)
  const IMG_MAX_H_OVERVIEW = "85vh";

  const IMG_STYLE = {
    width: "100%",
    height: "auto",
    maxHeight: IMG_MAX_H,
    objectFit: "contain",
    borderRadius: 14,
    display: "block",
    background: "transparent",
  };

  // 1) Poll status (così Continue può essere disabilitato se non completed)
  useEffect(() => {
    if (!processId) return;
    let timer = null;
    let cancelled = false;

    const tick = async () => {
      try {
        const r = await axios.get(`/processStatus/${processId}`, {
          headers: { "Cache-Control": "no-cache" },
        });
        if (!cancelled) setStatus(r.data?.status || "unknown");
      } catch {
        if (!cancelled) setStatus("error");
      }
    };

    tick();
    timer = setInterval(tick, 1500);

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [processId]);

  // 2) Load manifest
  useEffect(() => {
    if (!processId) return;

    let cancelled = false;
    setErr("");
    setManifest(null);

    const url = `/outputs/${processId}/analysis_images.json`;
    setLoading(true);

    fetch(url, { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} fetching ${url}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setManifest(data);
      })
      .catch((e) => {
        if (!cancelled) setErr(String(e?.message || e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [processId]);

  const images = useMemo(() => {
    if (!manifest) return [];
    return Array.isArray(manifest) ? manifest : (manifest.images || manifest.items || []);
  }, [manifest]);

  const dem = useMemo(() => pickByName(images, "dem_overview"), [images]);
  const asp = useMemo(() => pickByName(images, "aspect_overview"), [images]);

  const doubles = useMemo(() => {
    return (images || [])
      .filter((it) => getName(it).includes("double_"))
      .sort((a, b) => getName(a).localeCompare(getName(b), undefined, { numeric: true }));
  }, [images]);

  const slides = useMemo(() => {
    const arr = [];
    arr.push({ type: "overview" });
    for (const d of doubles) arr.push({ type: "double", img: d });
    return arr;
  }, [doubles]);

  useEffect(() => {
    setSlideIdx((i) => {
      const max = Math.max(0, slides.length - 1);
      return Math.min(i, max);
    });
  }, [slides.length]);

  const current = slides[slideIdx] || { type: "overview" };
  const prevDisabled = slideIdx <= 0;
  const nextDisabled = slideIdx >= slides.length - 1;

  const canContinue = !requireCompleted || status === "completed";

  const imgSrc = (it, fallbackFilename) => {
    // preferisci public_path quando c’è
    const base = it?.public_path || (fallbackFilename ? `/outputs/${processId}/${fallbackFilename}` : "");
    if (!base) return "";
    return `${base}?t=${Date.now()}`;
  };

  const renderCurrentSlide = () => {
    if (current.type === "overview") {
      // ✅ Niente box “vuoto”: se asp non c’è, la griglia diventa 1 colonna e il blocco aspect non viene renderizzato.
      return (
        <FramePaper title="Overview">
          <Box
            sx={{
              width: "100%",
              maxWidth: { xs: "100%", md: 1500 },
              mx: "auto",
              display: "grid",
              gridTemplateColumns: { xs: "1fr", md: asp ? "1fr 1fr" : "1fr" },
              gap: 2,
              alignItems: "center",
            }}
          >
            <Box sx={{ width: "100%", display: "flex", justifyContent: "center" }}>
              {dem ? (
                <img
                  alt="dem_overview"
                  src={imgSrc(dem, dem?.filename)}
                  style={{ ...IMG_STYLE, maxHeight: IMG_MAX_H_OVERVIEW }}
                />
              ) : (
                <Typography sx={{ opacity: 0.85, color: "#eaeaea", py: 2 }}>
                  DEM overview not available yet.
                </Typography>
              )}
            </Box>

            {asp ? (
              <Box sx={{ width: "100%", display: "flex", justifyContent: "center" }}>
                <img
                  alt="aspect_overview"
                  src={imgSrc(asp, asp?.filename)}
                  style={{ ...IMG_STYLE, maxHeight: IMG_MAX_H_OVERVIEW }}
                />
              </Box>
            ) : null}
          </Box>
        </FramePaper>
      );
    }

    const it = current?.img;
    const title = it?.filename || "Double panel";

    return (
      <FramePaper title={title}>
        <Box sx={{ width: "100%", display: "flex", justifyContent: "center" }}>
          {it ? (
            <img alt={title} src={imgSrc(it, it?.filename)} style={IMG_STYLE} />
          ) : (
            <Typography sx={{ opacity: 0.85, color: "#eaeaea", py: 2 }}>
              Not available yet.
            </Typography>
          )}
        </Box>
      </FramePaper>
    );
  };

  if (!processId) return null;

  return (
    <Box sx={{ width: "100%", pt: { xs: 2, md: 2 }, px: { xs: 1, md: 2 } }}>
      <Box sx={{ textAlign: "center", mb: 2 }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>
          DEM analysis
        </Typography>
        <Typography variant="body2" sx={{ opacity: 0.75 }}>
          processId: <code>{processId || "-"}</code> — status: <b>{status}</b>
        </Typography>
      </Box>

      {/* ✅ Top controls: Back/Continue allineati a Prev/Next, stesso "motivo" ma blu */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          gap: 1,
          flexWrap: "wrap",
          mb: 2,
          alignItems: "center",
        }}
      >
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {onBack ? (
            <Button
              variant="outlined"
              color="primary"
              onClick={onBack}
              sx={{
                textTransform: "none",
                borderWidth: 2,
                "&:hover": { borderWidth: 2 },
              }}
            >
              Back to upload
            </Button>
          ) : null}

          {onContinue ? (
            <Button
              variant="outlined"
              color="primary"
              onClick={onContinue}
              disabled={!canContinue}
              sx={{
                textTransform: "none",
                borderWidth: 2,
                "&:hover": { borderWidth: 2 },
              }}
            >
              Continue
            </Button>
          ) : null}
        </Box>

        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          <Button
            variant="outlined"
            color="primary"
            onClick={() => setSlideIdx((i) => Math.max(0, i - 1))}
            disabled={prevDisabled}
            sx={{ textTransform: "none", borderWidth: 2, "&:hover": { borderWidth: 2 } }}
          >
            Prev
          </Button>
          <Button
            variant="outlined"
            color="primary"
            onClick={() => setSlideIdx((i) => Math.min(slides.length - 1, i + 1))}
            disabled={nextDisabled}
            sx={{ textTransform: "none", borderWidth: 2, "&:hover": { borderWidth: 2 } }}
          >
            Next
          </Button>

          <Typography variant="body2" sx={{ opacity: 0.7 }}>
            {slides.length === 0 ? "0/0" : `${slideIdx + 1}/${slides.length}`}
          </Typography>

          <Box sx={{ width: 28, height: 28, display: "flex", alignItems: "center", justifyContent: "center" }}>
            {loading ? <CircularProgress size={20} /> : null}
          </Box>
        </Box>
      </Box>

      <Divider sx={{ mb: 2 }} />

      {err ? (
        <Typography sx={{ color: "crimson" }}>Errore: {err}</Typography>
      ) : !manifest ? (
        <Typography>Carico risultati…</Typography>
      ) : (
        <Box sx={{ mb: 2 }}>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              gap: 1,
              flexWrap: "wrap",
              alignItems: "center",
              mb: 1,
            }}
          >
            <Typography variant="subtitle2">
              {current.type === "overview" ? "Overview" : "Double panels"}
            </Typography>
          </Box>

          {renderCurrentSlide()}
        </Box>
      )}
    </Box>
  );
}
