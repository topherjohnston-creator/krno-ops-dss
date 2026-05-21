from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests


DATA = Path("data")
DATA.mkdir(exist_ok=True)

BUCKET_BASE = "https://noaa-rrfs-pds.s3.amazonaws.com"

# REFS path confirmed:
# rrfs_a/refs.YYYYMMDD/HH/enspost/
ROOT_TEMPLATE = "rrfs_a/refs.{ymd}/{hh}/enspost/"

# Check recent cycles first.
# RRFS/REFS can lag, so scan back enough to find a usable completed cycle.
CYCLE_HOURS_BACK = 48

# We only need enough inventory to determine whether a cycle is usable.
# But keep this high enough for all enspost files.
MAX_KEYS_PER_PAGE = 1000
MAX_PAGES_PER_CYCLE = 80

# What we want to inspect.
TARGET_HOURS = list(range(1, 61))

# Common REFS products we care about.
KEY_PATTERNS = {
    "mean": re.compile(r"/refs\.t\d{2}z\.avrg\.f(\d{2})\.conus\.grib2$"),
    "mean_idx": re.compile(r"/refs\.t\d{2}z\.avrg\.f(\d{2})\.conus\.grib2\.idx$"),
    "prob": re.compile(r"/refs\.t\d{2}z\..*prob.*\.f(\d{2})\.conus\.grib2$", re.IGNORECASE),
    "prob_idx": re.compile(r"/refs\.t\d{2}z\..*prob.*\.f(\d{2})\.conus\.grib2\.idx$", re.IGNORECASE),
    "any_conus_grib": re.compile(r"/refs\.t\d{2}z\..*\.f(\d{2})\.conus\.grib2$", re.IGNORECASE),
    "any_conus_idx": re.compile(r"/refs\.t\d{2}z\..*\.f(\d{2})\.conus\.grib2\.idx$", re.IGNORECASE),
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_cycle_candidates() -> list[datetime]:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    # RRFS cycles are 00/06/12/18Z.
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)

    cycles: list[datetime] = []
    for hours_back in range(0, CYCLE_HOURS_BACK + 1, 6):
        cycles.append(cycle - timedelta(hours=hours_back))

    return cycles


def s3_list_url(prefix: str, continuation_token: str | None = None) -> str:
    params = {
        "list-type": "2",
        "prefix": prefix,
        "max-keys": str(MAX_KEYS_PER_PAGE),
    }

    if continuation_token:
        params["continuation-token"] = continuation_token

    return f"{BUCKET_BASE}/?{urllib.parse.urlencode(params)}"


def parse_s3_xml(xml_text: str) -> tuple[list[dict[str, Any]], str | None, bool]:
    root = ET.fromstring(xml_text)

    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    contents = []
    for item in root.findall(f"{ns}Contents"):
        key = item.findtext(f"{ns}Key") or ""
        size_text = item.findtext(f"{ns}Size") or "0"
        modified = item.findtext(f"{ns}LastModified") or ""

        try:
            size = int(size_text)
        except ValueError:
            size = 0

        contents.append(
            {
                "key": key,
                "size": size,
                "last_modified": modified,
            }
        )

    token = root.findtext(f"{ns}NextContinuationToken")
    truncated_text = root.findtext(f"{ns}IsTruncated") or "false"
    is_truncated = truncated_text.lower() == "true"

    return contents, token, is_truncated


def list_prefix(prefix: str) -> list[dict[str, Any]]:
    all_items: list[dict[str, Any]] = []
    token: str | None = None

    for page in range(1, MAX_PAGES_PER_CYCLE + 1):
        url = s3_list_url(prefix, token)

        response = requests.get(url, timeout=45)
        response.raise_for_status()

        items, token, is_truncated = parse_s3_xml(response.text)
        all_items.extend(items)

        if not is_truncated or not token:
            break

        time.sleep(0.2)

    return all_items


def fxx_from_key(key: str) -> int | None:
    match = re.search(r"\.f(\d{2})\.", key)
    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def classify_key(key: str) -> list[str]:
    labels = []

    for label, pattern in KEY_PATTERNS.items():
        if pattern.search("/" + key):
            labels.append(label)

    if "/graphics/" in key:
        labels.append("graphics")

    if key.endswith(".grib2"):
        labels.append("grib2")

    if key.endswith(".idx"):
        labels.append("idx")

    if ".conus." in key:
        labels.append("conus")

    if ".ak." in key:
        labels.append("ak")

    if ".hi." in key:
        labels.append("hi")

    if ".pr." in key:
        labels.append("pr")

    return labels


