# complete_dem_analysis.py

import sys
import os
import json
import numpy as np
import rasterio
import time
import psutil
from contextlib import contextmanager

from scipy.ndimage import gaussian_filter   # ← niente 'sobel'

from PyQt5 import QtWidgets
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog, QMessageBox,
    QComboBox, QDialog, QLabel, QSizePolicy, QSpacerItem, QDialogButtonBox
)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QCursor

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5 import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

from mpl_toolkits.axes_grid1 import make_axes_locatable
from mpl_toolkits.axes_grid1.inset_locator import inset_axes  # non usato nella GUI, ma lasciato
from matplotlib import gridspec

import requests
import pandas as pd
import matplotlib.cm as cm
from matplotlib.widgets import RectangleSelector
import matplotlib.pyplot as plt

# ==== Memory & timing helpers ======================================
_process = psutil.Process(os.getpid())
_peak_mb = 0.0

def _mem_mb() -> float:
    return _process.memory_info().rss / (1024 * 1024)

def log_memory(tag: str = ""):
    """Logga snapshot RAM corrente e aggiorna il picco."""
    global _peak_mb
    cur = _mem_mb()
    if cur > _peak_mb:
        _peak_mb = cur
    print(f"[MEMORY] {tag}: {cur:.1f} MB (peak so far: {_peak_mb:.1f} MB)")

@contextmanager
def phase(name: str):
    """Context manager per tempi + RAM."""
    t0 = time.time()
    log_memory(f"{name}::start")
    try:
        yield
    finally:
        log_memory(f"{name}::end")
        dt = time.time() - t0
        print(f"[TIMING] {name}: {dt:.3f} s")

# ### Definizione della CustomNavigationToolbar ###
class CustomNavigationToolbar(NavigationToolbar):
    """
    Classe personalizzata per la NavigationToolbar di Matplotlib.
    Rimuove il pulsante di salvataggio predefinito.
    """
    def __init__(self, canvas, parent):
        super().__init__(canvas, parent)
        self.remove_save_button()

    def remove_save_button(self):
        """
        Rimuove il pulsante 'Save the figure' dalla toolbar.
        """
        for action in self.actions():
            if action.toolTip() == 'Save the figure':
                self.removeAction(action)
                break

# Funzioni di analisi (rimangono invariate, tranne aggiunta secondo slope)
def calculate_hillshade(dem, azimuth=45, altitude=45):
    x, y = np.gradient(dem)
    slope = np.pi / 2 - np.arctan(np.sqrt(x ** 2 + y ** 2))
    aspect = np.arctan2(-x, y)
    azimuth_rad = np.radians(azimuth)
    altitude_rad = np.radians(altitude)
    shaded = (
        np.sin(altitude_rad) * np.sin(slope) +
        np.cos(altitude_rad) * np.cos(slope) *
        np.cos(azimuth_rad - aspect)
    )
    hillshade = np.clip(shaded, 0, 1) * 255
    return hillshade

def calculate_aspect(dem):
    x, y = np.gradient(dem)
    aspect = np.arctan2(-x, y)
    aspect = np.degrees(aspect)
    aspect = np.where(aspect < 0, 360 + aspect, aspect)
    return aspect

def calculate_convexity(dem, amplification_factor=100):
    x, y = np.gradient(dem)
    xx, xy = np.gradient(x)
    yx, yy = np.gradient(y)
    convexity = xx + yy
    convexity *= amplification_factor
    return convexity

def shaded_relief(dem, scale=10):
    x, y = np.gradient(dem)
    shaded = np.sqrt(x ** 2 + y ** 2)
    shaded *= scale
    return shaded

def calculate_slope_2(dem):
    """Slope in gradi, metodo 1 (spaziatura implicita = 1)."""
    dz_dx, dz_dy = np.gradient(dem)
    slope = np.arctan(np.sqrt(dz_dx ** 2 + dz_dy ** 2)) * (180 / np.pi)
    return slope

def calculate_slope_res(dem, dx, dy):
    """
    Slope in gradi, metodo 2 (come la vecchia calcola_pendenza):
    usa la risoluzione spaziale esplicita dx, dy.
    """
    dzdx, dzdy = np.gradient(dem, dx, dy)
    slope = np.arctan(np.sqrt(dzdx**2 + dzdy**2))
    slope_deg = np.degrees(slope)
    return slope_deg

def calculate_curvature(dem):
    dz_dx = np.gradient(dem, axis=1)
    dz_dy = np.gradient(dem, axis=0)
    dz_dx2 = np.gradient(dz_dx, axis=1)
    dz_dy2 = np.gradient(dz_dy, axis=0)
    curvature = dz_dx2 + dz_dy2
    return curvature

def calculate_gaussian_curvature(dem, res, amplification_factor=10):
    dem_smoothed = gaussian_filter(dem, sigma=1)
    dzdx, dzdy = np.gradient(dem_smoothed, res)
    d2zdx2 = np.gradient(dzdx, res, axis=0)
    d2zdy2 = np.gradient(dzdy, res, axis=1)
    d2zdxdy = np.gradient(dzdy, res, axis=0)
    gaussian_curvature = d2zdx2 * d2zdy2 - d2zdxdy ** 2
    amplified_gaussian_curvature = gaussian_curvature * amplification_factor
    amplified_gaussian_curvature[amplified_gaussian_curvature < 0] = 0
    log_gaussian_curvature = np.log1p(amplified_gaussian_curvature)
    normalized_log_gaussian_curvature = (
        log_gaussian_curvature - np.min(log_gaussian_curvature)
    ) / (np.max(log_gaussian_curvature) - np.min(log_gaussian_curvature))
    return normalized_log_gaussian_curvature, log_gaussian_curvature

