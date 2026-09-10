#!/usr/bin/env python3
# check_gvp_seed_in_dem.py
# -----------------------------------------------------------------------------
# One-pass diagnostic: for every dem_working.tif under an outputs directory,
# find the matching GVP record (by folder name, case-insensitive; fallback to
# vnum if the folder name is numeric), project the GVP coordinates into the
# DEM CRS, and report whether the seed falls inside the raster bounds.
#
# Purpose: separate "DEM problem" (seed outside raster -> tile off-center or
# too small) from "detection problem" (seed inside but rim/center still wrong),
# BEFORE re-downloading anything.
#
# Usage (inside the container):
#   /opt/venv/bin/python /app/backend/scripts/check_gvp_seed_in_dem.py
#   /opt/venv/bin/python .../check_gvp_seed_in_dem.py --outputs /app/backend/outputs \
#        --gvp /app/backend/data/gvp_holocene.json
# -----------------------------------------------------------------------------

import argparse
import glob
import json
import os
import sys

import rasterio
from rasterio.warp import transform as rio_transform


def load_gvp(gvp_path):
    """Return (by_vnum, by_name_lower). Robust to a few snapshot shapes."""
    with open(gvp_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        recs = data
    elif isinstance(data, dict):
        recs = data.get("records")
        if recs is None:
            # fall back to the first list value found
            recs = next((v for v in data.values() if isinstance(v, list)), [])
    else:
        recs = []
    by_vnum = {}
    by_name = {}
    for r in recs:
        if not isinstance(r, dict):
            continue
        v = str(r.get("vnum", "")).strip()
        n = str(r.get("name", "")).strip().lower()
        if v:
            by_vnum[v] = r
        if n:
            by_name.setdefault(n, r)
    return by_vnum, by_name


def match_record(folder, by_vnum, by_name):
    """Match an outputs subfolder name to a GVP record."""
    key = folder.strip()
    # numeric folder -> treat as vnum
    if key.isdigit() and key in by_vnum:
        return by_vnum[key]
    # exact name (case-insensitive)
    lk = key.lower()
    if lk in by_name:
        return by_name[lk]
    # loose contains match (handles 'fernandina' vs 'Fernandina', minor variants)
    for name_lower, rec in by_name.items():
        if name_lower == lk or name_lower.replace(" ", "") == lk.replace(" ", ""):
            return rec
    for name_lower, rec in by_name.items():
        if lk in name_lower or name_lower in lk:
            return rec
    return None


def coord_fields(rec):
    """Extract (lat, lon) tolerating a few field-name variants."""
    for lat_k in ("latitude", "lat", "Latitude", "LAT"):
        if lat_k in rec and rec[lat_k] is not None:
            lat = rec[lat_k]
            break
    else:
        return None, None
    for lon_k in ("longitude", "lon", "lng", "Longitude", "LON"):
        if lon_k in rec and rec[lon_k] is not None:
            lon = rec[lon_k]
            break
    else:
        return None, None
    try:
        return float(lat), float(lon)
    except Exception:
        return None, None


def analyze(dem_path, rec):
    lat, lon = coord_fields(rec)
    if lat is None:
        return {"status": "NO_COORDS"}
    try:
        with rasterio.open(dem_path) as src:
            b = src.bounds
            crs = src.crs
            xs, ys = rio_transform("EPSG:4326", crs, [lon], [lat])
            x, y = xs[0], ys[0]
    except Exception as e:
        return {"status": "DEM_OPEN_ERROR", "error": str(e)}

    inside_x = b.left <= x <= b.right
    inside_y = b.bottom <= y <= b.top
    inside = inside_x and inside_y

    # signed overflow in metres (0 if inside on that axis)
    dx = 0.0
    if x < b.left:
        dx = x - b.left          # negative: west of tile
    elif x > b.right:
        dx = x - b.right         # positive: east of tile
    dy = 0.0
    if y < b.bottom:
        dy = y - b.bottom        # negative: south of tile
    elif y > b.top:
        dy = y - b.top           # positive: north of tile

    # how central is the seed, as a fraction of tile size (0.5,0.5 = perfect center)
    w = b.right - b.left
    h = b.top - b.bottom
    fx = (x - b.left) / w if w else float("nan")
    fy = (y - b.bottom) / h if h else float("nan")

    return {
        "status": "INSIDE" if inside else "OUTSIDE",
        "lat": lat, "lon": lon,
        "seed_xy": (x, y),
        "bounds": (b.left, b.bottom, b.right, b.top),
        "overflow_m": (dx, dy),
        "center_frac": (fx, fy),
        "tile_km": (w / 1000.0, h / 1000.0),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", default="/app/backend/outputs")
    ap.add_argument("--gvp", default="/app/backend/data/gvp_holocene.json")
    ap.add_argument("--dem-name", default="dem_working.tif")
    args = ap.parse_args()

    if not os.path.isdir(args.outputs):
        print(f"ERROR: outputs dir not found: {args.outputs}", file=sys.stderr)
        return 2
    if not os.path.isfile(args.gvp):
        print(f"ERROR: GVP snapshot not found: {args.gvp}", file=sys.stderr)
        return 2

    by_vnum, by_name = load_gvp(args.gvp)
    print(f"[gvp] loaded {len(by_vnum)} records ({len(by_name)} unique names)\n")

    dems = sorted(glob.glob(os.path.join(args.outputs, "*", args.dem_name)))
    if not dems:
        print(f"No {args.dem_name} found under {args.outputs}", file=sys.stderr)
        return 1

    rows = []
    for dem in dems:
        folder = os.path.basename(os.path.dirname(dem))
        rec = match_record(folder, by_vnum, by_name)
        if rec is None:
            rows.append((folder, "NO_GVP_MATCH", "", ""))
            continue
        res = analyze(dem, rec)
        st = res["status"]
        if st in ("NO_COORDS", "DEM_OPEN_ERROR"):
            rows.append((folder, st, res.get("error", ""), ""))
            continue
        dx, dy = res["overflow_m"]
        fx, fy = res["center_frac"]
        off = ""
        if st == "OUTSIDE":
            parts = []
            if dx:
                parts.append(f"{abs(dx)/1000:.1f}km {'E' if dx>0 else 'W'}")
            if dy:
                parts.append(f"{abs(dy)/1000:.1f}km {'N' if dy>0 else 'S'}")
            off = "seed outside by " + " + ".join(parts)
        else:
            off = f"center_frac=({fx:.2f},{fy:.2f})"
        rows.append((folder, st, f"vnum={rec.get('vnum','?')}", off))

    # print aligned table
    wname = max(len(r[0]) for r in rows) + 2
    print(f"{'DEM (folder)':<{wname}} {'STATUS':<14} {'MATCH':<16} DETAIL")
    print("-" * (wname + 14 + 16 + 30))
    for folder, st, match, detail in rows:
        print(f"{folder:<{wname}} {st:<14} {match:<16} {detail}")

    outside = [r for r in rows if r[1] == "OUTSIDE"]
    inside = [r for r in rows if r[1] == "INSIDE"]
    print()
    print(f"Summary: {len(inside)} INSIDE (detection scope), "
          f"{len(outside)} OUTSIDE (DEM to re-download), "
          f"{len(rows)-len(inside)-len(outside)} unresolved.")
    if outside:
        print("\nDEM problem (re-download centered on GVP coords):")
        for r in outside:
            print(f"  - {r[0]}: {r[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
