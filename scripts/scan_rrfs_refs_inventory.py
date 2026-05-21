from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests


BUCKET = "noaa-rrfs-pds"
S3_BASE = f"https://{BUCKET}.s3.amazonaws.com"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

DEFAULT_MAX_KEYS = 1000
REQUEST_TIMEOUT = 45

# We are looking for REFS buried under RRFS-style paths.
# Keep this broad because the exact production layout may vary.
CANDIDATE_TOP_PREFIXES = [
    "rrfs.",
    "rrfs_a.",
    "rrfs_b.",
    "refs.",
    "refs_a.",
    "refs_b.",
]

# Keywords to identify useful model fields.
FIELD_KEYWORDS = {
    "wind": [
        "GUST",
        "WIND",
        "UGRD",
        "VGRD",
        "WIND GUST",
        "10 m above ground",
    ],
    "temperature": [
        "TMP",
        "TMAX",
        "TMIN",
        "2 m above ground",
        "TCDC",
    ],
    "wet_bulb": [
        "WETBULB",
        "WBT",
        "TWET",
        "WET BULB",
    ],
    "rain": [
        "APCP",
        "PRATE",
        "RAIN",
        "PRECIP",
        "TP",
    ],
    "snow": [
        "ASNOW",
        "SNOD",
        "SNOW",
        "WEASD",
    ],
    "freezing_rain": [
        "FZRA",
        "CFRZR",
        "FREEZING RAIN",
        "ICE",
    ],
    "visibility": [
        "VIS",
        "VISIBILITY",
    ],
    "lightning": [
        "LTNG",
        "LIGHTNING",
        "TSTM",
        "THUNDER",
    ],
    "hail": [
        "HAIL",
    ],
    "probability": [
        "PROB",
        "prob",
        "%",
        "prob >",
        "prob <",
    ],
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def format_cycle(cycle: datetime) -> str:
    return cycle.strftime("%Y%m%d%H")


def cycle_candidates(hours_back: int = 48) -> list[datetime]:
    """
    Return recent 00/06/12/18Z cycles, newest first.
    """
    now = utc_now().replace(minute=0, second=0, microsecond=0)
    current_cycle_hour = (now.hour // 6) * 6
    current = now.replace(hour=current_cycle_hour)

    cycles = []
    t = current

    while t >= now - timedelta(hours=hours_back):
        cycles.append(t)
        t -= timedelta(hours=6)

    return cycles


def s3_list_url(
    prefix: str = "",
    delimiter: str | None = None,
    continuation_token: str | None = None,
    max_keys: int = DEFAULT_MAX_KEYS,
) -> str:
    params = [
        "list-type=2",
        f"max-keys={max_keys}",
        f"prefix={quote_plus(prefix)}",
    ]

    if delimiter is not None:
        params.append(f"delimiter={quote_plus(delimiter)}")

    if continuation_token:
        params.append(f"continuation-token={quote_plus(continuation_token)}")

    return f"{S3_BASE}/?{'&'.join(params)}"


def fetch_xml(url: str) -> ET.Element:
    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return ET.fromstring(r.text)


def strip_namespace(root: ET.Element) -> None:
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]


def list_s3(
    prefix: str = "",
    delimiter: str | None = None,
    max_pages: int = 20,
    max_keys: int = DEFAULT_MAX_KEYS,
) -> dict[str, Any]:
    """
    List S3 keys/prefixes without boto3 or AWS credentials.
    """
    keys: list[dict[str, Any]] = []
    prefixes: list[str] = []

    token = None
    pages = 0

    while True:
        pages += 1

        url = s3_list_url(
            prefix=prefix,
            delimiter=delimiter,
            continuation_token=token,
            max_keys=max_keys,
        )

        root = fetch_xml(url)
        strip_namespace(root)

        for cp in root.findall("CommonPrefixes"):
            p = cp.findtext("Prefix")
            if p:
                prefixes.append(p)

        for content in root.findall("Contents"):
            key = content.findtext("Key")
            size = content.findtext("Size")
            last_modified = content.findtext("LastModified")

            if key:
                keys.append(
                    {
                        "key": key,
                        "size": int(size) if size and size.isdigit() else None,
                        "last_modified": last_modified,
                    }
                )

        is_truncated = (root.findtext("IsTruncated") or "").lower() == "true"
        token = root.findtext("NextContinuationToken")

        if not is_truncated or not token:
            break

        if pages >= max_pages:
            break

    return {
        "prefix": prefix,
        "delimiter": delimiter,
        "pages_read": pages,
        "prefixes": sorted(set(prefixes)),
        "keys": keys,
    }


def fetch_text_url(url: str) -> str:
    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


def key_url(key: str) -> str:
    return f"{S3_BASE}/{key}"


def discover_root_prefixes() -> list[str]:
    """
    Pull root common prefixes from the bucket.
    """
    print("Listing root prefixes from noaa-rrfs-pds...")
    out = list_s3(prefix="", delimiter="/", max_pages=5)
    return out["prefixes"]


def candidate_cycle_prefixes(cycle: datetime, root_prefixes: list[str]) -> list[str]:
    """
    Build likely RRFS/REFS prefixes from observed root prefixes and common layouts.
    """
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")

    candidates: set[str] = set()

    # Known/common NOAA-style layouts.
    manual_roots = [
        f"rrfs.{ymd}/",
        f"rrfs_a.{ymd}/",
        f"rrfs_b.{ymd}/",
        f"refs.{ymd}/",
        f"refs_a.{ymd}/",
        f"refs_b.{ymd}/",
    ]

    for root in manual_roots:
        candidates.add(root)
        candidates.add(f"{root}{hh}/")
        candidates.add(f"{root}{hh}/control/")
        candidates.add(f"{root}{hh}/mean/")
        candidates.add(f"{root}{hh}/enspost/")
        candidates.add(f"{root}{hh}/post/")
        candidates.add(f"{root}{hh}/prod/")
        candidates.add(f"{root}{hh}/wrfsfc/")
        candidates.add(f"{root}{hh}/prslev/")

        for mem in range(1, 31):
            candidates.add(f"{root}{hh}/mem{mem:03d}/")
            candidates.add(f"{root}{hh}/member{mem:03d}/")
            candidates.add(f"{root}{hh}/m{mem:02d}/")

    # If root listing reveals dated prefixes, include them.
    for rp in root_prefixes:
        if ymd in rp or any(rp.startswith(x) for x in CANDIDATE_TOP_PREFIXES):
            candidates.add(rp)
            candidates.add(f"{rp}{hh}/")
            candidates.add(f"{rp}{hh}/control/")
            candidates.add(f"{rp}{hh}/mean/")
            candidates.add(f"{rp}{hh}/enspost/")
            candidates.add(f"{rp}{hh}/post/")
            candidates.add(f"{rp}{hh}/prod/")

            for mem in range(1, 31):
                candidates.add(f"{rp}{hh}/mem{mem:03d}/")
                candidates.add(f"{rp}{hh}/member{mem:03d}/")
                candidates.add(f"{rp}{hh}/m{mem:02d}/")

    return sorted(candidates)


def is_grib_or_idx(key: str) -> bool:
    lower = key.lower()
    return (
        lower.endswith(".grib2")
        or lower.endswith(".grib2.idx")
        or lower.endswith(".grb2")
        or lower.endswith(".grb2.idx")
        or lower.endswith(".idx")
    )


def looks_like_refs_key(key: str) -> bool:
    lower = key.lower()
    return (
        "refs" in lower
        or "ens" in lower
        or "mem" in lower
        or "mean" in lower
        or "prob" in lower
    )


def categorize_idx_line(line: str) -> list[str]:
    upper = line.upper()
    hits = []

    for category, keywords in FIELD_KEYWORDS.items():
        for keyword in keywords:
            if keyword.upper() in upper:
                hits.append(category)
                break

    return sorted(set(hits))


def scan_idx_file(key: str, max_lines: int = 3000) -> dict[str, Any]:
    """
    Download and scan a GRIB index file.
    """
    url = key_url(key)

    result = {
        "idx_key": key,
        "idx_url": url,
        "status": "unknown",
        "line_count": 0,
        "field_matches": {},
        "sample_lines": [],
    }

    try:
        text = fetch_text_url(url)
    except Exception as exc:
        result["status"] = f"error: {exc}"
        return result

    lines = text.splitlines()
    result["line_count"] = len(lines)
    result["status"] = "ok"

    field_matches: dict[str, list[str]] = {k: [] for k in FIELD_KEYWORDS.keys()}
    sample_lines = []

    for line in lines[:max_lines]:
        cats = categorize_idx_line(line)

        if cats:
            sample_lines.append(line)

        for cat in cats:
            if len(field_matches[cat]) < 50:
                field_matches[cat].append(line)

    result["field_matches"] = {
        k: v for k, v in field_matches.items() if v
    }
    result["sample_lines"] = sample_lines[:200]

    return result


def scan_prefix(prefix: str, recursive_pages: int = 10) -> dict[str, Any]:
    """
    Scan one prefix for files and IDX contents.
    """
    print(f"Scanning prefix: {prefix}")

    prefix_result = {
        "prefix": prefix,
        "status": "unknown",
        "prefixes": [],
        "keys": [],
        "grib_keys": [],
        "idx_keys": [],
        "refs_like_keys": [],
        "idx_scans": [],
    }

    try:
        listed = list_s3(
            prefix=prefix,
            delimiter=None,
            max_pages=recursive_pages,
            max_keys=DEFAULT_MAX_KEYS,
        )
    except Exception as exc:
        prefix_result["status"] = f"error: {exc}"
        return prefix_result

    keys = listed["keys"]
    key_names = [k["key"] for k in keys]

    grib_keys = [k for k in key_names if is_grib_or_idx(k)]
    idx_keys = [k for k in key_names if k.lower().endswith(".idx")]
    refs_like_keys = [k for k in key_names if looks_like_refs_key(k)]

    prefix_result["status"] = "ok"
    prefix_result["keys"] = keys[:500]
    prefix_result["grib_keys"] = grib_keys[:500]
    prefix_result["idx_keys"] = idx_keys[:100]
    prefix_result["refs_like_keys"] = refs_like_keys[:500]

    # Scan up to 20 IDX files per prefix.
    for idx_key in idx_keys[:20]:
        idx_scan = scan_idx_file(idx_key)
        prefix_result["idx_scans"].append(idx_scan)

    return prefix_result


def summarize_scan(scan_results: list[dict[str, Any]]) -> dict[str, Any]:
    found_prefixes = []
    idx_files = []
    grib_files = []
    refs_like = []
    field_hits: dict[str, list[str]] = {k: [] for k in FIELD_KEYWORDS.keys()}

    for result in scan_results:
        if result.get("status") != "ok":
            continue

        if result.get("grib_keys"):
            found_prefixes.append(result["prefix"])

        grib_files.extend(result.get("grib_keys", []))
        idx_files.extend(result.get("idx_keys", []))
        refs_like.extend(result.get("refs_like_keys", []))

        for idx_scan in result.get("idx_scans", []):
            for field, lines in idx_scan.get("field_matches", {}).items():
                for line in lines:
                    if len(field_hits[field]) < 100:
                        field_hits[field].append(
                            f"{idx_scan.get('idx_key')}: {line}"
                        )

    return {
        "found_prefixes": sorted(set(found_prefixes)),
        "grib_file_count_sampled": len(grib_files),
        "idx_file_count_sampled": len(idx_files),
        "refs_like_key_count_sampled": len(refs_like),
        "sample_grib_files": grib_files[:200],
        "sample_idx_files": idx_files[:100],
        "sample_refs_like_keys": refs_like[:200],
        "field_hits": {k: v for k, v in field_hits.items() if v},
    }


def write_text_report(report: dict[str, Any], path: Path) -> None:
    lines = []

    lines.append("RRFS / REFS AWS Inventory Scan")
    lines.append("=" * 80)
    lines.append(f"Generated UTC: {report['generated_utc']}")
    lines.append(f"Bucket: s3://{BUCKET}")
    lines.append(f"Cycles checked: {', '.join(report['cycles_checked'])}")
    lines.append("")

    lines.append("Root Prefixes")
    lines.append("-" * 80)
    for p in report.get("root_prefixes", []):
        lines.append(p)
    lines.append("")

    lines.append("Found Candidate Prefixes With GRIB/IDX Files")
    lines.append("-" * 80)
    for p in report["summary"].get("found_prefixes", []):
        lines.append(p)
    lines.append("")

    lines.append("Sample GRIB Files")
    lines.append("-" * 80)
    for k in report["summary"].get("sample_grib_files", []):
        lines.append(k)
    lines.append("")

    lines.append("Sample IDX Files")
    lines.append("-" * 80)
    for k in report["summary"].get("sample_idx_files", []):
        lines.append(k)
    lines.append("")

    lines.append("Sample REFS/Ensemble-Like Keys")
    lines.append("-" * 80)
    for k in report["summary"].get("sample_refs_like_keys", []):
        lines.append(k)
    lines.append("")

    lines.append("Field Hits From IDX Files")
    lines.append("-" * 80)
    field_hits = report["summary"].get("field_hits", {})

    if not field_hits:
        lines.append("No field hits found in scanned IDX files.")
    else:
        for field, hits in field_hits.items():
            lines.append("")
            lines.append(f"[{field.upper()}]")
            for hit in hits[:100]:
                lines.append(hit)

    path.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan noaa-rrfs-pds AWS bucket for RRFS/REFS inventory and useful hazard fields."
    )

    parser.add_argument(
        "--hours-back",
        type=int,
        default=48,
        help="How far back to search 00/06/12/18Z cycles. Default: 48.",
    )

    parser.add_argument(
        "--max-prefixes",
        type=int,
        default=80,
        help="Maximum candidate prefixes to deeply scan. Default: 80.",
    )

    parser.add_argument(
        "--recursive-pages",
        type=int,
        default=8,
        help="S3 listing pages per prefix. Default: 8.",
    )

    parser.add_argument(
        "--cycle",
        default="",
        help="Optional single cycle YYYYMMDDHH. Example: 2026052018.",
    )

    args = parser.parse_args()

    generated = utc_now()

    try:
        root_prefixes = discover_root_prefixes()
    except Exception as exc:
        print(f"ERROR: Could not list root prefixes from s3://{BUCKET}: {exc}", file=sys.stderr)
        root_prefixes = []

    if args.cycle:
        if not re.fullmatch(r"\d{10}", args.cycle):
            raise ValueError("--cycle must be YYYYMMDDHH")

        cycles = [
            datetime.strptime(args.cycle, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        ]
    else:
        cycles = cycle_candidates(hours_back=args.hours_back)

    candidate_prefixes: list[str] = []

    for cycle in cycles:
        candidate_prefixes.extend(candidate_cycle_prefixes(cycle, root_prefixes))

    candidate_prefixes = sorted(set(candidate_prefixes))

    print(f"Generated {len(candidate_prefixes)} candidate prefixes.")
    print(f"Scanning up to {args.max_prefixes} prefixes.")

    scan_results = []

    for i, prefix in enumerate(candidate_prefixes[: args.max_prefixes], start=1):
        print(f"[{i}/{min(args.max_prefixes, len(candidate_prefixes))}] {prefix}")
        result = scan_prefix(prefix, recursive_pages=args.recursive_pages)
        scan_results.append(result)

        # Light throttle to avoid hammering the public bucket.
        time.sleep(0.1)

    summary = summarize_scan(scan_results)

    report = {
        "generated_utc": generated.isoformat().replace("+00:00", "Z"),
        "bucket": BUCKET,
        "s3_base": S3_BASE,
        "cycles_checked": [format_cycle(c) for c in cycles],
        "root_prefixes": root_prefixes,
        "candidate_prefix_count": len(candidate_prefixes),
        "candidate_prefixes_scanned": candidate_prefixes[: args.max_prefixes],
        "summary": summary,
        "scan_results": scan_results,
        "notes": [
            "This scan uses unsigned public S3 HTTPS ListBucket requests.",
            "REFS appears to be stored under the RRFS public bucket, but directory layout may vary by cycle.",
            "Use the sample IDX files and field_hits section to identify exact GRIB variable names and paths.",
            "Next step after this scan is to build a REFS point extractor using the discovered GRIB/IDX keys.",
        ],
    }

    json_path = DATA_DIR / "rrfs_refs_inventory_report.json"
    txt_path = DATA_DIR / "rrfs_refs_inventory_report.txt"

    json_path.write_text(json.dumps(report, indent=2))
    write_text_report(report, txt_path)

    print("")
    print("Wrote:")
    print(f"  {json_path}")
    print(f"  {txt_path}")
    print("")
    print("Key findings:")
    print(f"  Found prefixes with GRIB/IDX files: {len(summary.get('found_prefixes', []))}")
    print(f"  Sample GRIB files: {len(summary.get('sample_grib_files', []))}")
    print(f"  Sample IDX files: {len(summary.get('sample_idx_files', []))}")
    print(f"  Field-hit categories: {', '.join(summary.get('field_hits', {}).keys()) or 'none'}")


if __name__ == "__main__":
    main()
