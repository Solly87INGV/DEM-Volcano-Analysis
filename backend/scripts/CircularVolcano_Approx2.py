# CircularVolcano_Approx2.py
# ——————————————————————————————————————————————————————————
# Approx2: mantiene i calcoli esistenti.
# Report:
#  - usa DEM + doppiette dal manifest (se presente), rimuovendo SOLO le triplette
#  - aggiunge in coda la nuova DOPPIETTA “Base vs Caldera” (stessa gabbia delle doppiette buone)
#  - fail-safe: se la lista è vuota, usa almeno la doppietta
# GUI: 3 pannelli (DEM, Base opposta, Caldera max slope) + overview PNG + payload JSON.
#
# ✅ Integrazione richiesta:
#  - generazione metrics.json + metrics.csv (umano verticale) in outputs/<process_id>/
#  - export automatico silenzioso in __init__ (come Approx1 funzionante)
#  - pulsante GUI "Export Metrics (JSON + CSV)"
#
# ✅ INTEGRAZIONE (questa risposta):
#  - caldera contour NON più da iso-quota globale (sovrastima),
#    ma da "break-of-morphology" (rim/crest interno) via:
#      1) coarse ROI = iso-contour alto (tratteggiato)
#      2) centro robusto dal "floor" dentro ROI
#      3) profili radiali: max slope (parete) + ricerca crest/rim subito fuori
#    Fallback robusto: se fallisce, torna al coarse contour.

import sys
import os
import csv
import time
import datetime
import math

# ================= PROJ / EPSG FIX (Windows + PostGIS conflicts) =================
def _force_proj_lib_to_pyproj():
    try:
        from pyproj import datadir
        proj_dir = datadir.get_data_dir()
        if proj_dir and os.path.isdir(proj_dir):
            os.environ["PROJ_LIB"] = proj_dir
            print(f"[DEBUG] PROJ_LIB forced to pyproj: {proj_dir}")
    except Exception as e:
        print(f"[WARN] Could not force PROJ_LIB via pyproj: {e}")

_force_proj_lib_to_pyproj()
# ================================================================================

import json
import numpy as np
import rasterio
from scipy.ndimage import sobel
from skimage import measure

from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QMessageBox, QFileDialog
)

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib import gridspec
from mpl_toolkits.axes_grid1 import make_axes_locatable

import pdf_generator
METRICS_BASENAME = "metrics_circ_a2"

# ========== helpers path/manifest ==========

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))

def _find_latest_dem_working() -> str:
    base = os.path.join(_script_dir(), "outputs")
    if not os.path.isdir(base):
        return ""

    newest_path = ""
    newest_mtime = -1.0

    for name in os.listdir(base):
        cand = os.path.join(base, name, "dem_working.tif")
        if os.path.exists(cand):
            mt = os.path.getmtime(cand)
            if mt > newest_mtime:
                newest_mtime = mt
                newest_path = cand

    return newest_path

def _resolve_process_id() -> str:
    return os.environ.get("PROCESS_ID") or f"local_{int(time.time())}"

def _ensure_outputs_dir(process_id: str) -> str:
    out_dir = os.path.join(_script_dir(), "outputs", process_id)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def _find_manifest():
    base = os.path.join(_script_dir(), "outputs")

    pid = os.environ.get("PROCESS_ID")
    if pid:
        out_dir = os.path.join(base, pid)
        mp = os.path.join(out_dir, "analysis_images.json")
        if os.path.exists(mp):
            return out_dir, mp

    if os.path.isdir(base):
        candidates = []
        for name in os.listdir(base):
            p = os.path.join(base, name, "analysis_images.json")
            if os.path.exists(p):
                candidates.append(p)
        if candidates:
            candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            mp = candidates[0]
            return os.path.dirname(mp), mp

    return None, None

def _load_manifest_images(manifest_path):
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        images = data.get("images", [])
        paths = []
        for it in images:
            p = it.get("abs_path") or it.get("filename")
            if p:
                paths.append(p)
        return paths
    except Exception as e:
        print(f"[WARN] Could not read manifest '{manifest_path}': {e}")
        return []

def _normalize_and_filter_paths(paths, base_dir=None):
    norm = []
    for p in paths:
        if not p:
            continue
        pp = p
        if not os.path.isabs(pp) and base_dir:
            pp = os.path.join(base_dir, os.path.basename(p))
        pp = os.path.normpath(pp)
        if os.path.exists(pp):
            norm.append(pp)
        else:
            print(f"[WARN] Missing image on disk (skipped): {pp}")
    return norm

def _remove_triplets(paths):
    return [p for p in paths if "triplet_" not in os.path.basename(p).lower()]


# ========== Analysis functions ==========

def find_lowest_base_contour(matrix, base_elevation_ratio=0.05):
    base_level = matrix.min() + (matrix.max() - matrix.min()) * base_elevation_ratio
    contours = measure.find_contours(matrix, base_level)
    if len(contours) == 0:
        raise ValueError("No contours found for the given base elevation ratio.")
    return max(contours, key=len)

def find_opposite_base_points(contour):
    contour = np.round(contour).astype(int)
    max_index1 = 0
    opposite_index = len(contour) // 2
    base_index1 = tuple(contour[max_index1])
    base_index2 = tuple(contour[opposite_index])
    return base_index1, base_index2

def _pixel_to_map_xy(transform, row, col):
    x, y = transform * (col + 0.5, row + 0.5)
    return float(x), float(y)

def distance_between_points(r1, c1, r2, c2, transform):
    x1, y1 = _pixel_to_map_xy(transform, r1, c1)
    x2, y2 = _pixel_to_map_xy(transform, r2, c2)
    return float(np.hypot(x2 - x1, y2 - y1))

def calculate_area(contour, transform):
    contour = np.asarray(contour, dtype=float)
    if contour.shape[0] < 3:
        return 0.0

    rows = contour[:, 0]
    cols = contour[:, 1]

    xs = np.empty(len(contour), dtype=float)
    ys = np.empty(len(contour), dtype=float)

    for i in range(len(contour)):
        xs[i], ys[i] = _pixel_to_map_xy(transform, rows[i], cols[i])

    area = 0.5 * np.abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1)))
    return float(area)

def calculate_slope(matrix):
    dx = sobel(matrix, axis=1)
    dy = sobel(matrix, axis=0)
    return np.hypot(dx, dy)

def find_caldera_contour_iso(matrix, level_ratio=0.8):
    contour_level = matrix.max() * level_ratio
    contours = measure.find_contours(matrix, contour_level)
    if len(contours) == 0:
        raise ValueError("No contours found for the given level ratio.")
    return max(contours, key=len), float(contour_level)

