from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


BUCKET = "noaa-rrfs-pds"
S3_LIST_URL = f"https://{BUCKET}.s3.amazonaws.com"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

OUT_JSON = DATA_DIR / "rrfs_refs_inventory.json"
OUT_TXT = DATA_DIR / "rrfs_refs_inventory.txt"

# Keep this conservative so GitHub Actions does not get stuck.
MAX_CYCLES_TO_CHECK = 16
MAX_KEYS_PER_PREFIX = 5000
REQUEST_TIMEOUT = 45

# RRFS expected synoptic cycles.
CYCLE_HOURS = [0, 6, 12, 18]

# Search terms relevant to REFS / ensemble / probability products.
IMPORTANT_TERMS = [
    "refs",
    "ens",
    "mean",
    "prob",
    "member",
    "mem",
    "ctl",
    "control",
    "gust",
    "wind",
    "tmax",
    "tmp",
    "temp",
    "snow",
    "asnow",
    "rain",
    "apcp",
    "qpf",
    "fzra",
    "frzr",
    "vis",
    "visibility",
    "ltng",
    "lightning",
    "hail",
    "wetbulb",
    "twet",
    "wb",
]

# Common GRIB/index patterns.
GRIB_EXTENSIONS = (".grib2", ".grb2", ".idx")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_s3_keys(prefix: str, max_keys_total: int = MAX_KEYS_PER_PREFIX) -> list[str]:
    """
    List public S3 keys under a prefix using unsigned HTTPS ListBucketV2.
    Does not require AWS CLI or AWS credentials.
    """
    keys: list[str] = []
    continuation_token: str | None = None

    while len(keys) < max_keys_total:
        params = {
            "list-type": "2",
            "prefix": prefix,
            "max-keys": "1000",
        }

        if continuation_token:
            params["continuation-token"] = continuation_token

        response = requests.get(S3_LIST_URL, params=params, timeout=REQUEST_TIMEOUT)

        if response.status_code in (403, 404):
            return []

        response.raise_for_status()

        root = ET.fromstring(response.text)

        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0].strip("{")

        def q(name: str) -> str:
            return f"{{{ns}}}{name}" if ns else name

        for contents in root.findall(q("Contents")):
            key_node = contents.find(q("Key"))
            if key_node is not None and key_node.text:
                keys.append(key_node.text)

                if len(keys) >= max_keys_total:
                    break

        is_truncated_node = root.find(q("IsTruncated"))
        is_truncated = (
            is_truncated_node is not None
            and is_truncated_node.text is not None
            and is_truncated_node.text.lower() == "true"
        )

        if not is_truncated:
            break

        token_node = root.find(q("NextContinuationToken"))
        if token_node is None or not token_node.text:
            break

        continuation_token = token_node.text

        # Be polite to public object store.
        time.sleep(0.2)

    return keys


