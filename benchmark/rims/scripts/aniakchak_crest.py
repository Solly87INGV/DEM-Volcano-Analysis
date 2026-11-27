"""Aniakchak (GVP 312090) - caldera.
Criterion: radial crest. From the caldera centre, 36 rays every 10 deg; on each ray the
rim vertex is the highest DEM point between 4 and 6 km from the centre (window chosen to
skip the intra-caldera cones W/N). The Gates breach (NE, az ~82-88 deg) falls between
the az 80 and az 90 rays, so it is closed by the straight segment between them.
DEM: Aniakchak.tif (GLO-30, ~20 m after reprojection)."""
import numpy as np
from rimlib import cli, load_dem, ray, sample, write_geojson, report

CENTRE = (415, 495)                  # caldera centre (row, col) on dem_working.tif
R_MIN_M, R_MAX_M, AZ_STEP = 4000, 6000, 10

def main():
    args = cli(__doc__).parse_args()
    d, meta = load_dem(args.dem)
    res = meta["res"]
    r = np.arange(R_MIN_M / res, R_MAX_M / res, 1.0)
    pts = []
    for az in range(0, 360, AZ_STEP):
        rows, cols = ray(CENTRE, az, r)
        k = int(np.argmax(sample(d, rows, cols)))
        pts.append((rows[k], cols[k]))
    rc = np.array(pts)
    write_geojson(rc, meta, args.out, f"Aniakchak radial crest: centre r{CENTRE[0]} c{CENTRE[1]}, 36 rays/10 deg, max between {R_MIN_M} and {R_MAX_M} m; Gates closed by the az80-az90 segment")
    report("Aniakchak", rc, d, meta, args)

if __name__ == "__main__":
    main()
