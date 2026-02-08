# EllipticalVolcano_Approx2.py
# ——————————————————————————————————————————————————————————
# Approx2 (ellittico): CALCOLI INVARIATI (non tocco le formule).
#
# Headless/Docker:
#  - NO PyQt, NO finestre, NO click
#  - salva SEMPRE la PNG "final doublet" in:
#       out_dir = os.path.join(os.environ["OUTPUTS_DIR"], os.environ["PROCESS_ID"])
#  - scrive volume_results.json nello stesso out_dir con:
#       { processId, status, moduleKey, result:{...}, images:[{filename,title}] }
#  - stampa UNA sola riga JSON su stdout (stessa struttura)
#
# GUI locale:
#  - mantiene la GUI esistente (se usata)
#  - PDF: carica immagini dal manifest ma rimuove SOLO TRIPLETTE, NON aggiunge overview,
#         aggiunge la DOPPIETTA finale "Base vs Caldera" (fail-safe incluso)

import sys
import os
import json
import uuid
import numpy as np
import rasterio
from scipy.ndimage import sobel
from skimage import measure

# ---------------- [PATCH] HEADLESS switch (must be decided BEFORE pyplot/QT imports) ----------------
def _is_headless():
    v = os.environ.get("HEADLESS", "")
    return str(v).strip().lower() in ("1", "true", "yes", "on")

HEADLESS = _is_headless()

import matplotlib
if HEADLESS:
    matplotlib.use("Agg")  # [PATCH] no display in Docker/headless

from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib import gridspec
from mpl_toolkits.axes_grid1 import make_axes_locatable  # ← per cbar co-alte affiancate

import pdf_generator


# =========================
# [PATCH] module key stabile
# =========================
MODULE_KEY = "elliptical_approx2"


# ========== helpers: outputs & manifest ==========

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))

def _outputs_base_dir():
    """
    OUTPUTS_DIR da env, fallback a "outputs" vicino allo script.
    """
    env_base = os.environ.get("OUTPUTS_DIR")
    if env_base and str(env_base).strip():
        return os.path.abspath(env_base)
    return os.path.join(_script_dir(), "outputs")

def _resolve_process_id() -> str:
    """
    PROCESS_ID da env, fallback a pid univoco (compat locale).
    """
    env_id = os.environ.get("PROCESS_ID")
    if env_id and str(env_id).strip():
        return str(env_id).strip()
    return uuid.uuid4().hex[:12]

def _ensure_outputs_dir(process_id: str) -> str:
    """
    out_dir = ${OUTPUTS_DIR}/${PROCESS_ID} (fallback locale).
    """
    out_dir = os.path.join(_outputs_base_dir(), process_id)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def _write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def _find_manifest():
    """
    Restituisce (outputs_dir, manifest_path) se trovati.
    Priorità: env PROCESS_ID -> ${OUTPUTS_DIR}/${PROCESS_ID}/analysis_images.json
    Fallback: manifest più recente in ${OUTPUTS_DIR}/*/analysis_images.json
    """
    base = _outputs_base_dir()

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


# ========== Analysis (calcoli invariati) ==========

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

def distance_between_points(x1, y1, x2, y2):
    return np.sqrt((x2 - x1)**2 + (y2 - y1)**2)

def calculate_area(contour, pixel_size):
    x = contour[:, 1]
    y = contour[:, 0]
    area_in_pixels = 0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
    area_in_meters = area_in_pixels * (pixel_size ** 2)
    return area_in_meters

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


# ———————— Doublet saver + Core riusabile (no GUI) ——————————

