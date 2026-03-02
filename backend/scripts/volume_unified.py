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
        ratio = 0.05
        level = vmin + rng * ratio
        contours = _find_contours_at_level(demf, level)
        if not contours:
            raise ValueError("No base contours found (island preset).")
        base = max(contours, key=len)
        dbg = {
            "method": "ratio_single_longest",
            "ratio": float(ratio),
            "level_m": float(level),
            "n_contours": int(len(contours)),
            "picked_len": int(len(base)),
        }
        return base, dbg

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

def find_caldera_contour_morphological(
    dem: np.ndarray,
    base_contour: np.ndarray,
    transform,
    nodata=None,
    preset: str = "continental",
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Morphological rim detection with two presets.
    We intentionally keep it parameter-driven (no new formulas).
    """
    bp = _normalize_base_profile(preset)

    # Presets (tuned knobs)
    if bp == "island":
        cfg = {
            "roi_dilate_px": 4,
            "smooth_sigma": 1.0,
            "slope_q": 90.0,
            "min_component_px": 400,
            "closing_iterations": 2,
            "extra_dilate_px": 0,
            "max_area_frac": 0.35,
            "inner_buffer_px": 10,
            "center_bias_weight": 18.0,
        }
    else:
        cfg = {
            "roi_dilate_px": 8,
            "smooth_sigma": 1.2,
            "slope_q": 86.0,
            "min_component_px": 600,
            "closing_iterations": 3,
            "extra_dilate_px": 1,
            "max_area_frac": 0.50,
            "inner_buffer_px": 14,
            "center_bias_weight": 22.0,
        }

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

    # Eligibility by size and max area fraction
    sizes = [int(np.sum(lbl == cid)) for cid in range(1, ncomp + 1)]
    eligible = []
    max_area_px = int(float(cfg["max_area_frac"]) * float(roi_px))
    for cid, sz in zip(range(1, ncomp + 1), sizes):
        if sz < int(cfg["min_component_px"]):
            continue
        if max_area_px > 0 and sz > max_area_px:
            continue
        eligible.append((cid, sz))

    if not eligible:
        raise ValueError("No rim component meets constraints.")

    # Center-biased selection with inner ROI overlap
    center = _peak_in_roi(demf, roi) or _centroid_of_mask(roi)
    if center is None:
        # fallback largest
        selected_cid, selected_sz = max(eligible, key=lambda t: t[1])
        center_used = None
    else:
        center_r, center_c = center
        center_used = {"row": float(center_r), "col": float(center_c), "method": "peak_in_roi_or_centroid"}

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

            # key: smaller mean radius is better, but reward inner overlap strongly
            key = (
                mean_radius_px - float(cfg["center_bias_weight"]) * inner_overlap,
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
    contours = measure.find_contours(best_mask.astype(np.uint8), 0.5)
    if not contours:
        raise ValueError("Rim contour extraction failed.")
    caldera_contour = max(contours, key=len)

    debug = {
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
        "center": center_used,
        "threshold": float(thr),
        "contour_len": int(len(caldera_contour)),
    }
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
        transform = src.transform
        crs = src.crs
        res = src.res
        nodata = src.nodata

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
    out = run_unified(dem, transform, meta, base_profile=base_profile)

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
        "images": ["final_doublet_base_vs_caldera.png"],
        "links": {
            "metrics_json": _public_path(process_id, "metrics.json"),
            "metrics_csv": _public_path(process_id, "metrics.csv"),
            "final_doublet": _public_path(process_id, "final_doublet_base_vs_caldera.png"),
        }
    }

    vr_path = proc_dir / "volume_results.json"
    vr_path.write_text(json.dumps(vr, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[INFO] Wrote: {vr_path}")
    print(f"[INFO] Wrote: {metrics_json_path}")
    print(f"[INFO] Wrote: {metrics_csv_path}")
    print(f"[INFO] Wrote: {doublet_path}")

    # Print compact JSON for Node logs / optional parsing
    try:
        print(json.dumps({
            "processId": process_id,
            "status": "completed",
            "moduleKey": module_key,
            "baseProfile": base_profile,
            "images": ["final_doublet_base_vs_caldera.png"],
        }, ensure_ascii=False), flush=True)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())