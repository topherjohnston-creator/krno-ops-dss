from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


DATA = Path("data")
DATA.mkdir(exist_ok=True)

REPORT_PATH = DATA / "refs_inventory_report.json"

# Candidate public AWS buckets / base URLs.
# We need to verify the live structure before writing the builder.
BUCKETS = [
    {
        "name": "noaa-href-pds",
        "base_url": "https://noaa-href-pds.s3.amazonaws.com",
        "model": "HREF",
    },
    {
        "name": "noaa-refs-pds",
        "base_url": "https://noaa-refs-pds.s3.amazonaws.com",
        "model": "REFS",
    },
]

# Candidate prefixes. The script will test which one actually exists.
PREFIX_PATTERNS = [
    "{model_lc}.{ymd}/",
    "{ymd}/",
    "{model_lc}.{ymd}/{hh}/",
    "{ymd}/{hh}/",
    "{model_lc}.{ymd}/ensprod/",
    "{model_lc}.{ymd}/{hh}/ensprod/",
    "{ymd}/{hh}/ensprod/",
    "{model_lc}.{ymd}/wgrbbul/",
    "{model_lc}.{ymd}/{hh}/wgrbbul/",
]

# Candidate IDX file path patterns.
IDX_PATTERNS = [
    "{prefix}{model_lc}.t{hh}z.conus.f{fxx:02d}.grib2.idx",
    "{prefix}{model_lc}.t{hh}z.conus.f{fxx:03d}.grib2.idx",
    "{prefix}{model_lc}.t{hh}z.conus.mean.f{fxx:02d}.grib2.idx",
    "{prefix}{model_lc}.t{hh}z.conus.mean.f{fxx:03d}.grib2.idx",
    "{prefix}{model_lc}.t{hh}z.conus.prob.f{fxx:02d}.grib2.idx",
    "{prefix}{model_lc}.t{hh}z.conus.prob.f{fxx:03d}.grib2.idx",
    "{prefix}{model_lc}.t{hh}z.conus.ens.f{fxx:02d}.grib2.idx",
    "{prefix}{model_lc}.t{hh}z.conus.ens.f{fxx:03d}.grib2.idx",
    "{prefix}{model_lc}.t{hh}z.f{fxx:02d}.grib2.idx",
    "{prefix}{model_lc}.t{hh}z.f{fxx:03d}.grib2.idx",
]

CYCLES = ["00", "06", "12", "18"]
FXX_CHECKS = [1, 3, 6, 12, 18, 24, 36, 48, 60]

