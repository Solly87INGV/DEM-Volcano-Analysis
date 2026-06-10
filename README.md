# MorphoVolc (DEM-Volcano-Analysis)

MorphoVolc is an open-source, browser-accessible workflow for DEM-based morphometry and first-order volume estimation of volcanic edifices.

Its main purpose is to provide a reproducible end-to-end workflow from DEM upload to terrain-derivative generation, boundary extraction, volumetric modeling, and export of structured outputs. MorphoVolc is intended for transparent, repeatable, and comparable morphometric analysis under explicit operational settings, not for reconstruction-grade 3D modeling of volcanic landforms.

---

## What MorphoVolc does

MorphoVolc allows the user to:

- upload a georeferenced DEM in GeoTIFF format
- generate terrain derivatives
- extract basal and caldera contours through a parameterized workflow
- compute first-order geometric and volumetric descriptors
- select among circular and elliptical model families with alternative caldera approximations
- export structured outputs for inspection and reuse

Each execution is associated with a unique `processId`, and all generated artifacts are written to a dedicated output directory.

---

## Current scope

MorphoVolc is primarily intended for positive-relief volcanic edifices that can be reasonably approximated by circular or elliptical planimetric configurations.

It is best suited to:

- isolated or relatively coherent volcanic edifices
- medium-resolution DEM-based exploratory analysis
- comparative morphometric workflows
- repeated and traceable execution under explicit settings

---

## Current limitations

The current release is:

- threshold-based and parameterized
- sensitive to DEM extent and clipping strategy
- sensitive to DEM quality, local noise, and gradual basal transitions
- based on simplified analytical volume models

The workflow does **not yet** include:

- adaptive thresholding
- user-defined masking
- dedicated DEM-wide denoising or artifact-correction preprocessing
- adaptive baseline estimation
- alternative normalization strategies as built-in workflow options

MorphoVolc is less suitable for:

- fissure-dominated systems
- maars
- multi-lobed edifices
- overlapping volcanic constructs
- multi-edifice clusters
- strongly fault-controlled or structurally complex basal settings
- cases where a single contour-based edifice boundary is geomorphologically ambiguous

The volumetric outputs should be interpreted as **standardized first-order analytical estimates**, not as exact reconstructions of volcanic form.

---

## Repository structure

- `frontend/` — React user interface
- `backend/` — Node/Express orchestration service
- `backend/scripts/` — Python processing scripts
- `backend/uploads/` — uploaded DEMs (created automatically)
- `backend/outputs/` — per-run outputs (created automatically)
- `examples/` — small demo data for quick testing

---

## Validated runtime versions

- Node: **v16.20.2**
- npm: **8.19.4**
- Python: **3.12.4**

---

## Quick start

### Dockerized execution (recommended)

From the project root:

```bash
docker compose up --build
```

Then open the application in your browser:

- Frontend: `http://localhost:3000`
- Backend: `http://localhost:5000`

---

## Main workflow

The typical workflow is:

1. upload a DEM in GeoTIFF format
2. run preliminary validation
3. inspect terrain derivatives
4. choose a model family:
   - `circular`
   - `elliptical`
5. choose a caldera approximation:
   - `approximation1`
   - `approximation2`
6. compute morphometric and volumetric outputs
7. inspect exported products and structured output files

---

## Outputs

All run artifacts are stored under:

- `backend/outputs/<processId>/`

Typical outputs may include:

- derivative previews
- boundary-related products
- tabular metrics
- JSON summaries
- PDF exports
- image overviews
- final comparison figures

---

## Quality control guidance

Before interpreting the outputs, inspect the derivative products carefully.

Recommended checks:

- verify that the DEM covers the volcanic edifice and a meaningful surrounding reference surface
- avoid overly tight DEM clipping
- inspect hillshade and slope for obvious inconsistencies
- check whether the extracted base is geomorphologically plausible
- verify whether the summit depression and caldera contour are consistent with the DEM morphology

The workflow may produce numerically valid outputs that are still **geomorphologically implausible** if DEM quality or clipping strategy is poor. Reproducibility does not remove the need for expert interpretation.

---

## Examples

This repository includes a small demo DEM for quick end-to-end testing.

- `examples/dem/Arenal.tif` — example DEM (GeoTIFF) for validating upload, processing, and export

Suggested use:

1. start the application with Docker
2. upload `examples/dem/Arenal.tif` from the web interface
3. verify that outputs are generated under `backend/outputs/<processId>/`

### Example DEM provenance

`examples/dem/Arenal.tif` is provided as a small test DEM for validating the end-to-end workflow. It is a subset derived from the ASTER GDEM distribution (courtesy of METI and NASA). If any redistribution issue is reported, this file can be removed and users can reproduce it by downloading ASTER GDEM v3 (ASTGTM.003) from LP DAAC and clipping the Arenal area of interest.

---

## License

MIT License — see `LICENSE`.

---

## Citation

If you use MorphoVolc, please cite:

1. the accompanying manuscript
2. the specific software version or release used for the analysis

When possible, cite the exact tagged release or commit associated with your results.