"""Tengger (GVP 263310) - Sea of Sand caldera (target defined with F. Galetto).
Criterion: crest of the caldera wall. Wall = largest 8-connected component of pixels with
slope > 28 deg in a ring 2.5-6.5 km from the caldera centre. From the centre, 72 rays every
5 deg; on each ray the rim vertex is the highest DEM point within 12 px outward of the
first wall pixel (sampling every 0.5 px). The W side has no wall (az 230-275): those rays
are dropped and the side is closed by a straight segment.
DEM: Tengger.tif (20 km tile, GLO-30, ~31 m after reprojection)."""
import numpy as np
from scipy import ndimage as ndi
from rimlib import cli, load_dem, ray, sample, slope_deg, write_geojson, report

CENTRE = (300, 340)
WALL_DEG, R_IN_M, R_OUT_M, WINDOW_PX, STEP_PX, AZ_STEP = 28.0, 2500, 6500, 12, 0.5, 5
GAP = (230, 275)

def main():
    args = cli(__doc__).parse_args()
    d, meta = load_dem(args.dem)
    res = meta["res"]
    rr, cc = np.indices(d.shape)
    dist = np.hypot(rr - CENTRE[0], cc - CENTRE[1]) * res
    m = (slope_deg(d, res) > WALL_DEG) & (dist >= R_IN_M) & (dist <= R_OUT_M)
    lab, n = ndi.label(m, structure=np.ones((3, 3)))
    wall = lab == (1 + int(np.argmax(ndi.sum(m, lab, range(1, n + 1)))))
    r = np.arange(0, 7000 / res, STEP_PX)
    pts = []
    for az in range(0, 360, AZ_STEP):
        if GAP[0] <= az <= GAP[1]:
            continue
        rows, cols = ray(CENTRE, az, r)
        w = np.nonzero(sample(wall.astype(float), rows, cols, order=0) > 0.5)[0]
        seg = slice(w[0], w[0] + int(WINDOW_PX / STEP_PX))
        k = int(np.argmax(sample(d, rows[seg], cols[seg])))
        pts.append((rows[seg][k], cols[seg][k]))
    rc = np.array(pts)
    write_geojson(rc, meta, args.out, f"Tengger Sea of Sand caldera: wall crest (max within {WINDOW_PX} px outward of the first pixel of the main wall component, slope > {WALL_DEG:g} deg, {R_IN_M}-{R_OUT_M} m from r{CENTRE[0]} c{CENTRE[1]}), 72 rays/5 deg; W side az {GAP[0]}-{GAP[1]} closed by a straight segment")
    report("Tengger", rc, d, meta, args)

if __name__ == "__main__":
    main()