def save_final_doublet_png(
    dem,
    base_contour, base_point1, base_point2,
    caldera_contour, max_slope_index1, max_slope_index2,
    out_path
):
    """
    Salva una DOPPIETTA 1x2 (base vs caldera) con la STESSA gabbia:
    14.5x5.5, spacer centrale, colorbar 4.6%. Funziona anche in headless (Agg).
    """
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    FIG_W, FIG_H = 14.5, 5.5
    fig = plt.figure(figsize=(FIG_W, FIG_H))
    gs = gridspec.GridSpec(1, 3, figure=fig, width_ratios=[1.0, 0.08, 1.0], wspace=0.15)

    # Sinistra: BASE
    ax1 = fig.add_subplot(gs[0, 0])
    im1 = ax1.imshow(dem, cmap='terrain', origin='upper', interpolation='nearest', resample=False)
    pc, = ax1.plot(base_contour[:, 1], base_contour[:, 0], 'w-', linewidth=1, label='Base Contour')
    p1, = ax1.plot(base_point1[1], base_point1[0], 'ro', markersize=8, label='Base 1')
    p2, = ax1.plot(base_point2[1], base_point2[0], 'yo', markersize=8, label='Base 2')
    ax1.set_title("Opposite Points of the Volcano Base", pad=8, fontsize=12)
    ax1.set_aspect('equal', adjustable='box')
    div1 = make_axes_locatable(ax1)
    cax1 = div1.append_axes("right", size="4.6%", pad=0.10)
    cbar1 = fig.colorbar(im1, cax=cax1); cbar1.set_label("Elevation (m)", rotation=90)
    cbar1.ax.yaxis.set_ticks_position('right')
    cbar1.ax.yaxis.set_label_position('right')
    cbar1.ax.tick_params(labelsize=9, pad=1)
    cbar1.ax.yaxis.labelpad = 2
    leg1 = ax1.legend(handles=[p1, p2, pc], labels=['Base 1', 'Base 2', 'Base Contour'],
                      loc='upper right', frameon=True)
    leg1.get_frame().set_alpha(0.7)
    leg1.get_frame().set_facecolor('white')
    leg1.get_frame().set_edgecolor('black')

    # Spacer
    fig.add_subplot(gs[0, 1]).axis('off')

    # Destra: CALDERA
    ax2 = fig.add_subplot(gs[0, 2])
    im2 = ax2.imshow(dem, cmap='terrain', origin='upper', interpolation='nearest', resample=False)
    cc, = ax2.plot(caldera_contour[:, 1], caldera_contour[:, 0], 'b-', linewidth=1, label='Caldera Contour')
    s1, = ax2.plot(max_slope_index1[1], max_slope_index1[0], 'ro', markersize=8, label='Max Slope 1')
    s2, = ax2.plot(max_slope_index2[1], max_slope_index2[0], 'yo', markersize=8, label='Max Slope 2')
    ax2.set_title("Opposite Maximum Slope Points on the Caldera", pad=8, fontsize=12)
    ax2.set_aspect('equal', adjustable='box')
    div2 = make_axes_locatable(ax2)
    cax2 = div2.append_axes("right", size="4.6%", pad=0.10)
    cbar2 = fig.colorbar(im2, cax=cax2); cbar2.set_label("Elevation (m)", rotation=90)
    cbar2.ax.yaxis.set_ticks_position('right')
    cbar2.ax.yaxis.set_label_position('right')
    cbar2.ax.tick_params(labelsize=9, pad=1)
    cbar2.ax.yaxis.labelpad = 2
    leg2 = ax2.legend(handles=[s1, s2, cc], labels=['Max Slope 1', 'Max Slope 2', 'Caldera Contour'],
                      loc='upper right', frameon=True)
    leg2.get_frame().set_alpha(0.7)
    leg2.get_frame().set_facecolor('white')
    leg2.get_frame().set_edgecolor('black')

    fig.savefig(out_path, dpi=170)
    plt.close(fig)

    if not os.path.exists(out_path):
        raise RuntimeError(f"Doublet not saved: {out_path}")

