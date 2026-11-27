"""Fogo (GVP 384010) - Pico do Fogo summit crater.
Criterion: radial crest. From the crater-floor seed (lowest DEM pixel of the crater,
~2611 m), 36 rays every 10 deg; on each ray the rim vertex is the highest DEM point
within 16 px (bilinear sampling every 0.25 px).
DEM: Fogo_recenter.tif (GLO-30, ~30 m after reprojection)."""
import numpy as np
from rimlib import cli, load_dem, ray, sample, write_geojson, report

SEED = (51, 50)          # crater-floor minimum (row, col) on dem_working.tif
R_MAX_PX, STEP_PX, AZ_STEP = 16, 0.25, 10

def main():
    args = cli(__doc__).parse_args()
    d, meta = load_dem(args.dem)
    pts = []
    for az in range(0, 360, AZ_STEP):
        r = np.arange(0, R_MAX_PX, STEP_PX)
        rows, cols = ray(SEED, az, r)
        k = int(np.argmax(sample(d, rows, cols)))
        pts.append((rows[k], cols[k]))
    rc = np.array(pts)
    write_geojson(rc, meta, args.out, __doc__.split("\n")[1] + " " + f"seed r{SEED[0]} c{SEED[1]}, 36 rays/10 deg, max within {R_MAX_PX} px")
    report("Fogo", rc, d, meta, args)

if __name__ == "__main__":
    main()
