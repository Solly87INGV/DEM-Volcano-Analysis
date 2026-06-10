#!/usr/bin/env python3
# build_pdf_report.py
#
# Usage:
#   python build_pdf_report.py <processId> [moduleKey]
#
# Env:
#   OUTPUTS_DIR=/path/to/outputs   (default: ./outputs next to this script's parent)
#
# Output:
#   /outputs/<pid>/report_<moduleKey>_<demBaseName>.pdf   (preferred)
#   /outputs/<pid>/report.pdf                             (copy for backward compatibility)

import os
import re
import json
import shutil
from pathlib import Path

SUPPORTED_KEYS = {
    "circular_approx1",
    "circular_approx2",
    "elliptical_approx1",
    "elliptical_approx2",
}

MODULE_TITLE = {
    "circular_approx1": ("Circular Base", "Approximation Type 1"),
    "circular_approx2": ("Circular Base", "Approximation Type 2"),
    "elliptical_approx1": ("Elliptical Base", "Approximation Type 1"),
    "elliptical_approx2": ("Elliptical Base", "Approximation Type 2"),
}

# Order hints: keep similar to your Fernandina reference PDF
PREFERRED_IMAGE_ORDER = [
    "dem_overview.png",
    "aspect_overview.png",
    "double_01.png",
    "double_02.png",
    "double_03.png",
    "double_04.png",
    # final always at end (handled separately)
]

FINAL_DOUBLET_NAME = "final_doublet_base_vs_caldera.png"


