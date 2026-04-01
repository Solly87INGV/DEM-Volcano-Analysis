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
# No UI choice for circular/elliptical or approx types.
#
# Inputs:
# - Env (recommended):
#     PROCESS_ID, OUTPUTS_DIR, HEADLESS
#     BASE_PROFILE: island|continental
#     MODULE_KEY: usually "{volumeType}_{baseProfile}" but here use "unified_{baseProfile}"
#     ORIGINAL_FILE_NAME, ORIGINAL_FILE_STEM (optional)
# - CLI (compat):
#     python volume_unified.py <dem_path> [original_file_stem]
#
# Outputs in OUTPUTS_DIR/<PROCESS_ID>/:
# - metrics.json, metrics.csv
# - volume_results.json (UI-ready)
# - final_doublet_base_vs_caldera.png
# - caldera_rim_auto.geojson (EPSG:4326)
# - dem_preview.png
# - dem_preview.json
# -----------------------------------------------------------------------------

import os
import sys
import json
import csv
import math
import time
import datetime
import shutil
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

import numpy as np

# -------------------- PROJ/GDAL safety (Windows vs Docker) --------------------
def _fix_proj_env():
    """
    - Windows: force PROJ_LIB to pyproj datadir to avoid PostGIS PROJ mismatch.
    - Non-Windows (Docker/Linux): unset PROJ_LIB/PROJ_DATA to avoid host mismatch.
    """
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
    binary_opening,
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
    return s or "continental"


# -------------------- DEM selection (manifest-first) --------------------
def _pick_dem_manifest_first(proc_dir: Path, cli_dem: Optional[str]) -> Tuple[Path, str]:
    """
    Priority:
    1) proc_dir/dem_working.tif
    2) cli_dem if exists
    """
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
    """
    If CRS is geographic, reproject to local UTM and write proc_dir/dem_working.tif.
    If already projected, return original path.
    """
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
            height=dst_height
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
            dst_nodata=src.nodata,
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

    rows = c[:, 0]
    cols = c[:, 1]

    xs = np.empty(len(c), dtype=float)
    ys = np.empty(len(c), dtype=float)
    for i in range(len(c)):
        xs[i], ys[i] = _pixel_to_map_xy(transform, rows[i], cols[i])

    area = 0.5 * np.abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1)))
    return float(area)

def contour_perimeter_m(contour: np.ndarray, transform) -> float:
    c = np.asarray(contour, dtype=float)
    if c.shape[0] < 2:
        return 0.0

    rows = c[:, 0]
    cols = c[:, 1]
    xs = np.empty(len(c), dtype=float)
    ys = np.empty(len(c), dtype=float)
    for i in range(len(c)):
        xs[i], ys[i] = _pixel_to_map_xy(transform, rows[i], cols[i])

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




# -------------------- Step2 logging & Leaflet export helpers --------------------
def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[STEP2B {ts}] {msg}", flush=True)

def _decimate_contour(contour: "np.ndarray", max_vertices: int = 2000) -> "np.ndarray":
    c = np.asarray(contour)
    if c.ndim != 2 or c.shape[0] < 3:
        return c
    n = int(c.shape[0])
    if max_vertices <= 0 or n <= max_vertices:
        return c
    step = int(math.ceil(n / float(max_vertices)))
    c2 = c[::step].copy()
    return c2 if c2.shape[0] >= 3 else c

def _transform_xy_to_lonlat(xs, ys, src_crs: str, chunk: int = 5000):
    out_lon, out_lat = [], []
    n = len(xs)
    for i in range(0, n, chunk):
        lons, lats = rio_transform(src_crs, "EPSG:4326", xs[i:i+chunk], ys[i:i+chunk])
        out_lon.extend(list(lons))
        out_lat.extend(list(lats))
    return out_lon, out_lat

def _same_xy(a: Tuple[float, float], b: Tuple[float, float], eps: float = 1e-12) -> bool:
    return (abs(float(a[0]) - float(b[0])) <= eps) and (abs(float(a[1]) - float(b[1])) <= eps)

