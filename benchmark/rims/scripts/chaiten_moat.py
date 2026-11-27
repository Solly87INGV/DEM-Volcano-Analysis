"""Chaiten (GVP 358041) - caldera (dome-filled).
Criterion: caldera margin = foot of the caldera wall at the outer edge of the moat. From
the caldera centre, 36 rays every 10 deg; on each ray the moat floor is the lowest point
between 0.7 and 1.55 km, and the rim vertex is the first point beyond it that rises 60 m
above that floor. Rays falling in the three breaches (N gap az 330-350, East Breach Creek
az 110-130, Caldera Creek az 180-190) are dropped, so the breaches are closed by straight
segments. Sensitivity: threshold 40-80 m, step 5-10 deg -> A 5.6-6.2 km2.
DEM: Chaiten.tif (GLO-30, ~25 m after reprojection)."""
import numpy as np
from rimlib import cli, load_dem, ray, sample, write_geojson, report

CENTRE = (100, 120)
R_MIN_M, R_MAX_M, RISE_M, STEP_PX, AZ_STEP = 700, 1550, 60.0, 0.25, 10
GAPS = [(330, 350), (110, 130), (180, 190)]

def main():
    args = cli(__doc__).parse_args()
    d, meta = load_dem(args.dem)
    res = meta["res"]
    r = np.arange(R_MIN_M / res, R_MAX_M / res, STEP_PX)
    pts = []
    for az in range(0, 360, AZ_STEP):
        if any(lo <= az <= hi for lo, hi in GAPS):
            continue
        rows, cols = ray(CENTRE, az, r)
        z = sample(d, rows, cols)
        k0 = int(np.argmin(z))
        above = np.nonzero(z[k0:] >= z[k0] + RISE_M)[0]
        k = k0 + (int(above[0]) if above.size else int(np.argmax(z[k0:])))
        pts.append((rows[k], cols[k]))
    rc = np.array(pts)
    write_geojson(rc, meta, args.out, f"Chaiten: wall foot at the outer edge of the moat (first point rising {RISE_M:g} m above the moat floor found between {R_MIN_M} and {R_MAX_M} m), centre r{CENTRE[0]} c{CENTRE[1]}, 36 rays/10 deg, gaps {GAPS} closed by straight segments")
    report("Chaiten", rc, d, meta, args)

if __name__ == "__main__":
    main()
