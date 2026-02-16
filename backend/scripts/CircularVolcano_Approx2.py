# CircularVolcano_Approx2.py
# ——————————————————————————————————————————————————————————
# Approx2: mantiene i calcoli esistenti.
# Headless-friendly (come Approx1):
# - usa OUTPUTS_DIR/<PID> come output (non scripts/outputs)
# - in HEADLESS=1: niente PyQt, salva SEMPRE:
#     * metrics.json
#     * metrics.csv
#     * volume_results.json          ✅ (richiesto dalla UI)
#     * final_doublet_base_vs_caldera.png (+ append a analysis_images.json se presente)
#
# NOTE:
# - Su Linux/Docker NON forzare PROJ_LIB a pyproj (causa mismatch proj.db).
# - Su Windows è ok forzare PROJ_LIB a pyproj per evitare clash PostGIS/OSGeo.

import sys
import os
import csv
import time
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

# Matplotlib backend for headless
if HEADLESS:
    os.environ.setdefault("MPLBACKEND", "Agg")
    try:
        import matplotlib
        matplotlib.use("Agg")
    except Exception:
        pass

import numpy as np
import rasterio
from scipy.ndimage import sobel
from skimage import measure

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

# Matplotlib canvas only if GUI
if not HEADLESS:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
else:
    FigureCanvas = None

from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib import gridspec
from mpl_toolkits.axes_grid1 import make_axes_locatable

import pdf_generator

METRICS_BASENAME = "metrics"  # ✅ allineato ad Approx1 (metrics.json / metrics.csv)

# -------------------- OUTPUTS BASE: UNICA FONTE DI VERITÀ (come Approx1) --------------------

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
    return os.environ.get("PROCESS_ID") or f"local_{int(time.time())}"

def _ensure_outputs_dir(process_id: str) -> str:
    base = _outputs_base_dir()
    out_dir = os.path.join(base, process_id)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def _public_path(process_id: str, filename: str) -> str:
    return f"/outputs/{process_id}/{filename}"

def _find_manifest_path(process_id: str) -> str:
    out_dir = _ensure_outputs_dir(process_id)
    mp = os.path.join(out_dir, "analysis_images.json")
    return mp if os.path.exists(mp) else mp  # return path even if not exists (caller handles)

def _load_manifest_public_images(manifest_path: str):
    try:
        if not os.path.exists(manifest_path):
            return []
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        imgs = data.get("images") or []
        out = []
        for it in imgs:
            if isinstance(it, dict):
                pub = it.get("public_path") or it.get("url")
                fn = it.get("filename") or it.get("name") or it.get("file")
                if pub:
                    out.append(pub)
                elif fn:
                    out.append(fn)
            elif isinstance(it, str):
                out.append(it)
        return out
    except Exception as e:
        print(f"[WARN] Could not read manifest '{manifest_path}': {e}")
        return []

# -------------------- ANALYSIS FUNCTIONS (calcoli invariati) --------------------

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
    max_index1 = int(np.argmax(contour_slopes))
    max_slope_index1 = tuple(contour[max_index1])
    opposite_index = (max_index1 + len(contour) // 2) % len(contour)
    max_slope_index2 = tuple(contour[opposite_index])
    return max_slope_index1, max_slope_index2

# ========== Caldera depth-integrated helpers (identici a Approx1 / tuo codice) ==========

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
    inner = binary_dilation(mask, iterations=offset_px) if offset_px > 0 else mask
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

# ========== Metrics helpers (come Approx1) ==========

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

    ("Model inputs", "Height used by model (h_max)", "morphometrics.h_max_m", "m"),

    ("Volumes", "Total edifice volume", "volumes.V_total_m3", "m³"),
    ("Volumes", "Caldera volume", "volumes.V_caldera_m3", "m³"),
    ("Volumes", "Effective edifice volume", "volumes.V_effective_m3", "m³"),
]

def metrics_to_human_rows(metrics: dict):
    rows = []
    for section, label, path, unit in HUMAN_FIELDS:
        v = _get_by_path(metrics, path)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        rows.append((section, label, v, unit))
    return rows

