# CircularVolcano_Approx1.py
# ——————————————————————————————————————————————————————————
# Modulo "volume" che:
# 1) calcola i risultati (base, caldera, distanze, volumi)
# 2) mostra la GUI con 3 pannelli (DEM, Base opposta, Caldera max slope)
# 3) genera un PDF che include:
#    - DEM + doppiette dal manifest di complete_dem_analysis (se disponibile)
#    - rimuove SOLO le triplette finali
#    - aggiunge in coda la nuova DOPPIETTA "Base vs Caldera"
# Fail-safe: se il manifest manca, il PDF contiene almeno la doppietta.

import sys
import os
import csv
import datetime
import math

# ================= PROJ / EPSG FIX (Windows + PostGIS conflicts) =================
# Se hai PostGIS/PostgreSQL installato, spesso mette in PATH un PROJ diverso.
# Rasterio/GDAL allora legge un proj.db incompatibile e "EPSG unknown".
# Qui forziamo PROJ_LIB verso il proj.db di pyproj (quello corretto per l'ambiente Python).
def _force_proj_lib_to_pyproj():
    try:
        from pyproj import datadir
        proj_dir = datadir.get_data_dir()  # tipicamente .../pyproj/proj_dir/share/proj
        if proj_dir and os.path.isdir(proj_dir):
            os.environ["PROJ_LIB"] = proj_dir
            print(f"[DEBUG] PROJ_LIB forced to pyproj: {proj_dir}")
    except Exception as e:
        print(f"[WARN] Could not force PROJ_LIB via pyproj: {e}")

_force_proj_lib_to_pyproj()
# ================================================================================

import json
import numpy as np
import rasterio  # For reading DEM files in .tif format
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.crs import CRS
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
from mpl_toolkits.axes_grid1 import make_axes_locatable  # <-- PER cbar co-alte accanto all'immagine

# Generatore PDF (non modificato)
import pdf_generator


# ———————— Funzioni di analisi di base ——————————

def find_lowest_base_contour(matrix, base_elevation_ratio=0.05):
    """
    Ritorna: (base_contour, base_level_m)
    base_level_m è la quota usata per estrarre il contorno base.
    """
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
    slope = np.hypot(dx, dy)
    return slope

def find_caldera_contour(matrix, level_ratio=0.8):
    """
    Ritorna: (caldera_contour, caldera_level_m)
    caldera_level_m è la quota usata per estrarre il contorno caldera.
    """
    contour_level = float(matrix.max() * level_ratio)
    contours = measure.find_contours(matrix, contour_level)
    if len(contours) == 0:
        raise ValueError("No contours found for the given level ratio.")
    main_contour = max(contours, key=len)
    return main_contour, contour_level

def find_opposite_slope_points(slope_matrix, contour):
    contour = np.round(contour).astype(int)
    # limiti immagine
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

    # Shoelace (chiusura implicita con roll)
    area = 0.5 * np.abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1)))
    return float(area)

def _utm_epsg_from_lonlat(lon: float, lat: float) -> int:
    # UTM zone
    zone = int((lon + 180) // 6) + 1
    if lat >= 0:
        return 32600 + zone  # WGS84 / UTM North
    return 32700 + zone      # WGS84 / UTM South

def ensure_metric_dem(dem_path: str) -> str:
    """
    Se il DEM è geografico (gradi), lo riproietta in UTM locale e salva un GeoTIFF working.
    Ritorna sempre il path del DEM da usare per i calcoli (originale o working).
    """
    with rasterio.open(dem_path) as src:
        src_crs = src.crs
        if src_crs is None:
            raise RuntimeError("Input DEM has no CRS. Please define CRS before running volume modules.")

        # Se è già metrico (proiettato), ok
        if not src_crs.is_geographic:
            return dem_path

        # Se è geografico: calcola centro e scegli UTM
        b = src.bounds
        lon_c = (b.left + b.right) / 2.0
        lat_c = (b.bottom + b.top) / 2.0
        epsg = _utm_epsg_from_lonlat(lon_c, lat_c)
        dst_crs = CRS.from_epsg(epsg)

        # Output working in outputs/<PROCESS_ID>/dem_working.tif (stesso naming della pipeline)
        base_out = os.path.join(_script_dir(), "outputs")
        pid = os.environ.get("PROCESS_ID") or "local"
        out_dir = os.path.join(base_out, pid)
        os.makedirs(out_dir, exist_ok=True)
        working_path = os.path.join(out_dir, "dem_working.tif")

        # Calcola transform/shape di destinazione
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

        # Resampling: bilinear per float, nearest per int
        resampling = Resampling.bilinear if str(src.dtypes[0]).startswith("float") else Resampling.nearest

        # Reproject banda 1
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

# ———————— Utility path/manifest ——————————

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))

