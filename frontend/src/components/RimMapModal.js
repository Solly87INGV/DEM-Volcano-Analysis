import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Dialog,
  DialogTitle,
  DialogContent,
  IconButton,
  Typography,
  CircularProgress,
  Alert,
  Button,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import axios from 'axios';
import { MapContainer, ImageOverlay, FeatureGroup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import '@geoman-io/leaflet-geoman-free';
import '@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css';

function FitToData({ previewBounds, geojson }) {
  const map = useMap();

  useEffect(() => {
    try {
      if (geojson) {
        const layer = L.geoJSON(geojson);
        const bounds = layer.getBounds();
        if (bounds && bounds.isValid()) {
          map.fitBounds(bounds.pad(0.08));
          return;
        }
      }

      if (Array.isArray(previewBounds) && previewBounds.length === 2) {
        map.fitBounds(previewBounds, { padding: [20, 20] });
      }
    } catch (e) {
      console.error('FitToData error:', e);
    }
  }, [map, previewBounds, geojson]);

  return null;
}

function flattenPolygonLayers(rootLayer) {
  const out = [];

  if (!rootLayer) return out;

  if (typeof rootLayer.eachLayer === 'function') {
    rootLayer.eachLayer((child) => {
      out.push(...flattenPolygonLayers(child));
    });
    return out;
  }

  if (
    rootLayer instanceof L.Polygon ||
    rootLayer instanceof L.MultiPolygon
  ) {
    out.push(rootLayer);
  }

  return out;
}

function extractFeatureFromFeatureGroup(featureGroup) {
  if (!featureGroup) return null;

  const layers = [];
  featureGroup.eachLayer((layer) => {
    layers.push(...flattenPolygonLayers(layer));
  });

  if (!layers.length) return null;

  const gj = layers[0].toGeoJSON();
  if (!gj || gj.type !== 'Feature') return null;
  if (gj?.geometry?.type !== 'Polygon') return null;

  return gj;
}

function deleteVerticesFromFeatureByBounds(feature, bounds) {
  if (!feature || feature?.geometry?.type !== 'Polygon') {
    return { feature, removedCount: 0, changed: false, error: 'Invalid polygon feature.' };
  }

  const coords = feature.geometry.coordinates;
  if (!Array.isArray(coords) || !coords.length || !Array.isArray(coords[0])) {
    return { feature, removedCount: 0, changed: false, error: 'Invalid polygon coordinates.' };
  }

  const outerRing = coords[0];
  if (outerRing.length < 4) {
    return { feature, removedCount: 0, changed: false, error: 'Polygon ring too short.' };
  }

  const ringOpen = outerRing.slice(0, -1); // remove closing coordinate
  const kept = [];
  let removedCount = 0;

  for (const pt of ringOpen) {
    const lng = Number(pt[0]);
    const lat = Number(pt[1]);

    if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
      kept.push(pt);
      continue;
    }

    const inside = bounds.contains(L.latLng(lat, lng));
    if (inside) {
      removedCount += 1;
    } else {
      kept.push([lng, lat]);
    }
  }

  if (removedCount === 0) {
    return { feature, removedCount: 0, changed: false, error: null };
  }

  if (kept.length < 3) {
    return {
      feature,
      removedCount: 0,
      changed: false,
      error: 'Selection would remove too many vertices and invalidate the polygon.',
    };
  }

  const rebuiltRing = [...kept, kept[0]];
  const newFeature = {
    ...feature,
    geometry: {
      ...feature.geometry,
      coordinates: [rebuiltRing],
    },
  };

  return {
    feature: newFeature,
    removedCount,
    changed: true,
    error: null,
  };
}