def _read_json(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_filename(s: str) -> str:
    # keep letters, numbers, underscore, dash, dot
    s = re.sub(r"[^\w\-.]+", "_", s.strip())
    s = re.sub(r"_+", "_", s)
    return s.strip("_") or "Unknown"


def _pick_input_dem_name(meta: dict, vol: dict, fallback_pid: str) -> str:
    # 1) meta.json (best)
    if isinstance(meta, dict):
        v = meta.get("input_dem_name")
        if isinstance(v, str) and v.strip():
            return v.strip()

    # 2) volume_results.json might have original_file_name (base without ext)
    if isinstance(vol, dict):
        base = vol.get("original_file_name")
        if isinstance(base, str) and base.strip():
            # assume tif if not known
            return f"{base.strip()}.tif"

    # 3) fallback
    return f"{fallback_pid}.tif"


def _pick_module_key(cli_module_key: str, meta: dict, vol: dict) -> str:
    """
    Priority:
      1) CLI moduleKey (from /api/report?moduleKey=...)
      2) meta.json moduleKey
      3) volume_results.json moduleKey
      4) default
    Normalize (strip/lower) to avoid casing issues.
    """
    mk_cli = (cli_module_key or "").strip().lower()
    if mk_cli in SUPPORTED_KEYS:
        return mk_cli

    if isinstance(meta, dict):
        mk = meta.get("moduleKey")
        if isinstance(mk, str) and mk.strip().lower() in SUPPORTED_KEYS:
            return mk.strip().lower()

    if isinstance(vol, dict):
        mk = vol.get("moduleKey")
        if isinstance(mk, str) and mk.strip().lower() in SUPPORTED_KEYS:
            return mk.strip().lower()

    return "circular_approx1"  # safe default


def _build_title(module_key: str, dem_name: str) -> str:
    base_label, approx_label = MODULE_TITLE.get(module_key, ("Circular Base", "Approximation Type 1"))
    return f"Calculation Results - {base_label}, {approx_label} Input DEM: {dem_name}"


def _resolve_images(proc_dir: Path, vol: dict, analysis: dict):
    """
    Returns:
      images_main: list of tuples (path, caption)
      final_img: tuple(path, caption) or None
    Resolution strategy:
      - use volume_results.json.images[].filename if present
      - enrich using analysis_images.json abs_path if present
      - ensure FINAL_DOUBLET_NAME always included if exists
    """
    # map filename -> abs_path from analysis_images.json
    abs_map = {}
    if isinstance(analysis, dict):
        imgs = analysis.get("images")
        if isinstance(imgs, list):
            for it in imgs:
                if isinstance(it, dict):
                    fn = it.get("filename")
                    ap = it.get("abs_path")
                    if isinstance(fn, str) and isinstance(ap, str):
                        abs_map[fn] = ap

    # gather filenames from volume_results.json
    filenames = []
    captions = {}

    if isinstance(vol, dict):
        imgs = vol.get("images")
        if isinstance(imgs, list):
            for it in imgs:
                if isinstance(it, dict):
                    fn = it.get("filename") or it.get("file") or it.get("name")
                    if isinstance(fn, str) and fn.strip():
                        fn = fn.strip()
                        filenames.append(fn)
                        cap = it.get("title")
                        if isinstance(cap, str) and cap.strip():
                            captions[fn] = cap.strip()
                elif isinstance(it, str) and it.strip():
                    filenames.append(it.strip())

    # Deduplicate while preserving order
    seen = set()
    filenames = [f for f in filenames if not (f in seen or seen.add(f))]

    # Ensure preferred images appear (if they exist on disk)
    for f in PREFERRED_IMAGE_ORDER:
        if f not in filenames:
            if (proc_dir / f).exists() or f in abs_map:
                filenames.append(f)

    # Separate final doublet and force inclusion at end if exists
    final_img = None
    final_path = None
    if (proc_dir / FINAL_DOUBLET_NAME).exists():
        final_path = proc_dir / FINAL_DOUBLET_NAME
    elif FINAL_DOUBLET_NAME in abs_map:
        final_path = Path(abs_map[FINAL_DOUBLET_NAME])

    if final_path and final_path.exists():
        final_img = (final_path, captions.get(FINAL_DOUBLET_NAME, "Base vs Caldera (final doublet)"))

    # Filter out any missing files, resolve abs paths first then fallback to proc_dir
    resolved = []
    for fn in filenames:
        if fn == FINAL_DOUBLET_NAME:
            continue  # handled separately
        p = Path(abs_map[fn]) if fn in abs_map else (proc_dir / fn)
        if p.exists():
            resolved.append((p, captions.get(fn, fn)))

    return resolved, final_img


def _make_results_list(vol: dict):
    result = vol.get("result") if isinstance(vol, dict) else None
    if not isinstance(result, dict):
        result = {}

    rows = [
        ("Base area of the volcano", result.get("base_area_km2"), "km²"),
        ("Base width (Distance between opposite points of the base)", result.get("base_width_km"), "km"),
        ("Caldera area of the volcano", result.get("caldera_area_km2"), "km²"),
        ("Caldera width (Distance between opposite points of the caldera)", result.get("caldera_width_km"), "km"),
        ("Total volume of the volcanic edifice", result.get("total_volume_km3"), "km³"),
        ("Caldera volume", result.get("caldera_volume_km3"), "km³"),
        ("Effective volume of the volcanic edifice", result.get("effective_volume_km3"), "km³"),
    ]

    def fmt(v):
        if v is None:
            return "N/A"
        try:
            if isinstance(v, (int, float)):
                return f"{v:.4g}"
            return str(v)
        except Exception:
            return "N/A"

    out = []
    for label, val, unit in rows:
        out.append(f"{label}: {fmt(val)} {unit}".strip())
    return out


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: build_pdf_report.py <processId> [moduleKey]")
        sys.exit(2)

    process_id = sys.argv[1]
    cli_module_key = sys.argv[2] if len(sys.argv) >= 3 else ""

    outputs_dir = os.environ.get("OUTPUTS_DIR")
    if not outputs_dir:
        outputs_dir = str(Path(__file__).resolve().parent.parent / "outputs")

    proc_dir = Path(outputs_dir) / process_id
    if not proc_dir.exists():
        print(f"[ERROR] process dir not found: {proc_dir}")
        sys.exit(1)

    meta = _read_json(proc_dir / "meta.json") or {}
    vol = _read_json(proc_dir / "volume_results.json") or {}
    analysis = _read_json(proc_dir / "analysis_images.json") or {}

    module_key = _pick_module_key(cli_module_key, meta, vol)
    dem_name = _pick_input_dem_name(meta, vol, process_id)
    dem_base = _safe_filename(Path(dem_name).stem)

    title = _build_title(module_key, dem_name)

    pretty_pdf_name = f"report_{module_key}_{dem_base}.pdf"
    pretty_pdf_path = proc_dir / pretty_pdf_name
    legacy_pdf_path = proc_dir / "report.pdf"

    images_main, final_img = _resolve_images(proc_dir, vol, analysis)
    image_paths = [str(p) for (p, _cap) in images_main]
    captions = [(_cap or None) for (_p, _cap) in images_main]

    if final_img:
        image_paths.append(str(final_img[0]))
        captions.append(final_img[1])

    results_list = _make_results_list(vol)

    # import pdf_generator from scripts/
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from pdf_generator import generate_pdf

    generate_pdf(
        file_path=str(pretty_pdf_path),
        results_list=results_list,
        title=title,
        image_paths=image_paths if image_paths else None,
        captions=captions if image_paths else None
    )

    try:
        shutil.copyfile(pretty_pdf_path, legacy_pdf_path)
    except Exception as e:
        print(f"[WARN] Could not write legacy report.pdf copy: {e}")

    print(f"[OK] Written: {pretty_pdf_path.name}")
    print(f"[OK] Written: {legacy_pdf_path.name}")


if __name__ == "__main__":
    main()
