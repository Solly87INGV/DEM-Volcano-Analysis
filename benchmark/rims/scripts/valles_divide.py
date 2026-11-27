"""Valles (GVP 327010) - caldera.
Criterion: topographic rim = drainage divide of the caldera catchment above its single
surface outlet (head of San Diego Canyon, Self et al. 1996). D8 flow directions with
pysheds (fill pits, fill depressions, resolve flats); upstream catchment of the outlet
pixel (confluence of East Fork Jemez River and San Antonio Creek); unrouted flat cells of
Valle Grande are included by hole filling; outline simplified (Douglas-Peucker, 20 px).
DEM: Valles.tif (GLO-30, ~26 m after reprojection)."""
import numpy as np
np.in1d = getattr(np, "in1d", np.isin)   # pysheds 0.5 + numpy 2
from scipy import ndimage as ndi
from skimage import measure
from pysheds.grid import Grid
from rimlib import cli, load_dem, write_geojson, report
from d8_catchment import catchment

OUTLET = (871, 329)       # (row, col) on dem_working.tif
SIMPLIFY_PX = 20

def flowdir(path):
    grid = Grid.from_raster(path)
    dem = grid.read_raster(path)
    dem = grid.fill_pits(dem)
    dem = grid.fill_depressions(dem)
    dem = grid.resolve_flats(dem)
    return np.asarray(grid.flowdir(dem, dirmap=(64, 128, 1, 2, 4, 8, 16, 32)))

def main():
    p = cli(__doc__)
    p.add_argument("--fdir", help="optional precomputed flow-direction .npy")
    args = p.parse_args()
    d, meta = load_dem(args.dem)
    fd = np.load(args.fdir) if args.fdir else flowdir(args.dem)
    cat = ndi.binary_fill_holes(catchment(fd, [OUTLET]))
    lab, _ = ndi.label(cat)
    cat = lab == lab[OUTLET]
    cs = measure.find_contours(cat.astype(float), 0.5)
    c = max(cs, key=len)
    rc = measure.approximate_polygon(c, tolerance=SIMPLIFY_PX)[:-1]
    write_geojson(rc, meta, args.out, f"Valles: drainage divide of the caldera catchment above the single outlet r{OUTLET[0]} c{OUTLET[1]} (D8, pysheds), holes filled, Douglas-Peucker {SIMPLIFY_PX} px")
    report("Valles", rc, d, meta, args)
    return fd, cat

if __name__ == "__main__":
    main()