function EditableRimLayer({
  geojson,
  featureGroupRef,
  editing,
  deleteBoxMode,
  onDirty,
  onDeleteBoxDone,
  onReplaceGeojson,
}) {
  const map = useMap();
  const workingLayersRef = useRef([]);

  useEffect(() => {
    const fg = featureGroupRef.current;
    if (!map || !fg || !geojson) return;

    fg.clearLayers();
    workingLayersRef.current = [];

    const layerGroup = L.geoJSON(geojson, {
      style: {
        color: '#0d6efd',
        weight: 3,
        opacity: 1,
        fill: false,
      },
    });

    layerGroup.eachLayer((layer) => {
      fg.addLayer(layer);
    });

    workingLayersRef.current = flattenPolygonLayers(layerGroup);

    return () => {
      try {
        fg.clearLayers();
      } catch (e) {
        // ignore
      }
      workingLayersRef.current = [];
    };
  }, [map, geojson, featureGroupRef]);

  useEffect(() => {
    const fg = featureGroupRef.current;
    if (!map || !fg) return;

    const layers = workingLayersRef.current || [];

    const markDirty = () => {
      if (onDirty) onDirty();
    };

    try {
      map.pm.removeControls();
    } catch (e) {
      // ignore
    }

    layers.forEach((layer) => {
      try {
        layer.off('pm:edit', markDirty);
        layer.off('pm:update', markDirty);
        layer.off('pm:vertexadded', markDirty);
        layer.off('pm:vertexremoved', markDirty);
        layer.off('pm:markerdragend', markDirty);
        layer.off('pm:dragend', markDirty);
      } catch (e) {
        // ignore
      }
    });

    if (!editing) {
      layers.forEach((layer) => {
        try {
          if (layer.pm) layer.pm.disable();
        } catch (e) {
          // ignore
        }
      });
      return;
    }

    map.pm.addControls({
      position: 'topleft',
      drawMarker: false,
      drawCircleMarker: false,
      drawPolyline: false,
      drawRectangle: false,
      drawCircle: false,
      drawText: false,
      drawPolygon: false,
      cutPolygon: false,
      rotateMode: false,
      editMode: false,
      dragMode: false,
      removalMode: false,
    });

    layers.forEach((layer) => {
      try {
        if (!layer.pm) return;

        layer.pm.enable({
          allowSelfIntersection: false,
          snappable: true,
          draggable: true,
        });

        layer.on('pm:edit', markDirty);
        layer.on('pm:update', markDirty);
        layer.on('pm:vertexadded', markDirty);
        layer.on('pm:vertexremoved', markDirty);
        layer.on('pm:markerdragend', markDirty);
        layer.on('pm:dragend', markDirty);
      } catch (e) {
        console.error('Enable editing failed:', e);
      }
    });

    return () => {
      layers.forEach((layer) => {
        try {
          layer.off('pm:edit', markDirty);
          layer.off('pm:update', markDirty);
          layer.off('pm:vertexadded', markDirty);
          layer.off('pm:vertexremoved', markDirty);
          layer.off('pm:markerdragend', markDirty);
          layer.off('pm:dragend', markDirty);
          if (layer.pm) layer.pm.disable();
        } catch (e) {
          // ignore
        }
      });

      try {
        map.pm.removeControls();
      } catch (e) {
        // ignore
      }
    };
  }, [map, featureGroupRef, editing, onDirty]);

  useEffect(() => {
    if (!map || !editing || !deleteBoxMode) return;

    const handleCreate = (e) => {
      try {
        if (e.shape !== 'Rectangle' || !e.layer) return;

        const bounds = e.layer.getBounds();
        try {
          map.removeLayer(e.layer);
        } catch (err) {
          // ignore
        }

        const fg = featureGroupRef.current;
        const currentFeature = extractFeatureFromFeatureGroup(fg);

        if (!currentFeature) {
          if (onDeleteBoxDone) {
            onDeleteBoxDone({
              ok: false,
              removedCount: 0,
              message: 'No editable rim geometry is currently loaded.',
            });
          }
          return;
        }

        const result = deleteVerticesFromFeatureByBounds(currentFeature, bounds);

        if (!result.changed) {
          if (onDeleteBoxDone) {
            onDeleteBoxDone({
              ok: false,
              removedCount: 0,
              message: result.error || 'No vertices were found inside the selected box.',
            });
          }
          return;
        }

        if (onReplaceGeojson) onReplaceGeojson(result.feature);
        if (onDirty) onDirty();

        if (onDeleteBoxDone) {
          onDeleteBoxDone({
            ok: true,
            removedCount: result.removedCount,
            message:
              result.removedCount === 1
                ? '1 vertex deleted from the selected box.'
                : `${result.removedCount} vertices deleted from the selected box.`,
          });
        }
      } catch (err) {
        console.error('Delete vertices by box failed:', err);
        if (onDeleteBoxDone) {
          onDeleteBoxDone({
            ok: false,
            removedCount: 0,
            message: err?.message || 'Failed to delete vertices by box.',
          });
        }
      }
    };

    map.on('pm:create', handleCreate);

    try {
      map.pm.enableDraw('Rectangle', {
        snappable: false,
        continueDrawing: false,
        pathOptions: {
          color: '#d97706',
          weight: 2,
        },
      });
    } catch (e) {
      console.error('Enable rectangle draw failed:', e);
      if (onDeleteBoxDone) {
        onDeleteBoxDone({
          ok: false,
          removedCount: 0,
          message: 'Unable to start rectangle selection mode.',
        });
      }
    }

    return () => {
      map.off('pm:create', handleCreate);
      try {
        map.pm.disableDraw('Rectangle');
      } catch (e) {
        // ignore
      }
    };
  }, [map, editing, deleteBoxMode, featureGroupRef, onDirty, onDeleteBoxDone, onReplaceGeojson]);

  return null;
}

