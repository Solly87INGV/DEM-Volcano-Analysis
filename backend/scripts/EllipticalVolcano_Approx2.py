#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# EllipticalVolcano_Approx2.py
# ——————————————————————————————————————————————————————————
# Approx2 (ellittico): calcoli invariati per l’EDIFICIO.
# Headless-friendly (allineato a Elliptical Approx1):
# - in HEADLESS=1: NO PyQt, MPL Agg
# - scrive SEMPRE in OUTPUTS_DIR/<PID>/ (fallback OUTPUTS_BASE, fallback ./outputs)
# - artifacts:
#     * metrics_ellip_a2.json
#     * metrics_ellip_a2.csv
#     * volume_results.json  (UI-ready + V2 meta)
#     * elliptical_approx2_overview.png
#     * final_doublet_base_vs_caldera.png
# - volume_results.json -> images contiene SOLO la final doublet (UI mostra la doppietta)
#
# ✅ CHANGE (requested, like Circular/Elliptical Approx1 “fixed”):
# - Caldera rim detection is now ROI + slope + morphology (center-biased)
#   to avoid grabbing the external flank ring when the level-based contour fails.
# - VOLUME LOGICS REMAIN UNCHANGED.
#
# ✅ PATCH (ported from Elliptical Approx1 “fixed”):
# - Improve rim component selection to avoid grabbing an outer flank ring:
#   * build an "inner ROI" (eroded ROI)
#   * prefer candidate component with smaller mean/p90 radius from center (peak-in-ROI)
#   * optional bonus using inner ROI overlap
#
# GUI:
# - mantiene UI e PDF, ma solo se NON headless

import sys
import os
import json
import time
import csv
import datetime
import math

import numpy as np