def run_volume_analysis(
    dem: np.ndarray,
    *,
    pixel_size: float = 30.0,
    base_elevation_ratio: float = 0.05,
    caldera_level_ratio: float = 0.8,
    outputs_dir: str = None,
    process_id: str = None,
    write_outputs: bool = True,
):
    """
    Core riusabile:
    - calcola (LOGICA SCIENTIFICA INVARIATA: copiata 1:1 dal tuo calculate_results)
    - in headless (write_outputs=True) salva doppietta + volume_results.json
    Ritorna payload utile per GUI o headless.
    """
    if process_id is None:
        process_id = _resolve_process_id()
    if outputs_dir is None:
        outputs_dir = _ensure_outputs_dir(process_id)
    else:
        os.makedirs(outputs_dir, exist_ok=True)

    # --- calcoli invariati ---
    base_contour = find_lowest_base_contour(dem, base_elevation_ratio=base_elevation_ratio)
    base_point1, base_point2 = find_opposite_base_points(base_contour)

    distance_pixel_base = distance_between_points(base_point1[0], base_point1[1], base_point2[0], base_point2[1])
    distance_meters_base = distance_pixel_base * pixel_size
    distance_base_km = distance_meters_base * 1e-3

    slope = calculate_slope(dem)
    caldera_contour = find_caldera_contour(dem, level_ratio=caldera_level_ratio)
    max_slope_index1, max_slope_index2 = find_opposite_slope_points(slope, caldera_contour)

    distance_pixel_caldera = distance_between_points(
        max_slope_index1[0], max_slope_index1[1], max_slope_index2[0], max_slope_index2[1]
    )
    distance_meters_caldera = distance_pixel_caldera * pixel_size
    distance_caldera_km = distance_meters_caldera * 1e-3

    area_base_km2 = calculate_area(base_contour, pixel_size) * 1e-6
    area_caldera_km2 = calculate_area(caldera_contour, pixel_size) * 1e-6

    # Volumi (ellittico – mantengo il tuo schema originale)
    h_max = float(np.max(dem))
    R1 = distance_meters_base / 2.0
    R2 = distance_meters_caldera / 2.0
    v = (1/3) * np.pi * h_max * (R1**2 + R2**2 + R1 * R2)
    v_km3 = float(v * 1e-9)

    # Caldera (Approx2: come nel tuo originale in questo file)
    r_caldera_km = float(R2 * 1e-3)
    v_caldera = float((2/3) * np.pi * (area_caldera_km2 / np.pi) * (distance_caldera_km / 2.0))

    v_volcano = float(v_km3 - v_caldera)

    results = {
        "base_area_km2": float(area_base_km2),
        "base_width_km": float(distance_base_km),
        "caldera_area_km2": float(area_caldera_km2),
        "caldera_width_km": float(distance_caldera_km),
        "total_volume_km3": float(v_km3),
        "caldera_volume_km3": float(v_caldera),
        "effective_volume_km3": float(v_volcano),
        "pixel_size_m": float(pixel_size),
        "base_elevation_ratio": float(base_elevation_ratio),
        "caldera_level_ratio": float(caldera_level_ratio),
        "h_max_m": float(h_max),
        "r_caldera_km": float(r_caldera_km),
    }

    human_text = (
        f"Base area of the volcano: {area_base_km2:.2f} km²\n"
        f"Base width (Distance between opposite points of the base): {distance_base_km:.2f} km\n"
        f"Caldera area of the volcano: {area_caldera_km2:.2f} km²\n"
        f"Caldera width (Distance between opposite points of the caldera): {distance_caldera_km:.2f} km\n"
        f"Total volume of the volcanic edifice: {v_km3:.2f} km³\n"
        f"Caldera volume: {v_caldera:.2f} km³\n"
        f"Effective volume of the volcanic edifice: {v_volcano:.2f} km³"
    )

    images = []
    module_key_out = MODULE_KEY  # sempre definito, anche se write_outputs=False

    if write_outputs:
        # ============================
        # [PATCH] 1) prima: immagini dal manifest (basenames) (NO triplets)
        # ============================
        out_prev, mp = _find_manifest()
        if mp:
            prev = _load_manifest_images(mp)
            prev = _normalize_and_filter_paths(prev, base_dir=out_prev)
            prev = _remove_triplets(prev)
            for pth in prev:
                fn = os.path.basename(pth)
                images.append({"filename": fn, "title": fn})

        # ============================
        # [PATCH] 2) poi: salva SEMPRE la doppietta e aggiungila in coda
        # ============================
        doublet_abs = os.path.join(outputs_dir, "final_doublet_base_vs_caldera.png")
        save_final_doublet_png(
            dem,
            base_contour, base_point1, base_point2,
            caldera_contour, max_slope_index1, max_slope_index2,
            doublet_abs
        )

        doublet_fn = os.path.basename(doublet_abs)
        if not any(it.get("filename") == doublet_fn for it in images):
            images.append({
                "filename": doublet_fn,
                "title": "Base vs Caldera (final doublet)"
            })

        wrapped = {
            "processId": process_id,
            "status": "completed",
            "moduleKey": module_key_out,
            "result": results,
            "images": images,
        }
        _write_json(os.path.join(outputs_dir, "volume_results.json"), wrapped)

    geometry = {
        "base_contour": base_contour,
        "base_point1": base_point1,
        "base_point2": base_point2,
        "slope": slope,
        "caldera_contour": caldera_contour,
        "max_slope_index1": max_slope_index1,
        "max_slope_index2": max_slope_index2,
    }

    return {
        "process_id": process_id,
        "outputs_dir": outputs_dir,
        "moduleKey": module_key_out,
        "results": results,
        "human_text": human_text,
        "images": images,
        "geometry": geometry,
    }