def _transform_xy_lists_to_wgs84(xs: List[float], ys: List[float], src_crs) -> Tuple[List[float], List[float]]:
    if src_crs is None:
        raise RuntimeError("Cannot export to EPSG:4326 because DEM CRS is missing.")
    try:
        if RioCRS.from_user_input(src_crs) == RioCRS.from_epsg(4326):
            return [float(x) for x in xs], [float(y) for y in ys]
    except Exception:
        pass
    chunk = int(os.environ.get("RIM_XFORM_CHUNK", "5000"))
    lon, lat = _transform_xy_to_lonlat(xs, ys, src_crs, chunk=chunk)
    return [float(v) for v in lon], [float(v) for v in lat]

def _contour_rc_to_lonlat_ring(contour: np.ndarray, transform, src_crs) -> List[List[float]]:
    c = np.asarray(contour, dtype=float)
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

def write_auto_rim_geojson(caldera_contour: np.ndarray, transform, src_crs, out_path: Path) -> None:
    max_vertices = int(os.environ.get("RIM_MAX_VERTICES", "2000"))
    n0 = int(np.asarray(caldera_contour).shape[0]) if caldera_contour is not None else -1
    _log(f"write_auto_rim_geojson: original_vertices={n0} max_vertices={max_vertices}")
    caldera_contour = _decimate_contour(caldera_contour, max_vertices=max_vertices)
    n1 = int(np.asarray(caldera_contour).shape[0]) if caldera_contour is not None else -1
    _log(f"write_auto_rim_geojson: decimated_vertices={n1}")
    ring = _contour_rc_to_lonlat_ring(caldera_contour, transform, src_crs)
    feature = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"source": "auto", "editing_crs": "EPSG:4326"},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(feature, indent=2, ensure_ascii=False), encoding="utf-8")

def _normalize_dem_for_preview(dem: np.ndarray, nodata=None, p_lo: float = 2.0, p_hi: float = 98.0) -> np.ndarray:
    arr = dem.astype(float)
    valid = np.isfinite(arr)
    if nodata is not None:
        try:
            valid = valid & (arr != float(nodata))
        except Exception:
            pass
    img = np.zeros(arr.shape, dtype=np.uint8)
    vals = arr[valid]
    if vals.size == 0:
        return img
    vmin = float(np.percentile(vals, p_lo))
    vmax = float(np.percentile(vals, p_hi))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        vmin = float(np.min(vals))
        vmax = float(np.max(vals))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        img[valid] = 127
        return img
    scaled = (arr - vmin) / (vmax - vmin)
    scaled = np.clip(scaled, 0.0, 1.0)
    img[valid] = np.round(scaled[valid] * 255.0).astype(np.uint8)
    return img

def write_dem_preview_png(dem: np.ndarray, nodata, out_path: Path, max_dim: int = 1600) -> None:
    img8 = _normalize_dem_for_preview(dem, nodata=nodata, p_lo=2.0, p_hi=98.0)
    h, w = img8.shape[:2]
    long_side = max(h, w)
    if long_side > int(max_dim):
        step = int(math.ceil(long_side / float(max_dim)))
        img8 = img8[::step, ::step]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.imsave(str(out_path), img8, cmap="gray", vmin=0, vmax=255, format="png")

def _bounds_to_wgs84(bounds, src_crs) -> Tuple[float, float, float, float]:
    xs = [float(bounds.left), float(bounds.right), float(bounds.right), float(bounds.left)]
    ys = [float(bounds.bottom), float(bounds.bottom), float(bounds.top), float(bounds.top)]
    lons, lats = _transform_xy_lists_to_wgs84(xs, ys, src_crs)
    west = float(min(lons))
    east = float(max(lons))
    south = float(min(lats))
    north = float(max(lats))
    return west, south, east, north

