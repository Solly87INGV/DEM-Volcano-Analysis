# CircularVolcano_Approx1.py
# ——————————————————————————————————————————————————————————
# Modulo "volume" (Circular Approximation 1) headless-friendly:
# - calcola metriche + volumi (LOGICHE INVARIATE)
# - in HEADLESS=1: niente GUI, ma scrive SEMPRE outputs/<PID>/:
#     * metrics.json
#     * metrics.csv
#     * volume_results.json          ✅ (richiesto dalla UI: VolumeResultsViewer.js)
#     * final_doublet_base_vs_caldera.png
#
# CHANGE (requested):
# - Nel viewer del modulo volume NON mostriamo più le immagini di complete_dem_analysis.
#   => volume_results.json -> images contiene SOLO la final doublet.
# - Non aggiorniamo più analysis_images.json (che appartiene al primo step).

import sys
import os
import csv
import datetime
import math
import json

# ================= PROJ / EPSG FIX (Windows + PostGIS conflicts) =================
def _fix_proj_env():
    """
    PROJ/GDAL safety:
    - Windows: OK to force PROJ_LIB to pyproj's bundled data (avoids PostGIS PROJ conflicts).
    - Linux/Docker: NEVER force pyproj PROJ db. If PROJ_LIB/PROJ_DATA are set, UNSET them to let
      rasterio/GDAL use their own compatible PROJ data inside the container.
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
                else:
                    print("[WARN] pyproj datadir not found or invalid; PROJ_LIB not set.")
            except Exception as e:
                print(f"[WARN] Unable to force PROJ_LIB to pyproj (Windows): {e}")
            return

        prev_proj_lib = os.environ.pop("PROJ_LIB", None)
        prev_proj_data = os.environ.pop("PROJ_DATA", None)

        if prev_proj_lib is not None:
            print(f"[DEBUG] PROJ_LIB unset (non-Windows): {prev_proj_lib}")
        if prev_proj_data is not None:
            print(f"[DEBUG] PROJ_DATA unset (non-Windows): {prev_proj_data}")

        if prev_proj_lib is None and prev_proj_data is None:
            print("[DEBUG] PROJ env OK (non-Windows): PROJ_LIB/PROJ_DATA not set.")

    except Exception as e:
        print(f"[WARN] Unable to fix PROJ env: {e}")


_fix_proj_env()
# ================================================================================

def _is_headless() -> bool:
    v = str(os.environ.get("HEADLESS", "")).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


HEADLESS = _is_headless()

# -------------------- Module identity (from server.js env) --------------------
# server.js passes these via spawnPython extraEnv:
#   MODULE_KEY, BASE_PROFILE, VOLUME_TYPE, APPROXIMATION_TYPE
MODULE_KEY = (os.environ.get("MODULE_KEY") or "circular_approx1").strip() or "circular_approx1"
BASE_PROFILE = (os.environ.get("BASE_PROFILE") or "").strip()
VOLUME_TYPE = (os.environ.get("VOLUME_TYPE") or "circular").strip()
APPROXIMATION_TYPE = (os.environ.get("APPROXIMATION_TYPE") or "approximation1").strip()

def _normalize_approx_type(v: str) -> str:
    v = (v or "").strip().lower()
    if v in ("approximation1", "approx1", "a1", "1"):
        return "approx1"
    if v in ("approximation2", "approx2", "a2", "2"):
        return "approx2"
    return v or "approx1"

APPROXIMATION_TYPE = _normalize_approx_type(APPROXIMATION_TYPE)
if HEADLESS:
    os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.crs import CRS

# ✅ Allowed new imports (SciPy already present) for morphological rim detection
from scipy.ndimage import (
    sobel,
    binary_dilation,
    binary_closing,
    binary_fill_holes,
    gaussian_filter,
    label,
)

from skimage import measure
from skimage.draw import polygon

# ---- PyQt5: import solo se NON headless ----
if not HEADLESS:
    from PyQt5 import QtWidgets, QtGui, QtCore
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLabel, QMessageBox, QFileDialog
    )
else:
    QtWidgets = QtGui = QtCore = None

    class QApplication:  # pragma: no cover
        def __init__(self, *args, **kwargs): pass
        def exec_(self): return 0

    class QMainWindow:  # pragma: no cover
        def __init__(self, *args, **kwargs): pass

    class QWidget:  # pragma: no cover
        pass

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

    class QLabel:  # pragma: no cover
        def __init__(self, *args, **kwargs): pass

    class QMessageBox:  # pragma: no cover
        ActionRole = 0
        Ok = 0

        @staticmethod
        def critical(parent, title, msg):
            print(f"[ERROR] {title}: {msg}")

        @staticmethod
        def information(parent, title, msg):
            print(f"[INFO] {title}: {msg}")

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

# Matplotlib
if not HEADLESS:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
else:
    FigureCanvas = None

from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib import gridspec
from mpl_toolkits.axes_grid1 import make_axes_locatable

# Generatore PDF (lasciato, se lo usi in GUI)
import pdf_generator


# -------------------- OUTPUTS BASE: UNICA FONTE DI VERITÀ --------------------

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _outputs_base_dir() -> str:
    """
    IMPORTANT:
    - Express serve /outputs da OUTPUTS_DIR (es. /app/backend/outputs)
    - Quindi qui priorità assoluta a OUTPUTS_DIR.
    """
    return (
        os.environ.get("OUTPUTS_DIR")
        or os.environ.get("OUTPUTS_BASE")
        or os.path.join(_script_dir(), "outputs")
    )


def _resolve_process_id() -> str:
    pid = os.environ.get("PROCESS_ID")
    return pid if pid else f"local_{int(datetime.datetime.utcnow().timestamp())}"


def _ensure_outputs_dir(process_id: str) -> str:
    base = _outputs_base_dir()
    out_dir = os.path.join(base, process_id)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _public_path(process_id: str, filename: str) -> str:
    return f"/outputs/{process_id}/{filename}"


# -------------------- DEM selection (manifest-first) --------------------

def _resolve_dem_path_manifest_first(input_dem_path: str, process_id: str) -> dict:
    """
    OBIETTIVO A) Eliminare divergenze dovute a DEM diverso (manifest-first)

    Priorità obbligatoria:
    1) Se esiste analysis_images.json (manifest complete_dem_analysis), usa dem_working.tif nella stessa cartella.
    2) Se non trovato, usa outputs/<PROCESS_ID>/dem_working.tif se esiste.
    3) Altrimenti usa l’input DEM passato a riga di comando.

    Ritorna dict con:
      - selected_path
      - reason
      - manifest_path (se trovato)
      - manifest_dir (se trovato)
    """
    out_dir = _ensure_outputs_dir(process_id)

    manifest_path = os.path.join(out_dir, "analysis_images.json")
    if os.path.exists(manifest_path):
        manifest_dir = os.path.dirname(manifest_path)
        manifest_dem = os.path.join(manifest_dir, "dem_working.tif")
        if os.path.exists(manifest_dem):
            return {
                "selected_path": manifest_dem,
                "reason": "manifest-first: analysis_images.json -> dem_working.tif in manifest dir",
                "manifest_path": manifest_path,
                "manifest_dir": manifest_dir,
            }
        else:
            # Manifest exists but dem_working.tif missing: continue with next priority, but log clearly.
            print(f"[WARN] analysis_images.json found but dem_working.tif not found in same folder: {manifest_dem}")

    candidate = os.path.join(out_dir, "dem_working.tif")
    if os.path.exists(candidate):
        return {
            "selected_path": candidate,
            "reason": "outputs-working: outputs/<PID>/dem_working.tif exists",
            "manifest_path": manifest_path if os.path.exists(manifest_path) else None,
            "manifest_dir": os.path.dirname(manifest_path) if os.path.exists(manifest_path) else None,
        }

    return {
        "selected_path": input_dem_path,
        "reason": "fallback-input: using CLI input DEM",
        "manifest_path": manifest_path if os.path.exists(manifest_path) else None,
        "manifest_dir": os.path.dirname(manifest_path) if os.path.exists(manifest_path) else None,
    }


# -------------------- Funzioni di analisi di base --------------------

def find_lowest_base_contour(matrix, base_elevation_ratio=0.05):
    base_level = float(matrix.min() + (matrix.max() - matrix.min()) * base_elevation_ratio)
    contours = measure.find_contours(matrix, base_level)
    if len(contours) == 0:
        raise ValueError("No contours found for the given base elevation ratio.")
    base_contour = max(contours, key=len)
    return base_contour, base_level


def find_opposite_base_points(contour):
    contour = np.round(contour).astype(int)
    max_index1 = 0
    opposite_index = len(contour) // 2
    base_index1 = tuple(contour[max_index1])
    base_index2 = tuple(contour[opposite_index])
    return base_index1, base_index2


def calculate_slope(matrix):
    dx = sobel(matrix, axis=1)
    dy = sobel(matrix, axis=0)
    return np.hypot(dx, dy)


def find_caldera_contour(matrix, level_ratio=0.8):
    """
    Legacy method (quota-based). Non usato più per definire la caldera in calculate_results().
    Lasciato per compatibilità / debug.
    """
    contour_level = float(matrix.max() * level_ratio)
    contours = measure.find_contours(matrix, contour_level)
    if len(contours) == 0:
        raise ValueError("No contours found for the given level ratio.")
    main_contour = max(contours, key=len)
    return main_contour, contour_level


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


def pixel_area_m2_from_transform(transform) -> float:
    return float(abs(transform.a * transform.e - transform.b * transform.d))


def contour_to_mask(contour, shape):
    c = np.asarray(contour, dtype=float)
    rr, cc = polygon(c[:, 0], c[:, 1], shape)
    m = np.zeros(shape, dtype=bool)
    m[rr, cc] = True
    return m


def outside_ring_mask(mask: np.ndarray, offset_px: int = 1, width_px: int = 3) -> np.ndarray:
    offset_px = int(max(0, offset_px))
    width_px = int(max(1, width_px))

    inner = binary_dilation(mask, iterations=offset_px) if offset_px > 0 else mask
    outer = binary_dilation(mask, iterations=offset_px + width_px)

    ring = outer & (~inner)
    return ring


# -------------------- NEW: Morphological rim detection (ROI + slope + morphology) --------------------

def _center_from_roi_peak_or_centroid(demf: np.ndarray, roi: np.ndarray) -> tuple:
    """
    Center strategy (headless-friendly):
    1) Peak (argmax elevation) within ROI (robust "summit" proxy)
    2) Fallback: centroid of ROI mask
    Returns: (center_row, center_col, method_str)
    """
    try:
        vals = demf[roi]
        if vals.size > 0 and np.any(np.isfinite(vals)):
            tmp = np.full(demf.shape, -np.inf, dtype=float)
            tmp[roi] = demf[roi]
            r, c = np.unravel_index(int(np.nanargmax(tmp)), tmp.shape)
            return float(r), float(c), "peak_in_roi"
    except Exception:
        pass

    rr, cc = np.where(roi)
    if rr.size == 0:
        return float(demf.shape[0] / 2.0), float(demf.shape[1] / 2.0), "fallback_image_center"
    return float(np.mean(rr)), float(np.mean(cc)), "roi_centroid"


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
    # ✅ NEW selection knobs (center-biased)
    max_area_frac: float = 0.35,
    selection_mode: str = "center_biased",  # "center_biased" | "largest"
) -> tuple:
    """
    OBIETTIVO B) Rim detection “morfologica” (ROI + morfologia + slope/break-in-slope-like)

    - ROI = interno della base (base_contour -> contour_to_mask) + dilatazione opzionale
    - slope map (Sobel) -> threshold robusto su percentile calcolato SOLO dentro ROI
    - candidati rim = slope alta dentro ROI
    - morfologia: binary_closing + fill_holes (+ eventuale dilatazione leggera)
    - componenti connesse: seleziona la "giusta" (default: center-biased per evitare l'anello esterno del fianco)
    - contorno finale: measure.find_contours(mask, 0.5) -> quello più lungo

    Ritorna:
      (caldera_contour, debug_dict)
    """
    if dem is None or dem.size == 0:
        raise ValueError("DEM is empty. Cannot detect caldera rim.")

    if base_contour is None or len(base_contour) < 3:
        raise ValueError("Base contour is missing/too small. Cannot build ROI for caldera rim detection.")

    demf = dem.astype(float)

    valid = np.isfinite(demf)
    if nodata is not None:
        valid = valid & (demf != float(nodata))

    roi = contour_to_mask(base_contour, demf.shape)
    roi_dilate_px = int(max(0, roi_dilate_px))
    if roi_dilate_px > 0:
        roi = binary_dilation(roi, iterations=roi_dilate_px)

    roi = roi & valid
    roi_px = int(np.sum(roi))
    if roi_px < max(50, int(min_component_px * 0.5)):
        raise ValueError(
            f"ROI too small after dilation/valid masking (roi_px={roi_px}). "
            "Cannot perform morphological rim detection."
        )

    if slope is None:
        slope = calculate_slope(demf)

    slopef = slope.astype(float)
    if smooth_sigma is not None and float(smooth_sigma) > 0:
        slopef = gaussian_filter(slopef, sigma=float(smooth_sigma))

    roi_slopes = slopef[roi]
    roi_slopes = roi_slopes[np.isfinite(roi_slopes)]
    if roi_slopes.size == 0:
        raise ValueError("No finite slope values inside ROI. Cannot detect rim.")

    slope_q = float(slope_q)
    if slope_q <= 0.0 or slope_q >= 100.0:
        raise ValueError(f"slope_q must be in (0,100). Got {slope_q}")

    thr = float(np.percentile(roi_slopes, slope_q))
    candidates = (slopef >= thr) & roi

    # Morphology to make region coherent
    closing_iterations = int(max(0, closing_iterations))
    if closing_iterations > 0:
        candidates = binary_closing(candidates, iterations=closing_iterations)

    candidates = binary_fill_holes(candidates)

    extra_dilate_px = int(max(0, extra_dilate_px))
    if extra_dilate_px > 0:
        candidates = binary_dilation(candidates, iterations=extra_dilate_px)

    # Ensure still inside ROI
    candidates = candidates & roi

    cand_px = int(np.sum(candidates))
    if cand_px == 0:
        raise ValueError(
            "Morphological rim detection produced empty candidate mask. "
            f"(slope_q={slope_q}, thr={thr}, roi_px={roi_px})"
        )

    lbl, ncomp = label(candidates)
    if ncomp <= 0:
        raise ValueError("Morphological rim detection found no connected components after labeling.")

    # Component stats
    min_component_px = int(max(1, min_component_px))
    max_area_frac = float(max(0.0, max_area_frac))
    if max_area_frac <= 0.0 or max_area_frac > 1.0:
        raise ValueError(f"max_area_frac must be in (0,1]. Got {max_area_frac}")

    # Eligible list with stats
    comps = []
    sizes = []
    for comp_id in range(1, ncomp + 1):
        mask_i = (lbl == comp_id)
        sz = int(np.sum(mask_i))
        sizes.append(sz)
        if sz < min_component_px:
            continue

        area_frac = float(sz / float(max(1, roi_px)))
        if area_frac > max_area_frac:
            continue

        rr, cc = np.where(mask_i)
        if rr.size == 0:
            continue

        cr = float(np.mean(rr))
        cc_ = float(np.mean(cc))
        mean_s = float(np.mean(slopef[mask_i])) if np.any(mask_i) else 0.0
        comps.append({
            "id": int(comp_id),
            "px": int(sz),
            "area_frac": float(area_frac),
            "centroid_rc": (cr, cc_),
            "mean_slope": float(mean_s),
        })

    if not comps:
        raise ValueError(
            "No connected component meets min_component_px AND max_area_frac constraints. "
            f"ncomp={ncomp}, roi_px={roi_px}, sizes(sample)={sizes[:10]}{'...' if len(sizes)>10 else ''}, "
            f"min_component_px={min_component_px}, max_area_frac={max_area_frac}"
        )

    # ✅ NEW: center-biased selection to avoid outer flank ring
    center_r, center_c, center_method = _center_from_roi_peak_or_centroid(demf, roi)

    selection_mode = str(selection_mode or "center_biased").strip().lower()
    if selection_mode not in ("center_biased", "largest"):
        raise ValueError(f"Invalid selection_mode='{selection_mode}'. Use 'center_biased' or 'largest'.")

    if selection_mode == "largest":
        best = max(comps, key=lambda d: d["px"])
        selection_reason = "largest_component_within_constraints"
    else:
        # center-biased score: minimize distance to center, with mild preference for steeper mean slope
        # normalize distances by sqrt(roi_px) to keep it scale-stable
        dist_norm_denom = float(max(1.0, math.sqrt(roi_px)))
        for d in comps:
            cr, cc_ = d["centroid_rc"]
            dist = float(np.hypot(cr - center_r, cc_ - center_c))
            d["dist_to_center_px"] = dist
            d["dist_to_center_norm"] = float(dist / dist_norm_denom)

        # mean_slope normalization
        ms = [d["mean_slope"] for d in comps]
        ms_min = float(min(ms))
        ms_max = float(max(ms))
        ms_rng = float(ms_max - ms_min) if (ms_max - ms_min) > 0 else 1.0

        # Score: smaller dist is better; slightly reward mean_slope; slightly penalize area_frac (avoid huge rings)
        for d in comps:
            mean_s_norm = float((d["mean_slope"] - ms_min) / ms_rng)
            d["mean_slope_norm"] = mean_s_norm
            d["score"] = float(
                (-1.0 * d["dist_to_center_norm"]) +
                (0.15 * mean_s_norm) +
                (-0.30 * d["area_frac"])
            )

        best = max(comps, key=lambda d: d["score"])
        selection_reason = "center_biased_score"

    best_id = int(best["id"])
    best_mask = (lbl == best_id)

    contours = measure.find_contours(best_mask.astype(np.uint8), 0.5)
    if not contours:
        raise ValueError("Connected component selected, but no contour could be extracted (find_contours returned empty).")

    caldera_contour = max(contours, key=len)

    debug = {
        "method": "morphological_slope_roi",
        "roi_dilate_px": int(roi_dilate_px),
        "smooth_sigma": float(smooth_sigma),
        "slope_q": float(slope_q),
        "slope_threshold": float(thr),
        "closing_iterations": int(closing_iterations),
        "extra_dilate_px": int(extra_dilate_px),
        "min_component_px": int(min_component_px),
        "max_area_frac": float(max_area_frac),
        "selection_mode": selection_mode,
        "selection_reason": selection_reason,
        "roi_px": int(roi_px),
        "candidate_px": int(cand_px),
        "n_components": int(ncomp),
        "component_sizes_sample": sizes[:20],  # debug only
        "center_method": center_method,
        "center_rc": [float(center_r), float(center_c)],
        "selected_component_id": int(best_id),
        "selected_component_px": int(best.get("px", 0)),
        "selected_component_area_frac": float(best.get("area_frac", 0.0)),
        "selected_component_centroid_rc": [float(best["centroid_rc"][0]), float(best["centroid_rc"][1])],
        "selected_component_mean_slope": float(best.get("mean_slope", 0.0)),
    }
    if "dist_to_center_px" in best:
        debug["selected_component_dist_to_center_px"] = float(best["dist_to_center_px"])
        debug["selected_component_dist_to_center_norm"] = float(best.get("dist_to_center_norm", 0.0))
    if "score" in best:
        debug["selected_component_score"] = float(best["score"])

    debug["contour_len"] = int(len(caldera_contour))

    return caldera_contour, debug


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
    demf = dem.astype(float)

    caldera_mask = contour_to_mask(caldera_contour, demf.shape)

    valid = np.isfinite(demf)
    if nodata is not None:
        valid = valid & (demf != float(nodata))

    ring = outside_ring_mask(caldera_mask, offset_px=rim_ring_offset_px, width_px=rim_ring_width_px)
    rim_vals = demf[ring & valid]

    if rim_vals.size == 0:
        cont = np.round(caldera_contour).astype(int)
        cont = cont[
            (cont[:, 0] >= 0) & (cont[:, 0] < demf.shape[0]) &
            (cont[:, 1] >= 0) & (cont[:, 1] < demf.shape[1])
        ]
        if cont.size == 0:
            raise ValueError("Caldera contour is empty after bounds filtering (ring fallback also failed).")

        rim_vals = demf[cont[:, 0], cont[:, 1]]
        rim_vals = rim_vals[np.isfinite(rim_vals)]
        if nodata is not None:
            rim_vals = rim_vals[rim_vals != float(nodata)]
        if rim_vals.size == 0:
            raise ValueError("No valid rim elevation values (ring empty and contour all NaN/NoData).")

        rim_method = "percentile_on_contour_fallback"
    else:
        rim_method = "percentile_on_outside_ring"

    z_rim = float(np.percentile(rim_vals, rim_percentile))

    inside_vals = demf[caldera_mask & valid]
    if inside_vals.size == 0:
        raise ValueError("No valid DEM values inside caldera mask.")
    z_floor = float(np.percentile(inside_vals, floor_percentile))

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


def edifice_height_p99_minus_p05(
    dem: np.ndarray,
    base_contour: np.ndarray,
    nodata=None,
    p_high: float = 99.0,
    p_low: float = 5.0,
) -> dict:
    demf = dem.astype(float)

    valid = np.isfinite(demf)
    if nodata is not None:
        valid = valid & (demf != float(nodata))

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
        h = float(np.percentile(vals, p_high) - np.percentile(vals, p_low))

    return {
        "h_max_m": h,
        "method": "p99_minus_p05",
        "scope": scope,
        "p_high": float(p_high),
        "p_low": float(p_low),
        "n": int(vals.size),
    }


# -------------------- Reprojection helper --------------------

def _utm_epsg_from_lonlat(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 + zone) if lat >= 0 else (32700 + zone)


def ensure_metric_dem(dem_path: str, process_id: str) -> str:
    with rasterio.open(dem_path) as src:
        src_crs = src.crs
        if src_crs is None:
            raise RuntimeError("Input DEM has no CRS. Please define CRS before running volume modules.")

        if not src_crs.is_geographic:
            return dem_path

        b = src.bounds
        lon_c = (b.left + b.right) / 2.0
        lat_c = (b.bottom + b.top) / 2.0
        epsg = _utm_epsg_from_lonlat(lon_c, lat_c)
        dst_crs = CRS.from_epsg(epsg)

        out_dir = _ensure_outputs_dir(process_id)
        working_path = os.path.join(out_dir, "dem_working.tif")

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
        dst_arr = np.empty((dst_height, dst_width), dtype=src.dtypes[0])

        reproject(
            source=rasterio.band(src, 1),
            destination=dst_arr,
            src_transform=src.transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=resampling
        )

        with rasterio.open(working_path, "w", **dst_profile) as dst:
            dst.write(dst_arr, 1)

        print(f"[DEBUG] Input DEM is geographic ({src_crs}). Reprojected to {dst_crs} -> {working_path}")
        return working_path


# -------------------- CSV “umano” --------------------

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

    ("Model inputs", "Height used by model (h_max)", "morphometrics.h_max_m", "m"),

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


# -------------------- Main App --------------------

class VolumeAnalysisApp(QMainWindow):
    def __init__(self, dem, transform, meta=None):
        super().__init__()
        if hasattr(self, "setWindowTitle"):
            self.setWindowTitle("Volcano Volume Analysis")

        self.dem = dem
        self.transform = transform
        self.meta = meta or {}

        self.process_id = self.meta.get("process_id") or _resolve_process_id()
        self.out_dir = _ensure_outputs_dir(self.process_id)

        self.calculate_results()

        if not HEADLESS:
            self.initUI()
        else:
            print("[DEBUG] HEADLESS=1 -> GUI disabled (no Qt windows).")

        # Scritture file richieste dalla UI
        self._write_all_artifacts()

    def initUI(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.figure = Figure(figsize=(18, 14), constrained_layout=True)
        self.canvas = FigureCanvas(self.figure)
        main_layout.addWidget(self.canvas)

        button_layout = QHBoxLayout()
        main_layout.addLayout(button_layout)

        self.results_button = QPushButton("View Results && Print Report")
        self.results_button.setFixedSize(180, 50)
        self.results_button.clicked.connect(self.show_results)
        button_layout.addWidget(self.results_button)

        self.download_image_button = QPushButton("Download as PNG or JPG")
        self.download_image_button.setFixedSize(200, 50)
        self.download_image_button.clicked.connect(self.download_graph_image)
        button_layout.addWidget(self.download_image_button)

        self.export_metrics_button = QPushButton("Export Metrics (JSON + CSV)")
        self.export_metrics_button.setFixedSize(220, 50)
        self.export_metrics_button.clicked.connect(self.export_metrics)
        button_layout.addWidget(self.export_metrics_button)

        self.update_display()

    def calculate_results(self):
        # --- LOGICHE DI CALCOLO: INVARIATE ---
        nodata = self.meta.get("nodata", None)

        self.base_contour, self.base_level_m = find_lowest_base_contour(self.dem, base_elevation_ratio=0.05)
        self.base_point1, self.base_point2 = find_opposite_base_points(self.base_contour)

        self.slope = calculate_slope(self.dem)

        # ✅ NEW: caldera_contour morfologico (ROI + slope percentile + morfologia + componenti connesse)
        # ✅ PATCH: component selection center-biased (evita l'anello esterno del fianco)
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
            max_area_frac=0.35,
            selection_mode="center_biased",
        )
        # Legacy caldera level non guida più la caldera
        self.caldera_level_m = None

        self.max_slope_index1, self.max_slope_index2 = find_opposite_slope_points(self.slope, self.caldera_contour)

        # Distances (m)
        self.distance_meters_base = float(
            distance_between_points(
                self.base_point1[0], self.base_point1[1],
                self.base_point2[0], self.base_point2[1],
                self.transform
            )
        )
        self.distance_meters_caldera = float(
            distance_between_points(
                self.max_slope_index1[0], self.max_slope_index1[1],
                self.max_slope_index2[0], self.max_slope_index2[1],
                self.transform
            )
        )

        # Areas (m²)
        self.area_base_m2 = float(calculate_area(self.base_contour, self.transform))
        self.area_caldera_m2 = float(calculate_area(self.caldera_contour, self.transform))

        # Perimeters (m)
        self.perimeter_base_m = float(contour_perimeter_m(self.base_contour, self.transform))
        self.perimeter_caldera_m = float(contour_perimeter_m(self.caldera_contour, self.transform))

        # Height robusta
        self.height_model = edifice_height_p99_minus_p05(
            dem=self.dem,
            base_contour=self.base_contour,
            nodata=nodata,
            p_high=99.0,
            p_low=5.0
        )
        self.h_max = float(self.height_model.get("h_max_m", 0.0))

        # Radii
        self.R1 = float(self.distance_meters_base / 2.0)
        self.R2 = float(self.distance_meters_caldera / 2.0)

        # Edifice (frustum-like)
        self.V_frustum_m3 = float((1.0 / 3.0) * np.pi * self.h_max * (self.R1**2 + self.R2**2 + self.R1 * self.R2))

        # Caldera volume (depth integrated)
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
        if depth_raw <= 0.0:
            print(
                f"[WARN] Caldera depth_ref_m <= 0 (rim={caldera_depth.get('z_rim_ref_m')}, "
                f"floor={caldera_depth.get('z_floor_ref_m')}, depth={depth_raw}). "
                "Trying fallback percentiles (rim=98, floor=2)."
            )

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
            Vfb = float(caldera_depth_fb.get("V_caldera_m3", 0.0))

            if (depth_fb > 0.0 and Vfb >= V0) or (depth_fb > depth_raw and Vfb > 0.0):
                caldera_depth = caldera_depth_fb
                self.caldera_fallback_used = True
                print(f"[INFO] Fallback accepted. depth_ref_m={depth_fb}, V_caldera_m3={Vfb}")
            else:
                print(f"[INFO] Fallback rejected. Keeping original. depth_ref_m={depth_raw}, V_caldera_m3={V0}")

        self.V_caldera_m3 = float(caldera_depth["V_caldera_m3"])
        self.V_effective_m3 = float(self.V_frustum_m3 - self.V_caldera_m3)

        # Classification
        depth_final = float(caldera_depth.get("depth_ref_m", 0.0))
        V_final = float(caldera_depth.get("V_caldera_m3", 0.0))

        if depth_final <= 0.0:
            self.caldera_status = "non_depressive_or_complex"
            self.caldera_reason = "rim_below_floor"
            self.caldera_action = "not_computed"
        elif V_final <= 0.0:
            self.caldera_status = "non_depressive_or_complex"
            self.caldera_reason = "no_depression_pixels"
            self.caldera_action = "not_computed"
        else:
            self.caldera_status = "depressive"
            self.caldera_reason = None
            self.caldera_action = "computed"

        # Keep refs
        self.caldera_z_rim_ref_m = float(caldera_depth["z_rim_ref_m"])
        self.caldera_z_floor_ref_m = float(caldera_depth["z_floor_ref_m"])
        self.caldera_depth_ref_m = float(caldera_depth.get("depth_ref_m", 0.0))
        self.caldera_depth_ref_m_clamped = float(caldera_depth.get("depth_ref_m_clamped", max(0.0, self.caldera_depth_ref_m)))
        self.caldera_rim_method = str(caldera_depth.get("rim_method", "unknown"))
        self.caldera_rim_ring_offset_px = int(caldera_depth.get("rim_ring_offset_px", 1))
        self.caldera_rim_ring_width_px = int(caldera_depth.get("rim_ring_width_px", 3))
        self.caldera_rim_sample_count = int(caldera_depth.get("rim_sample_count", 0))
        self.caldera_rim_percentile = float(caldera_depth.get("rim_percentile", 90.0))
        self.caldera_floor_percentile = float(caldera_depth.get("floor_percentile", 5.0))
        self.caldera_pixel_area_m2 = float(caldera_depth.get("pixel_area_m2", 0.0))
        self.caldera_mask_area_m2 = float(caldera_depth.get("caldera_mask_area_m2", 0.0))

        # GUI-friendly text (km etc) — utile anche in headless
        distance_base_km = self.distance_meters_base * 1e-3
        distance_caldera_km = self.distance_meters_caldera * 1e-3
        area_base_km2 = self.area_base_m2 * 1e-6
        area_caldera_km2 = self.area_caldera_m2 * 1e-6
        v_km3 = self.V_frustum_m3 * 1e-9
        v_caldera_km3 = self.V_caldera_m3 * 1e-9
        v_eff_km3 = self.V_effective_m3 * 1e-9

        if getattr(self, "caldera_status", "depressive") == "depressive":
            caldera_line_gui = f"Caldera volume: {v_caldera_km3:.3e} km³"
        else:
            reason = getattr(self, "caldera_reason", "complex")
            caldera_line_gui = f"Caldera volume: N/A (complex/non-depressive: {reason})"

        self.results_text = (
            f"Base area of the volcano: {area_base_km2:.2f} km²\n"
            f"Base width (Distance between opposite points of the base): {distance_base_km:.2f} km\n"
            f"Caldera area of the volcano: {area_caldera_km2:.2f} km²\n"
            f"Caldera width (Distance between opposite points of the caldera): {distance_caldera_km:.2f} km\n"
            f"Total volume of the volcanic edifice: {v_km3:.2f} km³\n"
            f"{caldera_line_gui}\n"
            f"Effective volume of the volcanic edifice: {v_eff_km3:.2f} km³"
        )

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
        ax1.set_title("Volcano DEM", fontsize=14, pad=20, y=1.02)
        ax1.axis('on')

        ax2 = fig.add_subplot(gs[0, 1])
        im2 = ax2.imshow(self.dem, cmap='terrain', origin='upper')
        div2 = make_axes_locatable(ax2)
        cax2 = div2.append_axes("right", size="4.6%", pad=0.10)
        cax2.set_in_layout(True)
        cbar2 = fig.colorbar(im2, cax=cax2)
        cbar2.set_label("Elevation (m)", rotation=90)
        ax2.plot(self.base_point1[1], self.base_point1[0], 'ro', markersize=10, label='Base 1')
        ax2.plot(self.base_point2[1], self.base_point2[0], 'yo', markersize=10, label='Base 2')
        ax2.plot(self.base_contour[:, 1], self.base_contour[:, 0], 'w-', linewidth=1, label="Base Contour")
        ax2.set_title("Opposite Points of the Volcano Base", fontsize=14, pad=20, y=1.02)
        ax2.axis('on')

        ax3 = fig.add_subplot(gs[0, 2])
        im3 = ax3.imshow(self.dem, cmap='terrain', origin='upper')
        div3 = make_axes_locatable(ax3)
        cax3 = div3.append_axes("right", size="4.6%", pad=0.10)
        cax3.set_in_layout(True)
        cbar3 = fig.colorbar(im3, cax=cax3)
        cbar3.set_label("Elevation (m)", rotation=90)
        ax3.plot(self.max_slope_index1[1], self.max_slope_index1[0], 'ro', markersize=10, label='Max Slope 1')
        ax3.plot(self.max_slope_index2[1], self.max_slope_index2[0], 'yo', markersize=10, label='Max Slope 2')
        ax3.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label="Caldera Contour")
        ax3.set_title("Opposite Maximum Slope Points on the Caldera", fontsize=14, pad=20, y=1.02)
        ax3.axis('on')

        self.canvas.draw()

    # -------------------- PNG doppietta finale --------------------

    def _save_final_doublet_png(self, out_path):
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

        FIG_W, FIG_H = 14.5, 5.5
        fig = plt.figure(figsize=(FIG_W, FIG_H))
        gs = gridspec.GridSpec(1, 3, figure=fig, width_ratios=[1.0, 0.08, 1.0], wspace=0.15)

        ax1 = fig.add_subplot(gs[0, 0])
        im1 = ax1.imshow(self.dem, cmap='terrain', origin='upper', interpolation='nearest', resample=False)
        ax1.plot(self.base_contour[:, 1], self.base_contour[:, 0], 'w-', linewidth=1, label='Base Contour')
        ax1.plot(self.base_point1[1], self.base_point1[0], 'ro', markersize=8, label='Base 1')
        ax1.plot(self.base_point2[1], self.base_point2[0], 'yo', markersize=8, label='Base 2')
        ax1.set_title("Opposite Points of the Volcano Base", pad=8, fontsize=12)
        ax1.set_aspect('equal', adjustable='box')
        div1 = make_axes_locatable(ax1)
        cax1 = div1.append_axes("right", size="4.6%", pad=0.10)
        cbar1 = fig.colorbar(im1, cax=cax1)
        cbar1.set_label("Elevation (m)", rotation=90)

        ax_spacer = fig.add_subplot(gs[0, 1])
        ax_spacer.axis('off')

        ax2 = fig.add_subplot(gs[0, 2])
        im2 = ax2.imshow(self.dem, cmap='terrain', origin='upper', interpolation='nearest', resample=False)
        ax2.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label='Caldera Contour')
        ax2.plot(self.max_slope_index1[1], self.max_slope_index1[0], 'ro', markersize=8, label='Max Slope 1')
        ax2.plot(self.max_slope_index2[1], self.max_slope_index2[0], 'yo', markersize=8, label='Max Slope 2')
        ax2.set_title("Opposite Maximum Slope Points on the Caldera", pad=8, fontsize=12)
        ax2.set_aspect('equal', adjustable='box')
        div2 = make_axes_locatable(ax2)
        cax2 = div2.append_axes("right", size="4.6%", pad=0.10)
        cbar2 = fig.colorbar(im2, cax=cax2)
        cbar2.set_label("Elevation (m)", rotation=90)

        fig.savefig(out_path, dpi=170)
        plt.close(fig)

        if not os.path.exists(out_path):
            raise RuntimeError(f"Doublet not saved: {out_path}")

    # -------------------- Metrics / Results artifacts --------------------

    def _build_metrics_dict(self) -> dict:
        crs = self.meta.get("crs")
        res = self.meta.get("res")
        nodata = self.meta.get("nodata")

        A_base_m2 = float(getattr(self, "area_base_m2", 0.0))
        A_caldera_m2 = float(getattr(self, "area_caldera_m2", 0.0))
        P_base = float(getattr(self, "perimeter_base_m", 0.0))
        P_caldera = float(getattr(self, "perimeter_caldera_m", 0.0))
        D_base = float(getattr(self, "distance_meters_base", 0.0))
        D_caldera = float(getattr(self, "distance_meters_caldera", 0.0))
        R_base = float(getattr(self, "R1", 0.0))
        R_caldera = float(getattr(self, "R2", 0.0))
        R_eq_base = math.sqrt(A_base_m2 / math.pi) if A_base_m2 > 0 else 0.0
        R_eq_caldera = math.sqrt(A_caldera_m2 / math.pi) if A_caldera_m2 > 0 else 0.0

        h_max = float(getattr(self, "h_max", 0.0))
        V_frustum_m3 = float(getattr(self, "V_frustum_m3", 0.0))
        V_caldera_m3 = float(getattr(self, "V_caldera_m3", 0.0))
        V_eff_m3 = float(getattr(self, "V_effective_m3", 0.0))

        metrics = {
            "meta": {
                "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                "process_id": self.process_id,
                "moduleKey": MODULE_KEY,
                "baseProfile": BASE_PROFILE,
                "volumeType": VOLUME_TYPE,
                "approximationType": APPROXIMATION_TYPE,

                "original_file_name": os.environ.get("ORIGINAL_FILE_NAME") or self.meta.get("original_file_name"),
                "original_file_stem": os.environ.get("ORIGINAL_FILE_STEM") or self.meta.get("original_file_stem"),
                "input_dem_path": self.meta.get("input_dem_path"),
                "working_dem_path": self.meta.get("working_dem_path"),
                "crs": str(crs) if crs is not None else None,
                "res": list(res) if res is not None else None,
                "nodata": nodata,
                "params": {
                    "base_elevation_ratio": 0.05,
                    # kept for backward readability; caldera is now slope/ROI-based
                    "caldera_level_ratio": 0.8,
                    "caldera_rim_detection": "morphological_slope_roi",
                }
            },
            "nodata_stats": dem_nodata_stats(self.dem, nodata=nodata),
            "height_model": getattr(self, "height_model", None),
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
                "h_max_m": h_max,
            },
            "volume_model": {
                "edifice_model": "frustum_like",
                "caldera_model": "depth_integrated_rim_to_dem",
                "inputs_used": {
                    "h_max_m": h_max,
                    "R_base_m": R_base,
                    "R_caldera_m": R_caldera,
                },
                "intermediate": {"V_frustum_m3": V_frustum_m3}
            },
            "volumes": {
                "V_total_m3": V_frustum_m3,
                "V_caldera_m3": V_caldera_m3,
                "V_effective_m3": V_eff_m3,
            },
        }

        # Optional debug (doesn't change formulas/flow; just extra info)
        if hasattr(self, "caldera_debug") and isinstance(self.caldera_debug, dict):
            metrics["meta"]["caldera_debug"] = self.caldera_debug

        return metrics

    def _write_metrics_files(self):
        metrics = self._build_metrics_dict()

        json_path = os.path.join(self.out_dir, "metrics.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(_as_serializable(metrics), f, indent=2, ensure_ascii=False)

        csv_path = os.path.join(self.out_dir, "metrics.csv")
        human_rows = metrics_to_human_rows(metrics)
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["section", "metric", "value", "unit"])
            for section, label_txt, value, unit in human_rows:
                w.writerow([section, label_txt, value, unit])

        print(f"[INFO] metrics written: {json_path}")
        print(f"[INFO] metrics written: {csv_path}")

    def _write_volume_results_json(self):
        """
        File richiesto dalla UI (VolumeResultsViewer.js):
          /outputs/<PID>/volume_results.json

        CHANGE:
        - images contiene SOLO la final doublet, senza pescare analysis_images.json.
        """
        distance_base_km = self.distance_meters_base * 1e-3
        distance_caldera_km = self.distance_meters_caldera * 1e-3
        area_base_km2 = self.area_base_m2 * 1e-6
        area_caldera_km2 = self.area_caldera_m2 * 1e-6
        v_tot_km3 = self.V_frustum_m3 * 1e-9
        v_cal_km3 = self.V_caldera_m3 * 1e-9
        v_eff_km3 = self.V_effective_m3 * 1e-9

        # pixel size (m): prendiamo res[0] se disponibile
        pixel_size_m = None
        res = self.meta.get("res")
        try:
            if res and isinstance(res, (list, tuple)) and len(res) >= 1:
                pixel_size_m = float(res[0])
        except Exception:
            pixel_size_m = None

        # ✅ SOLO la final doublet (stile Approx2)
        images = ["final_doublet_base_vs_caldera.png"]

        payload = {
            "processId": self.process_id,
            "status": "completed",
            "moduleKey": MODULE_KEY,
            "baseProfile": BASE_PROFILE,
            "volumeType": VOLUME_TYPE,
            "approximationType": APPROXIMATION_TYPE,
            "summaryText": self.results_text,

            "result": {
                "base_area_km2": area_base_km2,
                "base_width_km": distance_base_km,
                "caldera_area_km2": area_caldera_km2,
                "caldera_width_km": distance_caldera_km,
                "total_volume_km3": v_tot_km3,
                "caldera_volume_km3": v_cal_km3,
                "effective_volume_km3": v_eff_km3,
                "h_max_m": float(getattr(self, "h_max", None)) if getattr(self, "h_max", None) is not None else None,
                "pixel_size_m": pixel_size_m,
            },

            "images": images,

            "links": {
                "metrics_json": _public_path(self.process_id, "metrics.json"),
                "metrics_csv": _public_path(self.process_id, "metrics.csv"),
                "final_doublet": _public_path(self.process_id, "final_doublet_base_vs_caldera.png"),
            }
        }

        out_path = os.path.join(self.out_dir, "volume_results.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        print(f"[INFO] volume_results written: {out_path}")

        if HEADLESS:
            try:
                print(json.dumps(payload, ensure_ascii=False))
            except Exception:
                pass

    def _write_all_artifacts(self):
        # 1) metrics
        self._write_metrics_files()

        # 2) final doublet (sempre, anche headless)
        doublet_path = os.path.join(self.out_dir, "final_doublet_base_vs_caldera.png")
        try:
            self._save_final_doublet_png(doublet_path)
        except Exception as e:
            print(f"[WARN] Could not save final doublet: {e}")

        # 3) volume_results.json (sempre)
        self._write_volume_results_json()

    # ---- GUI-only stuff below ----
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
            title = f"Calculation Results - Circular Base, Approximation Type 1\nInput DEM: {orig_name}"

            out_dir_for_doublet = os.path.dirname(file_path) if os.path.dirname(file_path) else os.getcwd()
            doublet_png = os.path.join(out_dir_for_doublet, "final_doublet_base_vs_caldera.png")
            self._save_final_doublet_png(doublet_png)

            image_paths = []
            if os.path.exists(doublet_png):
                image_paths.append(doublet_png)
            if not image_paths:
                image_paths = [doublet_png]

            pdf_generator.generate_pdf(
                file_path=file_path,
                results_list=[self.results_text],
                title=title,
                image_paths=image_paths,
                captions=[None] * len(image_paths)
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

    def export_metrics(self):
        out_dir = QFileDialog.getExistingDirectory(self, "Select folder to export metrics")
        if not out_dir:
            return
        try:
            for name in ("metrics.json", "metrics.csv", "volume_results.json", "final_doublet_base_vs_caldera.png"):
                src = os.path.join(self.out_dir, name)
                if os.path.exists(src):
                    dst = os.path.join(out_dir, name)
                    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
                        fdst.write(fsrc.read())
            QMessageBox.information(self, "Success", f"Exported metrics/results to:\n{out_dir}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"An error occurred while exporting: {e}")


# -------------------- Main --------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python CircularVolcano_Approx1.py <dem_file_path>")
        sys.exit(1)

    input_dem_path = sys.argv[1]
    process_id = _resolve_process_id()

    # ✅ A) Manifest-first DEM selection (old behavior restored deterministically)
    dem_choice = _resolve_dem_path_manifest_first(input_dem_path=input_dem_path, process_id=process_id)
    dem_file_path = dem_choice["selected_path"]

    print("[DEBUG] DEM selection (manifest-first):")
    print(f"        process_id     = {process_id}")
    print(f"        input_dem_path = {input_dem_path}")
    if dem_choice.get("manifest_path"):
        print(f"        manifest_path  = {dem_choice.get('manifest_path')}")
    print(f"        selected_path  = {dem_file_path}")
    print(f"        reason         = {dem_choice.get('reason')}")

    # Se ancora geografico, riproietta e salva dentro outputs/<PID>/dem_working.tif
    if os.path.exists(dem_file_path):
        dem_file_path = ensure_metric_dem(dem_file_path, process_id=process_id)

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

            print(f"[DEBUG] DEM opened for volume. Path={dem_file_path}")
            print(f"[DEBUG] CRS={crs}  RES={res}  Transform={transform}  NODATA={nodata}")
    except Exception as e:
        print(f"Error opening DEM file: {e}")
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
        _ = VolumeAnalysisApp(dem, transform, meta=meta)
        print("[INFO] Headless run completed (no GUI).")
        sys.exit(0)

    app = QApplication(sys.argv)
    ex = VolumeAnalysisApp(dem, transform, meta=meta)
    ex.showMaximized()
    sys.exit(app.exec_())
