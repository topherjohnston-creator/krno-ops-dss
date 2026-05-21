from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


DATA = Path("data")
DATA.mkdir(exist_ok=True)

BUCKET = "noaa-rrfs-pds"
BASE_URL = f"https://{BUCKET}.s3.amazonaws.com"

OUT_JSON = DATA / "rrfs_refs_inventory.json"
OUT_TXT = DATA / "rrfs_refs_inventory.txt"
OUT_SELECTED = DATA / "rrfs_refs_selected_cycle.json"

LOOKBACK_CYCLES = 16
TIMEOUT = 60


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_cycle_floor() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return now.replace(hour=(now.hour // 6) * 6)


def cycle_candidates() -> list[datetime]:
    base = latest_cycle_floor()
    return [base - timedelta(hours=6 * i) for i in range(LOOKBACK_CYCLES)]


def refs_prefix(cycle: datetime) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return f"rrfs_a/refs.{ymd}/{hh}/enspost/"


def s3_list_all(prefix: str, max_keys_total: int = 10000) -> list[dict[str, Any]]:
    keys: list[dict[str, Any]] = []
    continuation_token = None

    while len(keys) < max_keys_total:
        params = {
            "list-type": "2",
            "prefix": prefix,
            "max-keys": "1000",
        }

        if continuation_token:
            params["continuation-token"] = continuation_token

        response = requests.get(BASE_URL, params=params, timeout=TIMEOUT)
        response.raise_for_status()

        root = ET.fromstring(response.text)
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

        for contents in root.findall("s3:Contents", ns):
            key_el = contents.find("s3:Key", ns)
            size_el = contents.find("s3:Size", ns)
            modified_el = contents.find("s3:LastModified", ns)

            if key_el is not None and key_el.text:
                keys.append(
                    {
                        "key": key_el.text,
                        "size": int(size_el.text) if size_el is not None and size_el.text else None,
                        "last_modified": modified_el.text if modified_el is not None else None,
                    }
                )

        truncated_el = root.find("s3:IsTruncated", ns)
        is_truncated = truncated_el is not None and truncated_el.text == "true"

        token_el = root.find("s3:NextContinuationToken", ns)
        continuation_token = token_el.text if token_el is not None else None

        if not is_truncated or not continuation_token:
            break

    return keys


def extract_fxx(key: str) -> int | None:
    match = re.search(r"\.f(\d{2,3})\.", key)
    if match:
        return int(match.group(1))
    return None


def extract_member(key: str) -> str | None:
    patterns = [
        r"\.mem(\d{2,3})\.",
        r"\.m(\d{2,3})\.",
        r"/mem(\d{2,3})/",
        r"/m(\d{2,3})/",
    ]

    for pattern in patterns:
        match = re.search(pattern, key.lower())
        if match:
            return match.group(1)

    return None


def classify_key(item: dict[str, Any]) -> dict[str, Any]:
    key = item["key"]
    lower = key.lower()

    hazard_terms = {
        "wind": ["gust", "wind", "ugrd", "vgrd"],
        "temperature": ["tmp", "tmax", "tmin", "temp"],
        "rain": ["apcp", "qpf", "rain"],
        "snow": ["asnow", "snow"],
        "freezing_rain": ["fzra", "frzr"],
        "visibility": ["vis"],
        "lightning": ["ltng", "lightning"],
        "hail": ["hail"],
        "wet_bulb": ["wetbulb", "twet", "wetb"],
        "probability": ["prob"],
        "mean": ["mean", "avg"],
    }

    matches = []
    for category, terms in hazard_terms.items():
        if any(term in lower for term in terms):
            matches.append(category)

    return {
        **item,
        "fxx": extract_fxx(key),
        "member": extract_member(key),
        "is_idx": lower.endswith(".idx"),
        "is_grib2": ".grib2" in lower,
        "matches": matches,
    }


def scan_cycle(cycle: datetime) -> dict[str, Any]:
    prefix = refs_prefix(cycle)
    print(f"Scanning {prefix}")

    try:
        keys = s3_list_all(prefix)
        classified = [classify_key(k) for k in keys]

        idx_keys = [k for k in classified if k["is_idx"]]
        grib_keys = [k for k in classified if k["is_grib2"] and not k["is_idx"]]
        fxx_values = sorted({k["fxx"] for k in classified if k["fxx"] is not None})
        members = sorted({k["member"] for k in classified if k["member"] is not None})

        category_counts = {}
        for k in classified:
            for match in k["matches"]:
                category_counts[match] = category_counts.get(match, 0) + 1

        return {
            "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
            "prefix": prefix,
            "status": "ok",
            "usable": len(keys) > 0,
            "total_keys": len(keys),
            "grib_key_count": len(grib_keys),
            "idx_key_count": len(idx_keys),
            "forecast_hours_detected": fxx_values,
            "members_detected": members,
            "category_counts": category_counts,
            "sample_keys": classified[:100],
            "sample_grib_keys": grib_keys[:100],
            "sample_idx_keys": idx_keys[:100],
        }

    except Exception as exc:
        return {
            "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
            "prefix": prefix,
            "status": "error",
            "usable": False,
            "error": str(exc),
            "total_keys": 0,
            "grib_key_count": 0,
            "idx_key_count": 0,
            "forecast_hours_detected": [],
            "members_detected": [],
            "category_counts": {},
            "sample_keys": [],
            "sample_grib_keys": [],
            "sample_idx_keys": [],
        }


def choose_selected(cycles: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [c for c in cycles if c.get("usable")]
    if not usable:
        return None

    return max(
        usable,
        key=lambda c: (
            c.get("grib_key_count", 0),
            c.get("idx_key_count", 0),
            len(c.get("forecast_hours_detected", [])),
            c.get("cycle_utc", ""),
        ),
    )


def write_txt(payload: dict[str, Any]) -> None:
    lines = []
    lines.append("RRFS / REFS Targeted Inventory Scan")
    lines.append(f"Generated UTC: {payload['generated_utc']}")
    lines.append(f"Bucket: s3://{BUCKET}")
    lines.append("Path template: rrfs_a/refs.YYYYMMDD/HH/enspost/")
    lines.append("")

    selected = payload.get("selected_cycle")
    if selected:
        lines.append("SELECTED CYCLE")
        lines.append(f"  cycle_utc: {selected['cycle_utc']}")
        lines.append(f"  prefix: {selected['prefix']}")
        lines.append(f"  total_keys: {selected['total_keys']}")
        lines.append(f"  grib_key_count: {selected['grib_key_count']}")
        lines.append(f"  idx_key_count: {selected['idx_key_count']}")
        lines.append(f"  forecast_hours_detected: {selected['forecast_hours_detected']}")
        lines.append(f"  members_detected: {selected['members_detected']}")
        lines.append(f"  category_counts: {selected['category_counts']}")
        lines.append("")
        lines.append("SAMPLE GRIB KEYS")
        for item in selected.get("sample_grib_keys", [])[:80]:
            lines.append(f"  {item['key']}")
        lines.append("")
        lines.append("SAMPLE IDX KEYS")
        for item in selected.get("sample_idx_keys", [])[:80]:
            lines.append(f"  {item['key']}")
    else:
        lines.append("SELECTED CYCLE: None")

    lines.append("")
    lines.append("ALL CYCLES")
    for c in payload["cycles"]:
        lines.append(
            f"  {c['cycle_utc']} | status={c['status']} | usable={c['usable']} | "
            f"keys={c['total_keys']} | grib={c['grib_key_count']} | idx={c['idx_key_count']} | "
            f"prefix={c['prefix']}"
        )
        if c.get("error"):
            lines.append(f"    error: {c['error']}")

    OUT_TXT.write_text("\n".join(lines))


def main() -> None:
    print("Running targeted RRFS/REFS inventory scan")

    cycles = []
    for cycle in cycle_candidates():
        result = scan_cycle(cycle)
        cycles.append(result)

        if result["usable"] and result["grib_key_count"] > 0:
            break

    selected = choose_selected(cycles)

    payload = {
        "generated_utc": utc_now(),
        "bucket": BUCKET,
        "base_url": BASE_URL,
        "path_template": "rrfs_a/refs.YYYYMMDD/HH/enspost/",
        "selected_cycle": selected,
        "cycles": cycles,
    }

    OUT_JSON.write_text(json.dumps(payload, indent=2))
    OUT_SELECTED.write_text(json.dumps(selected or {"selected_cycle": None}, indent=2))
    write_txt(payload)

    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_TXT}")
    print(f"Wrote {OUT_SELECTED}")

    if selected:
        print(f"Selected cycle: {selected['cycle_utc']}")
        print(f"Prefix: {selected['prefix']}")
        print(f"GRIB keys: {selected['grib_key_count']}")
        print(f"IDX keys: {selected['idx_key_count']}")
    else:
        print("No usable cycle found.")


if __name__ == "__main__":
    main()
