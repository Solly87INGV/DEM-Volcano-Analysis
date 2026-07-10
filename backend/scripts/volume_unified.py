# backend/scripts/volume_unified.py
# -----------------------------------------------------------------------------
# MorphoVolc 2.0 — Volume Unified
#
# Single scientific pipeline:
# - Edifice volume: prismoid/frustum formula using measured areas from contours:
#       V_edifice = h/3 * (A_base + A_caldera + sqrt(A_base*A_caldera))
# - Caldera volume: depth-integrated (rim reference - DEM) inside caldera mask
# - Effective volume: V_effective = max(0, V_edifice - V_caldera)
#
# Two scenario presets (baseProfile):
# - island: "simple base" (more selective, assumes isolated edifice)
# - continental: "complex base" (more robust to merged topography)
#
# Inputs:
# - Env (recommended):
#     PROCESS_ID, OUTPUTS_DIR, HEADLESS
#     BASE_PROFILE: island|continental
#     MODULE_KEY: "unified_{baseProfile}"
#     ORIGINAL_FILE_NAME, ORIGINAL_FILE_STEM (optional)
#     USE_EDITED_RIM=1|0
#     RIM_PATH=<outputs/<PID>/caldera_rim_edited.geojson>
# - CLI (compat):
#     python volume_unified.py <dem_path> [original_file_stem]
#
# Outputs in OUTPUTS_DIR/<PROCESS_ID>/:
# - metrics.json, metrics.csv
# - volume_results.json (UI-ready)
# - final_doublet_base_vs_caldera.png
# - caldera_rim_auto.geojson
# - dem_preview.png
# - dem_preview.json
# -----------------------------------------------------------------------------

import os
import sys
import json
import csv
import math
import time
import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

import numpy as np

# -------------------- PROJ/GDAL safety (Windows vs Docker) --------------------
def _fix_proj_env():
    try:
        is_windows = (os.name == "nt")
        if is_windows:
            try:
                from pyproj import datadir
                proj_dir = datadir.get_data_dir()
                if proj_dir and os.path.isdir(proj_dir):
                    os.environ["PROJ_LIB"] = proj_dir
                    os.environ.pop("PROJ_DATA", None)
                    print(f"[DEBUG] PROJ_LIB forced to pyproj (Windows): {proj_dir}")
            except Exception as e:
                print(f"[WARN] Unable to force PROJ_LIB to pyproj (Windows): {e}")
            return

        prev_proj_lib = os.environ.pop("PROJ_LIB", None)
        prev_proj_data = os.environ.pop("PROJ_DATA", None)
        if prev_proj_lib is not None:
            print(f"[DEBUG] PROJ_LIB unset (non-Windows): {prev_proj_lib}")
        if prev_proj_data is not None:
            print(f"[DEBUG] PROJ_DATA unset (non-Windows): {prev_proj_data}")
    except Exception as e:
        print(f"[WARN] Unable to fix PROJ env: {e}")

_fix_proj_env()

def _is_headless() -> bool:
    v = str(os.environ.get("HEADLESS", "")).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

HEADLESS = _is_headless()
if HEADLESS:
    os.environ.setdefault("MPLBACKEND", "Agg")

import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.warp import transform as rio_transform
from rasterio.crs import CRS as RioCRS

from scipy.ndimage import (
    sobel,
    gaussian_filter,
    binary_dilation,
    binary_closing,
    binary_fill_holes,
    binary_erosion,
    label,
)

from skimage import measure
from skimage.draw import polygon

import matplotlib.pyplot as plt
from matplotlib import gridspec
from mpl_toolkits.axes_grid1 import make_axes_locatable


# -------------------- paths / ids --------------------
def _script_dir() -> Path:
    return Path(__file__).resolve().parent

def _outputs_base_dir() -> Path:
    base = (os.environ.get("OUTPUTS_DIR") or os.environ.get("OUTPUTS_BASE") or "").strip()
    if base:
        return Path(base).resolve()
    return (_script_dir() / "outputs").resolve()

def _resolve_process_id() -> str:
    pid = (os.environ.get("PROCESS_ID") or "").strip()
    if pid:
        return pid
    return f"local_{int(time.time())}"

def _ensure_proc_dir(process_id: str) -> Path:
    d = _outputs_base_dir() / process_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def _public_path(process_id: str, filename: str) -> str:
    return f"/outputs/{process_id}/{filename}"

def _normalize_base_profile(v: str) -> str:
    s = str(v or "").strip().lower()
    if s in ("island", "simple", "simple_base"):
        return "island"
    if s in ("continental", "complex", "complex_base"):
        return "continental"
    if s in ("shield",):
        return "shield"
    return s or "continental"


# -------------------- GVP-informed preset selection (Week 1-2) --------------------
# Maps GVP primary volcano type -> (base_preset, rim_preset).
# Rationale and per-parameter motivation: see gvp_profile_mapping.md.
# Retro-compatible: when GVP_TYPE is absent or unknown, falls back to the
# legacy behaviour driven by BASE_PROFILE.
_GVP_TYPE_TO_PRESETS: Dict[str, Tuple[str, str]] = {
    # Shield-class edifices: island-style base contour, dedicated shield rim preset
    "shield":            ("island", "shield"),
    "shields":           ("island", "shield"),
    "shield volcano":    ("island", "shield"),
    "shield volcanoes":  ("island", "shield"),
    # Continental / stratovolcano / complex / caldera family
    "caldera":           ("continental", "continental"),
    "calderas":          ("continental", "continental"),
    "stratovolcano":     ("continental", "continental"),
    "stratovolcanoes":   ("continental", "continental"),
    "stratovolcanos":    ("continental", "continental"),
    "complex":           ("continental", "continental"),
    "complex volcano":   ("continental", "continental"),
    "compound":          ("continental", "continental"),
    "compound volcano":  ("continental", "continental"),
    "somma":             ("continental", "continental"),
    "somma volcano":     ("continental", "continental"),
    # Small isolated edifices
    "pyroclastic cone":   ("island", "island"),
    "pyroclastic cones":  ("island", "island"),
    "tuff cone":          ("island", "island"),
    "tuff cones":         ("island", "island"),
    "lava dome":          ("island", "island"),
    "lava domes":         ("island", "island"),
    "maar":               ("island", "island"),
    "maars":              ("island", "island"),
}


def _normalize_gvp_type(v: str) -> str:
    """Lowercase/strip; strip trailing parenthesised suffix like 'Shield(s)'."""
    s = str(v or "").strip().lower()
    # Handle GVP thesaurus style 'shield(s)' -> 'shield'
    if s.endswith("(s)"):
        s = s[:-3].strip()
    return s