def find_opposite_slope_points(slope_matrix, contour):
    contour = np.round(contour).astype(int)
    contour = contour[
        (contour[:, 0] >= 0) & (contour[:, 0] < slope_matrix.shape[0]) &
        (contour[:, 1] >= 0) & (contour[:, 1] < slope_matrix.shape[1])
    ]
    if len(contour) == 0:
        raise ValueError("No valid points in contour after filtering.")
    contour_slopes = [slope_matrix[pt[0], pt[1]] for pt in contour]
    max_index1 = int(np.argmax(contour_slopes))
    max_slope_index1 = tuple(contour[max_index1])
    opposite_index = (max_index1 + len(contour) // 2) % len(contour)
    max_slope_index2 = tuple(contour[opposite_index])
    return max_slope_index1, max_slope_index2


# ========== Caldera depth-integrated helpers (identici a Approx1) ==========

def pixel_area_m2_from_transform(transform):
    try:
        return float(abs(transform.a * transform.e - transform.b * transform.d))
    except Exception:
        try:
            return float(abs(transform.a * transform.e))
        except Exception:
            return 0.0

def contour_to_mask(contour, shape):
    from skimage.draw import polygon
    mask = np.zeros(shape, dtype=bool)
    if contour is None:
        return mask
    c = np.asarray(contour, dtype=float)
    if c.size == 0:
        return mask
    rr = c[:, 0]
    cc = c[:, 1]
    pr, pc = polygon(rr, cc, shape)
    mask[pr, pc] = True
    return mask

def outside_ring_mask(mask: np.ndarray, offset_px: int = 1, width_px: int = 3) -> np.ndarray:
    from scipy.ndimage import binary_dilation
    if mask is None or mask.size == 0:
        return np.zeros_like(mask, dtype=bool)
    if offset_px < 0:
        offset_px = 0
    if width_px < 1:
        width_px = 1

    inner = binary_dilation(mask, iterations=offset_px)
    outer = binary_dilation(mask, iterations=offset_px + width_px)
    ring = outer & (~inner)
    return ring

def caldera_volume_depth_integrated(
    dem: np.ndarray,
    caldera_contour: np.ndarray,
    transform,
    nodata=None,
    rim_percentile: float = 90.0,
    floor_percentile: float = 5.0,
    rim_ring_offset_px: int = 1,
    rim_ring_width_px: int = 3
) -> dict:
    dem = np.asarray(dem)
    caldera_mask = contour_to_mask(caldera_contour, dem.shape)

    valid = np.isfinite(dem)
    if nodata is not None:
        try:
            valid = valid & (dem != float(nodata))
        except Exception:
            pass

    px_area = pixel_area_m2_from_transform(transform)
    caldera_mask_area_m2 = float(np.sum(caldera_mask)) * float(px_area)

    ring = outside_ring_mask(caldera_mask, offset_px=rim_ring_offset_px, width_px=rim_ring_width_px)
    rim_samples = dem[ring & valid]
    rim_method = "outside_ring_percentile"

    if rim_samples.size == 0:
        rim_method = "contour_percentile"
        c = np.round(np.asarray(caldera_contour)).astype(int) if caldera_contour is not None else np.zeros((0, 2), dtype=int)
        if c.size > 0:
            rr = c[:, 0]
            cc = c[:, 1]
            ok = (rr >= 0) & (rr < dem.shape[0]) & (cc >= 0) & (cc < dem.shape[1])
            rr = rr[ok]
            cc = cc[ok]
            if rr.size > 0:
                rim_samples = dem[rr, cc]
                rim_samples = rim_samples[np.isfinite(rim_samples)]
                if nodata is not None:
                    try:
                        rim_samples = rim_samples[rim_samples != float(nodata)]
                    except Exception:
                        pass

    rim_sample_count = int(rim_samples.size) if rim_samples is not None else 0
    z_rim_ref_m = float(np.percentile(rim_samples, rim_percentile)) if rim_sample_count > 0 else None

    inside = caldera_mask & valid
    floor_samples = dem[inside]
    z_floor_ref_m = float(np.percentile(floor_samples, floor_percentile)) if floor_samples.size > 0 else None

    depth_ref_m = float((z_rim_ref_m - z_floor_ref_m)) if (z_rim_ref_m is not None and z_floor_ref_m is not None) else 0.0
    depth_ref_m_clamped = float(max(0.0, depth_ref_m))

    V_caldera_m3 = 0.0
    if z_rim_ref_m is not None and px_area > 0 and np.any(inside):
        dz = (float(z_rim_ref_m) - dem.astype(float))
        dz = np.where(inside, dz, 0.0)
        dz = np.maximum(dz, 0.0)
        dz = np.where(np.isfinite(dz), dz, 0.0)
        V_caldera_m3 = float(np.sum(dz)) * float(px_area)

    return {
        "V_caldera_m3": float(V_caldera_m3),
        "z_rim_ref_m": z_rim_ref_m,
        "z_floor_ref_m": z_floor_ref_m,
        "depth_ref_m": float(depth_ref_m),
        "depth_ref_m_clamped": float(depth_ref_m_clamped),
        "rim_method": rim_method,
        "rim_ring_offset_px": int(rim_ring_offset_px),
        "rim_ring_width_px": int(rim_ring_width_px),
        "rim_sample_count": int(rim_sample_count),
        "rim_percentile": float(rim_percentile),
        "floor_percentile": float(floor_percentile),
        "pixel_area_m2": float(px_area),
        "caldera_mask_area_m2": float(caldera_mask_area_m2),
    }

def robust_height_p99_minus_p05(dem: np.ndarray, base_contour: np.ndarray, nodata=None) -> float:
    dem = np.asarray(dem).astype(float)

    valid = np.isfinite(dem)
    if nodata is not None:
        try:
            valid = valid & (dem != float(nodata))
        except Exception:
            pass

    base_mask = contour_to_mask(base_contour, dem.shape) if base_contour is not None else np.zeros(dem.shape, dtype=bool)
    z = dem[base_mask & valid]
    if z.size == 0:
        z = dem[valid]
    if z.size == 0:
        return 0.0

    return float(np.percentile(z, 99) - np.percentile(z, 5))


# ========== NEW: Caldera rim detection (break-of-morphology) ==========

def _safe_round_int(x):
    return int(np.clip(int(round(x)), -10**9, 10**9))

def _circular_moving_average(values: np.ndarray, win: int) -> np.ndarray:
    """
    Smooth 1D circular series with odd window.
    """
    v = np.asarray(values, dtype=float)
    n = v.size
    if n == 0:
        return v
    win = int(win)
    if win < 3:
        return v
    if win % 2 == 0:
        win += 1
    pad = win // 2
    vv = np.r_[v[-pad:], v, v[:pad]]
    kernel = np.ones(win, dtype=float) / float(win)
    sm = np.convolve(vv, kernel, mode="valid")  # length n+pad+pad - win + 1 = n
    return sm[:n]

def _centroid_from_contour(contour: np.ndarray):
    c = np.asarray(contour, dtype=float)
    if c.size == 0:
        return None
    return float(np.mean(c[:, 0])), float(np.mean(c[:, 1]))

def _robust_center_from_floor(dem: np.ndarray, mask: np.ndarray, valid: np.ndarray, floor_percentile: float = 10.0):
    vals = dem[mask & valid]
    if vals.size < 20:
        return None
    thr = float(np.percentile(vals, floor_percentile))
    rr, cc = np.where(mask & valid & (dem <= thr))
    if rr.size < 10:
        return None
    return float(np.mean(rr)), float(np.mean(cc))

def _first_local_max_after(z: np.ndarray, start_idx: int, min_sep: int = 2):
    """
    Finds first local maximum after start_idx+min_sep where dz goes + then -.
    If not found, returns argmax on tail.
    """
    z = np.asarray(z, dtype=float)
    n = z.size
    s = int(start_idx) + int(min_sep)
    if n < 5 or s >= n - 2:
        return int(np.argmax(z)) if n > 0 else None

    dz = np.diff(z)
    # local max at i where dz[i-1] > 0 and dz[i] <= 0
    for i in range(max(2, s), n - 2):
        if dz[i - 1] > 0 and dz[i] <= 0:
            return i
    # fallback
    return int(np.argmax(z[s:]) + s)

def find_caldera_rim_break_of_morphology(
    dem: np.ndarray,
    slope: np.ndarray,
    coarse_contour: np.ndarray,
    nodata=None,
    floor_center_percentile: float = 10.0,
    n_rays: int = 360,
    inner_frac: float = 0.35,
    outer_frac: float = 1.05,
    smooth_window: int = 11,
    max_radius_guard: int = 2500
):
    """
    Returns: rim_contour (Nx2 row/col), center_rc (row,col)
    """
    demf = dem.astype(float)
    slp = slope.astype(float)

    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass

    coarse_mask = contour_to_mask(coarse_contour, demf.shape)
    if np.sum(coarse_mask & valid) < 100:
        # ROI troppo piccola o problematica
        cen = _centroid_from_contour(coarse_contour)
        if cen is None:
            raise ValueError("Coarse caldera ROI invalid and centroid failed.")
        center_r, center_c = cen
        return coarse_contour, (center_r, center_c)

    # Center from floor
    center = _robust_center_from_floor(demf, coarse_mask, valid, floor_percentile=floor_center_percentile)
    if center is None:
        cen = _centroid_from_contour(coarse_contour)
        if cen is None:
            raise ValueError("Cannot compute caldera center (floor + centroid failed).")
        center_r, center_c = cen
    else:
        center_r, center_c = center

    H, W = demf.shape
    center_r_i = float(center_r)
    center_c_i = float(center_c)

    # For each angle, estimate outer radius where it exits ROI (coarse mask)
    angles = np.linspace(0.0, 2.0 * np.pi, int(n_rays), endpoint=False)
    rim_points = []
    radii = []

    for ang in angles:
        dr = math.sin(ang)
        dc = math.cos(ang)

        # max possible radius to stay inside image
        # conservative (avoid long loops): also guard with max_radius_guard
        max_r_img = min(
            max_radius_guard,
            int(max(H, W))
        )

        # step along ray (pixel grid)
        rr_list = []
        cc_list = []
        for k in range(1, max_r_img):
            rr = center_r_i + dr * k
            cc = center_c_i + dc * k
            rri = int(round(rr))
            cci = int(round(cc))
            if rri < 0 or rri >= H or cci < 0 or cci >= W:
                break
            rr_list.append(rri)
            cc_list.append(cci)

        if len(rr_list) < 30:
            continue

        rr_arr = np.asarray(rr_list, dtype=int)
        cc_arr = np.asarray(cc_list, dtype=int)

        in_roi = coarse_mask[rr_arr, cc_arr]
        if not np.any(in_roi):
            continue

        # find contiguous segment starting near center: we expect first indices maybe inside
        # take the farthest index still inside ROI (outer boundary along this ray)
        idx_inside = np.where(in_roi)[0]
        if idx_inside.size < 10:
            continue
        outer_idx = int(idx_inside.max())

        # define search interval around that outer radius
        r_outer = outer_idx
        r0 = int(max(5, math.floor(r_outer * float(inner_frac))))
        r1 = int(min(len(rr_arr) - 1, math.ceil(r_outer * float(outer_frac))))
        if r1 - r0 < 20:
            continue

        prof_rr = rr_arr[r0:r1+1]
        prof_cc = cc_arr[r0:r1+1]

        # valid samples
        vmask = valid[prof_rr, prof_cc]
        if np.sum(vmask) < 20:
            continue

        z_prof = demf[prof_rr, prof_cc]
        s_prof = slp[prof_rr, prof_cc]

        # if many invalids, skip
        if not np.isfinite(s_prof).any() or not np.isfinite(z_prof).any():
            continue

        # pick wall = max slope in interval (robust: ignore invalid)
        s_tmp = np.where(np.isfinite(s_prof), s_prof, -np.inf)
        wall_rel = int(np.argmax(s_tmp))
        if not np.isfinite(s_tmp[wall_rel]):
            continue

        # crest = first local max in elevation after wall (rim)
        crest_rel = _first_local_max_after(z_prof, wall_rel, min_sep=2)
        if crest_rel is None:
            continue

        rr_rim = int(prof_rr[crest_rel])
        cc_rim = int(prof_cc[crest_rel])

        # radius from center
        rad = float(math.hypot(rr_rim - center_r_i, cc_rim - center_c_i))

        rim_points.append((rr_rim, cc_rim))
        radii.append(rad)

    if len(rim_points) < max(40, int(n_rays * 0.25)):
        # too few points -> fallback to coarse
        return coarse_contour, (center_r, center_c)

    rim_points = np.asarray(rim_points, dtype=float)
    radii = np.asarray(radii, dtype=float)

    # remove outliers by radius
    r_lo = float(np.percentile(radii, 5))
    r_hi = float(np.percentile(radii, 95))
    keep = (radii >= r_lo) & (radii <= r_hi)
    rim_points = rim_points[keep]
    radii = radii[keep]
    if rim_points.shape[0] < max(30, int(n_rays * 0.2)):
        return coarse_contour, (center_r, center_c)

    # sort by angle (recompute angle from center)
    angs = np.arctan2(rim_points[:, 0] - center_r_i, rim_points[:, 1] - center_c_i)
    order = np.argsort(angs)
    rim_points = rim_points[order]
    radii = radii[order]
    angs = angs[order]

    # smooth radii circularly
    radii_sm = _circular_moving_average(radii, smooth_window)

    # reconstruct smoothed points on same angles (use direction vectors from angle)
    rr_sm = center_r_i + np.sin(angs) * radii_sm
    cc_sm = center_c_i + np.cos(angs) * radii_sm

    rim_contour = np.stack([rr_sm, cc_sm], axis=1).astype(float)

    return rim_contour, (center_r, center_c)


# ========== Metrics helpers ==========

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

def dem_nodata_stats(dem, nodata=None):
    arr = dem.astype(float)
    finite = np.isfinite(arr)
    if nodata is not None:
        finite = finite & (arr != float(nodata))
    valid = arr[finite]
    return {
        "valid_count": int(valid.size),
        "total_count": int(arr.size),
        "valid_min": float(np.min(valid)) if valid.size else None,
        "valid_max": float(np.max(valid)) if valid.size else None,
        "valid_p02": float(np.percentile(valid, 2)) if valid.size else None,
        "valid_p98": float(np.percentile(valid, 98)) if valid.size else None,
    }

def contour_perimeter_m(contour, transform) -> float:
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

def _get_by_path(d: dict, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur

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

    ("Base morphometrics", "Base area", "morphometrics.A_base_m2", "m²"),
    ("Base morphometrics", "Base perimeter", "morphometrics.P_base_m", "m"),
    ("Base morphometrics", "Base diameter (opposite points)", "morphometrics.D_base_m", "m"),
    ("Base morphometrics", "Base radius used in volume", "morphometrics.R_base_m", "m"),
    ("Base morphometrics", "Equivalent base radius from area", "morphometrics.R_eq_base_m", "m"),

    ("Caldera morphometrics", "Caldera area", "morphometrics.A_caldera_m2", "m²"),
    ("Caldera morphometrics", "Caldera perimeter", "morphometrics.P_caldera_m", "m"),
    ("Caldera morphometrics", "Caldera diameter (opposite points)", "morphometrics.D_caldera_m", "m"),
    ("Caldera morphometrics", "Caldera radius used in volume", "morphometrics.R_caldera_m", "m"),
    ("Caldera morphometrics", "Equivalent caldera radius from area", "morphometrics.R_eq_caldera_m", "m"),

    ("Height model (Approx2)", "Height method", "height_model.method", ""),
    ("Height model (Approx2)", "Height (P99 - P05) inside base", "height_model.p99_minus_p05_m", "m"),

    ("Caldera depth (QA)", "Caldera reference depth (rim p90 - floor p05)", "volume_model.inputs_used.caldera_depth.depth_ref_m", "m"),
    ("Caldera depth (QA)", "Caldera rim reference elevation", "volume_model.inputs_used.caldera_depth.rim_reference.z_rim_ref_m", "m"),
    ("Caldera depth (QA)", "Caldera floor reference elevation", "volume_model.inputs_used.caldera_depth.floor_reference.z_floor_ref_m", "m"),
    ("Caldera depth (QA)", "Caldera mask area used for integration", "volume_model.inputs_used.caldera_depth.mask_area_m2", "m²"),
    ("Caldera depth (QA)", "Caldera reference depth CLAMPED", "volume_model.inputs_used.caldera_depth.depth_ref_m_clamped", "m"),
    ("Caldera depth (QA)", "Caldera fallback percentiles used", "volume_model.inputs_used.caldera_depth.fallback_used", ""),
    ("Caldera depth (QA)", "Caldera status", "volume_model.inputs_used.caldera_depth.status", ""),
    ("Caldera depth (QA)", "Caldera status reason", "volume_model.inputs_used.caldera_depth.reason", ""),
    ("Caldera depth (QA)", "Caldera action", "volume_model.inputs_used.caldera_depth.action", ""),

    ("Model descriptors", "Edifice volume model", "volume_model.edifice_model", ""),
    ("Model descriptors", "Caldera volume model", "volume_model.caldera_model", ""),
    ("Model descriptors", "Intermediate frustum-like volume", "volume_model.intermediate.V_frustum_m3", "m³"),

    ("Volumes", "Total edifice volume", "volumes.V_total_m3", "m³"),
    ("Volumes", "Caldera volume", "volumes.V_caldera_m3", "m³"),
    ("Volumes", "Effective edifice volume", "volumes.V_effective_m3", "m³"),

    ("Derived", "Slenderness H/Dbase", "derived.slenderness_H_over_Dbase", ""),
    ("Derived", "Sanity Abase/Dbase²", "derived.sanity_Abase_over_Dbase2", ""),
    ("Derived", "Circularity base", "derived.circularity_base", ""),
    ("Derived", "Circularity caldera", "derived.circularity_caldera", ""),
    ("Derived", "Equivalent height V/Abase", "derived.eq_height_V_over_Abase_m", "m"),
    ("Derived", "Ratio vs perfect cone", "derived.ratio_vs_cone", ""),
]

def metrics_to_human_rows(metrics: dict):
    rows = []
    for item in HUMAN_FIELDS:
        if len(item) == 4:
            section, label, path, unit = item
        elif len(item) == 2:
            path, label = item
            section = ""
            unit = ""
        else:
            continue

        v = _get_by_path(metrics, path)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        rows.append((section, label, v, unit))
    return rows


# ========== Main App ==========

class VolumeAnalysisApp(QMainWindow):
    def __init__(self, dem, transform, meta=None, original_file_name="Unknown"):
        super().__init__()
        self.setWindowTitle('Volcano Volume Analysis')
        self.dem = dem
        self.transform = transform
        self.original_file_name = original_file_name

        self.meta = meta or {}

        self.process_id = self.meta.get("process_id") or _resolve_process_id()
        self.out_dir = _ensure_outputs_dir(self.process_id)
        self.metrics_basename = METRICS_BASENAME

        self.calculate_results()
        self.initUI()

        try:
            self._save_overview_png()
            self._emit_stdout_payload()
        except Exception as e:
            print(f"[PY VOL WARN] post-render saving/payload failed: {e}")

        try:
            self._write_metrics_files()
        except Exception as e:
            print(f"[WARN] metrics export failed: {e}")

    def initUI(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)

        self.figure = Figure(figsize=(18, 14), constrained_layout=True)
        self.canvas = FigureCanvas(self.figure)
        main_layout.addWidget(self.canvas)

        button_layout = QHBoxLayout()
        main_layout.addLayout(button_layout)

        self.results_button = QPushButton('View Results && Print Report')
        self.results_button.setFixedSize(180, 50)
        self.results_button.clicked.connect(self.show_results)
        button_layout.addWidget(self.results_button)

        self.download_image_button = QPushButton('Download as PNG or JPG')
        self.download_image_button.setFixedSize(200, 50)
        self.download_image_button.clicked.connect(self.download_graph_image)
        button_layout.addWidget(self.download_image_button)

        self.export_metrics_button = QPushButton('Export Metrics (JSON + CSV)')
        self.export_metrics_button.setFixedSize(220, 50)
        self.export_metrics_button.clicked.connect(self.export_metrics)
        button_layout.addWidget(self.export_metrics_button)

        self.update_display()

    def calculate_results(self):
        try:
            # BASE
            self.base_contour = find_lowest_base_contour(self.dem, base_elevation_ratio=0.05)
            self.base_point1, self.base_point2 = find_opposite_base_points(self.base_contour)

            self.distance_meters_base = distance_between_points(
                self.base_point1[0], self.base_point1[1],
                self.base_point2[0], self.base_point2[1],
                self.transform
            )
            self.distance_base_km = self.distance_meters_base * 1e-3

            # CALDERA: slope map
            self.slope = calculate_slope(self.dem)

            # 1) coarse contour (ROI)
            self.coarse_caldera_contour, self.coarse_caldera_level_m = find_caldera_contour_iso(self.dem, level_ratio=0.8)

            # 2) refined rim contour (break-of-morphology)
            nodata = self.meta.get("nodata", None)
            try:
                rim_contour, center_rc = find_caldera_rim_break_of_morphology(
                    dem=self.dem,
                    slope=self.slope,
                    coarse_contour=self.coarse_caldera_contour,
                    nodata=nodata,
                    floor_center_percentile=10.0,
                    n_rays=360,
                    inner_frac=0.35,
                    outer_frac=1.05,
                    smooth_window=11,
                    max_radius_guard=2500
                )
                self.caldera_contour = rim_contour
                self.caldera_center_rc = center_rc  # (row,col) float
                self.caldera_detection_method = "rim_break_of_morphology"
            except Exception as e:
                print(f"[WARN] rim detection failed, fallback to coarse contour: {e}")
                self.caldera_contour = self.coarse_caldera_contour
                self.caldera_center_rc = _centroid_from_contour(self.coarse_caldera_contour) or (0.0, 0.0)
                self.caldera_detection_method = "fallback_coarse_iso"

            # 3) opposite points on contour using slope along *rim* contour
            self.max_slope_index1, self.max_slope_index2 = find_opposite_slope_points(self.slope, self.caldera_contour)

            # distance caldera
            self.distance_meters_caldera = distance_between_points(
                self.max_slope_index1[0], self.max_slope_index1[1],
                self.max_slope_index2[0], self.max_slope_index2[1],
                self.transform
            )
            self.distance_caldera_km = self.distance_meters_caldera * 1e-3

            # AREAS
            self.area_base_m2 = float(calculate_area(self.base_contour, self.transform))
            self.area_caldera_m2 = float(calculate_area(self.caldera_contour, self.transform))
            self.area_base = self.area_base_m2 * 1e-6
            self.area_caldera = self.area_caldera_m2 * 1e-6

            # VOLUMES Approx2 (edifice frustum-like + caldera depth-integrated)
            self.h_max = robust_height_p99_minus_p05(self.dem, self.base_contour, nodata=nodata)
            self.R1 = self.distance_meters_base / 2
            self.R2 = self.distance_meters_caldera / 2

            self.v = (1/3) * np.pi * self.h_max * (self.R1**2 + self.R2**2 + self.R1 * self.R2)
            self.v_km3 = self.v * 1e-9

            caldera_depth = caldera_volume_depth_integrated(
                dem=self.dem,
                caldera_contour=self.caldera_contour,
                transform=self.transform,
                nodata=nodata,
                rim_percentile=90.0,
                floor_percentile=5.0,
                rim_ring_offset_px=1,
                rim_ring_width_px=3
            )

            depth_raw = float(caldera_depth.get("depth_ref_m", 0.0))
            V0 = float(caldera_depth.get("V_caldera_m3", 0.0))

            self.caldera_fallback_used = False
            if depth_raw <= 0:
                caldera_depth_fb = caldera_volume_depth_integrated(
                    dem=self.dem,
                    caldera_contour=self.caldera_contour,
                    transform=self.transform,
                    nodata=nodata,
                    rim_percentile=98.0,
                    floor_percentile=2.0,
                    rim_ring_offset_px=1,
                    rim_ring_width_px=3
                )

                depth_fb = float(caldera_depth_fb.get("depth_ref_m", 0.0))
                V_fb = float(caldera_depth_fb.get("V_caldera_m3", 0.0))

                if (depth_fb > depth_raw) and (V_fb > V0):
                    caldera_depth = caldera_depth_fb
                    self.caldera_fallback_used = True

            V_caldera_m3 = float(caldera_depth.get("V_caldera_m3", 0.0))
            V_effective_m3 = float(self.v - V_caldera_m3)

            # classification
            if float(caldera_depth.get("depth_ref_m", 0.0)) <= 0:
                self.caldera_status = "non_depressive_or_complex"
                self.caldera_reason = "rim_below_floor"
                self.caldera_action = "not_computed"
            elif V_caldera_m3 <= 0:
                self.caldera_status = "non_depressive_or_complex"
                self.caldera_reason = "no_depression_pixels"
                self.caldera_action = "not_computed"
            else:
                self.caldera_status = "depressive"
                self.caldera_reason = None
                self.caldera_action = "computed"

            # QA attrs
            self.caldera_z_rim_ref_m = caldera_depth.get("z_rim_ref_m", None)
            self.caldera_z_floor_ref_m = caldera_depth.get("z_floor_ref_m", None)
            self.caldera_depth_ref_m = caldera_depth.get("depth_ref_m", None)
            self.caldera_depth_ref_m_clamped = caldera_depth.get("depth_ref_m_clamped", None)
            self.caldera_rim_method = caldera_depth.get("rim_method", None)
            self.caldera_rim_ring_offset_px = caldera_depth.get("rim_ring_offset_px", None)
            self.caldera_rim_ring_width_px = caldera_depth.get("rim_ring_width_px", None)
            self.caldera_rim_sample_count = caldera_depth.get("rim_sample_count", None)
            self.caldera_rim_percentile = caldera_depth.get("rim_percentile", None)
            self.caldera_floor_percentile = caldera_depth.get("floor_percentile", None)
            self.caldera_pixel_area_m2 = caldera_depth.get("pixel_area_m2", None)
            self.caldera_mask_area_m2 = caldera_depth.get("caldera_mask_area_m2", None)

            # legacy attrs
            self.r2 = self.R2 * 1e-3
            self.p = self.r2

            self.v_caldera = V_caldera_m3 * 1e-9
            self.v_volcano = V_effective_m3 * 1e-9

            self.description_base = (
                "Base 1: Represents one of the two opposite points along\n"
                "the base contour of the volcano. It is selected as part of\n"
                "the base delimitation process, relying on a specific\n"
                "elevation threshold calculated from the DEM.\n\n"
                "Base 2: Represents the point opposite to Base 1 along\n"
                "the base contour.\n"
                "Its position is automatically calculated considering the\n"
                "geometry of the base to obtain a representative width."
            )
            self.description_slope = (
                "Max Slope 1: Represents the point on the caldera\n"
                "contour with the highest slope, calculated using a\n"
                "slope map derived from the DEM.\n\n"
                "Max Slope 2: This is the point on the caldera contour\n"
                "opposite to Max Slope 1, positioned approximately\n"
                "halfway around the contour."
            )

            if getattr(self, "caldera_status", None) == "depressive":
                caldera_line_gui = f"Caldera volume: {self.v_caldera:.3e} km³"
                caldera_line_pdf = caldera_line_gui
            else:
                reason = getattr(self, "caldera_reason", "complex")
                caldera_line_gui = f"Caldera volume: N/A (complex/non-depressive: {reason})"
                caldera_line_pdf = f"Caldera volume: N/A ({reason})"

            self.results_text = (
                f"Base area of the volcano: {self.area_base:.2f} km²\n"
                f"Base width (Distance between opposite points of the base): {self.distance_base_km:.2f} km\n"
                f"Caldera area of the volcano: {self.area_caldera:.2f} km²\n"
                f"Caldera width (Distance between opposite points of the caldera): {self.distance_caldera_km:.2f} km\n"
                f"Total volume of the volcanic edifice: {self.v_km3:.2f} km³\n"
                f"{caldera_line_gui}\n"
                f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
            )

            self.results_list = [
                f"Base area of the volcano: {self.area_base:.2f} km²",
                f"Base width (Distance between opposite points of the base): {self.distance_base_km:.2f} km",
                f"Caldera area of the volcano: {self.area_caldera:.2f} km²",
                f"Caldera width (Distance between opposite points of the caldera): {self.distance_caldera_km:.2f} km",
                f"Total volume of the volcanic edifice: {self.v_km3:.2f} km³",
                f"{caldera_line_pdf}",
                f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
            ]

        except Exception as e:
            QMessageBox.critical(self, "Calculation Error", f"An error occurred during calculation: {e}")

    def update_display(self):
        self.figure.clear()
        fig = self.figure

        fig.set_constrained_layout_pads(w_pad=0.12, h_pad=0.02, wspace=0.40, hspace=0.60)
        gs = gridspec.GridSpec(nrows=3, ncols=3, height_ratios=[4, 1, 1.5], figure=fig, wspace=0.4, hspace=0.6)

        # DEM
        ax1 = fig.add_subplot(gs[0, 0])
        im1 = ax1.imshow(self.dem, cmap='terrain', origin='upper')
        div1 = make_axes_locatable(ax1)
        cax1 = div1.append_axes("right", size="4.6%", pad=0.10)
        cax1.set_in_layout(True)
        cbar1 = fig.colorbar(im1, cax=cax1)
        cbar1.set_label("Elevation (m)", rotation=90)
        cbar1.ax.yaxis.set_ticks_position('right')
        cbar1.ax.yaxis.set_label_position('right')
        cbar1.ax.tick_params(labelsize=9, pad=1)
        cbar1.ax.yaxis.labelpad = 2
        ax1.set_title("Volcano DEM", fontsize=14, pad=20, y=1.02)
        ax1.axis('on')

        # Base
        ax2 = fig.add_subplot(gs[0, 1])
        im2 = ax2.imshow(self.dem, cmap='terrain', origin='upper')
        div2 = make_axes_locatable(ax2)
        cax2 = div2.append_axes("right", size="4.6%", pad=0.10)
        cax2.set_in_layout(True)
        cbar2 = fig.colorbar(im2, cax=cax2)
        cbar2.set_label("Elevation (m)", rotation=90)
        cbar2.ax.yaxis.set_ticks_position('right')
        cbar2.ax.yaxis.set_label_position('right')
        cbar2.ax.tick_params(labelsize=9, pad=1)
        cbar2.ax.yaxis.labelpad = 2

        p1, = ax2.plot(self.base_point1[1], self.base_point1[0], 'ro', markersize=10, label='Base 1')
        p2, = ax2.plot(self.base_point2[1], self.base_point2[0], 'yo', markersize=10, label='Base 2')
        cplot, = ax2.plot(self.base_contour[:, 1], self.base_contour[:, 0], 'w-', linewidth=1, label="Base Contour")
        ax2.set_title("Opposite Points of the Volcano Base", fontsize=14, pad=20, y=1.02)
        ax2.axis('on')

        # Caldera
        ax3 = fig.add_subplot(gs[0, 2])
        im3 = ax3.imshow(self.dem, cmap='terrain', origin='upper')
        div3 = make_axes_locatable(ax3)
        cax3 = div3.append_axes("right", size="4.6%", pad=0.10)
        cax3.set_in_layout(True)
        cbar3 = fig.colorbar(im3, cax=cax3)
        cbar3.set_label("Elevation (m)", rotation=90)
        cbar3.ax.yaxis.set_ticks_position('right')
        cbar3.ax.yaxis.set_label_position('right')
        cbar3.ax.tick_params(labelsize=9, pad=1)
        cbar3.ax.yaxis.labelpad = 2

        s1, = ax3.plot(self.max_slope_index1[1], self.max_slope_index1[0], 'ro', markersize=10, label='Max Slope 1')
        s2, = ax3.plot(self.max_slope_index2[1], self.max_slope_index2[0], 'yo', markersize=10, label='Max Slope 2')

        # ✅ coarse ROI (tratteggiato) + center (magenta) + rim (blu)
        h_roi = None
        if hasattr(self, "coarse_caldera_contour") and self.coarse_caldera_contour is not None:
            h_roi, = ax3.plot(self.coarse_caldera_contour[:, 1], self.coarse_caldera_contour[:, 0],
                              'c--', linewidth=1, label="Coarse ROI")

        h_center = None
        if hasattr(self, "caldera_center_rc") and self.caldera_center_rc is not None:
            cr, cc = self.caldera_center_rc
            h_center, = ax3.plot(cc, cr, 'mo', markersize=7, label="Center (ROI floor)")

        cald, = ax3.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label="Caldera Contour")

        ax3.set_title("Opposite Maximum Slope Points on the Caldera", fontsize=14, pad=20, y=1.02)
        ax3.axis('on')

        # legends row
        l2 = fig.add_subplot(gs[1, 1]); l2.axis('off')
        leg2 = l2.legend([p1, p2, cplot], ['Base 1', 'Base 2', 'Base Contour'],
                         loc='center', frameon=True, edgecolor='black', facecolor='lightgray', ncol=3)
        leg2.get_frame().set_linewidth(1)

        l3 = fig.add_subplot(gs[1, 2]); l3.axis('off')
        handles = [s1, s2, cald]
        labels = ['Max Slope 1', 'Max Slope 2', 'Caldera Contour']
        if h_roi is not None:
            handles.insert(2, h_roi)
            labels.insert(2, 'Coarse ROI')
        if h_center is not None:
            handles.append(h_center)
            labels.append('Center (ROI floor)')

        leg3 = l3.legend(handles, labels,
                         loc='center', frameon=True, edgecolor='black', facecolor='lightgray', ncol=2)
        leg3.get_frame().set_linewidth(1)

        l1 = fig.add_subplot(gs[1, 0]); l1.axis('off'); l1.text(0.5, 0.5, "", ha='center', va='center')

        # descriptions row
        d1 = fig.add_subplot(gs[2, 0]); d1.axis('off'); d1.text(0, 0, "", fontsize=10, ha='left', va='center')
        d2 = fig.add_subplot(gs[2, 1]); d2.axis('off')
        d2.text(0.5, 1.35, self.description_base, fontsize=10, ha='center', va='center',
                bbox=dict(boxstyle="round,pad=0.5", edgecolor="black", facecolor="white"),
                wrap=True, transform=d2.transAxes)
        d3 = fig.add_subplot(gs[2, 2]); d3.axis('off')
        d3.text(0.5, 1.5, self.description_slope, fontsize=10, ha='center', va='center',
                bbox=dict(boxstyle="round,pad=0.5", edgecolor="black", facecolor="white"),
                wrap=True, transform=d3.transAxes)

        self.canvas.draw()

    # ----- overview + payload -----
    def _save_overview_png(self):
        try:
            out_png = os.path.join(self.out_dir, "circular_approx2_overview.png")
            self.figure.savefig(out_png, dpi=150)
            print(f"[PY VOL {self.process_id}] saved {out_png}")
        except Exception as e:
            print(f"[PY VOL ERR] failed to save overview png: {e}")

    def _emit_stdout_payload(self):
        images = []
        outputs_dir, manifest_path = _find_manifest()
        if manifest_path and os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for entry in data.get("images", []):
                    public = entry.get("public_path")
                    if public:
                        images.append(public)
            except Exception as e:
                print(f"[PY VOL WARN] failed reading manifest for stdout payload: {e}")

        images.append(f"/outputs/{self.process_id}/circular_approx2_overview.png")
        payload = {"result": self.results_text, "images": images}
        print(json.dumps(payload), flush=True)

    # ----- doppietta finale -----
    def _save_final_doublet_png(self, out_path):
        from mpl_toolkits.axes_grid1 import make_axes_locatable
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

        FIG_W, FIG_H = 14.5, 5.5
        fig = plt.figure(figsize=(FIG_W, FIG_H))
        gs = gridspec.GridSpec(
            1, 3, figure=fig,
            width_ratios=[1.0, 0.08, 1.0],
            wspace=0.15
        )

        # BASE
        ax1 = fig.add_subplot(gs[0, 0])
        im1 = ax1.imshow(self.dem, cmap='terrain', origin='upper',
                         interpolation='nearest', resample=False)
        pc, = ax1.plot(self.base_contour[:, 1], self.base_contour[:, 0], 'w-', linewidth=1, label='Base Contour')
        p1, = ax1.plot(self.base_point1[1], self.base_point1[0], 'ro', markersize=8, label='Base 1')
        p2, = ax1.plot(self.base_point2[1], self.base_point2[0], 'yo', markersize=8, label='Base 2')
        ax1.set_title("Opposite Points of the Volcano Base", pad=8, fontsize=12)
        ax1.set_aspect('equal', adjustable='box')
        div1 = make_axes_locatable(ax1)
        cax1 = div1.append_axes("right", size="4.6%", pad=0.10)
        cbar1 = fig.colorbar(im1, cax=cax1); cbar1.set_label("Elevation (m)", rotation=90)
        cbar1.ax.yaxis.set_ticks_position('right')
        cbar1.ax.yaxis.set_label_position('right')
        cbar1.ax.tick_params(labelsize=9, pad=1)
        cbar1.ax.yaxis.labelpad = 2

        leg1 = ax1.legend(
            handles=[p1, p2, pc],
            labels=['Base 1', 'Base 2', 'Base Contour'],
            loc='upper right',
            frameon=True
        )
        leg1.get_frame().set_alpha(0.7)
        leg1.get_frame().set_facecolor('white')
        leg1.get_frame().set_edgecolor('black')

        ax_spacer = fig.add_subplot(gs[0, 1]); ax_spacer.axis('off')

        # CALDERA
        ax2 = fig.add_subplot(gs[0, 2])
        im2 = ax2.imshow(self.dem, cmap='terrain', origin='upper',
                         interpolation='nearest', resample=False)

        # include ROI + center in doublet (QA utile)
        if hasattr(self, "coarse_caldera_contour") and self.coarse_caldera_contour is not None:
            ax2.plot(self.coarse_caldera_contour[:, 1], self.coarse_caldera_contour[:, 0],
                     'c--', linewidth=1, label='Coarse ROI')
        if hasattr(self, "caldera_center_rc") and self.caldera_center_rc is not None:
            cr, cc = self.caldera_center_rc
            ax2.plot(cc, cr, 'mo', markersize=6, label='Center (ROI floor)')

        cc_line, = ax2.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label='Caldera Contour')
        s1, = ax2.plot(self.max_slope_index1[1], self.max_slope_index1[0], 'ro', markersize=8, label='Max Slope 1')
        s2, = ax2.plot(self.max_slope_index2[1], self.max_slope_index2[0], 'yo', markersize=8, label='Max Slope 2')

        ax2.set_title("Opposite Maximum Slope Points on the Caldera", pad=8, fontsize=12)
        ax2.set_aspect('equal', adjustable='box')
        div2 = make_axes_locatable(ax2)
        cax2 = div2.append_axes("right", size="4.6%", pad=0.10)
        cbar2 = fig.colorbar(im2, cax=cax2); cbar2.set_label("Elevation (m)", rotation=90)
        cbar2.ax.yaxis.set_ticks_position('right')
        cbar2.ax.yaxis.set_label_position('right')
        cbar2.ax.tick_params(labelsize=9, pad=1)
        cbar2.ax.yaxis.labelpad = 2

        # legenda
        handles = [s1, s2, cc_line]
        labels = ['Max Slope 1', 'Max Slope 2', 'Caldera Contour']
        leg2 = ax2.legend(handles=handles, labels=labels, loc='upper right', frameon=True)
        leg2.get_frame().set_alpha(0.7)
        leg2.get_frame().set_facecolor('white')
        leg2.get_frame().set_edgecolor('black')

        fig.savefig(out_path, dpi=170)
        plt.close(fig)

        if not os.path.exists(out_path):
            raise RuntimeError(f"Doublet not saved: {out_path}")

    # ----- UI actions -----
    def show_results(self):
        msg_box = QMessageBox()
        msg_box.setWindowTitle("Summary Results")
        msg_box.setText(self.results_text)
        download_button = msg_box.addButton("Download Full Report", QMessageBox.ActionRole)
        msg_box.addButton(QMessageBox.Ok)
        msg_box.exec_()

        if msg_box.clickedButton() == download_button:
            self.download_results()

    def download_results(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Results As", "", "PDF Files (*.pdf)", options=options)
        if not file_path:
            return
        if not file_path.lower().endswith('.pdf'):
            file_path += '.pdf'

        try:
            orig_name = os.environ.get("ORIGINAL_FILE_NAME") or self.meta.get("original_file_name") or os.path.basename(self.meta.get("input_dem_path") or "")
            title = f"Calculation Results - Circular Base, Approximation Type 2\nInput DEM: {orig_name}"

            out_dir_for_doublet = os.path.dirname(file_path) if os.path.dirname(file_path) else os.getcwd()
            doublet_png = os.path.join(out_dir_for_doublet, "final_doublet_base_vs_caldera.png")
            self._save_final_doublet_png(doublet_png)

            image_paths = []
            outputs_dir, manifest_path = _find_manifest()
            if manifest_path:
                manifest_imgs = _load_manifest_images(manifest_path)
                image_paths = _normalize_and_filter_paths(manifest_imgs, base_dir=outputs_dir)
                image_paths = _remove_triplets(image_paths)
                print(f"[INFO] Loaded {len(image_paths)} images from manifest (triplets removed).")
            else:
                print("[WARN] Manifest not found. Proceeding with doublet only if needed.")

            if os.path.exists(doublet_png):
                image_paths.append(doublet_png)
            else:
                print(f"[WARN] Doublet PNG missing unexpectedly: {doublet_png}")

            if not image_paths:
                image_paths = [doublet_png]

            print("[INFO] Images in PDF (count={}):".format(len(image_paths)))
            for p in image_paths:
                print("   -", p)

            pdf_generator.generate_pdf(
                file_path=file_path,
                results_list=self.results_list,
                title=title,
                image_paths=image_paths,
                captions=[None]*len(image_paths)
            )
            QMessageBox.information(self, "Success", f"PDF successfully saved to {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "PDF Error", f"An error occurred while generating the PDF: {e}")

    def download_graph_image(self):
        options = QFileDialog.Options()
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Graph As",
            "",
            "PNG Files (*.png);;JPG Files (*.jpg);;All Files (*)",
            options=options
        )
        if not file_path:
            return

        if selected_filter.startswith("PNG"):
            fmt = 'png'
            if not file_path.lower().endswith('.png'):
                file_path += '.png'
        elif selected_filter.startswith("JPG"):
            fmt = 'jpg'
            if not file_path.lower().endswith('.jpg') and not file_path.lower().endswith('.jpeg'):
                file_path += '.jpg'
        else:
            fmt = 'png'
            if not file_path.lower().endswith('.png'):
                file_path += '.png'

        try:
            self.figure.savefig(file_path, format=fmt)
            QMessageBox.information(self, "Success", f"Graph successfully saved as {fmt.upper()} to {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"An error occurred while saving the graph: {e}")

    # =========================
    # ✅ METRICS
    # =========================

    def _build_metrics_dict(self) -> dict:
        crs = self.meta.get("crs")
        res = self.meta.get("res")
        nodata = self.meta.get("nodata")

        A_base_m2 = float(getattr(self, "area_base_m2", float(self.area_base) * 1e6))
        A_caldera_m2 = float(getattr(self, "area_caldera_m2", float(self.area_caldera) * 1e6))

        P_base = float(contour_perimeter_m(self.base_contour, self.transform))
        P_caldera = float(contour_perimeter_m(self.caldera_contour, self.transform))

        D_base = float(self.distance_meters_base)
        D_caldera = float(self.distance_meters_caldera)

        R_base = float(self.R1)
        R_caldera = float(self.R2)

        R_eq_base = math.sqrt(A_base_m2 / math.pi) if A_base_m2 > 0 else 0.0
        R_eq_caldera = math.sqrt(A_caldera_m2 / math.pi) if A_caldera_m2 > 0 else 0.0

        h_used = float(self.h_max)

        V_frustum_m3 = float(self.v)
        V_caldera_m3 = float(self.v_caldera) * 1e9
        V_eff_m3 = float(self.v_volcano) * 1e9

        circularity_base = (4 * math.pi * A_base_m2 / (P_base ** 2)) if (A_base_m2 > 0 and P_base > 0) else None
        circularity_caldera = (4 * math.pi * A_caldera_m2 / (P_caldera ** 2)) if (A_caldera_m2 > 0 and P_caldera > 0) else None

        slenderness = (h_used / D_base) if D_base > 0 else None
        sanity_A_over_D2 = (A_base_m2 / (D_base ** 2)) if D_base > 0 else None
        eq_height_V_over_A = (V_frustum_m3 / A_base_m2) if A_base_m2 > 0 else None

        denom_cone = (1.0/3.0) * A_base_m2 * h_used
        cone_ratio = (V_frustum_m3 / denom_cone) if (denom_cone and denom_cone > 0) else None

        base_p1_rc = [int(self.base_point1[0]), int(self.base_point1[1])]
        base_p2_rc = [int(self.base_point2[0]), int(self.base_point2[1])]
        cal_p1_rc = [int(self.max_slope_index1[0]), int(self.max_slope_index1[1])]
        cal_p2_rc = [int(self.max_slope_index2[0]), int(self.max_slope_index2[1])]

        # extra caldera detection QA
        caldera_center_rc = getattr(self, "caldera_center_rc", None)
        caldera_method = getattr(self, "caldera_detection_method", None)

        metrics = {
            "meta": {
                "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z"),
                "process_id": self.process_id,
                "original_file_name": os.environ.get("ORIGINAL_FILE_NAME") or self.meta.get("original_file_name"),
                "original_file_stem": os.environ.get("ORIGINAL_FILE_STEM") or self.meta.get("original_file_stem"),
                "input_dem_path": self.meta.get("input_dem_path"),
                "working_dem_path": self.meta.get("working_dem_path"),
                "crs": str(crs) if crs is not None else None,
                "res": list(res) if res is not None else None,
                "nodata": nodata,
                "params": {
                    "base_elevation_ratio": 0.05,
                    "caldera_level_ratio": 0.8,
                    "caldera_detection_method": caldera_method,
                    "caldera_center_rc": caldera_center_rc,
                }
            },

            "nodata_stats": dem_nodata_stats(self.dem, nodata=nodata),

            "morphometrics": {
                "A_base_m2": A_base_m2,
                "P_base_m": P_base,
                "D_base_m": D_base,
                "R_base_m": R_base,
                "R_eq_base_m": R_eq_base,

                "A_caldera_m2": A_caldera_m2,
                "P_caldera_m": P_caldera,
                "D_caldera_m": D_caldera,
                "R_caldera_m": R_caldera,
                "R_eq_caldera_m": R_eq_caldera,
            },

            "height_model": {
                "method": "p99_minus_p05_inside_base",
                "p99_minus_p05_m": h_used
            },

            "volume_model": {
                "edifice_model": "frustum_like",
                "caldera_model": "depth_integrated_rim_to_dem",
                "inputs_used": {
                    "h_max_m": h_used,
                    "R_base_m": R_base,
                    "R_caldera_m": R_caldera,
                    "caldera_depth": {
                        "rim_reference": {
                            "percentile": float(getattr(self, "caldera_rim_percentile", 90.0) or 90.0),
                            "z_rim_ref_m": getattr(self, "caldera_z_rim_ref_m", None),
                            "method": getattr(self, "caldera_rim_method", None),
                            "ring_offset_px": getattr(self, "caldera_rim_ring_offset_px", None),
                            "ring_width_px": getattr(self, "caldera_rim_ring_width_px", None),
                            "sample_count": getattr(self, "caldera_rim_sample_count", None),
                        },
                        "floor_reference": {
                            "percentile": float(getattr(self, "caldera_floor_percentile", 5.0) or 5.0),
                            "z_floor_ref_m": getattr(self, "caldera_z_floor_ref_m", None),
                            "method": "percentile_inside_mask",
                        },
                        "status": getattr(self, "caldera_status", None),
                        "reason": getattr(self, "caldera_reason", None),
                        "action": getattr(self, "caldera_action", None),
                        "depth_ref_m": getattr(self, "caldera_depth_ref_m", None),
                        "depth_ref_m_clamped": getattr(self, "caldera_depth_ref_m_clamped", None),
                        "mask_area_m2": getattr(self, "caldera_mask_area_m2", None),
                        "pixel_area_m2": getattr(self, "caldera_pixel_area_m2", None),
                        "fallback_used": bool(getattr(self, "caldera_fallback_used", False)),
                    }
                },
                "intermediate": {
                    "V_frustum_m3": V_frustum_m3
                }
            },

            "volumes": {
                "V_total_m3": V_frustum_m3,
                "V_caldera_m3": V_caldera_m3,
                "V_effective_m3": V_eff_m3,
            },

            "geometry": {
                "base_points": {"p1_rc": base_p1_rc, "p2_rc": base_p2_rc},
                "caldera_points": {"p1_rc": cal_p1_rc, "p2_rc": cal_p2_rc},
            },

            "derived": {
                "slenderness_H_over_Dbase": slenderness,
                "sanity_Abase_over_Dbase2": sanity_A_over_D2,
                "circularity_base": circularity_base,
                "circularity_caldera": circularity_caldera,
                "eq_height_V_over_Abase_m": eq_height_V_over_A,
                "ratio_vs_cone": cone_ratio,
            }
        }
        return metrics

    def _write_metrics_files(self, out_dir=None):
        out_dir = out_dir or self.out_dir
        os.makedirs(out_dir, exist_ok=True)

        metrics = self._build_metrics_dict()

        base = getattr(self, "metrics_basename", "metrics")
        json_path = os.path.join(out_dir, f"{base}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(_as_serializable(metrics), f, indent=2, ensure_ascii=False)

        csv_path = os.path.join(out_dir, f"{base}.csv")
        human_rows = metrics_to_human_rows(metrics)

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["section", "metric", "value", "unit"])
            for section, label, value, unit in human_rows:
                w.writerow([section, label, value, unit])

        print(f"[INFO] metrics written: {json_path}")
        print(f"[INFO] metrics written: {csv_path}")

    def export_metrics(self):
        out_dir = QFileDialog.getExistingDirectory(self, "Select folder to export metrics")
        if not out_dir:
            return

        try:
            self._write_metrics_files(out_dir=out_dir)

            base = getattr(self, "metrics_basename", "metrics")
            QMessageBox.information(
                self,
                "Success",
                f"{base}.json + {base}.csv exported to:\n{out_dir}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"An error occurred while exporting metrics: {e}")


# ========== Entry point ==========

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python CircularVolcano_Approx2.py <dem_file_path> [original_file_name]")
        sys.exit(1)

    dem_file_path = sys.argv[1]
    input_dem_path = sys.argv[1]
    original_file_name = sys.argv[2] if len(sys.argv) > 2 else "Unknown"

    pid = os.environ.get("PROCESS_ID")
    if pid:
        candidate = os.path.join(_script_dir(), "outputs", pid, "dem_working.tif")
        if os.path.exists(candidate):
            dem_file_path = candidate
            print(f"[DEBUG] Using dem_working from PROCESS_ID: {dem_file_path}")
        else:
            latest = _find_latest_dem_working()
            if latest:
                dem_file_path = latest
                print(f"[DEBUG] Using latest dem_working (PROCESS_ID missing file): {dem_file_path}")
    else:
        latest = _find_latest_dem_working()
        if latest:
            dem_file_path = latest
            print(f"[DEBUG] Using latest dem_working (no PROCESS_ID): {dem_file_path}")

    if not os.path.exists(dem_file_path):
        print(f"Error: File '{dem_file_path}' does not exist.")
        sys.exit(1)

    try:
        with rasterio.open(dem_file_path) as src:
            dem = src.read(1)
            transform = src.transform
            crs = src.crs
            res = src.res
            nodata = src.nodata
    except Exception as e:
        print(f"Error opening DEM file: {e}")
        sys.exit(1)

    meta = {
        "process_id": os.environ.get("PROCESS_ID"),
        "input_dem_path": sys.argv[1],
        "working_dem_path": dem_file_path,
        "crs": crs,
        "res": res,
        "nodata": nodata,
        "original_file_name": os.environ.get("ORIGINAL_FILE_NAME") or os.path.basename(input_dem_path),
        "original_file_stem": os.environ.get("ORIGINAL_FILE_STEM") or os.path.splitext(os.path.basename(input_dem_path))[0],
    }

    app = QApplication(sys.argv)
    ex = VolumeAnalysisApp(dem, transform, meta=meta, original_file_name=original_file_name)
    ex.showMaximized()
    sys.exit(app.exec_())