def _resolve_process_id() -> str:
    pid = os.environ.get("PROCESS_ID")
    return pid if pid else f"local_{int(datetime.datetime.utcnow().timestamp())}"

def _ensure_outputs_dir(process_id: str) -> str:
    out_dir = os.path.join(_script_dir(), "outputs", process_id)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def _as_serializable(v):
    # per json (numpy -> python)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.ndarray,)):
        return v.tolist()
    if isinstance(v, (tuple, list)):
        return [ _as_serializable(x) for x in v ]
    if isinstance(v, dict):
        return {k: _as_serializable(val) for k, val in v.items()}
    return v

def flatten_to_kv(metrics: dict, parent_key: str = "") -> list:
    """
    Converte un dict annidato in lista di tuple (key_path, value) per CSV verticale.
    Esempio chiave: "morphometrics.A_base_m2"
    """
    rows = []

    def _walk(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _walk(v, f"{prefix}.{k}" if prefix else str(k))
        elif isinstance(obj, list):
            # liste: serializza come JSON string per compatibilità Excel
            rows.append((prefix, json.dumps(obj, ensure_ascii=False)))
        else:
            rows.append((prefix, obj))

    _walk(metrics, parent_key)
    return rows

def contour_perimeter_m(contour, transform) -> float:
    """
    Perimetro in metri del contorno (Nx2 row/col) proiettandolo in coordinate mappa via transform.
    """
    c = np.asarray(contour, dtype=float)
    if c.shape[0] < 2:
        return 0.0

    # converti tutti i punti in XY (centro pixel)
    rows = c[:, 0]
    cols = c[:, 1]
    xs = np.empty(len(c), dtype=float)
    ys = np.empty(len(c), dtype=float)
    for i in range(len(c)):
        xs[i], ys[i] = _pixel_to_map_xy(transform, rows[i], cols[i])

    # chiudi il poligono
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
    """Estrae dall'JSON la lista di immagini (preferendo abs_path)."""
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
    """Rimuove ogni triplet_*.png dalla lista."""
    return [p for p in paths if "triplet_" not in os.path.basename(p).lower()]

# =========================
# CSV "umano" (verticale)
# - NO duplicati km/km²/km³
# - include parametri geometrici usati nei volumi
# =========================
HUMAN_FIELDS = [
    ("meta.timestamp_utc", "Run timestamp (UTC)"),
    ("meta.process_id", "Run ID (process_id)"),
    ("meta.input_dem_path", "Input DEM path"),
    ("meta.working_dem_path", "Working DEM path"),
    ("meta.crs", "Working DEM CRS (EPSG/WKT)"),
    ("meta.res", "Pixel resolution (m) [x,y]"),
    ("meta.nodata", "NoData value"),

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

    # --- Geometry thresholds (if present in metrics) ---
    ("geometry.base_level_m", "Base contour level used (m)"),
    ("geometry.caldera_level_m", "Caldera contour level used (m)"),

    # --- Model descriptors (if present in metrics) ---
    ("volume_model.edifice_model", "Edifice volume model"),
    ("volume_model.caldera_model", "Caldera volume model"),
    ("volume_model.intermediate.V_frustum_m3", "Intermediate frustum-like volume (m³)"),

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
    for path, label in HUMAN_FIELDS:
        v = _get_by_path(metrics, path)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        rows.append((label, v))
    return rows


# ———————— App principale ——————————
class VolumeAnalysisApp(QMainWindow):
    def __init__(self, dem, transform, meta=None):
        super().__init__()
        self.setWindowTitle('Volcano Volume Analysis')
        self.dem = dem
        self.transform = transform
        self.meta = meta or {}

        # output dir “per-run”
        self.process_id = self.meta.get("process_id") or _resolve_process_id()
        self.out_dir = _ensure_outputs_dir(self.process_id)

        self.calculate_results()
        self.initUI()

        # salva SEMPRE (silenzioso) json+csv per QA/ML
        try:
            self._write_metrics_files()
        except Exception as e:
            print(f"[WARN] metrics export failed: {e}")

    def initUI(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)

        # Figura Matplotlib — constrained layout per gestione margini/cbar
        self.figure = Figure(figsize=(18, 14), constrained_layout=True)
        self.canvas = FigureCanvas(self.figure)
        main_layout.addWidget(self.canvas)

        # Bottoni
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
            # Calculate results and store for later use
            self.base_contour, self.base_level_m = find_lowest_base_contour(self.dem, base_elevation_ratio=0.05)
            self.base_point1, self.base_point2 = find_opposite_base_points(self.base_contour)

            self.slope = calculate_slope(self.dem)
            self.caldera_contour, self.caldera_level_m = find_caldera_contour(self.dem, level_ratio=0.8)
            self.max_slope_index1, self.max_slope_index2 = find_opposite_slope_points(self.slope, self.caldera_contour)

            # -------------------------
            # Distances (METERS via transform)
            # -------------------------
            pA1, pA2 = self.base_point1[0], self.base_point1[1]  # row, col
            pB1, pB2 = self.base_point2[0], self.base_point2[1]  # row, col
            distance_meters_base = distance_between_points(pA1, pA2, pB1, pB2, self.transform)

            pA1_slope, pA2_slope = self.max_slope_index1[0], self.max_slope_index1[1]  # row, col
            pB1_slope, pB2_slope = self.max_slope_index2[0], self.max_slope_index2[1]  # row, col
            distance_meters_caldera = distance_between_points(
                pA1_slope, pA2_slope,
                pB1_slope, pB2_slope,
                self.transform
            )

            # Salva come attributi (servono per metrics export)
            self.distance_meters_base = float(distance_meters_base)
            self.distance_meters_caldera = float(distance_meters_caldera)

            # Per GUI (km)
            distance_base_km = distance_meters_base * 1e-3
            distance_caldera_km = distance_meters_caldera * 1e-3
            self.distance_base_km = float(distance_base_km)
            self.distance_caldera_km = float(distance_caldera_km)

            # -------------------------
            # Areas (m² via transform)
            # -------------------------
            A_base_m2 = calculate_area(self.base_contour, self.transform)
            A_caldera_m2 = calculate_area(self.caldera_contour, self.transform)

            self.area_base_m2 = float(A_base_m2)
            self.area_caldera_m2 = float(A_caldera_m2)

            # Per GUI (km²)
            area_base_km2 = A_base_m2 * 1e-6
            area_caldera_km2 = A_caldera_m2 * 1e-6

            # Perimetri (m) (servono per circularity ecc.)
            self.perimeter_base_m = float(contour_perimeter_m(self.base_contour, self.transform))
            self.perimeter_caldera_m = float(contour_perimeter_m(self.caldera_contour, self.transform))

            # -------------------------
            # Volumes (SI: m³) + conversione per GUI
            # -------------------------
            h_max = float(np.max(self.dem))
            R1 = float(distance_meters_base / 2.0)      # base radius used
            R2 = float(distance_meters_caldera / 2.0)   # caldera radius used

            # Edifice model (your current "frustum-like" formulation)
            V_frustum_m3 = (1.0 / 3.0) * np.pi * h_max * (R1**2 + R2**2 + R1 * R2)

            # Caldera model (your current code): hemisphere
            V_caldera_m3 = (2.0 / 3.0) * np.pi * (R2**3)

            V_effective_m3 = V_frustum_m3 - V_caldera_m3

            # Salva attributi SI per metrics export
            self.h_max = float(h_max)
            self.R1 = float(R1)
            self.R2 = float(R2)
            self.V_frustum_m3 = float(V_frustum_m3)
            self.V_caldera_m3 = float(V_caldera_m3)
            self.V_effective_m3 = float(V_effective_m3)

            # Conversione per GUI (km³)
            v_km3 = V_frustum_m3 * 1e-9
            v_caldera_km3 = V_caldera_m3 * 1e-9
            v_eff_km3 = V_effective_m3 * 1e-9

            # Mantieni i nomi storici usati nella GUI
            self.v_km3 = float(v_km3)
            self.v_caldera_km3 = float(v_caldera_km3)
            self.v_volcano = float(v_eff_km3)

            # -------------------------
            # Results text (immutato come output: km, km², km³)
            # -------------------------
            self.results_text = (
                f"Base area of the volcano: {area_base_km2:.2f} km²\n"
                f"Base width (Distance between opposite points of the base): {distance_base_km:.2f} km\n"
                f"Caldera area of the volcano: {area_caldera_km2:.2f} km²\n"
                f"Caldera width (Distance between opposite points of the caldera): {distance_caldera_km:.2f} km\n"
                f"Total volume of the volcanic edifice: {v_km3:.2f} km³\n"
                f"Caldera volume: {v_caldera_km3:.2f} km³\n"
                f"Effective volume of the volcanic edifice: {v_eff_km3:.2f} km³"
            )

            self.results_list = [
                f"Base area of the volcano: {area_base_km2:.2f} km²",
                f"Base width (Distance between opposite points of the base): {distance_base_km:.2f} km",
                f"Caldera area of the volcano: {area_caldera_km2:.2f} km²",
                f"Caldera width (Distance between opposite points of the caldera): {distance_caldera_km:.2f} km",
                f"Total volume of the volcanic edifice: {v_km3:.2f} km³",
                f"Caldera volume: {v_caldera_km3:.2f} km³",
                f"Effective volume of the volcanic edifice: {v_eff_km3:.2f} km³"
            ]

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

        except Exception as e:
            QMessageBox.critical(self, "Calculation Error", f"An error occurred during calculation: {e}")

    def update_display(self):
        self.figure.clear()
        fig = self.figure

        # Margine esterno per non tagliare numeri/label della 3ª cbar
        fig.set_constrained_layout_pads(w_pad=0.12, h_pad=0.02, wspace=0.40, hspace=0.60)

        gs = gridspec.GridSpec(nrows=3, ncols=3, height_ratios=[4, 1, 1.5], figure=fig, wspace=0.4, hspace=0.6)

        # Pannello 1: DEM
        ax1 = fig.add_subplot(gs[0, 0])
        im1 = ax1.imshow(self.dem, cmap='terrain', origin='upper')
        # Colorbar accanto all'immagine: stessa altezza e vicina
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

        # Pannello 2: Base opposta
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
        pc, = ax2.plot(self.base_contour[:, 1], self.base_contour[:, 0], 'w-', linewidth=1, label="Base Contour")
        ax2.set_title("Opposite Points of the Volcano Base", fontsize=14, pad=20, y=1.02)
        ax2.axis('on')

        # Pannello 3: Caldera max slope
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
        cc, = ax3.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label="Caldera Contour")
        ax3.set_title("Opposite Maximum Slope Points on the Caldera", fontsize=14, pad=20, y=1.02)
        ax3.axis('on')

        # Legende e descrizioni
        la2 = fig.add_subplot(gs[1, 1]); la2.axis('off')
        la2.legend([p1, p2, pc], ['Base 1', 'Base 2', 'Base Contour'],
                   loc='center', frameon=True, edgecolor='black', facecolor='lightgray', ncol=3)

        la3 = fig.add_subplot(gs[1, 2]); la3.axis('off')
        la3.legend([s1, s2, cc], ['Max Slope 1', 'Max Slope 2', 'Caldera Contour'],
                   loc='center', frameon=True, edgecolor='black', facecolor='lightgray', ncol=3)

        la1 = fig.add_subplot(gs[1, 0]); la1.axis('off')
        da1 = fig.add_subplot(gs[2, 0]); da1.axis('off')

        da2 = fig.add_subplot(gs[2, 1]); da2.axis('off')
        da2.text(0.5, 1.35, self.description_base, fontsize=10, ha='center', va='center',
                 bbox=dict(boxstyle="round,pad=0.5", edgecolor="black", facecolor="white"),
                 wrap=True, transform=da2.transAxes)

        da3 = fig.add_subplot(gs[2, 2]); da3.axis('off')
        da3.text(0.5, 1.5, self.description_slope, fontsize=10, ha='center', va='center',
                 bbox=dict(boxstyle="round,pad=0.5", edgecolor="black", facecolor="white"),
                 wrap=True, transform=da3.transAxes)

        # NIENTE fig.subplots_adjust(...): interferisce con constrained_layout
        self.canvas.draw()

    # ———— NUOVO: salva la DOPPIETTA finale (Base vs Caldera) ————
    def _save_final_doublet_png(self, out_path):
        """
        Salva una DOPPIETTA 1x2 (base vs caldera) con la STESSA gabbia
        delle doppiette precedenti: 14.5x5.5, spacer centrale, colorbar 4.6%.
        """
        from mpl_toolkits.axes_grid1 import make_axes_locatable
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

        # --- stessa gabbia delle doppiette "buone" ---
        FIG_W, FIG_H = 14.5, 5.5
        fig = plt.figure(figsize=(FIG_W, FIG_H))
        gs = gridspec.GridSpec(
            1, 3, figure=fig,
            width_ratios=[1.0, 0.08, 1.0],   # sinistra | SPACER | destra
            wspace=0.15
        )

        # === Sinistra: BASE ===
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
        cbar1 = fig.colorbar(im1, cax=cax1)
        cbar1.set_label("Elevation (m)", rotation=90)
        cbar1.ax.yaxis.set_ticks_position('right')
        cbar1.ax.yaxis.set_label_position('right')
        cbar1.ax.tick_params(labelsize=9, pad=1)
        cbar1.ax.yaxis.labelpad = 2

        # 🔹 Legenda nel pannello sinistro (in alto a destra)
        leg1 = ax1.legend(
            handles=[p1, p2, pc],
            labels=['Base 1', 'Base 2', 'Base Contour'],
            loc='upper right',
            frameon=True
        )
        leg1.get_frame().set_alpha(0.7)
        leg1.get_frame().set_facecolor('white')
        leg1.get_frame().set_edgecolor('black')

        # === Spacer centrale ===
        ax_spacer = fig.add_subplot(gs[0, 1])
        ax_spacer.axis('off')

        # === Destra: CALDERA ===
        ax2 = fig.add_subplot(gs[0, 2])
        im2 = ax2.imshow(self.dem, cmap='terrain', origin='upper',
                         interpolation='nearest', resample=False)
        cc, = ax2.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label='Caldera Contour')
        s1, = ax2.plot(self.max_slope_index1[1], self.max_slope_index1[0], 'ro', markersize=8, label='Max Slope 1')
        s2, = ax2.plot(self.max_slope_index2[1], self.max_slope_index2[0], 'yo', markersize=8, label='Max Slope 2')
        ax2.set_title("Opposite Maximum Slope Points on the Caldera", pad=8, fontsize=12)
        ax2.set_aspect('equal', adjustable='box')
        div2 = make_axes_locatable(ax2)
        cax2 = div2.append_axes("right", size="4.6%", pad=0.10)
        cbar2 = fig.colorbar(im2, cax=cax2)
        cbar2.set_label("Elevation (m)", rotation=90)
        cbar2.ax.yaxis.set_ticks_position('right')
        cbar2.ax.yaxis.set_label_position('right')
        cbar2.ax.tick_params(labelsize=9, pad=1)
        cbar2.ax.yaxis.labelpad = 2

        # 🔹 Legenda nel pannello destro (in alto a destra)
        leg2 = ax2.legend(
            handles=[s1, s2, cc],
            labels=['Max Slope 1', 'Max Slope 2', 'Caldera Contour'],
            loc='upper right',
            frameon=True
        )
        leg2.get_frame().set_alpha(0.7)
        leg2.get_frame().set_facecolor('white')
        leg2.get_frame().set_edgecolor('black')

        # NIENTE subplots_adjust / suptitle / bbox_inches='tight'
        fig.savefig(out_path, dpi=170)
        plt.close(fig)

        if not os.path.exists(out_path):
            raise RuntimeError(f"Doublet not saved: {out_path}")

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
        Genera il PDF finale:
        - prova a caricare DEM + doppiette dal manifest (se presente)
        - rimuove SOLO le triplette
        - aggiunge in coda la nuova DOPPIETTA
        - fail-safe: se lista vuota, usa almeno la doppietta
        """
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Results As", "", "PDF Files (*.pdf)", options=options)
        if not file_path:
            return
        if not file_path.lower().endswith('.pdf'):
            file_path += '.pdf'

        try:
            title = "Calculation Results - Circular Base, Approximation Type 1"

            # 1) salva SEMPRE la doppietta accanto al PDF
            out_dir_for_doublet = os.path.dirname(file_path) if os.path.dirname(file_path) else os.getcwd()
            doublet_png = os.path.join(out_dir_for_doublet, "final_doublet_base_vs_caldera.png")
            self._save_final_doublet_png(doublet_png)

            # 2) carica immagini dal manifest (se c'è)
            image_paths = []
            outputs_dir, manifest_path = _find_manifest()
            if manifest_path:
                manifest_imgs = _load_manifest_images(manifest_path)
                image_paths = _normalize_and_filter_paths(manifest_imgs, base_dir=outputs_dir)
                image_paths = _remove_triplets(image_paths)  # togli solo triplette
                print(f"[INFO] Loaded {len(image_paths)} images from manifest (triplets removed).")
            else:
                print("[WARN] Manifest not found. Proceeding with doublet only if needed.")

            # 3) aggiungi SEMPRE la nuova doppietta
            if os.path.exists(doublet_png):
                image_paths.append(doublet_png)
            else:
                print(f"[WARN] Doublet PNG missing unexpectedly: {doublet_png}")

            # 4) fail-safe: se lista vuota, usa almeno la doppietta
            if not image_paths:
                image_paths = [doublet_png]

            # debug: stampa elenco
            print("[INFO] Images in PDF (count={}):".format(len(image_paths)))
            for p in image_paths:
                print("   -", p)

            # 5) genera il PDF
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

    # ———— Salvataggio figura corrente ————
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

    def _build_metrics_dict(self) -> dict:
        # Primitive / metadata
        crs = self.meta.get("crs")
        res = self.meta.get("res")
        nodata = self.meta.get("nodata")

        # --- SI-only morphometrics ---
        A_base_m2 = float(getattr(self, "area_base_m2", 0.0))
        A_caldera_m2 = float(getattr(self, "area_caldera_m2", 0.0))

        P_base = float(getattr(self, "perimeter_base_m", 0.0))
        P_caldera = float(getattr(self, "perimeter_caldera_m", 0.0))

        D_base = float(getattr(self, "distance_meters_base", 0.0))
        D_caldera = float(getattr(self, "distance_meters_caldera", 0.0))

        # R used in volume
        R_base = float(getattr(self, "R1", 0.0))
        R_caldera = float(getattr(self, "R2", 0.0))

        # Equivalent radii from area (not duplicates: they are derived from area)
        R_eq_base = math.sqrt(A_base_m2 / math.pi) if A_base_m2 > 0 else 0.0
        R_eq_caldera = math.sqrt(A_caldera_m2 / math.pi) if A_caldera_m2 > 0 else 0.0

        # Volumes (m³) and model inputs
        h_max = float(getattr(self, "h_max", 0.0))
        V_frustum_m3 = float(getattr(self, "V_frustum_m3", 0.0))
        V_caldera_m3 = float(getattr(self, "V_caldera_m3", 0.0))
        V_eff_m3 = float(getattr(self, "V_effective_m3", 0.0))

        # Derivate (rapporti)
        circularity_base = (4 * math.pi * A_base_m2 / (P_base ** 2)) if (A_base_m2 > 0 and P_base > 0) else None
        circularity_caldera = (4 * math.pi * A_caldera_m2 / (P_caldera ** 2)) if (A_caldera_m2 > 0 and P_caldera > 0) else None

        slenderness = (h_max / D_base) if D_base > 0 else None
        sanity_A_over_D2 = (A_base_m2 / (D_base ** 2)) if D_base > 0 else None

        eq_height_V_over_A = (V_frustum_m3 / A_base_m2) if A_base_m2 > 0 else None

        # Ratio vs perfect cone volume using Abase and h (cone would be (1/3)*Abase*h)
        denom_cone = (1.0/3.0) * A_base_m2 * h_max
        cone_ratio = (V_frustum_m3 / denom_cone) if (denom_cone and denom_cone > 0) else None

        # Geometry levels + points (replicability)
        base_level_m = getattr(self, "base_level_m", None)
        caldera_level_m = getattr(self, "caldera_level_m", None)

        # points in rc and xy
        try:
            base_p1_rc = [int(self.base_point1[0]), int(self.base_point1[1])]
            base_p2_rc = [int(self.base_point2[0]), int(self.base_point2[1])]
            base_p1_xy = list(_pixel_to_map_xy(self.transform, self.base_point1[0], self.base_point1[1]))
            base_p2_xy = list(_pixel_to_map_xy(self.transform, self.base_point2[0], self.base_point2[1]))
        except Exception:
            base_p1_rc, base_p2_rc, base_p1_xy, base_p2_xy = None, None, None, None

        try:
            cal_p1_rc = [int(self.max_slope_index1[0]), int(self.max_slope_index1[1])]
            cal_p2_rc = [int(self.max_slope_index2[0]), int(self.max_slope_index2[1])]
            cal_p1_xy = list(_pixel_to_map_xy(self.transform, self.max_slope_index1[0], self.max_slope_index1[1]))
            cal_p2_xy = list(_pixel_to_map_xy(self.transform, self.max_slope_index2[0], self.max_slope_index2[1]))
        except Exception:
            cal_p1_rc, cal_p2_rc, cal_p1_xy, cal_p2_xy = None, None, None, None

        metrics = {
            "meta": {
                "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
                "process_id": self.process_id,
                "input_dem_path": self.meta.get("input_dem_path"),
                "working_dem_path": self.meta.get("working_dem_path"),
                "crs": str(crs) if crs is not None else None,
                "res": list(res) if res is not None else None,
                "nodata": nodata,
                "params": {
                    "base_elevation_ratio": 0.05,
                    "caldera_level_ratio": 0.8
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

                "h_max_m": h_max,
            },

            "volume_model": {
                "edifice_model": "frustum_like",
                "caldera_model": "hemisphere",
                "inputs_used": {
                    "h_max_m": h_max,
                    "R_base_m": R_base,
                    "R_caldera_m": R_caldera,
                },
                "intermediate": {
                    "V_frustum_m3": V_frustum_m3
                }
            },

            "volumes": {
                "V_caldera_m3": V_caldera_m3,
                "V_effective_m3": V_eff_m3,
            },

            "geometry": {
                "base_level_m": float(base_level_m) if base_level_m is not None else None,
                "caldera_level_m": float(caldera_level_m) if caldera_level_m is not None else None,
                "base_points": {
                    "p1_rc": base_p1_rc,
                    "p2_rc": base_p2_rc,
                    "p1_xy": base_p1_xy,
                    "p2_xy": base_p2_xy,
                },
                "caldera_points": {
                    "p1_rc": cal_p1_rc,
                    "p2_rc": cal_p2_rc,
                    "p1_xy": cal_p1_xy,
                    "p2_xy": cal_p2_xy,
                }
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

    def _flatten_for_csv(self, metrics: dict) -> dict:
        # una riga piatta; chiavi stabili (non usata nel flusso attuale, ma lasciata per compatibilità)
        m = metrics
        row = {}
        meta = m.get("meta", {})
        params = meta.get("params", {})
        nd = m.get("nodata_stats", {})
        mm = m.get("morphometrics", {})
        vm = m.get("volume_model", {})
        vv = m.get("volumes", {})
        dd = m.get("derived", {})
        gg = m.get("geometry", {})

        row.update({
            "timestamp_utc": meta.get("timestamp_utc"),
            "process_id": meta.get("process_id"),
            "input_dem_path": meta.get("input_dem_path"),
            "working_dem_path": meta.get("working_dem_path"),
            "crs": meta.get("crs"),
            "res_x": (meta.get("res")[0] if meta.get("res") else None),
            "res_y": (meta.get("res")[1] if meta.get("res") else None),
            "nodata": meta.get("nodata"),
            "base_elevation_ratio": params.get("base_elevation_ratio"),
            "caldera_level_ratio": params.get("caldera_level_ratio"),

            "valid_count": nd.get("valid_count"),
            "total_count": nd.get("total_count"),
            "valid_min": nd.get("valid_min"),
            "valid_max": nd.get("valid_max"),
            "valid_p02": nd.get("valid_p02"),
            "valid_p98": nd.get("valid_p98"),

            "A_base_m2": mm.get("A_base_m2"),
            "P_base_m": mm.get("P_base_m"),
            "D_base_m": mm.get("D_base_m"),
            "R_base_m": mm.get("R_base_m"),
            "R_eq_base_m": mm.get("R_eq_base_m"),

            "A_caldera_m2": mm.get("A_caldera_m2"),
            "P_caldera_m": mm.get("P_caldera_m"),
            "D_caldera_m": mm.get("D_caldera_m"),
            "R_caldera_m": mm.get("R_caldera_m"),
            "R_eq_caldera_m": mm.get("R_eq_caldera_m"),

            "h_max_m": mm.get("h_max_m"),

            "base_level_m": gg.get("base_level_m"),
            "caldera_level_m": gg.get("caldera_level_m"),

            "edifice_model": vm.get("edifice_model"),
            "caldera_model": vm.get("caldera_model"),
            "V_frustum_m3": (vm.get("intermediate", {}) or {}).get("V_frustum_m3"),
            "V_caldera_m3": vv.get("V_caldera_m3"),
            "V_effective_m3": vv.get("V_effective_m3"),

            "slenderness": dd.get("slenderness_H_over_Dbase"),
            "sanity_A_over_D2": dd.get("sanity_Abase_over_Dbase2"),
            "circularity_base": dd.get("circularity_base"),
            "circularity_caldera": dd.get("circularity_caldera"),
            "eq_height_m": dd.get("eq_height_V_over_Abase_m"),
            "ratio_vs_cone": dd.get("ratio_vs_cone"),
        })
        return row

    def _write_metrics_files(self, out_dir=None):
        """
        Scrive:
        - metrics.json (completo, strutturato)
        - metrics.csv  (umano, verticale, subset)
        """
        out_dir = out_dir or self.out_dir
        os.makedirs(out_dir, exist_ok=True)

        metrics = self._build_metrics_dict()

        json_path = os.path.join(out_dir, "metrics.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(_as_serializable(metrics), f, indent=2, ensure_ascii=False)

        csv_path = os.path.join(out_dir, "metrics.csv")
        human_rows = metrics_to_human_rows(metrics)

        # ✅ Excel fix: UTF-8 with BOM
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
        SIA metrics.json (completo) SIA metrics.csv (umano verticale).
        """
        out_dir = QFileDialog.getExistingDirectory(self, "Select folder to export metrics")
        if not out_dir:
            return

        try:
            # usa LA STESSA funzione del salvataggio automatico
            self._write_metrics_files(out_dir=out_dir)

            QMessageBox.information(
                self,
                "Success",
                f"metrics.json + metrics.csv exported to:\n{out_dir}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"An error occurred while exporting metrics: {e}")


# ———————— Main ——————————

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python CircularVolcano_Approx1.py <dem_file_path>")
        sys.exit(1)

    input_dem_path = sys.argv[1]
    dem_file_path = input_dem_path

    # 1) Prima scelta: usa il dem_working associato all'ultimo manifest disponibile
    outputs_dir, manifest_path = _find_manifest()
    if outputs_dir:
        candidate = os.path.join(outputs_dir, "dem_working.tif")
        if os.path.exists(candidate):
            dem_file_path = candidate
            print(f"[DEBUG] Using dem_working from outputs dir: {dem_file_path}")

    # 2) Se ancora non è metrico (es. EPSG:4326), riproietta qui (crea outputs/<PID o local>/dem_working.tif)
    dem_file_path = ensure_metric_dem(dem_file_path)

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

    # meta per export metrics (NON entra in PDF né in View Results)
    meta = {
        "process_id": os.environ.get("PROCESS_ID"),
        "input_dem_path": input_dem_path,
        "working_dem_path": dem_file_path,
        "crs": crs,
        "res": res,
        "nodata": nodata
    }

    app = QApplication(sys.argv)
    ex = VolumeAnalysisApp(dem, transform, meta=meta)
    ex.showMaximized()
    sys.exit(app.exec_())