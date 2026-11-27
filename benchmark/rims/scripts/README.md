# Scripts for the criterion-based reference rims

One script per case, each implementing the rim criterion listed in `../README.md` with all
parameters written as constants at the top of the file. Every script reads the
`dem_working.tif` written by MorphoVolc step 1 for that DEM, writes a GeoJSON in the format
the app imports (`caldera_rim_edited.geojson`), and can compare it with the archived rim
(`--ref`) and print depth and volume computed with the app's own loader and the frozen
`volume_unified.py` (`--vu`).

| Script | Case | Criterion |
|---|---|---|
| `ambrym_floor_hull.py` | Ambrym (outer caldera) | convex hull of the flat caldera floor |
| `fogo_crest.py` | Fogo (Pico do Fogo crater) | radial crest |
| `aniakchak_crest.py` | Aniakchak | radial crest, Gates closed |
| `valles_divide.py` (+ `d8_catchment.py`) | Valles | drainage divide above the single outlet |
| `nyamulagira_crest.py` | Nyamulagira | radial crest |
| `long_valley_bailey.py` | Long Valley | published rim, Bailey (1989), from `sources/bailey1989_dds81/` |
| `chaiten_moat.py` | Chaitén | wall foot at the outer edge of the moat |
| `izu_oshima_wall_crest.py` | Izu-Oshima | wall crest, open E side closed |
| `piton_dolomieu_walltop.py` | Piton de la Fournaise (Dolomieu) | top of the crater wall |
| `pagan_crest.py` | Pagan (summit crater) | first crest with 3 m prominence |
| `tengger_wall_crest.py` | Tengger (Sea of Sand) | wall crest, W side closed |

`rimlib.py` holds the shared I/O, ray sampling and comparison code. `run_all.sh` runs every
script (paths to the `dem_working.tif` files are passed as environment variables).

Example:

```
python3 fogo_crest.py --dem <backend/outputs/<run>/dem_working.tif> \
    --out Fogo_rim.geojson --ref ../Fogo_rim.geojson --vu ../../../backend/scripts/volume_unified.py
```

Requirements: Python 3 with numpy, scipy, scikit-image, rasterio, pyproj; `pysheds` (Valles)
and `pyshp` (Long Valley).

## Provenance and verification

The archived GeoJSON files in `../` are the reference rims used for the benchmark values.
The exploratory code that first produced them was written interactively (with the help of
an AI coding assistant) and was not kept as files; these scripts are a clean
re-implementation of the same criteria, written afterwards and checked against the archived
rims on the same `dem_working.tif` files (27 Nov 2026):

| Case | IoU with archived rim | Area difference | Max vertex offset |
|---|---|---|---|
| Ambrym outer | 0.999 | 0.0% | < 0.01 m |
| Fogo | 0.991* | 0.0% | < 0.01 m |
| Aniakchak | 0.998 | +0.09% | 11 m |
| Valles | 1.000 | 0.0% | < 0.01 m |
| Nyamulagira | 0.994* | −0.02% | 6 m |
| Long Valley | 1.000 | 0.0% | 0 m |
| Chaitén | 0.998 | −0.10% | 6 m |
| Izu-Oshima | 0.995 | +0.23% | 13 m |
| Piton (Dolomieu) | 1.000 | 0.0% | < 0.01 m |
| Pagan | 0.995* | 0.0% | < 0.01 m |
| Tengger | 1.000 | −0.01% | 15 m |

\* Small rims (Fogo, Pagan: ~200 pixels; Nyamulagira: ~3700): an IoU slightly below 1 comes
from boundary pixels of the rasterised masks; the vertices coincide.

All offsets are below one DEM pixel (20–31 m). Depth and volume computed from the
regenerated rims match the published values (e.g. Ambrym 205 m / 11.68 km³, Valles
712 m / 193.4 km³, Tengger 467 m / 15.41 km³).

Operator choices (documented in each script): centres and seeds, search windows, the
azimuths of breaches closed by straight segments (Aniakchak, Chaitén, Izu-Oshima, Tengger),
the interpolated az 270 ray at Dolomieu, and the northern end of the NW scarp at Izu-Oshima.
