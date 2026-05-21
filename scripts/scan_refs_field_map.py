from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DATA = Path("data")
DATA.mkdir(exist_ok=True)

BUCKET_BASE = "https://noaa-rrfs-pds.s3.amazonaws.com"

SELECTED_CYCLE_PATH = DATA / "rrfs_refs_selected_cycle.json"
FIELD_MAP_TXT = DATA / "refs_field_map.txt"
FIELD_MAP_JSON = DATA / "refs_field_map_summary.json"

# Keep this small and targeted.
TARGET_FXX = [1, 2, 3, 6, 12, 18, 24, 30, 36, 42, 48, 54, 60]

TARGET_PRODUCTS = {
    "mean",
    "avrg",
    "prob",
}

# Broad keyword search. This is intentionally generous so we can see the real IDX wording.
KEYWORDS = [
    # Wind
    "GUST",
    "WIND",
    "UGRD",
    "VGRD",

    # Temperature / wet bulb
    "TMP",
    "TMAX",
    "TMIN",
    "MAXT",
    "MINT",
    "DPT",
    "DEW",
    "RH",
    "WETB",
    "TWET",
    "WBT",

    # Precip / rain
    "APCP",
    "PRECIP",
    "PRATE",
    "RAIN",
    "QPF",

    # Snow
    "ASNOW",
    "SNOD",
    "SNOW",
    "WEASD",

    # Freezing rain
    "FZR",
    "FZRA",
    "CFRZR",
    "ICE",

    # Visibility
    "VIS",
    "FOG",

    # Lightning / thunder / convection
    "LTNG",
    "LIGHTNING",
    "TSTM",
    "THUNDER",
    "CAPE",
    "CIN",
    "REFC",
    "REFD",
    "MAXREF",
    "HAIL",
]

FXX_RE = re.compile(r"\.f(\d{1,3})\.", re.IGNORECASE)
PRODUCT_RE = re.compile(r"refs\.t\d{2}z\.([a-zA-Z0-9_]+)\.f\d{1,3}\.", re.IGNORECASE)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_selected_cycle() -> dict[str, Any]:
    if not SELECTED_CYCLE_PATH.exists():
        raise FileNotFoundError(
            "Missing data/rrfs_refs_selected_cycle.json. "
            "Run scripts/scan_rrfs_refs_inventory.py first."
        )

    payload = json.loads(SELECTED_CYCLE_PATH.read_text())

    # Current scanner may store the selected cycle under one of these keys.
    selected = payload.get("selected_cycle") or payload.get("cycle") or payload

    if not isinstance(selected, dict):
        raise RuntimeError("Could not parse selected cycle from rrfs_refs_selected_cycle.json")

    return selected


def parse_fxx_from_key(key: str) -> int | None:
    match = FXX_RE.search(key)
    if not match:
        return None
    return int(match.group(1))


def parse_product_from_key(key: str) -> str | None:
    match = PRODUCT_RE.search(key)
    if not match:
        return None
    return match.group(1).lower()


def idx_url_from_key(key: str) -> str:
    return f"{BUCKET_BASE}/{urllib.parse.quote(key, safe='/')}"


def fetch_text(url: str, timeout: int = 90) -> str:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def get_idx_keys_from_selected(selected: dict[str, Any]) -> list[str]:
    keys: list[str] = []

    # Selected cycle from our scanner can contain parsed_objects.
    for obj in selected.get("parsed_objects", []) or []:
        if not isinstance(obj, dict):
            continue
        key = obj.get("key")
        if isinstance(key, str) and key.endswith(".grib2.idx"):
            keys.append(key)

    # Or selected can contain sample/full keys.
    for field in ["keys", "files", "objects", "idx_keys"]:
        for item in selected.get(field, []) or []:
            if isinstance(item, str):
                key = item
            elif isinstance(item, dict):
                key = item.get("key") or item.get("name")
            else:
                continue

            if isinstance(key, str) and key.endswith(".grib2.idx"):
                keys.append(key)

    # Or selected may have product/hour dictionaries. This fallback uses the prefix.
    if not keys:
        prefix = selected.get("prefix")
        if not prefix:
            raise RuntimeError("No idx keys found and selected cycle has no prefix.")

        # If selected has idx_hours and products, construct possible keys from naming convention.
        products = selected.get("products") or ["mean", "avrg", "prob"]
        idx_hours = selected.get("idx_hours") or TARGET_FXX

        for product in products:
            if str(product).lower() not in TARGET_PRODUCTS:
                continue

            for fxx in idx_hours:
                for width in [3, 2]:
                    # REFS files observed in AWS are usually:
                    # refs.t18z.mean.f001.conus.grib2.idx
                    # refs.t18z.prob.f001.conus.grib2.idx
                    cycle_hour = selected.get("cycle_label", "")
                    hh = None

                    # Best source: prefix path contains /HH/
                    m = re.search(r"/(\d{2})/enspost/?$", prefix)
                    if m:
                        hh = m.group(1)

                    if hh is None:
                        m = re.search(r"(\d{2})Z", cycle_hour)
                        if m:
                            hh = m.group(1)

                    if hh is None:
                        continue

                    keys.append(
                        f"{prefix}refs.t{hh}z.{product}.f{int(fxx):0{width}d}.conus.grib2.idx"
                    )

    # Deduplicate while preserving order.
    seen = set()
    unique = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            unique.append(key)

    return unique


