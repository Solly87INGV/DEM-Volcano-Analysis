import React, { useEffect, useMemo, useState } from "react";

export default function AnalysisResultsViewer({ processId }) {
  const [manifest, setManifest] = useState(null);
  const [err, setErr] = useState("");
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    if (!processId) return;
    setErr("");
    setManifest(null);

    const url = `/outputs/${processId}/analysis_images.json`;
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} fetching ${url}`);
        return r.json();
      })
      .then((data) => {
        console.log("[AnalysisResultsViewer] manifest loaded:", data);
        setManifest(data);
        setIdx(0); // ✅ reset indice ad ogni nuovo processo
      })
      .catch((e) => setErr(String(e?.message || e)));
  }, [processId]);

  const items = useMemo(() => {
    if (!manifest) return [];
    return Array.isArray(manifest) ? manifest : (manifest?.images || manifest?.items || []);
  }, [manifest]);

  if (!processId) return null;
  if (err) return <div style={{ color: "crimson" }}>Errore: {err}</div>;
  if (!manifest) return <div>Carico risultati…</div>;

  if (items.length === 0) {
    return (
      <div>
        Manifest letto, ma non contiene immagini in un campo noto.
        <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(manifest, null, 2)}</pre>
      </div>
    );
  }

  const safeIdx = Math.min(Math.max(idx, 0), items.length - 1);
  const it = items[safeIdx];
  const filename = it?.filename || it?.file || it?.name || it;
  const title = (it?.titles && it.titles.join(" | ")) || it?.title || filename || `image_${safeIdx + 1}`;
  const desc = (it?.descriptions && it.descriptions.join(" / ")) || it?.description || "";
  const src = it?.public_path || `/outputs/${processId}/${filename}`;

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <h3 style={{ margin: 0 }}>Risultati (Complete DEM Analysis)</h3>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button onClick={() => setIdx((v) => Math.max(0, v - 1))} disabled={safeIdx === 0}>
            Prev
          </button>
          <div style={{ fontSize: 14 }}>
            {safeIdx + 1} / {items.length}
          </div>
          <button onClick={() => setIdx((v) => Math.min(items.length - 1, v + 1))} disabled={safeIdx === items.length - 1}>
            Next
          </button>
        </div>
      </div>

      <div style={{ marginTop: 8, fontWeight: 600 }}>{title}</div>
      {desc && <div style={{ marginTop: 4, opacity: 0.8, fontSize: 14 }}>{desc}</div>}

      {/* ✅ area scrollabile */}
      <div
        style={{
          marginTop: 10,
          border: "1px solid #ddd",
          borderRadius: 10,
          padding: 10,
          maxHeight: "70vh",
          overflow: "auto",
          background: "white",
        }}
      >
        <img
          src={src}
          alt={title}
          style={{ width: "100%", height: "auto", display: "block" }}
          onError={() => console.error("Image not found:", src)}
        />
      </div>
    </div>
  );
}
