from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import xml.etree.ElementTree as ET


DATA = Path("data")
DATA.mkdir(exist_ok=True)

BUCKET = "noaa-rrfs-pds"
BASE_URL = f"https://{BUCKET}.s3.amazonaws.com"

OUT_JSON = DATA / "rrfs_refs_inventory.json"
OUT_TXT = DATA / "rrfs_refs_inventory.txt"
OUT_SELECTED = DATA / "rrfs_refs_selected_cycle.json"

DEFAULT_LOOKBACK_CYCLES = 20
DEFAULT_MAX_DEPTH = 6
DEFAULT_MAX_PREFIXES_PER_CYCLE = 250
DEFAULT_MAX_KEYS_PER_PREFIX = 200
TIMEOUT = 45


@dataclass
class S3Listing:
    status: int
    prefix: str
    keys: list[str]
    common_prefixes: list[str]
    is_truncated: bool
    next_token: str | None
    error: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def latest_synoptic_cycle(now: datetime | None = None) -> datetime:
    now = now or utc_now()
    now = now.replace(minute=0, second=0, microsecond=0)
    return now.replace(hour=(now.hour // 6) * 6)


def cycle_candidates(lookback_cycles: int) -> list[datetime]:
    start = latest_synoptic_cycle()
    return [start - timedelta(hours=6 * i) for i in range(lookback_cycles)]


def s3_list(
    prefix: str,
    delimiter: str = "/",
    max_keys: int = 1000,
    continuation_token: str | None = None,
) -> S3Listing:
    params = {
        "list-type": "2",
        "prefix": prefix,
        "delimiter": delimiter,
        "max-keys": str(max_keys),
    }

    if continuation_token:
        params["continuation-token"] = continuation_token

    try:
        response = requests.get(BASE_URL, params=params, timeout=TIMEOUT)
        status = response.status_code

        if status != 200:
            return S3Listing(
                status=status,
                prefix=prefix,
                keys=[],
                common_prefixes=[],
                is_truncated=False,
                next_token=None,
                error=response.text[:1200],
            )

        root = ET.fromstring(response.text)
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

        keys = []
        for item in root.findall("s3:Contents", ns):
            key_el = item.find("s3:Key", ns)
            if key_el is not None and key_el.text:
                keys.append(html.unescape(key_el.text))

        common_prefixes = []
        for item in root.findall("s3:CommonPrefixes", ns):
            prefix_el = item.find("s3:Prefix", ns)
            if prefix_el is not None and prefix_el.text:
                common_prefixes.append(html.unescape(prefix_el.text))

        truncated_el = root.find("s3:IsTruncated", ns)
        is_truncated = truncated_el is not None and truncated_el.text == "true"

        next_token_el = root.find("s3:NextContinuationToken", ns)
        next_token = next_token_el.text if next_token_el is not None else None

        return S3Listing(
            status=200,
            prefix=prefix,
            keys=keys,
            common_prefixes=common_prefixes,
            is_truncated=is_truncated,
            next_token=next_token,
        )

    except Exception as exc:
        return S3Listing(
            status=0,
            prefix=prefix,
            keys=[],
            common_prefixes=[],
            is_truncated=False,
            next_token=None,
            error=repr(exc),
        )


def s3_list_all_keys(prefix: str, max_total_keys: int = 2000) -> list[str]:
    keys: list[str] = []
    token = None

    while True:
        listing = s3_list(
            prefix=prefix,
            delimiter="",
            max_keys=min(1000, max_total_keys - len(keys)),
            continuation_token=token,
        )

        if listing.status != 200:
            break

        keys.extend(listing.keys)

        if not listing.is_truncated or not listing.next_token:
            break

        if len(keys) >= max_total_keys:
            break

        token = listing.next_token

    return keys[:max_total_keys]


def root_prefixes_for_cycle(cycle: datetime) -> list[str]:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")

    return [
        f"rrfs.{ymd}/",
        f"rrfs.{ymd}/{hh}/",
        f"rrfs.{ymd}/{hh}/ens/",
        f"rrfs.{ymd}/{hh}/refs/",
        f"rrfs.{ymd}/{hh}/prslev/",
        f"rrfs.{ymd}/{hh}/natlev/",
        f"refs.{ymd}/",
        f"refs.{ymd}/{hh}/",
        f"{ymd}/",
        f"{ymd}/{hh}/",
    ]


def key_score(key: str) -> int:
    k = key.lower()
    score = 0

    if "refs" in k:
        score += 20
    if "/ens" in k or ".ens" in k or "ensprod" in k:
        score += 15
    if k.endswith(".idx"):
        score += 8
    if ".grib2" in k or ".grb2" in k:
        score += 8
    if "wrfsfc" in k or "sfc" in k:
        score += 4
    if "natlev" in k or "prslev" in k:
        score += 2

    wanted_terms = [
        "gust",
        "tmax",
        "tmin",
        "tmp",
        "dpt",
        "vis",
        "asnow",
        "apcp",
        "ltng",
        "ceil",
        "frzr",
        "fzra",
        "wetbulb",
        "twet",
    ]

    for term in wanted_terms:
        if term in k:
            score += 2

    return score


def looks_like_model_key(key: str) -> bool:
    k = key.lower()
    return (
        ".grib2" in k
        or ".grb2" in k
        or k.endswith(".idx")
        or ".grib2.idx" in k
        or ".grb2.idx" in k
    )


def summarize_keys(keys: list[str]) -> dict[str, Any]:
    model_keys = [k for k in keys if looks_like_model_key(k)]
    idx_keys = [k for k in model_keys if k.lower().endswith(".idx")]
    grib_keys = [k for k in model_keys if ".grib2" in k.lower() or ".grb2" in k.lower()]
    refs_keys = [k for k in model_keys if "refs" in k.lower()]
    ens_keys = [k for k in model_keys if "/ens" in k.lower() or ".ens" in k.lower() or "ensprod" in k.lower()]

    scored = sorted(model_keys, key=key_score, reverse=True)

    return {
        "total_keys": len(keys),
        "model_key_count": len(model_keys),
        "idx_key_count": len(idx_keys),
        "grib_key_count": len(grib_keys),
        "refs_key_count": len(refs_keys),
        "ens_key_count": len(ens_keys),
        "sample_model_keys": scored[:40],
        "sample_idx_keys": idx_keys[:25],
        "sample_grib_keys": grib_keys[:25],
    }


def scan_cycle(
    cycle: datetime,
    max_depth: int,
    max_prefixes_per_cycle: int,
    max_keys_per_prefix: int,
) -> dict[str, Any]:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")

    print(f"Scanning RRFS/REFS candidate cycle {cycle:%Y-%m-%d %HZ}")

    queue: list[tuple[str, int]] = [(p, 0) for p in root_prefixes_for_cycle(cycle)]
    seen_prefixes: set[str] = set()
    prefix_reports: list[dict[str, Any]] = []
    all_keys: list[str] = []

    while queue and len(seen_prefixes) < max_prefixes_per_cycle:
        prefix, depth = queue.pop(0)

        if prefix in seen_prefixes:
            continue

        seen_prefixes.add(prefix)

        print(f"  Listing prefix: s3://{BUCKET}/{prefix}")

        listing = s3_list(
            prefix=prefix,
            delimiter="/",
            max_keys=max_keys_per_prefix,
        )

        report = {
            "prefix": prefix,
            "depth": depth,
            "status": listing.status,
            "key_count": len(listing.keys),
            "common_prefix_count": len(listing.common_prefixes),
            "sample_keys": listing.keys[:20],
            "sample_common_prefixes": listing.common_prefixes[:30],
            "is_truncated": listing.is_truncated,
            "error": listing.error,
        }

        prefix_reports.append(report)

        if listing.status != 200:
            continue

        all_keys.extend(listing.keys)

        if depth < max_depth:
            for child in listing.common_prefixes:
                if child not in seen_prefixes:
                    queue.append((child, depth + 1))

        # If this prefix has many direct keys, pull more without delimiter.
        if listing.keys:
            direct_keys = s3_list_all_keys(prefix, max_total_keys=max_keys_per_prefix)
            all_keys.extend(direct_keys)

    # De-dupe while preserving order.
    deduped_keys = list(dict.fromkeys(all_keys))
    summary = summarize_keys(deduped_keys)

    usable = summary["model_key_count"] > 0

    return {
        "cycle": cycle.isoformat().replace("+00:00", "Z"),
        "ymd": ymd,
        "hh": hh,
        "bucket": BUCKET,
        "usable": usable,
        "prefixes_scanned": len(seen_prefixes),
        "summary": summary,
        "prefix_reports": prefix_reports,
    }


def choose_best_cycle(cycle_reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [c for c in cycle_reports if c.get("usable")]
    if not usable:
        return None

    def score(report: dict[str, Any]) -> tuple[int, int, int, str]:
        summary = report.get("summary", {})
        return (
            int(summary.get("refs_key_count", 0)) + int(summary.get("ens_key_count", 0)),
            int(summary.get("idx_key_count", 0)),
            int(summary.get("grib_key_count", 0)),
            str(report.get("cycle", "")),
        )

    return sorted(usable, key=score, reverse=True)[0]


def write_text_report(payload: dict[str, Any]) -> None:
    lines = []
    lines.append("RRFS / REFS AWS Inventory Scan")
    lines.append(f"Generated: {payload['generated_utc']}")
    lines.append(f"Bucket: s3://{BUCKET}")
    lines.append("")

    selected = payload.get("selected_cycle")
    if selected:
        lines.append(f"Selected cycle: {selected['cycle']}")
        lines.append("Selected cycle summary:")
        for k, v in selected.get("summary", {}).items():
            if not isinstance(v, list):
                lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append("Sample model keys:")
        for key in selected.get("summary", {}).get("sample_model_keys", [])[:40]:
            lines.append(f"  s3://{BUCKET}/{key}")
    else:
        lines.append("Selected cycle: NONE")
        lines.append("")
        lines.append("No usable GRIB/IDX keys were found in scanned prefixes.")
        lines.append("This does not necessarily mean RRFS/REFS is unavailable; it means the current guessed prefixes did not expose model keys.")
        lines.append("")

    lines.append("")
    lines.append("Cycle scan summaries:")
    for report in payload.get("cycles", []):
        summary = report.get("summary", {})
        lines.append(
            f"  {report.get('cycle')} | usable={report.get('usable')} | "
            f"prefixes={report.get('prefixes_scanned')} | "
            f"model_keys={summary.get('model_key_count')} | "
            f"idx={summary.get('idx_key_count')} | "
            f"grib={summary.get('grib_key_count')} | "
            f"refs={summary.get('refs_key_count')} | "
            f"ens={summary.get('ens_key_count')}"
        )

    OUT_TXT.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-cycles", type=int, default=DEFAULT_LOOKBACK_CYCLES)
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--max-prefixes-per-cycle", type=int, default=DEFAULT_MAX_PREFIXES_PER_CYCLE)
    parser.add_argument("--max-keys-per-prefix", type=int, default=DEFAULT_MAX_KEYS_PER_PREFIX)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if no usable RRFS/REFS cycle is found. Default is exit 0 so GitHub Actions can continue.",
    )
    args = parser.parse_args()

    generated = utc_now().isoformat().replace("+00:00", "Z")

    cycles = []
    for cycle in cycle_candidates(args.lookback_cycles):
        report = scan_cycle(
            cycle=cycle,
            max_depth=args.max_depth,
            max_prefixes_per_cycle=args.max_prefixes_per_cycle,
            max_keys_per_prefix=args.max_keys_per_prefix,
        )
        cycles.append(report)

        # Stop early if we found a very likely usable REFS/RRFS inventory.
        summary = report.get("summary", {})
        if (
            report.get("usable")
            and int(summary.get("grib_key_count", 0)) > 0
            and int(summary.get("idx_key_count", 0)) > 0
        ):
            break

    selected = choose_best_cycle(cycles)

    payload = {
        "generated_utc": generated,
        "bucket": BUCKET,
        "base_url": BASE_URL,
        "lookback_cycles": args.lookback_cycles,
        "max_depth": args.max_depth,
        "max_prefixes_per_cycle": args.max_prefixes_per_cycle,
        "max_keys_per_prefix": args.max_keys_per_prefix,
        "selected_cycle": selected,
        "cycles": cycles,
    }

    OUT_JSON.write_text(json.dumps(payload, indent=2))
    write_text_report(payload)

    if selected:
        OUT_SELECTED.write_text(json.dumps(selected, indent=2))
        print(f"Wrote {OUT_JSON}")
        print(f"Wrote {OUT_TXT}")
        print(f"Wrote {OUT_SELECTED}")
        print(f"Selected usable cycle: {selected['cycle']}")
        return 0

    OUT_SELECTED.write_text(json.dumps({"selected_cycle": None, "generated_utc": generated}, indent=2))
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_TXT}")
    print(f"Wrote {OUT_SELECTED}")
    print("No usable RRFS/REFS cycle found.")

    if args.strict:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