def latest_cycle_candidates() -> list[datetime]:
    """
    Generate latest likely RRFS cycle candidates, newest first.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    candidates: list[datetime] = []
    cursor = now

    while len(candidates) < MAX_CYCLES_TO_CHECK:
        cycle_hour = max([h for h in CYCLE_HOURS if h <= cursor.hour], default=18)

        if cycle_hour == 18 and cursor.hour < 0:
            cycle_date = cursor.date() - timedelta(days=1)
        else:
            cycle_date = cursor.date()

        cycle = datetime(
            cycle_date.year,
            cycle_date.month,
            cycle_date.day,
            cycle_hour,
            tzinfo=timezone.utc,
        )

        if cycle > now:
            cycle -= timedelta(hours=6)

        if cycle not in candidates:
            candidates.append(cycle)

        cursor = cycle - timedelta(hours=1)

    return candidates


def possible_cycle_prefixes(cycle: datetime) -> list[str]:
    """
    Try several likely RRFS public bucket path conventions.
    This is intentionally broad because RRFS/REFS paths are still easy to misremember.
    """
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")

    return [
        f"rrfs.{ymd}/{hh}/",
        f"rrfs.{ymd}/{hh}/ens/",
        f"rrfs.{ymd}/{hh}/refs/",
        f"rrfs.{ymd}/{hh}/prslev/",
        f"rrfs.{ymd}/{hh}/natlev/",
        f"rrfs.{ymd}/",
        f"refs.{ymd}/{hh}/",
        f"refs.{ymd}/",
        f"{ymd}/{hh}/",
    ]


def classify_key(key: str) -> dict[str, Any]:
    lower = key.lower()

    matched_terms = [term for term in IMPORTANT_TERMS if term in lower]

    fxx = None
    fxx_match = re.search(r"\.f(\d{2,3})\b|f(\d{2,3})\.", lower)
    if fxx_match:
        fxx = int(next(g for g in fxx_match.groups() if g is not None))

    member = None
    member_match = re.search(r"(?:mem|member|m)(\d{2,3})", lower)
    if member_match:
        member = member_match.group(1)

    product_guess = None
    if "prob" in lower:
        product_guess = "probability"
    elif "mean" in lower or "avg" in lower:
        product_guess = "mean"
    elif "ctl" in lower or "control" in lower:
        product_guess = "control"
    elif member is not None:
        product_guess = "member"
    elif "ens" in lower or "refs" in lower:
        product_guess = "ensemble_related"

    return {
        "key": key,
        "matched_terms": matched_terms,
        "fxx": fxx,
        "member": member,
        "product_guess": product_guess,
        "is_grib_or_idx": lower.endswith(GRIB_EXTENSIONS),
    }


def score_key(info: dict[str, Any]) -> int:
    terms = set(info["matched_terms"])
    score = 0

    if "refs" in terms:
        score += 10
    if "ens" in terms:
        score += 8
    if "prob" in terms:
        score += 8
    if "mean" in terms:
        score += 6
    if info["member"] is not None:
        score += 5
    if info["fxx"] is not None and 0 <= int(info["fxx"]) <= 60:
        score += 5
    if info["is_grib_or_idx"]:
        score += 3

    hazard_terms = {
        "gust",
        "wind",
        "tmax",
        "tmp",
        "temp",
        "snow",
        "asnow",
        "rain",
        "apcp",
        "qpf",
        "fzra",
        "frzr",
        "vis",
        "visibility",
        "ltng",
        "lightning",
        "hail",
        "wetbulb",
        "twet",
        "wb",
    }

    score += len(terms.intersection(hazard_terms))

    return score


def scan_cycle(cycle: datetime) -> dict[str, Any]:
    cycle_iso = cycle.isoformat().replace("+00:00", "Z")
    print(f"Scanning RRFS/REFS candidate cycle {cycle:%Y-%m-%d %HZ}")

    prefixes = possible_cycle_prefixes(cycle)
    prefix_results = []

    all_keys: list[str] = []

    for prefix in prefixes:
        print(f"  Listing prefix: s3://{BUCKET}/{prefix}")

        try:
            keys = list_s3_keys(prefix)
        except Exception as exc:
            prefix_results.append(
                {
                    "prefix": prefix,
                    "status": "error",
                    "error": str(exc),
                    "key_count": 0,
                    "sample_keys": [],
                }
            )
            continue

        prefix_results.append(
            {
                "prefix": prefix,
                "status": "ok",
                "key_count": len(keys),
                "sample_keys": keys[:25],
            }
        )

        all_keys.extend(keys)

        # If the broad date prefix has many keys, we do not need to hammer all alternates.
        if len(all_keys) >= MAX_KEYS_PER_PREFIX:
            break

    # De-duplicate while preserving order.
    seen = set()
    unique_keys = []
    for key in all_keys:
        if key not in seen:
            seen.add(key)
            unique_keys.append(key)

    classified = [classify_key(key) for key in unique_keys]
    for item in classified:
        item["score"] = score_key(item)

    interesting = sorted(
        [item for item in classified if item["score"] > 0],
        key=lambda item: (-item["score"], item["key"]),
    )

    grib_or_idx = [item for item in classified if item["is_grib_or_idx"]]

    fxx_values = sorted(
        {
            int(item["fxx"])
            for item in classified
            if item["fxx"] is not None and 0 <= int(item["fxx"]) <= 100
        }
    )

    matched_term_counts: dict[str, int] = {}
    for item in classified:
        for term in item["matched_terms"]:
            matched_term_counts[term] = matched_term_counts.get(term, 0) + 1

    return {
        "cycle_utc": cycle_iso,
        "prefixes_checked": prefix_results,
        "total_unique_keys": len(unique_keys),
        "total_grib_or_idx_keys": len(grib_or_idx),
        "forecast_hours_detected": fxx_values,
        "matched_term_counts": dict(sorted(matched_term_counts.items())),
        "interesting_keys_top_250": interesting[:250],
        "sample_all_keys_top_100": unique_keys[:100],
    }


def choose_best_cycle(cycle_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not cycle_results:
        return None

    def cycle_score(result: dict[str, Any]) -> tuple[int, int, int]:
        term_counts = result.get("matched_term_counts", {})
        refs_count = int(term_counts.get("refs", 0))
        ens_count = int(term_counts.get("ens", 0))
        prob_count = int(term_counts.get("prob", 0))
        total = int(result.get("total_unique_keys", 0))
        return (refs_count + ens_count + prob_count, total, len(result.get("forecast_hours_detected", [])))

    sorted_results = sorted(cycle_results, key=cycle_score, reverse=True)
    best = sorted_results[0]

    if int(best.get("total_unique_keys", 0)) <= 0:
        return None

    return best


def write_text_report(payload: dict[str, Any]) -> None:
    lines = []

    lines.append("RRFS / REFS AWS Inventory Scan")
    lines.append(f"Generated: {payload['generated_utc']}")
    lines.append(f"Bucket: s3://{BUCKET}")
    lines.append("")

    best = payload.get("best_cycle")
    if best:
        lines.append(f"Best detected cycle: {best.get('cycle_utc')}")
        lines.append(f"Total unique keys: {best.get('total_unique_keys')}")
        lines.append(f"GRIB/IDX keys: {best.get('total_grib_or_idx_keys')}")
        lines.append(f"Forecast hours detected: {best.get('forecast_hours_detected')}")
        lines.append("")
        lines.append("Matched term counts:")
        for term, count in best.get("matched_term_counts", {}).items():
            lines.append(f"  {term}: {count}")

        lines.append("")
        lines.append("Top interesting keys:")
        for item in best.get("interesting_keys_top_250", [])[:100]:
            lines.append(
                f"  score={item.get('score')} "
                f"fxx={item.get('fxx')} "
                f"member={item.get('member')} "
                f"type={item.get('product_guess')} "
                f"key={item.get('key')}"
            )
    else:
        lines.append("No usable RRFS/REFS cycle found.")

    lines.append("")
    lines.append("All cycle summaries:")
    for result in payload.get("cycle_results", []):
        lines.append("")
        lines.append(f"Cycle: {result.get('cycle_utc')}")
        lines.append(f"  total_unique_keys: {result.get('total_unique_keys')}")
        lines.append(f"  total_grib_or_idx_keys: {result.get('total_grib_or_idx_keys')}")
        lines.append(f"  forecast_hours_detected: {result.get('forecast_hours_detected')}")
        lines.append(f"  matched_term_counts: {result.get('matched_term_counts')}")

    OUT_TXT.write_text("\n".join(lines))


def main() -> None:
    cycles = latest_cycle_candidates()

    cycle_results = []
    for cycle in cycles:
        result = scan_cycle(cycle)
        cycle_results.append(result)

        # Stop early if we clearly found useful REFS/ensemble-related data.
        term_counts = result.get("matched_term_counts", {})
        if (
            result.get("total_unique_keys", 0) > 0
            and (
                term_counts.get("refs", 0) > 0
                or term_counts.get("ens", 0) > 0
                or term_counts.get("prob", 0) > 0
            )
        ):
            break

    best = choose_best_cycle(cycle_results)

    payload = {
        "generated_utc": utc_now(),
        "bucket": BUCKET,
        "bucket_https": S3_LIST_URL,
        "best_cycle": best,
        "cycle_results": cycle_results,
        "notes": [
            "This scanner uses public unsigned HTTPS S3 ListBucketV2 requests.",
            "It is designed to run entirely inside GitHub Actions.",
            "Use data/rrfs_refs_inventory.txt first because it is easier to inspect than JSON.",
            "The next step is to identify the exact GRIB/IDX key pattern for REFS hourly probabilities and member fields.",
        ],
    }

    OUT_JSON.write_text(json.dumps(payload, indent=2))
    write_text_report(payload)

    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_TXT}")

    if best:
        print(f"Best detected cycle: {best.get('cycle_utc')}")
        print(f"Total unique keys: {best.get('total_unique_keys')}")
        print(f"GRIB/IDX keys: {best.get('total_grib_or_idx_keys')}")
    else:
        print("No usable RRFS/REFS cycle found.")
        sys.exit(1)


if __name__ == "__main__":
    main()
