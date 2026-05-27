from __future__ import annotations

import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests


DATA = Path("data")
DATA.mkdir(exist_ok=True)

BUCKET_BASE = "https://noaa-rrfs-pds.s3.amazonaws.com"
ROOT = "rrfs_a"

# Scan recent cycles.
LOOKBACK_HOURS = 72
MIN_USABLE_HOURS = int(os.getenv("DSS_REFS_MIN_USABLE_HOURS", "12"))
REQUIRED_PRODUCTS = {
    product.strip().lower()
    for product in os.getenv("DSS_REFS_REQUIRED_PRODUCTS", "mean,prob,avrg").split(",")
    if product.strip()
}

# We are intentionally broad here.
# Do not fail just because the exact expected products are not found.
GRIB_RE = re.compile(r"\.grib2$")
IDX_RE = re.compile(r"\.grib2\.idx$")

# Accept f01, f001, f060, etc.
FXX_RE = re.compile(r"\.f(\d{1,3})\.", re.IGNORECASE)

# Example:
# rrfs_a/refs.20260521/12/enspost/refs.t12z.avrg.f001.conus.grib2
PRODUCT_RE = re.compile(
    r"/enspost/refs\.t\d{2}z\.([a-zA-Z0-9_]+)\.f\d{1,3}\.",
    re.IGNORECASE,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_cycle_floor() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return now.replace(hour=(now.hour // 6) * 6)


def cycle_candidates() -> list[datetime]:
    base = latest_cycle_floor()
    return [base - timedelta(hours=h) for h in range(0, LOOKBACK_HOURS + 1, 6)]


def refs_prefix(cycle: datetime) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return f"{ROOT}/refs.{ymd}/{hh}/enspost/"


def refs_data_prefix(cycle: datetime) -> str:
    """Prefix for REFS GRIB/IDX data files, excluding graphics products."""
    hh = cycle.strftime("%H")
    return f"{refs_prefix(cycle)}refs.t{hh}z."


def s3_list(prefix: str) -> list[dict[str, Any]]:
    """
    Public S3 ListObjectsV2 via HTTPS.
    Correctly paginates through all objects.
    """
    objects: list[dict[str, Any]] = []
    token: str | None = None

    while True:
        params = {
            "list-type": "2",
            "prefix": prefix,
            "max-keys": "1000",
        }

        if token:
            params["continuation-token"] = token

        url = f"{BUCKET_BASE}/?{urllib.parse.urlencode(params)}"
        response = requests.get(url, timeout=90)
        response.raise_for_status()

        root = ET.fromstring(response.text)

        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        for item in root.findall(f"{ns}Contents"):
            key = item.findtext(f"{ns}Key")
            size = item.findtext(f"{ns}Size")
            last_modified = item.findtext(f"{ns}LastModified")

            if key:
                objects.append(
                    {
                        "key": key,
                        "size": int(size) if size and size.isdigit() else None,
                        "last_modified": last_modified,
                    }
                )

        is_truncated = (root.findtext(f"{ns}IsTruncated") or "").lower() == "true"
        token = root.findtext(f"{ns}NextContinuationToken")

        if not is_truncated or not token:
            break

    return objects


def parse_object(key: str) -> dict[str, Any]:
    fxx = None
    product = None

    fxx_match = FXX_RE.search(key)
    if fxx_match:
        fxx = int(fxx_match.group(1))

    product_match = PRODUCT_RE.search(key)
    if product_match:
        product = product_match.group(1).lower()

    return {
        "key": key,
        "is_grib2": bool(GRIB_RE.search(key)) and not bool(IDX_RE.search(key)),
        "is_idx": bool(IDX_RE.search(key)),
        "fxx": fxx,
        "product": product,
    }


def summarize_cycle(cycle: datetime, objects: list[dict[str, Any]]) -> dict[str, Any]:
    parsed = []

    for obj in objects:
        p = parse_object(obj["key"])
        parsed.append({**obj, **p})

    grib_objects = [p for p in parsed if p["is_grib2"]]
    idx_objects = [p for p in parsed if p["is_idx"]]

    hours_by_product: dict[str, set[int]] = defaultdict(set)
    grib_hours_by_product: dict[str, set[int]] = defaultdict(set)
    idx_hours_by_product: dict[str, set[int]] = defaultdict(set)

    all_hours = set()
    grib_hours = set()
    idx_hours = set()

    for p in parsed:
        fxx = p.get("fxx")
        product = p.get("product") or "unknown"

        if fxx is None:
            continue

        all_hours.add(fxx)
        hours_by_product[product].add(fxx)

        if p["is_grib2"]:
            grib_hours.add(fxx)
            grib_hours_by_product[product].add(fxx)

        if p["is_idx"]:
            idx_hours.add(fxx)
            idx_hours_by_product[product].add(fxx)

    products = sorted(set(p.get("product") or "unknown" for p in parsed))

    return {
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "cycle_label": cycle.strftime("%Y-%m-%d %HZ"),
        "prefix": refs_prefix(cycle),
        "status": "ok",
        "object_count": len(objects),
        "grib_count": len(grib_objects),
        "idx_count": len(idx_objects),
        "products": products,
        "all_hours": sorted(all_hours),
        "grib_hours": sorted(grib_hours),
        "idx_hours": sorted(idx_hours),
        "hours_by_product": {
            product: sorted(hours)
            for product, hours in sorted(hours_by_product.items())
        },
        "grib_hours_by_product": {
            product: sorted(hours)
            for product, hours in sorted(grib_hours_by_product.items())
        },
        "idx_hours_by_product": {
            product: sorted(hours)
            for product, hours in sorted(idx_hours_by_product.items())
        },
        "sample_keys": [p["key"] for p in parsed[:50]],
        "parsed_objects": parsed,
    }


def choose_best_cycle(cycles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Choose the newest usable REFS cycle.

    Older logic scored "most complete" first, which could keep selecting an
    older 60-hour cycle while a newer cycle was already available. Operations
    users care about the newest run; if a cycle has the minimum usable lead
    time and the required products, use it.
    """
    usable = []
    for c in cycles:
        idx_hours = set(c.get("idx_hours", []))
        grib_hours = set(c.get("grib_hours", []))
        products = {str(p).lower() for p in c.get("products", [])}
        if not idx_hours or not grib_hours:
            continue
        if max(idx_hours) < MIN_USABLE_HOURS:
            continue
        if REQUIRED_PRODUCTS and not REQUIRED_PRODUCTS.issubset(products):
            continue
        usable.append(c)

    if not usable:
        return None

    def score(c: dict[str, Any]) -> tuple[datetime, int, int, int]:
        idx_hours = c.get("idx_hours", [])
        grib_hours = c.get("grib_hours", [])
        try:
            cycle_dt = datetime.fromisoformat(str(c.get("cycle_utc")).replace("Z", "+00:00"))
        except ValueError:
            cycle_dt = datetime.min.replace(tzinfo=timezone.utc)
        return (
            cycle_dt,
            max(idx_hours) if idx_hours else -1,
            len(idx_hours),
            len(grib_hours),
        )

    return max(usable, key=score)


def compact_selected_cycle(selected: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep selected-cycle diagnostics small enough to commit."""
    if not selected:
        return None

    compact = {
        "cycle_utc": selected.get("cycle_utc"),
        "cycle_label": selected.get("cycle_label"),
        "prefix": selected.get("prefix"),
        "status": selected.get("status"),
        "object_count": selected.get("object_count"),
        "grib_count": selected.get("grib_count"),
        "idx_count": selected.get("idx_count"),
        "products": selected.get("products", []),
        "all_hours": selected.get("all_hours", []),
        "grib_hours": selected.get("grib_hours", []),
        "idx_hours": selected.get("idx_hours", []),
        "hours_by_product": selected.get("hours_by_product", {}),
        "grib_hours_by_product": selected.get("grib_hours_by_product", {}),
        "idx_hours_by_product": selected.get("idx_hours_by_product", {}),
        "sample_keys": [],
        "parsed_objects": [],
    }

    sample_keys = []
    parsed_objects = []

    for obj in selected.get("parsed_objects", []) or []:
        if not isinstance(obj, dict):
            continue

        key = str(obj.get("key") or "")
        fxx = obj.get("fxx")
        if not key or fxx is None:
            continue
        if not (obj.get("is_grib2") or obj.get("is_idx")):
            continue
        if ".conus.grib2" not in key.lower():
            continue
        if not (1 <= int(fxx) <= 60):
            continue

        parsed_objects.append(
            {
                "key": key,
                "size": obj.get("size"),
                "last_modified": obj.get("last_modified"),
                "is_grib2": bool(obj.get("is_grib2")),
                "is_idx": bool(obj.get("is_idx")),
                "fxx": int(fxx),
                "product": obj.get("product"),
            }
        )

        if len(sample_keys) < 50:
            sample_keys.append(key)

    compact["sample_keys"] = sample_keys
    compact["parsed_objects"] = parsed_objects
    return compact


def write_text_report(cycles: list[dict[str, Any]], selected: dict[str, Any] | None) -> str:
    lines = []
    lines.append("RRFS / REFS AWS Inventory Scan")
    lines.append(f"Generated: {utc_now_iso()}")
    lines.append("")

    if selected:
        lines.append("Selected cycle:")
        lines.append(f"  {selected['cycle_label']}")
        lines.append(f"  prefix: {selected['prefix']}")
        lines.append(f"  products: {', '.join(selected.get('products', []))}")
        lines.append(f"  grib_count: {selected.get('grib_count')}")
        lines.append(f"  idx_count: {selected.get('idx_count')}")
        lines.append(f"  idx_hours: {selected.get('idx_hours')}")
        lines.append("")
    else:
        lines.append("Selected cycle: NONE")
        lines.append("")

    lines.append("=" * 100)
    lines.append("Cycle summaries")
    lines.append("=" * 100)

    for c in cycles:
        lines.append("")
        lines.append(f"{c.get('cycle_label')}")
        lines.append(f"  status: {c.get('status')}")
        lines.append(f"  prefix: {c.get('prefix')}")
        lines.append(f"  object_count: {c.get('object_count')}")
        lines.append(f"  grib_count: {c.get('grib_count')}")
        lines.append(f"  idx_count: {c.get('idx_count')}")
        lines.append(f"  products: {', '.join(c.get('products', []))}")
        lines.append(f"  grib_hours: {c.get('grib_hours')}")
        lines.append(f"  idx_hours: {c.get('idx_hours')}")

        lines.append("  hours_by_product:")
        for product, hours in c.get("hours_by_product", {}).items():
            lines.append(f"    {product}: {hours}")

        sample_keys = c.get("sample_keys", [])
        if sample_keys:
            lines.append("  sample_keys:")
            for key in sample_keys[:15]:
                lines.append(f"    {key}")

    return "\n".join(lines) + "\n"


def main() -> None:
    print("Scanning RRFS / REFS inventory from AWS S3")

    cycle_summaries = []

    for cycle in cycle_candidates():
        label = cycle.strftime("%Y-%m-%d %HZ")
        prefix = refs_prefix(cycle)
        data_prefix = refs_data_prefix(cycle)

        print(f"Checking REFS cycle {label}")

        try:
            objects = s3_list(data_prefix)
            summary = summarize_cycle(cycle, objects)

            print(
                "  "
                f"status=ok "
                f"objects={summary['object_count']} "
                f"grib={summary['grib_count']} "
                f"idx={summary['idx_count']} "
                f"products={summary['products']} "
                f"idx_hours={summary['idx_hours'][:5]}...{summary['idx_hours'][-5:] if summary['idx_hours'] else []}"
            )

        except Exception as exc:
            summary = {
                "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
                "cycle_label": label,
                "prefix": prefix,
                "status": "error",
                "message": str(exc),
                "object_count": 0,
                "grib_count": 0,
                "idx_count": 0,
                "products": [],
                "all_hours": [],
                "grib_hours": [],
                "idx_hours": [],
                "hours_by_product": {},
                "grib_hours_by_product": {},
                "idx_hours_by_product": {},
                "sample_keys": [],
                "parsed_objects": [],
            }

            print(f"  status=error message={exc}")

        cycle_summaries.append(summary)

    selected = choose_best_cycle(cycle_summaries)

    inventory_payload = {
        "generated_utc": utc_now_iso(),
        "bucket": BUCKET_BASE,
        "lookback_hours": LOOKBACK_HOURS,
        "selected_cycle": selected,
        "cycles": cycle_summaries,
    }

    selected_payload = {
        "generated_utc": utc_now_iso(),
        "bucket": BUCKET_BASE,
        "selected_cycle": compact_selected_cycle(selected),
    }

    txt = write_text_report(cycle_summaries, selected)

    (DATA / "rrfs_refs_inventory.json").write_text(json.dumps(inventory_payload, indent=2))
    (DATA / "rrfs_refs_selected_cycle.json").write_text(json.dumps(selected_payload, indent=2))
    (DATA / "rrfs_refs_inventory.txt").write_text(txt)

    print("")
    print("Wrote data/rrfs_refs_inventory.json")
    print("Wrote data/rrfs_refs_selected_cycle.json")
    print("Wrote data/rrfs_refs_inventory.txt")

    if selected:
        print("")
        print(f"Selected cycle: {selected['cycle_label']}")
        print(f"IDX hours: {selected.get('idx_hours')}")
        print(f"Products: {selected.get('products')}")
    else:
        print("")
        print("No REFS cycle with IDX files found.")

    # Important: do not fail the workflow.
    # This script is diagnostic and should never block the builder.
    return


if __name__ == "__main__":
    main()
