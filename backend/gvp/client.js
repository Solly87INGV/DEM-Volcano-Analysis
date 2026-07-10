// backend/gvp/client.js
// -----------------------------------------------------------------------------
// GVP client (Sett 3-4)
//
// Loads a static Holocene GVP snapshot at startup and exposes:
//   - resolveByVnum(vnum): full record or null
//   - searchByName(query, limit): array of records (case-insensitive substring)
//   - normalizeGvpType(str): mirror of Python _normalize_gvp_type
//   - resolvePresetsForType(gvpType): mirror of Python _GVP_TYPE_TO_PRESETS
//   - getStatus(): loader status for /api/gvp/status
//
// Design invariants:
//   1) The mapping resolvePresetsForType() must stay 1:1 with
//      _GVP_TYPE_TO_PRESETS and _normalize_gvp_type in volume_unified.py.
//      Any change on one side requires a manual mirror update on the other,
//      followed by the smoke test in backend/scripts/README.md (or the
//      inline unit block at the bottom of this file, run with `node client.js`).
//   2) This module is a UI/API convenience only. The authoritative preset
//      selection at compute time is performed by Python from the GVP_TYPE
//      env var passed by server.js.
// -----------------------------------------------------------------------------

'use strict';

const fs = require('fs');
const path = require('path');

// ---------------------------
// Python mapping mirror
// ---------------------------
// Must match _GVP_TYPE_TO_PRESETS in volume_unified.py.
const GVP_TYPE_TO_PRESETS = Object.freeze({
  // Shield-class edifices: island-style base contour, dedicated shield rim preset
  'shield':            { base: 'island', rim: 'shield' },
  'shields':           { base: 'island', rim: 'shield' },
  'shield volcano':    { base: 'island', rim: 'shield' },
  'shield volcanoes':  { base: 'island', rim: 'shield' },
  // Continental / stratovolcano / complex / caldera family
  'caldera':           { base: 'continental', rim: 'continental' },
  'calderas':          { base: 'continental', rim: 'continental' },
  'stratovolcano':     { base: 'continental', rim: 'continental' },
  'stratovolcanoes':   { base: 'continental', rim: 'continental' },
  'stratovolcanos':    { base: 'continental', rim: 'continental' },
  'complex':           { base: 'continental', rim: 'continental' },
  'complex volcano':   { base: 'continental', rim: 'continental' },
  'compound':          { base: 'continental', rim: 'continental' },
  'compound volcano':  { base: 'continental', rim: 'continental' },
  'somma':             { base: 'continental', rim: 'continental' },
  'somma volcano':     { base: 'continental', rim: 'continental' },
  // Small isolated edifices
  'pyroclastic cone':   { base: 'island', rim: 'island' },
  'pyroclastic cones':  { base: 'island', rim: 'island' },
  'tuff cone':          { base: 'island', rim: 'island' },
  'tuff cones':         { base: 'island', rim: 'island' },
  'lava dome':          { base: 'island', rim: 'island' },
  'lava domes':         { base: 'island', rim: 'island' },
  'maar':               { base: 'island', rim: 'island' },
  'maars':              { base: 'island', rim: 'island' },
});

/**
 * Mirror of Python _normalize_gvp_type():
 *   lower, strip, trim trailing "(s)".
 */
function normalizeGvpType(v) {
  let s = String(v == null ? '' : v).trim().toLowerCase();
  if (s.endsWith('(s)')) {
    s = s.slice(0, -3).trim();
  }
  return s;
}

/**
 * Resolve GVP type -> preset hint. Returns:
 *   { matched: true, gvp_type_raw, gvp_type_normalized, base_preset, rim_preset }
 * or
 *   { matched: false, gvp_type_raw, gvp_type_normalized, reason }
 *
 * reason: 'gvp_type_absent' | 'gvp_type_unknown'
 *
 * Note: this only reproduces the *lookup*; it does not attempt to reproduce
 * Python's legacy fallback via BASE_PROFILE, because that fallback depends
 * on runtime env and is not meaningful for a UI hint.
 */
function resolvePresetsForType(gvpType) {
  const raw = String(gvpType == null ? '' : gvpType);
  const norm = normalizeGvpType(raw);
  if (!norm) {
    return {
      matched: false,
      gvp_type_raw: raw,
      gvp_type_normalized: norm,
      reason: 'gvp_type_absent',
    };
  }
  const hit = GVP_TYPE_TO_PRESETS[norm];
  if (hit) {
    return {
      matched: true,
      gvp_type_raw: raw,
      gvp_type_normalized: norm,
      base_preset: hit.base,
      rim_preset: hit.rim,
    };
  }
  return {
    matched: false,
    gvp_type_raw: raw,
    gvp_type_normalized: norm,
    reason: 'gvp_type_unknown',
  };
}

// ---------------------------
// Dataset loading
// ---------------------------
const _state = {
  loaded: false,
  loadError: null,
  datasetPath: null,
  meta: null,
  byVnum: new Map(),        // key: string vnum
  all: [],                  // array of records, insertion order
  loadedAt: null,
};

function _coerceVnum(v) {
  // vnum is always stored as string; strip whitespace, keep as-is otherwise.
  if (v == null) return '';
  return String(v).trim();
}

/**
 * Load the dataset from disk. Idempotent: subsequent calls are no-ops unless
 * `force: true` is passed.
 */