def is_target_idx_key(key: str) -> bool:
    fxx = parse_fxx_from_key(key)
    product = parse_product_from_key(key)

    if fxx not in TARGET_FXX:
        return False

    if product not in TARGET_PRODUCTS:
        return False

    if ".conus." not in key.lower():
        return False

    return True


def line_matches(line: str) -> bool:
    upper = line.upper()
    return any(keyword in upper for keyword in KEYWORDS)


def classify_line(line: str) -> list[str]:
    upper = line.upper()
    categories = []

    if any(k in upper for k in ["GUST", "WIND", "UGRD", "VGRD"]):
        categories.append("wind")

    if any(k in upper for k in ["TMP", "TMAX", "TMIN", "MAXT", "MINT", "WETB", "TWET", "WBT", "DPT", "DEW"]):
        categories.append("temperature_wetbulb")

    if any(k in upper for k in ["APCP", "PRECIP", "PRATE", "RAIN", "QPF"]):
        categories.append("rain_precip")

    if any(k in upper for k in ["ASNOW", "SNOD", "SNOW", "WEASD"]):
        categories.append("snow")

    if any(k in upper for k in ["FZR", "FZRA", "CFRZR", "ICE"]):
        categories.append("freezing_rain")

    if any(k in upper for k in ["VIS", "FOG"]):
        categories.append("visibility")

    if any(k in upper for k in ["LTNG", "LIGHTNING", "TSTM", "THUNDER", "CAPE", "CIN", "REFC", "REFD", "HAIL"]):
        categories.append("lightning_convection")

    return categories or ["other"]


def main() -> None:
    print("Building compact REFS field map")

    selected = load_selected_cycle()
    idx_keys = get_idx_keys_from_selected(selected)
    target_keys = [key for key in idx_keys if is_target_idx_key(key)]

    if not target_keys:
        raise RuntimeError(
            "No target REFS IDX keys found. "
            "Check data/rrfs_refs_selected_cycle.json and scanner output."
        )

    print(f"Selected cycle: {selected.get('cycle_label') or selected.get('cycle_utc')}")
    print(f"Total IDX keys found: {len(idx_keys)}")
    print(f"Target IDX keys used: {len(target_keys)}")

    text_lines = []
    summary: dict[str, Any] = {
        "generated_utc": utc_now_iso(),
        "selected_cycle": {
            "cycle_utc": selected.get("cycle_utc"),
            "cycle_label": selected.get("cycle_label"),
            "prefix": selected.get("prefix"),
            "products": selected.get("products"),
            "idx_hours": selected.get("idx_hours"),
        },
        "target_fxx": TARGET_FXX,
        "target_products": sorted(TARGET_PRODUCTS),
        "files": [],
        "category_counts": {},
        "errors": [],
    }

    category_counts: dict[str, int] = {}

    text_lines.append("REFS Field Map")
    text_lines.append(f"Generated: {summary['generated_utc']}")
    text_lines.append("")
    text_lines.append(f"Selected cycle: {summary['selected_cycle']}")
    text_lines.append(f"Target products: {sorted(TARGET_PRODUCTS)}")
    text_lines.append(f"Target fxx: {TARGET_FXX}")
    text_lines.append("")
    text_lines.append("=" * 120)

    for key in sorted(target_keys):
        fxx = parse_fxx_from_key(key)
        product = parse_product_from_key(key)
        url = idx_url_from_key(key)

        print(f"Reading {product} f{fxx:03d}")

        file_record = {
            "key": key,
            "url": url,
            "product": product,
            "fxx": fxx,
            "matched_lines": [],
            "error": None,
        }

        text_lines.append("")
        text_lines.append("=" * 120)
        text_lines.append(f"PRODUCT={product} FXX={fxx:03d}")
        text_lines.append(f"KEY={key}")
        text_lines.append(f"URL={url}")
        text_lines.append("-" * 120)

        try:
            idx_text = fetch_text(url)
            lines = idx_text.splitlines()

            for line in lines:
                if not line_matches(line):
                    continue

                categories = classify_line(line)

                for cat in categories:
                    category_counts[cat] = category_counts.get(cat, 0) + 1

                file_record["matched_lines"].append(
                    {
                        "line": line,
                        "categories": categories,
                    }
                )

                text_lines.append(f"[{','.join(categories)}] {line}")

            if not file_record["matched_lines"]:
                text_lines.append("NO TARGET LINES MATCHED")

        except Exception as exc:
            msg = str(exc)
            file_record["error"] = msg
            summary["errors"].append({"key": key, "error": msg})
            text_lines.append(f"ERROR: {msg}")

        summary["files"].append(file_record)

    summary["category_counts"] = category_counts

    FIELD_MAP_TXT.write_text("\n".join(text_lines) + "\n")
    FIELD_MAP_JSON.write_text(json.dumps(summary, indent=2))

    print("")
    print(f"Wrote {FIELD_MAP_TXT}")
    print(f"Wrote {FIELD_MAP_JSON}")
    print("Category counts:")
    for cat, count in sorted(category_counts.items()):
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
