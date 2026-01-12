# DEM-Volcano-Analysis (MorphoVolc)

MorphoVolc is an open-source, web-native platform for volcanic edifice morphometry and volumetric estimation from Digital Elevation Models (DEMs).

The system is composed of:
- **Frontend**: React web UI (upload, progress tracking, results & export)
- **Backend**: Node/Express orchestration service (REST API, file upload, process management)
- **Python pipeline**: DEM processing and volumetric computation (headless)

It follows a **stateless, file-based** workflow: each run is identified by a `processId` and all artifacts are written to disk under a dedicated output folder, together with a `manifest.json` and runtime logs.

---

## Repository structure

- `frontend/` — React UI (Create React App)
- `backend/` — Node/Express server (`server.js`)
  - `backend/uploads/` — uploaded DEMs (created automatically)
  - `backend/outputs/` — per-run outputs (created automatically)
  - `backend/scripts/` — Python scripts invoked by the backend
- `examples/` — small demo DEM for quick testing

---

## Validated runtime versions

- Node: **v16.20.2**
- npm: **8.19.4**
- Python: **3.12.4**

---

## Python dependencies

Create a Python environment (venv or conda) and install dependencies:

```bash
pip install -r backend/requirements.txt
```
Core stack:
- `rasterio >= 1.2`
- `numpy >= 1.21`
- `scikit-image >= 0.18`
- `matplotlib >= 3.4`
- `PyQt5 >= 5.15`
- `reportlab >= 3.6`

> Windows note: `rasterio` may require conda-forge or prebuilt wheels (GDAL/PROJ).

---

## Backend API

Backend runs on `http://localhost:5000`  
Static outputs are served at `http://localhost:5000/outputs`

### Start DEM processing
**POST** `/process`

`multipart/form-data` fields:
- `demFile` (file): input DEM (GeoTIFF `.tif`)
- `originalFileName` (text): optional base filename

Returns:
- `{ "message": "Processing started", "processId": "<uuid>" }`

### Poll processing status
**GET** `/processStatus/<processId>`

Returns:
- `{ "status": "processing" }` or `{ "status": "completed" }`

### Mark completion (Python callback)
**POST** `/processComplete/<processId>`

---

## Volume estimation

**POST** `/calculateVolume`

`multipart/form-data` fields:
- `demFile` (file): input DEM (GeoTIFF `.tif`)
- `volumeType` (text): `circular` | `elliptical`
- `approximationType` (text): `approximation1` | `approximation2`
- `originalFileName` (text): required base filename
- `processId` (text): optional

Python scripts invoked:
- `backend/scripts/CircularVolcano_Approx1.py`
- `backend/scripts/CircularVolcano_Approx2.py`
- `backend/scripts/EllipticalVolcano_Approx1.py`
- `backend/scripts/EllipticalVolcano_Approx2.py`

---

## Morphometric derivatives (optional endpoints)

- **POST** `/shadedRelief` → `backend/scripts/generate_shaded_relief.py`
- **POST** `/calculateSlopes` → `backend/scripts/generate_slopes.py`
- **POST** `/calculateCurvatures` → `backend/scripts/calculate_curvatures.py`

Errors are appended to `backend/error_log.txt`.

---

## Output layout

All run artifacts are stored under:
- `backend/outputs/<processId>/`

---

## Headless execution (recommended)

For server/container environments:
- `MPLBACKEND=Agg`
- `QT_QPA_PLATFORM=offscreen`
- `OPENBLAS_NUM_THREADS=1`

---

## Quick start (development)

### 1) Backend (Node/Express)
```bash
cd backend
npm install
npm start
```
Backend will be available at `http://localhost:5000`.

### 2) Python interpreter path
The backend uses a Python interpreter path defined in `backend/server.js` (`pythonPath`).
Update it to match your local Python installation (or refactor it to use an environment variable).

### 3) Frontend (React)
```bash
cd frontend
npm install
npm start
```
Frontend default URL: `http://localhost:3000`.

> The frontend must be configured to point to the backend base URL (`http://localhost:5000`).

---

## Example data

A small demo DEM is provided in `examples/dem/Arenal.tif` for quick end-to-end testing.

---

## License

MIT License — see `LICENSE`.

---

## Citation

If you use this software, please cite the accompanying manuscript and this repository (including the tag/commit used for the results).