def summarize_cycle(cycle: datetime) -> dict[str, Any]:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    prefix = ROOT_TEMPLATE.format(ymd=ymd, hh=hh)

    cycle_result: dict[str, Any] = {
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "prefix": prefix,
        "status": "unknown",
        "usable": False,
        "total_keys": 0,
        "conus_grib_count": 0,
        "conus_idx_count": 0,
        "mean_grib_count": 0,
        "mean_idx_count": 0,
        "prob_grib_count": 0,
        "prob_idx_count": 0,
        "forecast_hours_detected": [],
        "mean_hours_detected": [],
        "prob_hours_detected": [],
        "sample_conus_grib_keys": [],
        "sample_conus_idx_keys": [],
        "sample_mean_keys": [],
        "sample_prob_keys": [],
        "all_matching_keys": [],
        "error": None,
    }

    try:
        items = list_prefix(prefix)
    except Exception as exc:
        cycle_result["status"] = "error"
        cycle_result["error"] = str(exc)
        return cycle_result

    cycle_result["total_keys"] = len(items)

    conus_grib_hours = set()
    conus_idx_hours = set()
    mean_hours = set()
    prob_hours = set()

    matching_keys = []

    for item in items:
        key = item["key"]
        labels = classify_key(key)
        fxx = fxx_from_key(key)

        if "graphics" in labels:
            continue

        if "conus" not in labels:
            continue

        record = {
            "key": key,
            "size": item["size"],
            "last_modified": item["last_modified"],
            "fxx": fxx,
            "labels": labels,
        }

        if "grib2" in labels:
            cycle_result["conus_grib_count"] += 1
            if fxx is not None:
                conus_grib_hours.add(fxx)
            if len(cycle_result["sample_conus_grib_keys"]) < 20:
                cycle_result["sample_conus_grib_keys"].append(record)

        if "idx" in labels:
            cycle_result["conus_idx_count"] += 1
            if fxx is not None:
                conus_idx_hours.add(fxx)
            if len(cycle_result["sample_conus_idx_keys"]) < 20:
                cycle_result["sample_conus_idx_keys"].append(record)

        if "mean" in labels:
            cycle_result["mean_grib_count"] += 1
            if fxx is not None:
                mean_hours.add(fxx)
            if len(cycle_result["sample_mean_keys"]) < 20:
                cycle_result["sample_mean_keys"].append(record)

        if "mean_idx" in labels:
            cycle_result["mean_idx_count"] += 1

        if "prob" in labels:
            cycle_result["prob_grib_count"] += 1
            if fxx is not None:
                prob_hours.add(fxx)
            if len(cycle_result["sample_prob_keys"]) < 20:
                cycle_result["sample_prob_keys"].append(record)

        if "prob_idx" in labels:
            cycle_result["prob_idx_count"] += 1

        if "grib2" in labels or "idx" in labels:
            matching_keys.append(record)

    cycle_result["forecast_hours_detected"] = sorted(conus_grib_hours | conus_idx_hours)
    cycle_result["mean_hours_detected"] = sorted(mean_hours)
    cycle_result["prob_hours_detected"] = sorted(prob_hours)

    # Keep the full matching key list small enough for GitHub.
    cycle_result["all_matching_keys"] = matching_keys[:300]

    has_enough_mean = len([h for h in TARGET_HOURS if h in mean_hours]) >= 48
    has_any_prob = len(prob_hours) > 0
    has_conus_grib = cycle_result["conus_grib_count"] > 0

    cycle_result["usable"] = bool(has_conus_grib and has_enough_mean)
    cycle_result["status"] = "ok"

    cycle_result["missing_mean_hours_1_60"] = [h for h in TARGET_HOURS if h not in mean_hours]
    cycle_result["has_probability_files"] = has_any_prob

    return cycle_result