# ———————— GUI: import PyQt SOLO se non headless ——————————
if not HEADLESS:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox, QFileDialog
    )
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas


# ========== Main App (GUI) ==========

if not HEADLESS:
    class VolumeAnalysisApp(QMainWindow):
        def __init__(self, dem, original_file_name="Unknown"):
            super().__init__()
            self.setWindowTitle('Elliptical Volcano Volume Analysis - Approximation 2')
            self.dem = dem

            self.process_id = _resolve_process_id()
            self.out_dir = _ensure_outputs_dir(self.process_id)
            self.original_file_name = original_file_name

            # usa core riusabile (no scrittura in GUI)
            payload = run_volume_analysis(self.dem, process_id=self.process_id, outputs_dir=self.out_dir, write_outputs=False)
            self._apply_payload(payload)

            self.initUI()

            try:
                self._save_overview_png()   # solo su disco, NON va nel PDF
                self._emit_stdout_payload() # payload JSON ok
            except Exception as e:
                print(f"[PY VOL WARN] post-render saving/payload failed: {e}")

        def _apply_payload(self, payload):
            g = payload["geometry"]
            r = payload["results"]

            self.base_contour = g["base_contour"]
            self.base_point1 = g["base_point1"]
            self.base_point2 = g["base_point2"]
            self.slope = g["slope"]
            self.caldera_contour = g["caldera_contour"]
            self.max_slope_index1 = g["max_slope_index1"]
            self.max_slope_index2 = g["max_slope_index2"]

            self.area_base = r["base_area_km2"]
            self.area_caldera = r["caldera_area_km2"]
            self.distance_base_km = r["base_width_km"]
            self.distance_caldera_km = r["caldera_width_km"]
            self.v_km3 = r["total_volume_km3"]
            self.v_caldera = r["caldera_volume_km3"]
            self.v_volcano = r["effective_volume_km3"]

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

            self.results_text = payload["human_text"]
            self.results_list = [
                f"Base area of the volcano: {self.area_base:.2f} km²",
                f"Base width (Distance between opposite points of the base): {self.distance_base_km:.2f} km",
                f"Caldera area of the volcano: {self.area_caldera:.2f} km²",
                f"Caldera width (Distance between opposite points of the caldera): {self.distance_caldera_km:.2f} km",
                f"Total volume of the volcanic edifice: {self.v_km3:.2f} km³",
                f"Caldera volume: {self.v_caldera:.2f} km³",
                f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
            ]

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

        # ----- overview (solo su disco) + payload JSON (stdout) -----
        def _save_overview_png(self):
            try:
                out_png = os.path.join(self.out_dir, "elliptical_approx2_overview.png")
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

            images.append(f"/outputs/{self.process_id}/elliptical_approx2_overview.png")
            payload = {"result": self.results_text, "images": images}
            print(json.dumps(payload), flush=True)

        def _save_final_doublet_png(self, out_path):
            save_final_doublet_png(
                self.dem,
                self.base_contour, self.base_point1, self.base_point2,
                self.caldera_contour, self.max_slope_index1, self.max_slope_index2,
                out_path
            )

        def _collect_report_images(self):
            """
            PDF:
              - immagini dal manifest
              - rimuove SOLO TRIPLETTE
              - NON inserisce overview
            """
            paths = []
            outputs_dir, manifest_path = _find_manifest()
            if manifest_path:
                manifest_imgs = _load_manifest_images(manifest_path)
                paths = _normalize_and_filter_paths(manifest_imgs, base_dir=outputs_dir)
                paths = _remove_triplets(paths)
            captions = [None] * len(paths)
            return paths, captions

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
                title = "Calculation Results - Elliptical Base, Approximation Type 2"

                out_dir_for_doublet = os.path.dirname(file_path) if os.path.dirname(file_path) else os.getcwd()
                doublet_png = os.path.join(out_dir_for_doublet, "final_doublet_base_vs_caldera.png")
                self._save_final_doublet_png(doublet_png)

                image_paths, captions = self._collect_report_images()

                if os.path.exists(doublet_png):
                    image_paths.append(doublet_png)
                    captions.append(None)

                if not image_paths:
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


