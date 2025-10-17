# CircularVolcano_Approx2.py

import sys
import os
import json
import time
import glob
import numpy as np
import rasterio  # To read DEM files in .tif format
from scipy.ndimage import sobel
from skimage import measure
from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox, QFileDialog
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib import gridspec
import pdf_generator

# ========== small helpers for outputs ==========

def _resolve_process_id() -> str:
    """Usa PROCESS_ID dall'env, altrimenti fallback deterministico."""
    env_id = os.environ.get("PROCESS_ID")
    if env_id:
        return env_id
    return f"local_{int(time.time())}"

def _base_dir():
    return os.path.dirname(os.path.abspath(__file__))

def _ensure_outputs_dir(process_id: str) -> str:
    out_dir = os.path.join(_base_dir(), "outputs", process_id)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def _manifest_path_for(out_dir: str) -> str:
    return os.path.join(out_dir, "analysis_images.json")

def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _load_analysis_manifest_with_fallback(out_dir: str):
    """
    1) prova manifest in out_dir
    2) se non c'è, cerca il manifest più recente in outputs/*/
    3) se ancora nulla, fallback a lista immagini triplet_*.png nell'out_dir
    Ritorna (manifest_dict | None, origin_out_dir)
    """
    # 1) manifest nella cartella del processId corrente
    manifest_path = _manifest_path_for(out_dir)
    if os.path.exists(manifest_path):
        data = _read_json(manifest_path)
        if data:
            return data, out_dir

    # 2) cerca manifest più recente in qualunque outputs/<pid>/
    outputs_root = os.path.join(_base_dir(), "outputs")
    candidates = glob.glob(os.path.join(outputs_root, "*", "analysis_images.json"))
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for cand in candidates:
        data = _read_json(cand)
        if data and isinstance(data.get("images"), list) and data["images"]:
            return data, os.path.dirname(cand)  # la dir del manifest trovato

    # 3) fallback "grezzo": usa triplet_*.png presenti in out_dir (se ci sono)
    triplets = sorted(glob.glob(os.path.join(out_dir, "triplet_*.png")))
    if triplets:
        fake = {
            "images": [
                {
                    "abs_path": p,
                    "public_path": f"/outputs/{os.path.basename(os.path.dirname(p))}/{os.path.basename(p)}",
                    "titles": []
                }
                for p in triplets
            ]
        }
        return fake, out_dir

    return None, out_dir

# ========== Analysis Functions ==========

def find_lowest_base_contour(matrix, base_elevation_ratio=0.05):
    base_level = matrix.min() + (matrix.max() - matrix.min()) * base_elevation_ratio
    contours = measure.find_contours(matrix, base_level)
    if len(contours) == 0:
        raise ValueError("No contours found for the given base elevation ratio.")
    base_contour = max(contours, key=len)
    return base_contour

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
    slope = np.hypot(dx, dy)
    return slope

def find_caldera_contour(matrix, level_ratio=0.8):
    contour_level = matrix.max() * level_ratio
    contours = measure.find_contours(matrix, contour_level)
    if len(contours) == 0:
        raise ValueError("No contours found for the given level ratio.")
    main_contour = max(contours, key=len)
    return main_contour

