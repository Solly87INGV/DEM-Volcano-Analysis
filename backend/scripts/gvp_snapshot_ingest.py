#!/usr/bin/env python3
# backend/scripts/gvp_snapshot_ingest.py
# -----------------------------------------------------------------------------
# Ingest the Smithsonian GVP Holocene volcano list (Excel export) into the
# static JSON snapshot consumed by backend/gvp/client.js.
#
# Usage:
#   python scripts/gvp_snapshot_ingest.py \
#       --input path/to/GVP_Volcano_List_Holocene.xlsx \
#       --output data/gvp_holocene.json \
#       [--snapshot-version 1.0.0]
#
# Design notes:
# - The GVP export column names have shifted over the years. This script
#   accepts a small set of aliases per field; if a required column is not
#   found, it fails fast with a diagnostic that names the columns it did see.
# - vnum is coerced to a stripped string to preserve leading zeros.
# - primary_volcano_type is passed through *unchanged* (raw GVP string).
#   Normalization to preset happens in _normalize_gvp_type in
#   volume_unified.py, mirrored by normalizeGvpType() in gvp/client.js.
# - The output is deterministic (sorted by vnum) so diffs are readable.
# -----------------------------------------------------------------------------

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    print("[FATAL] pandas is required. Install with: pip install pandas openpyxl", file=sys.stderr)
    sys.exit(2)


# Column-name aliases seen in past GVP exports. Extend as needed.
COLUMN_ALIASES = {
    "vnum":                 ["Volcano Number", "VNum", "vnum", "Number"],
    "name":                 ["Volcano Name", "Name"],
    "primary_volcano_type": ["Primary Volcano Type", "Primary Type", "Volcano Type", "Type"],
    "country":              ["Country"],
    "region":               ["Region"],
    "subregion":            ["Subregion", "Sub-Region", "Sub Region"],
    "latitude":             ["Latitude", "Lat", "Latitude (Decimal Degrees)"],
    "longitude":            ["Longitude", "Lon", "Lng", "Longitude (Decimal Degrees)"],
    "elevation_m":          ["Elevation (m)", "Elevation", "Elev (m)", "Elev"],
}

REQUIRED_FIELDS = ("vnum", "name", "primary_volcano_type")


def _resolve_columns(df_columns: list[str]) -> dict[str, str]:
    """Return field -> actual df column name mapping. Fail on missing required."""
    resolved: dict[str, str] = {}
    lookup = {c.strip(): c for c in df_columns}
    lookup_lc = {c.strip().lower(): c for c in df_columns}

    for field, aliases in COLUMN_ALIASES.items():
        found = None
        for a in aliases:
            if a in lookup:
                found = lookup[a]
                break
            if a.lower() in lookup_lc:
                found = lookup_lc[a.lower()]
                break
        if found is not None:
            resolved[field] = found

    missing_required = [f for f in REQUIRED_FIELDS if f not in resolved]
    if missing_required:
        print(
            f"[FATAL] required column(s) not found: {missing_required}\n"
            f"        observed columns: {list(df_columns)}",
            file=sys.stderr,
        )
        sys.exit(2)

    return resolved


def _to_str_stripped(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    return str(v).strip()


def _to_float_or_none(v):
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int_or_none(v):
    f = _to_float_or_none(v)
    if f is None:
        return None
    try:
        return int(round(f))
    except (TypeError, ValueError):
        return None


def build_records(df: "pd.DataFrame", colmap: dict[str, str]) -> list[dict]:
    records: list[dict] = []
    for _, row in df.iterrows():
        vnum = _to_str_stripped(row[colmap["vnum"]])
        if not vnum:
            continue
        rec = {
            "vnum": vnum,
            "name": _to_str_stripped(row[colmap["name"]]),
            "primary_volcano_type": _to_str_stripped(row[colmap["primary_volcano_type"]]),
            "country": _to_str_stripped(row[colmap["country"]]) if "country" in colmap else "",
            "region": _to_str_stripped(row[colmap["region"]]) if "region" in colmap else "",
            "subregion": _to_str_stripped(row[colmap["subregion"]]) if "subregion" in colmap else "",
            "latitude": _to_float_or_none(row[colmap["latitude"]]) if "latitude" in colmap else None,
            "longitude": _to_float_or_none(row[colmap["longitude"]]) if "longitude" in colmap else None,
            "elevation_m": _to_int_or_none(row[colmap["elevation_m"]]) if "elevation_m" in colmap else None,
            "type_verified": True,  # ingested from real GVP export
        }
        records.append(rec)
    # Deterministic order for readable diffs
    records.sort(key=lambda r: r["vnum"])
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest GVP Holocene xlsx into JSON snapshot.")
    parser.add_argument("--input", required=True, help="Path to GVP Excel export (.xlsx).")
    parser.add_argument("--output", required=True, help="Destination JSON snapshot path.")
    parser.add_argument("--snapshot-version", default="1.0.0", help="Semver version to record in _meta.")
    parser.add_argument("--source-url", default="https://volcano.si.edu/list_volcano_holocene.cfm")
    parser.add_argument("--sheet", default=0, help="Sheet name or index for pandas.read_excel.")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)

    if not in_path.exists():
        print(f"[FATAL] input file not found: {in_path}", file=sys.stderr)
        return 2

    try:
        df = pd.read_excel(in_path, sheet_name=args.sheet)
    except Exception as e:
        print(f"[FATAL] failed to read {in_path}: {e}", file=sys.stderr)
        return 2

    colmap = _resolve_columns(list(df.columns))
    print(f"[INFO] column mapping: {colmap}")

    records = build_records(df, colmap)
    print(f"[INFO] built {len(records)} records")

    payload = {
        "_meta": {
            "snapshot_source": "gvp_holocene_xlsx",
            "snapshot_date": date.today().isoformat(),
            "snapshot_version": args.snapshot_version,
            "gvp_source_url": args.source_url,
            "records_count": len(records),
            "notes": "Ingested via scripts/gvp_snapshot_ingest.py.",
        },
        "records": records,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")

    print(f"[OK] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
