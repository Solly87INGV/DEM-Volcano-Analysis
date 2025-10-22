# EllipticalVolcano_Approx2.py
# ——————————————————————————————————————————————————————————
# Approx2 (ellittico): calcoli invariati.
# PDF:
#   - carica immagini dal manifest ma rimuove SOLO le TRIPLETTE
#   - NON aggiunge l'overview (niente tripla in PDF)
#   - aggiunge la DOPPIETTA finale "Base vs Caldera"
#   - fail-safe: se resta vuoto, usa almeno la doppietta

import sys
import os
import json
import time
import numpy as np
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

import pdf_generator

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

# ========== Main App ==========

class VolumeAnalysisApp(QMainWindow):
    def __init__(self, dem, original_file_name="Unknown"):
        super().__init__()
        self.setWindowTitle('Elliptical Volcano Volume Analysis - Approximation 2')
        self.dem = dem

        self.process_id = _resolve_process_id()
        self.out_dir = _ensure_outputs_dir(self.process_id)
        self.original_file_name = original_file_name

        self.calculate_results()
        self.initUI()

        try:
            self._save_overview_png()   # solo su disco, NON va nel PDF
            self._emit_stdout_payload() # payload JSON ok
        except Exception as e:
            print(f"[PY VOL WARN] post-render saving/payload failed: {e}")

    def initUI(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)

        self.figure = Figure(figsize=(18, 14))
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

        gs = gridspec.GridSpec(nrows=3, ncols=3, height_ratios=[4, 1, 1.5], figure=fig, wspace=0.4, hspace=0.6)

        # DEM
        ax1 = fig.add_subplot(gs[0, 0])
        im1 = ax1.imshow(self.dem, cmap='terrain', origin='upper')
        cbar1 = fig.colorbar(im1, ax=ax1, fraction=0.046, pad=0.04)
        cbar1.set_label("Elevation (m)", rotation=90)
        ax1.set_title("Volcano DEM", fontsize=14, pad=20, y=1.02)
        ax1.axis('on')

        # Base
        ax2 = fig.add_subplot(gs[0, 1])
        im2 = ax2.imshow(self.dem, cmap='terrain', origin='upper')
        cbar2 = fig.colorbar(im2, ax=ax2, fraction=0.046, pad=0.04)
        cbar2.set_label("Elevation (m)", rotation=90)
        p1, = ax2.plot(self.base_point1[1], self.base_point1[0], 'ro', markersize=10, label='Base 1')
        p2, = ax2.plot(self.base_point2[1], self.base_point2[0], 'yo', markersize=10, label='Base 2')
        cplot, = ax2.plot(self.base_contour[:, 1], self.base_contour[:, 0], 'w-', linewidth=1, label="Base Contour")
        ax2.set_title("Opposite Points of the Volcano Base", fontsize=14, pad=20, y=1.02)
        ax2.axis('on')

        # Caldera
        ax3 = fig.add_subplot(gs[0, 2])
        im3 = ax3.imshow(self.dem, cmap='terrain', origin='upper')
        cbar3 = fig.colorbar(im3, ax=ax3, fraction=0.046, pad=0.04)
        cbar3.set_label("Elevation (m)", rotation=90)
        s1, = ax3.plot(self.max_slope_index1[1], self.max_slope_index1[0], 'ro', markersize=10, label='Max Slope 1')
        s2, = ax3.plot(self.max_slope_index2[1], self.max_slope_index2[0], 'yo', markersize=10, label='Max Slope 2')
        cald, = ax3.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label="Caldera Contour")
        ax3.set_title("Opposite Maximum Slope Points on the Caldera", fontsize=14, pad=20, y=1.02)
        ax3.axis('on')

        # legends & descriptions
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

        fig.subplots_adjust(left=0.05, right=0.95, top=0.95, bottom=0.05, wspace=0.4, hspace=0.6)
        self.canvas.draw()

    def calculate_results(self):
        try:
            pixel_size = 30  # modificabile
            self.base_contour = find_lowest_base_contour(self.dem, base_elevation_ratio=0.05)
            self.base_point1, self.base_point2 = find_opposite_base_points(self.base_contour)

            # Base distances
            self.distance_pixel_base = distance_between_points(self.base_point1[0], self.base_point1[1], self.base_point2[0], self.base_point2[1])
            self.distance_meters_base = self.distance_pixel_base * pixel_size
            self.distance_base_km = self.distance_meters_base * 1e-3

            # Caldera
            self.slope = calculate_slope(self.dem)
            self.caldera_contour = find_caldera_contour(self.dem, level_ratio=0.8)
            self.max_slope_index1, self.max_slope_index2 = find_opposite_slope_points(self.slope, self.caldera_contour)

            # Caldera distances
            self.distance_pixel_caldera = distance_between_points(self.max_slope_index1[0], self.max_slope_index1[1], self.max_slope_index2[0], self.max_slope_index2[1])
            self.distance_meters_caldera = self.distance_pixel_caldera * pixel_size
            self.distance_caldera_km = self.distance_meters_caldera * 1e-3

            # Aree
            self.area_base = calculate_area(self.base_contour, pixel_size) * 1e-6
            self.area_caldera = calculate_area(self.caldera_contour, pixel_size) * 1e-6

            # Volumi (ellittico – mantengo il tuo schema originale)
            self.h_max = np.max(self.dem)
            self.R1 = self.distance_meters_base / 2
            self.R2 = self.distance_meters_caldera / 2
            self.v = (1/3) * np.pi * self.h_max * (self.R1**2 + self.R2**2 + self.R1 * self.R2)
            self.v_km3 = self.v * 1e-9

            # Caldera (Approx1: come nel tuo originale)
            self.r_caldera_km = self.R2 * 1e-3
            self.v_caldera = (2/3) * np.pi * (self.area_caldera / np.pi) * (self.distance_caldera_km / 2)

            # Volume effettivo
            self.v_volcano = self.v_km3 - self.v_caldera

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

            # Testo risultati
            self.results_text = (
                f"Base area of the volcano: {self.area_base:.2f} km²\n"
                f"Base width (Distance between opposite points of the base): {self.distance_base_km:.2f} km\n"
                f"Caldera area of the volcano: {self.area_caldera:.2f} km²\n"
                f"Caldera width (Distance between opposite points of the caldera): {self.distance_caldera_km:.2f} km\n"
                f"Total volume of the volcanic edifice: {self.v_km3:.2f} km³\n"
                f"Caldera volume: {self.v_caldera:.2f} km³\n"
                f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
            )

            self.results_list = [
                f"Base area of the volcano: {self.area_base:.2f} km²",
                f"Base width (Distance between opposite points of the base): {self.distance_base_km:.2f} km",
                f"Caldera area of the volcano: {self.area_caldera:.2f} km²",
                f"Caldera width (Distance between opposite points of the caldera): {self.distance_caldera_km:.2f} km",
                f"Total volume of the volcanic edifice: {self.v_km3:.2f} km³",
                f"Caldera volume: {self.v_caldera:.2f} km³",
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

        # ok pubblicare anche l'overview nel payload JSON (non nel PDF)
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
        ax1.plot(self.base_contour[:, 1], self.base_contour[:, 0], 'w-', linewidth=1)
        ax1.plot(self.base_point1[1], self.base_point1[0], 'ro', markersize=8)
        ax1.plot(self.base_point2[1], self.base_point2[0], 'yo', markersize=8)
        ax1.set_title("Opposite Points of the Volcano Base", pad=8, fontsize=12)
        ax1.set_aspect('equal', adjustable='box')
        div1 = make_axes_locatable(ax1)
        cax1 = div1.append_axes("right", size="4.6%", pad=0.25)
        cbar1 = fig.colorbar(im1, cax=cax1); cbar1.set_label("Elevation (m)", rotation=90)

        # Spacer
        fig.add_subplot(gs[0, 1]).axis('off')

        # Destra: CALDERA
        ax2 = fig.add_subplot(gs[0, 2])
        im2 = ax2.imshow(self.dem, cmap='terrain', origin='upper', interpolation='nearest', resample=False)
        ax2.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1)
        ax2.plot(self.max_slope_index1[1], self.max_slope_index1[0], 'ro', markersize=8)
        ax2.plot(self.max_slope_index2[1], self.max_slope_index2[0], 'yo', markersize=8)
        ax2.set_title("Opposite Maximum Slope Points on the Caldera", pad=8, fontsize=12)
        ax2.set_aspect('equal', adjustable='box')
        div2 = make_axes_locatable(ax2)
        cax2 = div2.append_axes("right", size="4.6%", pad=0.25)
        cbar2 = fig.colorbar(im2, cax=cax2); cbar2.set_label("Elevation (m)", rotation=90)

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
            paths = _remove_triplets(paths)  # <<< filtro anti-triplette

        # sincronizza captions con paths
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
            title = "Calculation Results - Elliptical Base, Approximation Type 2"

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

# ### Entry Point ###
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python EllipticalVolcano_Approx2.py <dem_file_path> [original_file_name]")
        sys.exit(1)

    dem_file_path = sys.argv[1]
    if not os.path.exists(dem_file_path):
        print(f"Error: File '{dem_file_path}' does not exist.")
        sys.exit(1)

    original_file_name = sys.argv[2] if len(sys.argv) > 2 else "Unknown"

    try:
        with rasterio.open(dem_file_path) as src:
            dem = src.read(1)
    except Exception as e:
        print(f"Error opening DEM file: {e}")
        sys.exit(1)

    app = QApplication(sys.argv)
    ex = VolumeAnalysisApp(dem, original_file_name=original_file_name)
    ex.showMaximized()
    sys.exit(app.exec_())
