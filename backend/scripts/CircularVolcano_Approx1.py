# circular_approx1.py

import sys
import os
import numpy as np
import rasterio  # For reading DEM files in .tif format
from scipy.ndimage import sobel
from skimage import measure
from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox, QFileDialog
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib import gridspec
# Import the PDF generator module
import pdf_generator

# ### Analysis Functions ###

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
    # Avoid out-of-bounds indices
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

# ### Main Application Class ###

class VolumeAnalysisApp(QMainWindow):
    def __init__(self, dem):
        super().__init__()
        self.setWindowTitle('Volcano Volume Analysis')
        self.dem = dem
        self.calculate_results()  # Call before initUI
        self.initUI()
        
    def initUI(self):
        # Central Widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main Layout
        main_layout = QVBoxLayout(central_widget)

        # Matplotlib Figure and Canvas
        self.figure = Figure(figsize=(18, 14))  # Increased height for additional space
        self.canvas = FigureCanvas(self.figure)
        main_layout.addWidget(self.canvas)

        # Buttons Layout
        button_layout = QHBoxLayout()
        main_layout.addLayout(button_layout)

        self.results_button = QPushButton('View Results && Print Report')
        self.results_button.setFixedSize(180, 50)
        self.results_button.clicked.connect(self.show_results)
        button_layout.addWidget(self.results_button)

        # ### Aggiunta del Pulsante di Download in Formato PNG o JPG ###
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

        # Define GridSpec with 3 rows and 3 columns
        # height_ratios adjusted to bring legends and descriptions closer
        gs = gridspec.GridSpec(nrows=3, ncols=3, height_ratios=[4, 1, 1.5], figure=fig, wspace=0.4, hspace=0.6)

        # Plot 1: Volcano DEM
        ax1 = fig.add_subplot(gs[0, 0])
        im1 = ax1.imshow(self.dem, cmap='terrain', origin='upper')
        cbar1 = fig.colorbar(im1, ax=ax1, fraction=0.046, pad=0.04)
        cbar1.set_label("Elevation (m)", rotation=90)
        ax1.set_title("Volcano DEM", fontsize=14, pad=20, y=1.02)  # Adjusted pad and y to position title closer to the graph
        ax1.set_xlabel("")
        ax1.set_ylabel("")
        ax1.axis('on')  # Show axes

        # Plot 2: Opposite Points of the Volcano Base
        ax2 = fig.add_subplot(gs[0, 1])
        im2 = ax2.imshow(self.dem, cmap='terrain', origin='upper')
        cbar2 = fig.colorbar(im2, ax=ax2, fraction=0.046, pad=0.04)
        cbar2.set_label("Elevation (m)", rotation=90)
        point1_plot, = ax2.plot(self.base_point1[1], self.base_point1[0], 'ro', markersize=10, label='Base 1')
        point2_plot, = ax2.plot(self.base_point2[1], self.base_point2[0], 'yo', markersize=10, label='Base 2')
        contour_plot, = ax2.plot(self.base_contour[:, 1], self.base_contour[:, 0], 'w-', linewidth=1, label="Base Contour")
        ax2.set_title("Opposite Points of the Volcano Base", fontsize=14, pad=20, y=1.02)  # Adjusted pad and y
        ax2.set_xlabel("")
        ax2.set_ylabel("")
        ax2.axis('on')  # Show axes

        # Plot 3: Opposite Maximum Slope Points on the Caldera
        ax3 = fig.add_subplot(gs[0, 2])
        im3 = ax3.imshow(self.dem, cmap='terrain', origin='upper')
        cbar3 = fig.colorbar(im3, ax=ax3, fraction=0.046, pad=0.04)
        cbar3.set_label("Elevation (m)", rotation=90)
        slope1_plot, = ax3.plot(self.max_slope_index1[1], self.max_slope_index1[0], 'ro', markersize=10, label='Max Slope 1')
        slope2_plot, = ax3.plot(self.max_slope_index2[1], self.max_slope_index2[0], 'yo', markersize=10, label='Max Slope 2')
        caldera_contour_plot, = ax3.plot(self.caldera_contour[:, 1], self.caldera_contour[:, 0], 'b-', linewidth=1, label="Caldera Contour")
        ax3.set_title("Opposite Maximum Slope Points on the Caldera", fontsize=14, pad=20, y=1.02)  # Adjusted pad and y
        ax3.set_xlabel("")
        ax3.set_ylabel("")
        ax3.axis('on')  # Show axes

        # Legend for Plot 2
        legend_ax2 = fig.add_subplot(gs[1, 1])
        legend_ax2.axis('off')  # Hide axes
        legend2 = legend_ax2.legend(
            [point1_plot, point2_plot, contour_plot], 
            ['Base 1', 'Base 2', 'Base Contour'], 
            loc='center', 
            frameon=True, 
            edgecolor='black', 
            facecolor='lightgray', 
            ncol=3
        )
        legend2.get_frame().set_linewidth(1)

        # Legend for Plot 3
        legend_ax3 = fig.add_subplot(gs[1, 2])
        legend_ax3.axis('off')  # Hide axes
        legend3 = legend_ax3.legend(
            [slope1_plot, slope2_plot, caldera_contour_plot], 
            ['Max Slope 1', 'Max Slope 2', 'Caldera Contour'], 
            loc='center', 
            frameon=True, 
            edgecolor='black', 
            facecolor='lightgray', 
            ncol=3
        )
        legend3.get_frame().set_linewidth(1)

        # Celle vuote per mantenere la simmetria (legenda e descrizione per Grafico 1)
        legend_ax1 = fig.add_subplot(gs[1, 0])
        legend_ax1.axis('off')  # Hide axes
        # Aggiungi un placeholder o lascia vuoto per simmetria
        legend_ax1.text(0.5, 0.5, "", ha='center', va='center')

        desc_ax1 = fig.add_subplot(gs[2, 0])
        desc_ax1.axis('off')  # Hide axes
        # Aggiungi un placeholder o lascia vuoto per simmetria
        desc_ax1.text(0, 0, "", fontsize=10, ha='left', va='center')

        # Description for Plot 2 with borders and center alignment
        desc_ax2 = fig.add_subplot(gs[2, 1])
        desc_ax2.axis('off')  # Hide axes
        desc_ax2.text(
            0.5, 1.35, 
            self.description_base, 
            fontsize=10, 
            ha='center', 
            va='center',
            bbox=dict(boxstyle="round,pad=0.5", edgecolor="black", facecolor="white"),
            wrap=True, 
            transform=desc_ax2.transAxes
        )

        # Description for Plot 3 with borders and center alignment
        desc_ax3 = fig.add_subplot(gs[2, 2])
        desc_ax3.axis('off')  # Hide axes
        desc_ax3.text(
            0.5, 1.5, 
            self.description_slope, 
            fontsize=10, 
            ha='center', 
            va='center',
            bbox=dict(boxstyle="round,pad=0.5", edgecolor="black", facecolor="white"),
            wrap=True, 
            transform=desc_ax3.transAxes
        )

        # Adjust layout to make space for legends and descriptions
        fig.subplots_adjust(left=0.05, right=0.95, top=0.95, bottom=0.05, wspace=0.4, hspace=0.6)

        self.canvas.draw()
        
    def show_results(self):
        # Show results in a modal window with download option
        msg_box = QMessageBox()
        msg_box.setWindowTitle("Summary Results")
        msg_box.setText(self.results_text)
        download_button = msg_box.addButton("Download Full Report", QMessageBox.ActionRole)
        msg_box.addButton(QMessageBox.Ok)
        msg_box.exec_()

        if msg_box.clickedButton() == download_button:
            self.download_results()

    def download_results(self):
        # Save results to a PDF file using the external pdf_generator module
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Results As", "", "PDF Files (*.pdf)", options=options)
        if file_path:
            if not file_path.lower().endswith('.pdf'):
                file_path += '.pdf'
            try:
                # Generate the title with all information
                title = "Calculation Results - Circular Base, Approximation Type 1"

                pdf_generator.generate_pdf(
                    file_path=file_path,
                    results_list=self.results_list,
                    title=title
                )
                QMessageBox.information(self, "Success", f"PDF successfully saved to {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "PDF Error", f"An error occurred while generating the PDF: {e}")
                # Remove print statement to avoid unwanted messages
                # print(f"Error during PDF generation: {e}")

    # ### Funzione Aggiunta per Scaricare il Grafico in Formato PNG o JPG ###
    def download_graph_image(self):
        # Open a file dialog to choose the save location and format
        options = QFileDialog.Options()
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Graph As",
            "",
            "PNG Files (*.png);;JPG Files (*.jpg);;All Files (*)",
            options=options
        )
        if file_path:
            # Determine the format based on the selected filter or file extension
            if selected_filter.startswith("PNG"):
                format = 'png'
                if not file_path.lower().endswith('.png'):
                    file_path += '.png'
            elif selected_filter.startswith("JPG"):
                format = 'jpg'
                if not file_path.lower().endswith('.jpg') and not file_path.lower().endswith('.jpeg'):
                    file_path += '.jpg'
            else:
                # Default to PNG if no specific format is selected
                format = 'png'
                if not file_path.lower().endswith('.png'):
                    file_path += '.png'
            
            try:
                # Save the current figure in the chosen format
                self.figure.savefig(file_path, format=format)
                QMessageBox.information(self, "Success", f"Graph successfully saved as {format.upper()} to {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Save Error", f"An error occurred while saving the graph: {e}")
                # Optionally log the error or handle it as needed
                # print(f"Error during graph saving: {e}")

# ### Application Entry Point ###

if __name__ == '__main__':
    # Ensure that the DEM file path is provided
    if len(sys.argv) < 2:
        print("Usage: python circular_approx1.py <dem_file_path>")
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
