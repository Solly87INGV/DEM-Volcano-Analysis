# Examples

This folder contains a small demo DEM for quick end-to-end testing.

- `dem/Arenal.tif` — example DEM (GeoTIFF) used to test upload, processing, and export.

How to use:
1. Start backend and frontend.
2. Upload `dem/Arenal.tif` from the UI.
3. Verify that outputs are generated under `backend/outputs/<processId>/`.

## Example DEM provenance

`examples/dem/Arenal.tif` is provided as a small test DEM for validating the end-to-end workflow.
It is a subset derived from the ASTER GDEM distribution (courtesy of METI and NASA).
If any redistribution issue is reported, this file can be removed and users can reproduce it by downloading ASTER GDEM v3 (ASTGTM.003) from LP DAAC and clipping the Arenal AOI.
