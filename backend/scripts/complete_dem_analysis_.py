# complete_dem_analysis.py

import sys
import os
import json
import numpy as np
import rasterio
from scipy.ndimage import gaussian_filter, sobel
from skimage import measure
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog, QMessageBox
)
from PyQt5.QtCore import QTimer  # Importa QTimer
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib import gridspec  # For advanced subplot layout
from mpl_toolkits.mplot3d import Axes3D  # Necessary for 3D plots
import requests  # Per inviare richieste HTTP al server Node.js
import pandas as pd  # Per esportare i dati in CSV

# Function to calculate hillshade
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

# Function to calculate aspect
def calculate_aspect(dem):
    x, y = np.gradient(dem)
    aspect = np.arctan2(-x, y)
    aspect = np.degrees(aspect)
    aspect = np.where(aspect < 0, 360 + aspect, aspect)
    return aspect

# Function to calculate convexity
def calculate_convexity(dem, amplification_factor=100):
    x, y = np.gradient(dem)
    xx, xy = np.gradient(x)
    yx, yy = np.gradient(y)
    convexity = xx + yy
    convexity *= amplification_factor
    return convexity

# Function to create a shaded relief map
def shaded_relief(dem, scale=10):
    x, y = np.gradient(dem)
    shaded = np.sqrt(x ** 2 + y ** 2)
    shaded *= scale
    return shaded

# Function to calculate slope (slope 2)
def calculate_slope_2(dem):
    dz_dx, dz_dy = np.gradient(dem)
    slope = np.arctan(np.sqrt(dz_dx ** 2 + dz_dy ** 2)) * (180 / np.pi)
    return slope

# Function to calculate mean curvature
def calculate_curvature(dem):
    dz_dx = np.gradient(dem, axis=1)
    dz_dy = np.gradient(dem, axis=0)
    dz_dx2 = np.gradient(dz_dx, axis=1)
    dz_dy2 = np.gradient(dz_dy, axis=0)
    curvature = dz_dx2 + dz_dy2
    return curvature

# Function to calculate Gaussian curvature
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

# Function to gather statistics
def gather_statistics(curvature, description=""):
    stats = {
        'min': float(np.min(curvature)),
        'max': float(np.max(curvature)),
        'mean': float(np.mean(curvature)),
        'median': float(np.median(curvature)),
        'std': float(np.std(curvature))
    }
    return {description: stats}, stats

# Function to write statistics to a JSON file
def write_statistics_to_json(stats, filename="output_statistics.json"):
    with open(filename, 'w') as f:
        json.dump(stats, f, indent=4)

# Function to calculate roughness (rugosità)
def calculate_roughness(dem, window=3):
    from scipy.ndimage import generic_filter
    roughness = generic_filter(dem, np.std, size=window)
    return roughness