export default function RimMapModal({ open, onClose, processId }) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState('');
  const [previewMeta, setPreviewMeta] = useState(null);
  const [rimPayload, setRimPayload] = useState(null);
  const [workingGeojson, setWorkingGeojson] = useState(null);
  const [isEditing, setIsEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [deleteBoxMode, setDeleteBoxMode] = useState(false);
  const [infoMessage, setInfoMessage] = useState('');

  const featureGroupRef = useRef(null);

  const loadData = useCallback(async () => {
    if (!processId) return;

    try {
      setLoading(true);
      setError('');
      setInfoMessage('');
      setPreviewMeta(null);
      setRimPayload(null);
      setWorkingGeojson(null);
      setDirty(false);
      setDeleteBoxMode(false);

      const [previewResp, rimResp] = await Promise.all([
        axios.get(`/outputs/${processId}/dem_preview.json`, {
          headers: { 'Cache-Control': 'no-cache' },
        }),
        axios.get(`/api/rim/${processId}`, {
          headers: { 'Cache-Control': 'no-cache' },
        }),
      ]);

      const rimData = rimResp.data || null;
      setPreviewMeta(previewResp.data || null);
      setRimPayload(rimData);
      setWorkingGeojson(rimData?.geojson || null);
    } catch (e) {
      console.error('RimMapModal load error:', e);
      setError(
        e?.response?.data?.error ||
          e?.message ||
          'Failed to load DEM preview or rim GeoJSON.'
      );
    } finally {
      setLoading(false);
    }
  }, [processId]);

  useEffect(() => {
    if (!open || !processId) return;
    loadData();
  }, [open, processId, loadData]);

  const previewBounds = useMemo(() => {
    const b = previewMeta?.bounds;
    if (!Array.isArray(b) || b.length !== 2) return null;
    return b;
  }, [previewMeta]);

  const rimGeojson = useMemo(() => workingGeojson || rimPayload?.geojson || null, [workingGeojson, rimPayload]);

  const previewUrl = useMemo(() => {
    if (!processId) return null;
    return `/outputs/${processId}/dem_preview.png`;
  }, [processId]);

  const handleDirty = useCallback(() => {
    setDirty(true);
  }, []);

  const handleSave = async () => {
    try {
      const fg = featureGroupRef.current;
      const feature = extractFeatureFromFeatureGroup(fg);

      if (!feature) {
        alert('No valid polygon is currently available to save.');
        return;
      }

      setSaving(true);
      setError('');
      setInfoMessage('');
      setDeleteBoxMode(false);

      await axios.post(`/api/rim/${processId}`, feature, {
        headers: { 'Content-Type': 'application/json' },
      });

      await loadData();
      setIsEditing(false);
      setDirty(false);
      setInfoMessage('Edited rim saved successfully.');
    } catch (e) {
      console.error('Save rim failed:', e);
      setError(
        e?.response?.data?.error ||
          e?.message ||
          'Failed to save edited rim.'
      );
    } finally {
      setSaving(false);
    }
  };

  const handleResetToAuto = async () => {
    try {
      setResetting(true);
      setError('');
      setInfoMessage('');
      setDeleteBoxMode(false);

      await axios.delete(`/api/rim/${processId}`);

      await loadData();
      setIsEditing(false);
      setDirty(false);
      setInfoMessage('Edited rim removed. Auto rim restored.');
    } catch (e) {
      console.error('Reset rim failed:', e);
      setError(
        e?.response?.data?.error ||
          e?.message ||
          'Failed to reset rim to auto.'
      );
    } finally {
      setResetting(false);
    }
  };

  const handleReloadActive = async () => {
    setIsEditing(false);
    setDirty(false);
    setDeleteBoxMode(false);
    setInfoMessage('');
    await loadData();
  };

  const handleStartEditing = () => {
    setIsEditing(true);
    setDirty(false);
    setDeleteBoxMode(false);
    setInfoMessage('');
  };

  const handleStopEditing = () => {
    setIsEditing(false);
    setDirty(false);
    setDeleteBoxMode(false);
    setInfoMessage('');
    loadData();
  };

  const handleStartDeleteByBox = () => {
    setDeleteBoxMode(true);
    setInfoMessage('Draw a rectangle on the map to delete all vertices inside it.');
    setError('');
  };

  const handleDeleteBoxDone = useCallback(({ ok, message }) => {
    setDeleteBoxMode(false);
    if (ok) {
      setInfoMessage(message || 'Vertices deleted.');
      setError('');
    } else {
      setError(message || 'No vertices were deleted.');
    }
  }, []);

  const handleReplaceGeojson = useCallback((feature) => {
    setWorkingGeojson(feature);
  }, []);

  const isBusy = loading || saving || resetting;
  const isEditedSource = rimPayload?.rim_source === 'edited';

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="xl"
      PaperProps={{
        sx: {
          height: '88vh',
          borderRadius: 3,
          overflow: 'hidden',
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          pr: 1,
          gap: 2,
        }}
      >
        <Box>
          <Typography variant="h6">Caldera rim map</Typography>
          <Typography variant="body2" sx={{ opacity: 0.75 }}>
            processId: <code>{processId || '-'}</code>
            {rimPayload?.rim_source ? <> — source: <b>{rimPayload.rim_source}</b></> : null}
            {isEditing ? <> — <b>editing</b></> : null}
            {deleteBoxMode ? <> — <b>delete-by-box</b></> : null}
          </Typography>
        </Box>

        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
          {!isEditing ? (
            <>
              <Button
                variant="outlined"
                onClick={handleStartEditing}
                disabled={isBusy || !rimGeojson}
              >
                Edit rim
              </Button>

              <Button
                variant="outlined"
                color="warning"
                onClick={handleResetToAuto}
                disabled={isBusy || !isEditedSource}
              >
                {resetting ? 'Resetting...' : 'Reset to auto'}
              </Button>
            </>
          ) : (
            <>
              <Button variant="outlined" onClick={handleStopEditing} disabled={saving || resetting}>
                Cancel
              </Button>
              <Button variant="outlined" onClick={handleReloadActive} disabled={saving || resetting}>
                Reload
              </Button>
              <Button
                variant="outlined"
                color="secondary"
                onClick={handleStartDeleteByBox}
                disabled={saving || resetting || deleteBoxMode}
              >
                {deleteBoxMode ? 'Draw box...' : 'Delete vertices by box'}
              </Button>
              <Button
                variant="outlined"
                color="warning"
                onClick={handleResetToAuto}
                disabled={saving || resetting || !isEditedSource}
              >
                {resetting ? 'Resetting...' : 'Reset to auto'}
              </Button>
              <Button variant="contained" onClick={handleSave} disabled={saving || resetting || !dirty}>
                {saving ? 'Saving...' : 'Save rim'}
              </Button>
            </>
          )}

          <IconButton onClick={onClose} disabled={isBusy}>
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent sx={{ p: 0, position: 'relative' }}>
        {loading ? (
          <Box
            sx={{
              height: '100%',
              minHeight: 500,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 2,
            }}
          >
            <CircularProgress />
            <Typography>Loading DEM preview and rim...</Typography>
          </Box>
        ) : error ? (
          <Box sx={{ p: 2 }}>
            <Alert severity="error">{error}</Alert>
          </Box>
        ) : previewBounds && previewUrl && rimGeojson ? (
          <Box sx={{ height: '100%', minHeight: 500, display: 'flex', flexDirection: 'column' }}>
            {isEditing ? (
              <Box sx={{ p: 1.5, pb: 0 }}>
                <Alert severity={deleteBoxMode ? 'warning' : 'info'} sx={{ alignItems: 'flex-start' }}>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    Editing tips
                  </Typography>
                  <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                    <li>
                      <Typography variant="body2">
                        Drag a vertex to move it.
                      </Typography>
                    </li>
                    <li>
                      <Typography variant="body2">
                        Click on an edge handle to add a new vertex.
                      </Typography>
                    </li>
                    <li>
                      <Typography variant="body2">
                        Right-click a vertex marker to delete that vertex.
                      </Typography>
                    </li>
                    <li>
                      <Typography variant="body2">
                        Use <b>Delete vertices by box</b> to draw a rectangle and remove multiple vertices at once.
                      </Typography>
                    </li>
                  </Box>
                </Alert>
              </Box>
            ) : null}

            {infoMessage ? (
              <Box sx={{ p: 1.5, pb: 0 }}>
                <Alert severity="success">{infoMessage}</Alert>
              </Box>
            ) : null}

            <Box sx={{ flex: 1, minHeight: 500 }}>
              <MapContainer
                center={[0, 0]}
                zoom={2}
                style={{ height: '100%', width: '100%', background: '#111' }}
                zoomControl={true}
                attributionControl={false}
              >
                <ImageOverlay url={previewUrl} bounds={previewBounds} opacity={1.0} />

                <FeatureGroup ref={featureGroupRef} />

                <EditableRimLayer
                  geojson={rimGeojson}
                  featureGroupRef={featureGroupRef}
                  editing={isEditing}
                  deleteBoxMode={deleteBoxMode}
                  onDirty={handleDirty}
                  onDeleteBoxDone={handleDeleteBoxDone}
                  onReplaceGeojson={handleReplaceGeojson}
                />

                <FitToData previewBounds={previewBounds} geojson={rimGeojson} />
              </MapContainer>
            </Box>
          </Box>
        ) : (
          <Box sx={{ p: 2 }}>
            <Alert severity="warning">
              Missing DEM preview bounds or rim GeoJSON.
            </Alert>
          </Box>
        )}
      </DialogContent>
    </Dialog>
  );
}