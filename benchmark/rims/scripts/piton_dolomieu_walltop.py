"""Piton de la Fournaise (GVP 233020) - Dolomieu crater (2007 collapse).
Criterion: rim = top of the crater wall. From the centroid of the fill-sinks depression of
the Dolomieu crater, 36 rays every 10 deg; on each ray, going outward, the wall is the
first stretch with along-ray slope > 25 deg and the rim vertex is the first point after it
where the along-ray slope drops below 10 deg. The az 270 ray stops on an inner ledge and
is replaced by the mean radius of its two neighbours (operator choice).
DEM: Piton_de_la_fournaise.tif (19x16 km tile, GLO-30, ~30 m after reprojection)."""
import math
import numpy as np
from rimlib import cli, load_dem, ray, sample, write_geojson, report

CENTRE = (253.9, 206.9)      # centroid of the fill-sinks depression (row, col)
WALL_DEG, TOP_DEG, R_MAX_PX, STEP_PX, AZ_STEP = 25.0, 10.0, 30, 0.25, 10
INTERPOLATE_AZ = [270]

def main():
    args = cli(__doc__).parse_args()
    d, meta = load_dem(args.dem)
    res = meta["res"]
    r = np.arange(0, R_MAX_PX, STEP_PX)
    radii = {}
    for az in range(0, 360, AZ_STEP):
        rows, cols = ray(CENTRE, az, r)
        z = sample(d, rows, cols)
        s = np.degrees(np.arctan(np.gradient(z, STEP_PX * res)))
        wall = np.nonzero(s > WALL_DEG)[0]
        k = len(r) - 1
        if wall.size:
            top = np.nonzero(s[wall[0]:] < TOP_DEG)[0]
            if top.size:
                k = wall[0] + int(top[0])
        radii[az] = r[k]
    for az in INTERPOLATE_AZ:
        radii[az] = 0.5 * (radii[(az - AZ_STEP) % 360] + radii[(az + AZ_STEP) % 360])
    rc = np.array([ray(CENTRE, az, np.array([radii[az]])) for az in sorted(radii)])[:, :, 0]
    write_geojson(rc, meta, args.out, f"Dolomieu crater: top of the crater wall (first point with along-ray slope < {TOP_DEG:g} deg after the > {WALL_DEG:g} deg wall), 36 rays/10 deg from r{CENTRE[0]} c{CENTRE[1]}; az {INTERPOLATE_AZ} interpolated from neighbours")
    report("Piton/Dolomieu", rc, d, meta, args)

if __name__ == "__main__":
    main()
