"""Long Valley (GVP 323822) - caldera.
Rim from the literature: the "recognized trace of caldera rim" of Bailey (1989), USGS
Miscellaneous Investigations Series Map I-1933, as distributed in the USGS DDS-81 database
(layer `caldera`, files caldera.shp/.shx/.dbf; no .prj). Coordinates are NAD27 / UTM 11N
(Clarke 1866, from the metadata) and are converted to WGS84 with the EPSG transformation
"NAD27 to WGS 84 (6)" (western CONUS, accuracy 7 m). No simplification.
Source: https://pubs.usgs.gov/dds/dds-81/GeologicalMaps/DigitalMap/Caldera/"""
import argparse
import json
import shapefile                     # pyshp
from pyproj.transformer import TransformerGroup

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--shp", required=True, help="path to caldera.shp (DDS-81)")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    sh = shapefile.Reader(args.shp)
    shp = max(sh.shapes(), key=lambda s: len(s.points))     # the closed caldera boundary
    tg = TransformerGroup("EPSG:26711", "EPSG:4326", always_xy=True)
    tr = next(t for t in tg.transformers if "NAD27 to WGS 84 (6)" in t.description)
    ring = [list(map(lambda v: round(v, 7), tr.transform(x, y))) for x, y in shp.points]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    feat = {"type": "Feature",
            "properties": {"source": "edited", "editing_crs": "EPSG:4326",
                           "origin": "Bailey 1989, USGS I-1933 via DDS-81 caldera.shp (caldera_bdy); NAD27 UTM11 -> WGS84 with EPSG 'NAD27 to WGS 84 (6)'"},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}
    json.dump(feat, open(args.out, "w"))
    print(f"Long Valley: {len(ring) - 1} vertices written to {args.out}")

if __name__ == "__main__":
    main()
