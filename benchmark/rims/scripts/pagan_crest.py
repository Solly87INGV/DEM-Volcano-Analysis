"""Pagan (GVP 284170) - summit crater of Mount Pagan.
Criterion: radial crest = first peak. From the crater centre, 36 rays every 10 deg; on
each ray the rim vertex is the first local maximum that is followed by a drop of at least
3 m (3 m prominence), searched within 20 px. Only the diameter is meaningful: the crater
floor of GLO-30 is a flat void-fill surface (481 m).
DEM: Pagan.tif (14 km tile, GLO-30, ~30 m after reprojection)."""
import numpy as np
from rimlib import cli, load_dem, ray, sample, write_geojson, report

CENTRE = (190, 186)
PROMINENCE_M, R_MAX_PX, STEP_PX, AZ_STEP = 3.0, 20, 0.25, 10

def first_peak(z, prom):
    best, kbest = -np.inf, 0
    for k, v in enumerate(z):
        if v > best:
            best, kbest = v, k
        elif best - v >= prom:
            return kbest
    return int(np.argmax(z))

def main():
    args = cli(__doc__).parse_args()
    d, meta = load_dem(args.dem)
    r = np.arange(0, R_MAX_PX, STEP_PX)
    pts = []
    for az in range(0, 360, AZ_STEP):
        rows, cols = ray(CENTRE, az, r)
        k = first_peak(sample(d, rows, cols), PROMINENCE_M)
        pts.append((rows[k], cols[k]))
    rc = np.array(pts)
    write_geojson(rc, meta, args.out, f"Pagan summit crater radial crest: centre r{CENTRE[0]} c{CENTRE[1]}, 36 rays/10 deg, first peak with {PROMINENCE_M:g} m prominence")
    report("Pagan", rc, d, meta, args)

if __name__ == "__main__":
    main()
