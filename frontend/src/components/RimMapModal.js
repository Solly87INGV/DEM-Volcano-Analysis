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

function FitToData({ previewBounds, geojson, drawingNew = false }) {
  const map = useMap();

  useEffect(() => {
    try {
      if (!drawingNew && geojson) {
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
  }, [map, previewBounds, geojson, drawingNew]);

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

function EditableRimLayer({
  geojson,
  featureGroupRef,
  editing,
  drawingNew = false,
  onDirty,
}) {
  const map = useMap();
  const workingLayersRef = useRef([]);

  useEffect(() => {
    const fg = featureGroupRef.current;
    if (!map || !fg || !geojson) return;

    fg.clearLayers();
    workingLayersRef.current = [];
    if (drawingNew) return;

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
  }, [map, geojson, featureGroupRef, drawingNew]);

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

  return null;
}

function DrawNewRimLayer({ featureGroupRef, mode, onDirty, onComplete }) {
  const map = useMap();
  const freehandRef = useRef({
    active: false,
    points: [],
    guide: null,
    dragWasEnabled: true,
  });

  useEffect(() => {
    const fg = featureGroupRef.current;
    if (!map || !fg || !mode) return;

    const stopDraw = () => {
      try {
        map.pm.disableDraw();
      } catch (e) {
        // ignore
      }
    };

    stopDraw();

    if (mode === 'polygon') {
      const handleCreate = (e) => {
        if (!e?.layer || !(e.layer instanceof L.Polygon)) return;

        try {
          map.removeLayer(e.layer);
        } catch (err) {
          // ignore
        }

        fg.clearLayers();

        e.layer.setStyle({
          color: '#0d6efd',
          weight: 3,
          opacity: 1,
          fill: false,
        });

        fg.addLayer(e.layer);
        stopDraw();

        if (onDirty) onDirty('manual_vertex_draw');
        if (onComplete) onComplete();
      };

      map.on('pm:create', handleCreate);

      map.pm.enableDraw('Polygon', {
        allowSelfIntersection: false,
        snappable: false,
        finishOn: 'dblclick',
        pathOptions: {
          color: '#0d6efd',
          weight: 3,
          opacity: 1,
          fill: false,
        },
      });

      return () => {
        map.off('pm:create', handleCreate);
        stopDraw();
      };
    }

    if (mode === 'freehand') {
      const state = freehandRef.current;
      const MIN_PIXEL_DISTANCE = 5;

      const removeGuide = () => {
        if (!state.guide) return;

        try {
          map.removeLayer(state.guide);
        } catch (e) {
          // ignore
        }

        state.guide = null;
      };

      const start = (e) => {
        if (!e?.latlng) return;

        state.active = true;
        state.points = [e.latlng];
        state.dragWasEnabled = !!map.dragging?.enabled?.();

        if (state.dragWasEnabled) {
          map.dragging.disable();
        }

        removeGuide();

        state.guide = L.polyline(state.points, {
          color: '#0d6efd',
          weight: 2,
          opacity: 1,
          interactive: false,
        }).addTo(map);
      };

      const move = (e) => {
        if (!state.active || !e?.latlng) return;

        const last = state.points[state.points.length - 1];
        if (!last) return;

        const p1 = map.latLngToContainerPoint(last);
        const p2 = map.latLngToContainerPoint(e.latlng);

        if (p1.distanceTo(p2) < MIN_PIXEL_DISTANCE) return;

        state.points.push(e.latlng);

        if (state.guide) {
          state.guide.setLatLngs(state.points);
        }
      };

      const finish = () => {
        if (!state.active) return;

        state.active = false;

        if (
          state.dragWasEnabled &&
          map.dragging &&
          !map.dragging.enabled()
        ) {
          map.dragging.enable();
        }

        const points = [...state.points];

        state.points = [];
        removeGuide();

        if (points.length < 3) return;

        fg.clearLayers();

        fg.addLayer(
          L.polygon(points, {
            color: '#0d6efd',
            weight: 3,
            opacity: 1,
            fill: false,
          })
        );

        if (onDirty) onDirty('manual_freehand');
        if (onComplete) onComplete();
      };

      map.on('mousedown', start);
      map.on('mousemove', move);
      map.on('mouseup', finish);
      map.on('mouseout', finish);

      return () => {
        map.off('mousedown', start);
        map.off('mousemove', move);
        map.off('mouseup', finish);
        map.off('mouseout', finish);

        if (
          state.dragWasEnabled &&
          map.dragging &&
          !map.dragging.enabled()
        ) {
          map.dragging.enable();
        }

        removeGuide();

        state.active = false;
        state.points = [];
      };
    }
  }, [map, featureGroupRef, mode, onDirty, onComplete]);

  return null;
}

function extractFeatureFromFeatureGroup(featureGroup, rimEditMethod = '') {
  if (!featureGroup) return null;

  const layers = [];

  featureGroup.eachLayer((layer) => {
    layers.push(...flattenPolygonLayers(layer));
  });

  if (layers.length !== 1) return null;

  const gj = layers[0].toGeoJSON();

  if (!gj || gj.type !== 'Feature') return null;
  if (gj?.geometry?.type !== 'Polygon') return null;

  gj.properties = {
    ...(gj.properties || {}),
  };

  if (rimEditMethod) {
    gj.properties.rim_edit_method = rimEditMethod;
  }

  return gj;
}

export default function RimMapModal({ open, onClose, processId }) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState('');
  const [previewMeta, setPreviewMeta] = useState(null);
  const [rimPayload, setRimPayload] = useState(null);
  const [isEditing, setIsEditing] = useState(false);
  const [drawMode, setDrawMode] = useState(null);
  const [drawingComplete, setDrawingComplete] = useState(false);
  const [rimEditMethod, setRimEditMethod] = useState('');
  const [dirty, setDirty] = useState(false);

  const featureGroupRef = useRef(null);

  const loadData = useCallback(async () => {
    if (!processId) return;

    try {
      setLoading(true);
      setError('');
      setPreviewMeta(null);
      setRimPayload(null);
      setDirty(false);
      setRimEditMethod('');
      setDrawingComplete(false);

      const [previewResp, rimResp] = await Promise.all([
        axios.get(`/outputs/${processId}/dem_preview.json`, {
          headers: { 'Cache-Control': 'no-cache' },
        }),
        axios.get(`/api/rim/${processId}`, {
          headers: { 'Cache-Control': 'no-cache' },
        }),
      ]);

      setPreviewMeta(previewResp.data || null);
      setRimPayload(rimResp.data || null);
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

    setIsEditing(false);
    setDrawMode(null);

    loadData();
  }, [open, processId, loadData]);

  const previewBounds = useMemo(() => {
    const b = previewMeta?.bounds;

    if (!Array.isArray(b) || b.length !== 2) return null;

    return b;
  }, [previewMeta]);

  const rimGeojson = useMemo(
    () => rimPayload?.geojson || null,
    [rimPayload]
  );

  const previewUrl = useMemo(() => {
    if (!processId) return null;

    return `/outputs/${processId}/dem_preview.png`;
  }, [processId]);

  const handleDirty = useCallback((method = '') => {
    setDirty(true);

    if (method) {
      setRimEditMethod(method);
    }
  }, []);

  const handleDrawingComplete = useCallback(() => {
    setDrawingComplete(true);
  }, []);

  const handleSave = async () => {
    try {
      const fg = featureGroupRef.current;
      const feature = extractFeatureFromFeatureGroup(
        fg,
        rimEditMethod
      );

      if (!feature) {
        alert('No valid polygon is currently available to save.');
        return;
      }

      setSaving(true);
      setError('');

      await axios.post(`/api/rim/${processId}`, feature, {
        headers: {
          'Content-Type': 'application/json',
        },
      });

      setIsEditing(false);
      setDrawMode(null);

      await loadData();

      setDirty(false);
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

      await axios.delete(`/api/rim/${processId}`);

      setIsEditing(false);
      setDrawMode(null);

      await loadData();

      setDirty(false);
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
    setDrawMode(null);
    setDirty(false);

    await loadData();
  };

  const handleStartEditing = () => {
    setIsEditing(true);
    setDrawMode(null);
    setRimEditMethod('vertex_edit');
    setDirty(false);
  };

  const handleStartDrawing = (mode) => {
    if (featureGroupRef.current) {
      featureGroupRef.current.clearLayers();
    }

    setIsEditing(false);
    setDrawMode(mode);
    setDrawingComplete(false);
    setRimEditMethod('');
    setDirty(false);
  };

  const handleStopEditing = () => {
    setIsEditing(false);
    setDrawMode(null);
    setDirty(false);

    loadData();
  };

  const isBusy = loading || saving || resetting;
  const isEditedSource = rimPayload?.rim_source === 'edited';
  const isWorking = isEditing || !!drawMode;

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
          <Typography variant="h6">
            Caldera rim map
          </Typography>

          <Typography variant="body2" sx={{ opacity: 0.75 }}>
            processId: <code>{processId || '-'}</code>

            {rimPayload?.rim_source ? (
              <>
                {' '}
                — source: <b>{rimPayload.rim_source}</b>
              </>
            ) : null}

            {isEditing ? (
              <>
                {' '}
                — <b>editing</b>
              </>
            ) : null}

            {drawMode === 'polygon' ? (
              <>
                {' '}
                — <b>drawing new rim</b>
              </>
            ) : null}

            {drawMode === 'freehand' ? (
              <>
                {' '}
                — <b>freehand rim</b>
              </>
            ) : null}
          </Typography>
        </Box>

        <Box
          sx={{
            display: 'flex',
            gap: 1,
            alignItems: 'center',
            flexWrap: 'wrap',
          }}
        >
          {!isWorking ? (
            <>
              <Button
                variant="outlined"
                onClick={handleStartEditing}
                disabled={isBusy || !rimGeojson}
              >
                Edit rim
              </Button>

              <Button
                variant="contained"
                onClick={() => handleStartDrawing('polygon')}
                disabled={isBusy || !rimGeojson}
              >
                Draw new rim
              </Button>

              <Button
                variant="outlined"
                onClick={() => handleStartDrawing('freehand')}
                disabled={isBusy || !rimGeojson}
              >
                Freehand rim
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
              <Button
                variant="outlined"
                onClick={handleStopEditing}
                disabled={saving || resetting}
              >
                Cancel
              </Button>

              <Button
                variant="outlined"
                onClick={handleReloadActive}
                disabled={saving || resetting}
              >
                Reload
              </Button>

              <Button
                variant="outlined"
                color="warning"
                onClick={handleResetToAuto}
                disabled={saving || resetting || !isEditedSource}
              >
                {resetting ? 'Resetting...' : 'Reset to auto'}
              </Button>

              <Button
                variant="contained"
                onClick={handleSave}
                disabled={saving || resetting || !dirty}
              >
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

            <Typography>
              Loading DEM preview and rim...
            </Typography>
          </Box>
        ) : error ? (
          <Box sx={{ p: 2 }}>
            <Alert severity="error">
              {error}
            </Alert>
          </Box>
        ) : previewBounds && previewUrl && rimGeojson ? (
          <Box
            sx={{
              height: '100%',
              minHeight: 500,
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            {isEditing ? (
              <Box sx={{ p: 1.5, pb: 0 }}>
                <Alert severity="info" sx={{ alignItems: 'flex-start' }}>
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
                  </Box>
                </Alert>
              </Box>
            ) : null}

            {drawMode ? (
              <Box sx={{ p: 1.5, pb: 0 }}>
                <Alert severity={drawingComplete ? 'success' : 'info'}>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    {drawMode === 'freehand'
                      ? 'Draw a new rim freehand'
                      : 'Draw a new rim'}
                  </Typography>

                  <Typography variant="body2">
                    {drawingComplete
                      ? 'New rim ready. Check it on the DEM, then click Save rim.'
                      : drawMode === 'freehand'
                        ? 'Hold the left mouse button, trace the caldera rim, then release. The previous rim is only hidden locally until Save rim.'
                        : 'Click vertex-by-vertex around the caldera and double-click to close the polygon. The previous rim is only hidden locally until Save rim.'}
                  </Typography>
                </Alert>
              </Box>
            ) : null}

            <Box sx={{ flex: 1, minHeight: 500 }}>
              <MapContainer
                center={[0, 0]}
                zoom={2}
                style={{
                  height: '100%',
                  width: '100%',
                  background: '#111',
                }}
                zoomControl={true}
                attributionControl={false}
              >
                <ImageOverlay
                  url={previewUrl}
                  bounds={previewBounds}
                  opacity={1.0}
                />

                <FeatureGroup ref={featureGroupRef} />

                <EditableRimLayer
                  geojson={rimGeojson}
                  featureGroupRef={featureGroupRef}
                  editing={isEditing}
                  drawingNew={!!drawMode}
                  onDirty={handleDirty}
                />

                <DrawNewRimLayer
                  featureGroupRef={featureGroupRef}
                  mode={drawMode}
                  onDirty={handleDirty}
                  onComplete={handleDrawingComplete}
                />

                <FitToData
                  previewBounds={previewBounds}
                  geojson={rimGeojson}
                  drawingNew={!!drawMode}
                />
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