def find_opposite_slope_points(slope_matrix, contour):
    contour = np.round(contour).astype(int)
    # Evita indici fuori matrice
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
        self.setWindowTitle('Volcano Volume Analysis')
        self.dem = dem

        # outputs context
        self.process_id = _resolve_process_id()
        self.out_dir = _ensure_outputs_dir(self.process_id)
        self.original_file_name = original_file_name

        self.calculate_results()  # prima dei widget
        self.initUI()

        # salva overview + emetti payload JSON (non blocca la GUI in caso di errore)
        try:
            self._save_overview_png()
            self._emit_stdout_payload()
        except Exception as e:
            print(f"[PY VOL WARN] post-render saving/payload failed: {e}")

    def initUI(self):
        # Widget centrale
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Layout principale
        main_layout = QVBoxLayout(central_widget)

        # Figura Matplotlib e Canvas
        self.figure = Figure(figsize=(18, 14))
        self.canvas = FigureCanvas(self.figure)
        main_layout.addWidget(self.canvas)

        # Pulsanti
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

    def calculate_results(self):
        try:
            pixel_size = 30  # adattabile
            self.base_contour = find_lowest_base_contour(self.dem, base_elevation_ratio=0.05)
            self.base_point1, self.base_point2 = find_opposite_base_points(self.base_contour)

            # Base distances
            self.distance_pixel_base = distance_between_points(
                self.base_point1[0], self.base_point1[1], self.base_point2[0], self.base_point2[1]
            )
            self.distance_meters_base = self.distance_pixel_base * pixel_size
            self.distance_base_km = self.distance_meters_base * 1e-3

            # Caldera
            self.slope = calculate_slope(self.dem)
            self.caldera_contour = find_caldera_contour(self.dem, level_ratio=0.8)
            self.max_slope_index1, self.max_slope_index2 = find_opposite_slope_points(self.slope, self.caldera_contour)

            # Caldera distances
            self.distance_pixel_caldera = distance_between_points(
                self.max_slope_index1[0], self.max_slope_index1[1],
                self.max_slope_index2[0], self.max_slope_index2[1]
            )
            self.distance_meters_caldera = self.distance_pixel_caldera * pixel_size
            self.distance_caldera_km = self.distance_meters_caldera * 1e-3

            # Areas
            self.area_base = calculate_area(self.base_contour, pixel_size) * 1e-6
            self.area_caldera = calculate_area(self.caldera_contour, pixel_size) * 1e-6

            # Volumes (Approx2: caldera cilindro h=r)
            self.h_max = np.max(self.dem)
            self.R1 = self.distance_meters_base / 2
            self.R2 = self.distance_meters_caldera / 2
            self.v = (1/3) * np.pi * self.h_max * (self.R1**2 + self.R2**2 + self.R1 * self.R2)
            self.v_km3 = self.v * 1e-9
            self.r2 = self.R2 * 1e-3
            self.p = self.r2
            self.v_caldera = np.pi * (self.r2**2) * self.p
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

    def update_display(self):
        self.figure.clear()
        fig = self.figure

        gs = gridspec.GridSpec(nrows=3, ncols=3, height_ratios=[4, 1, 1.5], figure=fig, wspace=0.4, hspace=0.6)

        # Plot 1
        ax1 = fig.add_subplot(gs[0, 0])
        im1 = ax1.imshow(self.dem, cmap='terrain', origin='upper')
        cbar1 = fig.colorbar(im1, ax=ax1, fraction=0.046, pad=0.04)
        cbar1.set_label("Elevation (m)", rotation=90)
        ax1.set_title("Volcano DEM", fontsize=14, pad=20, y=1.02)
        ax1.axis('on')

        # Plot 2
        ax2 = fig.add_subplot(gs[0, 1])
        im2 = ax2.imshow(self.dem, cmap='terrain', origin='upper')
        cbar2 = fig.colorbar(im2, ax=ax2, fraction=0.046, pad=0.04)
        cbar2.set_label("Elevation (m)", rotation=90)
        p1, = ax2.plot(self.base_point1[1], self.base_point1[0], 'ro', markersize=10, label='Base 1')
        p2, = ax2.plot(self.base_point2[1], self.base_point2[0], 'yo', markersize=10, label='Base 2')
        cplot, = ax2.plot(self.base_contour[:, 1], self.base_contour[:, 0], 'w-', linewidth=1, label="Base Contour")
        ax2.set_title("Opposite Points of the Volcano Base", fontsize=14, pad=20, y=1.02)
        ax2.axis('on')

        # Plot 3
        ax3 = fig.add_subplot(gs[0, 2])
        im3 = ax3.imshow(self.dem, cmap='terrain', origin='upper')
        cbar3 = fig.colorbar(im3, ax=ax3, fraction=0.046, pad=0.04)
        cbar3.set_label("Elevation (m)", rotation=90)
        s1, = ax3.plot(self.max_slope_index1[1], self.max_slope_index1[0], 'ro', markersize=10, label='Max Slope 1')
        s2, = ax3.plot(self.max_slope_index2[1], self.max_slope_index2[0], 'yo', markersize=10, label='Max Slope 2')
        cald, = ax3.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label="Caldera Contour")
        ax3.set_title("Opposite Maximum Slope Points on the Caldera", fontsize=14, pad=20, y=1.02)
        ax3.axis('on')

        # legends row
        l2 = fig.add_subplot(gs[1, 1]); l2.axis('off')
        leg2 = l2.legend([p1, p2, cplot], ['Base 1', 'Base 2', 'Base Contour'],
                         loc='center', frameon=True, edgecolor='black', facecolor='lightgray', ncol=3)
        leg2.get_frame().set_linewidth(1)

        l3 = fig.add_subplot(gs[1, 2]); l3.axis('off')
        leg3 = l3.legend([s1, s2, cald], ['Max Slope 1', 'Max Slope 2', 'Caldera Contour'],
                         loc='center', frameon=True, edgecolor='black', facecolor='lightgray', ncol=3)
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

        fig.subplots_adjust(left=0.05, right=0.95, top=0.95, bottom=0.05, wspace=0.4, hspace=0.6)
        self.canvas.draw()

    # ----- save overview and emit JSON payload -----
    def _save_overview_png(self):
        try:
            out_png = os.path.join(self.out_dir, "circular_approx2_overview.png")
            self.figure.savefig(out_png, dpi=150, bbox_inches='tight')
            print(f"[PY VOL {self.process_id}] saved {out_png}")
        except Exception as e:
            print(f"[PY VOL ERR] failed to save overview png: {e}")

    def _emit_stdout_payload(self):
        images = []

        # 1) manifest (con fallback a manifest più recente o triplet_*.png)
        manifest, origin_dir = _load_analysis_manifest_with_fallback(self.out_dir)
        if manifest and isinstance(manifest.get("images"), list):
            for entry in manifest["images"]:
                public = entry.get("public_path")
                if public:
                    images.append(public)

        # 2) overview corrente
        images.append(f"/outputs/{self.process_id}/circular_approx2_overview.png")

        payload = {
            "result": self.results_text,
            "images": images
        }
        print(json.dumps(payload), flush=True)

    # ----- collect images for PDF (absolute paths + captions) -----
    def _collect_report_images(self):
        """
        Raccoglie percorsi ASSOLUTI immagini per PDF:
        - tutte le triplette da analysis_images.json (con fallback a manifest più recente o triplet_*.png)
        - l’overview di questa GUI.
        Ritorna (paths, captions).
        """
        paths, captions = [], []

        manifest, origin_dir = _load_analysis_manifest_with_fallback(self.out_dir)
        if manifest and isinstance(manifest.get("images"), list):
            for i, entry in enumerate(manifest["images"], start=1):
                abs_path = entry.get("abs_path")
                public_path = entry.get("public_path")
                titles = entry.get("titles") or []

                # Se l'abs_path manca o non esiste, ricostruisci da public_path
                if not abs_path or not os.path.exists(abs_path):
                    if public_path:
                        candidate = os.path.join(origin_dir, os.path.basename(public_path))
                        if os.path.exists(candidate):
                            abs_path = candidate

                # Se ancora nulla, prova triplet_*.png in origin_dir
                if (not abs_path or not os.path.exists(abs_path)) and public_path and "triplet_" in public_path:
                    candidate = os.path.join(origin_dir, os.path.basename(public_path))
                    if os.path.exists(candidate):
                        abs_path = candidate

                if abs_path and os.path.exists(abs_path):
                    paths.append(abs_path)
                    cap = " | ".join(titles) if titles else f"Triplet {i}"
                    captions.append(cap)

        # overview corrente
        overview_path = os.path.join(self.out_dir, "circular_approx2_overview.png")
        if os.path.exists(overview_path):
            paths.append(overview_path)
            captions.append("Circular Approximation 2 — Overview")

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
        if file_path:
            if not file_path.lower().endswith('.pdf'):
                file_path += '.pdf'
            try:
                title = "Calculation Results - Circular Base, Approximation Type 2"
                img_paths, img_caps = self._collect_report_images()
                pdf_generator.generate_pdf(
                    file_path=file_path,
                    results_list=self.results_list,
                    title=title,
                    image_paths=img_paths,
                    captions=img_caps
                )
                QMessageBox.information(self, "Download Complete", "The results have been saved successfully.")
            except Exception as e:
                QMessageBox.critical(self, "PDF Error", f"An error occurred while generating the PDF: {e}")

    # ### Download PNG/JPG ###
    def download_graph_image(self):
        options = QFileDialog.Options()
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Graph As",
            "",
            "PNG Files (*.png);;JPG Files (*.jpg);;All Files (*)",
            options=options
        )
        if file_path:
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

# ### Entry point ###

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python CircularVolcano_Approx2.py <dem_file_path> [original_file_name]")
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
