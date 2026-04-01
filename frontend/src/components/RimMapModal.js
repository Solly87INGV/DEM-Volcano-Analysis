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

function EditableRimLayer({
  geojson,
  featureGroupRef,
  editing,
  onDirty,
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

  return null;
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

export default function RimMapModal({ open, onClose, processId }) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState('');
  const [previewMeta, setPreviewMeta] = useState(null);
  const [rimPayload, setRimPayload] = useState(null);
  const [isEditing, setIsEditing] = useState(false);
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
    loadData();
  }, [open, processId, loadData]);

  const previewBounds = useMemo(() => {
    const b = previewMeta?.bounds;
    if (!Array.isArray(b) || b.length !== 2) return null;
    return b;
  }, [previewMeta]);

  const rimGeojson = useMemo(() => rimPayload?.geojson || null, [rimPayload]);

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

      await axios.post(`/api/rim/${processId}`, feature, {
        headers: { 'Content-Type': 'application/json' },
      });

      await loadData();
      setIsEditing(false);
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

      await loadData();
      setIsEditing(false);
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
    setDirty(false);
    await loadData();
  };

  const handleStartEditing = () => {
    setIsEditing(true);
    setDirty(false);
  };

  const handleStopEditing = () => {
    setIsEditing(false);
    setDirty(false);
    loadData();
  };

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
                  onDirty={handleDirty}
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