"""Ambrym (GVP 257040) - outer caldera (~12 km).
Criterion: outline of the flat caldera floor. Slope from the DEM smoothed with a Gaussian
(sigma = 2 px); floor = pixels with slope < 5 deg and elevation > 300 m connected to a
floor seed; morphological closing (3x3, 4 iterations), hole filling, opening (3x3,
3 iterations); the rim is the convex hull of the resulting region. The hull is stable to
the threshold (5 deg: 115 km2, 6 deg: 117 km2), while the region itself is not.
DEM: Ambrym_1.tif (whole island, GLO-30, ~30 m after reprojection)."""
import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import ConvexHull
from rimlib import cli, load_dem, slope_deg, write_geojson, report

SEED = (675, 1120)                 # a pixel of the caldera floor (row, col)
SLOPE_MAX_DEG, SIGMA_PX, Z_MIN_M = 5.0, 2.0, 300.0

def main():
    args = cli(__doc__).parse_args()
    d, meta = load_dem(args.dem)
    sl = slope_deg(d, meta["res"], SIGMA_PX)
    m = (sl < SLOPE_MAX_DEG) & (d > Z_MIN_M)
    lab, _ = ndi.label(m)
    reg = lab == lab[SEED]
    reg = ndi.binary_closing(reg, structure=np.ones((3, 3)), iterations=4)
    reg = ndi.binary_fill_holes(reg)
    reg = ndi.binary_opening(reg, structure=np.ones((3, 3)), iterations=3)
    lab2, _ = ndi.label(reg)
    reg = lab2 == lab2[SEED]
    pts = np.c_[np.nonzero(reg)].astype(float)
    rc = pts[ConvexHull(pts).vertices]
    write_geojson(rc, meta, args.out, f"Ambrym outer caldera: convex hull of the caldera floor (slope < {SLOPE_MAX_DEG:g} deg, Gaussian sigma {SIGMA_PX:g} px, z > {Z_MIN_M:g} m, region connected to seed r{SEED[0]} c{SEED[1]})")
    report("Ambrym outer", rc, d, meta, args)

if __name__ == "__main__":
    main()
