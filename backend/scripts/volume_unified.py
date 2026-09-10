# backend/scripts/volume_unified.py
# -----------------------------------------------------------------------------
# MorphoVolc 2.0 — Volume Unified
#
# Single scientific pipeline:
# - Edifice volume: prismoid/frustum formula using measured areas from contours:
#       V_edifice = h/3 * (A_base + A_caldera + sqrt(A_base*A_caldera))
# - Caldera volume: depth-integrated (rim reference - DEM) inside caldera mask
# - Effective volume: V_effective = max(0, V_edifice - V_caldera)
#
# Two scenario presets (baseProfile):
# - island: "simple base" (more selective, assumes isolated edifice)
# - continental: "complex base" (more robust to merged topography)
#
# Inputs:
# - Env (recommended):
#     PROCESS_ID, OUTPUTS_DIR, HEADLESS
#     BASE_PROFILE: island|continental
#     MODULE_KEY: "unified_{baseProfile}"
#     ORIGINAL_FILE_NAME, ORIGINAL_FILE_STEM (optional)
#     USE_EDITED_RIM=1|0
#     RIM_PATH=<outputs/<PID>/caldera_rim_edited.geojson>
# - CLI (compat):
#     python volume_unified.py <dem_path> [original_file_stem]
#
# Outputs in OUTPUTS_DIR/<PROCESS_ID>/:
# - metrics.json, metrics.csv
# - volume_results.json (UI-ready)
# - final_doublet_base_vs_caldera.png
# - caldera_rim_auto.geojson
# - dem_preview.png
# - dem_preview.json
# -----------------------------------------------------------------------------

import os
import sys
import json
import csv
import re
import math
import time
import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

import numpy as np

# -------------------- PROJ/GDAL safety (Windows vs Docker) --------------------
def _fix_proj_env():
    try:
        is_windows = (os.name == "nt")
        if is_windows:
            try:
                from pyproj import datadir
                proj_dir = datadir.get_data_dir()
                if proj_dir and os.path.isdir(proj_dir):
                    os.environ["PROJ_LIB"] = proj_dir
                    os.environ.pop("PROJ_DATA", None)
                    print(f"[DEBUG] PROJ_LIB forced to pyproj (Windows): {proj_dir}")
            except Exception as e:
                print(f"[WARN] Unable to force PROJ_LIB to pyproj (Windows): {e}")
            return

        prev_proj_lib = os.environ.pop("PROJ_LIB", None)
        prev_proj_data = os.environ.pop("PROJ_DATA", None)
        if prev_proj_lib is not None:
            print(f"[DEBUG] PROJ_LIB unset (non-Windows): {prev_proj_lib}")
        if prev_proj_data is not None:
            print(f"[DEBUG] PROJ_DATA unset (non-Windows): {prev_proj_data}")
    except Exception as e:
        print(f"[WARN] Unable to fix PROJ env: {e}")

_fix_proj_env()

def _is_headless() -> bool:
    v = str(os.environ.get("HEADLESS", "")).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

HEADLESS = _is_headless()
if HEADLESS:
    os.environ.setdefault("MPLBACKEND", "Agg")

import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.warp import transform as rio_transform
from rasterio.crs import CRS as RioCRS

from scipy.ndimage import (
    sobel,
    gaussian_filter,
    binary_dilation,
    binary_closing,
    binary_fill_holes,
    binary_erosion,
    label,
)

from skimage import measure
from skimage.draw import polygon

import matplotlib.pyplot as plt
from matplotlib import gridspec
from mpl_toolkits.axes_grid1 import make_axes_locatable


# -------------------- paths / ids --------------------
def _script_dir() -> Path:
    return Path(__file__).resolve().parent

def _outputs_base_dir() -> Path:
    base = (os.environ.get("OUTPUTS_DIR") or os.environ.get("OUTPUTS_BASE") or "").strip()
    if base:
        return Path(base).resolve()
    return (_script_dir() / "outputs").resolve()

def _resolve_process_id() -> str:
    pid = (os.environ.get("PROCESS_ID") or "").strip()
    if pid:
        return pid
    return f"local_{int(time.time())}"

def _ensure_proc_dir(process_id: str) -> Path:
    d = _outputs_base_dir() / process_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def _public_path(process_id: str, filename: str) -> str:
    return f"/outputs/{process_id}/{filename}"

def _normalize_base_profile(v: str) -> str:
    s = str(v or "").strip().lower()
    if s in ("island", "simple", "simple_base"):
        return "island"
    if s in ("continental", "complex", "complex_base"):
        return "continental"
    if s in ("shield",):
        return "shield"
    return s or "continental"


# -------------------- GVP-informed preset selection (Week 1-2) --------------------
# Maps GVP primary volcano type -> (base_preset, rim_preset).
# Rationale and per-parameter motivation: see gvp_profile_mapping.md.
# Retro-compatible: when GVP_TYPE is absent or unknown, falls back to the
# legacy behaviour driven by BASE_PROFILE.
_GVP_TYPE_TO_PRESETS: Dict[str, Tuple[str, str]] = {
    # Shield-class edifices: island-style base contour, dedicated shield rim preset
    "shield":            ("island", "shield"),
    "shields":           ("island", "shield"),
    "shield volcano":    ("island", "shield"),
    "shield volcanoes":  ("island", "shield"),
    # Continental / stratovolcano / complex / caldera family
    "caldera":           ("continental", "continental"),
    "calderas":          ("continental", "continental"),
    "stratovolcano":     ("continental", "continental"),
    "stratovolcanoes":   ("continental", "continental"),
    "stratovolcanos":    ("continental", "continental"),
    "complex":           ("continental", "continental"),
    "complex volcano":   ("continental", "continental"),
    "compound":          ("continental", "continental"),
    "compound volcano":  ("continental", "continental"),
    "somma":             ("continental", "continental"),
    "somma volcano":     ("continental", "continental"),
    # Small isolated edifices
    "pyroclastic cone":   ("island", "island"),
    "pyroclastic cones":  ("island", "island"),
    "tuff cone":          ("island", "island"),
    "tuff cones":         ("island", "island"),
    "lava dome":          ("island", "island"),
    "lava domes":         ("island", "island"),
    "maar":               ("island", "island"),
    "maars":              ("island", "island"),
}