def write_dem_preview_json(bounds, src_crs, out_path: Path) -> None:
    west, south, east, north = _bounds_to_wgs84(bounds, src_crs)
    payload = {
        "bounds": [[south, west], [north, east]],
        "crs": "EPSG:4326",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

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

def select_base_contour(dem: np.ndarray, transform, nodata=None, base_profile: str = "continental") -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Island: simple + deterministic (legacy-like) -> ratio=0.05, pick longest contour.
    Continental: robust -> sweep ratios, score candidates, pick best.
    """
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
        # Island: still deterministic, but avoid pathological base contours that hug the raster border.
        # This is important for summit-focused crops (e.g., Piton) where the minimum elevation often lies on the clip edge.
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
                # Avoid clip-edge / coastline artifacts
                if _touches_border(c, demf.shape, margin_px=2):
                    continue

                mask = contour_to_mask(c, demf.shape)
                area_px = float(np.sum(mask))
                area_frac = float(area_px / img_area_px)

                # reject pathological: too small or too big
                if area_frac < 0.01 or area_frac > 0.75:
                    continue

                A = calculate_area(c, transform)
                P = contour_perimeter_m(c, transform)
                circ = (4.0 * math.pi * A / (P * P)) if (A > 0 and P > 0) else 0.0

                # score: prefer mid-size base + some circularity + longer contour
                size_term = -abs(area_frac - 0.35)
                score = 2.0 * size_term + 0.3 * float(circ) + 0.0005 * float(len(c))

                n_kept += 1
                if score > best_score:
                    best_score = score
                    best = c
                    picked_ratio = float(ratio)
                    picked_level = float(level)

        if best is None:
            # Last-resort fallback: allow border-touching but still prefer reasonable size.
            ratio = 0.12
            level = vmin + rng * ratio
            contours = _find_contours_at_level(demf, level)
            if not contours:
                raise ValueError("No base contours found (island preset).")
            # pick the largest non-tiny contour
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

    # continental: sweep and score
    ratios = [0.03, 0.05, 0.07, 0.10, 0.13, 0.16, 0.20]
    best = None

    img_area_px = float(demf.shape[0] * demf.shape[1])

    for ratio in ratios:
        level = vmin + rng * float(ratio)
        contours = _find_contours_at_level(demf, level)
        if not contours:
            continue

        for c in contours:
            if len(c) < 50:
                continue
            if _touches_border(c, demf.shape, margin_px=2):
                continue

            mask = contour_to_mask(c, demf.shape)
            area_px = float(np.sum(mask))
            area_frac = float(area_px / img_area_px)

            # reject pathological: too small or too big
            if area_frac < 0.01 or area_frac > 0.75:
                continue

            A = calculate_area(c, transform)
            P = contour_perimeter_m(c, transform)
            circ = (4.0 * math.pi * A / (P * P)) if (A > 0 and P > 0) else 0.0

            # score: prefer mid-size base, reasonable circularity, longer contour
            # keep it simple & deterministic
            size_term = -abs(area_frac - 0.25)  # target ~25% of frame (heuristic)
            circ_term = max(0.0, min(1.0, circ))
            len_term = math.log(float(len(c)))

            score = (2.0 * size_term) + (1.0 * circ_term) + (0.25 * len_term)

            cand = {
                "ratio": float(ratio),
                "level_m": float(level),
                "len": int(len(c)),
                "area_frac_px": float(area_frac),
                "circularity": float(circ),
                "score": float(score),
            }

            if (best is None) or (score > best["cand"]["score"]):
                best = {"contour": c, "cand": cand}

    if not best:
        # hard fallback: island-style
        ratio = 0.05
        level = vmin + rng * ratio
        contours = _find_contours_at_level(demf, level)
        if not contours:
            raise ValueError("No base contours found (continental preset, fallback failed).")
        base = max(contours, key=len)
        dbg = {
            "method": "continental_sweep_failed_fallback_longest",
            "fallback_ratio": float(ratio),
            "fallback_level_m": float(level),
            "picked_len": int(len(base)),
        }
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
    """Return the location (row,col) of the minimum elevation inside ROI.
    Useful for caldera-centered selection (depression) vs peak-based selection.
    """
    try:
        vals = np.where(roi, dem.astype(float), np.inf)
        if not np.isfinite(vals).any():
            return _centroid_of_mask(roi)
        idx = int(np.nanargmin(vals))
        r, c = np.unravel_index(idx, dem.shape)
        return (float(r), float(c))
    except Exception:
        return _centroid_of_mask(roi)

def find_caldera_contour_morphological(
    dem: np.ndarray,
    base_contour: np.ndarray,
    transform,
    nodata=None,
    preset: str = "continental",
    _override_cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Morphological rim detection with two presets.
    We intentionally keep it parameter-driven (no new formulas).
    """
    debug: Dict[str, Any] = {}
    bp = _normalize_base_profile(preset)

    # Presets (tuned knobs)
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
            "center_mode": "depression",
        }

    # Optional override (used by fallback relaxed pass)
    if _override_cfg is not None:
        try:
            cfg = dict(_override_cfg)
        except Exception:
            pass

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
        # Summit/clip safety: if ROI derived from base is too small, fall back to full valid-data mask.
        roi = valid.copy()
        roi_px = int(np.sum(roi))
        debug["roi_fallback"] = {
            "reason": "base_roi_too_small",
            "roi_px": int(roi_px),
        }
        if roi_px == 0:
            raise ValueError(f"ROI too small (roi_px={roi_px}).")

    # inner ROI (eroded) for overlap scoring
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
        raise ValueError("No finite slope values inside ROI.")

    thr = float(np.percentile(roi_slopes, float(cfg["slope_q"])))
    candidates = (slopef >= thr) & roi

    if cfg["closing_iterations"] > 0:
        candidates = binary_closing(candidates, iterations=int(cfg["closing_iterations"]))
    candidates = binary_fill_holes(candidates)
    if cfg["extra_dilate_px"] > 0:
        candidates = binary_dilation(candidates, iterations=int(cfg["extra_dilate_px"]))
    candidates = candidates & roi

    cand_px = int(np.sum(candidates))
    if cand_px == 0:
        raise ValueError("Candidate rim mask empty after morphology.")

    lbl, ncomp = label(candidates)
    if ncomp <= 0:
        raise ValueError("No components in rim candidates.")

    # Eligibility by size and area fractions (relative to ROI)
    sizes = [int(np.sum(lbl == cid)) for cid in range(1, ncomp + 1)]
    eligible = []

    max_area_px = int(float(cfg["max_area_frac"]) * float(roi_px))
    min_area_px = int(float(cfg.get("min_area_frac", 0.0)) * float(roi_px))

    # Always enforce min_component_px, and (optionally) a minimum area fraction
    hard_min_px = int(max(int(cfg["min_component_px"]), min_area_px))

    for cid, sz in zip(range(1, ncomp + 1), sizes):
        if sz < hard_min_px:
            continue
        if max_area_px > 0 and sz > max_area_px:
            continue
        eligible.append((cid, sz))
    if not eligible:
        # Second-pass fallback: relax constraints and retry once.
        # Useful for small summit-focused crops (e.g., Piton/Dolomieu) where the rim may be fragmented.
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
                _override_cfg=cfg_rel,
            )
        raise ValueError("No rim component meets constraints.")


    # Barnie-mode heuristic (summit caldera with deep pit craters):
    # If the only eligible components are very small relative to ROI, the detector is likely picking pit fragments.
    # In that case, run one additional relaxed pass that:
    # - lowers slope_q more (includes more of the caldera scarp),
    # - increases closing (connects fragmented rim),
    # - increases inner_buffer (reduces pit dominance in overlap),
    # - penalizes inner overlap (prefers boundary ring instead of pit interior).
    if eligible and not bool(cfg.get("_barnie_pass", False)):
        roi_px_f = float(max(1, roi_px))
        max_elig_px = max(int(sz) for _, sz in eligible)
        max_elig_frac = float(max_elig_px) / roi_px_f
        # trigger when components are tiny (<~1.5% of ROI) which is typical of pit fragments on summit crops
        if max_elig_frac < float(cfg.get("barnie_trigger_max_elig_frac", 0.015)):
            cfg_rel2 = dict(cfg)
            cfg_rel2["_barnie_pass"] = True
            cfg_rel2["slope_q"] = float(cfg_rel2.get("slope_q", 90.0)) - 10.0
            cfg_rel2["closing_iterations"] = int(cfg_rel2.get("closing_iterations", 2)) + 4
            cfg_rel2["inner_buffer_px"] = int(cfg_rel2.get("inner_buffer_px", 10)) + 8
            cfg_rel2["min_component_px"] = max(80, int(cfg_rel2.get("min_component_px", 400) * 0.35))
            cfg_rel2["min_area_frac"] = float(cfg_rel2.get("min_area_frac", 0.0)) * 0.25
            cfg_rel2["post_close_iterations"] = int(cfg_rel2.get("post_close_iterations", 2)) + 2
            cfg_rel2["center_mode"] = "centroid"
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
                _override_cfg=cfg_rel2,
            )

    # Center-biased selection with inner ROI overlap
    center_mode = str(cfg.get("center_mode", "depression")).strip().lower()

    # Adaptive center selection:
    # - "depression" is great for simple calderas (Okmok), but fails when deep pits exist inside the caldera (Erta Ale),
    #   because the minimum can sit on a pit or edge/noise and drag selection to a small component.
    # Heuristics to auto-switch to centroid:
    #   1) depression-center falls near raster border (likely edge/noise) OR
    #   2) eligible components are all small relative to ROI (likely pit/segment) OR
    #   3) depression-center is far from centroid of roi_inner (pit offset).
    def _near_border(rc, shape, margin=3):
        if rc is None:
            return True
        r, c = rc
        return (r < margin) or (c < margin) or (r > (shape[0] - 1 - margin)) or (c > (shape[1] - 1 - margin))

    center_dep = _min_in_roi(demf, roi_inner) or _min_in_roi(demf, roi)
    center_ctr = _centroid_of_mask(roi_inner) or _centroid_of_mask(roi)

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

    if center_mode in ("auto", "adaptive"):
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
    }

    best = None
    for cid, sz in eligible:
            m = (lbl == cid)
            rr, cc = np.nonzero(m)
            if rr.size == 0:
                continue

            # distance stats to center
            rad = np.hypot(rr - center_r, cc - center_c)
            mean_radius_px = float(np.mean(rad))
            p90_radius_px = float(np.percentile(rad, 90))

            cent = _centroid_of_mask(m) or (center_r, center_c)
            dr = float(cent[0] - center_r)
            dc = float(cent[1] - center_c)
            d2 = float(dr * dr + dc * dc)

            # inner overlap (prefer components that actually occupy inner ROI)
            try:
                inner_overlap = float(np.sum(m & roi_inner)) / float(np.sum(m) + 1e-9)
            except Exception:
                inner_overlap = 0.0

            # key: smaller mean radius is better.
            # Default behavior rewards inner overlap (works well for simple calderas).
            # For pit-dominated summit systems (e.g., Erta Ale), we may instead penalize inner overlap
            # to avoid selecting pit-slope fragments.
            overlap_mode = str(cfg.get("overlap_mode", "reward_inner")).strip().lower()
            w = float(cfg["center_bias_weight"])
            overlap_term = (-w * inner_overlap) if overlap_mode in ("reward_inner", "inner", "reward") else (+w * inner_overlap)
            key = (
                mean_radius_px + overlap_term,
                p90_radius_px,
                d2,
                -sz
            )

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

    best_mask = (lbl == selected_cid)
    # Post-process selected rim mask to reduce small gaps and spurs.
    # This helps cases where the rim has minor discontinuities in the slope-based candidates.
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
    # Keep the largest connected component after post-processing
    try:
        lbl2, n2 = label(best_mask)
        if n2 > 1:
            sizes2 = [int(np.sum(lbl2 == cid)) for cid in range(1, n2 + 1)]
            keep = int(np.argmax(sizes2) + 1)
            best_mask = (lbl2 == keep)
    except Exception:
        pass
    contours = measure.find_contours(best_mask.astype(np.uint8), 0.5)
    if not contours:
        raise ValueError("Rim contour extraction failed.")
    caldera_contour = max(contours, key=len)

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
        "post_process": {"post_close_iterations": int(max(0, cfg.get("post_close_iterations", 0))), "post_fill_holes": bool(cfg.get("post_fill_holes", True))},
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

    ring = outside_ring_mask(caldera_mask, offset_px=rim_ring_offset_px, width_px=rim_ring_width_px)
    rim_vals = demf[ring & valid]
    rim_method = "percentile_on_outside_ring"

    if rim_vals.size == 0:
        # fallback: percentile on contour samples
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
    im1 = ax1.imshow(dem, cmap="terrain", origin="upper", interpolation="nearest", resample=False)
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
    im2 = ax2.imshow(dem, cmap="terrain", origin="upper", interpolation="nearest", resample=False)
    ax2.plot(caldera_contour[:, 1], caldera_contour[:, 0], "b-", linewidth=1)
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
def run_unified(dem: np.ndarray, transform, meta: Dict[str, Any], base_profile: str) -> Dict[str, Any]:
    nodata = meta.get("nodata", None)

    base_contour, base_dbg = select_base_contour(dem, transform, nodata=nodata, base_profile=base_profile)
    slope = calculate_slope(dem)

    caldera_contour, cal_dbg = find_caldera_contour_morphological(
        dem=dem,
        base_contour=base_contour,
        transform=transform,
        nodata=nodata,
        preset=base_profile,
    )

    # Visualization points
    base_p1, base_p2 = find_opposite_points(base_contour)
    cal_p1, cal_p2 = find_opposite_slope_points(slope, caldera_contour)

    # Morphometrics
    A_base_m2 = calculate_area(base_contour, transform)
    A_caldera_m2 = calculate_area(caldera_contour, transform)
    P_base_m = contour_perimeter_m(base_contour, transform)
    P_caldera_m = contour_perimeter_m(caldera_contour, transform)

    D_base_m = distance_between_points(base_p1[0], base_p1[1], base_p2[0], base_p2[1], transform)
    D_caldera_m = distance_between_points(cal_p1[0], cal_p1[1], cal_p2[0], cal_p2[1], transform)

    # Height
    height_model = height_p99_minus_p05_inside_base(dem, base_contour, nodata=nodata)
    h_max_m = float(height_model.get("h_max_m", 0.0))

    # Edifice volume (prismoid)
    if A_base_m2 > 0 and A_caldera_m2 > 0 and h_max_m > 0:
        V_total_m3 = float((h_max_m / 3.0) * (A_base_m2 + A_caldera_m2 + math.sqrt(A_base_m2 * A_caldera_m2)))
    else:
        V_total_m3 = 0.0

    # Caldera volume (depth-integrated)
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

    # Fallback percentiles if depth is non-positive
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

    V_effective_m3 = float(max(0.0, V_total_m3 - V_caldera_m3))

    return {
        "base_contour": base_contour,
        "caldera_contour": caldera_contour,
        "base_points": {"p1_rc": base_p1, "p2_rc": base_p2},
        "caldera_points": {"p1_rc": cal_p1, "p2_rc": cal_p2},
        "base_debug": base_dbg,
        "caldera_debug": cal_dbg,
        "height_model": height_model,
        "caldera_depth": caldera_depth,
        "caldera_fallback_used": bool(fallback_used),
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

    base_profile = _normalize_base_profile(os.environ.get("BASE_PROFILE") or os.environ.get("BASE_SCENARIO") or "")
    module_key = (os.environ.get("MODULE_KEY") or "").strip()
    if not module_key:
        module_key = f"unified_{base_profile}"

    dem_path, reason = _pick_dem_manifest_first(proc_dir, cli_dem)
    if reason == "missing" or not dem_path.exists():
        print(f"[ERROR] DEM not found. Expected dem_working.tif in {proc_dir} or valid CLI DEM.")
        return 1

    # Ensure metric CRS (extra safety)
    dem_path = ensure_metric_dem(dem_path, proc_dir)

    # Read DEM
    with rasterio.open(dem_path) as src:
        dem = src.read(1)
        _log("DEM read OK")
        _log(f"DEM shape={dem.shape} crs={src.crs}")
        transform = src.transform
        crs = src.crs
        res = src.res
        nodata = src.nodata
        bounds = src.bounds

    meta = {
        "process_id": process_id,
        "input_dem_path": str(Path(cli_dem).expanduser().resolve()),
        "working_dem_path": str(dem_path),
        "crs": str(crs) if crs is not None else None,
        "res": list(res) if res is not None else None,
        "nodata": nodata,
        "original_file_name": os.environ.get("ORIGINAL_FILE_NAME") or str(Path(cli_dem).name),
        "original_file_stem": os.environ.get("ORIGINAL_FILE_STEM") or (cli_stem or Path(cli_dem).stem),
    }

    # Compute
    _log("run_unified START")
    out = run_unified(dem, transform, meta, base_profile=base_profile)
    _log("run_unified DONE")

    auto_rim_name = "caldera_rim_auto.geojson"
    auto_rim_path = proc_dir / auto_rim_name
    _log("write_auto_rim_geojson START")
    write_auto_rim_geojson(
        caldera_contour=out["caldera_contour"],
        transform=transform,
        src_crs=crs,
        out_path=auto_rim_path,
    )
    _log("write_auto_rim_geojson DONE")

    dem_preview_png_name = "dem_preview.png"
    dem_preview_json_name = "dem_preview.json"
    dem_preview_png_path = proc_dir / dem_preview_png_name
    dem_preview_json_path = proc_dir / dem_preview_json_name
    _log("write_dem_preview START")
    write_dem_preview_png(dem=dem, nodata=nodata, out_path=dem_preview_png_path)
    write_dem_preview_json(bounds=bounds, src_crs=crs, out_path=dem_preview_json_path)
    _log("write_dem_preview DONE")

    # Save final doublet
    doublet_name = "final_doublet_base_vs_caldera.png"
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

    # Build metrics dict (canonical)
    metrics = {
        "meta": {
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "process_id": process_id,
            "moduleKey": module_key,
            "baseProfile": base_profile,
            "model": "unified_area_prismoid_plus_caldera_depth_integrated",

            "original_file_name": meta.get("original_file_name"),
            "original_file_stem": meta.get("original_file_stem"),
            "input_dem_path": meta.get("input_dem_path"),
            "working_dem_path": meta.get("working_dem_path"),
            "crs": meta.get("crs"),
            "res": meta.get("res"),
            "nodata": meta.get("nodata"),

            "base_selection": out.get("base_debug"),
            "caldera_rim_detection": out.get("caldera_debug"),
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

    # Write metrics.json/csv
    metrics_json_path = proc_dir / "metrics.json"
    metrics_csv_path = proc_dir / "metrics.csv"

    metrics_json_path.write_text(json.dumps(_as_serializable(metrics), indent=2, ensure_ascii=False), encoding="utf-8")

    rows = metrics_to_human_rows(metrics)
    with open(metrics_csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["section", "metric", "value", "unit"])
        for section, label_txt, value, unit in rows:
            w.writerow([section, label_txt, value, unit])

    # Build volume_results.json (UI-ready)
    res0 = meta.get("res")
    pixel_size_m = None
    try:
        if isinstance(res0, (list, tuple)) and len(res0) >= 1:
            pixel_size_m = float(res0[0])
    except Exception:
        pixel_size_m = None

    # Convert to km units for UI
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
        "images": ["final_doublet_base_vs_caldera.png", "dem_preview.png"],
        "links": {
            "metrics_json": _public_path(process_id, "metrics.json"),
            "metrics_csv": _public_path(process_id, "metrics.csv"),
            "final_doublet": _public_path(process_id, "final_doublet_base_vs_caldera.png"),
            "dem_preview": _public_path(process_id, "dem_preview.png"),
            "dem_preview_meta": _public_path(process_id, "dem_preview.json"),
            "caldera_rim_auto": _public_path(process_id, "caldera_rim_auto.geojson"),
        }
    }

    vr_path = proc_dir / "volume_results.json"
    vr_path.write_text(json.dumps(vr, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[INFO] Wrote: {vr_path}")
    print(f"[INFO] Wrote: {metrics_json_path}")
    print(f"[INFO] Wrote: {metrics_csv_path}")
    print(f"[INFO] Wrote: {doublet_path}")
    print(f"[INFO] Wrote: {auto_rim_path}")
    print(f"[INFO] Wrote: {dem_preview_png_path}")
    print(f"[INFO] Wrote: {dem_preview_json_path}")

    # Print compact JSON for Node logs / optional parsing
    try:
        print(json.dumps({
            "processId": process_id,
            "status": "completed",
            "moduleKey": module_key,
            "baseProfile": base_profile,
            "images": ["final_doublet_base_vs_caldera.png", "dem_preview.png"],
        }, ensure_ascii=False), flush=True)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