# ================= PROJ / EPSG FIX (Windows + PostGIS conflicts) =================
def _fix_proj_env():
    """
    PROJ/GDAL safety:
    - Windows: OK forzare PROJ_LIB a pyproj per evitare conflitti PostGIS/PROJ.
    - Linux/Docker: NON forzare pyproj. Se PROJ_LIB/PROJ_DATA sono settate, UNSET
      per lasciare che rasterio/GDAL usino i dati PROJ compatibili del container.
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
                    print(f"[PY VOL][DEBUG] PROJ_LIB forced to pyproj (Windows): {proj_dir}")
                else:
                    print("[PY VOL][WARN] pyproj datadir not found or invalid; PROJ_LIB not set.")
            except Exception as e:
                print(f"[PY VOL][WARN] Unable to force PROJ_LIB to pyproj (Windows): {e}")
            return

        prev_proj_lib = os.environ.pop("PROJ_LIB", None)
        prev_proj_data = os.environ.pop("PROJ_DATA", None)

        if prev_proj_lib is not None:
            print(f"[PY VOL][DEBUG] PROJ_LIB unset (non-Windows): {prev_proj_lib}")
        if prev_proj_data is not None:
            print(f"[PY VOL][DEBUG] PROJ_DATA unset (non-Windows): {prev_proj_data}")

        if prev_proj_lib is None and prev_proj_data is None:
            print("[PY VOL][DEBUG] PROJ env OK (non-Windows): PROJ_LIB/PROJ_DATA not set.")

    except Exception as e:
        print(f"[PY VOL][WARN] Unable to fix PROJ env: {e}")

_fix_proj_env()
# ================================================================================

def _is_headless() -> bool:
    v = str(os.environ.get("HEADLESS", "")).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

HEADLESS = _is_headless()

if HEADLESS:
    os.environ.setdefault("MPLBACKEND", "Agg")
    try:
        import matplotlib
        matplotlib.use("Agg")
    except Exception:
        pass

import rasterio
from scipy.ndimage import (
    sobel,
    gaussian_filter,
    binary_dilation,
    binary_closing,
    binary_fill_holes,
    binary_erosion,   # ✅ PATCH
    label,
)
from skimage import measure

# ---- PyQt5: import SOLO se NON headless ----
if not HEADLESS:
    from PyQt5 import QtWidgets, QtGui, QtCore
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QMessageBox, QFileDialog
    )
else:
    QtWidgets = QtGui = QtCore = None

    class QApplication:  # pragma: no cover
        def __init__(self, *args, **kwargs): pass
        def exec_(self): return 0

    class QMainWindow:  # pragma: no cover
        def __init__(self, *args, **kwargs): pass
        def setWindowTitle(self, *args, **kwargs): pass
        def setCentralWidget(self, *args, **kwargs): pass
        def showMaximized(self): pass

    class QWidget:  # pragma: no cover
        def __init__(self, *args, **kwargs): pass

    class QVBoxLayout:  # pragma: no cover
        def __init__(self, *args, **kwargs): pass
        def addWidget(self, *args, **kwargs): pass
        def addLayout(self, *args, **kwargs): pass

    class QHBoxLayout:  # pragma: no cover
        def __init__(self, *args, **kwargs): pass
        def addWidget(self, *args, **kwargs): pass

    class QPushButton:  # pragma: no cover
        def __init__(self, *args, **kwargs): pass
        def setFixedSize(self, *args, **kwargs): pass
        def clicked(self, *args, **kwargs): return self
        def connect(self, *args, **kwargs): pass

    class QMessageBox:  # pragma: no cover
        ActionRole = 0
        Ok = 0

        @staticmethod
        def critical(parent, title, msg):
            print(f"[PY VOL][ERROR] {title}: {msg}")

        @staticmethod
        def information(parent, title, msg):
            print(f"[PY VOL][INFO] {title}: {msg}")

        def setWindowTitle(self, *args, **kwargs): pass
        def setText(self, *args, **kwargs): pass
        def addButton(self, *args, **kwargs): return None
        def exec_(self): return None
        def clickedButton(self): return None

    class QFileDialog:  # pragma: no cover
        Options = object

        @staticmethod
        def getSaveFileName(*args, **kwargs):
            return ("", "")

        @staticmethod
        def getExistingDirectory(*args, **kwargs):
            return ""

# Matplotlib (canvas solo se GUI)
if not HEADLESS:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
else:
    FigureCanvas = None

from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib import gridspec
from mpl_toolkits.axes_grid1 import make_axes_locatable

import pdf_generator

METRICS_BASENAME = "metrics_ellip_a2"

# ========== helpers: env meta V2 ==========

def _env_str(name: str, default=None):
    v = os.environ.get(name)
    if v is None:
        return default
    v = str(v).strip()
    return v if v != "" else default

def _normalize_approx_type(v: str) -> str:
    v = (v or "").strip().lower()
    if v in ("approximation1", "approx1", "a1", "1"):
        return "approx1"
    if v in ("approximation2", "approx2", "a2", "2"):
        return "approx2"
    return v or "approx2"

def _v2_meta_from_env(default_module_key: str):
    """
    Standard V2 meta fields (aligned with other modules):
      - moduleKey
      - baseProfile
      - volumeType
      - approximationType
    """
    module_key = _env_str("MODULE_KEY", default_module_key)
    base_profile = _env_str("BASE_PROFILE", None)
    volume_type = _env_str("VOLUME_TYPE", "elliptical")
    approximation_type = _normalize_approx_type(_env_str("APPROXIMATION_TYPE", "approx2"))
    return {
        "moduleKey": module_key,
        "baseProfile": base_profile,
        "volumeType": volume_type,
        "approximationType": approximation_type,
    }

# ========== helpers: outputs ==========

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))

def _outputs_base_dir() -> str:
    """
    Express serve /outputs da OUTPUTS_DIR (es. /app/backend/outputs)
    Quindi qui priorità assoluta a OUTPUTS_DIR.
    """
    return (
        os.environ.get("OUTPUTS_DIR")
        or os.environ.get("OUTPUTS_BASE")
        or os.path.join(_script_dir(), "outputs")
    )

def _resolve_process_id() -> str:
    env_id = os.environ.get("PROCESS_ID")
    if env_id:
        return env_id
    return f"local_{int(time.time())}"

def _ensure_outputs_dir(process_id: str) -> str:
    out_dir = os.path.join(_outputs_base_dir(), process_id)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def _public_path(process_id: str, filename: str) -> str:
    return f"/outputs/{process_id}/{filename}"

# ------------------------------------------------------------
# dem_working selector (come vuoi tu: non usare originale se manca)
# ------------------------------------------------------------
def _choose_working_dem(process_id: str):
    """
    Returns:
      - path to dem_working.tif, or None if not found.
    NOTE: no Python 3.10 union type here (compat with py<3.10).
    """
    base_outputs = _outputs_base_dir()
    pid = os.environ.get("PROCESS_ID") or process_id

    # 1) outputs/<PID>/dem_working.tif
    candidate = os.path.join(base_outputs, pid, "dem_working.tif")
    if os.path.exists(candidate):
        print(f"[PY VOL {pid}] [DEBUG] Using dem_working from PROCESS_ID/newest: {candidate}")
        return candidate

    # 2) fallback: dem_working più recente in outputs/*
    if os.path.isdir(base_outputs):
        candidates = []
        for name in os.listdir(base_outputs):
            p = os.path.join(base_outputs, name, "dem_working.tif")
            if os.path.exists(p):
                candidates.append(p)
        if candidates:
            candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            chosen = candidates[0]
            print(f"[PY VOL {pid}] [DEBUG] Using dem_working from PROCESS_ID/newest: {chosen}")
            return chosen

    return None

# ========== Analysis (EDIFICE invariato) ==========

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

# ========== Mask helpers (also used by caldera depth) ==========

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

def _centroid_of_mask(mask: np.ndarray):
    rr, cc = np.nonzero(mask)
    if rr.size == 0:
        return None
    return (float(np.mean(rr)), float(np.mean(cc)))

def _peak_in_roi(dem: np.ndarray, roi: np.ndarray):
    """
    Center estimator used for center-biased selection:
    pick maximum elevation inside ROI; fallback to ROI centroid.
    """
    try:
        vals = np.where(roi, dem.astype(float), -np.inf)
        if not np.isfinite(vals).any():
            return _centroid_of_mask(roi)
        idx = int(np.nanargmax(vals))
        r, c = np.unravel_index(idx, dem.shape)
        return (float(r), float(c))
    except Exception:
        return _centroid_of_mask(roi)

# ========== ✅ Caldera rim detection (robust: ROI+slope+morphology) ==========

def find_caldera_contour_morphological(
    dem: np.ndarray,
    base_contour: np.ndarray,
    slope: np.ndarray = None,
    nodata=None,
    roi_dilate_px: int = 6,
    smooth_sigma: float = 1.0,
    slope_q: float = 88.0,
    min_component_px: int = 500,
    closing_iterations: int = 2,
    extra_dilate_px: int = 0,
    selection_mode: str = "center_biased",
    max_area_frac: float = 0.45,
) -> tuple:
    """
    Detect caldera rim as a slope-defined ring inside an ROI derived from base contour.
    Returns: (caldera_contour, debug_dict)
    """
    if dem is None or dem.size == 0:
        raise ValueError("DEM is empty. Cannot detect caldera rim.")
    if base_contour is None or len(base_contour) < 3:
        raise ValueError("Base contour missing/too small. Cannot detect caldera rim.")

    demf = dem.astype(float)

    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass

    roi = contour_to_mask(base_contour, demf.shape)
    roi_dilate_px = int(max(0, roi_dilate_px))
    if roi_dilate_px > 0:
        roi = binary_dilation(roi, iterations=roi_dilate_px)
    roi = roi & valid

    roi_px = int(np.sum(roi))
    if roi_px < max(50, int(min_component_px * 0.5)):
        raise ValueError(f"ROI too small after dilation/valid masking (roi_px={roi_px}).")

    # ✅ PATCH: inner ROI (eroded) for overlap scoring
    inner_buffer_px = 12  # tune 10-20 if needed
    try:
        roi_inner = binary_erosion(roi, iterations=int(inner_buffer_px)) if inner_buffer_px > 0 else roi
        if int(np.sum(roi_inner)) < 100:
            roi_inner = roi
    except Exception:
        roi_inner = roi

    if slope is None:
        slope = calculate_slope(demf)

    slopef = slope.astype(float)
    if smooth_sigma is not None and float(smooth_sigma) > 0:
        slopef = gaussian_filter(slopef, sigma=float(smooth_sigma))

    roi_slopes = slopef[roi]
    roi_slopes = roi_slopes[np.isfinite(roi_slopes)]
    if roi_slopes.size == 0:
        raise ValueError("No finite slope values inside ROI.")

    thr = float(np.percentile(roi_slopes, float(slope_q)))
    candidates = (slopef >= thr) & roi

    closing_iterations = int(max(0, closing_iterations))
    if closing_iterations > 0:
        candidates = binary_closing(candidates, iterations=closing_iterations)

    candidates = binary_fill_holes(candidates)

    extra_dilate_px = int(max(0, extra_dilate_px))
    if extra_dilate_px > 0:
        candidates = binary_dilation(candidates, iterations=extra_dilate_px)

    candidates = candidates & roi

    cand_px = int(np.sum(candidates))
    if cand_px == 0:
        raise ValueError("Candidate rim mask is empty after morphological steps.")

    lbl, ncomp = label(candidates)
    if ncomp <= 0:
        raise ValueError("No connected components found in candidate mask.")

    sizes = []
    comp_ids = list(range(1, ncomp + 1))
    for cid in comp_ids:
        sizes.append(int(np.sum(lbl == cid)))

    min_component_px = int(max(1, min_component_px))
    eligible = []
    max_area_frac = float(max_area_frac)
    max_area_px = int(max_area_frac * roi_px) if (max_area_frac is not None and max_area_frac > 0) else None

    for cid, sz in zip(comp_ids, sizes):
        if sz < min_component_px:
            continue
        if max_area_px is not None and sz > max_area_px:
            continue
        eligible.append((cid, sz))

    if not eligible:
        raise ValueError(
            "No connected component meets constraints (min_component_px / max_area_frac). "
            f"ncomp={ncomp}, roi_px={roi_px}, sizes={sizes[:10]}{'...' if len(sizes)>10 else ''}"
        )

    selection_mode = str(selection_mode or "center_biased").strip().lower()
    selected_component_id = None
    selected_component_px = None
    center_used = None

    if selection_mode == "largest":
        selected_component_id, selected_component_px = max(eligible, key=lambda t: t[1])
    else:
        center = _peak_in_roi(demf, roi)
        if center is None:
            center = _centroid_of_mask(roi)

        if center is None:
            selected_component_id, selected_component_px = max(eligible, key=lambda t: t[1])
        else:
            center_r, center_c = center
            center_used = {"row": float(center_r), "col": float(center_c), "method": "peak_in_roi"}

            # ✅ PATCH: prefer INNER ring by mean/p90 radius around center (+ inner ROI overlap bonus)
            best = None
            for cid, sz in eligible:
                m = (lbl == cid)

                rr, cc = np.nonzero(m)
                if rr.size == 0:
                    continue

                rad = np.hypot(rr - center_r, cc - center_c)
                mean_radius_px = float(np.mean(rad))
                p90_radius_px = float(np.percentile(rad, 90))

                cent = _centroid_of_mask(m)
                if cent is None:
                    continue
                dr = cent[0] - center_r
                dc = cent[1] - center_c
                d2 = float(dr * dr + dc * dc)

                try:
                    inner_overlap = float(np.sum(m & roi_inner)) / float(np.sum(m) + 1e-9)
                except Exception:
                    inner_overlap = 0.0

                key = (
                    mean_radius_px - 20.0 * inner_overlap,
                    p90_radius_px,
                    d2,
                    -sz
                )

                if best is None or key < best[0]:
                    best = (key, cid, sz, cent, inner_overlap, mean_radius_px, p90_radius_px)

            if best is None:
                selected_component_id, selected_component_px = max(eligible, key=lambda t: t[1])
            else:
                selected_component_id = int(best[1])
                selected_component_px = int(best[2])
                center_used["selected_component_centroid"] = {"row": float(best[3][0]), "col": float(best[3][1])}
                center_used["selected_component_inner_overlap"] = float(best[4])
                center_used["selected_component_mean_radius_px"] = float(best[5])
                center_used["selected_component_p90_radius_px"] = float(best[6])

    best_mask = (lbl == selected_component_id)

    contours = measure.find_contours(best_mask.astype(np.uint8), 0.5)
    if not contours:
        raise ValueError("Selected component exists, but contour extraction failed.")

    caldera_contour = max(contours, key=len)

    debug = {
        "method": "morphological_slope_roi",
        "selection_mode": selection_mode,
        "max_area_frac": float(max_area_frac),
        "roi_dilate_px": int(roi_dilate_px),
        "smooth_sigma": float(smooth_sigma),
        "slope_q": float(slope_q),
        "slope_threshold": float(thr),
        "closing_iterations": int(closing_iterations),
        "extra_dilate_px": int(extra_dilate_px),
        "min_component_px": int(min_component_px),
        "roi_px": int(roi_px),
        "inner_buffer_px": int(inner_buffer_px),
        "inner_roi_px": int(np.sum(roi_inner)) if roi_inner is not None else None,
        "candidate_px": int(cand_px),
        "n_components": int(ncomp),
        "all_component_sizes_px": sizes,
        "eligible_component_sizes_px": [int(sz) for (_, sz) in eligible],
        "selected_component_id": int(selected_component_id),
        "selected_component_px": int(selected_component_px) if selected_component_px is not None else None,
        "center": center_used,
        "contour_len": int(len(caldera_contour)),
    }

    return caldera_contour, debug

# ========== Caldera depth helpers (rim–floor from DEM) ==========

def pixel_area_m2_from_transform(transform):
    try:
        return float(abs(transform.a * transform.e - transform.b * transform.d))
    except Exception:
        try:
            return float(abs(transform.a * transform.e))
        except Exception:
            return 0.0

def outside_ring_mask(mask: np.ndarray, offset_px: int = 1, width_px: int = 3) -> np.ndarray:
    if mask is None or mask.size == 0:
        return np.zeros_like(mask, dtype=bool)
    if offset_px < 0:
        offset_px = 0
    if width_px < 1:
        width_px = 1

    inner = binary_dilation(mask, iterations=offset_px) if offset_px > 0 else mask
    outer = binary_dilation(mask, iterations=offset_px + width_px)
    ring = outer & (~inner)
    return ring

def caldera_depth_from_dem(
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
    caldera_mask_area_m2 = float(np.sum(caldera_mask & valid)) * float(px_area)

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

    status = None
    reason = None
    action = None

    if z_rim_ref_m is None or z_floor_ref_m is None:
        status = "non_depressive_or_complex"
        reason = "insufficient_samples"
        action = "not_computed"
    elif depth_ref_m <= 0:
        status = "non_depressive_or_complex"
        reason = "rim_below_floor"
        action = "not_computed"
    else:
        status = "depressive"
        action = "computed"

    return {
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
        "status": status,
        "reason": reason,
        "action": action,
    }

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
    ("meta.timestamp_utc", "Run timestamp (UTC)"),
    ("meta.process_id", "Run ID (process_id)"),
    ("meta.moduleKey", "moduleKey"),
    ("meta.baseProfile", "baseProfile"),
    ("meta.volumeType", "volumeType"),
    ("meta.approximationType", "approximationType"),

    ("meta.original_file_name", "Original input filename"),
    ("meta.original_file_stem", "Original input filename (stem)"),
    ("meta.input_dem_path", "Input DEM path"),
    ("meta.working_dem_path", "Working DEM path"),
    ("meta.crs", "Working DEM CRS (EPSG/WKT)"),
    ("meta.res", "Pixel resolution (m) [x,y]"),
    ("meta.nodata", "NoData value"),

    ("morphometrics.A_base_m2", "Base area (m²)"),
    ("morphometrics.P_base_m", "Base perimeter (m)"),
    ("morphometrics.D_base_m", "Base diameter (m) [opposite points]"),
    ("morphometrics.R_base_m", "Base radius used in volume (m)"),
    ("morphometrics.R_eq_base_m", "Equivalent base radius from area (m)"),

    ("morphometrics.A_caldera_m2", "Caldera area (m²)"),
    ("morphometrics.P_caldera_m", "Caldera perimeter (m)"),
    ("morphometrics.D_caldera_m", "Caldera diameter (m) [opposite points]"),
    ("morphometrics.R_caldera_m", "Caldera radius used in volume (m)"),
    ("morphometrics.R_eq_caldera_m", "Equivalent caldera radius from area (m)"),

    ("morphometrics.h_max_m", "Height used by model (m)"),

    ("volumes.V_total_m3", "Total edifice volume (m³)"),
    ("volumes.V_caldera_m3", "Caldera volume (m³)"),
    ("volumes.V_effective_m3", "Effective edifice volume (m³)"),

    ("derived.slenderness_H_over_Dbase", "Slenderness H/Dbase (unitless)"),
    ("derived.circularity_base", "Circularity base (unitless)"),
    ("derived.circularity_caldera", "Circularity caldera (unitless)"),
    ("derived.eq_height_V_over_Abase_m", "Equivalent height V/Abase (m)"),
    ("derived.ratio_vs_cone", "Ratio vs perfect cone (unitless)"),

    ("caldera.status", "Caldera status"),
    ("caldera.depth_ref_m", "Caldera depth ref (m)"),
    ("caldera.rim_method", "Caldera rim method"),
    ("caldera.fallback_used", "Caldera fallback used"),
]

def metrics_to_human_rows(metrics: dict):
    rows = []
    for path, label in HUMAN_FIELDS:
        v = _get_by_path(metrics, path)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        rows.append((label, v))
    return rows

# ========== Main App ==========

class VolumeAnalysisApp(QMainWindow):
    def __init__(self, dem, transform, meta=None, original_file_name="Unknown"):
        super().__init__()
        self.dem = dem
        self.transform = transform

        self.meta = meta or {}
        self.process_id = self.meta.get("process_id") or _resolve_process_id()
        self.out_dir = _ensure_outputs_dir(self.process_id)
        self.original_file_name = original_file_name
        self.metrics_basename = METRICS_BASENAME

        if hasattr(self, "setWindowTitle"):
            self.setWindowTitle('Elliptical Volcano Volume Analysis - Approximation 2')

        # compute first
        self.calculate_results()

        # GUI only if not headless
        if not HEADLESS:
            self.initUI()
            try:
                self._save_overview_png_gui()
            except Exception as e:
                print(f"[PY VOL {self.process_id}] [WARN] overview saving failed (GUI): {e}")
        else:
            print(f"[PY VOL {self.process_id}] [DEBUG] HEADLESS=1 -> GUI disabled (no Qt windows).")

        # ALWAYS write artifacts for UI/backend
        self._write_all_artifacts()

    # ---------------- GUI ----------------
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

    def update_display(self):
        self.figure.clear()
        fig = self.figure

        fig.set_constrained_layout_pads(w_pad=0.12, h_pad=0.02, wspace=0.40, hspace=0.60)
        gs = gridspec.GridSpec(nrows=3, ncols=3, height_ratios=[4, 1, 1.5], figure=fig, wspace=0.4, hspace=0.6)

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
        cald, = ax3.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label="Caldera Contour")
        ax3.set_title("Opposite Maximum Slope Points on the Caldera", fontsize=14, pad=20, y=1.02)
        ax3.axis('on')

        l2 = fig.add_subplot(gs[1, 1]); l2.axis('off')
        l2.legend([p1, p2, cplot], ['Base 1', 'Base 2', 'Base Contour'],
                  loc='center', frameon=True, edgecolor='black', facecolor='lightgray', ncol=3)

        l3 = fig.add_subplot(gs[1, 2]); l3.axis('off')
        l3.legend([s1, s2, cald], ['Max Slope 1', 'Max Slope 2', 'Caldera Contour'],
                  loc='center', frameon=True, edgecolor='black', facecolor='lightgray', ncol=3)

        fig.add_subplot(gs[1, 0]).axis('off')
        d2 = fig.add_subplot(gs[2, 1]); d2.axis('off')
        d2.text(0.5, 1.35, self.description_base, fontsize=10, ha='center', va='center',
                bbox=dict(boxstyle="round,pad=0.5", edgecolor="black", facecolor="white"),
                wrap=True, transform=d2.transAxes)
        d3 = fig.add_subplot(gs[2, 2]); d3.axis('off')
        d3.text(0.5, 1.5, self.description_slope, fontsize=10, ha='center', va='center',
                bbox=dict(boxstyle="round,pad=0.5", edgecolor="black", facecolor="white"),
                wrap=True, transform=d3.transAxes)

        self.canvas.draw()

    # ---------------- core results ----------------
    def calculate_results(self):
        nodata = self.meta.get("nodata", None)

        self.base_contour = find_lowest_base_contour(self.dem, base_elevation_ratio=0.05)
        self.base_point1, self.base_point2 = find_opposite_base_points(self.base_contour)

        self.distance_meters_base = distance_between_points(
            self.base_point1[0], self.base_point1[1],
            self.base_point2[0], self.base_point2[1],
            self.transform
        )
        self.distance_base_km = self.distance_meters_base * 1e-3

        self.slope = calculate_slope(self.dem)

        # ✅ FIXED caldera rim (ROI + slope + morphology) — now with robust inner-ring selection
        self.caldera_contour, self.caldera_debug = find_caldera_contour_morphological(
            dem=self.dem,
            base_contour=self.base_contour,
            slope=self.slope,
            nodata=nodata,
            roi_dilate_px=6,
            smooth_sigma=1.0,
            slope_q=88.0,
            min_component_px=500,
            closing_iterations=2,
            extra_dilate_px=0,
            selection_mode="center_biased",
            max_area_frac=0.45,
        )

        self.max_slope_index1, self.max_slope_index2 = find_opposite_slope_points(self.slope, self.caldera_contour)

        self.distance_meters_caldera = distance_between_points(
            self.max_slope_index1[0], self.max_slope_index1[1],
            self.max_slope_index2[0], self.max_slope_index2[1],
            self.transform
        )
        self.distance_caldera_km = self.distance_meters_caldera * 1e-3

        # areas (km² for text)
        self.area_base = calculate_area(self.base_contour, self.transform) * 1e-6
        self.area_caldera = calculate_area(self.caldera_contour, self.transform) * 1e-6

        # edifice volume (INVARIATO)
        z = self.dem.astype(float)
        valid = np.isfinite(z)
        if nodata is not None:
            try:
                valid = valid & (z != float(nodata))
            except Exception:
                pass
        z = z[valid]
        if z.size == 0:
            self.h_max = 0.0
        else:
            self.h_max = float(np.percentile(z, 99) - np.percentile(z, 5))

        self.R1 = self.distance_meters_base / 2.0
        self.R2 = self.distance_meters_caldera / 2.0
        self.v = (1/3) * np.pi * self.h_max * (self.R1**2 + self.R2**2 + self.R1 * self.R2)
        self.v_km3 = self.v * 1e-9

        # caldera depth from DEM (rim–floor) + fallback
        cd = caldera_depth_from_dem(
            dem=self.dem,
            caldera_contour=self.caldera_contour,
            transform=self.transform,
            nodata=nodata,
            rim_percentile=90.0,
            floor_percentile=5.0,
            rim_ring_offset_px=1,
            rim_ring_width_px=3
        )

        depth_raw = float(cd.get("depth_ref_m", 0.0) or 0.0)
        depth_clamped = float(cd.get("depth_ref_m_clamped", 0.0) or 0.0)

        self.caldera_fallback_used = False
        if depth_raw <= 0:
            cd_fb = caldera_depth_from_dem(
                dem=self.dem,
                caldera_contour=self.caldera_contour,
                transform=self.transform,
                nodata=nodata,
                rim_percentile=98.0,
                floor_percentile=2.0,
                rim_ring_offset_px=1,
                rim_ring_width_px=3
            )
            depth_fb_raw = float(cd_fb.get("depth_ref_m", 0.0) or 0.0)
            depth_fb_clamped = float(cd_fb.get("depth_ref_m_clamped", 0.0) or 0.0)

            if depth_fb_raw > depth_raw:
                cd = cd_fb
                depth_raw = depth_fb_raw
                depth_clamped = depth_fb_clamped
                self.caldera_fallback_used = True

        self.caldera_status = cd.get("status", None)
        self.caldera_reason = cd.get("reason", None)
        self.caldera_action = cd.get("action", None)

        # Approx2 caldera volume: cylinder with elliptical base => V = A_caldera * depth (INVARIATO)
        A_caldera_m2 = float(self.area_caldera) * 1e6
        if self.caldera_status == "depressive" and depth_clamped > 0 and A_caldera_m2 > 0:
            V_caldera_m3 = float(A_caldera_m2 * depth_clamped)
        else:
            V_caldera_m3 = 0.0

        # ✅ clamp
        V_effective_m3 = float(max(0.0, self.v - V_caldera_m3))

        self.v_caldera = float(V_caldera_m3) * 1e-9
        self.v_volcano = float(V_effective_m3) * 1e-9

        # QA fields
        self.caldera_z_rim_ref_m = cd.get("z_rim_ref_m", None)
        self.caldera_z_floor_ref_m = cd.get("z_floor_ref_m", None)
        self.caldera_depth_ref_m = cd.get("depth_ref_m", None)
        self.caldera_depth_ref_m_clamped = cd.get("depth_ref_m_clamped", None)
        self.caldera_rim_method = cd.get("rim_method", None)
        self.caldera_rim_ring_offset_px = cd.get("rim_ring_offset_px", None)
        self.caldera_rim_ring_width_px = cd.get("rim_ring_width_px", None)
        self.caldera_rim_sample_count = cd.get("rim_sample_count", None)
        self.caldera_rim_percentile = cd.get("rim_percentile", None)
        self.caldera_floor_percentile = cd.get("floor_percentile", None)
        self.caldera_pixel_area_m2 = cd.get("pixel_area_m2", None)
        self.caldera_mask_area_m2 = cd.get("caldera_mask_area_m2", None)

        # descriptions
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

        # lines
        if self.caldera_status == "depressive":
            caldera_line = f"Caldera volume (cylinder): {self.v_caldera:.3e} km³"
            depth_line = f"Caldera depth (DEM rim–floor): {float(depth_clamped)*1e-3:.2f} km"
        else:
            reason = self.caldera_reason or "complex/non-depressive"
            caldera_line = f"Caldera volume: N/A ({reason})"
            depth_line = "Caldera depth (DEM rim–floor): N/A"

        self.results_text = (
            f"Base area of the volcano: {self.area_base:.2f} km²\n"
            f"Base span (Distance between opposite points of the base): {self.distance_base_km:.2f} km\n"
            f"Caldera area of the volcano: {self.area_caldera:.2f} km²\n"
            f"Caldera span (Distance between opposite points of the caldera): {self.distance_caldera_km:.2f} km\n"
            f"{depth_line}\n"
            f"Total volume of the volcanic edifice (elliptical frustum): {self.v_km3:.2f} km³\n"
            f"{caldera_line}\n"
            f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
        )

        self.results_list = [
            f"Base area of the volcano: {self.area_base:.2f} km²",
            f"Base span (Distance between opposite points of the base): {self.distance_base_km:.2f} km",
            f"Caldera area of the volcano: {self.area_caldera:.2f} km²",
            f"Caldera span (Distance between opposite points of the caldera): {self.distance_caldera_km:.2f} km",
            depth_line,
            f"Total volume of the volcanic edifice (elliptical frustum): {self.v_km3:.2f} km³",
            caldera_line,
            f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
        ]

    # ---------------- overview ----------------
    def _save_overview_png_gui(self):
        out_png = os.path.join(self.out_dir, "elliptical_approx2_overview.png")
        self.figure.savefig(out_png, dpi=150)
        print(f"[PY VOL {self.process_id}] saved {out_png}")

    def _save_overview_png_headless(self):
        """
        In headless non abbiamo self.figure. Genero un overview semplice (DEM + base/caldera overlays).
        """
        out_png = os.path.join(self.out_dir, "elliptical_approx2_overview.png")
        fig = plt.figure(figsize=(10.5, 7.2))
        ax = fig.add_subplot(1, 1, 1)
        im = ax.imshow(self.dem, cmap="terrain", origin="upper")
        ax.plot(self.base_contour[:, 1], self.base_contour[:, 0], "w-", linewidth=1)
        ax.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], "b-", linewidth=1)
        ax.set_title("Elliptical Approx2 Overview (DEM + Base/Caldera)", fontsize=12)
        ax.set_aspect("equal", adjustable="box")
        div = make_axes_locatable(ax)
        cax = div.append_axes("right", size="4.6%", pad=0.10)
        cbar = fig.colorbar(im, cax=cax)
        cbar.set_label("Elevation (m)", rotation=90)
        fig.savefig(out_png, dpi=150)
        plt.close(fig)
        print(f"[PY VOL {self.process_id}] saved {out_png}")

    # ---------------- final doublet ----------------
    def _save_final_doublet_png(self, out_path):
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

        FIG_W, FIG_H = 14.5, 5.5
        fig = plt.figure(figsize=(FIG_W, FIG_H))
        gs = gridspec.GridSpec(1, 3, figure=fig, width_ratios=[1.0, 0.08, 1.0], wspace=0.15)

        ax1 = fig.add_subplot(gs[0, 0])
        im1 = ax1.imshow(self.dem, cmap='terrain', origin='upper', interpolation='nearest', resample=False)
        pc, = ax1.plot(self.base_contour[:, 1], self.base_contour[:, 0], 'w-', linewidth=1, label='Base Contour')
        p1, = ax1.plot(self.base_point1[1], self.base_point1[0], 'ro', markersize=8, label='Base 1')
        p2, = ax1.plot(self.base_point2[1], self.base_point2[0], 'yo', markersize=8, label='Base 2')
        ax1.set_title("Opposite Points of the Volcano Base", pad=8, fontsize=12)
        ax1.set_aspect('equal', adjustable='box')
        div1 = make_axes_locatable(ax1)
        cax1 = div1.append_axes("right", size="4.6%", pad=0.10)
        cbar1 = fig.colorbar(im1, cax=cax1); cbar1.set_label("Elevation (m)", rotation=90)

        leg1 = ax1.legend(handles=[p1, p2, pc], labels=['Base 1', 'Base 2', 'Base Contour'],
                          loc='upper right', frameon=True)
        leg1.get_frame().set_alpha(0.7)
        leg1.get_frame().set_facecolor('white')
        leg1.get_frame().set_edgecolor('black')

        fig.add_subplot(gs[0, 1]).axis('off')

        ax2 = fig.add_subplot(gs[0, 2])
        im2 = ax2.imshow(self.dem, cmap='terrain', origin='upper', interpolation='nearest', resample=False)
        cc, = ax2.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label='Caldera Contour')
        s1, = ax2.plot(self.max_slope_index1[1], self.max_slope_index1[0], 'ro', markersize=8, label='Max Slope 1')
        s2, = ax2.plot(self.max_slope_index2[1], self.max_slope_index2[0], 'yo', markersize=8, label='Max Slope 2')
        ax2.set_title("Opposite Maximum Slope Points on the Caldera", pad=8, fontsize=12)
        ax2.set_aspect('equal', adjustable='box')
        div2 = make_axes_locatable(ax2)
        cax2 = div2.append_axes("right", size="4.6%", pad=0.10)
        cbar2 = fig.colorbar(im2, cax=cax2); cbar2.set_label("Elevation (m)", rotation=90)

        leg2 = ax2.legend(handles=[s1, s2, cc], labels=['Max Slope 1', 'Max Slope 2', 'Caldera Contour'],
                          loc='upper right', frameon=True)
        leg2.get_frame().set_alpha(0.7)
        leg2.get_frame().set_facecolor('white')
        leg2.get_frame().set_edgecolor('black')

        fig.savefig(out_path, dpi=170)
        plt.close(fig)

        if not os.path.exists(out_path):
            raise RuntimeError(f"Doublet not saved: {out_path}")

        print(f"[PY VOL {self.process_id}] saved {out_path}")

    # ---------------- metrics ----------------
    def _build_metrics_dict(self) -> dict:
        crs = self.meta.get("crs")
        res = self.meta.get("res")
        nodata = self.meta.get("nodata")

        v2 = _v2_meta_from_env(default_module_key="elliptical_approx2")

        A_base_m2 = float(self.area_base) * 1e6
        A_caldera_m2 = float(self.area_caldera) * 1e6

        P_base = float(contour_perimeter_m(self.base_contour, self.transform))
        P_caldera = float(contour_perimeter_m(self.caldera_contour, self.transform))

        D_base = float(self.distance_meters_base)
        D_caldera = float(self.distance_meters_caldera)

        R_base = float(self.R1)
        R_caldera = float(self.R2)

        R_eq_base = math.sqrt(A_base_m2 / math.pi) if A_base_m2 > 0 else 0.0
        R_eq_caldera = math.sqrt(A_caldera_m2 / math.pi) if A_caldera_m2 > 0 else 0.0

        h_used = float(self.h_max)

        V_total_m3 = float(self.v)
        V_caldera_m3 = float(self.v_caldera) * 1e9
        V_eff_m3 = float(self.v_volcano) * 1e9

        circularity_base = (4 * math.pi * A_base_m2 / (P_base ** 2)) if (A_base_m2 > 0 and P_base > 0) else None
        circularity_caldera = (4 * math.pi * A_caldera_m2 / (P_caldera ** 2)) if (A_caldera_m2 > 0 and P_caldera > 0) else None

        slenderness = (h_used / D_base) if D_base > 0 else None
        eq_height_V_over_A = (V_total_m3 / A_base_m2) if A_base_m2 > 0 else None

        denom_cone = (1.0 / 3.0) * A_base_m2 * h_used
        cone_ratio = (V_total_m3 / denom_cone) if (denom_cone and denom_cone > 0) else None

        metrics = {
            "meta": {
                "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                "process_id": self.process_id,

                # ✅ V2 meta
                "moduleKey": v2["moduleKey"],
                "baseProfile": v2["baseProfile"],
                "volumeType": v2["volumeType"],
                "approximationType": v2["approximationType"],

                "original_file_name": os.environ.get("ORIGINAL_FILE_NAME") or self.meta.get("original_file_name"),
                "original_file_stem": os.environ.get("ORIGINAL_FILE_STEM") or self.meta.get("original_file_stem"),
                "input_dem_path": self.meta.get("input_dem_path"),
                "working_dem_path": self.meta.get("working_dem_path"),
                "crs": str(crs) if crs is not None else None,
                "res": list(res) if res is not None else None,
                "nodata": nodata,
                "params": {
                    "base_elevation_ratio": 0.05,
                    "caldera_rim_detection": "morphological_slope_roi_center_biased_with_radius_and_inner_roi_overlap",
                    "roi_dilate_px": 6,
                    "smooth_sigma": 1.0,
                    "slope_q": 88.0,
                    "min_component_px": 500,
                    "max_area_frac": 0.45,
                    "caldera_depth_method": "dem_rim_floor_percentiles_with_fallback",
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

                "h_max_m": h_used,
            },

            "volume_model": {
                "edifice_model": "frustum_like",
                "caldera_model": "cylinder_elliptical_base_depth_dem_rim_floor",
            },

            "volumes": {
                "V_total_m3": V_total_m3,
                "V_caldera_m3": V_caldera_m3,
                "V_effective_m3": V_eff_m3,
            },

            "caldera": {
                "status": getattr(self, "caldera_status", None),
                "reason": getattr(self, "caldera_reason", None),
                "action": getattr(self, "caldera_action", None),
                "fallback_used": bool(getattr(self, "caldera_fallback_used", False)),
                "z_rim_ref_m": getattr(self, "caldera_z_rim_ref_m", None),
                "z_floor_ref_m": getattr(self, "caldera_z_floor_ref_m", None),
                "depth_ref_m": getattr(self, "caldera_depth_ref_m", None),
                "depth_ref_m_clamped": getattr(self, "caldera_depth_ref_m_clamped", None),
                "rim_method": getattr(self, "caldera_rim_method", None),
                "rim_ring_offset_px": getattr(self, "caldera_rim_ring_offset_px", None),
                "rim_ring_width_px": getattr(self, "caldera_rim_ring_width_px", None),
                "rim_sample_count": getattr(self, "caldera_rim_sample_count", None),
                "rim_percentile": getattr(self, "caldera_rim_percentile", None),
                "floor_percentile": getattr(self, "caldera_floor_percentile", None),
                "pixel_area_m2": getattr(self, "caldera_pixel_area_m2", None),
                "caldera_mask_area_m2": getattr(self, "caldera_mask_area_m2", None),
            },

            "derived": {
                "slenderness_H_over_Dbase": slenderness,
                "circularity_base": circularity_base,
                "circularity_caldera": circularity_caldera,
                "eq_height_V_over_Abase_m": eq_height_V_over_A,
                "ratio_vs_cone": cone_ratio,
            }
        }

        # ✅ keep debug (does not change volumes)
        if hasattr(self, "caldera_debug") and isinstance(self.caldera_debug, dict):
            metrics["meta"]["caldera_debug"] = self.caldera_debug

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
            w.writerow(["metric", "value"])
            for label, value in human_rows:
                w.writerow([label, value])

        print(f"[PY VOL][INFO] metrics written: {json_path}")
        print(f"[PY VOL][INFO] metrics written: {csv_path}")

    # ---------------- UI payload ----------------
    def _write_volume_results_json(self):
        """
        File richiesto dalla UI:
          /outputs/<PID>/volume_results.json

        images: SOLO la final doublet (UI mostra la doppietta).
        """
        v2 = _v2_meta_from_env(default_module_key="elliptical_approx2")

        pixel_size_m = None
        res = self.meta.get("res")
        try:
            if res and isinstance(res, (list, tuple)) and len(res) >= 1:
                pixel_size_m = float(res[0])
        except Exception:
            pixel_size_m = None

        caldera_km3 = float(self.v_caldera) if getattr(self, "caldera_status", None) == "depressive" else None

        payload = {
            "processId": self.process_id,
            "status": "completed",

            # ✅ V2 fields
            "moduleKey": v2["moduleKey"],
            "baseProfile": v2["baseProfile"],
            "volumeType": v2["volumeType"],
            "approximationType": v2["approximationType"],

            "summaryText": self.results_text,
            "result": {
                "base_area_km2": float(self.area_base),
                "base_width_km": float(self.distance_base_km),
                "caldera_area_km2": float(self.area_caldera),
                "caldera_width_km": float(self.distance_caldera_km),
                "total_volume_km3": float(self.v_km3),
                "caldera_volume_km3": caldera_km3,
                "effective_volume_km3": float(self.v_volcano),
                "h_max_m": float(getattr(self, "h_max", 0.0)),
                "pixel_size_m": pixel_size_m,
            },
            "images": ["final_doublet_base_vs_caldera.png"],
            "links": {
                "metrics_json": _public_path(self.process_id, f"{self.metrics_basename}.json"),
                "metrics_csv": _public_path(self.process_id, f"{self.metrics_basename}.csv"),
                "final_doublet": _public_path(self.process_id, "final_doublet_base_vs_caldera.png"),
                "overview": _public_path(self.process_id, "elliptical_approx2_overview.png"),
            }
        }

        out_path = os.path.join(self.out_dir, "volume_results.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        print(f"[PY VOL][INFO] volume_results written: {out_path}")

        if HEADLESS:
            try:
                print(json.dumps(payload, ensure_ascii=False), flush=True)
            except Exception:
                pass

    def _write_all_artifacts(self):
        # 1) overview (sempre)
        try:
            if HEADLESS:
                self._save_overview_png_headless()
            else:
                self._save_overview_png_gui()
        except Exception as e:
            print(f"[PY VOL {self.process_id}] [WARN] Could not save overview: {e}")

        # 2) metrics
        try:
            self._write_metrics_files()
        except Exception as e:
            print(f"[PY VOL {self.process_id}] [WARN] metrics export failed: {e}")

        # 3) final doublet (sempre, in outputs/<PID>/)
        try:
            doublet_path = os.path.join(self.out_dir, "final_doublet_base_vs_caldera.png")
            self._save_final_doublet_png(doublet_path)
        except Exception as e:
            print(f"[PY VOL {self.process_id}] [WARN] Could not save final doublet: {e}")

        # 4) volume_results.json (UI)
        try:
            self._write_volume_results_json()
        except Exception as e:
            print(f"[PY VOL {self.process_id}] [WARN] Could not write volume_results.json: {e}")

    # ---------------- GUI actions ----------------
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
        """
        PDF GUI:
          - include almeno la final doublet (copiata accanto al PDF)
        """
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Results As", "", "PDF Files (*.pdf)", options=options)
        if not file_path:
            return
        if not file_path.lower().endswith('.pdf'):
            file_path += '.pdf'

        try:
            orig_name = os.environ.get("ORIGINAL_FILE_NAME") or self.meta.get("original_file_name") or os.path.basename(self.meta.get("input_dem_path") or "")
            title = f"Calculation Results - Elliptical Base, Approximation Type 2\nInput DEM: {orig_name}"

            out_dir_for_pdf = os.path.dirname(file_path) if os.path.dirname(file_path) else os.getcwd()
            doublet_png = os.path.join(out_dir_for_pdf, "final_doublet_base_vs_caldera.png")
            self._save_final_doublet_png(doublet_png)

            image_paths = [doublet_png]
            captions = [None]

            pdf_generator.generate_pdf(
                file_path=file_path,
                results_list=self.results_list,
                title=title,
                image_paths=image_paths,
                captions=captions
            )
            QMessageBox.information(self, "Success", f"PDF successfully saved to {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "PDF Error", f"An error occurred while generating the PDF: {e}")

    def download_graph_image(self):
        options = QFileDialog.Options()
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save Graph As", "", "PNG Files (*.png);;JPG Files (*.jpg);;All Files (*)", options=options
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

    def export_metrics(self):
        out_dir = QFileDialog.getExistingDirectory(self, "Select folder to export metrics")
        if not out_dir:
            return

        try:
            self._write_metrics_files(out_dir=out_dir)
            base = getattr(self, "metrics_basename", "metrics")
            QMessageBox.information(self, "Success", f"{base}.json + {base}.csv exported to:\n{out_dir}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"An error occurred while exporting metrics: {e}")


# ========== Entry Point ==========

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python EllipticalVolcano_Approx2.py <dem_file_path> [original_file_name]")
        sys.exit(1)

    input_dem_path = sys.argv[1]
    original_file_name = sys.argv[2] if len(sys.argv) > 2 else "Unknown"

    process_id = _resolve_process_id()
    _ = _ensure_outputs_dir(process_id)

    chosen = _choose_working_dem(process_id)
    if chosen is None:
        print("[PY VOL][ERROR] dem_working.tif not found in outputs/. Refusing to compute on original DEM to avoid bogus 0.00 values.")
        sys.exit(1)

    dem_file_path = chosen

    if not os.path.exists(dem_file_path):
        print(f"[PY VOL][ERROR] File '{dem_file_path}' does not exist.")
        sys.exit(1)

    try:
        with rasterio.open(dem_file_path) as src:
            dem = src.read(1)
            transform = src.transform
            crs = src.crs
            res = src.res
            nodata = src.nodata

            if (crs is None) or getattr(crs, "is_geographic", False):
                print(f"[PY VOL][ERROR] DEM CRS is not metric (CRS={crs}). Refusing to compute because results would collapse to ~0.")
                sys.exit(1)

    except Exception as e:
        print(f"[PY VOL][ERROR] Error opening DEM file: {e}")
        sys.exit(1)

    meta = {
        "process_id": process_id,
        "input_dem_path": input_dem_path,
        "working_dem_path": dem_file_path,
        "crs": crs,
        "res": res,
        "nodata": nodata,
        "original_file_name": os.environ.get("ORIGINAL_FILE_NAME") or os.path.basename(input_dem_path),
        "original_file_stem": os.environ.get("ORIGINAL_FILE_STEM") or os.path.splitext(os.path.basename(input_dem_path))[0],
    }

    if HEADLESS:
        _ = VolumeAnalysisApp(dem, transform, meta=meta, original_file_name=original_file_name)
        print("[PY VOL][INFO] Headless run completed (no GUI).")
        sys.exit(0)

    app = QApplication(sys.argv)
    ex = VolumeAnalysisApp(dem, transform, meta=meta, original_file_name=original_file_name)
    ex.showMaximized()
    sys.exit(app.exec_())