def _normalize_gvp_type(v: str) -> str:
    """Lowercase/strip; remove any parenthesised qualifier, e.g. 'Shield(pyroclastic)' -> 'shield', 'Caldera(s)' -> 'caldera'."""
    s = str(v or "").strip().lower()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_presets(
    base_profile_env: str,
    gvp_type: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Resolve (base_preset, rim_preset) given the legacy BASE_PROFILE env value
    and an optional GVP primary type.

    Returns (base_preset, rim_preset, source_debug) where source_debug records
    how the choice was made — this ends up in metrics.json for traceability.
    """
    legacy_bp = _normalize_base_profile(base_profile_env)
    gvp_norm = _normalize_gvp_type(gvp_type) if gvp_type else ""

    if gvp_norm and gvp_norm in _GVP_TYPE_TO_PRESETS:
        base_preset, rim_preset = _GVP_TYPE_TO_PRESETS[gvp_norm]
        source = {
            "preset_source": "gvp_mapping",
            "gvp_type_raw": str(gvp_type),
            "gvp_type_normalized": gvp_norm,
            "base_preset": base_preset,
            "rim_preset": rim_preset,
            "base_profile_env": legacy_bp,
        }
        return base_preset, rim_preset, source

    # Fallback: legacy behaviour — base and rim both driven by BASE_PROFILE
    source = {
        "preset_source": "base_profile_env",
        "gvp_type_raw": str(gvp_type) if gvp_type else "",
        "gvp_type_normalized": gvp_norm,
        "base_preset": legacy_bp,
        "rim_preset": legacy_bp,
        "base_profile_env": legacy_bp,
        "reason": "gvp_type_absent" if not gvp_norm else "gvp_type_unknown",
    }
    return legacy_bp, legacy_bp, source


# -------------------- logging --------------------
def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[STEP4B {ts}] {msg}", flush=True)


# -------------------- DEM selection (manifest-first) --------------------
def _pick_dem_manifest_first(proc_dir: Path, cli_dem: Optional[str]) -> Tuple[Path, str]:
    dem_working = proc_dir / "dem_working.tif"
    if dem_working.exists():
        return dem_working, "process_dir/dem_working.tif"
    if cli_dem:
        p = Path(cli_dem).expanduser().resolve()
        if p.exists():
            return p, "cli_dem_path"
    return dem_working, "missing"


# -------------------- CRS safety: ensure metric DEM --------------------
def _utm_epsg_from_lonlat(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 + zone) if lat >= 0 else (32700 + zone)

def ensure_metric_dem(dem_path: Path, proc_dir: Path) -> Path:
    DST_NODATA = -9999.0  # sentinella non plausibile come quota reale

    with rasterio.open(dem_path) as src:
        src_crs = src.crs
        if src_crs is None:
            raise RuntimeError("Input DEM has no CRS. Please define CRS before running volume_unified.")

        if not getattr(src_crs, "is_geographic", False):
            return dem_path

        b = src.bounds
        lon_c = (b.left + b.right) / 2.0
        lat_c = (b.bottom + b.top) / 2.0
        epsg = _utm_epsg_from_lonlat(lon_c, lat_c)
        dst_crs = RioCRS.from_epsg(int(epsg))

        working_path = proc_dir / "dem_working.tif"

        dst_transform, dst_width, dst_height = calculate_default_transform(
            src_crs, dst_crs, src.width, src.height, *src.bounds
        )

        dst_profile = src.profile.copy()
        dst_profile.update(
            crs=dst_crs,
            transform=dst_transform,
            width=dst_width,
            height=dst_height,
            nodata=DST_NODATA
        )

        resampling = Resampling.bilinear if str(src.dtypes[0]).startswith("float") else Resampling.nearest
        dst_arr = np.empty((dst_height, dst_width), dtype=np.float32)

        reproject(
            source=rasterio.band(src, 1),
            destination=dst_arr,
            src_transform=src.transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=resampling,
            src_nodata=src.nodata,
            dst_nodata=DST_NODATA,
        )

        dst_profile.update(dtype=rasterio.float32)
        with rasterio.open(working_path, "w", **dst_profile) as dst:
            dst.write(dst_arr.astype(rasterio.float32), 1)

        print(f"[DEBUG] DEM geographic ({src_crs}). Reprojected to {dst_crs} -> {working_path}")
        return working_path


# -------------------- geometry helpers --------------------
def _pixel_to_map_xy(transform, row, col) -> Tuple[float, float]:
    x, y = transform * (col + 0.5, row + 0.5)
    return float(x), float(y)

def distance_between_points(r1, c1, r2, c2, transform) -> float:
    x1, y1 = _pixel_to_map_xy(transform, r1, c1)
    x2, y2 = _pixel_to_map_xy(transform, r2, c2)
    return float(np.hypot(x2 - x1, y2 - y1))

def calculate_area(contour: np.ndarray, transform) -> float:
    c = np.asarray(contour, dtype=float)
    if c.shape[0] < 3:
        return 0.0

    xs = np.empty(len(c), dtype=float)
    ys = np.empty(len(c), dtype=float)
    for i in range(len(c)):
        xs[i], ys[i] = _pixel_to_map_xy(transform, c[i, 0], c[i, 1])

    area = 0.5 * np.abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1)))
    return float(area)

def contour_perimeter_m(contour: np.ndarray, transform) -> float:
    c = np.asarray(contour, dtype=float)
    if c.shape[0] < 2:
        return 0.0

    xs = np.empty(len(c), dtype=float)
    ys = np.empty(len(c), dtype=float)
    for i in range(len(c)):
        xs[i], ys[i] = _pixel_to_map_xy(transform, c[i, 0], c[i, 1])

    dx = np.diff(np.r_[xs, xs[0]])
    dy = np.diff(np.r_[ys, ys[0]])
    return float(np.sum(np.hypot(dx, dy)))

def contour_to_mask(contour: np.ndarray, shape: Tuple[int, int]) -> np.ndarray:
    c = np.asarray(contour, dtype=float)
    rr, cc = polygon(c[:, 0], c[:, 1], shape)
    m = np.zeros(shape, dtype=bool)
    m[rr, cc] = True
    return m

def pixel_area_m2_from_transform(transform) -> float:
    try:
        return float(abs(transform.a * transform.e - transform.b * transform.d))
    except Exception:
        try:
            return float(abs(transform.a * transform.e))
        except Exception:
            return 0.0

def dem_nodata_stats(dem: np.ndarray, nodata=None) -> Dict[str, Any]:
    arr = dem.astype(float)
    finite = np.isfinite(arr)
    if nodata is not None:
        try:
            finite = finite & (arr != float(nodata))
        except Exception:
            pass
    valid = arr[finite]
    return {
        "valid_count": int(valid.size),
        "total_count": int(arr.size),
        "valid_min": float(np.min(valid)) if valid.size else None,
        "valid_max": float(np.max(valid)) if valid.size else None,
        "valid_p02": float(np.percentile(valid, 2)) if valid.size else None,
        "valid_p98": float(np.percentile(valid, 98)) if valid.size else None,
    }


# -------------------- preview + auto rim export --------------------
RIM_MAX_VERTICES = 2000
DEM_PREVIEW_MAX_SIZE = 1400

def _same_xy(a: Tuple[float, float], b: Tuple[float, float], eps: float = 1e-12) -> bool:
    return (abs(float(a[0]) - float(b[0])) <= eps) and (abs(float(a[1]) - float(b[1])) <= eps)

def _decimate_contour_vertices(contour: np.ndarray, max_vertices: int = RIM_MAX_VERTICES) -> np.ndarray:
    c = np.asarray(contour, dtype=float)
    n = int(c.shape[0])
    if n <= max_vertices or max_vertices < 3:
        return c
    idx = np.linspace(0, n - 1, max_vertices, dtype=int)
    idx = np.unique(idx)
    return c[idx]

def _transform_xy_lists_to_wgs84(xs: List[float], ys: List[float], src_crs) -> Tuple[List[float], List[float]]:
    if src_crs is None:
        raise RuntimeError("Cannot export to EPSG:4326 because DEM CRS is missing.")

    try:
        if RioCRS.from_user_input(src_crs) == RioCRS.from_epsg(4326):
            return [float(x) for x in xs], [float(y) for y in ys]
    except Exception:
        pass

    lon, lat = rio_transform(src_crs, "EPSG:4326", xs, ys)
    return [float(v) for v in lon], [float(v) for v in lat]

def _contour_rc_to_lonlat_ring(contour: np.ndarray, transform, src_crs) -> List[List[float]]:
    c = _decimate_contour_vertices(np.asarray(contour, dtype=float), max_vertices=RIM_MAX_VERTICES)
    if c.ndim != 2 or c.shape[0] < 3 or c.shape[1] < 2:
        raise ValueError("Contour is invalid or too short for GeoJSON export.")

    xs = []
    ys = []
    for row, col in c:
        x, y = _pixel_to_map_xy(transform, row, col)
        xs.append(float(x))
        ys.append(float(y))

    lons, lats = _transform_xy_lists_to_wgs84(xs, ys, src_crs)
    ring = [[float(lon), float(lat)] for lon, lat in zip(lons, lats)]

    if len(ring) < 3:
        raise ValueError("GeoJSON ring has too few vertices.")

    if not _same_xy(tuple(ring[0]), tuple(ring[-1])):
        ring.append([float(ring[0][0]), float(ring[0][1])])

    return ring

def write_auto_rim_geojson(
    caldera_contour: np.ndarray,
    transform,
    src_crs,
    out_path: Path,
) -> None:
    ring = _contour_rc_to_lonlat_ring(caldera_contour, transform, src_crs)
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [ring],
        },
        "properties": {
            "source": "auto",
            "editing_crs": "EPSG:4326",
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(feature, indent=2, ensure_ascii=False), encoding="utf-8")

def _bounds_to_wgs84(bounds_tuple, src_crs) -> List[List[float]]:
    left, bottom, right, top = [float(v) for v in bounds_tuple]

    try:
        if RioCRS.from_user_input(src_crs) == RioCRS.from_epsg(4326):
            west, south, east, north = left, bottom, right, top
        else:
            xs = [left, right, right, left]
            ys = [bottom, bottom, top, top]
            lons, lats = rio_transform(src_crs, "EPSG:4326", xs, ys)
            west = float(min(lons))
            east = float(max(lons))
            south = float(min(lats))
            north = float(max(lats))
    except Exception as e:
        raise RuntimeError(f"Failed to transform DEM bounds to EPSG:4326: {e}") from e

    return [[south, west], [north, east]]

def _downsample_for_preview(arr: np.ndarray, max_size: int = DEM_PREVIEW_MAX_SIZE) -> np.ndarray:
    h, w = arr.shape
    scale = max(h / float(max_size), w / float(max_size), 1.0)
    step = int(math.ceil(scale))
    if step <= 1:
        return arr
    return arr[::step, ::step]

def write_dem_preview(
    dem: np.ndarray,
    bounds_tuple,
    src_crs,
    out_png: Path,
    out_json: Path,
) -> None:
    arr = dem.astype(float)
    valid = np.isfinite(arr)
    if not np.any(valid):
        raise RuntimeError("Cannot write DEM preview: no valid DEM pixels.")

    preview = _downsample_for_preview(arr, max_size=DEM_PREVIEW_MAX_SIZE)
    valid_p = np.isfinite(preview)
    vals = preview[valid_p]

    vmin = float(np.percentile(vals, 2))
    vmax = float(np.percentile(vals, 98))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        vmin = float(np.min(vals))
        vmax = float(np.max(vals))
    if vmax <= vmin:
        vmax = vmin + 1.0

    preview_norm = np.zeros_like(preview, dtype=float)
    preview_norm[valid_p] = np.clip((preview[valid_p] - vmin) / (vmax - vmin), 0.0, 1.0)

    # Hillshade (shaded relief) come sfondo della preview: molto piu' leggibile
    # del grigio piatto di quota per riconoscere i bordi della caldera
    # (feedback Federico 2026-09-10). Puramente cosmetico: NON tocca preview_norm,
    # vmin/vmax, bounds o il JSON — cambia solo i pixel del PNG.
    az_rad = np.deg2rad(315.0)   # azimuth luce da NW (convenzione cartografica)
    alt_rad = np.deg2rad(45.0)   # altitudine luce
    fill_val = float(np.nanmin(preview[valid_p]))
    dem_filled = np.where(valid_p, preview, fill_val).astype(float)
    dy, dx = np.gradient(dem_filled)
    slope = np.pi / 2.0 - np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    hs = (np.sin(alt_rad) * np.sin(slope) +
          np.cos(alt_rad) * np.cos(slope) * np.cos(az_rad - aspect))
    hs = np.clip(hs, 0.0, 1.0)
    hs[~valid_p] = 0.0

    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.imsave(str(out_png), hs, cmap="gray", vmin=0.0, vmax=1.0)

    bounds_wgs84 = _bounds_to_wgs84(bounds_tuple, src_crs)
    meta = {
        "bounds": bounds_wgs84,
        "crs": "EPSG:4326",
    }
    out_json.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


# -------------------- base contour selection (profile presets) --------------------
def _find_contours_at_level(dem: np.ndarray, level: float) -> List[np.ndarray]:
    contours = measure.find_contours(dem, float(level))
    return [np.asarray(c) for c in contours if c is not None and len(c) >= 10]

def _touches_border(contour: np.ndarray, shape: Tuple[int, int], margin_px: int = 2) -> bool:
    h, w = shape
    c = np.round(contour).astype(int)
    r = c[:, 0]
    col = c[:, 1]
    return bool(
        np.any(r <= margin_px) or np.any(col <= margin_px) or
        np.any(r >= (h - 1 - margin_px)) or np.any(col >= (w - 1 - margin_px))
    )


# -------------------- edited rim override helpers (Step 4B) --------------------
def _is_closed_ring(coords: List[List[float]], eps: float = 1e-12) -> bool:
    if not coords or len(coords) < 4:
        return False
    a = coords[0]
    b = coords[-1]
    return (abs(float(a[0]) - float(b[0])) <= eps) and (abs(float(a[1]) - float(b[1])) <= eps)

def _feature_to_lonlat_ring(feature: dict) -> List[List[float]]:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ValueError("Edited rim GeoJSON must be a Feature.")

    geom = feature.get("geometry") or {}
    if geom.get("type") != "Polygon":
        raise ValueError("Edited rim GeoJSON geometry must be Polygon.")

    coords = geom.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 1:
        raise ValueError("Edited rim GeoJSON has no polygon coordinates.")

    ring = coords[0]
    if not isinstance(ring, list) or len(ring) < 4:
        raise ValueError("Edited rim polygon ring is too short.")

    for pt in ring:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            raise ValueError("Edited rim polygon contains invalid coordinate.")
        x = float(pt[0])
        y = float(pt[1])
        if not np.isfinite(x) or not np.isfinite(y):
            raise ValueError("Edited rim polygon contains non-finite coordinate.")

    if not _is_closed_ring(ring):
        raise ValueError("Edited rim polygon ring is not closed.")

    return [[float(pt[0]), float(pt[1])] for pt in ring]

def load_edited_rim_contour_from_geojson(
    rim_path: Path,
    transform,
    dst_crs,
    dem_shape: Tuple[int, int],
) -> np.ndarray:
    raw = json.loads(Path(rim_path).read_text(encoding="utf-8"))
    ring_lonlat = _feature_to_lonlat_ring(raw)

    if len(ring_lonlat) >= 2 and ring_lonlat[0] == ring_lonlat[-1]:
        ring_lonlat = ring_lonlat[:-1]

    lons = [float(pt[0]) for pt in ring_lonlat]
    lats = [float(pt[1]) for pt in ring_lonlat]

    if dst_crs is None:
        raise RuntimeError("DEM CRS is missing; cannot project edited rim.")

    try:
        if RioCRS.from_user_input(dst_crs) == RioCRS.from_epsg(4326):
            xs = lons
            ys = lats
        else:
            xs, ys = rio_transform("EPSG:4326", dst_crs, lons, lats)
    except Exception as e:
        raise RuntimeError(f"Failed to transform edited rim to DEM CRS: {e}") from e

    inv = ~transform
    rows = []
    cols = []
    for x, y in zip(xs, ys):
        col, row = inv * (float(x), float(y))
        rows.append(float(row))
        cols.append(float(col))

    contour = np.column_stack([rows, cols]).astype(float)

    if contour.shape[0] < 3:
        raise ValueError("Edited rim contour has too few vertices after reprojection.")

    h, w = dem_shape
    if np.all((contour[:, 0] < -1) | (contour[:, 0] > h + 1) | (contour[:, 1] < -1) | (contour[:, 1] > w + 1)):
        raise ValueError("Edited rim contour falls completely outside DEM extent.")

    return contour

def select_base_contour(dem: np.ndarray, transform, nodata=None, base_profile: str = "continental") -> Tuple[np.ndarray, Dict[str, Any]]:
    demf = dem.astype(float)

    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass
    if not np.any(valid):
        raise ValueError("DEM has no valid pixels.")

    vmin = float(np.min(demf[valid]))
    vmax = float(np.max(demf[valid]))
    rng = float(max(1e-9, vmax - vmin))

    bp = _normalize_base_profile(base_profile)

    if bp == "island":
        ratios = [0.05, 0.08, 0.12, 0.16, 0.20]
        img_area_px = float(demf.shape[0] * demf.shape[1])

        best = None
        best_score = -1e18
        picked_ratio = None
        picked_level = None
        n_total = 0
        n_kept = 0

        for ratio in ratios:
            level = vmin + rng * float(ratio)
            contours = _find_contours_at_level(demf, level)
            if not contours:
                continue
            n_total += len(contours)

            for c in contours:
                if len(c) < 50:
                    continue
                if _touches_border(c, demf.shape, margin_px=2):
                    continue

                mask = contour_to_mask(c, demf.shape)
                area_px = float(np.sum(mask))
                area_frac = float(area_px / img_area_px)

                if area_frac < 0.01 or area_frac > 0.75:
                    continue

                A = calculate_area(c, transform)
                P = contour_perimeter_m(c, transform)
                circ = (4.0 * math.pi * A / (P * P)) if (A > 0 and P > 0) else 0.0

                size_term = -abs(area_frac - 0.35)
                score = 2.0 * size_term + 0.3 * float(circ) + 0.0005 * float(len(c))

                n_kept += 1
                if score > best_score:
                    best_score = score
                    best = c
                    picked_ratio = float(ratio)
                    picked_level = float(level)

        if best is None:
            ratio = 0.12
            level = vmin + rng * ratio
            contours = _find_contours_at_level(demf, level)
            if not contours:
                raise ValueError("No base contours found (island preset).")
            best = max(contours, key=len)
            picked_ratio = float(ratio)
            picked_level = float(level)

        dbg = {
            "method": "island_sweep_scored_no_border",
            "ratios": [float(r) for r in ratios],
            "picked_ratio": float(picked_ratio) if picked_ratio is not None else None,
            "picked_level_m": float(picked_level) if picked_level is not None else None,
            "n_contours_total": int(n_total),
            "n_candidates_kept": int(n_kept),
            "picked_len": int(len(best)),
        }
        return best, dbg

    ratios = [0.03, 0.05, 0.07, 0.10, 0.13, 0.16, 0.20]
    best = None
    img_area_px = float(demf.shape[0] * demf.shape[1])

    # Pixel di quota massima: usato dal gate-edificio (2026-08-29, Pagan) per
    # distinguere un contorno-bordo che racchiude l'edificio (isola che riempie
    # il tile) da una linea-spazzatura o da una caldera-depressione (che NON
    # racchiude un picco). vedi commento al gate sotto.
    try:
        _pk_r, _pk_c = np.unravel_index(
            int(np.nanargmax(np.where(valid, demf, -np.inf))), demf.shape)
    except Exception:
        _pk_r, _pk_c = None, None
    _h_img, _w_img = demf.shape

    for ratio in ratios:
        level = vmin + rng * float(ratio)
        contours = _find_contours_at_level(demf, level)
        if not contours:
            continue

        for c in contours:
            if len(c) < 50:
                continue

            mask = contour_to_mask(c, demf.shape)
            area_px = float(np.sum(mask))
            area_frac = float(area_px / img_area_px)

            # --- Gate-edificio-che-riempie-il-tile (2026-08-29, Pagan) ---
            # Normalmente un contorno che tocca il bordo del raster viene
            # scartato (protegge Valles/Long Valley da linee-spazzatura di
            # bordo). ECCEZIONE: se il contorno tocca il bordo SOLO perche'
            # l'edificio riempie il tile (Pagan, isola 6x5 km in tile 6.3x5.2),
            # va recuperato. Il triplo gate lo distingue da una depressione:
            # area_frac>=0.30 AND centroide interno AND racchiude il pixel di
            # quota massima. Le caldere-depressione (Valles/Long Valley) NON
            # racchiudono un picco -> restano scartate, invariate.
            _tb = _touches_border(c, demf.shape, margin_px=2)
            _is_edifice_fill = False
            if _tb:
                _cr = float(c[:, 0].mean()); _cc = float(c[:, 1].mean())
                _cent_int = (0.15 * _h_img < _cr < 0.85 * _h_img) and \
                            (0.15 * _w_img < _cc < 0.85 * _w_img)
                _encl_pk = (_pk_r is not None) and bool(mask[_pk_r, _pk_c])
                if (area_frac >= 0.30) and _cent_int and _encl_pk:
                    _is_edifice_fill = True
                else:
                    continue  # bordo non-edificio: scarta come prima
            # cap superiore sull'area (edifici che riempiono possono superare
            # 0.75; ammessi fino a 0.90 solo se passano il gate-edificio)
            _area_hi = 0.90 if _is_edifice_fill else 0.75
            if area_frac < 0.01 or area_frac > _area_hi:
                continue

            A = calculate_area(c, transform)
            P = contour_perimeter_m(c, transform)
            circ = (4.0 * math.pi * A / (P * P)) if (A > 0 and P > 0) else 0.0

            size_term = -abs(area_frac - 0.25)
            circ_term = max(0.0, min(1.0, circ))
            len_term = math.log(float(len(c)))

            score = (2.0 * size_term) + (1.0 * circ_term) + (0.25 * len_term)
            # bonus per il contorno-edificio recuperato: garantisce che batta
            # una struttura interna spuria (laguna/lago) col suo score.
            if _is_edifice_fill:
                score += 5.0

            cand = {
                "ratio": float(ratio),
                "level_m": float(level),
                "len": int(len(c)),
                "area_frac_px": float(area_frac),
                "circularity": float(circ),
                "score": float(score),
                "edifice_fills_tile": bool(_is_edifice_fill),
            }

            if (best is None) or (score > best["cand"]["score"]):
                best = {"contour": c, "cand": cand}

    if not best:
        ratio = 0.05
        level = vmin + rng * ratio
        contours = _find_contours_at_level(demf, level)
        if not contours:
            raise ValueError("No base contours found (continental preset, fallback failed).")
        base = max(contours, key=len)

        # Livello 1 robustezza (2026-08-04, diagnosi Marco): il fallback
        # "contorno piu' lungo vince" non validava affatto la geometria.
        # Su domini senza edificio (Valles, Long Valley) sceglie una linea
        # che serpeggia sul bordo del raster (area quasi nulla, a ridosso
        # del bordo) e la restituisce come base valida. Si applica ora la
        # stessa validazione usata nello sweep sopra, solo per marcare
        # l'esito come degenere in dbg: la geometria resta comunque
        # restituita (nessuna rottura a valle, nessun cambio di firma) ma
        # run_unified() usa questo flag per non scrivere un volume
        # "completed" fondato su una base spazzatura.
        base_mask = contour_to_mask(base, demf.shape)
        base_area_frac = float(np.sum(base_mask)) / img_area_px
        base_touches_border = _touches_border(base, demf.shape, margin_px=2)
        base_degenerate = bool(base_touches_border or base_area_frac < 0.01 or base_area_frac > 0.75)

        dbg = {
            "method": "continental_sweep_failed_fallback_longest",
            "fallback_ratio": float(ratio),
            "fallback_level_m": float(level),
            "picked_len": int(len(base)),
            "degenerate": base_degenerate,
        }
        if base_degenerate:
            dbg["degenerate_reason"] = (
                "touches_raster_border" if base_touches_border else "area_frac_out_of_range"
            )
            dbg["area_frac"] = base_area_frac
        return base, dbg

    dbg = {
        "method": "continental_sweep_scored",
        "picked": best["cand"],
        "ratios_tested": ratios,
    }
    return best["contour"], dbg


# -------------------- caldera rim detection (profile presets) --------------------
def calculate_slope(dem: np.ndarray) -> np.ndarray:
    dx = sobel(dem, axis=1)
    dy = sobel(dem, axis=0)
    return np.hypot(dx, dy)


def _find_caldera_by_elevation(
    dem: np.ndarray,
    transform,
    nodata=None,
    level_ratio: float = 0.80,
    dst_crs=None,
    center_rc: Optional[Tuple[float, float]] = None,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """Rilevamento rim caldera col metodo v1 MorphoVolc: isolinea di quota a
    zmax*level_ratio. Adatto a coni/stratovulcani con caldera SOMMITALE, dove
    l'isolinea alta cinge il pit con un anello compatto. NON adatto a shield
    (caldera depressa larga) ne' a caldere larghe frammentate: quei preset
    usano il metodo slope-morfologico, non questa funzione.

    Differenze rispetto al v1 grezzo (find_caldera_contour, level_ratio=0.8,
    "contorno piu' lungo vince"):
      - gestisce NaN/nodata riempiendo col minimo valido (find_contours non
        tollera NaN), come faceva implicitamente il v1 su matrici piene;
      - tra i contorni chiusi candidati sceglie non il piu' lungo in assoluto
        ma quello piu' vicino al centro (seed GVP se disponibile, altrimenti
        centro immagine) e non degenere, cosi' un lembo di fianco lungo non
        vince sull'anello sommitale;
      - non solleva mai: ritorna (None, debug) in caso di fallimento, coerente
        col resto del detector (run_unified gestisce caldera_contour is None).

    Ritorna (contour_rc, debug).
    """
    dbg: Dict[str, Any] = {"method": "elevation_contour", "level_ratio": float(level_ratio)}
    demf = dem.astype(float)
    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass
    if not np.any(valid):
        dbg["failed"] = True
        dbg["fail_reason"] = "no_valid_pixels"
        return None, dbg

    vmin = float(np.min(demf[valid]))
    vmax = float(np.max(demf[valid]))
    # find_contours non tollera NaN: riempio i non-validi col minimo valido
    # (come il v1, che lavorava su matrici gia' piene). Cosi' l'isolinea alta
    # non e' influenzata dai bordi mare/nodata.
    demfill = np.where(valid, demf, vmin)

    level = float(vmax * float(level_ratio))
    dbg["level_m"] = level
    dbg["zmax"] = vmax
    dbg["zmin"] = vmin

    contours = measure.find_contours(demfill, level)
    contours = [np.asarray(c) for c in contours if c is not None and len(c) >= 10]
    if not contours:
        dbg["failed"] = True
        dbg["fail_reason"] = "no_contours_at_level"
        return None, dbg

    # centro di riferimento: seed GVP se passato, altrimenti centro immagine
    if center_rc is not None:
        cr, cc0 = float(center_rc[0]), float(center_rc[1])
        dbg["center_source"] = "gvp_or_caller"
    else:
        cr, cc0 = demf.shape[0] / 2.0, demf.shape[1] / 2.0
        dbg["center_source"] = "image_center"

    h, w = demf.shape

    def _closed(c):
        return bool(np.hypot(c[0, 0] - c[-1, 0], c[0, 1] - c[-1, 1]) <= 2.0)

    def _touches(c, m=2):
        r = np.round(c).astype(int)
        return bool(np.any(r[:, 0] <= m) or np.any(r[:, 1] <= m) or
                    np.any(r[:, 0] >= h - 1 - m) or np.any(r[:, 1] >= w - 1 - m))

    def _area_px(c):
        x = c[:, 1]; y = c[:, 0]
        return float(0.5 * np.abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))

    # Filtro area minima: un rim di caldera non puo' essere un micro-contorno.
    # find_contours a quota alta produce spesso minuscole isolinee chiuse
    # attorno a singoli picchi/rumore vicino al centro; senza questo filtro lo
    # scoring "vicino al centro" le premierebbe (bug osservato su Izu: sceglieva
    # un contorno da 0.01 km2 invece dell'anello da 3.4 km2). Soglia = frazione
    # dei pixel validi, conservativa.
    n_valid = float(np.sum(valid))
    min_area_px = max(200.0, 0.0015 * n_valid)   # ~0.15% del DEM valido, min 200 px
    dbg["min_area_px"] = float(min_area_px)

    # scoring dei candidati: preferisci contorni chiusi, non a bordo, di area
    # sufficiente, vicini al centro. Distanza centroide-centro come chiave
    # primaria; lunghezza come tie-breaker (piu' lungo = piu' definito).
    scored = []
    rejected_small = 0
    for c in contours:
        if _area_px(c) < min_area_px:
            rejected_small += 1
            continue
        cent_r = float(np.mean(c[:, 0]))
        cent_c = float(np.mean(c[:, 1]))
        d = math.hypot(cent_r - cr, cent_c - cc0)
        penalty = 0.0
        if not _closed(c):
            penalty += 1e6           # i contorni aperti sono pessimi rim
        if _touches(c):
            penalty += 5e5           # a bordo raster = quasi certamente non-caldera
        scored.append((d + penalty, -len(c), c))

    dbg["rejected_small_contours"] = int(rejected_small)
    if not scored:
        dbg["min_area_fallback"] = True
        best = max(contours, key=len)
        dbg["selected_dist_from_center_px"] = -1.0
    else:
        scored.sort(key=lambda t: (t[0], t[1]))
        best = scored[0][2]
        dbg["selected_dist_from_center_px"] = float(scored[0][0] if scored[0][0] < 1e5 else -1.0)

    dbg["n_contours"] = len(contours)
    dbg["selected_len"] = int(len(best))
    dbg["selected_centroid_rc"] = [float(np.mean(best[:, 0])), float(np.mean(best[:, 1]))]
    dbg["selected_closed"] = bool(_closed(best))
    dbg["selected_touches_border"] = bool(_touches(best))
    return best, dbg


def _find_caldera_by_fillsinks(
    dem: np.ndarray,
    transform,
    nodata=None,
    dst_crs=None,
    center_rc: Optional[Tuple[float, float]] = None,
    min_area_km2: float = 10.0,
    min_depth_m: float = 30.0,
    min_vol_km3: float = 0.1,
    min_fill_m: float = 5.0,
    min_component_px: int = 200,
):
    """Detection del rim caldera via fill-sinks (Planchon & Darboux 2001).

    Complementare a _find_caldera_by_elevation_adaptive. Mentre il metodo-quota
    traccia un'isolinea attorno alla sommita' (adatto a crateri sommitali su
    edifici alti, es. Karthala), questo metodo estrae il CONTORNO della
    depressione riempita (adatto a caldere-depressione larghe dove lo slope
    frammenta l'anello e il metodo-quota cingerebbe l'edificio, es. Alcedo).

    Riusa la stessa primitiva morfologica (reconstruction by erosion) gia' in
    caldera_volume_fill_sinks: qui pero' l'ancoraggio e' il SEED GVP (non il
    centroide di un contorno pre-esistente) e l'output e' il contorno della
    depressione da usare come rim.

    Accetta il risultato SOLO se la depressione agganciata dal seed e' una
    caldera-depressione plausibile (area >= min_area_km2, profondita' >
    min_depth_m, volume > min_vol_km3). Questo e' il discriminante verso
    Karthala: li' il fill-sinks aggancia il solo pit Chahale (~3 km2), sotto la
    soglia area, quindi questo metodo si astiene e il chiamante resta sul
    metodo-quota. Ritorna (contour, debug) oppure (None, debug) con motivo.
    """
    from skimage.morphology import reconstruction as _reconstruction
    from scipy.ndimage import label as _label

    dbg: Dict[str, Any] = {"method": "fillsinks_detection"}

    demf = dem.astype(float)
    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass
    if not np.any(valid):
        dbg["fail_reason"] = "no_valid_pixels"
        return None, dbg

    zmax = float(np.max(demf[valid]))
    demfill_in = np.where(valid, demf, zmax)

    seed = demfill_in.copy()
    seed[1:-1, 1:-1] = demfill_in.max()
    try:
        filled = _reconstruction(seed, demfill_in, method="erosion")
    except Exception as e:
        dbg["fail_reason"] = f"reconstruction_error:{e}"
        return None, dbg
    fill_depth = filled - demfill_in
    fill_depth[~valid] = 0.0

    Apx = pixel_area_m2_from_transform(transform)

    filled_regions = (fill_depth > float(min_fill_m)) & valid
    lbl, n = _label(filled_regions)
    if n == 0:
        dbg["fail_reason"] = "no_filled_depression"
        return None, dbg

    # seed GVP: la depressione che LO CONTIENE ha priorita'; in mancanza, la
    # depressione di volume maggiore (fallback osservativo).
    seed_rc = center_rc if center_rc is not None else _gvp_seed_rc(transform, dst_crs, demf.shape)
    chosen = 0
    seed_inside = False
    if seed_rc is not None:
        gr = int(round(seed_rc[0]))
        gc = int(round(seed_rc[1]))
        if 0 <= gr < lbl.shape[0] and 0 <= gc < lbl.shape[1]:
            lab_at_seed = int(lbl[gr, gc])
            if lab_at_seed > 0:
                chosen = lab_at_seed
                seed_inside = True
    if chosen == 0:
        # nessuna depressione sotto il seed: prendi quella di volume maggiore
        best_v = -1.0
        for i in range(1, n + 1):
            m = (lbl == i)
            if int(np.sum(m)) < int(min_component_px):
                continue
            v = float(np.sum(fill_depth[m]))
            if v > best_v:
                best_v = v
                chosen = i

    if chosen == 0:
        dbg["fail_reason"] = "no_component_above_min_size"
        return None, dbg

    mcald = (lbl == chosen)
    sz = int(np.sum(mcald))
    if sz < int(min_component_px):
        dbg["fail_reason"] = "chosen_below_min_component_px"
        dbg["chosen_px"] = sz
        return None, dbg

    area_km2 = sz * Apx / 1e6
    depth_max = float(fill_depth[mcald].max())
    vol_km3 = float(np.sum(fill_depth[mcald]) * Apx) / 1e9

    # centroide della depressione + distanza dal seed (osservativo)
    rr, cc = np.nonzero(mcald)
    cent = (float(rr.mean()), float(cc.mean()))
    dist_seed = None
    if seed_rc is not None:
        dist_seed = float(math.hypot(cent[0] - seed_rc[0], cent[1] - seed_rc[1]))

    dbg.update({
        "seed_inside": bool(seed_inside),
        "area_km2": float(area_km2),
        "depth_max_m": float(depth_max),
        "vol_km3": float(vol_km3),
        "centroid_rc": [cent[0], cent[1]],
        "dist_from_seed_px": dist_seed,
        "n_filled_regions": int(n),
        "gvp_seed_rc": ([float(seed_rc[0]), float(seed_rc[1])] if seed_rc is not None else None),
        "thresholds": {
            "min_area_km2": float(min_area_km2),
            "min_depth_m": float(min_depth_m),
            "min_vol_km3": float(min_vol_km3),
        },
    })

    # --- criterio d'accettazione (discriminante verso Karthala/quota) ---
    accept = (
        area_km2 >= float(min_area_km2)
        and depth_max > float(min_depth_m)
        and vol_km3 > float(min_vol_km3)
    )
    # se il seed non e' dentro la depressione, richiedi che sia comunque vicina
    # (evita di agganciare una depressione periferica non calderica)
    if accept and not seed_inside and dist_seed is not None:
        # tolleranza: mezza dimensione lineare equivalente della depressione
        equiv_r = (area_km2 * 1e6) ** 0.5 / (Apx ** 0.5)  # raggio equivalente in px
        if dist_seed > 1.5 * equiv_r:
            accept = False
            dbg["reject_reason"] = "seed_far_from_depression"

    if not accept:
        dbg.setdefault("reject_reason", "below_caldera_depression_thresholds")
        return None, dbg

    # estrai il contorno della depressione (rim). Riempi i buchi interni per un
    # anello pulito (post-processing coerente col ramo slope/quota).
    try:
        mfill = binary_fill_holes(mcald)
    except Exception:
        mfill = mcald
    conts = measure.find_contours(mfill.astype(np.uint8), 0.5)
    if not conts:
        dbg["fail_reason"] = "contour_extraction_failed"
        return None, dbg
    contour = max(conts, key=len)

    dbg["accepted"] = True
    dbg["contour_len"] = int(len(contour))
    dbg["selected_area_km2"] = float(area_km2)
    return contour, dbg


def _find_caldera_by_fillsinks_continental(
    dem: np.ndarray,
    transform,
    nodata=None,
    dst_crs=None,
    center_rc: Optional[Tuple[float, float]] = None,
    min_depth_m: float = 50.0,
    min_solidity: float = 0.85,
    min_area_km2: float = 0.3,
    max_area_km2: float = 60.0,
    min_component_px: int = 200,
):
    """Fill-sinks come FALLBACK del preset continental (2026-09-01, Nyiragongo).

    Complementare al ramo shield (_find_caldera_by_fillsinks). Scatta SOLO quando
    il metodo-quota continental e' degenere/collassato, e aggancia il rim SOLO se
    la depressione e' una conca calderica VERA e ben definita. Criterio STRETTO
    (diverso dal ramo shield): non usa la soglia area 10 km² (che escluderebbe
    crateri piccoli reali come Nyiragongo 1.1 km²), ma un discriminante su
    solidity + seed_inside + profondita', calibrato sui dati reali:
      - Nyiragongo (ACCETTA): seed_inside=T, solidity 0.96, depth 280 m
      - Tengger/Pagan (RIFIUTA, ash-plain): seed_inside=F, solidity 0.55/0.17
    La doppia condizione (seed_inside AND solidity alta) garantisce che gli
    ash-plain (fondo piatto frastagliato, seed fuori dalla conca parziale) e le
    caldere-depressione senza fondo chiuso (Valles/Long Valley/Fogo) siano esclusi.
    """
    from skimage.morphology import reconstruction as _reconstruction, convex_hull_image
    from scipy.ndimage import label as _label

    dbg: Dict[str, Any] = {"method": "fillsinks_continental_fallback"}

    demf = dem.astype(float)
    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass
    # tratta anche gli 0 di mare/nodata-mascherato come non-validi (isole)
    valid = valid & (demf > 1.0)
    if not np.any(valid):
        dbg["fail_reason"] = "no_valid_pixels"
        return None, dbg

    zmax = float(np.max(demf[valid]))
    demfill_in = np.where(valid, demf, zmax)
    seed = demfill_in.copy()
    seed[1:-1, 1:-1] = demfill_in.max()
    try:
        filled = _reconstruction(seed, demfill_in, method="erosion")
    except Exception as e:
        dbg["fail_reason"] = f"reconstruction_error:{e}"
        return None, dbg
    fill_depth = filled - demfill_in
    fill_depth[~valid] = 0.0

    Apx = pixel_area_m2_from_transform(transform)
    filled_regions = (fill_depth > 2.0) & valid
    lbl, n = _label(filled_regions)
    if n == 0:
        dbg["fail_reason"] = "no_filled_depression"
        return None, dbg

    # la depressione DEVE contenere il seed GVP (criterio stretto).
    seed_rc = center_rc if center_rc is not None else _gvp_seed_rc(transform, dst_crs, demf.shape)
    if seed_rc is None:
        dbg["fail_reason"] = "no_gvp_seed"
        return None, dbg
    gr = int(round(seed_rc[0])); gc = int(round(seed_rc[1]))
    if not (0 <= gr < lbl.shape[0] and 0 <= gc < lbl.shape[1]):
        dbg["fail_reason"] = "seed_out_of_bounds"
        return None, dbg
    chosen = int(lbl[gr, gc])
    if chosen == 0:
        dbg["fail_reason"] = "seed_not_inside_any_depression"
        dbg["seed_inside"] = False
        return None, dbg

    mcald = (lbl == chosen)
    sz = int(np.sum(mcald))
    if sz < int(min_component_px):
        dbg["fail_reason"] = "chosen_below_min_component_px"
        return None, dbg

    area_km2 = sz * Apx / 1e6
    depth_max = float(fill_depth[mcald].max())
    vol_km3 = float(np.sum(fill_depth[mcald]) * Apx) / 1e9
    try:
        solidity = float(sz / int(np.sum(convex_hull_image(mcald))))
    except Exception:
        solidity = 0.0
    rr, cc = np.nonzero(mcald)
    cent = (float(rr.mean()), float(cc.mean()))
    dist_seed = float(math.hypot(cent[0] - seed_rc[0], cent[1] - seed_rc[1]))

    dbg.update({
        "seed_inside": True,
        "area_km2": float(area_km2),
        "depth_max_m": float(depth_max),
        "vol_km3": float(vol_km3),
        "solidity": float(solidity),
        "dist_from_seed_px": dist_seed,
        "centroid_rc": [cent[0], cent[1]],
        "thresholds": {
            "min_depth_m": float(min_depth_m), "min_solidity": float(min_solidity),
            "min_area_km2": float(min_area_km2), "max_area_km2": float(max_area_km2),
        },
    })

    # --- criterio d'accettazione STRETTO ---
    accept = (
        depth_max >= float(min_depth_m)
        and solidity >= float(min_solidity)
        and float(min_area_km2) <= area_km2 <= float(max_area_km2)
    )
    if not accept:
        reasons = []
        if depth_max < float(min_depth_m): reasons.append("depth_too_shallow")
        if solidity < float(min_solidity): reasons.append("solidity_too_low")
        if not (float(min_area_km2) <= area_km2 <= float(max_area_km2)): reasons.append("area_out_of_range")
        dbg["reject_reason"] = ",".join(reasons)
        return None, dbg

    try:
        mfill = binary_fill_holes(mcald)
    except Exception:
        mfill = mcald
    conts = measure.find_contours(mfill.astype(np.uint8), 0.5)
    if not conts:
        dbg["fail_reason"] = "contour_extraction_failed"
        return None, dbg
    contour = max(conts, key=len)
    dbg["accepted"] = True
    dbg["contour_len"] = int(len(contour))
    dbg["selected_area_km2"] = float(area_km2)
    return contour, dbg


def _find_caldera_by_elevation_adaptive(
    dem: np.ndarray,
    transform,
    nodata=None,
    dst_crs=None,
    center_rc: Optional[Tuple[float, float]] = None,
    ratio_lo: float = 0.80,
    ratio_hi: float = 0.96,
    ratio_step: float = 0.01,
    min_solidity: float = 0.75,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """Fallback a ratio adattivo per il metodo di quota. Scandisce level_ratio
    da ratio_lo a ratio_hi e sceglie il contorno CHIUSO, COMPATTO (solidity >=
    min_solidity), NON a bordo, piu' vicino al centro. Serve per caldere
    sommitali su edifici alti col rim basso (es. Karthala: caldera 4x3 km con
    pareti di soli ~100 m su shield di 2361 m, verificato Strong & Jacquot 1970),
    dove: (a) il metodo slope non trova candidati (scarpata debole), e (b) il
    metodo di quota a ratio fisso 0.80 e' troppo basso e cinge il fianco. Su
    Karthala il ratio ottimale e' ~0.95 (diametro ~3.9 km, coerente con i 4 km
    reali). Usato SOLO come fallback quando lo slope shield fallisce: i casi
    shield che trovano candidati (Fernandina/Darwin/Okmok) non lo attivano mai.

    Ritorna (contour_rc, debug). Sceglie tra tutti i ratio quello che da' il
    contorno con solidity piu' alta a parita' di centratura (preferisce anelli
    netti). Non solleva mai.
    """
    dbg: Dict[str, Any] = {
        "method": "elevation_contour_adaptive",
        "ratio_range": [float(ratio_lo), float(ratio_hi)],
        "ratio_step": float(ratio_step),
        "min_solidity": float(min_solidity),
    }
    demf = dem.astype(float)
    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass
    if not np.any(valid):
        dbg["failed"] = True
        dbg["fail_reason"] = "no_valid_pixels"
        return None, dbg

    vmax = float(np.max(demf[valid]))
    n_valid = float(np.sum(valid))
    if center_rc is not None:
        cr, cc0 = float(center_rc[0]), float(center_rc[1])
        dbg["center_source"] = "gvp_or_caller"
    else:
        cr, cc0 = demf.shape[0] / 2.0, demf.shape[1] / 2.0
        dbg["center_source"] = "image_center"

    tried = []
    best_overall = None  # (score_tuple, contour, ratio, solidity, area_km2, dist)
    px_area_m2 = abs(transform.a * transform.e)
    h, w = demf.shape
    # find_contours non tollera NaN: riempi i non-validi col minimo valido.
    _vmin_fb = float(np.min(demf[valid]))
    demfill = np.where(valid, demf, _vmin_fb)

    def _closed_fb(c):
        return bool(np.hypot(c[0, 0] - c[-1, 0], c[0, 1] - c[-1, 1]) <= 3.0)

    def _touches_fb(c, m=2):
        r_ = np.round(c).astype(int)
        return bool(np.any(r_[:, 0] <= m) or np.any(r_[:, 1] <= m) or
                    np.any(r_[:, 0] >= h - 1 - m) or np.any(r_[:, 1] >= w - 1 - m))

    r = float(ratio_lo)
    while r <= ratio_hi + 1e-9:
        level = float(vmax * r)
        # Selezione DEDICATA (replica lo sweep manuale verificato): tra tutti i
        # contorni chiusi non-a-bordo a questo livello, prendi quello piu' vicino
        # al centro con area in un range caldera-plausibile. Non riuso
        # _find_caldera_by_elevation perche' il suo scoring (min_area + distanza
        # + penalita') sceglie il pit interno ai ratio alti invece della caldera.
        try:
            raw = measure.find_contours(demfill, level)
        except Exception:
            raw = []
        best_at_level = None  # (dist, contour, area_km2, solidity)
        for c in raw:
            c = np.asarray(c)
            if len(c) < 20 or not _closed_fb(c) or _touches_fb(c):
                continue
            m = contour_to_mask(c, demf.shape)
            a_px = float(np.sum(m))
            a_km2 = a_px * px_area_m2 / 1e6
            if not (1.0 <= a_km2 <= 30.0):   # scarta pit minuscoli e fianchi enormi
                continue
            cent = _centroid_of_mask(m)
            if cent is None:
                continue
            d = math.hypot(cent[0] - cr, cent[1] - cc0)
            if d > 90:                        # deve essere vicino al centro caldera
                continue
            s = _mask_solidity(m)
            if s is None or s < min_solidity:
                continue
            # a questo livello, tieni il piu' vicino al centro
            if best_at_level is None or d < best_at_level[0]:
                best_at_level = (d, c, a_km2, s)
        if best_at_level is not None:
            d, c, a_km2, s = best_at_level
            tried.append({"ratio": round(r, 3), "area_km2": round(a_km2, 2),
                          "solidity": round(float(s), 3), "dist": round(d, 1)})
            # Tra i ratio: preferisci l'anello caldera-plausibile piu' compatto
            # e centrato. Score = distanza dal centro (piccola meglio), poi
            # -solidity. Cosi' scelgo il livello a cui l'anello e' piu' definito.
            score = (round(d, 1), -round(float(s), 3))
            if best_overall is None or score < best_overall[0]:
                best_overall = (score, c, round(r, 3), float(s), a_km2, d)
        r += ratio_step

    dbg["ratios_tried"] = tried
    if best_overall is None:
        dbg["failed"] = True
        dbg["fail_reason"] = "no_compact_contour_in_ratio_range"
        return None, dbg

    _, best_c, best_ratio, best_sol, best_area_km2, best_d = best_overall
    dbg["selected_ratio"] = best_ratio
    dbg["selected_solidity"] = float(best_sol)
    dbg["selected_level_m"] = float(vmax * best_ratio)
    dbg["selected_area_km2"] = float(best_area_km2)
    dbg["selected_dist_from_center_px"] = float(best_d)
    return best_c, dbg


def _centroid_of_mask(mask: np.ndarray) -> Optional[Tuple[float, float]]:
    rr, cc = np.nonzero(mask)
    if rr.size == 0:
        return None
    return (float(np.mean(rr)), float(np.mean(cc)))

def _peak_in_roi(dem: np.ndarray, roi: np.ndarray) -> Optional[Tuple[float, float]]:
    try:
        vals = np.where(roi, dem.astype(float), -np.inf)
        if not np.isfinite(vals).any():
            return _centroid_of_mask(roi)
        idx = int(np.nanargmax(vals))
        r, c = np.unravel_index(idx, dem.shape)
        return (float(r), float(c))
    except Exception:
        return _centroid_of_mask(roi)

def _min_in_roi(dem: np.ndarray, roi: np.ndarray) -> Optional[Tuple[float, float]]:
    try:
        vals = np.where(roi, dem.astype(float), np.inf)
        if not np.isfinite(vals).any():
            return _centroid_of_mask(roi)
        idx = int(np.nanargmin(vals))
        r, c = np.unravel_index(idx, dem.shape)
        return (float(r), float(c))
    except Exception:
        return _centroid_of_mask(roi)

def _gvp_seed_rc(transform, dst_crs, shape):
    """Approccio 3 (GVP-anchored center). Project the GVP volcano coordinates
    (env GVP_LAT/GVP_LON, WGS84) into the working DEM and return (row, col),
    or None if the env vars are absent/invalid, the CRS is missing, or the
    resulting pixel falls outside the raster. Never raises."""
    lat_s = (os.environ.get("GVP_LAT") or "").strip()
    lon_s = (os.environ.get("GVP_LON") or "").strip()
    if not lat_s or not lon_s or dst_crs is None:
        return None
    try:
        lat = float(lat_s)
        lon = float(lon_s)
    except Exception:
        return None
    try:
        if RioCRS.from_user_input(dst_crs) == RioCRS.from_epsg(4326):
            x, y = lon, lat
        else:
            xs, ys = rio_transform("EPSG:4326", dst_crs, [lon], [lat])
            x, y = xs[0], ys[0]
        col, row = (~transform) * (float(x), float(y))
        r, c = float(row), float(col)
    except Exception:
        return None
    if not (0.0 <= r <= float(shape[0] - 1) and 0.0 <= c <= float(shape[1] - 1)):
        return None
    return (r, c)

def _mask_solidity(mask):
    """Solidity = filled_area / convex_hull_area, computed directly on the mask
    pixels (no shapely dependency). ~1.0 for a compact/convex blob (a clean
    small caldera), low (<0.3) for a ragged ring that wanders and leaves open
    pockets (a large caldera whose scarp fragments into arcs). Returns None if
    the mask is empty or the hull cannot be built. Never raises."""
    try:
        from skimage.morphology import convex_hull_image
        m = np.asarray(mask, dtype=bool)
        area = int(m.sum())
        if area <= 0:
            return None
        hull = convex_hull_image(m)
        hull_area = int(hull.sum())
        if hull_area <= 0:
            return None
        return float(area) / float(hull_area)
    except Exception:
        return None


def _regularize_ragged_mask(mask, min_solidity, max_close_iters, step=2):
    """Adaptive morphological regularization. If the mask's contour is ragged
    (solidity < min_solidity), apply progressively stronger binary_closing
    (bridging fragmented scarp arcs and filling open pockets) until solidity
    clears the threshold or max_close_iters is reached. A compact mask
    (Fernandina-like) already passes on the first check and is returned
    untouched: strong closing is a no-op on an already-solid blob, so this
    never degrades good cases. Returns (mask_out, info_dict)."""
    info = {
        "applied": False,
        "solidity_before": None,
        "solidity_after": None,
        "close_iters_used": 0,
        "min_solidity": float(min_solidity),
        "max_close_iters": int(max_close_iters),
    }
    s0 = _mask_solidity(mask)
    info["solidity_before"] = s0
    if s0 is None or s0 >= min_solidity or max_close_iters <= 0:
        info["solidity_after"] = s0
        return mask, info
    best = mask
    used = 0
    it = step
    while it <= max_close_iters:
        try:
            cand = binary_fill_holes(binary_closing(mask, iterations=int(it)))
        except Exception:
            break
        s = _mask_solidity(cand)
        best = cand
        used = it
        if s is not None and s >= min_solidity:
            break
        it += step
    info["applied"] = True
    info["close_iters_used"] = int(used)
    info["solidity_after"] = _mask_solidity(best)
    return best, info


def find_caldera_contour_morphological(
    dem: np.ndarray,
    base_contour: np.ndarray,
    transform,
    nodata=None,
    preset: str = "continental",
    dst_crs=None,
    _override_cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    debug: Dict[str, Any] = {}
    bp = _normalize_base_profile(preset)

    if bp == "island":
        cfg = {
            "roi_dilate_px": 4,
            "smooth_sigma": 1.0,
            "slope_q": 90.0,
            "min_component_px": 400,
            "min_area_frac": 0.02,
            "closing_iterations": 2,
            "extra_dilate_px": 0,
            "max_area_frac": 0.35,
            "inner_buffer_px": 10,
            "center_bias_weight": 18.0,
            "overlap_mode": "reward_inner",
            "post_close_iterations": 2,
            "post_fill_holes": True,
            "center_mode": "centroid",
        }
    elif bp == "shield":
        # Shield preset (Week 1-2 initial values, to be validated against
        # ground truth in Week 7-8). See gvp_profile_mapping.md for rationale.
        # Design goal: summit calderas on shield edifices — compact, steep,
        # small relative to the edifice, sometimes without a true central
        # depression.
        cfg = {
            "roi_dilate_px": 3,               # tight ROI: rim is at summit, not on flanks
            "smooth_sigma": 1.2,
            "slope_q": 93.0,                  # very selective: avoid flank slope breaks
            "min_component_px": 200,          # allow small components
            "min_area_frac": 0.005,
            "closing_iterations": 3,
            "extra_dilate_px": 1,
            "max_area_frac": 0.15,            # shield calderas small vs edifice
            "inner_buffer_px": 6,
            "center_bias_weight": 15.0,
            "overlap_mode": "reward_inner",
            "post_close_iterations": 2,
            "post_fill_holes": True,
            "center_mode": "adaptive",        # switch to centroid if depression is unreliable
            "dep_to_centroid_max_px": 12.0,   # tight: reject depressions far from centroid
            # Adaptive rim regularization (2026-08-21): large shield calderas
            # (Okmok, ~10 km) fragment into scarp arcs that the base closing
            # cannot bridge, yielding a ragged ring (low solidity) that
            # under-measures caldera area. Bridge them up to a solidity target.
            # No-op on compact calderas (Fernandina passes immediately).
            "rim_min_solidity": 0.6,          # target contour solidity (0=off)
            "rim_max_close_iters": 16,        # cap on progressive closing
        }
    else:
        cfg = {
            "roi_dilate_px": 8,
            "smooth_sigma": 1.2,
            "slope_q": 86.0,
            "min_component_px": 600,
            "min_area_frac": 0.01,
            "closing_iterations": 3,
            "extra_dilate_px": 1,
            "max_area_frac": 0.50,
            "inner_buffer_px": 14,
            "center_bias_weight": 22.0,
            "overlap_mode": "reward_inner",
            "post_close_iterations": 2,
            "post_fill_holes": True,
            "center_mode": "gvp_or_auto",  # GVP-anchored center when vnum present, else prior auto behaviour
            # Rim detection method (2026-08-26). Sui coni/stratovulcani con
            # caldera SOMMITALE (Izu-Oshima, Miyakejima, Fentale) il metodo
            # slope-morfologico "allunga" la componente verso strutture
            # adiacenti (solidity ~0.38). L'isolinea di quota alta (metodo v1
            # MorphoVolc) invece cinge il pit sommitale con un anello compatto
            # (solidity ~0.90-0.95, verificato sui DEM reali). Per gli shield
            # (Fernandina/Darwin: caldera depressa larga -> l'isolinea cinge
            # l'edificio non la depressione, area 2-4x troppo grande) e per
            # Okmok (caldera larga frammentata -> isolinea collassa) il metodo
            # di quota NON va bene: quei preset restano su slope. Discriminante
            # = tipo GVP (Stratovolcano->continental->elevation_contour;
            # Shield->slope), coerente con la classificazione GVP del progetto.
            "rim_method": "elevation_contour",
            "elevation_level_ratio": 0.80,   # quota = zmax * ratio (metodo v1)
        }

    if _override_cfg is not None:
        try:
            cfg = dict(_override_cfg)
        except Exception:
            pass

    # Dispatch metodo rim (2026-08-26). Se il preset richiede il metodo di
    # quota (continental/stratovolcano: caldera sommitale), dirotta sul
    # detector a isolinea di quota e salta tutta la pipeline slope/componenti.
    # Shield e island NON impostano rim_method -> restano sul metodo
    # slope-morfologico, byte-identici a prima (nessuna regressione su
    # Fernandina/Darwin/Okmok). Il branch relaxed/barnie ricorsivo non imposta
    # rim_method nell'override, quindi non ricade qui.
    rim_method = str(cfg.get("rim_method", "slope_morphological")).strip().lower()
    if rim_method == "elevation_contour":
        # centro di riferimento: seed GVP se disponibile (coerente con
        # center_mode gvp_or_auto del preset continental), altrimenti la
        # funzione ripiega sul centro immagine.
        center_gvp = _gvp_seed_rc(transform, dst_crs, dem.shape)
        level_ratio = float(cfg.get("elevation_level_ratio", 0.80))
        caldera_contour, elev_dbg = _find_caldera_by_elevation(
            dem=dem,
            transform=transform,
            nodata=nodata,
            level_ratio=level_ratio,
            dst_crs=dst_crs,
            center_rc=center_gvp,
        )
        debug.update({
            "method": "elevation_contour",
            "preset": bp,
            "rim_method": "elevation_contour",
            "elevation_detail": elev_dbg,
            "gvp_seed_available": bool(center_gvp is not None),
            "gvp_seed_rc": ([float(center_gvp[0]), float(center_gvp[1])] if center_gvp is not None else None),
            "config": cfg,
        })
        if caldera_contour is None:
            debug["failed"] = True
            debug["fail_reason"] = elev_dbg.get("fail_reason", "elevation_contour_failed")
            return None, debug

        # --- Fallback fill-sinks continental (2026-09-01, Nyiragongo) ---
        # Se il rim-quota e' DEGENERE (tocca il bordo del raster oppure e' un
        # anello che non racchiude una vera depressione), prova il fill-sinks
        # con criterio stretto. Accettato SOLO se la depressione e' una conca
        # calderica vera (seed dentro, solidity alta, profonda) -> chiude
        # Nyiragongo (cratere 1.1 km²/280 m) senza toccare gli ash-plain
        # (Tengger/Pagan: seed fuori + solidity bassa -> rifiutati) ne' le
        # caldere-depressione senza fondo chiuso (Valles/Long Valley/Fogo).
        _quota_degenere = bool(elev_dbg.get("selected_touches_border", False))
        if _quota_degenere:
            fs_c, fs_d = _find_caldera_by_fillsinks_continental(
                dem=dem, transform=transform, nodata=nodata, dst_crs=dst_crs,
                center_rc=center_gvp,
            )
            if fs_c is not None and fs_d.get("accepted"):
                debug.update({
                    "method": "fillsinks_continental_fallback",
                    "rim_method": "fillsinks_continental_fallback",
                    "quota_degenerate": {
                        "selected_touches_border": True,
                        "elevation_detail": elev_dbg,
                    },
                    "fillsinks_detail": fs_d,
                })
                return fs_c, debug
            else:
                debug["fillsinks_continental_rejected"] = fs_d

        return caldera_contour, debug

    demf = dem.astype(float)
    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass

    roi = contour_to_mask(base_contour, demf.shape)
    if cfg["roi_dilate_px"] > 0:
        roi = binary_dilation(roi, iterations=int(cfg["roi_dilate_px"]))
    roi = roi & valid

    roi_px = int(np.sum(roi))
    if roi_px < max(200, int(cfg["min_component_px"] * 0.5)):
        roi = valid.copy()
        roi_px = int(np.sum(roi))
        debug["roi_fallback"] = {
            "reason": "base_roi_too_small",
            "roi_px": int(roi_px),
        }
        if roi_px == 0:
            raise ValueError(f"ROI too small (roi_px={roi_px}).")

    inner_buffer_px = int(max(0, cfg["inner_buffer_px"]))
    try:
        roi_inner = binary_erosion(roi, iterations=inner_buffer_px) if inner_buffer_px > 0 else roi
        if int(np.sum(roi_inner)) < 150:
            roi_inner = roi
    except Exception:
        roi_inner = roi

    slope = calculate_slope(demf)
    slopef = slope.astype(float)
    if cfg["smooth_sigma"] and float(cfg["smooth_sigma"]) > 0:
        slopef = gaussian_filter(slopef, sigma=float(cfg["smooth_sigma"]))

    roi_slopes = slopef[roi]
    roi_slopes = roi_slopes[np.isfinite(roi_slopes)]
    if roi_slopes.size == 0:
        # Livello 1 (2026-08-04): nessun segnale di pendenza nella ROI,
        # nulla da salvare come parziale. Fallimento onesto invece di
        # crash fatale: run_unified() gestisce caldera_contour is None.
        debug["failed"] = True
        debug["fail_reason"] = "no_finite_slope_in_roi"
        return None, debug

    thr = float(np.percentile(roi_slopes, float(cfg["slope_q"])))
    candidates = (slopef >= thr) & roi

    if cfg["closing_iterations"] > 0:
        candidates = binary_closing(candidates, iterations=int(cfg["closing_iterations"]))
    candidates = binary_fill_holes(candidates)
    if cfg["extra_dilate_px"] > 0:
        candidates = binary_dilation(candidates, iterations=int(cfg["extra_dilate_px"]))
    candidates = candidates & roi

    cand_px = int(np.sum(candidates))
    # Livello 1 (2026-08-04): una maschera candidata vuota (o senza
    # componenti) non e' piu' un raise immediato -- confluisce nel ramo
    # "eligible vuoto" sotto, che gia' tenta il relaxed_pass prima di
    # arrendersi. Prima di questo fix Karthala falliva qui senza mai
    # provare il retry gia' esistente per gli altri casi.
    if cand_px > 0:
        lbl, ncomp = label(candidates)
    else:
        lbl, ncomp = np.zeros(candidates.shape, dtype=int), 0

    sizes = [int(np.sum(lbl == cid)) for cid in range(1, ncomp + 1)]
    eligible = []

    max_area_px = int(float(cfg["max_area_frac"]) * float(roi_px))
    min_area_px = int(float(cfg.get("min_area_frac", 0.0)) * float(roi_px))
    hard_min_px = int(max(int(cfg["min_component_px"]), min_area_px))

    for cid, sz in zip(range(1, ncomp + 1), sizes):
        if sz < hard_min_px:
            continue
        if max_area_px > 0 and sz > max_area_px:
            continue
        eligible.append((cid, sz))
    if not eligible:
        if not bool(cfg.get("_relaxed_pass", False)):
            cfg_rel = dict(cfg)
            cfg_rel["_relaxed_pass"] = True
            cfg_rel["slope_q"] = float(cfg_rel.get("slope_q", 90.0)) - 4.0
            cfg_rel["min_component_px"] = max(80, int(cfg_rel.get("min_component_px", 400) * 0.5))
            cfg_rel["min_area_frac"] = float(cfg_rel.get("min_area_frac", 0.0)) * 0.25
            cfg_rel["inner_buffer_px"] = max(3, int(cfg_rel.get("inner_buffer_px", 10) * 0.6))
            cfg_rel["closing_iterations"] = int(cfg_rel.get("closing_iterations", 2)) + 1
            cfg_rel["post_close_iterations"] = int(cfg_rel.get("post_close_iterations", 2)) + 2
            debug["fallback_relaxed_cfg"] = {
                "slope_q": cfg_rel["slope_q"],
                "min_component_px": cfg_rel["min_component_px"],
                "min_area_frac": cfg_rel["min_area_frac"],
                "inner_buffer_px": cfg_rel["inner_buffer_px"],
                "closing_iterations": cfg_rel["closing_iterations"],
                "post_close_iterations": cfg_rel["post_close_iterations"],
            }
            return find_caldera_contour_morphological(
                dem,
                base_contour,
                transform,
                nodata=nodata,
                preset=preset,
                dst_crs=dst_crs,
                _override_cfg=cfg_rel,
            )

        # Livello 1 (2026-08-04, diagnosi Marco): il relaxed_pass e' gia'
        # stato tentato (siamo in una chiamata ricorsiva con
        # _relaxed_pass=True) e non ha prodotto nessuna componente entro i
        # vincoli di area. Invece di un raise fatale, si restituisce la
        # componente candidata piu' grande vista finora (anche se non
        # rispetta min/max_area_frac), marcata partial=True con
        # confidenza bassa e la frazione di ROI occupata, cosi'
        # un'analisi a valle puo' escluderla da statistiche quantitative.
        # Se non esiste nemmeno una componente candidata (ncomp == 0), si
        # dichiara fallimento onesto invece di inventare geometria dal
        # nulla.
        if ncomp > 0 and sizes:
            best_cid = int(np.argmax(sizes)) + 1
            best_sz = int(max(sizes))
            eligible = [(best_cid, best_sz)]
            debug["partial"] = True
            debug["partial_reason"] = "no_component_met_area_constraints_after_relaxed_pass"
            debug["partial_confidence"] = "low"
            debug["partial_component_px"] = best_sz
            debug["partial_component_frac_of_roi"] = float(best_sz) / float(max(1, roi_px))
        else:
            # Fallback (2026-08-26): lo slope non ha trovato candidati. Per gli
            # shield con caldera sommitale su edificio alto e rim basso
            # (Karthala: pareti ~100 m su 2361 m -> scarpata troppo debole per
            # lo slope) tento il metodo di quota a ratio ADATTIVO. Isolato:
            # scatta SOLO qui, cioe' solo quando lo slope fallisce del tutto;
            # gli shield che trovano candidati (Fernandina/Darwin/Okmok) non
            # arrivano mai a questo punto -> nessuna regressione.
            center_gvp_fb = _gvp_seed_rc(transform, dst_crs, dem.shape)
            fb_contour, fb_dbg = _find_caldera_by_elevation_adaptive(
                dem=dem, transform=transform, nodata=nodata, dst_crs=dst_crs,
                center_rc=center_gvp_fb, ratio_lo=0.80, ratio_hi=0.96,
                ratio_step=0.01, min_solidity=0.75,
            )
            if fb_contour is not None:
                debug["method"] = "elevation_contour_adaptive_fallback"
                debug["rim_method"] = "elevation_contour_adaptive_fallback"
                debug["slope_failed_reason"] = "no_rim_candidates_after_relaxed_pass"
                debug["elevation_adaptive_detail"] = fb_dbg
                debug["gvp_seed_available"] = bool(center_gvp_fb is not None)
                debug["gvp_seed_rc"] = ([float(center_gvp_fb[0]), float(center_gvp_fb[1])]
                                         if center_gvp_fb is not None else None)
                return fb_contour, debug
            # anche il fallback ha fallito: fallimento onesto
            debug["failed"] = True
            debug["fail_reason"] = "no_rim_candidates_after_relaxed_pass"
            debug["fallback_elevation_adaptive"] = fb_dbg
            return None, debug

    if eligible and not bool(cfg.get("_barnie_pass", False)):
        roi_px_f = float(max(1, roi_px))
        max_elig_px = max(int(sz) for _, sz in eligible)
        max_elig_frac = float(max_elig_px) / roi_px_f
        if max_elig_frac < float(cfg.get("barnie_trigger_max_elig_frac", 0.015)):
            cfg_rel2 = dict(cfg)
            cfg_rel2["_barnie_pass"] = True
            cfg_rel2["slope_q"] = float(cfg_rel2.get("slope_q", 90.0)) - 10.0
            cfg_rel2["closing_iterations"] = int(cfg_rel2.get("closing_iterations", 2)) + 4
            cfg_rel2["inner_buffer_px"] = int(cfg_rel2.get("inner_buffer_px", 10)) + 8
            cfg_rel2["min_component_px"] = max(80, int(cfg_rel2.get("min_component_px", 400) * 0.35))
            cfg_rel2["min_area_frac"] = float(cfg_rel2.get("min_area_frac", 0.0)) * 0.25
            cfg_rel2["post_close_iterations"] = int(cfg_rel2.get("post_close_iterations", 2)) + 2
            # Preserve GVP anchoring on continental: if the top-level preset
            # requested gvp_or_auto, do NOT downgrade the center to centroid in
            # the barnie fallback — the GVP seed must keep precedence.
            cfg_rel2["center_mode"] = (
                "gvp_or_auto" if str(cfg.get("center_mode", "")).strip().lower() == "gvp_or_auto"
                else "centroid"
            )
            cfg_rel2["overlap_mode"] = "penalize_inner"
            debug["fallback_barnie_cfg"] = {
                "trigger_max_elig_frac": float(cfg.get("barnie_trigger_max_elig_frac", 0.015)),
                "max_elig_frac": float(max_elig_frac),
                "slope_q": cfg_rel2["slope_q"],
                "closing_iterations": cfg_rel2["closing_iterations"],
                "inner_buffer_px": cfg_rel2["inner_buffer_px"],
                "min_component_px": cfg_rel2["min_component_px"],
                "min_area_frac": cfg_rel2["min_area_frac"],
                "post_close_iterations": cfg_rel2["post_close_iterations"],
                "center_mode": cfg_rel2["center_mode"],
                "overlap_mode": cfg_rel2["overlap_mode"],
            }
            return find_caldera_contour_morphological(
                dem,
                base_contour,
                transform,
                nodata=nodata,
                preset=preset,
                dst_crs=dst_crs,
                _override_cfg=cfg_rel2,
            )

    center_mode = str(cfg.get("center_mode", "depression")).strip().lower()

    def _near_border(rc, shape, margin=3):
        if rc is None:
            return True
        r, c = rc
        return (r < margin) or (c < margin) or (r > (shape[0] - 1 - margin)) or (c > (shape[1] - 1 - margin))

    center_dep = _min_in_roi(demf, roi_inner) or _min_in_roi(demf, roi)
    center_ctr = _centroid_of_mask(roi_inner) or _centroid_of_mask(roi)
    center_gvp = _gvp_seed_rc(transform, dst_crs, dem.shape)

    roi_px_f = float(max(1, roi_px))
    elig_sizes = [sz for _, sz in eligible] if eligible else []
    max_elig_frac = (max(elig_sizes) / roi_px_f) if elig_sizes else 0.0

    dep_far = False
    if center_dep is not None and center_ctr is not None:
        dr = float(center_dep[0] - center_ctr[0])
        dc = float(center_dep[1] - center_ctr[1])
        dep_far = (dr * dr + dc * dc) ** 0.5 > float(cfg.get("dep_to_centroid_max_px", 25.0))

    auto_centroid = (
        _near_border(center_dep, dem.shape, margin=int(cfg.get("center_border_margin_px", 3)))
        or (max_elig_frac > 0.0 and max_elig_frac < float(cfg.get("min_eligible_frac_for_depression", 0.06)))
        or dep_far
    )

    # Approccio 3: when the continental preset requests GVP anchoring
    # ("gvp_or_auto") and a valid in-raster GVP seed exists, it wins over
    # every morphology-derived center — including the centroid forced by the
    # relaxed/barnie recursive overrides (those overrides do NOT set
    # "gvp_or_auto", so they naturally fall through to the auto branch here,
    # but if the top-level preset was gvp_or_auto and the seed exists we anchor
    # unconditionally). Island ("centroid") and shield ("adaptive") never carry
    # the "gvp_or_auto" mode, so they are untouched: no regression on the 3
    # good cases.
    if center_mode == "gvp_or_auto" and center_gvp is not None:
        center = center_gvp
        center_mode_used = "gvp_seed"
    elif center_mode in ("gvp_or_auto", "auto", "adaptive"):
        if auto_centroid:
            center = center_ctr
            center_mode_used = "centroid_auto"
        else:
            center = center_dep or center_ctr
            center_mode_used = "depression_auto"
    elif center_mode in ("depression", "min", "pit"):
        center = center_dep or center_ctr
        center_mode_used = "depression"
    elif center_mode in ("centroid", "center"):
        center = center_ctr
        center_mode_used = "centroid"
    else:
        center = _peak_in_roi(demf, roi) or center_ctr
        center_mode_used = "peak_legacy"

    if center is None:
        center = (float(dem.shape[0]) * 0.5, float(dem.shape[1]) * 0.5)
        center_mode_used = "fallback_image_center"

    center_r, center_c = float(center[0]), float(center[1])
    center_used = {
        "row": float(center_r),
        "col": float(center_c),
        "method": f"center_mode:{center_mode_used}",
        "auto_centroid": bool(auto_centroid),
        "max_eligible_frac": float(max_elig_frac),
        "gvp_seed_available": bool(center_gvp is not None),
        "gvp_seed_rc": ([float(center_gvp[0]), float(center_gvp[1])] if center_gvp is not None else None),
    }

    best = None
    # Dump diagnostico (2026-08-22): raccoglie, per OGNI componente eligibile,
    # le metriche che entrano (o dovrebbero entrare) nello scoring, piu' la
    # distanza del suo centroide dal seed GVP. Puramente osservativo: non
    # altera in alcun modo la selezione. Serve a tarare sui numeri reali il
    # prossimo intervento (filtro prossimita' GVP + penalita' di forma) invece
    # che a intuito. Attivo su tutti i preset (non ha effetti collaterali);
    # utile soprattutto su continental dove convivono piu' eligibili.
    eligible_dump = []
    for cid, sz in eligible:
        m = (lbl == cid)
        rr, cc = np.nonzero(m)
        if rr.size == 0:
            continue

        rad = np.hypot(rr - center_r, cc - center_c)
        mean_radius_px = float(np.mean(rad))
        p90_radius_px = float(np.percentile(rad, 90))

        cent = _centroid_of_mask(m) or (center_r, center_c)
        dr = float(cent[0] - center_r)
        dc = float(cent[1] - center_c)
        d2 = float(dr * dr + dc * dc)

        try:
            inner_overlap = float(np.sum(m & roi_inner)) / float(np.sum(m) + 1e-9)
        except Exception:
            inner_overlap = 0.0

        overlap_mode = str(cfg.get("overlap_mode", "reward_inner")).strip().lower()
        w = float(cfg["center_bias_weight"])
        overlap_term = (-w * inner_overlap) if overlap_mode in ("reward_inner", "inner", "reward") else (+w * inner_overlap)
        key = (
            mean_radius_px + overlap_term,
            p90_radius_px,
            d2,
            -sz
        )

        # --- solo osservazione, non entra nello score ---
        solidity = _mask_solidity(m)
        elong_ratio = (p90_radius_px / mean_radius_px) if mean_radius_px > 1e-6 else None
        dist_gvp = None
        if center_gvp is not None:
            dgr = float(cent[0] - center_gvp[0])
            dgc = float(cent[1] - center_gvp[1])
            dist_gvp = float((dgr * dgr + dgc * dgc) ** 0.5)
        eligible_dump.append({
            "cid": int(cid),
            "size_px": int(sz),
            "centroid_rc": [float(cent[0]), float(cent[1])],
            "dist_from_center_px": float(d2 ** 0.5),
            "dist_from_gvp_px": dist_gvp,
            "mean_radius_px": mean_radius_px,
            "p90_radius_px": p90_radius_px,
            "elong_ratio": elong_ratio,
            "solidity": solidity,
            "inner_overlap": float(inner_overlap),
            "overlap_term": float(overlap_term),
            "score_primary": float(mean_radius_px + overlap_term),
        })

        if best is None or key < best[0]:
            best = (key, cid, sz, cent, inner_overlap, mean_radius_px, p90_radius_px)

    if best is None:
        selected_cid, selected_sz = max(eligible, key=lambda t: t[1])
    else:
        selected_cid = int(best[1])
        selected_sz = int(best[2])
        center_used["selected_component_centroid"] = {"row": float(best[3][0]), "col": float(best[3][1])}
        center_used["selected_component_inner_overlap"] = float(best[4])
        center_used["selected_component_mean_radius_px"] = float(best[5])
        center_used["selected_component_p90_radius_px"] = float(best[6])

    # Dump diagnostico ordinato per score_primary crescente (il primo e' quello
    # che lo scoring attuale seleziona). Permette di leggere a colpo d'occhio
    # perche' una componente vince e di quanto stacca le rivali.
    center_used["eligible_components_debug"] = sorted(
        eligible_dump, key=lambda d: d["score_primary"]
    )

    best_mask = (lbl == selected_cid)
    post_close = int(max(0, cfg.get("post_close_iterations", 0)))
    post_fill = bool(cfg.get("post_fill_holes", True))
    if post_close > 0:
        try:
            best_mask = binary_closing(best_mask, iterations=post_close)
        except Exception:
            pass
    if post_fill:
        try:
            best_mask = binary_fill_holes(best_mask)
        except Exception:
            pass
    try:
        lbl2, n2 = label(best_mask)
        if n2 > 1:
            sizes2 = [int(np.sum(lbl2 == cid)) for cid in range(1, n2 + 1)]
            keep = int(np.argmax(sizes2) + 1)
            best_mask = (lbl2 == keep)
    except Exception:
        pass

    # Adaptive rim regularization (2026-08-21): a ragged selected component
    # (large fragmented caldera scarp, e.g. Okmok: solidity ~0.16, two spurs
    # enclosing an empty pocket) is bridged into a solid disk via progressive
    # closing, driven by a solidity target. Compact calderas (Fernandina:
    # solidity ~0.99) pass the check immediately and are left untouched, so
    # there is no regression on good cases. Both thresholds are configurable
    # per preset; defaults are conservative no-ops when absent.
    rim_min_solidity = float(cfg.get("rim_min_solidity", 0.0))
    rim_max_close_iters = int(cfg.get("rim_max_close_iters", 0))
    if rim_min_solidity > 0.0 and rim_max_close_iters > 0:
        best_mask, _reg_info = _regularize_ragged_mask(
            best_mask, rim_min_solidity, rim_max_close_iters
        )
        try:
            lbl3, n3 = label(best_mask)
            if n3 > 1:
                sizes3 = [int(np.sum(lbl3 == cid)) for cid in range(1, n3 + 1)]
                best_mask = (lbl3 == int(np.argmax(sizes3) + 1))
        except Exception:
            pass
        center_used["rim_regularization"] = _reg_info

    contours = measure.find_contours(best_mask.astype(np.uint8), 0.5)
    if not contours:
        # Livello 1 (2026-08-04): maschera valida ma non estraibile come
        # contorno (caso raro, es. componente ridotta a pixel isolati).
        # Nessun ulteriore fallback disponibile a questo stadio: esito
        # onesto invece di crash.
        debug["failed"] = True
        debug["fail_reason"] = "rim_contour_extraction_failed"
        return None, debug
    caldera_contour = max(contours, key=len)

    # Gate di qualita' (2026-08-26): se il rim slope e' degenere — troppo
    # piccolo E poco solido — su un preset shield, il metodo slope ha
    # verosimilmente agganciato un pit interno invece della caldera (Karthala:
    # slope da' ~2 km2 sol 0.63 = solo il pit Chahale, non la caldera 4x3 km).
    # In quel caso tento il metodo di quota a ratio adattivo e tengo il migliore
    # dei due. Soglie STRETTE per non toccare i validati: Fernandina (24 km2,
    # sol 0.97) e Okmok (83 km2) non le attivano mai. Solo shield.
    if bp == "shield" and not bool(cfg.get("_barnie_pass", False)):
        try:
            _m = contour_to_mask(caldera_contour, dem.shape)
            _sol = _mask_solidity(_m)
            _area_px = float(np.sum(_m))
            _roi_frac = _area_px / float(max(1, roi_px))
        except Exception:
            _sol, _area_px, _roi_frac = None, 0.0, 0.0
        # degenere = solidity bassa E area piccola rispetto alla ROI.
        # Fernandina/Okmok hanno sol >0.9: la condizione AND le esclude.
        degenerate_slope_rim = (
            _sol is not None and _sol < 0.75 and _roi_frac < 0.05
        )
        if degenerate_slope_rim:
            center_gvp_q = _gvp_seed_rc(transform, dst_crs, dem.shape)

            # --- Ramo A: fill-sinks (caldera-depressione larga, es. Alcedo) ---
            # Provato PRIMA del metodo-quota. Accettato solo se aggancia una
            # depressione calderica plausibile (area >= 10 km2 &c). Su Karthala
            # il fill-sinks trova solo il pit Chahale (~3 km2 < soglia) e si
            # astiene -> si cade sul metodo-quota (Ramo B), Karthala invariato.
            fs_c, fs_d = _find_caldera_by_fillsinks(
                dem=dem, transform=transform, nodata=nodata, dst_crs=dst_crs,
                center_rc=center_gvp_q,
                min_area_km2=10.0, min_depth_m=30.0, min_vol_km3=0.1,
            )
            if fs_c is not None and fs_d.get("accepted"):
                debug.update({
                    "method": "fillsinks_fallback",
                    "rim_method": "fillsinks_fallback",
                    "slope_rim_degenerate": {
                        "solidity": float(_sol), "roi_frac": float(_roi_frac),
                        "area_px": float(_area_px),
                    },
                    "fillsinks_detail": fs_d,
                    "preset": bp,
                    "gvp_seed_available": bool(center_gvp_q is not None),
                    "gvp_seed_rc": ([float(center_gvp_q[0]), float(center_gvp_q[1])]
                                     if center_gvp_q is not None else None),
                })
                return fs_c, debug

            # --- Ramo B: metodo-quota adattivo (cratere sommitale, es. Karthala) ---
            fb_c, fb_d = _find_caldera_by_elevation_adaptive(
                dem=dem, transform=transform, nodata=nodata, dst_crs=dst_crs,
                center_rc=center_gvp_q, ratio_lo=0.80, ratio_hi=0.96,
                ratio_step=0.01, min_solidity=0.75,
            )
            if fb_c is not None:
                fb_sol = fb_d.get("selected_solidity") or 0.0
                # accetta il fallback solo se e' NETTAMENTE migliore del rim slope
                if fb_sol > (_sol + 0.15):
                    debug.update({
                        "method": "elevation_contour_adaptive_fallback",
                        "rim_method": "elevation_contour_adaptive_fallback",
                        "slope_rim_degenerate": {
                            "solidity": float(_sol), "roi_frac": float(_roi_frac),
                            "area_px": float(_area_px),
                        },
                        "elevation_adaptive_detail": fb_d,
                        "fillsinks_rejected": fs_d,
                        "preset": bp,
                        "gvp_seed_available": bool(center_gvp_q is not None),
                        "gvp_seed_rc": ([float(center_gvp_q[0]), float(center_gvp_q[1])]
                                         if center_gvp_q is not None else None),
                    })
                    return fb_c, debug

    debug.update({
        "method": "morphological_slope_roi",
        "preset": bp,
        "config": cfg,
        "roi_px": int(roi_px),
        "candidate_px": int(cand_px),
        "n_components": int(ncomp),
        "all_component_sizes_px": sizes,
        "eligible_component_sizes_px": [int(sz) for (_, sz) in eligible],
        "selected_component_id": int(selected_cid),
        "selected_component_px": int(selected_sz),
        "post_process": {
            "post_close_iterations": int(max(0, cfg.get("post_close_iterations", 0))),
            "post_fill_holes": bool(cfg.get("post_fill_holes", True))
        },
        "center": center_used,
        "threshold": float(thr),
        "contour_len": int(len(caldera_contour)),
    })
    return caldera_contour, debug


# -------------------- caldera volume: depth-integrated --------------------
def outside_ring_mask(mask: np.ndarray, offset_px: int = 1, width_px: int = 3) -> np.ndarray:
    offset_px = int(max(0, offset_px))
    width_px = int(max(1, width_px))
    inner = binary_dilation(mask, iterations=offset_px) if offset_px > 0 else mask
    outer = binary_dilation(mask, iterations=offset_px + width_px)
    return outer & (~inner)

def caldera_volume_fill_sinks(
    dem: np.ndarray,
    caldera_contour: np.ndarray,
    transform,
    nodata=None,
) -> Dict[str, Any]:
    """Volume della caldera col metodo 'fill sinks' / depression filling
    (Planchon & Darboux 2001; equivalente a MATLAB imfill(...,'holes')).
    Riempie la depressione racchiusa fino al livello di sfioro del bordo e
    integra la differenza (DEM_riempito - DEM). A differenza del metodo
    depth-integrated (rim p90 - floor p5), NON dipende da un rim tracciato
    correttamente: il volume viene dalla FORMA della depressione. Serve per i
    casi in cui il rim di quota, tracciato sulla sommità, rende z_rim ~ z_floor
    e quindi il depth-integrated collassa a V=0 pur essendoci una caldera reale
    (es. Puyehue: caldera 2.5 km / 280 m, depth-integrated dava 0, fill-sinks
    da' ~0.3 km3 fino allo sfioro).

    Metodo: reconstruction morfologica per erosione con marker = massimo al
    centro e DEM al bordo (l'acqua entra dai bordi), che riempie le depressioni
    interne fino al punto di sfioro piu' basso. La caldera e' la depressione
    riempita piu' vicina al centro della maschera del contorno.

    Riporta il volume con TRE definizioni di riferimento del rim (sfioro, p90,
    massimo del bordo) per trasparenza scientifica; V_caldera_m3 usa lo sfioro
    (definizione idrologica conservativa e piu' riproducibile).
    """
    from skimage.morphology import reconstruction as _reconstruction
    from scipy.ndimage import label as _label, binary_dilation as _bdil

    demf = dem.astype(float)
    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass
    if not np.any(valid):
        return {"V_caldera_m3": 0.0, "method": "fill_sinks", "failed": True,
                "fail_reason": "no_valid_pixels"}

    zmax = float(np.max(demf[valid]))
    zmin = float(np.min(demf[valid]))
    demfill_in = np.where(valid, demf, zmax)   # NaN->max: niente falsi sink al bordo

    # fill sinks: marker = max ovunque tranne bordo (=DEM), reconstruction by erosion
    seed = demfill_in.copy()
    seed[1:-1, 1:-1] = demfill_in.max()
    try:
        filled = _reconstruction(seed, demfill_in, method="erosion")
    except Exception as e:
        return {"V_caldera_m3": 0.0, "method": "fill_sinks", "failed": True,
                "fail_reason": f"reconstruction_error:{e}"}
    fill_depth = filled - demfill_in
    fill_depth[~valid] = 0.0

    Apx = pixel_area_m2_from_transform(transform)

    # centro di riferimento = centroide della maschera del contorno caldera
    cmask_contour = contour_to_mask(caldera_contour, demf.shape)
    cc = _centroid_of_mask(cmask_contour)
    if cc is None:
        cc = (demf.shape[0] / 2.0, demf.shape[1] / 2.0)

    # componente di riempimento piu' vicina al centro (>= soglia minima 5 m)
    filled_regions = (fill_depth > 5.0) & valid
    lbl, n = _label(filled_regions)
    if n == 0:
        return {"V_caldera_m3": 0.0, "method": "fill_sinks",
                "note": "no_filled_depression", "max_fill_depth_m": float(fill_depth.max())}
    best = None
    for i in range(1, n + 1):
        m = (lbl == i)
        sz = int(np.sum(m))
        if sz < 50:
            continue
        rr, ccx = np.nonzero(m)
        d = math.hypot(float(rr.mean()) - cc[0], float(ccx.mean()) - cc[1])
        if best is None or d < best[0]:
            best = (d, m, sz)
    if best is None:
        return {"V_caldera_m3": 0.0, "method": "fill_sinks",
                "note": "no_component_above_min_size"}
    _, mcald, sz = best

    # volume fino allo sfioro (fill-sinks nativo)
    V_spill = float(np.sum(fill_depth[mcald]) * Apx)

    # quote per definizioni alternative di profondita'/volume
    inside = demf[mcald & valid]
    z_floor = float(np.min(inside)) if inside.size else zmin
    ring = _bdil(mcald, iterations=2) & (~mcald)
    rv = demf[ring & valid]; rv = rv[np.isfinite(rv)]
    if rv.size:
        z_rim_spill = float(np.min(rv))
        z_rim_p90 = float(np.percentile(rv, 90))
        z_rim_max = float(np.max(rv))
    else:
        z_rim_spill = z_rim_p90 = z_rim_max = z_floor

    def _vol_to(zr):
        dep = np.maximum(0.0, zr - demf)
        dep[~mcald] = 0.0
        dep[~valid] = 0.0
        return float(np.sum(dep) * Apx)

    V_p90 = _vol_to(z_rim_p90)
    V_max = _vol_to(z_rim_max)

    area_km2 = sz * Apx / 1e6
    return {
        "V_caldera_m3": V_spill,                 # definizione principale: fino a sfioro
        "method": "fill_sinks",
        "caldera_model": "fill_sinks_planchon_darboux",
        "spill_point_m": z_rim_spill,
        "z_floor_min_m": z_floor,
        "depth_to_spill_m": float(z_rim_spill - z_floor),
        "depth_to_rim_p90_m": float(z_rim_p90 - z_floor),
        "depth_to_rim_max_m": float(z_rim_max - z_floor),
        "V_to_spill_m3": V_spill,
        "V_to_rim_p90_m3": V_p90,
        "V_to_rim_max_m3": V_max,
        "filled_area_km2": float(area_km2),
        "max_fill_depth_m": float(fill_depth[mcald].max()),
    }


def caldera_volume_depth_integrated(
    dem: np.ndarray,
    caldera_contour: np.ndarray,
    transform,
    nodata=None,
    rim_percentile: float = 90.0,
    floor_percentile: float = 5.0,
    rim_ring_offset_px: int = 1,
    rim_ring_width_px: int = 3
) -> Dict[str, Any]:
    demf = dem.astype(float)
    caldera_mask = contour_to_mask(caldera_contour, demf.shape)

    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass

    # Guardia anti-crash (2026-08-30, Tengger): se il contorno del rim e'
    # degenere (tocca il bordo del raster, si auto-interseca, o cade su nodata)
    # la maschera puo' risultare vuota o priva di pixel validi. In quel caso NON
    # si solleva eccezione (che abbatterebbe l'intero processo di volume), ma si
    # ritorna un risultato degenere con volume nullo e un flag diagnostico. Il
    # chiamante (run_unified) lo tratta come volume non calcolabile, coerente
    # con gli altri fallback, e scrive comunque un metrics interpretabile.
    _mask_valid_px = int(np.sum(caldera_mask & valid))
    if int(np.sum(caldera_mask)) == 0 or _mask_valid_px == 0:
        return {
            "z_rim_ref_m": None, "z_floor_ref_m": None,
            "depth_ref_m": 0.0, "depth_ref_m_clamped": 0.0,
            "V_caldera_m3": 0.0, "mask_area_m2": 0.0,
            "pixel_area_m2": None, "fallback_used": True,
            "degenerate_rim": True,
            "degenerate_reason": (
                "caldera_mask_empty" if int(np.sum(caldera_mask)) == 0
                else "no_valid_dem_inside_mask"
            ),
        }

    ring = outside_ring_mask(caldera_mask, offset_px=rim_ring_offset_px, width_px=rim_ring_width_px)
    rim_vals = demf[ring & valid]
    rim_method = "percentile_on_outside_ring"

    if rim_vals.size == 0:
        c = np.round(np.asarray(caldera_contour)).astype(int)
        ok = (c[:, 0] >= 0) & (c[:, 0] < demf.shape[0]) & (c[:, 1] >= 0) & (c[:, 1] < demf.shape[1])
        c = c[ok]
        if c.size == 0:
            raise ValueError("Caldera contour empty after bounds filtering.")
        rim_vals = demf[c[:, 0], c[:, 1]]
        rim_vals = rim_vals[np.isfinite(rim_vals)]
        if nodata is not None:
            try:
                rim_vals = rim_vals[rim_vals != float(nodata)]
            except Exception:
                pass
        if rim_vals.size == 0:
            raise ValueError("No valid rim samples (ring empty and contour invalid).")
        rim_method = "percentile_on_contour_fallback"

    z_rim = float(np.percentile(rim_vals, float(rim_percentile)))

    inside_vals = demf[caldera_mask & valid]
    if inside_vals.size == 0:
        raise ValueError("No valid DEM values inside caldera mask.")
    z_floor = float(np.percentile(inside_vals, float(floor_percentile)))

    depth_ref_raw = float(z_rim - z_floor)
    depth_ref_clamped = float(max(0.0, depth_ref_raw))

    depth = np.maximum(0.0, z_rim - demf)
    depth[~caldera_mask] = 0.0
    depth[~valid] = 0.0

    Apx = pixel_area_m2_from_transform(transform)
    V = float(np.sum(depth) * Apx)

    return {
        "V_caldera_m3": V,
        "z_rim_ref_m": z_rim,
        "z_floor_ref_m": z_floor,
        "depth_ref_m": depth_ref_raw,
        "depth_ref_m_clamped": depth_ref_clamped,
        "rim_method": rim_method,
        "rim_ring_offset_px": int(rim_ring_offset_px),
        "rim_ring_width_px": int(rim_ring_width_px),
        "rim_sample_count": int(rim_vals.size),
        "rim_percentile": float(rim_percentile),
        "floor_percentile": float(floor_percentile),
        "pixel_area_m2": float(Apx),
        "caldera_mask_area_m2": float(np.sum(caldera_mask & valid) * Apx),
    }


# -------------------- height model (robust) --------------------
def height_p99_minus_p05_inside_base(dem: np.ndarray, base_contour: np.ndarray, nodata=None) -> Dict[str, Any]:
    demf = dem.astype(float)
    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass

    scope = "base_mask"
    try:
        base_mask = contour_to_mask(base_contour, demf.shape)
        vals = demf[base_mask & valid]
    except Exception:
        vals = demf[valid]
        scope = "full_valid_fallback"

    if vals.size < 50:
        vals = demf[valid]
        scope = "full_valid_fallback"

    if vals.size == 0:
        h = 0.0
    else:
        h = float(np.percentile(vals, 99) - np.percentile(vals, 5))

    return {
        "h_max_m": float(h),
        "method": "p99_minus_p05",
        "scope": scope,
        "p_high": 99.0,
        "p_low": 5.0,
        "n": int(vals.size),
    }


# -------------------- opposite points helpers (for visualization only) --------------------
def find_opposite_points(contour: np.ndarray) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    c = np.round(np.asarray(contour)).astype(int)
    if c.shape[0] < 2:
        return (0, 0), (0, 0)
    idx1 = 0
    idx2 = int(len(c) // 2)
    p1 = (int(c[idx1, 0]), int(c[idx1, 1]))
    p2 = (int(c[idx2, 0]), int(c[idx2, 1]))
    return p1, p2

def find_opposite_slope_points(slope: np.ndarray, contour: np.ndarray) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    c = np.round(np.asarray(contour)).astype(int)
    c = c[
        (c[:, 0] >= 0) & (c[:, 0] < slope.shape[0]) &
        (c[:, 1] >= 0) & (c[:, 1] < slope.shape[1])
    ]
    if c.size == 0:
        return (0, 0), (0, 0)

    vals = [slope[r, col] for r, col in c]
    idx1 = int(np.argmax(vals))
    idx2 = (idx1 + len(c) // 2) % len(c)
    p1 = (int(c[idx1, 0]), int(c[idx1, 1]))
    p2 = (int(c[idx2, 0]), int(c[idx2, 1]))
    return p1, p2

def _hillshade_for_doublet(dem: np.ndarray, azimuth: float = 315.0, altitude: float = 45.0) -> np.ndarray:
    """Hillshade normalizzato 0..1 per lo sfondo del doublet. Puramente
    cosmetico: non entra in nessun calcolo morfometrico/volumetrico."""
    arr = np.asarray(dem, dtype=float)
    valid = np.isfinite(arr)
    if not np.any(valid):
        return np.zeros_like(arr, dtype=float)
    fill_val = float(np.nanmin(arr[valid]))
    dem_filled = np.where(valid, arr, fill_val)
    az_rad = np.deg2rad(azimuth)
    alt_rad = np.deg2rad(altitude)
    dy, dx = np.gradient(dem_filled)
    slope = np.pi / 2.0 - np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    hs = (np.sin(alt_rad) * np.sin(slope) +
          np.cos(alt_rad) * np.cos(slope) * np.cos(az_rad - aspect))
    hs = np.clip(hs, 0.0, 1.0)
    hs[~valid] = 0.0
    return hs
# -------------------- outputs: PNG doublet --------------------
def save_final_doublet_png(
    dem: np.ndarray,
    base_contour: np.ndarray,
    base_p1: Tuple[int, int],
    base_p2: Tuple[int, int],
    caldera_contour: np.ndarray,
    cal_p1: Tuple[int, int],
    cal_p2: Tuple[int, int],
    out_path: Path,
):
    out_path.parent.mkdir(parents=True, exist_ok=True)

    FIG_W, FIG_H = 14.5, 5.5
    fig = plt.figure(figsize=(FIG_W, FIG_H))
    gs = gridspec.GridSpec(1, 3, figure=fig, width_ratios=[1.0, 0.08, 1.0], wspace=0.15)

    ax1 = fig.add_subplot(gs[0, 0])
    _hs1 = _hillshade_for_doublet(dem)
    ax1.imshow(_hs1, cmap="gray", origin="upper", interpolation="nearest", resample=False, vmin=0.0, vmax=1.0)
    im1 = ax1.imshow(dem, cmap="terrain", origin="upper", interpolation="nearest", resample=False, alpha=0.5)
    ax1.plot(base_contour[:, 1], base_contour[:, 0], "w-", linewidth=1)
    ax1.plot(base_p1[1], base_p1[0], "ro", markersize=8)
    ax1.plot(base_p2[1], base_p2[0], "yo", markersize=8)
    ax1.set_title("Base contour + opposite points", pad=8, fontsize=12)
    ax1.set_aspect("equal", adjustable="box")
    div1 = make_axes_locatable(ax1)
    cax1 = div1.append_axes("right", size="4.6%", pad=0.10)
    cbar1 = fig.colorbar(im1, cax=cax1)
    cbar1.set_label("Elevation (m)", rotation=90)

    ax_sp = fig.add_subplot(gs[0, 1])
    ax_sp.axis("off")

    ax2 = fig.add_subplot(gs[0, 2])
    _hs2 = _hillshade_for_doublet(dem)
    ax2.imshow(_hs2, cmap="gray", origin="upper", interpolation="nearest", resample=False, vmin=0.0, vmax=1.0)
    im2 = ax2.imshow(dem, cmap="terrain", origin="upper", interpolation="nearest", resample=False, alpha=0.5)
    caldera_plot = np.vstack([caldera_contour, caldera_contour[0]])
    ax2.plot(caldera_plot[:, 1], caldera_plot[:, 0], "b-", linewidth=1)
    ax2.plot(cal_p1[1], cal_p1[0], "ro", markersize=8)
    ax2.plot(cal_p2[1], cal_p2[0], "yo", markersize=8)
    ax2.set_title("Caldera rim + opposite slope points", pad=8, fontsize=12)
    ax2.set_aspect("equal", adjustable="box")
    div2 = make_axes_locatable(ax2)
    cax2 = div2.append_axes("right", size="4.6%", pad=0.10)
    cbar2 = fig.colorbar(im2, cax=cax2)
    cbar2.set_label("Elevation (m)", rotation=90)

    fig.savefig(str(out_path), dpi=170)
    plt.close(fig)


# -------------------- metrics CSV schema (human) --------------------
HUMAN_FIELDS = [
    ("Meta", "Run timestamp (UTC)", "meta.timestamp_utc", ""),
    ("Meta", "Run ID (process_id)", "meta.process_id", ""),
    ("Meta", "Original input filename", "meta.original_file_name", ""),
    ("Meta", "Original input filename (stem)", "meta.original_file_stem", ""),
    ("Meta", "Input DEM path", "meta.input_dem_path", ""),
    ("Meta", "Working DEM path", "meta.working_dem_path", ""),
    ("Meta", "Working DEM CRS (EPSG/WKT)", "meta.crs", ""),
    ("Meta", "Pixel resolution", "meta.res", "m"),
    ("Meta", "NoData value", "meta.nodata", ""),

    ("Scenario", "Base profile", "meta.baseProfile", ""),
    ("Scenario", "Model", "meta.model", ""),

    ("Base morphometrics", "Base area", "morphometrics.A_base_m2", "m²"),
    ("Base morphometrics", "Base perimeter", "morphometrics.P_base_m", "m"),
    ("Base morphometrics", "Base span (opposite points)", "morphometrics.D_base_m", "m"),

    ("Caldera morphometrics", "Caldera area", "morphometrics.A_caldera_m2", "m²"),
    ("Caldera morphometrics", "Caldera perimeter", "morphometrics.P_caldera_m", "m"),
    ("Caldera morphometrics", "Caldera span (opposite points)", "morphometrics.D_caldera_m", "m"),

    ("Height model", "Height method", "height_model.method", ""),
    ("Height model", "Height (P99 - P05)", "height_model.h_max_m", "m"),

    ("Volumes", "Total edifice volume", "volumes.V_total_m3", "m³"),
    ("Volumes", "Caldera volume", "volumes.V_caldera_m3", "m³"),
    ("Volumes", "Effective edifice volume", "volumes.V_effective_m3", "m³"),
]

def _get_by_path(d: dict, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur

def metrics_to_human_rows(metrics: dict):
    rows = []
    for section, label_txt, path, unit in HUMAN_FIELDS:
        v = _get_by_path(metrics, path)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        rows.append((section, label_txt, v, unit))
    return rows

def _as_serializable(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.ndarray,)):
        return v.tolist()
    if isinstance(v, (tuple, list)):
        return [_as_serializable(x) for x in v]
    if isinstance(v, dict):
        return {k: _as_serializable(val) for k, val in v.items()}
    return v


# -------------------- main compute --------------------
def run_unified(
    dem: np.ndarray,
    transform,
    meta: Dict[str, Any],
    base_profile: str,
    caldera_contour_override: Optional[np.ndarray] = None,
    rim_source: str = "auto",
    rim_preset: Optional[str] = None,
) -> Dict[str, Any]:
    nodata = meta.get("nodata", None)

    # rim_preset defaults to base_profile for full backward compatibility.
    # When the GVP mapping produces different presets for base and rim
    # (e.g. Shield -> base=island, rim=shield), the caller passes rim_preset
    # explicitly.
    effective_rim_preset = rim_preset if rim_preset else base_profile

    base_contour, base_dbg = select_base_contour(dem, transform, nodata=nodata, base_profile=base_profile)
    slope = calculate_slope(dem)

    if caldera_contour_override is not None:
        caldera_contour = np.asarray(caldera_contour_override, dtype=float)
        cal_dbg = {
            "method": "geojson_override",
            "source": rim_source,
            "contour_len": int(len(caldera_contour)),
        }
    else:
        caldera_contour, cal_dbg = find_caldera_contour_morphological(
            dem=dem,
            base_contour=base_contour,
            transform=transform,
            nodata=nodata,
            preset=effective_rim_preset,
            dst_crs=meta.get("crs"),
        )

    # Livello 1 (2026-08-04): find_caldera_contour_morphological ora puo'
    # restituire (None, debug) invece di sollevare un'eccezione fatale.
    # select_base_contour non restituisce mai None, quindi base_contour
    # resta il placeholder geometrico per PNG/GeoJSON a valle; tutte le
    # grandezze numeriche legate alla caldera vengono pero' azzerate
    # esplicitamente sotto, non derivate dal placeholder.
    rim_detection_failed = caldera_contour is None
    caldera_contour_for_output = caldera_contour if not rim_detection_failed else base_contour

    base_p1, base_p2 = find_opposite_points(base_contour)
    if rim_detection_failed:
        cal_p1, cal_p2 = (0.0, 0.0), (0.0, 0.0)
    else:
        cal_p1, cal_p2 = find_opposite_slope_points(slope, caldera_contour)

    A_base_m2 = calculate_area(base_contour, transform)
    P_base_m = contour_perimeter_m(base_contour, transform)
    D_base_m = distance_between_points(base_p1[0], base_p1[1], base_p2[0], base_p2[1], transform)

    if rim_detection_failed:
        A_caldera_m2 = 0.0
        P_caldera_m = 0.0
        D_caldera_m = 0.0
    else:
        A_caldera_m2 = calculate_area(caldera_contour, transform)
        P_caldera_m = contour_perimeter_m(caldera_contour, transform)
        D_caldera_m = distance_between_points(cal_p1[0], cal_p1[1], cal_p2[0], cal_p2[1], transform)

    height_model = height_p99_minus_p05_inside_base(dem, base_contour, nodata=nodata)
    h_max_m = float(height_model.get("h_max_m", 0.0))

    if A_base_m2 > 0 and A_caldera_m2 > 0 and h_max_m > 0:
        V_total_m3 = float((h_max_m / 3.0) * (A_base_m2 + A_caldera_m2 + math.sqrt(A_base_m2 * A_caldera_m2)))
    else:
        V_total_m3 = 0.0

    # rim_method del detector: serve a imporre la definizione di volume coerente
    # per il ramo fill-sinks (vedi blocco Opzione A sotto).
    _rim_method = None
    try:
        _rim_method = cal_dbg.get("rim_method") or cal_dbg.get("method")
    except Exception:
        _rim_method = None

    if rim_detection_failed:
        caldera_depth = {"depth_ref_m": 0.0, "V_caldera_m3": 0.0}
        depth_raw = 0.0
        V_caldera_m3 = 0.0
        fallback_used = False
    elif _rim_method == "fillsinks_fallback":
        # --- Opzione A: coerenza volumetrica con Puyehue ---
        # Quando il rim proviene dal ramo fill-sinks (caldera-depressione, es.
        # Alcedo), il volume UFFICIALE e' il volume fill-sinks fino allo sfioro
        # (idrologico, conservativo, riproducibile) — la STESSA definizione usata
        # per Puyehue. NON si usa il depth-integrated: sebbene qui darebbe un
        # valore positivo (il rim e' corretto), userebbe rim-p90/floor-p05 e
        # produrrebbe una definizione di volume DIVERSA da Puyehue, rompendo la
        # coerenza tra casi comparabili. Il metrics riporta comunque le 3
        # definizioni (sfioro / rim p90 / rim max) per trasparenza.
        caldera_depth = caldera_volume_fill_sinks(
            dem=dem,
            caldera_contour=caldera_contour,
            transform=transform,
            nodata=nodata,
        )
        V_caldera_m3 = float(caldera_depth.get("V_caldera_m3", 0.0))
        depth_raw = float(caldera_depth.get("depth_to_spill_m", 0.0))
        fallback_used = True
        # se per qualche ragione il fill-sinks non trova depressione (non
        # atteso: il ramo e' scattato proprio perche' ne ha trovata una),
        # ripiega sul depth-integrated per non azzerare il volume.
        if V_caldera_m3 <= 1.0e3:
            caldera_depth = caldera_volume_depth_integrated(
                dem=dem, caldera_contour=caldera_contour, transform=transform,
                nodata=nodata, rim_percentile=90.0, floor_percentile=5.0,
                rim_ring_offset_px=1, rim_ring_width_px=3,
            )
            depth_raw = float(caldera_depth.get("depth_ref_m", 0.0))
            V_caldera_m3 = float(caldera_depth.get("V_caldera_m3", 0.0))
            fallback_used = False
    else:
        caldera_depth = caldera_volume_depth_integrated(
            dem=dem,
            caldera_contour=caldera_contour,
            transform=transform,
            nodata=nodata,
            rim_percentile=90.0,
            floor_percentile=5.0,
            rim_ring_offset_px=1,
            rim_ring_width_px=3,
        )

        depth_raw = float(caldera_depth.get("depth_ref_m", 0.0))
        V_caldera_m3 = float(caldera_depth.get("V_caldera_m3", 0.0))
        fallback_used = False

        if depth_raw <= 0.0:
            caldera_depth_fb = caldera_volume_depth_integrated(
                dem=dem,
                caldera_contour=caldera_contour,
                transform=transform,
                nodata=nodata,
                rim_percentile=98.0,
                floor_percentile=2.0,
                rim_ring_offset_px=1,
                rim_ring_width_px=3,
            )
            depth_fb = float(caldera_depth_fb.get("depth_ref_m", 0.0))
            Vfb = float(caldera_depth_fb.get("V_caldera_m3", 0.0))
            if (depth_fb > depth_raw and Vfb >= V_caldera_m3) or (depth_fb > 0 and Vfb > 0):
                caldera_depth = caldera_depth_fb
                depth_raw = depth_fb
                V_caldera_m3 = Vfb
                fallback_used = True

        # Secondo fallback (2026-08-26): fill-sinks. Se il depth-integrated
        # (anche col fallback percentili estremi) resta a V~0 pur essendoci una
        # caldera, il rim di quota e' tracciato sulla sommita' (z_rim~z_floor).
        # Il fill-sinks calcola il volume dalla FORMA della depressione,
        # indipendente dal rim. Scatta SOLO se V ancora ~0: i casi con volume
        # gia' corretto (Fernandina, Darwin, Okmok, Karthala, Fentale) non lo
        # attivano. Se anche il fill-sinks non trova depressione (Arenal,
        # Lewotolok senza caldera), V resta 0 -> corretto.
        if V_caldera_m3 <= 1.0e3:   # ~0 (soglia in m3, di fatto zero)
            try:
                cd_fs = caldera_volume_fill_sinks(
                    dem=dem, caldera_contour=caldera_contour,
                    transform=transform, nodata=nodata,
                )
                Vfs = float(cd_fs.get("V_caldera_m3", 0.0))
                if Vfs > 1.0e3:
                    caldera_depth = cd_fs
                    depth_raw = float(cd_fs.get("depth_to_spill_m", 0.0))
                    V_caldera_m3 = Vfs
                    fallback_used = True
            except Exception:
                pass

    V_effective_m3 = float(max(0.0, V_total_m3 - V_caldera_m3))

    # Livello 1 (2026-08-04): flag additivo e onesto sulla qualita' del run.
    # Non tocca "status" (resta "completed" a valle in main(): la UI --
    # RimMapModal, PDF -- e' gated su status === 'completed' e deve restare
    # utilizzabile, es. per rifinire manualmente Valles/Long Valley dopo un
    # run automatico degenere). run_quality/run_issues sono la fonte di
    # verita' per escludere questi casi da statistiche quantitative del
    # benchmark.
    run_issues = []
    if bool(base_dbg.get("degenerate")):
        run_issues.append("degenerate_base")
    if rim_detection_failed:
        run_issues.append("rim_detection_failed")
    elif bool(cal_dbg.get("partial")):
        run_issues.append("partial_rim")
    run_quality = "ok" if not run_issues else "degenerate"

    return {
        "base_contour": base_contour,
        "caldera_contour": caldera_contour_for_output,
        "base_points": {"p1_rc": base_p1, "p2_rc": base_p2},
        "caldera_points": {"p1_rc": cal_p1, "p2_rc": cal_p2},
        "base_debug": base_dbg,
        "caldera_debug": cal_dbg,
        "height_model": height_model,
        "caldera_depth": caldera_depth,
        "caldera_fallback_used": bool(fallback_used),
        "run_quality": run_quality,
        "run_issues": run_issues,
        "morphometrics": {
            "A_base_m2": float(A_base_m2),
            "P_base_m": float(P_base_m),
            "D_base_m": float(D_base_m),
            "A_caldera_m2": float(A_caldera_m2),
            "P_caldera_m": float(P_caldera_m),
            "D_caldera_m": float(D_caldera_m),
        },
        "volumes": {
            "V_total_m3": float(V_total_m3),
            "V_caldera_m3": float(V_caldera_m3),
            "V_effective_m3": float(V_effective_m3),
        }
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python volume_unified.py <dem_path> [original_file_stem]")
        return 1

    cli_dem = sys.argv[1]
    cli_stem = sys.argv[2] if len(sys.argv) > 2 else ""

    process_id = _resolve_process_id()
    proc_dir = _ensure_proc_dir(process_id)

    base_profile_env = os.environ.get("BASE_PROFILE") or os.environ.get("BASE_SCENARIO") or ""
    gvp_type_env = os.environ.get("GVP_TYPE") or ""

    # Week 1-2: GVP-informed preset selection.
    # If GVP_TYPE is set and recognised, base_preset and rim_preset are
    # derived from the mapping in _GVP_TYPE_TO_PRESETS. Otherwise both fall
    # back to the legacy BASE_PROFILE behaviour (byte-identical to pre-patch).
    base_preset, rim_preset, preset_source_dbg = resolve_presets(
        base_profile_env=base_profile_env,
        gvp_type=gvp_type_env,
    )

    # Keep the legacy `base_profile` variable name in scope: it drives the
    # module_key naming and metrics.json fields already consumed by the UI.
    # Semantically it now means "base contour preset".
    base_profile = base_preset

    _log(
        f"preset resolution: source={preset_source_dbg['preset_source']} "
        f"base={base_preset} rim={rim_preset} "
        f"gvp_type='{gvp_type_env}' base_profile_env='{base_profile_env}'"
    )

    module_key = (os.environ.get("MODULE_KEY") or "").strip()
    if not module_key:
        module_key = f"unified_{base_profile}"

    dem_path, reason = _pick_dem_manifest_first(proc_dir, cli_dem)
    if reason == "missing" or not dem_path.exists():
        print(f"[ERROR] DEM not found. Expected dem_working.tif in {proc_dir} or valid CLI DEM.")
        return 1

    dem_path = ensure_metric_dem(dem_path, proc_dir)

    with rasterio.open(dem_path) as src:
        dem = src.read(1)
        transform = src.transform
        crs = src.crs
        res = src.res
        nodata = src.nodata
        bounds_tuple = (src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top)

    meta = {
        "process_id": process_id,
        "input_dem_path": str(Path(cli_dem).expanduser().resolve()),
        "working_dem_path": str(dem_path),
        "crs": str(crs) if crs is not None else None,
        "res": list(res) if res is not None else None,
        "nodata": nodata,
        "bounds": list(bounds_tuple),
        "original_file_name": os.environ.get("ORIGINAL_FILE_NAME") or str(Path(cli_dem).name),
        "original_file_stem": os.environ.get("ORIGINAL_FILE_STEM") or (cli_stem or Path(cli_dem).stem),
        "preset_selection": preset_source_dbg,
    }

    # Naming outputs con il nome del vulcano (stem del DEM) — richiesta
    # 2026-08-22 (Marco). La cartella resta l'UUID (unicita' garantita);
    # qui prefissiamo/suffissiamo solo i file "prodotto" che l'utente scarica
    # o guarda: metrics_<stem>.json/csv e final_doublet_base_vs_caldera_<stem>.png.
    # I file di lavoro (volume_results.json, meta.json, caldera_rim_*.geojson,
    # dem_preview.*) restano a nome fisso: server.js e il RimMapModal li
    # indirizzano per nome, rinominarli romperebbe editing/anteprima.
    def _slugify_stem(s: str) -> str:
        s = str(s or "").strip()
        s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)   # solo caratteri sicuri per un filename
        s = re.sub(r"_+", "_", s).strip("_.")
        return s or "output"
    _stem_slug = _slugify_stem(meta.get("original_file_stem") or Path(cli_dem).stem)
    metrics_json_name = f"metrics_{_stem_slug}.json"
    metrics_csv_name = f"metrics_{_stem_slug}.csv"
    doublet_name = f"final_doublet_base_vs_caldera_{_stem_slug}.png"

    rim_source: str = "auto"
    rim_file: str = "caldera_rim_auto.geojson"
    caldera_contour_override: Optional[np.ndarray] = None

    use_edited_rim = str(os.environ.get("USE_EDITED_RIM", "")).strip().lower() in ("1", "true", "yes", "on")
    rim_path_env = (os.environ.get("RIM_PATH") or "").strip()

    if use_edited_rim and rim_path_env:
        rim_candidate = Path(rim_path_env)
        if rim_candidate.exists():
            _log(f"edited rim override requested: {rim_candidate}")
            caldera_contour_override = load_edited_rim_contour_from_geojson(
                rim_path=rim_candidate,
                transform=transform,
                dst_crs=crs,
                dem_shape=dem.shape,
            )
            rim_source = "edited"
            rim_file = rim_candidate.name
            _log(f"edited rim override loaded: vertices={int(caldera_contour_override.shape[0])}")
        else:
            _log(f"edited rim override requested but file is missing: {rim_candidate}")

    out = run_unified(
        dem,
        transform,
        meta,
        base_profile=base_profile,
        caldera_contour_override=caldera_contour_override,
        rim_source=rim_source,
        rim_preset=rim_preset,
    )

    auto_rim_path = proc_dir / "caldera_rim_auto.geojson"
    if rim_source == "auto":
        _log("write_auto_rim_geojson START")
        write_auto_rim_geojson(
            caldera_contour=out["caldera_contour"],
            transform=transform,
            src_crs=crs,
            out_path=auto_rim_path,
        )
        _log("write_auto_rim_geojson DONE")
    else:
        _log("skip write_auto_rim_geojson because edited rim is active")

    dem_preview_png = proc_dir / "dem_preview.png"
    dem_preview_json = proc_dir / "dem_preview.json"
    _log("write_dem_preview START")
    write_dem_preview(
        dem=dem,
        bounds_tuple=bounds_tuple,
        src_crs=crs,
        out_png=dem_preview_png,
        out_json=dem_preview_json,
    )
    _log("write_dem_preview DONE")

    doublet_path = proc_dir / doublet_name
    save_final_doublet_png(
        dem=dem,
        base_contour=out["base_contour"],
        base_p1=out["base_points"]["p1_rc"],
        base_p2=out["base_points"]["p2_rc"],
        caldera_contour=out["caldera_contour"],
        cal_p1=out["caldera_points"]["p1_rc"],
        cal_p2=out["caldera_points"]["p2_rc"],
        out_path=doublet_path,
    )

    metrics = {
        "meta": {
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "process_id": process_id,
            "moduleKey": module_key,
            "baseProfile": base_profile,
            "model": "unified_area_prismoid_plus_caldera_depth_integrated",
            "rim_source": rim_source,
            "rim_file": rim_file,

            "original_file_name": meta.get("original_file_name"),
            "original_file_stem": meta.get("original_file_stem"),
            "input_dem_path": meta.get("input_dem_path"),
            "working_dem_path": meta.get("working_dem_path"),
            "crs": meta.get("crs"),
            "res": meta.get("res"),
            "nodata": meta.get("nodata"),
            "preset_selection": meta.get("preset_selection"),

            "base_selection": out.get("base_debug"),
            "caldera_rim_detection": out.get("caldera_debug"),
            "run_quality": out.get("run_quality", "ok"),
            "run_issues": out.get("run_issues", []),
        },
        "nodata_stats": dem_nodata_stats(dem, nodata=nodata),
        "height_model": out.get("height_model"),
        "morphometrics": out.get("morphometrics"),
        "volume_model": {
            "edifice_model": "prismoid_area_based",
            "caldera_model": "depth_integrated_rim_to_dem",
            "inputs_used": {
                "h_max_m": float(out.get("height_model", {}).get("h_max_m", 0.0)),
                "A_base_m2": float(out["morphometrics"]["A_base_m2"]),
                "A_caldera_m2": float(out["morphometrics"]["A_caldera_m2"]),
                "caldera_depth": {
                    "rim_reference": {
                        "z_rim_ref_m": out["caldera_depth"].get("z_rim_ref_m"),
                        "percentile": out["caldera_depth"].get("rim_percentile"),
                        "method": out["caldera_depth"].get("rim_method"),
                        "ring_offset_px": out["caldera_depth"].get("rim_ring_offset_px"),
                        "ring_width_px": out["caldera_depth"].get("rim_ring_width_px"),
                        "sample_count": out["caldera_depth"].get("rim_sample_count"),
                    },
                    "floor_reference": {
                        "z_floor_ref_m": out["caldera_depth"].get("z_floor_ref_m"),
                        "percentile": out["caldera_depth"].get("floor_percentile"),
                        "method": "percentile_inside_mask",
                    },
                    "depth_ref_m": out["caldera_depth"].get("depth_ref_m"),
                    "depth_ref_m_clamped": out["caldera_depth"].get("depth_ref_m_clamped"),
                    "mask_area_m2": out["caldera_depth"].get("caldera_mask_area_m2"),
                    "pixel_area_m2": out["caldera_depth"].get("pixel_area_m2"),
                    "fallback_used": bool(out.get("caldera_fallback_used", False)),
                }
            },
        },
        "volumes": out.get("volumes"),
        "geometry": {
            "base_points": out.get("base_points"),
            "caldera_points": out.get("caldera_points"),
        }
    }

    metrics_json_path = proc_dir / metrics_json_name
    metrics_csv_path = proc_dir / metrics_csv_name

    metrics_json_path.write_text(json.dumps(_as_serializable(metrics), indent=2, ensure_ascii=False), encoding="utf-8")

    rows = metrics_to_human_rows(metrics)
    with open(metrics_csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["section", "metric", "value", "unit"])
        for section, label_txt, value, unit in rows:
            w.writerow([section, label_txt, value, unit])

    res0 = meta.get("res")
    pixel_size_m = None
    try:
        if isinstance(res0, (list, tuple)) and len(res0) >= 1:
            pixel_size_m = float(res0[0])
    except Exception:
        pixel_size_m = None

    mm = out["morphometrics"]
    vv = out["volumes"]

    base_area_km2 = float(mm["A_base_m2"]) * 1e-6
    caldera_area_km2 = float(mm["A_caldera_m2"]) * 1e-6
    base_width_km = float(mm["D_base_m"]) * 1e-3
    caldera_width_km = float(mm["D_caldera_m"]) * 1e-3

    total_km3 = float(vv["V_total_m3"]) * 1e-9
    caldera_km3 = float(vv["V_caldera_m3"]) * 1e-9
    eff_km3 = float(vv["V_effective_m3"]) * 1e-9

    summary = (
        f"Base area: {base_area_km2:.2f} km²\n"
        f"Base span: {base_width_km:.2f} km\n"
        f"Caldera area: {caldera_area_km2:.2f} km²\n"
        f"Caldera span: {caldera_width_km:.2f} km\n"
        f"Total edifice volume: {total_km3:.2f} km³\n"
        f"Caldera volume: {caldera_km3:.3e} km³\n"
        f"Effective edifice volume: {eff_km3:.2f} km³"
    )

    vr = {
        "processId": process_id,
        "status": "completed",
        "moduleKey": module_key,
        "baseProfile": base_profile,
        "volumeType": "unified",
        "approximationType": "unified",
        "rim_source": rim_source,
        "rim_file": rim_file,
        "run_quality": out.get("run_quality", "ok"),
        "run_issues": out.get("run_issues", []),
        "summaryText": summary,
        "result": {
            "base_area_km2": base_area_km2,
            "base_width_km": base_width_km,
            "caldera_area_km2": caldera_area_km2,
            "caldera_width_km": caldera_width_km,
            "total_volume_km3": total_km3,
            "caldera_volume_km3": caldera_km3,
            "effective_volume_km3": eff_km3,
            "h_max_m": float(out.get("height_model", {}).get("h_max_m", 0.0)),
            "pixel_size_m": pixel_size_m,
        },
        "images": [doublet_name],
        "links": {
            "metrics_json": _public_path(process_id, metrics_json_name),
            "metrics_csv": _public_path(process_id, metrics_csv_name),
            "final_doublet": _public_path(process_id, doublet_name),
            "caldera_rim_auto": _public_path(process_id, "caldera_rim_auto.geojson"),
            "dem_preview_png": _public_path(process_id, "dem_preview.png"),
            "dem_preview_json": _public_path(process_id, "dem_preview.json"),
        }
    }

    vr_path = proc_dir / "volume_results.json"
    vr_path.write_text(json.dumps(vr, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[INFO] Wrote: {vr_path}")
    print(f"[INFO] Wrote: {metrics_json_path}")
    print(f"[INFO] Wrote: {metrics_csv_path}")
    print(f"[INFO] Wrote: {doublet_path}")
    if auto_rim_path.exists():
        print(f"[INFO] Wrote: {auto_rim_path}")
    print(f"[INFO] Wrote: {dem_preview_png}")
    print(f"[INFO] Wrote: {dem_preview_json}")

    try:
        print(json.dumps({
            "processId": process_id,
            "status": "completed",
            "moduleKey": module_key,
            "baseProfile": base_profile,
            "rim_source": rim_source,
            "rim_file": rim_file,
            "run_quality": out.get("run_quality", "ok"),
            "run_issues": out.get("run_issues", []),
            "images": [doublet_name],
        }, ensure_ascii=False), flush=True)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())