def gather_statistics(curvature, description=""):
    stats = {
        'min': float(np.min(curvature)),
        'max': float(np.max(curvature)),
        'mean': float(np.mean(curvature)),
        'median': float(np.median(curvature)),
        'std': float(np.std(curvature))
    }
    return {description: stats}, stats

def write_statistics_to_json(stats, filename="output_statistics.json"):
    with open(filename, 'w') as f:
        json.dump(stats, f, indent=4)

def calculate_roughness(dem, window=3):
    from scipy.ndimage import generic_filter
    roughness = generic_filter(dem, np.std, size=window)
    return roughness

def load_dem(dem_path):
    with rasterio.open(dem_path) as src:
        dem = src.read(1)  # Read the first channel
        profile = src.profile
        transform = src.transform  # Affine transformation
        res = src.res[0]
    return dem, profile, transform, res

# Dialog per selezionare il colormap
class ColormapDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Colormap")
        self.selected_colormap = "terrain"  # Default colormap

        layout = QVBoxLayout()

        label = QLabel("Choose a colormap:")
        layout.addWidget(label)

        self.combo_box = QComboBox()
        # Aggiungi una lista di colormap di matplotlib
        self.combo_box.addItems(sorted(m for m in cm.datad if not m.endswith("_r")))
        self.combo_box.setCurrentText(self.selected_colormap)
        layout.addWidget(self.combo_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def get_selected_colormap(self):
        return self.combo_box.currentText()

# ==================== NUOVE UTILITY PER PNG + MANIFEST ====================

def _resolve_process_id(cli_process_id: str | None) -> str:
    env_id = os.environ.get("PROCESS_ID")
    if env_id and len(env_id) > 0:
        return env_id
    if cli_process_id and len(cli_process_id) > 0:
        return cli_process_id
    return f"local_{int(time.time())}"

def _ensure_outputs_dir(process_id: str) -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(base_dir, "outputs", process_id)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def _save_triplets_pngs(analysis_triplets, titles, cmaps, units, descriptions, file_name, out_dir):
    saved = []
    num_triplets = len(analysis_triplets)

    FIG_W, FIG_H   = 18.0, 5.0
    LEFT, RIGHT    = 0.015, 0.985
    TOP, BOTTOM    = 0.92, 0.20
    WSPACE         = 0.05
    CB_FRACTION    = 0.035
    CB_PAD         = 0.015
    TITLE_PAD      = 8
    DESC_Y         = -0.20
    DPI_SAVE       = 180

    for i in range(num_triplets):
        dem_data, data1, data2 = analysis_triplets[i]
        idx = i * 3
        t_list = titles[idx:idx+3]
        u_list = units[idx:idx+3]
        d_list = descriptions[idx:idx+3]
        c_list = cmaps[idx:idx+3]

        fig = plt.figure(figsize=(FIG_W, FIG_H))
        gs = gridspec.GridSpec(1, 3, figure=fig, wspace=WSPACE)
        fig.subplots_adjust(left=LEFT, right=RIGHT, top=TOP, bottom=BOTTOM, wspace=WSPACE)

        data_arrays = [dem_data, data1, data2]
        for j in range(3):
            ax = fig.add_subplot(gs[0, j])
            im = ax.imshow(
                data_arrays[j],
                cmap=c_list[j],
                origin='upper',
                interpolation='nearest',
                resample=False
            )
            ax.set_title(t_list[j], pad=TITLE_PAD, fontsize=11)
            ax.set_aspect('equal', adjustable='box')

            divider = make_axes_locatable(ax)
            cax = divider.append_axes("right", size="3.5%", pad=0.20)
            cbar = fig.colorbar(im, cax=cax)
            cbar.set_label(f"{u_list[j]}", rotation=90, fontsize=9)

            for spine in cax.spines.values():
                spine.set_visible(True)
                spine.set_edgecolor('black')
                spine.set_linewidth(0.8)

            ax.text(0.5, DESC_Y, d_list[j], transform=ax.transAxes,
                    ha='center', fontsize=8, wrap=True)

        fig.suptitle(f"Location: {file_name} ({i+1}/{num_triplets})", fontsize=13)

        fname = f"triplet_{i+1:02d}.png"
        fpath = os.path.join(out_dir, fname)
        try:
            fig.savefig(fpath, dpi=DPI_SAVE)
            saved.append({
                "filename": fname,
                "abs_path": fpath,
                "public_path": f"/outputs/{os.path.basename(out_dir)}/{fname}",
                "titles": t_list,
                "units": u_list,
                "descriptions": d_list
            })
            print(f"[DEBUG] Saved analysis PNG: {fpath}")
        except Exception as e:
            print(f"[ERROR] Failed to save {fpath}: {e}")
        finally:
            plt.close(fig)

    return saved


def _write_manifest_json(process_id: str, out_dir: str, saved_entries: list, source: str = "complete_dem_analysis", original_file_name: str = ""):
    manifest = {
        "processId": process_id,
        "source": source,
        "original_file_name": original_file_name,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "images": saved_entries
    }
    manifest_path = os.path.join(out_dir, "analysis_images.json")
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        print(f"[DEBUG] Wrote manifest: {manifest_path}")
    except Exception as e:
        print(f"[ERROR] Failed to write manifest JSON: {e}")


def _save_dem_overview_png(dem, out_dir, file_name, nodata_value=None):
    fig = plt.figure(figsize=(14.5, 5.5))
    ax = fig.add_subplot(111)

    mask = ~np.isfinite(dem)
    if nodata_value is not None:
        mask |= np.isclose(dem, nodata_value)

    if mask.any():
        valid_min = np.nanmin(dem[~mask]) if (~mask).any() else 0.0
        dem_filled = dem.copy()
        dem_filled[mask] = valid_min
    else:
        dem_filled = dem

    h, w = dem_filled.shape
    im = ax.imshow(
        dem_filled,
        cmap='terrain',
        origin='upper',
        interpolation='nearest',
        resample=False,
        extent=(-0.5, w - 0.5, h - 0.5, -0.5)
    )
    ax.set_title("DEM", pad=8)
    ax.set_aspect('equal', adjustable='box')

    CB_SIZE = "4%"
    CB_PAD  = 0.3
    divider = make_axes_locatable(ax)
    cax = divider.append_axes("right", size=CB_SIZE, pad=CB_PAD)

    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label("Elevation (m)", rotation=90)

    cbar.outline.set_visible(False)
    for side in ("left", "right", "top", "bottom"):
        sp = cax.spines[side]
        sp.set_visible(True)
        sp.set_linewidth(0.6)
        try:
            sp.set_edgecolor("black")
        except Exception:
            sp.set_color("black")

    cax.set_facecolor('none')
    cax.grid(False)
    cax.tick_params(length=3)

    fig.subplots_adjust(left=0.050, right=0.890, top=0.88, bottom=0.16)
    fig.suptitle(f"Location: {file_name}", fontsize=14)

    out_path = os.path.join(out_dir, "dem_overview.png")
    fig.savefig(out_path, dpi=170)
    plt.close(fig)

    return {
        "filename": "dem_overview.png",
        "abs_path": out_path,
        "public_path": f"/outputs/{os.path.basename(out_dir)}/dem_overview.png",
        "titles": ["DEM"],
        "units": ["m"],
        "descriptions": ["Represents terrain elevation in meters above sea level."]
    }

def _save_doublets_from_arrays(analysis_triplets, titles, cmaps, units, descriptions, file_name, out_dir):
    saved = []
    num_triplets = len(analysis_triplets)

    for i in range(num_triplets):
        dem_data, data1, data2 = analysis_triplets[i]
        base_idx = i * 3
        t_list = titles[base_idx+1:base_idx+3]
        c_list = cmaps[base_idx+1:base_idx+3]
        u_list = units[base_idx+1:base_idx+3]
        d_list = descriptions[base_idx+1:base_idx+3]

        fig = plt.figure(figsize=(14.5, 5.5))
        gs = gridspec.GridSpec(
            1, 3, figure=fig,
            width_ratios=[1.0, 0.08, 1.0],
            wspace=0.15
        )

        ax1 = fig.add_subplot(gs[0, 0])
        im1 = ax1.imshow(data1, cmap=c_list[0], origin='upper')
        ax1.set_title(t_list[0], pad=8)
        ax1.set_aspect('equal', adjustable='box')

        divider1 = make_axes_locatable(ax1)
        cax1 = divider1.append_axes("right", size="4.6%", pad=0.25)
        cbar1 = fig.colorbar(im1, cax=cax1)
        cbar1.set_label(f"{u_list[0]}", rotation=90)

        ax1.text(0.5, -0.18, d_list[0], transform=ax1.transAxes,
                 ha='center', fontsize=9, wrap=True)

        ax_spacer = fig.add_subplot(gs[0, 1])
        ax_spacer.axis('off')

        ax2 = fig.add_subplot(gs[0, 2])
        im2 = ax2.imshow(data2, cmap=c_list[1], origin='upper')
        ax2.set_title(t_list[1], pad=8)
        ax2.set_aspect('equal', adjustable='box')

        divider2 = make_axes_locatable(ax2)
        cax2 = divider2.append_axes("right", size="4.6%", pad=0.25)
        cbar2 = fig.colorbar(im2, cax=cax2)
        cbar2.set_label(f"{u_list[1]}", rotation=90)

        ax2.text(0.5, -0.18, d_list[1], transform=ax2.transAxes,
                 ha='center', fontsize=9, wrap=True)

        fig.suptitle(f"Location: {file_name} — Panel {i+1}/{num_triplets}", fontsize=13)

        fname = f"double_{i+1:02d}.png"
        fpath = os.path.join(out_dir, fname)
        try:
            fig.savefig(fpath, dpi=170)
            saved.append({
                "filename": fname,
                "abs_path": fpath,
                "public_path": f"/outputs/{os.path.basename(out_dir)}/{fname}",
                "titles": t_list,
                "units": u_list,
                "descriptions": d_list
            })
            print(f"[DEBUG] Saved analysis double-panel PNG: {fpath}")
        except Exception as e:
            print(f"[ERROR] Failed to save {fpath}: {e}")
        finally:
            plt.close(fig)

    return saved

# ==================== FINE UTILITY PNG + MANIFEST ====================

# Classe PyQt5 Application con le modifiche richieste
class DEMAnalysisApp(QMainWindow):
    def __init__(self, dem, profile, analysis_triplets, titles, cmaps, units, descriptions, file_name):
        super().__init__()
        self.setWindowTitle('DEM Analysis')
        self.dem = dem
        self.profile = profile
        self.analysis_triplets = analysis_triplets
        self.titles = titles
        self.cmaps = cmaps
        self.units = units
        self.descriptions = descriptions
        self.file_name = file_name
        self.current_index = 0

        # Nuove proprietà per colormap e selezione grafico
        self.selected_colormap = "terrain"  # Default colormap
        self.selected_graph = 0  # Indice del grafico selezionato (0, 1, 2)

        # Liste per memorizzare immagini, colorbar e assi dei grafici
        self.images = [None, None, None]
        self.colorbars = [None, None, None]
        self.image_axes = [None, None, None]
        self.cbar_axes = [None, None, None]  # <-- NUOVO: axes dedicati delle colorbar
        self.rectangle_selectors = [None, None, None]

        # Etichetta per i tooltips
        self.tooltip_label = QLabel("", self)
        self.tooltip_label.setStyleSheet("background-color: white; border: 1px solid black;")
        self.tooltip_label.setVisible(False)
        self.tooltip_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.tooltip_label.setWindowFlags(Qt.ToolTip)

        self.initUI()

    def initUI(self):
        # Widget centrale
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Layout principale
        main_layout = QVBoxLayout(central_widget)

        # Figura Matplotlib e Canvas con constrained_layout
        self.figure = Figure(figsize=(15, 8), constrained_layout=True)
        self.canvas = FigureCanvas(self.figure)
        main_layout.addWidget(self.canvas)

        # Creazione della Custom Navigation Toolbar (senza pulsante di salvataggio)
        self.toolbar = CustomNavigationToolbar(self.canvas, self)
        
        # Pulsanti e toolbar
        nav_layout = QHBoxLayout()
        main_layout.addLayout(nav_layout)

        nav_layout.addWidget(self.toolbar)
        nav_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.prev_button = QPushButton('Previous')
        self.prev_button.setFixedSize(100, 40)
        self.prev_button.clicked.connect(self.previous_triplet)
        nav_layout.addWidget(self.prev_button)

        self.next_button = QPushButton('Next')
        self.next_button.setFixedSize(100, 40)
        self.next_button.clicked.connect(self.next_triplet)
        nav_layout.addWidget(self.next_button)

        self.view3d_button = QPushButton('View 3D Model')
        self.view3d_button.setFixedSize(120, 40)
        self.view3d_button.clicked.connect(self.display_volcano_3d)
        nav_layout.addWidget(self.view3d_button)

        nav_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.graph_selection_label = QLabel("Select Graph:")
        self.graph_selection_label.setFixedSize(100, 40)
        self.graph_selection_label.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        nav_layout.addWidget(self.graph_selection_label)

        self.graph_selection_combo = QComboBox()
        self.graph_selection_combo.setFixedSize(200, 40)
        self.graph_selection_combo.currentIndexChanged.connect(self.update_selected_graph)
        nav_layout.addWidget(self.graph_selection_combo)

        self.select_colormap_button = QPushButton('Select Colormap')
        self.select_colormap_button.setFixedSize(150, 40)
        self.select_colormap_button.clicked.connect(self.select_colormap)
        nav_layout.addWidget(self.select_colormap_button)

        nav_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.download_image_button = QPushButton('Download as PNG or JPG')
        self.download_image_button.setFixedSize(200, 40)
        self.download_image_button.clicked.connect(self.download_graph_image)
        nav_layout.addWidget(self.download_image_button)

        self.export_data_button = QPushButton('Export Data')
        self.export_data_button.setFixedSize(120, 40)
        self.export_data_button.clicked.connect(self.export_data)
        nav_layout.addWidget(self.export_data_button)

        nav_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        # Connessione degli eventi per i tooltips
        self.canvas.mpl_connect("motion_notify_event", self.on_motion)

        # Display iniziale
        self.update_display()

    def update_selected_graph(self):
        self.selected_graph = self.graph_selection_combo.currentIndex()
        print(f"[DEBUG] Selected graph index: {self.selected_graph}")

    def update_display(self):
        print("[DEBUG] Updating display...")
        self.figure.clear()
        fig = self.figure

        # === Margine esterno per non tagliare la 3ª colorbar ===
        fig.set_constrained_layout_pads(w_pad=0.60, h_pad=0.02, wspace=0.16, hspace=0.08)

        # Gridspec: spazio tra i pannelli (non i bordi esterni)
        gs = gridspec.GridSpec(1, 3, figure=fig, wspace=0.16)

        # Ottieni il tripletto corrente
        dem_data, data1, data2 = self.analysis_triplets[self.current_index]

        # Titolo generale
        fig.suptitle(
            f"Location: {self.file_name} ({self.current_index + 1}/{len(self.analysis_triplets)})",
            fontsize=16
        )

        idx = self.current_index * 3

        # Lista dei dati e titoli
        data_list = [dem_data, data1, data2]
        titles = self.titles[idx:idx + 3]
        units = self.units[idx:idx + 3]
        descriptions = self.descriptions[idx:idx + 3]
        cmaps = self.cmaps[idx:idx + 3]

        # Reset handle
        self.images = [None, None, None]
        self.colorbars = [None, None, None]
        self.image_axes = [None, None, None]
        self.cbar_axes = [None, None, None]
        for selector in self.rectangle_selectors:
            if selector is not None:
                selector.set_active(False)
        self.rectangle_selectors = [None, None, None]

        # Plot dei tre grafici
        for i in range(3):
            try:
                ax = fig.add_subplot(gs[0, i])
                im = ax.imshow(data_list[i], cmap=cmaps[i], origin='upper')
                ax.set_title(titles[i], pad=10)
                ax.set_aspect('equal', adjustable='box')

                # === Colorbar accanto al riquadro dati, stessa altezza dell'immagine ===
                divider = make_axes_locatable(ax)
                cax = divider.append_axes("right", size="4.6%", pad=0.10)
                cax.set_in_layout(True)  # IMPORTANT: la cbar entra nel layout → niente clip
                cbar = fig.colorbar(im, cax=cax)
                cbar.set_label(f"{units[i]}", rotation=90)

                # Aggiungi descrizione
                ax.text(
                    0.5, -0.15, descriptions[i],
                    transform=ax.transAxes, ha='center', fontsize=10, wrap=True
                )

                # Memorizza le referenze
                self.images[i] = im
                self.colorbars[i] = cbar
                self.image_axes[i] = ax
                self.cbar_axes[i] = cax

                # Selettore rettangolo
                self.rectangle_selectors[i] = RectangleSelector(
                    ax, self.on_select,
                    useblit=True, button=[1],
                    minspanx=5, minspany=5, spancoords='pixels',
                    interactive=True
                )

                print(f"[DEBUG] Plotted graph {i} with title '{titles[i]}'")
            except Exception as e:
                print(f"[ERROR] Error plotting graph {i}: {e}")

        self.canvas.draw()

        # Pulsanti
        if self.current_index == 0:
            self.prev_button.setVisible(False)
            self.next_button.setVisible(True)
        elif self.current_index == len(self.analysis_triplets) - 1:
            self.prev_button.setVisible(True)
            self.next_button.setVisible(False)
        else:
            self.prev_button.setVisible(True)
            self.next_button.setVisible(True)

        # ComboBox
        self.graph_selection_combo.blockSignals(True)
        self.graph_selection_combo.clear()
        current_titles = self.titles[idx:idx + 3]
        self.graph_selection_combo.addItems(current_titles)
        self.graph_selection_combo.setCurrentIndex(0)
        self.selected_graph = 0
        self.graph_selection_combo.blockSignals(False)
        print("[DEBUG] Display update complete.")

    def next_triplet(self):
        if self.current_index < len(self.analysis_triplets) - 1:
            self.current_index += 1
            print(f"[DEBUG] Moving to next triplet: {self.current_index}")
            self.update_display()

    def previous_triplet(self):
        if self.current_index > 0:
            self.current_index -= 1
            print(f"[DEBUG] Moving to previous triplet: {self.current_index}")
            self.update_display()

    def display_volcano_3d(self):
        try:
            print("[DEBUG] Displaying 3D model window.")
            self.three_d_window = QtWidgets.QMainWindow()
            self.three_d_window.setWindowTitle('3D Model')
            central_widget = QtWidgets.QWidget()
            self.three_d_window.setCentralWidget(central_widget)
            layout = QVBoxLayout(central_widget)

            figure_3d = Figure(figsize=(10, 8))
            canvas_3d = FigureCanvas(figure_3d)
            layout.addWidget(canvas_3d)

            ax = figure_3d.add_subplot(111, projection='3d')

            x = np.arange(0, self.dem.shape[1])
            y = np.arange(0, self.dem.shape[0])
            x, y = np.meshgrid(x, y)

            z = self.dem

            surface = ax.plot_surface(
                x, y, z, cmap=self.selected_colormap, edgecolor='none',
                rstride=1, cstride=1, antialiased=True
            )

            figure_3d.colorbar(surface, ax=ax, shrink=0.5, aspect=5)
            ax.set_title('3D Model', fontsize=15)
            ax.set_xlabel('X Coordinate', fontsize=12)
            ax.set_ylabel('Y Coordinate', fontsize=12)
            ax.set_zlabel('Elevation (m)', fontsize=12)
            ax.view_init(elev=60, azim=20)

            canvas_3d.draw()
            self.three_d_window.show()
            print("[DEBUG] 3D model window displayed successfully.")
        except Exception as e:
            QMessageBox.critical(
                self, 
                "3D Model Error", 
                f"An error occurred while displaying the 3D model: {e}"
            )
            print(f"[ERROR] Error in display_volcano_3d: {e}")

    def download_graph_image(self):
        try:
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
                    format = 'png'
                    if not file_path.lower().endswith('.png'):
                        file_path += '.png'
                elif selected_filter.startswith("JPG"):
                    format = 'jpg'
                    if not file_path.lower().endswith('.jpg') and not file_path.lower().endswith('.jpeg'):
                        file_path += '.jpg'
                else:
                    format = 'png'
                    if not file_path.lower().endswith('.png'):
                        file_path += '.png'

                try:
                    self.figure.savefig(file_path, format=format)
                    QMessageBox.information(
                        self, 
                        "Success", 
                        f"Graph successfully saved as {format.upper()} to {file_path}"
                    )
                    print(f"[DEBUG] Graph saved successfully as {format.upper()} to {file_path}")
                except Exception as e:
                    QMessageBox.critical(
                        self, 
                        "Save Error", 
                        f"An error occurred while saving the graph: {e}"
                    )
                    print(f"[ERROR] Error during graph saving: {e}")
        except Exception as e:
            QMessageBox.critical(
                self, 
                "Save Error", 
                f"An error occurred while initiating the save dialog: {e}"
            )
            print(f"[ERROR] Error initiating graph saving: {e}")

    def select_colormap(self):
        try:
            dialog = ColormapDialog(self)
            if dialog.exec_() == QDialog.Accepted:
                new_colormap = dialog.get_selected_colormap()
                self.selected_colormap = new_colormap
                print(f"[DEBUG] Selected new colormap: {self.selected_colormap}")
                self.apply_colormap_to_selected_graph()
        except Exception as e:
            QMessageBox.critical(
                self, 
                "Colormap Selection Error", 
                f"An error occurred while selecting the colormap: {e}"
            )
            print(f"[ERROR] Error in select_colormap: {e}")

    def apply_colormap_to_selected_graph(self):
        try:
            if self.selected_graph not in [0, 1, 2]:
                print(f"[DEBUG] Invalid selected_graph index: {self.selected_graph}")
                return

            fig = self.figure

            ax = self.image_axes[self.selected_graph]
            im = self.images[self.selected_graph]
            cbar = self.colorbars[self.selected_graph]
            cax_saved = self.cbar_axes[self.selected_graph]

            if im is None or ax is None:
                print(f"[DEBUG] No image/axis associated with graph {self.selected_graph}")
                return

            im.set_cmap(self.selected_colormap)
            print(f"[DEBUG] Updated colormap for graph {self.selected_graph} to {self.selected_colormap}")

            # rimuovi la vecchia cbar
            if cbar is not None:
                try:
                    cbar.remove()
                except Exception:
                    pass

            # usa lo stesso cax se disponibile; altrimenti creane uno nuovo accanto all'ax
            if cax_saved is None:
                divider = make_axes_locatable(ax)
                cax = divider.append_axes("right", size="4.6%", pad=0.10)
                cax.set_in_layout(True)  # IMPORTANT: evita il clipping a destra
                self.cbar_axes[self.selected_graph] = cax
            else:
                cax = cax_saved
                cax.set_in_layout(True)
                cax.cla()

            # ricrea la colorbar nel cax dedicato → stessa altezza del box immagine
            cbar = fig.colorbar(im, cax=cax)
            cbar.set_label(f"{self.units[self.current_index * 3 + self.selected_graph]}", rotation=90)

            self.colorbars[self.selected_graph] = cbar

            self.canvas.draw()
            print(f"[DEBUG] Applied new colormap and updated colorbar for graph {self.selected_graph}")
        except Exception as e:
            QMessageBox.critical(
                self, 
                "Colormap Application Error", 
                f"An error occurred while applying the colormap: {e}"
            )
            print(f"[ERROR] Error in apply_colormap_to_selected_graph: {e}")

    def export_data(self):
        try:
            format_dialog = QMessageBox(self)
            format_dialog.setWindowTitle("Select Export Format")
            format_dialog.setText("Choose the format to export the data:")
            format_dialog.setIcon(QMessageBox.Question)
            csv_button = format_dialog.addButton("CSV", QMessageBox.AcceptRole)
            geotiff_button = format_dialog.addButton("GeoTIFF", QMessageBox.AcceptRole)
            cancel_button = format_dialog.addButton(QMessageBox.Cancel)
            format_dialog.exec_()

            if format_dialog.clickedButton() == csv_button:
                export_format = 'CSV'
            elif format_dialog.clickedButton() == geotiff_button:
                export_format = 'GeoTIFF'
            else:
                print("[DEBUG] Export operation canceled by user.")
                return

            options = QFileDialog.Options()
            directory = QFileDialog.getExistingDirectory(
                self,
                "Select Export Directory",
                "",
                options=options
            )
            if not directory:
                print("[DEBUG] Export directory selection canceled by user.")
                return

            try:
                for i, (dem_data, data1, data2) in enumerate(self.analysis_triplets, start=1):
                    current_titles = self.titles[(i-1)*3:i*3]
                    data_arrays = [dem_data, data1, data2]

                    for title, data in zip(current_titles, data_arrays):
                        safe_title = title.replace(" ", "_").replace("/", "_")
                        if export_format == 'CSV':
                            df = pd.DataFrame(data)
                            csv_filename = os.path.join(directory, f"Triplet{i}_{safe_title}.csv")
                            df.to_csv(csv_filename, index=False, header=False, na_rep="NaN")
                            print(f"[DEBUG] Exported {csv_filename}")
                        elif export_format == 'GeoTIFF':
                            geotiff_filename = os.path.join(directory, f"Triplet{i}_{safe_title}.tif")
                            new_profile = self.profile.copy()
                            new_profile.update(dtype=rasterio.float32, count=1, nodata=None)
                            with rasterio.open(geotiff_filename, 'w', **new_profile) as dst:
                                dst.write(data.astype(rasterio.float32), 1)
                            print(f"[DEBUG] Exported {geotiff_filename}")
                QMessageBox.information(
                    self, 
                    "Export Successful", 
                    f"All data successfully exported as {export_format} to {directory}"
                )
                print(f"[DEBUG] All data successfully exported as {export_format} to {directory}")
            except Exception as e:
                QMessageBox.critical(
                    self, 
                    "Export Error", 
                    f"An error occurred while exporting the data: {e}"
                )
                print(f"[ERROR] Error during data export: {e}")
        except Exception as e:
            QMessageBox.critical(
                self, 
                "Export Error", 
                f"An error occurred during export setup: {e}"
            )
            print(f"[ERROR] Error in export_data setup: {e}")

    # Evento di movimento del mouse per i tooltips
    def on_motion(self, event):
        try:
            if event.inaxes in self.image_axes:
                ax_index = self.image_axes.index(event.inaxes)
                if ax_index == -1:
                    self.tooltip_label.setVisible(False)
                    return
                im = self.images[ax_index]
                if im is None:
                    self.tooltip_label.setVisible(False)
                    return
                if event.xdata is None or event.ydata is None:
                    self.tooltip_label.setVisible(False)
                    return
                xdata = int(event.xdata)
                ydata = int(event.ydata)
                if 0 <= xdata < self.dem.shape[1] and 0 <= ydata < self.dem.shape[0]:
                    z = self.dem[ydata, xdata]
                    self.tooltip_label.setText(f"X: {xdata}, Y: {ydata}, Elevation: {z:.2f} m")
                    self.tooltip_label.adjustSize()
                    cursor_pos = QCursor.pos()
                    window_pos = self.mapFromGlobal(cursor_pos)
                    self.tooltip_label.move(window_pos.x() + 10, window_pos.y() + 10)
                    self.tooltip_label.setVisible(True)
                else:
                    self.tooltip_label.setVisible(False)
            else:
                self.tooltip_label.setVisible(False)
        except Exception as e:
            print(f"[ERROR] Error in on_motion: {e}")
            self.tooltip_label.setVisible(False)

    # Funzione di callback per la selezione di una regione
    def on_select(self, eclick, erelease):
        try:
            x1, y1 = eclick.xdata, eclick.ydata
            x2, y2 = erelease.xdata, erelease.ydata

            if x1 is None or y1 is None or x2 is None or y2 is None:
                print("[DEBUG] Selection coordinates are None.")
                return

            x1, y1 = int(x1), int(y1)
            x2, y2 = int(x2), int(y2)

            x_min, x_max = sorted([x1, x2])
            y_min, y_max = sorted([y1, y2])

            x_min = max(x_min, 0)
            y_min = max(y_min, 0)
            x_max = min(x_max, self.dem.shape[1])
            y_max = min(y_max, self.dem.shape[0])

            selected_region = self.dem[y_min:y_max, x_min:x_max]

            if selected_region.size == 0:
                QMessageBox.warning(self, "Selection Warning", "Selected region is empty.")
                print("[DEBUG] Selected region is empty.")
                return

            local_stats = {
                'min': float(np.min(selected_region)),
                'max': float(np.max(selected_region)),
                'mean': float(np.mean(selected_region)),
                'median': float(np.median(selected_region)),
                'std': float(np.std(selected_region))
            }

            stats_text = (
                f"Selected Region Statistics:\n"
                f"Min: {local_stats['min']}\n"
                f"Max: {local_stats['max']}\n"
                f"Mean: {local_stats['mean']:.2f}\n"
                f"Median: {local_stats['median']}\n"
                f"Std Dev: {local_stats['std']:.2f}"
            )
            QMessageBox.information(self, "Local Statistics", stats_text)
            print("[DEBUG] Displayed local statistics for selected region.")
        except Exception as e:
            QMessageBox.critical(
                self, 
                "Selection Error", 
                f"An error occurred during region selection: {e}"
            )
            print(f"[ERROR] Error in on_select: {e}")

# Funzione principale
def main():
    if len(sys.argv) < 2:
        print("Error: specify the DEM file path as an argument.")
        sys.exit(1)

    dem_path = sys.argv[1]

    # Controllo esplicito per il nome originale del file
    original_file_name = sys.argv[2] if len(sys.argv) > 2 else "Unknown"

    # Ottieni il process_id dagli argomenti della riga di comando
    cli_process_id = sys.argv[3] if len(sys.argv) > 3 else None
    # Preferisci l'env var PROCESS_ID se presente
    process_id = _resolve_process_id(cli_process_id)
    if process_id:
        print(f"[DEBUG] Process ID: {process_id}")

    print(f"[DEBUG] DEM path provided: {dem_path}")
    print(f"[DEBUG] Original file name: {original_file_name}")
    print(f"[DEBUG] Number of arguments received: {len(sys.argv)}")

    # ==== Benchmark: start ====
    _t_all_start = time.time()
    log_memory("program_start")

    # Caricamento DEM (timed)
    try:
        with phase("load_dem"):
            dem, profile, transform, res = load_dem(dem_path)
            print(f"[DEBUG] DEM loaded successfully. Dimensions: {dem.shape}, Resolution: {res}")
            log_memory("after_load_dem")
    except Exception as e:
        print(f"Error loading DEM: {e}")
        sys.exit(1)

    # Esegui le varie analisi
    try:
        with phase("derivatives"):
            with phase("calc: hillshade"):
                print("[DEBUG] Starting Hillshade calculation.")
                hillshade = calculate_hillshade(dem)
                print("[DEBUG] Hillshade calculated successfully.")

            with phase("calc: aspect"):
                print("[DEBUG] Starting Aspect calculation.")
                aspect = calculate_aspect(dem)
                print("[DEBUG] Aspect calculated successfully.")

            with phase("calc: convexity"):
                print("[DEBUG] Starting Convexity calculation.")
                convexity = calculate_convexity(dem, amplification_factor=200)
                print("[DEBUG] Convexity calculated successfully.")

            with phase("calc: shaded_relief"):
                print("[DEBUG] Starting Shaded Relief calculation.")
                shaded = shaded_relief(dem, scale=10)
                print("[DEBUG] Shaded Relief calculated successfully.")

            with phase("calc: roughness"):
                print("[DEBUG] Starting Roughness calculation.")
                roughness = calculate_roughness(dem)
                print("[DEBUG] Roughness calculated successfully.")

            with phase("calc: slope_method1"):
                print("[DEBUG] Starting Slope calculation (method 1).")
                slope_deg_1 = calculate_slope_2(dem)
                print("[DEBUG] Slope (method 1) calculated successfully.")

            with phase("calc: slope_method2"):
                print("[DEBUG] Starting Slope calculation (method 2 - dx,dy=res).")
                slope_deg_2 = calculate_slope_res(dem, res, res)
                print("[DEBUG] Slope (method 2) calculated successfully.")

            with phase("calc: curvature"):
                print("[DEBUG] Starting Curvature calculation.")
                curvature = calculate_curvature(dem)
                print("[DEBUG] Curvature calculated successfully.")

            with phase("calc: gaussian_curvature"):
                print("[DEBUG] Starting Gaussian Curvature calculation.")
                normalized_log_gaussian_curvature, log_gaussian_curvature = calculate_gaussian_curvature(dem, res)
                print("[DEBUG] Gaussian Curvature calculated successfully.")

            with phase("calc: smooth+normalize"):
                print("[DEBUG] Starting curvature amplify + smooth + normalize.")
                amplification_factor = 5
                curvature_amplified = curvature * amplification_factor
                curvature_smoothed = gaussian_filter(curvature_amplified, sigma=1)
                curvature_smoothed_normalized = (
                    curvature_smoothed - np.min(curvature_smoothed)
                ) / (np.max(curvature_smoothed) - np.min(curvature_smoothed))
                print("[DEBUG] Curvature smoothing/normalization completed.")
    except Exception as e:
        print(f"Error during analysis calculations: {e}")
        sys.exit(1)

    # Raccogli statistiche in un dizionario (timed)
    with phase("statistics_json"):
        total_statistics, gauss_curv_stats = gather_statistics(
            log_gaussian_curvature, "Logarithmic Amplified Gaussian Curvature"
        )
        _, smooth_curv_stats = gather_statistics(
            curvature_smoothed, "Amplified and Smoothed Curvature"
        )
        write_statistics_to_json(total_statistics, filename="output_statistics.json")
        print("[DEBUG] Statistics written to output_statistics.json")
        log_memory("after_statistics_json")

    # Organizza le analisi in triplette (timed)
    with phase("prepare_triplets"):
        print("[DEBUG] Preparing analysis triplets.")
        analysis_triplets = [
            (dem, shaded, hillshade),                             # Prima finestra
            (dem, slope_deg_1, slope_deg_2),                      # Seconda finestra
            (dem, roughness, convexity),                          # Terza finestra
            (aspect, curvature_smoothed_normalized,               # Quarta finestra
             normalized_log_gaussian_curvature)
        ]
        print("[DEBUG] Analysis triplets prepared.")
        titles = [
            "DEM", "Shaded Relief", "Hillshade",
            "DEM", "Slope 1", "Slope 2",
            "DEM", "Roughness", "Convexity",
            "Aspect", "Amplified and Smoothed Curvature", "Logarithmic Amplified Gaussian Curvature"
        ]
        cmaps = [
            "terrain", "gray", "gray",
            "terrain", "plasma", "plasma",
            "terrain", "seismic", "twilight",
            "twilight", "plasma", "plasma"
        ]
        units = [
            "m", "Adimensional", "Adimensional",   # Prima finestra
            "m", "Degrees", "Degrees",            # Seconda finestra
            "m", "Adimensional", "Adimensional",  # Terza finestra
            "Degrees", "Adimensional", "Adimensional"   # Quarta finestra
        ]
        descriptions = [
            "Represents terrain elevation in meters above sea level.",
            "Simulates light and shadow effects on the terrain.",
            "Relative terrain illumination (Sun Alt 45°, Az 45°).",
            "Represents terrain elevation in meters above sea level.",
            "Slope in degrees (method 1).",
            "Slope in degrees (method 2, uses DEM resolution).",
            "Represents terrain elevation in meters above sea level.",
            "Measures local variations in elevation.",
            "Shows whether terrain areas are convex or concave.",
            "Direction of slope (°): 0°=N, clockwise to 360°.",
            "Smoothed curvature for improved interpretation.",
            "Gaussian curvature for detailed terrain analysis."
        ]
        log_memory("after_prepare_triplets")

    # ========== NEW: salvataggio DEM singolo + doppietti e manifest ==========
    with phase("save_pngs_and_manifest"):
        try:
            out_dir = _ensure_outputs_dir(process_id)

            # 1) DEM una sola volta
            dem_entry = _save_dem_overview_png(dem, out_dir, original_file_name)

            # 2) Per ogni tripletta, salva SOLO i pannelli 2 e 3 (senza DEM / senza Aspect nel caso 4)
            double_entries = _save_doublets_from_arrays(
                analysis_triplets, titles, cmaps, units, descriptions, original_file_name, out_dir
            )

            saved_entries = [dem_entry] + double_entries

            _write_manifest_json(
                process_id,
                out_dir,
                saved_entries,
                source="complete_dem_analysis",
                original_file_name=original_file_name
            )
        except Exception as e:
            print(f"[ERROR] Error during PNG/manifest generation: {e}")
    # ================================================================================

    # Invia notifica (opzionale)
    if process_id:
        with phase("notify_server"):
            try:
                print(f"[DEBUG] Sending completion notification to server for process ID: {process_id}")
                response = requests.post(f'http://localhost:5000/processComplete/{process_id}')
                print(f"[DEBUG] Server response status code: {response.status_code}")
                print(f"[DEBUG] Server response content: {response.content}")
            except Exception as e:
                print(f"[ERROR] Error sending completion notification: {e}")

    print(f"[RESULT] Peak RAM = {_peak_mb:.1f} MB")
    print(f"[TIMING] total_end_to_end = {time.time() - _t_all_start:.3f} s")
    log_memory("before_gui_start")

    # Avvia l'applicazione PyQt5
    try:
        app = QApplication(sys.argv)
        ex = DEMAnalysisApp(
            dem, profile, analysis_triplets, titles, cmaps, units, descriptions, original_file_name
        )

        def show_main_window():
            ex.showMaximized()
            print("[DEBUG] Main window displayed.")

        QTimer.singleShot(0, show_main_window)
        sys.exit(app.exec_())
    except Exception as e:
        print(f"[ERROR] Error starting the application: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