# Function to load a DEM from a file
def load_dem(dem_path):
    with rasterio.open(dem_path) as src:
        dem = src.read(1)  # Read the first channel
        profile = src.profile
        transform = src.transform  # Affine transformation
        res = src.res[0]
    return dem, profile, transform, res

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

        # Pulsanti di navigazione
        nav_layout = QHBoxLayout()
        main_layout.addLayout(nav_layout)

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

        # ### Aggiunta del Pulsante di Download in Formato PNG o JPG ###
        self.download_image_button = QPushButton('Download as PNG or JPG')
        self.download_image_button.setFixedSize(200, 40)
        self.download_image_button.clicked.connect(self.download_graph_image)
        nav_layout.addWidget(self.download_image_button)

        # ### Aggiunta del Pulsante di Esportazione dei Dati ###
        self.export_data_button = QPushButton('Export Data')
        self.export_data_button.setFixedSize(120, 40)
        self.export_data_button.clicked.connect(self.export_data)
        nav_layout.addWidget(self.export_data_button)

        # Nascondi il pulsante "Previous" all'avvio
        self.prev_button.setVisible(False)

        # Display iniziale
        self.update_display()

    def update_display(self):
        self.figure.clear()
        fig = self.figure

        # Configurazione di GridSpec
        gs = gridspec.GridSpec(1, 3, figure=fig)

        # Ottieni il tripletto corrente
        dem_data, data1, data2 = self.analysis_triplets[self.current_index]

        # Titolo generale
        fig.suptitle(
            f"Location: {self.file_name} ({self.current_index + 1}/{len(self.analysis_triplets)})",
            fontsize=16
        )

        idx = self.current_index * 3

        # Primo grafico (DEM)
        ax1 = fig.add_subplot(gs[0, 0])
        im1 = ax1.imshow(dem_data, cmap='terrain', origin='upper')
        ax1.set_title(self.titles[idx], pad=10)
        ax1.set_aspect('equal', adjustable='box')
        cbar1 = ax1.figure.colorbar(im1, ax=ax1, fraction=0.046, pad=0.04)
        cbar1.set_label(f"Elevation ({self.units[idx]})", rotation=90)
        ax1.text(
            0.5, -0.15, self.descriptions[idx],
            transform=ax1.transAxes, ha='center', fontsize=10, wrap=True
        )

        # Secondo grafico
        ax2 = fig.add_subplot(gs[0, 1])
        im2 = ax2.imshow(data1, cmap=self.cmaps[idx + 1], origin='upper')
        ax2.set_title(self.titles[idx + 1], pad=10)
        ax2.set_aspect('equal', adjustable='box')
        cbar2 = ax2.figure.colorbar(im2, ax=ax2, fraction=0.046, pad=0.04)
        cbar2.set_label(f"{self.units[idx + 1]}", rotation=90)
        ax2.text(
            0.5, -0.15, self.descriptions[idx + 1],
            transform=ax2.transAxes, ha='center', fontsize=10, wrap=True
        )

        # Terzo grafico
        ax3 = fig.add_subplot(gs[0, 2])
        im3 = ax3.imshow(data2, cmap=self.cmaps[idx + 2], origin='upper')
        ax3.set_title(self.titles[idx + 2], pad=10)
        ax3.set_aspect('equal', adjustable='box')
        cbar3 = ax3.figure.colorbar(im3, ax=ax3, fraction=0.046, pad=0.04)
        cbar3.set_label(f"{self.units[idx + 2]}", rotation=90)
        ax3.text(
            0.5, -0.15, self.descriptions[idx + 2],
            transform=ax3.transAxes, ha='center', fontsize=10, wrap=True
        )

        self.canvas.draw()

        # Gestisci la visibilità dei pulsanti
        if self.current_index == 0:
            self.prev_button.setVisible(False)
            self.next_button.setVisible(True)
        elif self.current_index == len(self.analysis_triplets) - 1:
            self.prev_button.setVisible(True)
            self.next_button.setVisible(False)
        else:
            self.prev_button.setVisible(True)
            self.next_button.setVisible(True)

    def next_triplet(self):
        if self.current_index < len(self.analysis_triplets) - 1:
            self.current_index += 1
            self.update_display()

    def previous_triplet(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.update_display()

    def display_volcano_3d(self):
        # Creazione di una nuova finestra per il modello 3D
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

        # Scala l'asse z se necessario
        z = self.dem

        surface = ax.plot_surface(
            x, y, z, cmap='terrain', edgecolor='none',
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

    # ### Funzione Aggiunta per Scaricare il Grafico in Formato PNG o JPG ###
    def download_graph_image(self):
        # Apri una finestra di dialogo per scegliere la destinazione e il formato
        options = QFileDialog.Options()
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Graph As",
            "",
            "PNG Files (*.png);;JPG Files (*.jpg);;All Files (*)",
            options=options
        )
        if file_path:
            # Determina il formato basato sul filtro selezionato o sull'estensione del file
            if selected_filter.startswith("PNG"):
                format = 'png'
                if not file_path.lower().endswith('.png'):
                    file_path += '.png'
            elif selected_filter.startswith("JPG"):
                format = 'jpg'
                if not file_path.lower().endswith('.jpg') and not file_path.lower().endswith('.jpeg'):
                    file_path += '.jpg'
            else:
                # Default a PNG se nessun formato specifico è selezionato
                format = 'png'
                if not file_path.lower().endswith('.png'):
                    file_path += '.png'

            try:
                # Salva la figura corrente nel formato scelto
                self.figure.savefig(file_path, format=format)
                QMessageBox.information(
                    self, 
                    "Success", 
                    f"Graph successfully saved as {format.upper()} to {file_path}"
                )
            except Exception as e:
                QMessageBox.critical(
                    self, 
                    "Save Error", 
                    f"An error occurred while saving the graph: {e}"
                )
                # Opzionale: loggare l'errore o gestirlo come necessario
                # print(f"Error during graph saving: {e}")

    # ### Funzione Aggiunta per Esportare i Dati Analizzati ###
    def export_data(self):
        # Chiedi all'utente di scegliere il formato di esportazione
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
            # L'utente ha annullato l'operazione
            return

        # Apri una finestra di dialogo per scegliere la destinazione
        options = QFileDialog.Options()
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Export Directory",
            "",
            options=options
        )
        if not directory:
            # L'utente ha annullato l'operazione
            return

        # Inizia l'esportazione dei dati
        try:
            for i, (dem_data, data1, data2) in enumerate(self.analysis_triplets, start=1):
                # Mappatura dei nomi delle analisi
                data_names = ['DEM', 'Shaded Relief', 'Hillshade', 'Aspect', 'Slope', 'Roughness', 'Convexity',
                              'Amplified and Smoothed Curvature', 'Logarithmic Amplified Gaussian Curvature']
                # Per ogni triplet, identifica i nomi corretti
                if i == 1:
                    current_titles = self.titles[0:3]
                elif i == 2:
                    current_titles = self.titles[3:6]
                elif i == 3:
                    current_titles = self.titles[6:9]
                elif i == 4:
                    current_titles = self.titles[9:12]
                else:
                    current_titles = [f"Data{j}" for j in range(1,4)]

                data_arrays = [dem_data, data1, data2]

                for title, data in zip(current_titles, data_arrays):
                    safe_title = title.replace(" ", "_").replace("/", "_")
                    if export_format == 'CSV':
                        # Esporta in CSV usando Pandas
                        df = pd.DataFrame(data)
                        csv_filename = os.path.join(directory, f"Triplet{i}_{safe_title}.csv")
                        df.to_csv(csv_filename, index=False, header=False)
                    elif export_format == 'GeoTIFF':
                        # Esporta in GeoTIFF usando Rasterio
                        geotiff_filename = os.path.join(directory, f"Triplet{i}_{safe_title}.tif")
                        # Aggiorna il profilo per riflettere i nuovi dati
                        new_profile = self.profile.copy()
                        new_profile.update(dtype=rasterio.float32, count=1)
                        with rasterio.open(geotiff_filename, 'w', **new_profile) as dst:
                            dst.write(data.astype(rasterio.float32), 1)
            # Mostra un messaggio di successo
            QMessageBox.information(
                self, 
                "Export Successful", 
                f"All data successfully exported as {export_format} to {directory}"
            )
        except Exception as e:
            QMessageBox.critical(
                self, 
                "Export Error", 
                f"An error occurred while exporting the data: {e}"
            )
            # Opzionale: loggare l'errore o gestirlo come necessario
            # print(f"Error during data export: {e}")