def write_txt_report(payload: dict[str, Any]) -> None:
    lines = []
    lines.append("RRFS / REFS Inventory Scan")
    lines.append(f"Generated: {payload['generated_utc']}")
    lines.append(f"Bucket: {BUCKET_BASE}")
    lines.append("")
    lines.append("Selected cycle:")
    selected = payload.get("selected_cycle")
    if selected:
        lines.append(f"  cycle_utc: {selected.get('cycle_utc')}")
        lines.append(f"  prefix: {selected.get('prefix')}")
        lines.append(f"  usable: {selected.get('usable')}")
        lines.append(f"  conus_grib_count: {selected.get('conus_grib_count')}")
        lines.append(f"  conus_idx_count: {selected.get('conus_idx_count')}")
        lines.append(f"  mean_grib_count: {selected.get('mean_grib_count')}")
        lines.append(f"  mean_idx_count: {selected.get('mean_idx_count')}")
        lines.append(f"  prob_grib_count: {selected.get('prob_grib_count')}")
        lines.append(f"  prob_idx_count: {selected.get('prob_idx_count')}")
        lines.append(f"  mean_hours_detected: {selected.get('mean_hours_detected')}")
        lines.append(f"  prob_hours_detected: {selected.get('prob_hours_detected')}")
        lines.append(f"  missing_mean_hours_1_60: {selected.get('missing_mean_hours_1_60')}")
    else:
        lines.append("  None found")

    lines.append("")
    lines.append("=" * 100)
    lines.append("All scanned cycles")
    lines.append("=" * 100)

    for cycle in payload.get("cycles", []):
        lines.append("")
        lines.append(f"cycle_utc: {cycle.get('cycle_utc')}")
        lines.append(f"prefix: {cycle.get('prefix')}")
        lines.append(f"status: {cycle.get('status')}")
        lines.append(f"usable: {cycle.get('usable')}")
        lines.append(f"total_keys: {cycle.get('total_keys')}")
        lines.append(f"conus_grib_count: {cycle.get('conus_grib_count')}")
        lines.append(f"conus_idx_count: {cycle.get('conus_idx_count')}")
        lines.append(f"mean_grib_count: {cycle.get('mean_grib_count')}")
        lines.append(f"mean_idx_count: {cycle.get('mean_idx_count')}")
        lines.append(f"prob_grib_count: {cycle.get('prob_grib_count')}")
        lines.append(f"prob_idx_count: {cycle.get('prob_idx_count')}")
        lines.append(f"forecast_hours_detected: {cycle.get('forecast_hours_detected')}")
        lines.append(f"mean_hours_detected: {cycle.get('mean_hours_detected')}")
        lines.append(f"prob_hours_detected: {cycle.get('prob_hours_detected')}")
        lines.append(f"missing_mean_hours_1_60: {cycle.get('missing_mean_hours_1_60')}")
        if cycle.get("error"):
            lines.append(f"error: {cycle.get('error')}")

        lines.append("")
        lines.append("Sample CONUS GRIB keys:")
        for item in cycle.get("sample_conus_grib_keys", [])[:10]:
            lines.append(f"  {item['key']}")

        lines.append("Sample CONUS IDX keys:")
        for item in cycle.get("sample_conus_idx_keys", [])[:10]:
            lines.append(f"  {item['key']}")

        lines.append("Sample MEAN keys:")
        for item in cycle.get("sample_mean_keys", [])[:10]:
            lines.append(f"  {item['key']}")

        lines.append("Sample PROB keys:")
        for item in cycle.get("sample_prob_keys", [])[:10]:
            lines.append(f"  {item['key']}")

    (DATA / "rrfs_refs_inventory.txt").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    print("Scanning RRFS / REFS inventory from AWS S3")

    cycles = []
    selected = None

    for cycle in latest_cycle_candidates():
        print(f"Checking REFS cycle {cycle:%Y-%m-%d %HZ}")
        result = summarize_cycle(cycle)
        cycles.append(result)

        print(
            f"  status={result.get('status')} "
            f"usable={result.get('usable')} "
            f"conus_grib={result.get('conus_grib_count')} "
            f"mean_hours={len(result.get('mean_hours_detected', []))} "
            f"prob_hours={len(result.get('prob_hours_detected', []))}"
        )

        if selected is None and result.get("usable"):
            selected = result

    payload = {
        "generated_utc": utc_now_iso(),
        "bucket": BUCKET_BASE,
        "root_template": ROOT_TEMPLATE,
        "selected_cycle": selected,
        "cycles": cycles,
    }

    (DATA / "rrfs_refs_inventory.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (DATA / "rrfs_refs_selected_cycle.json").write_text(json.dumps(selected or {}, indent=2), encoding="utf-8")
    write_txt_report(payload)

    print("")
    print("Wrote data/rrfs_refs_inventory.json")
    print("Wrote data/rrfs_refs_selected_cycle.json")
    print("Wrote data/rrfs_refs_inventory.txt")

    if selected:
        print("")
        print("Selected usable REFS cycle:")
        print(f"  {selected['cycle_utc']}")
        print(f"  {selected['prefix']}")
    else:
        print("")
        print("No usable REFS cycle found.")
        sys.exit(1)


if __name__ == "__main__":
    main()
