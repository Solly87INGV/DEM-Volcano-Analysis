# EllipticalVolcano_Approx2.py
# ——————————————————————————————————————————————————————————
# Approx2 (ellittico): calcoli invariati per l’EDIFICIO.
# PDF:
#   - carica immagini dal manifest ma rimuove SOLO le TRIPLETTE
#   - NON aggiunge l'overview (niente tripla in PDF)
#   - aggiunge la DOPPIETTA finale "Base vs Caldera"
#   - fail-safe: se resta vuoto, usa almeno la doppietta
#
# ✅ Integrazioni:
#   - PROJ/EPSG fix (Windows): forza PROJ_LIB verso pyproj/proj_dir
#   - Caldera contour: NEW slope-guided rim (come Approx1) + fallback controllato
#   - Caldera depth: dal DEM (rim–floor) + fallback percentili + classificazione (N/A se non depressiva/complessa)
#   - Caldera volume (Approx2): CILINDRO a base ellittica => V = A_caldera * depth_DEM
#   - genera metrics.json + metrics.csv in outputs/<process_id>/ (silenzioso in __init__)
#   - pulsante GUI "Export Metrics (JSON + CSV)" per esportare in una cartella scelta

import sys
import os
import json
import time
import csv
import datetime
import math

import numpy as np

# ================= PROJ / EPSG FIX (Windows + PostGIS conflicts) =================
def _force_proj_lib_to_pyproj():
    try:
        from pyproj import datadir
        proj_dir = datadir.get_data_dir()  # .../pyproj/proj_dir/share/proj
        if proj_dir and os.path.isdir(proj_dir):
            os.environ["PROJ_LIB"] = proj_dir
            print(f"[DEBUG] PROJ_LIB forced to pyproj: {proj_dir}")
    except Exception as e:
        print(f"[WARN] Could not force PROJ_LIB via pyproj: {e}")

_force_proj_lib_to_pyproj()
# ================================================================================

import rasterio
from scipy.ndimage import sobel
from skimage import measure

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox, QFileDialog
)

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib import gridspec
from mpl_toolkits.axes_grid1 import make_axes_locatable  # ← per cbar co-alte affiancate

import pdf_generator
METRICS_BASENAME = "metrics_ellip_a2"

# ==========================
# Caldera contour parameters (slope-guided) — SAME AS Approx1
# ==========================
CALDERA_RIM_SLOPE_TOP_PERCENT = 2.0     # top % slope pixels inside base -> rim candidates
CALDERA_RIM_ELEV_PERCENTILE = 60.0      # percentile of DEM elevations on rim candidates -> iso-level
CALDERA_MIN_AREA_RATIO = 0.005          # caldera area must be >= 0.5% base area
CALDERA_MAX_AREA_RATIO = 0.25           # caldera area must be <= 25% base area
CALDERA_INSIDE_FRAC_MIN = 0.90          # at least 90% contour pixels inside base
CALDERA_MIN_CONTOUR_POINTS = 50         # ignore tiny noisy contours


# ========== helpers: outputs & manifest (come negli altri OK) ==========

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))

def _resolve_process_id() -> str:
    env_id = os.environ.get("PROCESS_ID")
    if env_id:
        return env_id
    return f"local_{int(time.time())}"

def _ensure_outputs_dir(process_id: str) -> str:
    out_dir = os.path.join(_script_dir(), "outputs", process_id)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def _find_manifest():
    """
    Restituisce (outputs_dir, manifest_path) se trovati.
    Priorità: env PROCESS_ID -> outputs/<PROCESS_ID>/analysis_images.json
    Fallback: manifest più recente in outputs/*/analysis_images.json
    """
    base = os.path.join(_script_dir(), "outputs")

    # 1) PROCESS_ID esplicito
    pid = os.environ.get("PROCESS_ID")
    if pid:
        out_dir = os.path.join(base, pid)
        mp = os.path.join(out_dir, "analysis_images.json")
        if os.path.exists(mp):
            return out_dir, mp

    # 2) fallback: ultimo manifest disponibile
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
    """Estrae dal JSON la lista di immagini (preferendo abs_path; fallback a filename)."""
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
    """Normalizza e tiene solo i file realmente esistenti."""
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
    """Rimuove ogni triplet_*.png (case-insensitive) dalla lista."""
    out = []
    for p in paths:
        name = os.path.basename(p).lower()
        if "triplet_" in name:
            continue
        out.append(p)
    return out