HAZARD_KEYWORDS = {
    "wind": [
        "GUST",
        "WIND",
        "UGRD",
        "VGRD",
        "10 m above ground",
    ],
    "lightning": [
        "LTNG",
        "LIGHTNING",
        "TSTM",
        "THUNDER",
    ],
    "rain": [
        "APCP",
        "RAIN",
        "PRECIP",
        "QPF",
    ],
    "snow": [
        "ASNOW",
        "SNOW",
        "WEASD",
    ],
    "freezing_rain": [
        "FZRA",
        "FREEZING RAIN",
        "CFRZR",
    ],
    "visibility": [
        "VIS",
        "VISIBILITY",
    ],
    "temperature": [
        "TMP",
        "TMAX",
        "TMIN",
        "2 m above ground",
    ],
    "wet_bulb": [
        "WETB",
        "WET BULB",
        "TWET",
    ],
    "hail": [
        "HAIL",
    ],
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def recent_dates(hours_back: int = 72) -> list[datetime]:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    dates = []

    for offset in range(0, hours_back + 1, 6):
        dt = now - timedelta(hours=offset)
        dt = dt.replace(hour=(dt.hour // 6) * 6)
        if dt not in dates:
            dates.append(dt)

    return dates


def http_get_text(url: str, timeout: int = 25) -> tuple[int, str]:
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code, response.text
    except Exception as exc:
        return 0, f"REQUEST_ERROR: {exc}"


def http_head(url: str, timeout: int = 20) -> tuple[int, dict[str, str]]:
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        return response.status_code, dict(response.headers)
    except Exception as exc:
        return 0, {"error": str(exc)}


def s3_list_prefix(base_url: str, prefix: str, max_keys: int = 50) -> dict[str, Any]:
    url = f"{base_url}/?list-type=2&prefix={prefix}&max-keys={max_keys}"
    status, text = http_get_text(url)

    result: dict[str, Any] = {
        "url": url,
        "status": status,
        "prefix": prefix,
        "keys": [],
        "error": None,
    }

    if status != 200:
        result["error"] = text[:500]
        return result

    try:
        root = ET.fromstring(text)
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

        keys = []
        for item in root.findall(".//s3:Contents", ns):
            key_el = item.find("s3:Key", ns)
            if key_el is not None and key_el.text:
                keys.append(key_el.text)

        # S3 XML may also come back without namespace depending on endpoint/proxy.
        if not keys:
            for item in root.findall(".//Contents"):
                key_el = item.find("Key")
                if key_el is not None and key_el.text:
                    keys.append(key_el.text)

        result["keys"] = keys
        return result

    except Exception as exc:
        result["error"] = f"XML_PARSE_ERROR: {exc}; text={text[:500]}"
        return result


def make_prefixes(bucket: dict[str, str], cycle: datetime) -> list[str]:
    model_lc = bucket["model"].lower()
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")

    prefixes = []
    for pattern in PREFIX_PATTERNS:
        prefixes.append(
            pattern.format(
                model_lc=model_lc,
                ymd=ymd,
                hh=hh,
            )
        )

    # Preserve order, remove duplicates.
    return list(dict.fromkeys(prefixes))


def make_idx_urls(bucket: dict[str, str], cycle: datetime, prefix: str, fxx: int) -> list[str]:
    model_lc = bucket["model"].lower()
    hh = cycle.strftime("%H")

    urls = []
    for pattern in IDX_PATTERNS:
        key = pattern.format(
            prefix=prefix,
            model_lc=model_lc,
            hh=hh,
            fxx=fxx,
        )
        urls.append(f"{bucket['base_url']}/{key}")

    return list(dict.fromkeys(urls))


def classify_idx_lines(idx_text: str) -> dict[str, Any]:
    lines = idx_text.splitlines()

    matches: dict[str, list[str]] = {hazard: [] for hazard in HAZARD_KEYWORDS}

    for line in lines:
        upper = line.upper()

        for hazard, keywords in HAZARD_KEYWORDS.items():
            for keyword in keywords:
                if keyword.upper() in upper:
                    matches[hazard].append(line)
                    break

    summary = {
        "total_lines": len(lines),
        "hazards": {},
    }

    for hazard, hazard_lines in matches.items():
        # Keep report small.
        summary["hazards"][hazard] = {
            "count": len(hazard_lines),
            "sample_lines": hazard_lines[:25],
        }

    return summary


def find_candidate_idx_files() -> dict[str, Any]:
    report: dict[str, Any] = {
        "generated_utc": utc_now_iso(),
        "purpose": (
            "Scan likely AWS REFS/HREF bucket structures and IDX files so the DSS builder "
            "can be rewritten against the actual available hourly fields."
        ),
        "buckets_checked": [],
        "candidate_idx_files": [],
        "best_candidates": [],
        "notes": [],
    }

    cycles = recent_dates(hours_back=72)

    for bucket in BUCKETS:
        bucket_report: dict[str, Any] = {
            "bucket": bucket["name"],
            "model": bucket["model"],
            "base_url": bucket["base_url"],
            "prefix_checks": [],
            "idx_checks": [],
        }

        for cycle in cycles:
            prefixes = make_prefixes(bucket, cycle)

            for prefix in prefixes:
                prefix_result = s3_list_prefix(bucket["base_url"], prefix, max_keys=20)
                bucket_report["prefix_checks"].append(
                    {
                        "cycle": cycle.isoformat().replace("+00:00", "Z"),
                        "prefix": prefix,
                        "status": prefix_result["status"],
                        "key_count": len(prefix_result.get("keys", [])),
                        "sample_keys": prefix_result.get("keys", [])[:10],
                        "error": prefix_result.get("error"),
                    }
                )

                if prefix_result["status"] != 200 or not prefix_result.get("keys"):
                    continue

                # If this prefix has keys, test IDX file patterns.
                for fxx in FXX_CHECKS:
                    for idx_url in make_idx_urls(bucket, cycle, prefix, fxx):
                        status, headers = http_head(idx_url)

                        idx_check = {
                            "cycle": cycle.isoformat().replace("+00:00", "Z"),
                            "bucket": bucket["name"],
                            "model": bucket["model"],
                            "fxx": fxx,
                            "idx_url": idx_url,
                            "status": status,
                            "content_length": headers.get("Content-Length"),
                            "content_type": headers.get("Content-Type"),
                        }

                        bucket_report["idx_checks"].append(idx_check)

                        if status == 200:
                            text_status, idx_text = http_get_text(idx_url)
                            classified = classify_idx_lines(idx_text) if text_status == 200 else {}

                            candidate = {
                                **idx_check,
                                "get_status": text_status,
                                "classification": classified,
                            }

                            report["candidate_idx_files"].append(candidate)

                            # Strong candidate if it has any useful hazard fields.
                            total_hazard_matches = 0
                            if classified:
                                for hazard_info in classified.get("hazards", {}).values():
                                    total_hazard_matches += int(hazard_info.get("count", 0))

                            if total_hazard_matches > 0:
                                report["best_candidates"].append(
                                    {
                                        **candidate,
                                        "total_hazard_matches": total_hazard_matches,
                                    }
                                )

                            # Do not hammer every pattern after finding one good IDX for that fxx.
                            break

        report["buckets_checked"].append(bucket_report)

    report["best_candidates"] = sorted(
        report["best_candidates"],
        key=lambda item: item.get("total_hazard_matches", 0),
        reverse=True,
    )

    if not report["candidate_idx_files"]:
        report["notes"].append(
            "No IDX files found from the tested AWS patterns. This likely means the bucket name/path "
            "is different, the model is not publicly exposed under these keys, or the product is not "
            "available through this AWS endpoint."
        )

    return report


def main() -> None:
    print("Scanning AWS REFS/HREF inventory...")

    report = find_candidate_idx_files()

    REPORT_PATH.write_text(json.dumps(report, indent=2))

    print(f"Wrote {REPORT_PATH}")
    print(f"Candidate IDX files found: {len(report.get('candidate_idx_files', []))}")
    print(f"Best candidates found: {len(report.get('best_candidates', []))}")

    if report.get("best_candidates"):
        print("\nTop candidate:")
        top = report["best_candidates"][0]
        print(json.dumps(
            {
                "model": top.get("model"),
                "cycle": top.get("cycle"),
                "fxx": top.get("fxx"),
                "idx_url": top.get("idx_url"),
                "total_hazard_matches": top.get("total_hazard_matches"),
            },
            indent=2,
        ))
    else:
        print("No usable candidate found. Send data/refs_inventory_report.json for review.")


if __name__ == "__main__":
    main()
