"""Izu-Oshima (GVP 284010) - summit caldera, open to the east.
Criterion: crest of the caldera wall, east side closed by a straight segment.
Wall pixels: slope > 22 deg in a ring 0.9-2.5 km from the caldera centre, excluding the
Mihara-yama cone (< 0.8 km from its crater); only 8-connected components of at least
20 px are kept (the result is the same for any minimum size between 10 and 90 px).
1) Rays from the centre (az 0 and az 130-350, every 10 deg, 0.5 px sampling): first wall
   pixel along the ray; rim vertex = highest DEM point from 2 px inside to 6 px outside it.
2) NW scarp, which runs N-S and is badly sampled by rays: followed row by row from the
   az 0 vertex northwards to its NE end (row 54); on each row, j = column of maximum slope
   in columns 140-189, and the vertex is the highest DEM point in columns j-5 .. j+2;
   one vertex every 4 rows.
3) The east side has no wall (az ~10-120): it is closed by the straight segment between
   the NE end of the scarp and the az 130 vertex.
The row-54 end of the scarp and the 140-189 column window are operator choices.
DEM: Izu-Oshima_2.tif (9x9 km tile, GLO-30, ~28 m after reprojection)."""
import numpy as np
from scipy import ndimage as ndi
from rimlib import cli, load_dem, ray, sample, slope_deg, write_geojson, report

CENTRE, CRATER = (160, 150), (170, 148)
WALL_DEG, R_IN_M, R_OUT_M, CONE_M, MIN_COMP_PX = 22.0, 900, 2500, 800, 20
IN_PX, OUT_PX, STEP_PX = 2, 6, 0.5
RAY_AZ = [0] + list(range(130, 360, 10))
NE_END_ROW, COLS, ROW_STEP, WIN = 54, (140, 190), 4, (5, 2)

def main():
    args = cli(__doc__).parse_args()
    d, meta = load_dem(args.dem)
    res = meta["res"]
    sl = slope_deg(d, res)
    rr, cc = np.indices(d.shape)
    dist = np.hypot(rr - CENTRE[0], cc - CENTRE[1]) * res
    dcone = np.hypot(rr - CRATER[0], cc - CRATER[1]) * res
    wall = (sl > WALL_DEG) & (dist >= R_IN_M) & (dist <= R_OUT_M) & (dcone > CONE_M)
    lab, n = ndi.label(wall, structure=np.ones((3, 3)))
    size = ndi.sum(wall, lab, range(1, n + 1))
    wall = np.isin(lab, [i + 1 for i in range(n) if size[i] >= MIN_COMP_PX])
    r = np.arange(R_IN_M / res, R_OUT_M / res, STEP_PX)
    ray_pts = {}
    for az in RAY_AZ:
        rows, cols = ray(CENTRE, az, r)
        w = np.nonzero(sample(wall.astype(float), rows, cols, order=0) > 0.5)[0]
        seg = slice(max(w[0] - int(IN_PX / STEP_PX), 0), w[0] + int(OUT_PX / STEP_PX) + 1)
        k = int(np.argmax(sample(d, rows[seg], cols[seg])))
        ray_pts[az] = (rows[seg][k], cols[seg][k])
    start_row = int(round(ray_pts[0][0])) - 1
    scarp = []
    for row in range(start_row, NE_END_ROW - 1, -ROW_STEP):
        j = COLS[0] + int(np.argmax(sl[row, COLS[0]:COLS[1]]))
        c = j - WIN[0] + int(np.argmax(d[row, j - WIN[0]:j + WIN[1] + 1]))
        scarp.append((row, c))
    rc = np.array([ray_pts[0]] + scarp + [ray_pts[az] for az in RAY_AZ[1:]], float)
    write_geojson(rc, meta, args.out, "Izu-Oshima summit caldera wall crest (slope > 22 deg, 0.9-2.5 km from r160 c150, cone excluded); rays az 0 and 130-350/10 deg, crest within -2/+6 px of the first wall pixel; NW scarp followed row by row to row 54; open E side closed by a straight segment")
    report("Izu-Oshima", rc, d, meta, args)

if __name__ == "__main__":
    main()