# Funzione principale
def main():
    if len(sys.argv) < 2:
        print("Error: specify the DEM file path as an argument.")
        sys.exit(1)

    dem_path = sys.argv[1]

    # Controllo esplicito per il nome originale del file
    original_file_name = sys.argv[2] if len(sys.argv) > 2 else "Unknown"

    # Ottieni il process_id dagli argomenti della riga di comando
    process_id = sys.argv[3] if len(sys.argv) > 3 else None
    if process_id:
        print(f"[DEBUG] Process ID: {process_id}")

    print(f"[DEBUG] DEM path provided: {dem_path}")
    print(f"[DEBUG] Original file name: {original_file_name}")
    print(f"[DEBUG] Number of arguments received: {len(sys.argv)}")

    try:
        dem, profile, transform, res = load_dem(dem_path)
        print(f"[DEBUG] DEM loaded successfully. Dimensions: {dem.shape}, Resolution: {res}")
    except Exception as e:
        print(f"Error loading DEM: {e}")
        sys.exit(1)

    # Esegui le varie analisi
    try:
        print("[DEBUG] Starting Hillshade calculation.")
        hillshade = calculate_hillshade(dem)
        print("[DEBUG] Hillshade calculated successfully.")

        print("[DEBUG] Starting Aspect calculation.")
        aspect = calculate_aspect(dem)
        print("[DEBUG] Aspect calculated successfully.")

        print("[DEBUG] Starting Convexity calculation.")
        convexity = calculate_convexity(dem, amplification_factor=200)
        print("[DEBUG] Convexity calculated successfully.")

        print("[DEBUG] Starting Shaded Relief calculation.")
        shaded = shaded_relief(dem, scale=10)
        print("[DEBUG] Shaded Relief calculated successfully.")

        print("[DEBUG] Starting Roughness calculation.")
        roughness = calculate_roughness(dem)
        print("[DEBUG] Roughness calculated successfully.")

        print("[DEBUG] Starting Slope calculation.")
        slope2 = calculate_slope_2(dem)
        print("[DEBUG] Slope calculated successfully.")

        print("[DEBUG] Starting Curvature calculation.")
        curvature = calculate_curvature(dem)
        print("[DEBUG] Curvature calculated successfully.")

        print("[DEBUG] Starting Gaussian Curvature calculation.")
        normalized_log_gaussian_curvature, log_gaussian_curvature = calculate_gaussian_curvature(dem, res)
        print("[DEBUG] Gaussian Curvature calculated successfully.")

        # Amplificazione e smoothing della curvatura
        amplification_factor = 5
        curvature_amplified = curvature * amplification_factor
        curvature_smoothed = gaussian_filter(curvature_amplified, sigma=1)
        curvature_smoothed_normalized = (
            curvature_smoothed - np.min(curvature_smoothed)
        ) / (np.max(curvature_smoothed) - np.min(curvature_smoothed))

    except Exception as e:
        print(f"Error during analysis calculations: {e}")
        sys.exit(1)

    # Raccogli statistiche in un dizionario
    total_statistics, gauss_curv_stats = gather_statistics(
        log_gaussian_curvature, "Logarithmic Amplified Gaussian Curvature"
    )
    _, smooth_curv_stats = gather_statistics(
        curvature_smoothed, "Amplified and Smoothed Curvature"
    )

    # Scrivi le statistiche in un file JSON
    write_statistics_to_json(total_statistics, filename="output_statistics.json")

    # Organizza le analisi in triplette
    print("[DEBUG] Preparing analysis triplets.")
    analysis_triplets = [
        (dem, shaded, hillshade),  # Prima finestra
        (dem, aspect, slope2),     # Seconda finestra
        (dem, roughness, convexity),  # Terza finestra
        (dem, curvature_smoothed_normalized, normalized_log_gaussian_curvature)
    ]
    print("[DEBUG] Analysis triplets prepared.")
    titles = [
        "DEM", "Shaded Relief", "Hillshade",
        "DEM", "Aspect", "Slope",
        "DEM", "Roughness", "Convexity",
        "DEM", "Amplified and Smoothed Curvature", "Logarithmic Amplified Gaussian Curvature"
    ]
    cmaps = [
        "terrain", "gray", "gray",
        "terrain", "twilight", "plasma",
        "terrain", "seismic", "twilight",
        "terrain", "plasma", "plasma"
    ]
    units = [
        "m", "Adimensional", "Adimensional",  # Prima finestra
        "m", "Degrees", "Degrees",            # Seconda finestra
        "m", "Adimensional", "Adimensional",  # Terza finestra
        "m", "Adimensional", "Adimensional"   # Quarta finestra
    ]
    descriptions = [
        "Represents terrain elevation in meters above sea level.",
        "Simulates light and shadow effects on the terrain.",
        "Represents relative illumination intensity on the terrain.\nSun position Altitude=45° - Azimuth=45°",
        "Represents terrain elevation in meters above sea level.",
        "Indicates the direction of the slope in degrees.\nWith 0° representing north, increasing clockwise to 360°",
        "Measures terrain slope in degrees.",
        "Represents terrain elevation in meters above sea level.",
        "Measures local variations in elevation.",
        "Shows whether terrain areas are convex or concave.",
        "Represents terrain elevation in meters above sea level.",
        "Smoothed curvature for improved interpretation.",
        "Gaussian curvature for detailed terrain analysis."
    ]

    # Invia notifica di completamento al server Node.js
    if process_id:
        try:
            print(f"[DEBUG] Sending completion notification to server for process ID: {process_id}")
            response = requests.post(f'http://localhost:5000/processComplete/{process_id}')
            print(f"[DEBUG] Server response status code: {response.status_code}")
            print(f"[DEBUG] Server response content: {response.content}")
        except Exception as e:
            print(f"[ERROR] Error sending completion notification: {e}")

    # Avvia l'applicazione PyQt5 con un piccolo ritardo per assicurare che la richiesta sia completata
    app = QApplication(sys.argv)
    ex = DEMAnalysisApp(
        dem, profile, analysis_triplets, titles, cmaps, units, descriptions, original_file_name
    )

    def show_main_window():
        ex.showMaximized()

    QTimer.singleShot(0, show_main_window)
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
