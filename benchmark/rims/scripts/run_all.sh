#!/usr/bin/env bash
# Regenerate the 11 non-automatic reference rims and compare them with the archived ones.
# Each script needs the dem_working.tif written by MorphoVolc step 1 for that DEM: process
# the DEM in the app (with its GVP number) and point the variables below to
# backend/outputs/<run>/dem_working.tif. Long Valley needs only the Bailey (1989) shapefile.
set -e
cd "$(dirname "$0")"
REF=..                                   # archived GeoJSON (benchmark/rims)
VU=${VU:-../../../backend/scripts/volume_unified.py}
OUT=${OUT:-./regenerated}; mkdir -p "$OUT"

AMBRYM=${AMBRYM:?path to Ambrym_1 dem_working.tif}
FOGO=${FOGO:?path to Fogo_recenter dem_working.tif}
ANIAKCHAK=${ANIAKCHAK:?}
VALLES=${VALLES:?}
NYAMULAGIRA=${NYAMULAGIRA:?}
CHAITEN=${CHAITEN:?}
IZU=${IZU:?path to Izu-Oshima_2 dem_working.tif}
PITON=${PITON:?}
PAGAN=${PAGAN:?path to Pagan (14 km tile) dem_working.tif}
TENGGER=${TENGGER:?}

python3 ambrym_floor_hull.py       --dem "$AMBRYM"      --out "$OUT/Ambrym_outer_rim.geojson"   --ref $REF/Ambrym_outer_rim.geojson   --vu $VU
python3 fogo_crest.py              --dem "$FOGO"        --out "$OUT/Fogo_rim.geojson"           --ref $REF/Fogo_rim.geojson           --vu $VU
python3 aniakchak_crest.py         --dem "$ANIAKCHAK"   --out "$OUT/Aniakchak_rim.geojson"      --ref $REF/Aniakchak_rim.geojson      --vu $VU
python3 valles_divide.py           --dem "$VALLES"      --out "$OUT/Valles_rim.geojson"         --ref $REF/Valles_rim.geojson         --vu $VU
python3 nyamulagira_crest.py       --dem "$NYAMULAGIRA" --out "$OUT/Nyamulagira_rim.geojson"    --ref $REF/Nyamulagira_rim.geojson    --vu $VU
python3 long_valley_bailey.py      --shp sources/bailey1989_dds81/caldera.shp --out "$OUT/Long_Valley_rim.geojson"
python3 chaiten_moat.py            --dem "$CHAITEN"     --out "$OUT/Chaiten_rim.geojson"        --ref $REF/Chaiten_rim.geojson        --vu $VU
python3 izu_oshima_wall_crest.py   --dem "$IZU"         --out "$OUT/Izu-Oshima_rim.geojson"     --ref $REF/Izu-Oshima_rim.geojson     --vu $VU
python3 piton_dolomieu_walltop.py  --dem "$PITON"       --out "$OUT/Piton_Dolomieu_rim.geojson" --ref $REF/Piton_Dolomieu_rim.geojson --vu $VU
python3 pagan_crest.py             --dem "$PAGAN"       --out "$OUT/Pagan_rim.geojson"          --ref $REF/Pagan_rim.geojson          --vu $VU
python3 tengger_wall_crest.py      --dem "$TENGGER"     --out "$OUT/Tengger_rim.geojson"        --ref $REF/Tengger_rim.geojson        --vu $VU