def resolve_presets(
    base_profile_env: str,
    gvp_type: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Resolve (base_preset, rim_preset) given the legacy BASE_PROFILE env value
    and an optional GVP primary type.

    Returns (base_preset, rim_preset, source_debug) where source_debug records
    how the choice was made — this ends up in metrics.json for traceability.
    """
    legacy_bp = _normalize_base_profile(base_profile_env)
    gvp_norm = _normalize_gvp_type(gvp_type) if gvp_type else ""

    if gvp_norm and gvp_norm in _GVP_TYPE_TO_PRESETS:
        base_preset, rim_preset = _GVP_TYPE_TO_PRESETS[gvp_norm]
        source = {
            "preset_source": "gvp_mapping",
            "gvp_type_raw": str(gvp_type),
            "gvp_type_normalized": gvp_norm,
            "base_preset": base_preset,
            "rim_preset": rim_preset,
            "base_profile_env": legacy_bp,
        }
        return base_preset, rim_preset, source

    # Fallback: legacy behaviour — base and rim both driven by BASE_PROFILE
    source = {
        "preset_source": "base_profile_env",
        "gvp_type_raw": str(gvp_type) if gvp_type else "",
        "gvp_type_normalized": gvp_norm,
        "base_preset": legacy_bp,
        "rim_preset": legacy_bp,
        "base_profile_env": legacy_bp,
        "reason": "gvp_type_absent" if not gvp_norm else "gvp_type_unknown",
    }
    return legacy_bp, legacy_bp, source


# -------------------- logging --------------------
def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[STEP4B {ts}] {msg}", flush=True)


# -------------------- DEM selection (manifest-first) --------------------
def _pick_dem_manifest_first(proc_dir: Path, cli_dem: Optional[str]) -> Tuple[Path, str]:
    dem_working = proc_dir / "dem_working.tif"
    if dem_working.exists():
        return dem_working, "process_dir/dem_working.tif"
    if cli_dem:
        p = Path(cli_dem).expanduser().resolve()
        if p.exists():
            return p, "cli_dem_path"
    return dem_working, "missing"


# -------------------- CRS safety: ensure metric DEM --------------------
def _utm_epsg_from_lonlat(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 + zone) if lat >= 0 else (32700 + zone)

def ensure_metric_dem(dem_path: Path, proc_dir: Path) -> Path:
    with rasterio.open(dem_path) as src:
        src_crs = src.crs
        if src_crs is None:
            raise RuntimeError("Input DEM has no CRS. Please define CRS before running volume_unified.")

        if not getattr(src_crs, "is_geographic", False):
            return dem_path

        b = src.bounds
        lon_c = (b.left + b.right) / 2.0
        lat_c = (b.bottom + b.top) / 2.0
        epsg = _utm_epsg_from_lonlat(lon_c, lat_c)
        dst_crs = RioCRS.from_epsg(int(epsg))

        working_path = proc_dir / "dem_working.tif"

        dst_transform, dst_width, dst_height = calculate_default_transform(
            src_crs, dst_crs, src.width, src.height, *src.bounds
        )

        dst_profile = src.profile.copy()
        dst_profile.update(
            crs=dst_crs,
            transform=dst_transform,
            width=dst_width,
            height=dst_height
        )

        resampling = Resampling.bilinear if str(src.dtypes[0]).startswith("float") else Resampling.nearest
        dst_arr = np.empty((dst_height, dst_width), dtype=np.float32)

        reproject(
            source=rasterio.band(src, 1),
            destination=dst_arr,
            src_transform=src.transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=resampling,
            src_nodata=src.nodata,
            dst_nodata=src.nodata,
        )

        dst_profile.update(dtype=rasterio.float32)
        with rasterio.open(working_path, "w", **dst_profile) as dst:
            dst.write(dst_arr.astype(rasterio.float32), 1)

        print(f"[DEBUG] DEM geographic ({src_crs}). Reprojected to {dst_crs} -> {working_path}")
        return working_path


# -------------------- geometry helpers --------------------
def _pixel_to_map_xy(transform, row, col) -> Tuple[float, float]:
    x, y = transform * (col + 0.5, row + 0.5)
    return float(x), float(y)

def distance_between_points(r1, c1, r2, c2, transform) -> float:
    x1, y1 = _pixel_to_map_xy(transform, r1, c1)
    x2, y2 = _pixel_to_map_xy(transform, r2, c2)
    return float(np.hypot(x2 - x1, y2 - y1))

def calculate_area(contour: np.ndarray, transform) -> float:
    c = np.asarray(contour, dtype=float)
    if c.shape[0] < 3:
        return 0.0

    xs = np.empty(len(c), dtype=float)
    ys = np.empty(len(c), dtype=float)
    for i in range(len(c)):
        xs[i], ys[i] = _pixel_to_map_xy(transform, c[i, 0], c[i, 1])

    area = 0.5 * np.abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1)))
    return float(area)

def contour_perimeter_m(contour: np.ndarray, transform) -> float:
    c = np.asarray(contour, dtype=float)
    if c.shape[0] < 2:
        return 0.0

    xs = np.empty(len(c), dtype=float)
    ys = np.empty(len(c), dtype=float)
    for i in range(len(c)):
        xs[i], ys[i] = _pixel_to_map_xy(transform, c[i, 0], c[i, 1])

    dx = np.diff(np.r_[xs, xs[0]])
    dy = np.diff(np.r_[ys, ys[0]])
    return float(np.sum(np.hypot(dx, dy)))

def contour_to_mask(contour: np.ndarray, shape: Tuple[int, int]) -> np.ndarray:
    c = np.asarray(contour, dtype=float)
    rr, cc = polygon(c[:, 0], c[:, 1], shape)
    m = np.zeros(shape, dtype=bool)
    m[rr, cc] = True
    return m

def pixel_area_m2_from_transform(transform) -> float:
    try:
        return float(abs(transform.a * transform.e - transform.b * transform.d))
    except Exception:
        try:
            return float(abs(transform.a * transform.e))
        except Exception:
            return 0.0

def dem_nodata_stats(dem: np.ndarray, nodata=None) -> Dict[str, Any]:
    arr = dem.astype(float)
    finite = np.isfinite(arr)
    if nodata is not None:
        try:
            finite = finite & (arr != float(nodata))
        except Exception:
            pass
    valid = arr[finite]
    return {
        "valid_count": int(valid.size),
        "total_count": int(arr.size),
        "valid_min": float(np.min(valid)) if valid.size else None,
        "valid_max": float(np.max(valid)) if valid.size else None,
        "valid_p02": float(np.percentile(valid, 2)) if valid.size else None,
        "valid_p98": float(np.percentile(valid, 98)) if valid.size else None,
    }


# -------------------- preview + auto rim export --------------------
RIM_MAX_VERTICES = 2000
DEM_PREVIEW_MAX_SIZE = 1400

def _same_xy(a: Tuple[float, float], b: Tuple[float, float], eps: float = 1e-12) -> bool:
    return (abs(float(a[0]) - float(b[0])) <= eps) and (abs(float(a[1]) - float(b[1])) <= eps)

def _decimate_contour_vertices(contour: np.ndarray, max_vertices: int = RIM_MAX_VERTICES) -> np.ndarray:
    c = np.asarray(contour, dtype=float)
    n = int(c.shape[0])
    if n <= max_vertices or max_vertices < 3:
        return c
    idx = np.linspace(0, n - 1, max_vertices, dtype=int)
    idx = np.unique(idx)
    return c[idx]

def _transform_xy_lists_to_wgs84(xs: List[float], ys: List[float], src_crs) -> Tuple[List[float], List[float]]:
    if src_crs is None:
        raise RuntimeError("Cannot export to EPSG:4326 because DEM CRS is missing.")

    try:
        if RioCRS.from_user_input(src_crs) == RioCRS.from_epsg(4326):
            return [float(x) for x in xs], [float(y) for y in ys]
    except Exception:
        pass

    lon, lat = rio_transform(src_crs, "EPSG:4326", xs, ys)
    return [float(v) for v in lon], [float(v) for v in lat]

def _contour_rc_to_lonlat_ring(contour: np.ndarray, transform, src_crs) -> List[List[float]]:
    c = _decimate_contour_vertices(np.asarray(contour, dtype=float), max_vertices=RIM_MAX_VERTICES)
    if c.ndim != 2 or c.shape[0] < 3 or c.shape[1] < 2:
        raise ValueError("Contour is invalid or too short for GeoJSON export.")

    xs = []
    ys = []
    for row, col in c:
        x, y = _pixel_to_map_xy(transform, row, col)
        xs.append(float(x))
        ys.append(float(y))

    lons, lats = _transform_xy_lists_to_wgs84(xs, ys, src_crs)
    ring = [[float(lon), float(lat)] for lon, lat in zip(lons, lats)]

    if len(ring) < 3:
        raise ValueError("GeoJSON ring has too few vertices.")

    if not _same_xy(tuple(ring[0]), tuple(ring[-1])):
        ring.append([float(ring[0][0]), float(ring[0][1])])

    return ring

def write_auto_rim_geojson(
    caldera_contour: np.ndarray,
    transform,
    src_crs,
    out_path: Path,
) -> None:
    ring = _contour_rc_to_lonlat_ring(caldera_contour, transform, src_crs)
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [ring],
        },
        "properties": {
            "source": "auto",
            "editing_crs": "EPSG:4326",
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(feature, indent=2, ensure_ascii=False), encoding="utf-8")

def _bounds_to_wgs84(bounds_tuple, src_crs) -> List[List[float]]:
    left, bottom, right, top = [float(v) for v in bounds_tuple]

    try:
        if RioCRS.from_user_input(src_crs) == RioCRS.from_epsg(4326):
            west, south, east, north = left, bottom, right, top
        else:
            xs = [left, right, right, left]
            ys = [bottom, bottom, top, top]
            lons, lats = rio_transform(src_crs, "EPSG:4326", xs, ys)
            west = float(min(lons))
            east = float(max(lons))
            south = float(min(lats))
            north = float(max(lats))
    except Exception as e:
        raise RuntimeError(f"Failed to transform DEM bounds to EPSG:4326: {e}") from e

    return [[south, west], [north, east]]

def _downsample_for_preview(arr: np.ndarray, max_size: int = DEM_PREVIEW_MAX_SIZE) -> np.ndarray:
    h, w = arr.shape
    scale = max(h / float(max_size), w / float(max_size), 1.0)
    step = int(math.ceil(scale))
    if step <= 1:
        return arr
    return arr[::step, ::step]

def write_dem_preview(
    dem: np.ndarray,
    bounds_tuple,
    src_crs,
    out_png: Path,
    out_json: Path,
) -> None:
    arr = dem.astype(float)
    valid = np.isfinite(arr)
    if not np.any(valid):
        raise RuntimeError("Cannot write DEM preview: no valid DEM pixels.")

    preview = _downsample_for_preview(arr, max_size=DEM_PREVIEW_MAX_SIZE)
    valid_p = np.isfinite(preview)
    vals = preview[valid_p]

    vmin = float(np.percentile(vals, 2))
    vmax = float(np.percentile(vals, 98))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        vmin = float(np.min(vals))
        vmax = float(np.max(vals))
    if vmax <= vmin:
        vmax = vmin + 1.0

    preview_norm = np.zeros_like(preview, dtype=float)
    preview_norm[valid_p] = np.clip((preview[valid_p] - vmin) / (vmax - vmin), 0.0, 1.0)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.imsave(str(out_png), preview_norm, cmap="gray", vmin=0.0, vmax=1.0)

    bounds_wgs84 = _bounds_to_wgs84(bounds_tuple, src_crs)
    meta = {
        "bounds": bounds_wgs84,
        "crs": "EPSG:4326",
    }
    out_json.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


# -------------------- base contour selection (profile presets) --------------------
def _find_contours_at_level(dem: np.ndarray, level: float) -> List[np.ndarray]:
    contours = measure.find_contours(dem, float(level))
    return [np.asarray(c) for c in contours if c is not None and len(c) >= 10]

def _touches_border(contour: np.ndarray, shape: Tuple[int, int], margin_px: int = 2) -> bool:
    h, w = shape
    c = np.round(contour).astype(int)
    r = c[:, 0]
    col = c[:, 1]
    return bool(
        np.any(r <= margin_px) or np.any(col <= margin_px) or
        np.any(r >= (h - 1 - margin_px)) or np.any(col >= (w - 1 - margin_px))
    )


# -------------------- edited rim override helpers (Step 4B) --------------------
def _is_closed_ring(coords: List[List[float]], eps: float = 1e-12) -> bool:
    if not coords or len(coords) < 4:
        return False
    a = coords[0]
    b = coords[-1]
    return (abs(float(a[0]) - float(b[0])) <= eps) and (abs(float(a[1]) - float(b[1])) <= eps)

def _feature_to_lonlat_ring(feature: dict) -> List[List[float]]:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ValueError("Edited rim GeoJSON must be a Feature.")

    geom = feature.get("geometry") or {}
    if geom.get("type") != "Polygon":
        raise ValueError("Edited rim GeoJSON geometry must be Polygon.")

    coords = geom.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 1:
        raise ValueError("Edited rim GeoJSON has no polygon coordinates.")

    ring = coords[0]
    if not isinstance(ring, list) or len(ring) < 4:
        raise ValueError("Edited rim polygon ring is too short.")

    for pt in ring:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            raise ValueError("Edited rim polygon contains invalid coordinate.")
        x = float(pt[0])
        y = float(pt[1])
        if not np.isfinite(x) or not np.isfinite(y):
            raise ValueError("Edited rim polygon contains non-finite coordinate.")

    if not _is_closed_ring(ring):
        raise ValueError("Edited rim polygon ring is not closed.")

    return [[float(pt[0]), float(pt[1])] for pt in ring]

def load_edited_rim_contour_from_geojson(
    rim_path: Path,
    transform,
    dst_crs,
    dem_shape: Tuple[int, int],
) -> np.ndarray:
    raw = json.loads(Path(rim_path).read_text(encoding="utf-8"))
    ring_lonlat = _feature_to_lonlat_ring(raw)

    if len(ring_lonlat) >= 2 and ring_lonlat[0] == ring_lonlat[-1]:
        ring_lonlat = ring_lonlat[:-1]

    lons = [float(pt[0]) for pt in ring_lonlat]
    lats = [float(pt[1]) for pt in ring_lonlat]

    if dst_crs is None:
        raise RuntimeError("DEM CRS is missing; cannot project edited rim.")

    try:
        if RioCRS.from_user_input(dst_crs) == RioCRS.from_epsg(4326):
            xs = lons
            ys = lats
        else:
            xs, ys = rio_transform("EPSG:4326", dst_crs, lons, lats)
    except Exception as e:
        raise RuntimeError(f"Failed to transform edited rim to DEM CRS: {e}") from e

    inv = ~transform
    rows = []
    cols = []
    for x, y in zip(xs, ys):
        col, row = inv * (float(x), float(y))
        rows.append(float(row))
        cols.append(float(col))

    contour = np.column_stack([rows, cols]).astype(float)

    if contour.shape[0] < 3:
        raise ValueError("Edited rim contour has too few vertices after reprojection.")

    h, w = dem_shape
    if np.all((contour[:, 0] < -1) | (contour[:, 0] > h + 1) | (contour[:, 1] < -1) | (contour[:, 1] > w + 1)):
        raise ValueError("Edited rim contour falls completely outside DEM extent.")

    return contour

def select_base_contour(dem: np.ndarray, transform, nodata=None, base_profile: str = "continental") -> Tuple[np.ndarray, Dict[str, Any]]:
    demf = dem.astype(float)

    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass
    if not np.any(valid):
        raise ValueError("DEM has no valid pixels.")

    vmin = float(np.min(demf[valid]))
    vmax = float(np.max(demf[valid]))
    rng = float(max(1e-9, vmax - vmin))

    bp = _normalize_base_profile(base_profile)

    if bp == "island":
        ratios = [0.05, 0.08, 0.12, 0.16, 0.20]
        img_area_px = float(demf.shape[0] * demf.shape[1])

        best = None
        best_score = -1e18
        picked_ratio = None
        picked_level = None
        n_total = 0
        n_kept = 0

        for ratio in ratios:
            level = vmin + rng * float(ratio)
            contours = _find_contours_at_level(demf, level)
            if not contours:
                continue
            n_total += len(contours)

            for c in contours:
                if len(c) < 50:
                    continue
                if _touches_border(c, demf.shape, margin_px=2):
                    continue

                mask = contour_to_mask(c, demf.shape)
                area_px = float(np.sum(mask))
                area_frac = float(area_px / img_area_px)

                if area_frac < 0.01 or area_frac > 0.75:
                    continue

                A = calculate_area(c, transform)
                P = contour_perimeter_m(c, transform)
                circ = (4.0 * math.pi * A / (P * P)) if (A > 0 and P > 0) else 0.0

                size_term = -abs(area_frac - 0.35)
                score = 2.0 * size_term + 0.3 * float(circ) + 0.0005 * float(len(c))

                n_kept += 1
                if score > best_score:
                    best_score = score
                    best = c
                    picked_ratio = float(ratio)
                    picked_level = float(level)

        if best is None:
            ratio = 0.12
            level = vmin + rng * ratio
            contours = _find_contours_at_level(demf, level)
            if not contours:
                raise ValueError("No base contours found (island preset).")
            best = max(contours, key=len)
            picked_ratio = float(ratio)
            picked_level = float(level)

        dbg = {
            "method": "island_sweep_scored_no_border",
            "ratios": [float(r) for r in ratios],
            "picked_ratio": float(picked_ratio) if picked_ratio is not None else None,
            "picked_level_m": float(picked_level) if picked_level is not None else None,
            "n_contours_total": int(n_total),
            "n_candidates_kept": int(n_kept),
            "picked_len": int(len(best)),
        }
        return best, dbg

    ratios = [0.03, 0.05, 0.07, 0.10, 0.13, 0.16, 0.20]
    best = None
    img_area_px = float(demf.shape[0] * demf.shape[1])

    for ratio in ratios:
        level = vmin + rng * float(ratio)
        contours = _find_contours_at_level(demf, level)
        if not contours:
            continue

        for c in contours:
            if len(c) < 50:
                continue
            if _touches_border(c, demf.shape, margin_px=2):
                continue

            mask = contour_to_mask(c, demf.shape)
            area_px = float(np.sum(mask))
            area_frac = float(area_px / img_area_px)

            if area_frac < 0.01 or area_frac > 0.75:
                continue

            A = calculate_area(c, transform)
            P = contour_perimeter_m(c, transform)
            circ = (4.0 * math.pi * A / (P * P)) if (A > 0 and P > 0) else 0.0

            size_term = -abs(area_frac - 0.25)
            circ_term = max(0.0, min(1.0, circ))
            len_term = math.log(float(len(c)))

            score = (2.0 * size_term) + (1.0 * circ_term) + (0.25 * len_term)

            cand = {
                "ratio": float(ratio),
                "level_m": float(level),
                "len": int(len(c)),
                "area_frac_px": float(area_frac),
                "circularity": float(circ),
                "score": float(score),
            }

            if (best is None) or (score > best["cand"]["score"]):
                best = {"contour": c, "cand": cand}

    if not best:
        ratio = 0.05
        level = vmin + rng * ratio
        contours = _find_contours_at_level(demf, level)
        if not contours:
            raise ValueError("No base contours found (continental preset, fallback failed).")
        base = max(contours, key=len)
        dbg = {
            "method": "continental_sweep_failed_fallback_longest",
            "fallback_ratio": float(ratio),
            "fallback_level_m": float(level),
            "picked_len": int(len(base)),
        }
        return base, dbg

    dbg = {
        "method": "continental_sweep_scored",
        "picked": best["cand"],
        "ratios_tested": ratios,
    }
    return best["contour"], dbg


# -------------------- caldera rim detection (profile presets) --------------------
def calculate_slope(dem: np.ndarray) -> np.ndarray:
    dx = sobel(dem, axis=1)
    dy = sobel(dem, axis=0)
    return np.hypot(dx, dy)

def _centroid_of_mask(mask: np.ndarray) -> Optional[Tuple[float, float]]:
    rr, cc = np.nonzero(mask)
    if rr.size == 0:
        return None
    return (float(np.mean(rr)), float(np.mean(cc)))

def _peak_in_roi(dem: np.ndarray, roi: np.ndarray) -> Optional[Tuple[float, float]]:
    try:
        vals = np.where(roi, dem.astype(float), -np.inf)
        if not np.isfinite(vals).any():
            return _centroid_of_mask(roi)
        idx = int(np.nanargmax(vals))
        r, c = np.unravel_index(idx, dem.shape)
        return (float(r), float(c))
    except Exception:
        return _centroid_of_mask(roi)

def _min_in_roi(dem: np.ndarray, roi: np.ndarray) -> Optional[Tuple[float, float]]:
    try:
        vals = np.where(roi, dem.astype(float), np.inf)
        if not np.isfinite(vals).any():
            return _centroid_of_mask(roi)
        idx = int(np.nanargmin(vals))
        r, c = np.unravel_index(idx, dem.shape)
        return (float(r), float(c))
    except Exception:
        return _centroid_of_mask(roi)

def find_caldera_contour_morphological(
    dem: np.ndarray,
    base_contour: np.ndarray,
    transform,
    nodata=None,
    preset: str = "continental",
    _override_cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    debug: Dict[str, Any] = {}
    bp = _normalize_base_profile(preset)

    if bp == "island":
        cfg = {
            "roi_dilate_px": 4,
            "smooth_sigma": 1.0,
            "slope_q": 90.0,
            "min_component_px": 400,
            "min_area_frac": 0.02,
            "closing_iterations": 2,
            "extra_dilate_px": 0,
            "max_area_frac": 0.35,
            "inner_buffer_px": 10,
            "center_bias_weight": 18.0,
            "overlap_mode": "reward_inner",
            "post_close_iterations": 2,
            "post_fill_holes": True,
            "center_mode": "centroid",
        }
    elif bp == "shield":
        # Shield preset (Week 1-2 initial values, to be validated against
        # ground truth in Week 7-8). See gvp_profile_mapping.md for rationale.
        # Design goal: summit calderas on shield edifices — compact, steep,
        # small relative to the edifice, sometimes without a true central
        # depression.
        cfg = {
            "roi_dilate_px": 3,               # tight ROI: rim is at summit, not on flanks
            "smooth_sigma": 1.2,
            "slope_q": 93.0,                  # very selective: avoid flank slope breaks
            "min_component_px": 200,          # allow small components
            "min_area_frac": 0.005,
            "closing_iterations": 3,
            "extra_dilate_px": 1,
            "max_area_frac": 0.15,            # shield calderas small vs edifice
            "inner_buffer_px": 6,
            "center_bias_weight": 15.0,
            "overlap_mode": "reward_inner",
            "post_close_iterations": 2,
            "post_fill_holes": True,
            "center_mode": "adaptive",        # switch to centroid if depression is unreliable
            "dep_to_centroid_max_px": 12.0,   # tight: reject depressions far from centroid
        }
    else:
        cfg = {
            "roi_dilate_px": 8,
            "smooth_sigma": 1.2,
            "slope_q": 86.0,
            "min_component_px": 600,
            "min_area_frac": 0.01,
            "closing_iterations": 3,
            "extra_dilate_px": 1,
            "max_area_frac": 0.50,
            "inner_buffer_px": 14,
            "center_bias_weight": 22.0,
            "overlap_mode": "reward_inner",
            "post_close_iterations": 2,
            "post_fill_holes": True,
            "center_mode": "depression",
        }

    if _override_cfg is not None:
        try:
            cfg = dict(_override_cfg)
        except Exception:
            pass

    demf = dem.astype(float)
    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass

    roi = contour_to_mask(base_contour, demf.shape)
    if cfg["roi_dilate_px"] > 0:
        roi = binary_dilation(roi, iterations=int(cfg["roi_dilate_px"]))
    roi = roi & valid

    roi_px = int(np.sum(roi))
    if roi_px < max(200, int(cfg["min_component_px"] * 0.5)):
        roi = valid.copy()
        roi_px = int(np.sum(roi))
        debug["roi_fallback"] = {
            "reason": "base_roi_too_small",
            "roi_px": int(roi_px),
        }
        if roi_px == 0:
            raise ValueError(f"ROI too small (roi_px={roi_px}).")

    inner_buffer_px = int(max(0, cfg["inner_buffer_px"]))
    try:
        roi_inner = binary_erosion(roi, iterations=inner_buffer_px) if inner_buffer_px > 0 else roi
        if int(np.sum(roi_inner)) < 150:
            roi_inner = roi
    except Exception:
        roi_inner = roi

    slope = calculate_slope(demf)
    slopef = slope.astype(float)
    if cfg["smooth_sigma"] and float(cfg["smooth_sigma"]) > 0:
        slopef = gaussian_filter(slopef, sigma=float(cfg["smooth_sigma"]))

    roi_slopes = slopef[roi]
    roi_slopes = roi_slopes[np.isfinite(roi_slopes)]
    if roi_slopes.size == 0:
        raise ValueError("No finite slope values inside ROI.")

    thr = float(np.percentile(roi_slopes, float(cfg["slope_q"])))
    candidates = (slopef >= thr) & roi

    if cfg["closing_iterations"] > 0:
        candidates = binary_closing(candidates, iterations=int(cfg["closing_iterations"]))
    candidates = binary_fill_holes(candidates)
    if cfg["extra_dilate_px"] > 0:
        candidates = binary_dilation(candidates, iterations=int(cfg["extra_dilate_px"]))
    candidates = candidates & roi

    cand_px = int(np.sum(candidates))
    if cand_px == 0:
        raise ValueError("Candidate rim mask empty after morphology.")

    lbl, ncomp = label(candidates)
    if ncomp <= 0:
        raise ValueError("No components in rim candidates.")

    sizes = [int(np.sum(lbl == cid)) for cid in range(1, ncomp + 1)]
    eligible = []

    max_area_px = int(float(cfg["max_area_frac"]) * float(roi_px))
    min_area_px = int(float(cfg.get("min_area_frac", 0.0)) * float(roi_px))
    hard_min_px = int(max(int(cfg["min_component_px"]), min_area_px))

    for cid, sz in zip(range(1, ncomp + 1), sizes):
        if sz < hard_min_px:
            continue
        if max_area_px > 0 and sz > max_area_px:
            continue
        eligible.append((cid, sz))
    if not eligible:
        if not bool(cfg.get("_relaxed_pass", False)):
            cfg_rel = dict(cfg)
            cfg_rel["_relaxed_pass"] = True
            cfg_rel["slope_q"] = float(cfg_rel.get("slope_q", 90.0)) - 4.0
            cfg_rel["min_component_px"] = max(80, int(cfg_rel.get("min_component_px", 400) * 0.5))
            cfg_rel["min_area_frac"] = float(cfg_rel.get("min_area_frac", 0.0)) * 0.25
            cfg_rel["inner_buffer_px"] = max(3, int(cfg_rel.get("inner_buffer_px", 10) * 0.6))
            cfg_rel["closing_iterations"] = int(cfg_rel.get("closing_iterations", 2)) + 1
            cfg_rel["post_close_iterations"] = int(cfg_rel.get("post_close_iterations", 2)) + 2
            debug["fallback_relaxed_cfg"] = {
                "slope_q": cfg_rel["slope_q"],
                "min_component_px": cfg_rel["min_component_px"],
                "min_area_frac": cfg_rel["min_area_frac"],
                "inner_buffer_px": cfg_rel["inner_buffer_px"],
                "closing_iterations": cfg_rel["closing_iterations"],
                "post_close_iterations": cfg_rel["post_close_iterations"],
            }
            return find_caldera_contour_morphological(
                dem,
                base_contour,
                transform,
                nodata=nodata,
                preset=preset,
                _override_cfg=cfg_rel,
            )
        raise ValueError("No rim component meets constraints.")

    if eligible and not bool(cfg.get("_barnie_pass", False)):
        roi_px_f = float(max(1, roi_px))
        max_elig_px = max(int(sz) for _, sz in eligible)
        max_elig_frac = float(max_elig_px) / roi_px_f
        if max_elig_frac < float(cfg.get("barnie_trigger_max_elig_frac", 0.015)):
            cfg_rel2 = dict(cfg)
            cfg_rel2["_barnie_pass"] = True
            cfg_rel2["slope_q"] = float(cfg_rel2.get("slope_q", 90.0)) - 10.0
            cfg_rel2["closing_iterations"] = int(cfg_rel2.get("closing_iterations", 2)) + 4
            cfg_rel2["inner_buffer_px"] = int(cfg_rel2.get("inner_buffer_px", 10)) + 8
            cfg_rel2["min_component_px"] = max(80, int(cfg_rel2.get("min_component_px", 400) * 0.35))
            cfg_rel2["min_area_frac"] = float(cfg_rel2.get("min_area_frac", 0.0)) * 0.25
            cfg_rel2["post_close_iterations"] = int(cfg_rel2.get("post_close_iterations", 2)) + 2
            cfg_rel2["center_mode"] = "centroid"
            cfg_rel2["overlap_mode"] = "penalize_inner"
            debug["fallback_barnie_cfg"] = {
                "trigger_max_elig_frac": float(cfg.get("barnie_trigger_max_elig_frac", 0.015)),
                "max_elig_frac": float(max_elig_frac),
                "slope_q": cfg_rel2["slope_q"],
                "closing_iterations": cfg_rel2["closing_iterations"],
                "inner_buffer_px": cfg_rel2["inner_buffer_px"],
                "min_component_px": cfg_rel2["min_component_px"],
                "min_area_frac": cfg_rel2["min_area_frac"],
                "post_close_iterations": cfg_rel2["post_close_iterations"],
                "center_mode": cfg_rel2["center_mode"],
                "overlap_mode": cfg_rel2["overlap_mode"],
            }
            return find_caldera_contour_morphological(
                dem,
                base_contour,
                transform,
                nodata=nodata,
                preset=preset,
                _override_cfg=cfg_rel2,
            )

    center_mode = str(cfg.get("center_mode", "depression")).strip().lower()

    def _near_border(rc, shape, margin=3):
        if rc is None:
            return True
        r, c = rc
        return (r < margin) or (c < margin) or (r > (shape[0] - 1 - margin)) or (c > (shape[1] - 1 - margin))

    center_dep = _min_in_roi(demf, roi_inner) or _min_in_roi(demf, roi)
    center_ctr = _centroid_of_mask(roi_inner) or _centroid_of_mask(roi)

    roi_px_f = float(max(1, roi_px))
    elig_sizes = [sz for _, sz in eligible] if eligible else []
    max_elig_frac = (max(elig_sizes) / roi_px_f) if elig_sizes else 0.0

    dep_far = False
    if center_dep is not None and center_ctr is not None:
        dr = float(center_dep[0] - center_ctr[0])
        dc = float(center_dep[1] - center_ctr[1])
        dep_far = (dr * dr + dc * dc) ** 0.5 > float(cfg.get("dep_to_centroid_max_px", 25.0))

    auto_centroid = (
        _near_border(center_dep, dem.shape, margin=int(cfg.get("center_border_margin_px", 3)))
        or (max_elig_frac > 0.0 and max_elig_frac < float(cfg.get("min_eligible_frac_for_depression", 0.06)))
        or dep_far
    )

    if center_mode in ("auto", "adaptive"):
        if auto_centroid:
            center = center_ctr
            center_mode_used = "centroid_auto"
        else:
            center = center_dep or center_ctr
            center_mode_used = "depression_auto"
    elif center_mode in ("depression", "min", "pit"):
        center = center_dep or center_ctr
        center_mode_used = "depression"
    elif center_mode in ("centroid", "center"):
        center = center_ctr
        center_mode_used = "centroid"
    else:
        center = _peak_in_roi(demf, roi) or center_ctr
        center_mode_used = "peak_legacy"

    if center is None:
        center = (float(dem.shape[0]) * 0.5, float(dem.shape[1]) * 0.5)
        center_mode_used = "fallback_image_center"

    center_r, center_c = float(center[0]), float(center[1])
    center_used = {
        "row": float(center_r),
        "col": float(center_c),
        "method": f"center_mode:{center_mode_used}",
        "auto_centroid": bool(auto_centroid),
        "max_eligible_frac": float(max_elig_frac),
    }

    best = None
    for cid, sz in eligible:
        m = (lbl == cid)
        rr, cc = np.nonzero(m)
        if rr.size == 0:
            continue

        rad = np.hypot(rr - center_r, cc - center_c)
        mean_radius_px = float(np.mean(rad))
        p90_radius_px = float(np.percentile(rad, 90))

        cent = _centroid_of_mask(m) or (center_r, center_c)
        dr = float(cent[0] - center_r)
        dc = float(cent[1] - center_c)
        d2 = float(dr * dr + dc * dc)

        try:
            inner_overlap = float(np.sum(m & roi_inner)) / float(np.sum(m) + 1e-9)
        except Exception:
            inner_overlap = 0.0

        overlap_mode = str(cfg.get("overlap_mode", "reward_inner")).strip().lower()
        w = float(cfg["center_bias_weight"])
        overlap_term = (-w * inner_overlap) if overlap_mode in ("reward_inner", "inner", "reward") else (+w * inner_overlap)
        key = (
            mean_radius_px + overlap_term,
            p90_radius_px,
            d2,
            -sz
        )

        if best is None or key < best[0]:
            best = (key, cid, sz, cent, inner_overlap, mean_radius_px, p90_radius_px)

    if best is None:
        selected_cid, selected_sz = max(eligible, key=lambda t: t[1])
    else:
        selected_cid = int(best[1])
        selected_sz = int(best[2])
        center_used["selected_component_centroid"] = {"row": float(best[3][0]), "col": float(best[3][1])}
        center_used["selected_component_inner_overlap"] = float(best[4])
        center_used["selected_component_mean_radius_px"] = float(best[5])
        center_used["selected_component_p90_radius_px"] = float(best[6])

    best_mask = (lbl == selected_cid)
    post_close = int(max(0, cfg.get("post_close_iterations", 0)))
    post_fill = bool(cfg.get("post_fill_holes", True))
    if post_close > 0:
        try:
            best_mask = binary_closing(best_mask, iterations=post_close)
        except Exception:
            pass
    if post_fill:
        try:
            best_mask = binary_fill_holes(best_mask)
        except Exception:
            pass
    try:
        lbl2, n2 = label(best_mask)
        if n2 > 1:
            sizes2 = [int(np.sum(lbl2 == cid)) for cid in range(1, n2 + 1)]
            keep = int(np.argmax(sizes2) + 1)
            best_mask = (lbl2 == keep)
    except Exception:
        pass

    contours = measure.find_contours(best_mask.astype(np.uint8), 0.5)
    if not contours:
        raise ValueError("Rim contour extraction failed.")
    caldera_contour = max(contours, key=len)

    debug.update({
        "method": "morphological_slope_roi",
        "preset": bp,
        "config": cfg,
        "roi_px": int(roi_px),
        "candidate_px": int(cand_px),
        "n_components": int(ncomp),
        "all_component_sizes_px": sizes,
        "eligible_component_sizes_px": [int(sz) for (_, sz) in eligible],
        "selected_component_id": int(selected_cid),
        "selected_component_px": int(selected_sz),
        "post_process": {
            "post_close_iterations": int(max(0, cfg.get("post_close_iterations", 0))),
            "post_fill_holes": bool(cfg.get("post_fill_holes", True))
        },
        "center": center_used,
        "threshold": float(thr),
        "contour_len": int(len(caldera_contour)),
    })
    return caldera_contour, debug


# -------------------- caldera volume: depth-integrated --------------------
def outside_ring_mask(mask: np.ndarray, offset_px: int = 1, width_px: int = 3) -> np.ndarray:
    offset_px = int(max(0, offset_px))
    width_px = int(max(1, width_px))
    inner = binary_dilation(mask, iterations=offset_px) if offset_px > 0 else mask
    outer = binary_dilation(mask, iterations=offset_px + width_px)
    return outer & (~inner)

def caldera_volume_depth_integrated(
    dem: np.ndarray,
    caldera_contour: np.ndarray,
    transform,
    nodata=None,
    rim_percentile: float = 90.0,
    floor_percentile: float = 5.0,
    rim_ring_offset_px: int = 1,
    rim_ring_width_px: int = 3
) -> Dict[str, Any]:
    demf = dem.astype(float)
    caldera_mask = contour_to_mask(caldera_contour, demf.shape)

    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass

    ring = outside_ring_mask(caldera_mask, offset_px=rim_ring_offset_px, width_px=rim_ring_width_px)
    rim_vals = demf[ring & valid]
    rim_method = "percentile_on_outside_ring"

    if rim_vals.size == 0:
        c = np.round(np.asarray(caldera_contour)).astype(int)
        ok = (c[:, 0] >= 0) & (c[:, 0] < demf.shape[0]) & (c[:, 1] >= 0) & (c[:, 1] < demf.shape[1])
        c = c[ok]
        if c.size == 0:
            raise ValueError("Caldera contour empty after bounds filtering.")
        rim_vals = demf[c[:, 0], c[:, 1]]
        rim_vals = rim_vals[np.isfinite(rim_vals)]
        if nodata is not None:
            try:
                rim_vals = rim_vals[rim_vals != float(nodata)]
            except Exception:
                pass
        if rim_vals.size == 0:
            raise ValueError("No valid rim samples (ring empty and contour invalid).")
        rim_method = "percentile_on_contour_fallback"

    z_rim = float(np.percentile(rim_vals, float(rim_percentile)))

    inside_vals = demf[caldera_mask & valid]
    if inside_vals.size == 0:
        raise ValueError("No valid DEM values inside caldera mask.")
    z_floor = float(np.percentile(inside_vals, float(floor_percentile)))

    depth_ref_raw = float(z_rim - z_floor)
    depth_ref_clamped = float(max(0.0, depth_ref_raw))

    depth = np.maximum(0.0, z_rim - demf)
    depth[~caldera_mask] = 0.0
    depth[~valid] = 0.0

    Apx = pixel_area_m2_from_transform(transform)
    V = float(np.sum(depth) * Apx)

    return {
        "V_caldera_m3": V,
        "z_rim_ref_m": z_rim,
        "z_floor_ref_m": z_floor,
        "depth_ref_m": depth_ref_raw,
        "depth_ref_m_clamped": depth_ref_clamped,
        "rim_method": rim_method,
        "rim_ring_offset_px": int(rim_ring_offset_px),
        "rim_ring_width_px": int(rim_ring_width_px),
        "rim_sample_count": int(rim_vals.size),
        "rim_percentile": float(rim_percentile),
        "floor_percentile": float(floor_percentile),
        "pixel_area_m2": float(Apx),
        "caldera_mask_area_m2": float(np.sum(caldera_mask & valid) * Apx),
    }


# -------------------- height model (robust) --------------------
def height_p99_minus_p05_inside_base(dem: np.ndarray, base_contour: np.ndarray, nodata=None) -> Dict[str, Any]:
    demf = dem.astype(float)
    valid = np.isfinite(demf)
    if nodata is not None:
        try:
            valid = valid & (demf != float(nodata))
        except Exception:
            pass

    scope = "base_mask"
    try:
        base_mask = contour_to_mask(base_contour, demf.shape)
        vals = demf[base_mask & valid]
    except Exception:
        vals = demf[valid]
        scope = "full_valid_fallback"

    if vals.size < 50:
        vals = demf[valid]
        scope = "full_valid_fallback"

    if vals.size == 0:
        h = 0.0
    else:
        h = float(np.percentile(vals, 99) - np.percentile(vals, 5))

    return {
        "h_max_m": float(h),
        "method": "p99_minus_p05",
        "scope": scope,
        "p_high": 99.0,
        "p_low": 5.0,
        "n": int(vals.size),
    }


# -------------------- opposite points helpers (for visualization only) --------------------
def find_opposite_points(contour: np.ndarray) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    c = np.round(np.asarray(contour)).astype(int)
    if c.shape[0] < 2:
        return (0, 0), (0, 0)
    idx1 = 0
    idx2 = int(len(c) // 2)
    p1 = (int(c[idx1, 0]), int(c[idx1, 1]))
    p2 = (int(c[idx2, 0]), int(c[idx2, 1]))
    return p1, p2

def find_opposite_slope_points(slope: np.ndarray, contour: np.ndarray) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    c = np.round(np.asarray(contour)).astype(int)
    c = c[
        (c[:, 0] >= 0) & (c[:, 0] < slope.shape[0]) &
        (c[:, 1] >= 0) & (c[:, 1] < slope.shape[1])
    ]
    if c.size == 0:
        return (0, 0), (0, 0)

    vals = [slope[r, col] for r, col in c]
    idx1 = int(np.argmax(vals))
    idx2 = (idx1 + len(c) // 2) % len(c)
    p1 = (int(c[idx1, 0]), int(c[idx1, 1]))
    p2 = (int(c[idx2, 0]), int(c[idx2, 1]))
    return p1, p2


# -------------------- outputs: PNG doublet --------------------
def save_final_doublet_png(
    dem: np.ndarray,
    base_contour: np.ndarray,
    base_p1: Tuple[int, int],
    base_p2: Tuple[int, int],
    caldera_contour: np.ndarray,
    cal_p1: Tuple[int, int],
    cal_p2: Tuple[int, int],
    out_path: Path,
):
    out_path.parent.mkdir(parents=True, exist_ok=True)

    FIG_W, FIG_H = 14.5, 5.5
    fig = plt.figure(figsize=(FIG_W, FIG_H))
    gs = gridspec.GridSpec(1, 3, figure=fig, width_ratios=[1.0, 0.08, 1.0], wspace=0.15)

    ax1 = fig.add_subplot(gs[0, 0])
    im1 = ax1.imshow(dem, cmap="terrain", origin="upper", interpolation="nearest", resample=False)
    ax1.plot(base_contour[:, 1], base_contour[:, 0], "w-", linewidth=1)
    ax1.plot(base_p1[1], base_p1[0], "ro", markersize=8)
    ax1.plot(base_p2[1], base_p2[0], "yo", markersize=8)
    ax1.set_title("Base contour + opposite points", pad=8, fontsize=12)
    ax1.set_aspect("equal", adjustable="box")
    div1 = make_axes_locatable(ax1)
    cax1 = div1.append_axes("right", size="4.6%", pad=0.10)
    cbar1 = fig.colorbar(im1, cax=cax1)
    cbar1.set_label("Elevation (m)", rotation=90)

    ax_sp = fig.add_subplot(gs[0, 1])
    ax_sp.axis("off")

    ax2 = fig.add_subplot(gs[0, 2])
    im2 = ax2.imshow(dem, cmap="terrain", origin="upper", interpolation="nearest", resample=False)
    ax2.plot(caldera_contour[:, 1], caldera_contour[:, 0], "b-", linewidth=1)
    ax2.plot(cal_p1[1], cal_p1[0], "ro", markersize=8)
    ax2.plot(cal_p2[1], cal_p2[0], "yo", markersize=8)
    ax2.set_title("Caldera rim + opposite slope points", pad=8, fontsize=12)
    ax2.set_aspect("equal", adjustable="box")
    div2 = make_axes_locatable(ax2)
    cax2 = div2.append_axes("right", size="4.6%", pad=0.10)
    cbar2 = fig.colorbar(im2, cax=cax2)
    cbar2.set_label("Elevation (m)", rotation=90)

    fig.savefig(str(out_path), dpi=170)
    plt.close(fig)


# -------------------- metrics CSV schema (human) --------------------
HUMAN_FIELDS = [
    ("Meta", "Run timestamp (UTC)", "meta.timestamp_utc", ""),
    ("Meta", "Run ID (process_id)", "meta.process_id", ""),
    ("Meta", "Original input filename", "meta.original_file_name", ""),
    ("Meta", "Original input filename (stem)", "meta.original_file_stem", ""),
    ("Meta", "Input DEM path", "meta.input_dem_path", ""),
    ("Meta", "Working DEM path", "meta.working_dem_path", ""),
    ("Meta", "Working DEM CRS (EPSG/WKT)", "meta.crs", ""),
    ("Meta", "Pixel resolution", "meta.res", "m"),
    ("Meta", "NoData value", "meta.nodata", ""),

    ("Scenario", "Base profile", "meta.baseProfile", ""),
    ("Scenario", "Model", "meta.model", ""),

    ("Base morphometrics", "Base area", "morphometrics.A_base_m2", "m²"),
    ("Base morphometrics", "Base perimeter", "morphometrics.P_base_m", "m"),
    ("Base morphometrics", "Base span (opposite points)", "morphometrics.D_base_m", "m"),

    ("Caldera morphometrics", "Caldera area", "morphometrics.A_caldera_m2", "m²"),
    ("Caldera morphometrics", "Caldera perimeter", "morphometrics.P_caldera_m", "m"),
    ("Caldera morphometrics", "Caldera span (opposite points)", "morphometrics.D_caldera_m", "m"),

    ("Height model", "Height method", "height_model.method", ""),
    ("Height model", "Height (P99 - P05)", "height_model.h_max_m", "m"),

    ("Volumes", "Total edifice volume", "volumes.V_total_m3", "m³"),
    ("Volumes", "Caldera volume", "volumes.V_caldera_m3", "m³"),
    ("Volumes", "Effective edifice volume", "volumes.V_effective_m3", "m³"),
]

def _get_by_path(d: dict, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur

def metrics_to_human_rows(metrics: dict):
    rows = []
    for section, label_txt, path, unit in HUMAN_FIELDS:
        v = _get_by_path(metrics, path)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        rows.append((section, label_txt, v, unit))
    return rows

def _as_serializable(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.ndarray,)):
        return v.tolist()
    if isinstance(v, (tuple, list)):
        return [_as_serializable(x) for x in v]
    if isinstance(v, dict):
        return {k: _as_serializable(val) for k, val in v.items()}
    return v


# -------------------- main compute --------------------
def run_unified(
    dem: np.ndarray,
    transform,
    meta: Dict[str, Any],
    base_profile: str,
    caldera_contour_override: Optional[np.ndarray] = None,
    rim_source: str = "auto",
    rim_preset: Optional[str] = None,
) -> Dict[str, Any]:
    nodata = meta.get("nodata", None)

    # rim_preset defaults to base_profile for full backward compatibility.
    # When the GVP mapping produces different presets for base and rim
    # (e.g. Shield -> base=island, rim=shield), the caller passes rim_preset
    # explicitly.
    effective_rim_preset = rim_preset if rim_preset else base_profile

    base_contour, base_dbg = select_base_contour(dem, transform, nodata=nodata, base_profile=base_profile)
    slope = calculate_slope(dem)

    if caldera_contour_override is not None:
        caldera_contour = np.asarray(caldera_contour_override, dtype=float)
        cal_dbg = {
            "method": "geojson_override",
            "source": rim_source,
            "contour_len": int(len(caldera_contour)),
        }
    else:
        caldera_contour, cal_dbg = find_caldera_contour_morphological(
            dem=dem,
            base_contour=base_contour,
            transform=transform,
            nodata=nodata,
            preset=effective_rim_preset,
        )

    base_p1, base_p2 = find_opposite_points(base_contour)
    cal_p1, cal_p2 = find_opposite_slope_points(slope, caldera_contour)

    A_base_m2 = calculate_area(base_contour, transform)
    A_caldera_m2 = calculate_area(caldera_contour, transform)
    P_base_m = contour_perimeter_m(base_contour, transform)
    P_caldera_m = contour_perimeter_m(caldera_contour, transform)

    D_base_m = distance_between_points(base_p1[0], base_p1[1], base_p2[0], base_p2[1], transform)
    D_caldera_m = distance_between_points(cal_p1[0], cal_p1[1], cal_p2[0], cal_p2[1], transform)

    height_model = height_p99_minus_p05_inside_base(dem, base_contour, nodata=nodata)
    h_max_m = float(height_model.get("h_max_m", 0.0))

    if A_base_m2 > 0 and A_caldera_m2 > 0 and h_max_m > 0:
        V_total_m3 = float((h_max_m / 3.0) * (A_base_m2 + A_caldera_m2 + math.sqrt(A_base_m2 * A_caldera_m2)))
    else:
        V_total_m3 = 0.0

    caldera_depth = caldera_volume_depth_integrated(
        dem=dem,
        caldera_contour=caldera_contour,
        transform=transform,
        nodata=nodata,
        rim_percentile=90.0,
        floor_percentile=5.0,
        rim_ring_offset_px=1,
        rim_ring_width_px=3,
    )

    depth_raw = float(caldera_depth.get("depth_ref_m", 0.0))
    V_caldera_m3 = float(caldera_depth.get("V_caldera_m3", 0.0))

    fallback_used = False
    if depth_raw <= 0.0:
        caldera_depth_fb = caldera_volume_depth_integrated(
            dem=dem,
            caldera_contour=caldera_contour,
            transform=transform,
            nodata=nodata,
            rim_percentile=98.0,
            floor_percentile=2.0,
            rim_ring_offset_px=1,
            rim_ring_width_px=3,
        )
        depth_fb = float(caldera_depth_fb.get("depth_ref_m", 0.0))
        Vfb = float(caldera_depth_fb.get("V_caldera_m3", 0.0))
        if (depth_fb > depth_raw and Vfb >= V_caldera_m3) or (depth_fb > 0 and Vfb > 0):
            caldera_depth = caldera_depth_fb
            depth_raw = depth_fb
            V_caldera_m3 = Vfb
            fallback_used = True

    V_effective_m3 = float(max(0.0, V_total_m3 - V_caldera_m3))

    return {
        "base_contour": base_contour,
        "caldera_contour": caldera_contour,
        "base_points": {"p1_rc": base_p1, "p2_rc": base_p2},
        "caldera_points": {"p1_rc": cal_p1, "p2_rc": cal_p2},
        "base_debug": base_dbg,
        "caldera_debug": cal_dbg,
        "height_model": height_model,
        "caldera_depth": caldera_depth,
        "caldera_fallback_used": bool(fallback_used),
        "morphometrics": {
            "A_base_m2": float(A_base_m2),
            "P_base_m": float(P_base_m),
            "D_base_m": float(D_base_m),
            "A_caldera_m2": float(A_caldera_m2),
            "P_caldera_m": float(P_caldera_m),
            "D_caldera_m": float(D_caldera_m),
        },
        "volumes": {
            "V_total_m3": float(V_total_m3),
            "V_caldera_m3": float(V_caldera_m3),
            "V_effective_m3": float(V_effective_m3),
        }
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python volume_unified.py <dem_path> [original_file_stem]")
        return 1

    cli_dem = sys.argv[1]
    cli_stem = sys.argv[2] if len(sys.argv) > 2 else ""

    process_id = _resolve_process_id()
    proc_dir = _ensure_proc_dir(process_id)

    base_profile_env = os.environ.get("BASE_PROFILE") or os.environ.get("BASE_SCENARIO") or ""
    gvp_type_env = os.environ.get("GVP_TYPE") or ""

    # Week 1-2: GVP-informed preset selection.
    # If GVP_TYPE is set and recognised, base_preset and rim_preset are
    # derived from the mapping in _GVP_TYPE_TO_PRESETS. Otherwise both fall
    # back to the legacy BASE_PROFILE behaviour (byte-identical to pre-patch).
    base_preset, rim_preset, preset_source_dbg = resolve_presets(
        base_profile_env=base_profile_env,
        gvp_type=gvp_type_env,
    )

    # Keep the legacy `base_profile` variable name in scope: it drives the
    # module_key naming and metrics.json fields already consumed by the UI.
    # Semantically it now means "base contour preset".
    base_profile = base_preset

    _log(
        f"preset resolution: source={preset_source_dbg['preset_source']} "
        f"base={base_preset} rim={rim_preset} "
        f"gvp_type='{gvp_type_env}' base_profile_env='{base_profile_env}'"
    )

    module_key = (os.environ.get("MODULE_KEY") or "").strip()
    if not module_key:
        module_key = f"unified_{base_profile}"

    dem_path, reason = _pick_dem_manifest_first(proc_dir, cli_dem)
    if reason == "missing" or not dem_path.exists():
        print(f"[ERROR] DEM not found. Expected dem_working.tif in {proc_dir} or valid CLI DEM.")
        return 1

    dem_path = ensure_metric_dem(dem_path, proc_dir)

    with rasterio.open(dem_path) as src:
        dem = src.read(1)
        transform = src.transform
        crs = src.crs
        res = src.res
        nodata = src.nodata
        bounds_tuple = (src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top)

    meta = {
        "process_id": process_id,
        "input_dem_path": str(Path(cli_dem).expanduser().resolve()),
        "working_dem_path": str(dem_path),
        "crs": str(crs) if crs is not None else None,
        "res": list(res) if res is not None else None,
        "nodata": nodata,
        "bounds": list(bounds_tuple),
        "original_file_name": os.environ.get("ORIGINAL_FILE_NAME") or str(Path(cli_dem).name),
        "original_file_stem": os.environ.get("ORIGINAL_FILE_STEM") or (cli_stem or Path(cli_dem).stem),
        "preset_selection": preset_source_dbg,
    }

    rim_source: str = "auto"
    rim_file: str = "caldera_rim_auto.geojson"
    caldera_contour_override: Optional[np.ndarray] = None

    use_edited_rim = str(os.environ.get("USE_EDITED_RIM", "")).strip().lower() in ("1", "true", "yes", "on")
    rim_path_env = (os.environ.get("RIM_PATH") or "").strip()

    if use_edited_rim and rim_path_env:
        rim_candidate = Path(rim_path_env)
        if rim_candidate.exists():
            _log(f"edited rim override requested: {rim_candidate}")
            caldera_contour_override = load_edited_rim_contour_from_geojson(
                rim_path=rim_candidate,
                transform=transform,
                dst_crs=crs,
                dem_shape=dem.shape,
            )
            rim_source = "edited"
            rim_file = rim_candidate.name
            _log(f"edited rim override loaded: vertices={int(caldera_contour_override.shape[0])}")
        else:
            _log(f"edited rim override requested but file is missing: {rim_candidate}")

    out = run_unified(
        dem,
        transform,
        meta,
        base_profile=base_profile,
        caldera_contour_override=caldera_contour_override,
        rim_source=rim_source,
        rim_preset=rim_preset,
    )

    auto_rim_path = proc_dir / "caldera_rim_auto.geojson"
    if rim_source == "auto":
        _log("write_auto_rim_geojson START")
        write_auto_rim_geojson(
            caldera_contour=out["caldera_contour"],
            transform=transform,
            src_crs=crs,
            out_path=auto_rim_path,
        )
        _log("write_auto_rim_geojson DONE")
    else:
        _log("skip write_auto_rim_geojson because edited rim is active")

    dem_preview_png = proc_dir / "dem_preview.png"
    dem_preview_json = proc_dir / "dem_preview.json"
    _log("write_dem_preview START")
    write_dem_preview(
        dem=dem,
        bounds_tuple=bounds_tuple,
        src_crs=crs,
        out_png=dem_preview_png,
        out_json=dem_preview_json,
    )
    _log("write_dem_preview DONE")

    doublet_name = "final_doublet_base_vs_caldera.png"
    doublet_path = proc_dir / doublet_name
    save_final_doublet_png(
        dem=dem,
        base_contour=out["base_contour"],
        base_p1=out["base_points"]["p1_rc"],
        base_p2=out["base_points"]["p2_rc"],
        caldera_contour=out["caldera_contour"],
        cal_p1=out["caldera_points"]["p1_rc"],
        cal_p2=out["caldera_points"]["p2_rc"],
        out_path=doublet_path,
    )

    metrics = {
        "meta": {
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "process_id": process_id,
            "moduleKey": module_key,
            "baseProfile": base_profile,
            "model": "unified_area_prismoid_plus_caldera_depth_integrated",
            "rim_source": rim_source,
            "rim_file": rim_file,

            "original_file_name": meta.get("original_file_name"),
            "original_file_stem": meta.get("original_file_stem"),
            "input_dem_path": meta.get("input_dem_path"),
            "working_dem_path": meta.get("working_dem_path"),
            "crs": meta.get("crs"),
            "res": meta.get("res"),
            "nodata": meta.get("nodata"),

            "base_selection": out.get("base_debug"),
            "caldera_rim_detection": out.get("caldera_debug"),
        },
        "nodata_stats": dem_nodata_stats(dem, nodata=nodata),
        "height_model": out.get("height_model"),
        "morphometrics": out.get("morphometrics"),
        "volume_model": {
            "edifice_model": "prismoid_area_based",
            "caldera_model": "depth_integrated_rim_to_dem",
            "inputs_used": {
                "h_max_m": float(out.get("height_model", {}).get("h_max_m", 0.0)),
                "A_base_m2": float(out["morphometrics"]["A_base_m2"]),
                "A_caldera_m2": float(out["morphometrics"]["A_caldera_m2"]),
                "caldera_depth": {
                    "rim_reference": {
                        "z_rim_ref_m": out["caldera_depth"].get("z_rim_ref_m"),
                        "percentile": out["caldera_depth"].get("rim_percentile"),
                        "method": out["caldera_depth"].get("rim_method"),
                        "ring_offset_px": out["caldera_depth"].get("rim_ring_offset_px"),
                        "ring_width_px": out["caldera_depth"].get("rim_ring_width_px"),
                        "sample_count": out["caldera_depth"].get("rim_sample_count"),
                    },
                    "floor_reference": {
                        "z_floor_ref_m": out["caldera_depth"].get("z_floor_ref_m"),
                        "percentile": out["caldera_depth"].get("floor_percentile"),
                        "method": "percentile_inside_mask",
                    },
                    "depth_ref_m": out["caldera_depth"].get("depth_ref_m"),
                    "depth_ref_m_clamped": out["caldera_depth"].get("depth_ref_m_clamped"),
                    "mask_area_m2": out["caldera_depth"].get("caldera_mask_area_m2"),
                    "pixel_area_m2": out["caldera_depth"].get("pixel_area_m2"),
                    "fallback_used": bool(out.get("caldera_fallback_used", False)),
                }
            },
        },
        "volumes": out.get("volumes"),
        "geometry": {
            "base_points": out.get("base_points"),
            "caldera_points": out.get("caldera_points"),
        }
    }

    metrics_json_path = proc_dir / "metrics.json"
    metrics_csv_path = proc_dir / "metrics.csv"

    metrics_json_path.write_text(json.dumps(_as_serializable(metrics), indent=2, ensure_ascii=False), encoding="utf-8")

    rows = metrics_to_human_rows(metrics)
    with open(metrics_csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["section", "metric", "value", "unit"])
        for section, label_txt, value, unit in rows:
            w.writerow([section, label_txt, value, unit])

    res0 = meta.get("res")
    pixel_size_m = None
    try:
        if isinstance(res0, (list, tuple)) and len(res0) >= 1:
            pixel_size_m = float(res0[0])
    except Exception:
        pixel_size_m = None

    mm = out["morphometrics"]
    vv = out["volumes"]

    base_area_km2 = float(mm["A_base_m2"]) * 1e-6
    caldera_area_km2 = float(mm["A_caldera_m2"]) * 1e-6
    base_width_km = float(mm["D_base_m"]) * 1e-3
    caldera_width_km = float(mm["D_caldera_m"]) * 1e-3

    total_km3 = float(vv["V_total_m3"]) * 1e-9
    caldera_km3 = float(vv["V_caldera_m3"]) * 1e-9
    eff_km3 = float(vv["V_effective_m3"]) * 1e-9

    summary = (
        f"Base area: {base_area_km2:.2f} km²\n"
        f"Base span: {base_width_km:.2f} km\n"
        f"Caldera area: {caldera_area_km2:.2f} km²\n"
        f"Caldera span: {caldera_width_km:.2f} km\n"
        f"Total edifice volume: {total_km3:.2f} km³\n"
        f"Caldera volume: {caldera_km3:.3e} km³\n"
        f"Effective edifice volume: {eff_km3:.2f} km³"
    )

    vr = {
        "processId": process_id,
        "status": "completed",
        "moduleKey": module_key,
        "baseProfile": base_profile,
        "volumeType": "unified",
        "approximationType": "unified",
        "rim_source": rim_source,
        "rim_file": rim_file,
        "summaryText": summary,
        "result": {
            "base_area_km2": base_area_km2,
            "base_width_km": base_width_km,
            "caldera_area_km2": caldera_area_km2,
            "caldera_width_km": caldera_width_km,
            "total_volume_km3": total_km3,
            "caldera_volume_km3": caldera_km3,
            "effective_volume_km3": eff_km3,
            "h_max_m": float(out.get("height_model", {}).get("h_max_m", 0.0)),
            "pixel_size_m": pixel_size_m,
        },
        "images": ["final_doublet_base_vs_caldera.png"],
        "links": {
            "metrics_json": _public_path(process_id, "metrics.json"),
            "metrics_csv": _public_path(process_id, "metrics.csv"),
            "final_doublet": _public_path(process_id, "final_doublet_base_vs_caldera.png"),
            "caldera_rim_auto": _public_path(process_id, "caldera_rim_auto.geojson"),
            "dem_preview_png": _public_path(process_id, "dem_preview.png"),
            "dem_preview_json": _public_path(process_id, "dem_preview.json"),
        }
    }

    vr_path = proc_dir / "volume_results.json"
    vr_path.write_text(json.dumps(vr, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[INFO] Wrote: {vr_path}")
    print(f"[INFO] Wrote: {metrics_json_path}")
    print(f"[INFO] Wrote: {metrics_csv_path}")
    print(f"[INFO] Wrote: {doublet_path}")
    if auto_rim_path.exists():
        print(f"[INFO] Wrote: {auto_rim_path}")
    print(f"[INFO] Wrote: {dem_preview_png}")
    print(f"[INFO] Wrote: {dem_preview_json}")

    try:
        print(json.dumps({
            "processId": process_id,
            "status": "completed",
            "moduleKey": module_key,
            "baseProfile": base_profile,
            "rim_source": rim_source,
            "rim_file": rim_file,
            "images": ["final_doublet_base_vs_caldera.png"],
        }, ensure_ascii=False), flush=True)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())