# ===================== Main App =====================

class VolumeAnalysisApp(QMainWindow):
    def __init__(self, dem, transform, meta=None, original_file_name="Unknown"):
        super().__init__()
        if hasattr(self, "setWindowTitle"):
            self.setWindowTitle('Volcano Volume Analysis')

        self.dem = dem
        self.transform = transform
        self.original_file_name = original_file_name
        self.meta = meta or {}

        self.process_id = self.meta.get("process_id") or _resolve_process_id()
        self.out_dir = _ensure_outputs_dir(self.process_id)

        # ✅ calcoli invariati
        self.calculate_results()

        # GUI only if not headless
        if not HEADLESS:
            self.initUI()
            try:
                self._save_overview_png()
            except Exception as e:
                print(f"[PY VOL WARN] overview save failed: {e}")
        else:
            print("[DEBUG] HEADLESS=1 -> GUI disabled (no Qt windows).")

        # ✅ artifacts come Approx1 (sempre)
        self._write_all_artifacts()

        # ✅ stdout payload (server.js lo può parsare)
        try:
            self._emit_stdout_payload()
        except Exception as e:
            print(f"[WARN] stdout payload failed: {e}")

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
        # ======= TUOI CALCOLI (INVARIATI) =======
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
        self.caldera_contour = find_caldera_contour(self.dem, level_ratio=0.8)
        self.max_slope_index1, self.max_slope_index2 = find_opposite_slope_points(self.slope, self.caldera_contour)

        self.distance_meters_caldera = distance_between_points(
            self.max_slope_index1[0], self.max_slope_index1[1],
            self.max_slope_index2[0], self.max_slope_index2[1],
            self.transform
        )
        self.distance_caldera_km = self.distance_meters_caldera * 1e-3

        self.area_base_m2 = float(calculate_area(self.base_contour, self.transform))
        self.area_caldera_m2 = float(calculate_area(self.caldera_contour, self.transform))
        self.area_base = self.area_base_m2 * 1e-6
        self.area_caldera = self.area_caldera_m2 * 1e-6

        self.h_max = robust_height_p99_minus_p05(self.dem, self.base_contour, nodata=nodata)
        self.R1 = self.distance_meters_base / 2.0
        self.R2 = self.distance_meters_caldera / 2.0

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

        self.v_caldera = (V_caldera_m3 * 1e-9) if (self.caldera_status == "depressive") else None
        self.v_volcano = (V_effective_m3 * 1e-9)

        if getattr(self, "caldera_status", None) == "depressive":
            caldera_line_gui = f"Caldera volume: {self.v_caldera:.3e} km³"
        else:
            reason = getattr(self, "caldera_reason", "complex")
            caldera_line_gui = f"Caldera volume: N/A (complex/non-depressive: {reason})"

        self.results_text = (
            f"Base area of the volcano: {self.area_base:.2f} km²\n"
            f"Base width (Distance between opposite points of the base): {self.distance_base_km:.2f} km\n"
            f"Caldera area of the volcano: {self.area_caldera:.2f} km²\n"
            f"Caldera width (Distance between opposite points of the caldera): {self.distance_caldera_km:.2f} km\n"
            f"Total volume of the volcanic edifice: {self.v_km3:.2f} km³\n"
            f"{caldera_line_gui}\n"
            f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
        )

    def update_display(self):
        # GUI-only draw
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
        P_base = float(contour_perimeter_m(self.base_contour, self.transform))
        P_caldera = float(contour_perimeter_m(self.caldera_contour, self.transform))
        D_base = float(self.distance_meters_base)
        D_caldera = float(self.distance_meters_caldera)
        R_base = float(self.R1)
        R_caldera = float(self.R2)
        R_eq_base = math.sqrt(A_base_m2 / math.pi) if A_base_m2 > 0 else 0.0
        R_eq_caldera = math.sqrt(A_caldera_m2 / math.pi) if A_caldera_m2 > 0 else 0.0

        h_max = float(self.h_max)
        V_total_m3 = float(self.v)
        V_caldera_m3 = float(self.v_caldera * 1e9) if self.v_caldera is not None else 0.0
        V_eff_m3 = float(self.v_volcano * 1e9)

        metrics = {
            "meta": {
                "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                "process_id": self.process_id,
                "original_file_name": os.environ.get("ORIGINAL_FILE_NAME") or self.meta.get("original_file_name"),
                "original_file_stem": os.environ.get("ORIGINAL_FILE_STEM") or self.meta.get("original_file_stem"),
                "input_dem_path": self.meta.get("input_dem_path"),
                "working_dem_path": self.meta.get("working_dem_path"),
                "crs": str(crs) if crs is not None else None,
                "res": list(res) if res is not None else None,
                "nodata": nodata,
                "params": {"base_elevation_ratio": 0.05, "caldera_level_ratio": 0.8}
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
                "h_max_m": h_max,
            },
            "volumes": {
                "V_total_m3": V_total_m3,
                "V_caldera_m3": V_caldera_m3,
                "V_effective_m3": V_eff_m3,
            },
        }
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
            for section, label, value, unit in human_rows:
                w.writerow([section, label, value, unit])

        print(f"[INFO] metrics written: {json_path}")
        print(f"[INFO] metrics written: {csv_path}")

    def _append_doublet_to_analysis_manifest(self, doublet_abs_path: str):
        mp = os.path.join(self.out_dir, "analysis_images.json")
        filename = os.path.basename(doublet_abs_path)

        entry = {
            "filename": filename,
            "abs_path": doublet_abs_path,
            "public_path": _public_path(self.process_id, filename),
            "titles": ["Base vs Caldera"],
            "units": ["m"],
            "descriptions": ["Final comparison panel: base contour and caldera contour with selected opposite points."]
        }

        data = None
        if os.path.exists(mp):
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = None

        if not isinstance(data, dict):
            data = {
                "processId": self.process_id,
                "source": "complete_dem_analysis",
                "original_file_name": os.environ.get("ORIGINAL_FILE_NAME") or self.meta.get("original_file_name"),
                "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "images": []
            }

        imgs = data.get("images")
        if not isinstance(imgs, list):
            imgs = []
        already = any((isinstance(it, dict) and it.get("filename") == filename) for it in imgs)
        if not already:
            imgs.append(entry)
        data["images"] = imgs

        with open(mp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"[INFO] analysis_images.json updated (doublet appended): {mp}")

    def _write_volume_results_json(self):
        # km/ km²/ km³
        distance_base_km = float(self.distance_meters_base) * 1e-3
        distance_caldera_km = float(self.distance_meters_caldera) * 1e-3
        area_base_km2 = float(self.area_base_m2) * 1e-6
        area_caldera_km2 = float(self.area_caldera_m2) * 1e-6
        v_tot_km3 = float(self.v) * 1e-9
        v_cal_km3 = (float(self.v_caldera) if self.v_caldera is not None else None)
        v_eff_km3 = float(self.v_volcano)

        payload = {
            "processId": self.process_id,
            "status": "completed",
            "moduleKey": "circular_approx2",
            "result": {
                "base_area_km2": area_base_km2,
                "base_width_km": distance_base_km,
                "caldera_area_km2": area_caldera_km2,
                "caldera_width_km": distance_caldera_km,
                "total_volume_km3": v_tot_km3,
                "caldera_volume_km3": v_cal_km3,
                "effective_volume_km3": v_eff_km3,
                "h_max_m": float(self.h_max),
                "pixel_size_m": float(self.meta.get("res")[0]) if self.meta.get("res") else None,
            },
            # ✅ per il viewer: nomi file (server normalizza a /outputs/<pid>/...)
            "images": [
                "final_doublet_base_vs_caldera.png"
            ],
            "links": {
                "metrics_json": _public_path(self.process_id, "metrics.json"),
                "metrics_csv": _public_path(self.process_id, "metrics.csv"),
                "analysis_images": _public_path(self.process_id, "analysis_images.json"),
                "final_doublet": _public_path(self.process_id, "final_doublet_base_vs_caldera.png"),
            }
        }

        out_path = os.path.join(self.out_dir, "volume_results.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        print(f"[INFO] volume_results written: {out_path}")

        if HEADLESS:
            # utile se server.js fa JSON.parse(stdout)
            try:
                print(json.dumps(payload, ensure_ascii=False), flush=True)
            except Exception:
                pass

    def _write_all_artifacts(self):
        # 1) metrics
        self._write_metrics_files()

        # 2) final doublet (sempre)
        doublet_path = os.path.join(self.out_dir, "final_doublet_base_vs_caldera.png")
        try:
            self._save_final_doublet_png(doublet_path)
            self._append_doublet_to_analysis_manifest(doublet_path)
        except Exception as e:
            print(f"[WARN] Could not save/append final doublet: {e}")

        # 3) volume_results.json (sempre)
        self._write_volume_results_json()

    # ----- overview png (solo GUI) -----
    def _save_overview_png(self):
        out_png = os.path.join(self.out_dir, "circular_approx2_overview.png")
        self.figure.savefig(out_png, dpi=150)
        print(f"[PY VOL {self.process_id}] saved {out_png}")

    # ----- stdout payload -----
    def _emit_stdout_payload(self):
        # includi anche immagini del manifest (prima UX) + la doppietta
        mp = os.path.join(self.out_dir, "analysis_images.json")
        manifest_imgs = _load_manifest_public_images(mp)

        images = []
        # normalizza: se sono già /outputs/... ok, se sono filename li lasciamo (server normalizza)
        for it in manifest_imgs:
            images.append(it)

        # aggiungi sempre la doppietta finale (filename)
        images.append("final_doublet_base_vs_caldera.png")

        payload = {
            "processId": self.process_id,
            "status": "completed",
            "moduleKey": "circular_approx2",
            "result": {
                "base_area_km2": float(self.area_base_m2) * 1e-6,
                "base_width_km": float(self.distance_meters_base) * 1e-3,
                "caldera_area_km2": float(self.area_caldera_m2) * 1e-6,
                "caldera_width_km": float(self.distance_meters_caldera) * 1e-3,
                "total_volume_km3": float(self.v) * 1e-9,
                "caldera_volume_km3": float(self.v_caldera) if self.v_caldera is not None else None,
                "effective_volume_km3": float(self.v_volcano),
                "h_max_m": float(self.h_max),
                "pixel_size_m": float(self.meta.get("res")[0]) if self.meta.get("res") else None,
            },
            "images": images
        }
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    # ---- GUI-only actions ----
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
            mp = os.path.join(self.out_dir, "analysis_images.json")
            if os.path.exists(mp):
                try:
                    with open(mp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for it in (data.get("images") or []):
                        p = it.get("abs_path")
                        if p and os.path.exists(p):
                            # rimuovi solo triplet_
                            if "triplet_" not in os.path.basename(p).lower():
                                image_paths.append(p)
                except Exception:
                    pass

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

# ===================== Entry point =====================

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python CircularVolcano_Approx2.py <dem_file_path> [original_file_name]")
        sys.exit(1)

    input_dem_path = sys.argv[1]
    original_file_name = sys.argv[2] if len(sys.argv) > 2 else "Unknown"

    process_id = _resolve_process_id()
    out_dir = _ensure_outputs_dir(process_id)

    # Prefer working dem if exists
    candidate = os.path.join(out_dir, "dem_working.tif")
    dem_file_path = candidate if os.path.exists(candidate) else input_dem_path

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
        _ = VolumeAnalysisApp(dem, transform, meta=meta, original_file_name=original_file_name)
        print("[INFO] Headless run completed (no GUI).")
        sys.exit(0)

    app = QApplication(sys.argv)
    ex = VolumeAnalysisApp(dem, transform, meta=meta, original_file_name=original_file_name)
    ex.showMaximized()
    sys.exit(app.exec_())
