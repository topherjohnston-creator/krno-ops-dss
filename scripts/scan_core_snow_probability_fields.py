from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests


DATA = Path("data")
DATA.mkdir(exist_ok=True)

FXX_HOURS = list(range(1, 49))


def latest_cycle_utc() -> datetime:
    """
    Use the most recent likely complete 6-hour NBM cycle.
    Lag one cycle to avoid partially available NOMADS files.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def core_idx_url(cycle: datetime, fxx: int) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
        f"blend.{ymd}/{hh}/core/blend.t{hh}z.core.f{fxx:03d}.co.grib2.idx"
    )


def fetch_idx(url: str) -> tuple[str, str | None]:
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        return response.text, None
    except Exception as exc:
        return "", str(exc)


def is_snow_related(line: str) -> bool:
    upper = line.upper()

    snow_terms = [
        "ASNOW",
        "SNOW",
        "WEASD",
        "SNOD",
        "SNOWC",
    ]

    if not any(term in upper for term in snow_terms):
        return False

    # Keep this focused on probability-like fields.
    probability_terms = [
        "PROB",
        "PROB >",
        "PROB <",
        "%",
    ]

    return any(term in upper for term in probability_terms)


def is_one_hour_accumulation(line: str) -> bool:
    lower = line.lower()

    patterns = [
        "1 hour acc fcst",
        "1-hour acc fcst",
        "0-1 hour acc fcst",
        "hour acc fcst",
    ]

    return any(pattern in lower for pattern in patterns)


def extract_threshold_hint(line: str) -> str:
    """
    Pull useful threshold text from IDX lines, if present.
    Examples:
      prob >0.254
      prob >2.54
      prob >12.7
    """
    lower = line.lower()

    match = re.search(r"prob\s*[<>]=?\s*([0-9]+(?:\.[0-9]+)?)", lower)
    if match:
        return match.group(0)

    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:kg/m\^2|mm|in)", lower)
    if match:
        return match.group(0)

    return ""


def classify_line(line: str) -> str:
    upper = line.upper()
    lower = line.lower()

    parts = []

    if "ASNOW" in upper:
        parts.append("ASNOW")
    if "WEASD" in upper:
        parts.append("WEASD")
    if "SNOD" in upper:
        parts.append("SNOD")
    if "SNOW" in upper and "ASNOW" not in upper:
        parts.append("SNOW")

    if "PROB" in upper:
        parts.append("PROB")

    if is_one_hour_accumulation(line):
        parts.append("1HR_ACC")
    elif "ACC FCST" in upper:
        parts.append("ACC")

    threshold = extract_threshold_hint(line)
    if threshold:
        parts.append(threshold)

    if "SURFACE" in upper:
        parts.append("SFC")

    return " | ".join(parts) if parts else "UNKNOWN"


def main() -> None:
    cycle = latest_cycle_utc()

    lines_out = []
    csv_rows = []

    header = [
        "NBM Core snow probability field scan",
        f"Cycle: {cycle:%Y-%m-%d %HZ}",
        "",
        "Goal: find official Core 1-hour snowfall probability fields for KRNO snow risk.",
        "Target thresholds:",
        "  - trace / measurable snow",
        "  - >0.5 in/hr",
        "  - >1.0 in/hr",
        "  - >2.0 in/hr",
        "",
        "=" * 100,
        "",
    ]

    lines_out.extend(header)

    total_matches = 0
    one_hour_matches = 0

    for fxx in FXX_HOURS:
        url = core_idx_url(cycle, fxx)
        idx_text, error = fetch_idx(url)

        lines_out.append("=" * 100)
        lines_out.append(f"f{fxx:03d}")
        lines_out.append(f"url: {url}")

        if error:
            lines_out.append(f"status: error: {error}")
            lines_out.append("")
            continue

        idx_lines = idx_text.splitlines()
        matches = [line for line in idx_lines if is_snow_related(line)]
        hourly_matches = [line for line in matches if is_one_hour_accumulation(line)]

        total_matches += len(matches)
        one_hour_matches += len(hourly_matches)

        lines_out.append(f"total_idx_lines: {len(idx_lines)}")
        lines_out.append(f"snow_probability_matches: {len(matches)}")
        lines_out.append(f"one_hour_snow_probability_matches: {len(hourly_matches)}")
        lines_out.append("")

        if matches:
            lines_out.append("All snow probability-like matches:")
            for line in matches:
                classification = classify_line(line)
                lines_out.append(f"  [{classification}] {line}")

                csv_rows.append(
                    {
                        "fxx": fxx,
                        "one_hour": is_one_hour_accumulation(line),
                        "classification": classification,
                        "idx_line": line,
                        "url": url,
                    }
                )

            lines_out.append("")

        if hourly_matches:
            lines_out.append("Focused 1-hour snow probability matches:")
            for line in hourly_matches:
                classification = classify_line(line)
                lines_out.append(f"  [{classification}] {line}")
            lines_out.append("")

    lines_out.extend(
        [
            "=" * 100,
            "SUMMARY",
            f"total_snow_probability_matches: {total_matches}",
            f"total_one_hour_snow_probability_matches: {one_hour_matches}",
            "",
            "Next step:",
            "Use the exact 1-hour ASNOW/SNOW probability lines found here to build",
            "scripts/build_real_core_snow_outputs.py.",
            "",
        ]
    )

    out_txt = DATA / "core_snow_probability_scan.txt"
    out_txt.write_text("\n".join(lines_out))

    out_csv = DATA / "core_snow_probability_scan.csv"
    with out_csv.open("w") as f:
        f.write("fxx,one_hour,classification,idx_line,url\n")
        for row in csv_rows:
            idx_line = row["idx_line"].replace('"', '""')
            classification = row["classification"].replace('"', '""')
            url = row["url"]
            f.write(
                f'{row["fxx"]},{row["one_hour"]},"{classification}","{idx_line}","{url}"\n'
            )

    print(f"Wrote {out_txt}")
    print(f"Wrote {out_csv}")
    print(f"Total snow probability matches: {total_matches}")
    print(f"Total 1-hour snow probability matches: {one_hour_matches}")


if __name__ == "__main__":
    main()
