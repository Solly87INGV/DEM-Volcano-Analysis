"""Shared helpers for the criterion-based caldera rims of the MorphoVolc 2.0 benchmark.

All rim scripts work on the `dem_working.tif` written by MorphoVolc step 1
(`complete_dem_analysis.py`) for the corresponding DEM, i.e. the DEM already
reprojected to UTM. Vertex coordinates are handled as (row, col) pixel
coordinates of that raster, the same convention used by `volume_unified.py`.
"""
import argparse
import json
import math

import numpy as np
import rasterio
from rasterio import warp
from rasterio.transform import xy
from scipy.ndimage import map_coordinates, gaussian_filter
from skimage.draw import polygon as draw_polygon


# ----------------------------------------------------------------------------- I/O
def load_dem(path):
    """Return (dem as float64, rasterio dataset profile dict)."""
    with rasterio.open(path) as r:
        d = r.read(1).astype(float)
        if r.nodata is not None:
            d[d == r.nodata] = np.nan
        meta = {"transform": r.transform, "crs": r.crs, "res": r.res[0], "shape": d.shape}
    return d, meta


def write_geojson(rc, meta, path, origin, extra=None):
    """Write a closed (row, col) polygon as a lon/lat GeoJSON Feature (EPSG:4326).

    Same convention as the MorphoVolc editor/loader: pixel coordinates are mapped
    with `offset="ul"`, so `load_edited_rim_contour_from_geojson` returns the same
    (row, col) values.
    """
    rc = np.asarray(rc, float)[:, :2]
    xs, ys = xy(meta["transform"], rc[:, 0], rc[:, 1], offset="ul")
    lon, lat = warp.transform(meta["crs"], "EPSG:4326", list(xs), list(ys))
    ring = [[round(a, 7), round(b, 7)] for a, b in zip(lon, lat)]
    ring.append(ring[0])
    props = {"source": "edited", "editing_crs": "EPSG:4326", "origin": origin}
    if extra:
        props.update(extra)
    feat = {"type": "Feature", "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [ring]}}
    with open(path, "w") as fh:
        json.dump(feat, fh)
    return path


def read_geojson_rc(path, meta):
    """Read a lon/lat GeoJSON polygon back to (row, col) pixel coordinates."""
    g = json.load(open(path))
    if g.get("type") == "FeatureCollection":
        g = g["features"][0]
    ring = np.array(g["geometry"]["coordinates"][0], float)
    xs, ys = warp.transform("EPSG:4326", meta["crs"], list(ring[:, 0]), list(ring[:, 1]))
    inv = ~meta["transform"]
    cols, rows = inv * (np.array(xs), np.array(ys))
    rc = np.c_[rows, cols]
    if np.allclose(rc[0], rc[-1]):
        rc = rc[:-1]
    return rc


# ----------------------------------------------------------------------- geometry
def area_m2(rc, res):
    y, x = rc[:, 0], rc[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) * res * res


def mask(rc, shape):
    m = np.zeros(shape, bool)
    rr, cc = draw_polygon(rc[:, 0], rc[:, 1], shape)
    m[rr, cc] = True
    return m


def compare(rc_new, rc_ref, meta):
    """Area of both polygons, IoU of their pixel masks, mean vertex-to-reference distance."""
    a_new, a_ref = area_m2(rc_new, meta["res"]), area_m2(rc_ref, meta["res"])
    m1, m2 = mask(rc_new, meta["shape"]), mask(rc_ref, meta["shape"])
    iou = (m1 & m2).sum() / max((m1 | m2).sum(), 1)
    # distance of each new vertex to the reference outline (densified)
    dense = densify(rc_ref, 0.25)
    dist = np.sqrt(((rc_new[:, None, :] - dense[None, :, :]) ** 2).sum(-1)).min(1) * meta["res"]
    return {"A_new_km2": float(a_new / 1e6), "A_ref_km2": float(a_ref / 1e6),
            "dA_pct": float(100 * (a_new - a_ref) / a_ref), "IoU": float(iou),
            "mean_vertex_dist_m": float(dist.mean()), "max_vertex_dist_m": float(dist.max())}


def densify(rc, step_px):
    pts = []
    n = len(rc)
    for i in range(n):
        p, q = rc[i], rc[(i + 1) % n]
        k = max(int(np.hypot(*(q - p)) / step_px), 1)
        t = np.linspace(0, 1, k, endpoint=False)[:, None]
        pts.append(p + t * (q - p))
    return np.vstack(pts)


# --------------------------------------------------------------------------- rays
def ray(centre, az_deg, r_px):
    """Sample positions along a ray; azimuth clockwise from north (row up)."""
    a = math.radians(az_deg)
    rows = centre[0] - r_px * math.cos(a)
    cols = centre[1] + r_px * math.sin(a)
    return rows, cols


def sample(arr, rows, cols, order=1):
    return map_coordinates(arr, [rows, cols], order=order, mode="nearest")


def slope_deg(d, res, sigma=0.0):
    z = gaussian_filter(d, sigma) if sigma else d
    dy, dx = np.gradient(z, res)
    return np.degrees(np.arctan(np.hypot(dx, dy)))


# ------------------------------------------------------------------------ metrics
def volume_metrics(d, geojson_path, meta, vu_path):
    """Depth-integrated caldera metrics computed exactly as the app does after an import:
    the GeoJSON is read with MorphoVolc's own loader and passed to the frozen
    `caldera_volume_depth_integrated` (volume_unified.py, v0.5.2)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("vu", vu_path)
    vu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vu)
    rc = vu.load_edited_rim_contour_from_geojson(geojson_path, meta["transform"], meta["crs"], meta["shape"])
    o = vu.caldera_volume_depth_integrated(dem=d, caldera_contour=rc, transform=meta["transform"])
    return {"depth_p90_p05_m": o.get("depth_ref_m"), "depth_max_min_m": o.get("depth_max_min_m"),
            "V_caldera_km3": o.get("V_caldera_m3", 0) / 1e9}


def cli(description):
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--dem", required=True, help="dem_working.tif from MorphoVolc step 1")
    p.add_argument("--out", required=True, help="output GeoJSON (caldera_rim_edited.geojson)")
    p.add_argument("--ref", help="reference GeoJSON to compare with (optional)")
    p.add_argument("--vu", help="path to volume_unified.py, to print depth/volume (optional)")
    return p


def report(name, rc, d, meta, args):
    a = area_m2(rc, meta["res"])
    print(f"{name}: {len(rc)} vertices, A = {a/1e6:.3f} km2, D_equiv = {2*math.sqrt(a/math.pi)/1e3:.2f} km")
    if args.vu:
        print("  metrics:", {k: round(v, 4) if isinstance(v, float) else v
                             for k, v in volume_metrics(d, args.out, meta, args.vu).items()})
    if args.ref:
        ref = read_geojson_rc(args.ref, meta)
        print("  vs reference:", {k: round(v, 3) for k, v in compare(rc, ref, meta).items()})