function loadGvpDataset(datasetPath, opts = {}) {
  const force = !!opts.force;
  if (_state.loaded && !force) return _state;

  _state.datasetPath = datasetPath;
  _state.loaded = false;
  _state.loadError = null;
  _state.meta = null;
  _state.byVnum = new Map();
  _state.all = [];

  try {
    const raw = fs.readFileSync(datasetPath, 'utf-8');
    const parsed = JSON.parse(raw);

    if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.records)) {
      throw new Error('Invalid dataset: missing `records` array.');
    }

    _state.meta = parsed._meta && typeof parsed._meta === 'object' ? parsed._meta : {};
    for (const rec of parsed.records) {
      if (!rec || typeof rec !== 'object') continue;
      const vnum = _coerceVnum(rec.vnum);
      if (!vnum) continue;
      const record = { ...rec, vnum };
      _state.byVnum.set(vnum, record);
      _state.all.push(record);
    }

    _state.loaded = true;
    _state.loadedAt = new Date().toISOString();
    console.log(
      `[GVP] loaded ${_state.all.length} records from ${datasetPath} ` +
      `(snapshot_version=${_state.meta.snapshot_version || 'unknown'}, ` +
      `snapshot_date=${_state.meta.snapshot_date || 'unknown'})`
    );
  } catch (err) {
    _state.loadError = err && err.message ? err.message : String(err);
    console.error(`[GVP] failed to load dataset from ${datasetPath}: ${_state.loadError}`);
  }

  return _state;
}

// ---------------------------
// Query API
// ---------------------------
function resolveByVnum(vnum) {
  const key = _coerceVnum(vnum);
  if (!key) return null;
  return _state.byVnum.get(key) || null;
}

function searchByName(query, limit = 10) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return [];
  const out = [];
  const cap = Math.max(1, Math.min(50, Number(limit) || 10));
  for (const rec of _state.all) {
    const name = String(rec.name || '').toLowerCase();
    if (name.includes(q)) {
      out.push(rec);
      if (out.length >= cap) break;
    }
  }
  return out;
}

function getStatus() {
  return {
    loaded: _state.loaded,
    loadError: _state.loadError,
    datasetPath: _state.datasetPath,
    records_count: _state.all.length,
    meta: _state.meta,
    loadedAt: _state.loadedAt,
  };
}

// ---------------------------
// Exports
// ---------------------------
module.exports = {
  loadGvpDataset,
  resolveByVnum,
  searchByName,
  normalizeGvpType,
  resolvePresetsForType,
  getStatus,
  // exported for tests
  _GVP_TYPE_TO_PRESETS: GVP_TYPE_TO_PRESETS,
};

// ---------------------------
// Standalone smoke test:
//   node backend/gvp/client.js [path/to/gvp_holocene.json]
// ---------------------------
if (require.main === module) {
  const argPath = process.argv[2] || path.join(__dirname, '..', 'data', 'gvp_holocene.json');
  console.log(`[smoke] loading ${argPath}`);
  loadGvpDataset(argPath);
  const status = getStatus();
  console.log('[smoke] status:', JSON.stringify(status, null, 2));

  const cases = [
    { vnum: '233020', expectedType: 'Shield',        expectedPresets: ['island', 'shield'] },
    { vnum: '221080', expectedType: 'Shield',        expectedPresets: ['island', 'shield'] },
    { vnum: '268060', expectedType: 'Caldera',       expectedPresets: ['continental', 'continental'] },
    { vnum: '358041', expectedType: 'Caldera',       expectedPresets: ['continental', 'continental'] },
  ];

  let failed = 0;
  for (const c of cases) {
    const rec = resolveByVnum(c.vnum);
    const res = rec ? resolvePresetsForType(rec.primary_volcano_type) : null;
    const ok =
      rec &&
      rec.primary_volcano_type === c.expectedType &&
      res && res.matched &&
      res.base_preset === c.expectedPresets[0] &&
      res.rim_preset === c.expectedPresets[1];
    console.log(
      `[smoke] vnum=${c.vnum} name=${rec ? rec.name : '(missing)'} ` +
      `type=${rec ? rec.primary_volcano_type : '-'} ` +
      `presets=${res && res.matched ? `(${res.base_preset},${res.rim_preset})` : '(no match)'} ` +
      `-> ${ok ? 'OK' : 'FAIL'}`
    );
    if (!ok) failed++;
  }

  // Alias / normalization edge cases (do not depend on the dataset)
  const aliasCases = [
    ['Shield(s)', 'shield'],
    ['SHIELD ', 'shield'],
    ['stratovolcano', 'stratovolcano'],
    ['Caldera(s)', 'caldera'],
    ['Complex volcano', 'complex volcano'],
    ['', ''],
    [null, ''],
    ['Fissure vents', 'fissure vents'],  // unknown, should normalize but not match
  ];
  for (const [raw, expectedNorm] of aliasCases) {
    const gotNorm = normalizeGvpType(raw);
    const ok = gotNorm === expectedNorm;
    console.log(
      `[smoke] normalize raw=${JSON.stringify(raw)} -> ${JSON.stringify(gotNorm)} ` +
      `expected=${JSON.stringify(expectedNorm)} -> ${ok ? 'OK' : 'FAIL'}`
    );
    if (!ok) failed++;
  }

  process.exit(failed === 0 ? 0 : 1);
}