# ========== Analysis (invariato) ==========

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
    # centro pixel
    x, y = transform * (col + 0.5, row + 0.5)
    return float(x), float(y)

def distance_between_points(r1, c1, r2, c2, transform):
    """
    Distanza in metri tra due punti in coordinate pixel (row/col),
    usando l'Affine transform del raster.
    """
    x1, y1 = _pixel_to_map_xy(transform, r1, c1)
    x2, y2 = _pixel_to_map_xy(transform, r2, c2)
    return float(np.hypot(x2 - x1, y2 - y1))

def calculate_area(contour, transform):
    """
    Area in m² del contorno (Nx2: [row, col]) usando coordinate mappa (metri)
    via transform + formula di Shoelace.
    """
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

def find_caldera_contour(matrix, level_ratio=0.8):
    contour_level = matrix.max() * level_ratio
    contours = measure.find_contours(matrix, contour_level)
    if len(contours) == 0:
        raise ValueError("No contours found for the given level ratio.")
    return max(contours, key=len)

def find_opposite_slope_points(slope_matrix, contour):
    contour = np.round(contour).astype(int)
    contour = contour[
        (contour[:,0] >= 0) & (contour[:,0] < slope_matrix.shape[0]) &
        (contour[:,1] >= 0) & (contour[:,1] < slope_matrix.shape[1])
    ]
    if len(contour) == 0:
        raise ValueError("No valid points in contour after filtering.")
    contour_slopes = [slope_matrix[pt[0], pt[1]] for pt in contour]
    max_index1 = np.argmax(contour_slopes)
    max_slope_index1 = tuple(contour[max_index1])
    opposite_index = (max_index1 + len(contour) // 2) % len(contour)
    max_slope_index2 = tuple(contour[opposite_index])
    return max_slope_index1, max_slope_index2


# ========== Caldera depth-integrated helpers (usati QUI solo per stimare depth rim–floor) ==========

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
    """
    NOTA: in Approx2 usiamo questa funzione per stimare:
      - z_rim_ref_m, z_floor_ref_m, depth_ref_m (rim–floor)
      - QA fields
    Non usiamo più V_caldera_m3 'depth-integrated' come volume finale del modello.
    """
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

    # (lasciato per QA/diagnostica; NON è il volume di modello Approx2)
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


# ========== NEW: Slope-guided caldera contour extraction (as in Approx1) ==========

def find_caldera_contour_slope_guided(
    dem: np.ndarray,
    slope: np.ndarray,
    base_contour: np.ndarray,
    transform,
    nodata=None,
    rim_slope_top_percent: float = CALDERA_RIM_SLOPE_TOP_PERCENT,
    rim_elev_percentile: float = CALDERA_RIM_ELEV_PERCENTILE,
    min_area_ratio: float = CALDERA_MIN_AREA_RATIO,
    max_area_ratio: float = CALDERA_MAX_AREA_RATIO,
    inside_frac_min: float = CALDERA_INSIDE_FRAC_MIN,
    min_contour_points: int = CALDERA_MIN_CONTOUR_POINTS,
) -> np.ndarray:
    """
    Estrae il caldera contour stimando una quota 'rim' dai pixel a slope più alto
    dentro la base, poi seleziona l'isoline DEM a quella quota.

    Filtri:
      - contorno quasi tutto dentro base (inside_frac_min)
      - area ratio in [min_area_ratio, max_area_ratio] rispetto alla base
    Scoring:
      - massimizza slope medio campionato lungo il contorno
    """
    dem = np.asarray(dem).astype(float)
    slope = np.asarray(slope).astype(float)

    valid = np.isfinite(dem)
    if nodata is not None:
        try:
            valid &= (dem != float(nodata))
        except Exception:
            pass

    base_mask = contour_to_mask(base_contour, dem.shape)
    in_base = base_mask & valid
    if int(np.sum(in_base)) == 0:
        raise ValueError("Base mask empty/invalid; cannot guide caldera search.")

    slope_vals = slope[in_base]
    if slope_vals.size < 50:
        raise ValueError("Too few slope samples inside base for slope-guided caldera.")

    # rim candidates = top X% slope inside base
    thr = np.percentile(slope_vals, 100.0 - float(rim_slope_top_percent))
    rim_pix = in_base & (slope >= thr)

    # widen fallback if too few
    if int(np.sum(rim_pix)) < 30:
        thr = np.percentile(slope_vals, 95.0)
        rim_pix = in_base & (slope >= thr)

    if int(np.sum(rim_pix)) < 10:
        raise ValueError("Unable to extract rim candidates from slope (too few pixels).")

    rim_elev = dem[rim_pix]
    rim_elev = rim_elev[np.isfinite(rim_elev)]
    if rim_elev.size == 0:
        raise ValueError("No valid elevations for rim candidates.")

    z_rim_guess = float(np.percentile(rim_elev, float(rim_elev_percentile)))

    contours = measure.find_contours(dem, z_rim_guess)
    if not contours:
        raise ValueError("No contours found at slope-guided rim elevation.")

    A_base = float(calculate_area(base_contour, transform))
    if A_base <= 0:
        raise ValueError("Base area invalid (<=0).")

    def _inside_fraction(cont):
        cc = np.round(np.asarray(cont)).astype(int)
        rr = cc[:, 0]
        cc2 = cc[:, 1]
        ok = (rr >= 0) & (rr < dem.shape[0]) & (cc2 >= 0) & (cc2 < dem.shape[1])
        rr = rr[ok]
        cc2 = cc2[ok]
        if rr.size == 0:
            return 0.0
        return float(np.mean(base_mask[rr, cc2]))

    def _mean_slope_on_contour(cont):
        cc = np.round(np.asarray(cont)).astype(int)
        rr = cc[:, 0]
        cc2 = cc[:, 1]
        ok = (rr >= 0) & (rr < dem.shape[0]) & (cc2 >= 0) & (cc2 < dem.shape[1])
        rr = rr[ok]
        cc2 = cc2[ok]
        if rr.size == 0:
            return 0.0
        return float(np.mean(slope[rr, cc2]))

    best = None
    best_score = -1e18

    for c in contours:
        if c is None or c.shape[0] < int(min_contour_points):
            continue

        inside_frac = _inside_fraction(c)
        if inside_frac < float(inside_frac_min):
            continue

        A_c = float(calculate_area(c, transform))
        if A_c <= 0:
            continue

        ratio = A_c / A_base
        if ratio < float(min_area_ratio) or ratio > float(max_area_ratio):
            continue

        score = _mean_slope_on_contour(c)
        score2 = score + 1e-6 * float(c.shape[0])  # tie-break
        if score2 > best_score:
            best_score = score2
            best = c

    # fallback: if none pass filters, pick max mean slope
    if best is None:
        for c in contours:
            if c is None or c.shape[0] < int(min_contour_points):
                continue
            score = _mean_slope_on_contour(c)
            score2 = score + 1e-6 * float(c.shape[0])
            if score2 > best_score:
                best_score = score2
                best = c

    if best is None:
        raise ValueError("No suitable caldera contour found (slope-guided).")

    return best


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
    ("meta.original_file_name", "Original input filename"),
    ("meta.original_file_stem", "Original input filename (stem)"),
    ("meta.input_dem_path", "Input DEM path"),
    ("meta.working_dem_path", "Working DEM path"),
    ("meta.crs", "Working DEM CRS (EPSG/WKT)"),
    ("meta.res", "Pixel resolution (m) [x,y]"),
    ("meta.nodata", "NoData value"),
    ("meta.params.caldera_contour_method", "Caldera contour method"),

    # --- Morphometrics (Base) ---
    ("morphometrics.A_base_m2", "Base area (m²)"),
    ("morphometrics.P_base_m", "Base perimeter (m)"),
    ("morphometrics.D_base_m", "Base diameter (m) [opposite points]"),
    ("morphometrics.R_base_m", "Base radius used in volume (m)"),
    ("morphometrics.R_eq_base_m", "Equivalent base radius from area (m)"),

    # --- Morphometrics (Caldera) ---
    ("morphometrics.A_caldera_m2", "Caldera area (m²)"),
    ("morphometrics.P_caldera_m", "Caldera perimeter (m)"),
    ("morphometrics.D_caldera_m", "Caldera diameter (m) [opposite points]"),
    ("morphometrics.R_caldera_m", "Caldera radius used in volume (m)"),
    ("morphometrics.R_eq_caldera_m", "Equivalent caldera radius from area (m)"),

    # --- Height used by the model ---
    ("morphometrics.h_max_m", "Height used by model (m)"),

    # --- Volumes ---
    ("volumes.V_total_m3", "Total edifice volume (m³)"),
    ("volumes.V_caldera_m3", "Caldera volume (m³)"),
    ("volumes.V_effective_m3", "Effective edifice volume (m³)"),

    # --- Derived ---
    ("derived.slenderness_H_over_Dbase", "Slenderness H/Dbase (unitless)"),
    ("derived.sanity_Abase_over_Dbase2", "Sanity Abase/Dbase² (unitless)"),
    ("derived.circularity_base", "Circularity base (unitless)"),
    ("derived.circularity_caldera", "Circularity caldera (unitless)"),
    ("derived.eq_height_V_over_Abase_m", "Equivalent height V/Abase (m)"),
    ("derived.ratio_vs_cone", "Ratio vs perfect cone (unitless)"),

    # --- Caldera QA (extra, utile in CSV) ---
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
        self.setWindowTitle('Elliptical Volcano Volume Analysis - Approximation 2')
        self.dem = dem
        self.transform = transform

        self.meta = meta or {}
        self.process_id = self.meta.get("process_id") or _resolve_process_id()
        self.out_dir = _ensure_outputs_dir(self.process_id)
        self.original_file_name = original_file_name
        self.metrics_basename = METRICS_BASENAME

        self.calculate_results()
        self.initUI()

        try:
            self._save_overview_png()   # solo su disco, NON va nel PDF
            self._emit_stdout_payload() # payload JSON ok
        except Exception as e:
            print(f"[PY VOL WARN] post-render saving/payload failed: {e}")

        # ✅ metrics: scrive SEMPRE, silenzioso in outputs/<process_id>/
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

    def calculate_results(self):
        try:
            self.base_contour = find_lowest_base_contour(self.dem, base_elevation_ratio=0.05)
            self.base_point1, self.base_point2 = find_opposite_base_points(self.base_contour)

            # Base distances
            self.distance_meters_base = distance_between_points(
                self.base_point1[0], self.base_point1[1],
                self.base_point2[0], self.base_point2[1],
                self.transform
            )
            self.distance_base_km = self.distance_meters_base * 1e-3

            nodata = self.meta.get("nodata", None)

            # Caldera
            self.slope = calculate_slope(self.dem)

            # ✅ NEW: slope-guided caldera contour + fallback controllato
            self.caldera_contour_method = "slope_guided_rim"
            try:
                self.caldera_contour = find_caldera_contour_slope_guided(
                    dem=self.dem,
                    slope=self.slope,
                    base_contour=self.base_contour,
                    transform=self.transform,
                    nodata=nodata,
                    rim_slope_top_percent=CALDERA_RIM_SLOPE_TOP_PERCENT,
                    rim_elev_percentile=CALDERA_RIM_ELEV_PERCENTILE,
                    min_area_ratio=CALDERA_MIN_AREA_RATIO,
                    max_area_ratio=CALDERA_MAX_AREA_RATIO,
                    inside_frac_min=CALDERA_INSIDE_FRAC_MIN,
                    min_contour_points=CALDERA_MIN_CONTOUR_POINTS,
                )
            except Exception as e:
                self.caldera_contour_method = "fallback_level_ratio_0p8"
                print(f"[WARN] slope-guided caldera contour failed ({e}). Falling back to level_ratio=0.8.")
                self.caldera_contour = find_caldera_contour(self.dem, level_ratio=0.8)

            self.max_slope_index1, self.max_slope_index2 = find_opposite_slope_points(self.slope, self.caldera_contour)

            # Caldera distances (span)
            self.distance_meters_caldera = distance_between_points(
                self.max_slope_index1[0], self.max_slope_index1[1],
                self.max_slope_index2[0], self.max_slope_index2[1],
                self.transform
            )
            self.distance_caldera_km = self.distance_meters_caldera * 1e-3

            # Aree
            self.area_base = calculate_area(self.base_contour, self.transform) * 1e-6
            self.area_caldera = calculate_area(self.caldera_contour, self.transform) * 1e-6

            # ----------------------------
            # Volumi EDIFICIO (INVARIATI)
            # ----------------------------
            z = self.dem[np.isfinite(self.dem)]
            self.h_max = float(np.percentile(z, 99) - np.percentile(z, 5))

            self.R1 = self.distance_meters_base / 2
            self.R2 = self.distance_meters_caldera / 2
            self.v = (1/3) * np.pi * self.h_max * (self.R1**2 + self.R2**2 + self.R1 * self.R2)
            self.v_km3 = self.v * 1e-9

            # ----------------------------
            # Caldera depth: DEM rim–floor (con fallback)
            # ----------------------------
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

                if depth_fb > depth_raw:
                    caldera_depth = caldera_depth_fb
                    self.caldera_fallback_used = True

            depth_m = float(caldera_depth.get("depth_ref_m_clamped", 0.0))
            self.caldera_depth_km = depth_m * 1e-3

            # ----------------------------
            # Caldera volume (Approx2): CYLINDER with elliptical base
            #   V = A_caldera * depth_DEM
            #   (A_caldera dal contorno, in m²)
            # ----------------------------
            A_caldera_m2 = float(self.area_caldera) * 1e6

            if depth_m <= 0:
                self.caldera_status = "non_depressive_or_complex"
                self.caldera_reason = "rim_below_floor"
                self.caldera_action = "not_computed"
                V_caldera_m3 = 0.0
            elif A_caldera_m2 <= 0:
                self.caldera_status = "non_depressive_or_complex"
                self.caldera_reason = "zero_area"
                self.caldera_action = "not_computed"
                V_caldera_m3 = 0.0
            else:
                self.caldera_status = "depressive"
                self.caldera_reason = None
                self.caldera_action = "computed"
                V_caldera_m3 = float(A_caldera_m2 * depth_m)

            V_effective_m3 = float(self.v - V_caldera_m3)

            # QA fields (da caldera_depth)
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

            # Mantieni variabili storiche (usate in UI/metrics)
            self.r_caldera_km = self.R2 * 1e-3
            self.v_caldera = float(V_caldera_m3) * 1e-9     # km³
            self.v_volcano = float(V_effective_m3) * 1e-9   # km³

            # Descrizioni
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

            # Output risultati: riga caldera con N/A
            if self.caldera_status == "depressive":
                caldera_line_gui = f"Caldera volume (cylinder): {self.v_caldera:.3e} km³"
                caldera_line_pdf = caldera_line_gui
            else:
                reason = self.caldera_reason or "complex"
                caldera_line_gui = f"Caldera volume: N/A (complex/non-depressive: {reason})"
                caldera_line_pdf = f"Caldera volume: N/A ({reason})"

            depth_line_gui = (
                f"Caldera depth (DEM rim–floor): {self.caldera_depth_km:.2f} km"
                if self.caldera_status == "depressive" else
                f"Caldera depth (DEM rim–floor): N/A"
            )
            depth_line_pdf = depth_line_gui

            self.results_text = (
                f"Base area of the volcano: {self.area_base:.2f} km²\n"
                f"Base span (Distance between opposite points of the base): {self.distance_base_km:.2f} km\n"
                f"Caldera area of the volcano: {self.area_caldera:.2f} km²\n"
                f"Caldera span (Distance between opposite points of the caldera): {self.distance_caldera_km:.2f} km\n"
                f"{depth_line_gui}\n"
                f"Total volume of the volcanic edifice (elliptical frustum): {self.v_km3:.2f} km³\n"
                f"{caldera_line_gui}\n"
                f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
            )

            self.results_list = [
                f"Base area of the volcano: {self.area_base:.2f} km²",
                f"Base span (Distance between opposite points of the base): {self.distance_base_km:.2f} km",
                f"Caldera area of the volcano: {self.area_caldera:.2f} km²",
                f"Caldera span (Distance between opposite points of the caldera): {self.distance_caldera_km:.2f} km",
                f"{depth_line_pdf}",
                f"Total volume of the volcanic edifice (elliptical frustum): {self.v_km3:.2f} km³",
                f"{caldera_line_pdf}",
                f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
            ]
        except Exception as e:
            QMessageBox.critical(self, "Calculation Error", f"An error occurred during calculation: {e}")

    # ----- overview + payload (overview NON va nel PDF) -----
    def _save_overview_png(self):
        try:
            out_png = os.path.join(self.out_dir, "elliptical_approx2_overview.png")
            self.figure.savefig(out_png, dpi=150)  # no bbox_inches='tight'
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

        images.append(f"/outputs/{self.process_id}/elliptical_approx2_overview.png")
        payload = {"result": self.results_text, "images": images}
        print(json.dumps(payload), flush=True)

    # ----- doppietta finale con gabbia delle doppiette buone -----
    def _save_final_doublet_png(self, out_path):
        from mpl_toolkits.axes_grid1 import make_axes_locatable
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

        FIG_W, FIG_H = 14.5, 5.5
        fig = plt.figure(figsize=(FIG_W, FIG_H))
        gs = gridspec.GridSpec(1, 3, figure=fig, width_ratios=[1.0, 0.08, 1.0], wspace=0.15)

        # Sinistra: BASE
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

        # Spacer
        fig.add_subplot(gs[0, 1]).axis('off')

        # Destra: CALDERA
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
        cbar2.ax.yaxis.set_ticks_position('right')
        cbar2.ax.yaxis.set_label_position('right')
        cbar2.ax.tick_params(labelsize=9, pad=1)
        cbar2.ax.yaxis.labelpad = 2

        leg2 = ax2.legend(
            handles=[s1, s2, cc],
            labels=['Max Slope 1', 'Max Slope 2', 'Caldera Contour'],
            loc='upper right',
            frameon=True
        )
        leg2.get_frame().set_alpha(0.7)
        leg2.get_frame().set_facecolor('white')
        leg2.get_frame().set_edgecolor('black')

        fig.savefig(out_path, dpi=170)  # no bbox_inches='tight'
        plt.close(fig)

        if not os.path.exists(out_path):
            raise RuntimeError(f"Doublet not saved: {out_path}")

    # ----- raccolta immagini per PDF (niente overview!) -----
    def _collect_report_images(self):
        """
        Raccoglie immagini dal manifest, rimuove SOLO le triplette.
        NON inserisce l’overview (evita “tripla” nel PDF).
        """
        paths, captions = [], []

        outputs_dir, manifest_path = _find_manifest()
        if manifest_path:
            manifest_imgs = _load_manifest_images(manifest_path)
            paths = _normalize_and_filter_paths(manifest_imgs, base_dir=outputs_dir)
            paths = _remove_triplets(paths)

        captions = [None] * len(paths)
        return paths, captions

    # ----- UI -----
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
        PDF finale:
          - immagini dal manifest (senza triplette)
          - + DOPPIETTA finale
          - fail-safe se vuoto
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

            # 1) Salva la doppietta accanto al PDF
            out_dir_for_doublet = os.path.dirname(file_path) if os.path.dirname(file_path) else os.getcwd()
            doublet_png = os.path.join(out_dir_for_doublet, "final_doublet_base_vs_caldera.png")
            self._save_final_doublet_png(doublet_png)

            # 2) immagini dal manifest (senza triplette)
            image_paths, captions = self._collect_report_images()

            # 3) aggiungi doppietta
            if os.path.exists(doublet_png):
                image_paths.append(doublet_png)
                captions.append(None)

            # 4) fail-safe
            if not image_paths:
                image_paths = [doublet_png]
                captions = [None]

            print("[INFO] Images in PDF (count={}):".format(len(image_paths)))
            for p in image_paths:
                print("   -", p)

            # 5) genera PDF
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

    # ### Download PNG/JPG ###
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

    # =========================
    # ✅ METRICS
    # =========================
    def _build_metrics_dict(self) -> dict:
        crs = self.meta.get("crs")
        res = self.meta.get("res")
        nodata = self.meta.get("nodata")

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
        sanity_A_over_D2 = (A_base_m2 / (D_base ** 2)) if D_base > 0 else None
        eq_height_V_over_A = (V_total_m3 / A_base_m2) if A_base_m2 > 0 else None

        denom_cone = (1.0/3.0) * A_base_m2 * h_used
        cone_ratio = (V_total_m3 / denom_cone) if (denom_cone and denom_cone > 0) else None

        base_p1_rc = [int(self.base_point1[0]), int(self.base_point1[1])]
        base_p2_rc = [int(self.base_point2[0]), int(self.base_point2[1])]
        cal_p1_rc = [int(self.max_slope_index1[0]), int(self.max_slope_index1[1])]
        cal_p2_rc = [int(self.max_slope_index2[0]), int(self.max_slope_index2[1])]

        metrics = {
            "meta": {
                "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
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
                    "caldera_contour_method": getattr(self, "caldera_contour_method", "unknown"),
                    "caldera_rim_slope_top_percent": float(CALDERA_RIM_SLOPE_TOP_PERCENT),
                    "caldera_rim_elev_percentile": float(CALDERA_RIM_ELEV_PERCENTILE),
                    "caldera_min_area_ratio": float(CALDERA_MIN_AREA_RATIO),
                    "caldera_max_area_ratio": float(CALDERA_MAX_AREA_RATIO),
                    "caldera_inside_frac_min": float(CALDERA_INSIDE_FRAC_MIN),
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

            "height_model": {
                "method": "p99_minus_p05",
                "p99_minus_p05_m": h_used
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

            "geometry": {
                "base_points": {"p1_rc": base_p1_rc, "p2_rc": base_p2_rc},
                "caldera_points": {"p1_rc": cal_p1_rc, "p2_rc": cal_p2_rc},
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
            w.writerow(["metric", "value"])
            for label, value in human_rows:
                w.writerow([label, value])

        print(f"[INFO] metrics written: {json_path}")
        print(f"[INFO] metrics written: {csv_path}")

    def export_metrics(self):
        """
        Pulsante GUI: esporta nella cartella scelta dall'utente
        SIA <basename>.json (completo) SIA <basename>.csv (umano verticale).
        """
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

# ### Entry Point ###
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python EllipticalVolcano_Approx2.py <dem_file_path> [original_file_name]")
        sys.exit(1)

    input_dem_path = sys.argv[1]
    dem_file_path = input_dem_path
    original_file_name = sys.argv[2] if len(sys.argv) > 2 else "Unknown"

    # ------------------------------------------------------------
    # FORZA dem_working.tif (metrico) se disponibile
    # 1) priorità: outputs/<PROCESS_ID>/dem_working.tif
    # 2) fallback: dem_working.tif più recente in outputs/*
    # ------------------------------------------------------------
    base_outputs = os.path.join(_script_dir(), "outputs")
    chosen = None

    pid = os.environ.get("PROCESS_ID")
    if pid:
        candidate = os.path.join(base_outputs, pid, "dem_working.tif")
        if os.path.exists(candidate):
            chosen = candidate
            print(f"[DEBUG] Using dem_working from PROCESS_ID: {chosen}")

    if chosen is None and os.path.isdir(base_outputs):
        candidates = []
        for name in os.listdir(base_outputs):
            p = os.path.join(base_outputs, name, "dem_working.tif")
            if os.path.exists(p):
                candidates.append(p)
        if candidates:
            candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            chosen = candidates[0]
            print(f"[DEBUG] Using most recent dem_working: {chosen}")

    if chosen is not None:
        dem_file_path = chosen
    else:
        print("[ERROR] dem_working.tif not found in outputs/. "
              "Refusing to compute on original DEM to avoid bogus 0.00 values.")
        sys.exit(1)

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
            if (crs is None) or crs.is_geographic:
                print(f"[ERROR] DEM CRS is not metric (CRS={crs}). "
                      f"Refusing to compute because results would collapse to ~0.")
                sys.exit(1)
    except Exception as e:
        print(f"Error opening DEM file: {e}")
        sys.exit(1)

    meta = {
        "process_id": os.environ.get("PROCESS_ID"),
        "input_dem_path": input_dem_path,
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