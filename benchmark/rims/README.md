# Non-automatic reference caldera rims — MorphoVolc 2.0 benchmark (v0.5.2)

Polygons in lon/lat (EPSG:4326, GeoJSON Feature Polygon, `properties.source = "edited"`; `properties.origin`, where present, describes the criterion).

**Reproducing the published values:** process the DEM with the listed GVP volcano number, copy the file to
`backend/outputs/<run>/caldera_rim_edited.geojson`, then press **Recalculate** in the results panel.

| File | Volcano (GVP no.) | DEM | Rim criterion | Reference run |
|---|---|---|---|---|
| Ambrym_outer_rim.geojson | Ambrym (257040) | Ambrym_1.tif | convex hull of the caldera floor (slope < 5°, Gaussian σ = 2 px) | ddb0929d |
| Fogo_rim.geojson | Fogo (384010) | Fogo_recenter.tif | radial crest of the Pico do Fogo summit crater (36 rays) | 1b2bf4d0 |
| Aniakchak_rim.geojson | Aniakchak (312090) | Aniakchak.tif | radial crest (10° step), straight closure across The Gates | 517da9fb |
| Valles_rim.geojson | Valles (327010) | Valles.tif | drainage divide of the caldera catchment (D8) above the single outlet (San Diego Canyon) | e6a33803 |
| Nyamulagira_rim.geojson | Nyamulagira (223020) | Nyamulagira.tif | radial crest (36 rays, 0.6–1.6 km from the centre) | d8fb64d2 |
| Long_Valley_rim.geojson | Long Valley (323822) | Long_Valley.tif | published rim: Bailey (1989), USGS I-1933 / DDS-81, `caldera` layer (NAD27 → WGS84) | bbea0937* |
| Chaiten_rim.geojson | Chaitén (358041) | Chaiten.tif | wall foot at the outer edge of the moat (+60 m above the moat floor), gaps closed with straight segments | 0f727d24* |
| Izu-Oshima_rim.geojson | Izu-Oshima (284010) | Izu-Oshima_2.tif | caldera wall crest (slope > 22°), open E side closed with a straight segment | a49db9a8* |
| Piton_Dolomieu_rim.geojson | Piton de la Fournaise (233020) | Piton_de_la_fournaise.tif | top of the Dolomieu crater wall (slope < 10° after the > 25° wall) | 0bc36b3b* |
| Pagan_rim.geojson | Pagan (284170) | Pagan.tif (14 km tile) | radial crest of the summit crater (first peak with 3 m prominence); diameter only, floor is GLO-30 void-fill | 4b73b9fe* |
| Tengger_rim.geojson | Tengger (263310) | Tengger.tif (20 km tile) | crest of the Sea of Sand caldera wall (slope > 28°), W side closed with a straight segment | 1c4819d2* |

\* Run folder no longer on disk; values are recorded in the benchmark table and are reproducible from this file and the DEM.

DEMs: Copernicus GLO-30 (via OpenTopography), reprojected to UTM by MorphoVolc step 1.
Hand-drawn rims of the same cases were used only to quantify operator variability; they are not reference rims.
