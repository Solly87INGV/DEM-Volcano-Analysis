"""Nyamulagira (GVP 223020) - summit caldera.
Criterion: radial crest. From the caldera centre, 36 rays every 10 deg; on each ray the
rim vertex is the highest DEM point between 0.6 and 1.6 km from the centre.
DEM: Nyamulagira.tif (GLO-30, ~31 m after reprojection)."""
import numpy as np
from rimlib import cli, load_dem, ray, sample, write_geojson, report

CENTRE = (86, 112)
R_MIN_M, R_MAX_M, AZ_STEP, STEP_PX = 600, 1600, 10, 0.25

def main():
    args = cli(__doc__).parse_args()
    d, meta = load_dem(args.dem)
    r = np.arange(R_MIN_M / meta["res"], R_MAX_M / meta["res"], STEP_PX)
    pts = []
    for az in range(0, 360, AZ_STEP):
        rows, cols = ray(CENTRE, az, r)
        k = int(np.argmax(sample(d, rows, cols)))
        pts.append((rows[k], cols[k]))
    rc = np.array(pts)
    write_geojson(rc, meta, args.out, f"Nyamulagira radial crest: centre r{CENTRE[0]} c{CENTRE[1]}, 36 rays/10 deg, max between {R_MIN_M} and {R_MAX_M} m")
    report("Nyamulagira", rc, d, meta, args)

if __name__ == "__main__":
    main()
