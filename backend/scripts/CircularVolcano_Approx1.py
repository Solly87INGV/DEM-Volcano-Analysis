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
import json
import numpy as np
import rasterio  # For reading DEM files in .tif format
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

def distance_between_points(x1, y1, x2, y2):
    return np.sqrt((x2 - x1)**2 + (y2 - y1)**2)

def calculate_area(contour, pixel_size):
    x = contour[:, 1]
    y = contour[:, 0]
    area_in_pixels = 0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
    area_in_meters = area_in_pixels * (pixel_size ** 2)
    return area_in_meters


# ———————— Utility path/manifest ——————————

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))

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


# ———————— App principale ——————————

class VolumeAnalysisApp(QMainWindow):
    def __init__(self, dem):
        super().__init__()
        self.setWindowTitle('Volcano Volume Analysis')
        self.dem = dem
        self.calculate_results()  # calcoli prima della UI
        self.initUI()
        
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
        
        self.update_display()
        
    def calculate_results(self):
        try:
            # Calculate results and store for later use
            pixel_size = 30  # Modify this value if necessary
            self.base_contour = find_lowest_base_contour(self.dem, base_elevation_ratio=0.05)
            self.base_point1, self.base_point2 = find_opposite_base_points(self.base_contour)

            self.slope = calculate_slope(self.dem)
            self.caldera_contour = find_caldera_contour(self.dem, level_ratio=0.8)
            self.max_slope_index1, self.max_slope_index2 = find_opposite_slope_points(self.slope, self.caldera_contour)

            # Calculate base distances
            pA1, pA2 = self.base_point1[0], self.base_point1[1]
            pB1, pB2 = self.base_point2[0], self.base_point2[1]
            distance_pixel_base = distance_between_points(pA1, pA2, pB1, pB2)
            distance_meters_base = distance_pixel_base * pixel_size
            distance_base_km = distance_meters_base * 1e-3  # km

            # Calculate caldera distances
            pA1_slope, pA2_slope = self.max_slope_index1[0], self.max_slope_index1[1]
            pB1_slope, pB2_slope = self.max_slope_index2[0], self.max_slope_index2[1]
            distance_pixel_caldera = distance_between_points(pA1_slope, pA2_slope, pB1_slope, pB2_slope)
            distance_meters_caldera = distance_pixel_caldera * pixel_size
            distance_caldera_km = distance_meters_caldera * 1e-3  # km

            # Calculate areas
            area_base = calculate_area(self.base_contour, pixel_size) * 1e-6  # m² to km²
            area_caldera = calculate_area(self.caldera_contour, pixel_size) * 1e-6  # m² to km²

            # Calculate volumes
            h_max = np.max(self.dem)
            R1 = distance_meters_base / 2
            R2 = distance_meters_caldera / 2
            v = (1/3) * np.pi * h_max * (R1**2 + R2**2 + R1 * R2)
            v_km3 = v * 1e-9  # m³ to km³

            r2 = R2 * 1e-3
            v_caldera = (2/3) * np.pi * (r2**3)

            self.v_volcano = v_km3 - v_caldera

            # Store results text
            self.results_text = (
                f"Base area of the volcano: {area_base:.2f} km²\n"
                f"Base width (Distance between opposite points of the base): {distance_base_km:.2f} km\n"
                f"Caldera area of the volcano: {area_caldera:.2f} km²\n"
                f"Caldera width (Distance between opposite points of the caldera): {distance_caldera_km:.2f} km\n"
                f"Total volume of the volcanic edifice: {v_km3:.2f} km³\n"
                f"Caldera volume: {v_caldera:.2f} km³\n"
                f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
            )

            # Create a list of results for download
            self.results_list = [
                f"Base area of the volcano: {area_base:.2f} km²",
                f"Base width (Distance between opposite points of the base): {distance_base_km:.2f} km",
                f"Caldera area of the volcano: {area_caldera:.2f} km²",
                f"Caldera width (Distance between opposite points of the caldera): {distance_caldera_km:.2f} km",
                f"Total volume of the volcanic edifice: {v_km3:.2f} km³",
                f"Caldera volume: {v_caldera:.2f} km³",
                f"Effective volume of the volcanic edifice: {self.v_volcano:.2f} km³"
            ]

            # Store descriptions in English for the GUI
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
            # Optionally log the error or handle it as needed
            # print(f"Error during calculation: {e}")
            
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


# ———————— Main ——————————

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python CircularVolcano_Approx1.py <dem_file_path>")
        sys.exit(1)

    dem_file_path = sys.argv[1]
    if not os.path.exists(dem_file_path):
        print(f"Error: File '{dem_file_path}' does not exist.")
        sys.exit(1)

    try:
        with rasterio.open(dem_file_path) as src:
            dem = src.read(1)
    except Exception as e:
        print(f"Error opening DEM file: {e}")
        sys.exit(1)

    app = QApplication(sys.argv)
    ex = VolumeAnalysisApp(dem)
    ex.showMaximized()
    sys.exit(app.exec_())