# ========== Entry point con headless/GUI ==========

def main(argv=None):
    argv = argv or sys.argv

    if len(argv) < 2:
        print("Usage: python EllipticalVolcano_Approx2.py <dem_file_path> [original_file_name]")
        return 1

    dem_file_path = argv[1]
    if not os.path.exists(dem_file_path):
        print(f"Error: File '{dem_file_path}' does not exist.")
        return 1

    original_file_name = argv[2] if len(argv) > 2 else "Unknown"

    try:
        with rasterio.open(dem_file_path) as src:
            dem = src.read(1)
    except Exception as e:
        print(f"Error opening DEM file: {e}")
        return 1

    if HEADLESS:
        pid = _resolve_process_id()
        out_dir = _ensure_outputs_dir(pid)

        try:
            payload = run_volume_analysis(
                dem,
                process_id=pid,
                outputs_dir=out_dir,
                write_outputs=True
            )
            final_line = {
                "processId": payload["process_id"],
                "status": "completed",
                "moduleKey": MODULE_KEY,
                "result": payload["results"],
                "images": payload["images"],
            }
            print(json.dumps(final_line, ensure_ascii=False), flush=True)
            return 0
        except Exception as e:
            err_line = {
                "processId": pid,
                "status": "failed",
                "moduleKey": MODULE_KEY,
                "result": {"error": str(e)},
                "images": [],
            }
            try:
                _write_json(os.path.join(out_dir, "volume_results.json"), err_line)
            except Exception:
                pass
            print(json.dumps(err_line, ensure_ascii=False), flush=True)
            return 2

    # GUI locale
    app = QApplication(argv)
    ex = VolumeAnalysisApp(dem, original_file_name=original_file_name)
    ex.showMaximized()
    return app.exec_()

if __name__ == "__main__":
    raise SystemExit(main())
