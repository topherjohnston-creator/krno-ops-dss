from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests


DATA = Path("data")
DATA.mkdir(exist_ok=True)

# Core 6-hour freezing rain probability should be on 6-hour forecast steps.
FXX_HOURS = [6, 12, 18, 24, 30, 36, 42, 48]


def latest_cycle_utc() -> datetime:
    """
    Use a likely-complete NBM cycle.
    Lag by 12 hours to avoid NOMADS partial-cycle 404 errors.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=12)


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


def is_fzra_related(line: str) -> bool:
    upper = line.upper()

    fzra_terms = [
        "FZRA",
        "FRZR",
        "CFRZR",
        "FREEZING",
        "ICE",
    ]

    if not any(term in upper for term in fzra_terms):
        return False

    probability_terms = [
        "PROB",
        "PROB >",
        "PROB <",
        "%",
    ]

    return any(term in upper for term in probability_terms)


def is_six_hour_accumulation(line: str) -> bool:
    lower = line.lower()

    patterns = [
        "6 hour acc fcst",
        "6-hour acc fcst",
        "0-6 hour acc fcst",
        "hour acc fcst",
    ]

    return any(pattern in lower for pattern in patterns)


def extract_threshold_hint(line: str) -> str:
    lower = line.lower()

    match = re.search(r"prob\s*[<>]=?\s*([0-9]+(?:\.[0-9]+)?)", lower)
    if match:
        return match.group(0)

    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:kg/m\^2|mm|m|in)", lower)
    if match:
        return match.group(0)

    return ""


def classify_line(line: str) -> str:
    upper = line.upper()

    parts = []

    for term in ["FZRA", "FRZR", "CFRZR", "ICE"]:
        if term in upper:
            parts.append(term)

    if "PROB" in upper:
        parts.append("PROB")

    if is_six_hour_accumulation(line):
        parts.append("6HR_ACC")
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

    lines_out.extend(
        [
            "NBM Core freezing rain probability field scan",
            f"Cycle: {cycle:%Y-%m-%d %HZ}",
            "",
            "Goal: find official Core 6-hour freezing rain probability fields for KRNO ground-ops risk.",
            "Target thresholds, if available:",
            "  - trace / measurable freezing rain",
            "  - >0.01 in / 6 hr",
            "  - >0.03 in / 6 hr",
            "  - >0.05 in / 6 hr",
            "  - >0.10 in / 6 hr",
            "",
            "=" * 100,
            "",
        ]
    )

    total_matches = 0
    six_hour_matches = 0

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
        matches = [line for line in idx_lines if is_fzra_related(line)]
        six_hr_matches = [line for line in matches if is_six_hour_accumulation(line)]

        total_matches += len(matches)
        six_hour_matches += len(six_hr_matches)

        lines_out.append(f"total_idx_lines: {len(idx_lines)}")
        lines_out.append(f"fzra_probability_matches: {len(matches)}")
        lines_out.append(f"six_hour_fzra_probability_matches: {len(six_hr_matches)}")
        lines_out.append("")

        if matches:
            lines_out.append("All freezing-rain probability-like matches:")
            for line in matches:
                classification = classify_line(line)
                lines_out.append(f"  [{classification}] {line}")

                csv_rows.append(
                    {
                        "fxx": fxx,
                        "six_hour": is_six_hour_accumulation(line),
                        "classification": classification,
                        "idx_line": line,
                        "url": url,
                    }
                )

            lines_out.append("")

        if six_hr_matches:
            lines_out.append("Focused 6-hour freezing-rain probability matches:")
            for line in six_hr_matches:
                classification = classify_line(line)
                lines_out.append(f"  [{classification}] {line}")
            lines_out.append("")

    lines_out.extend(
        [
            "=" * 100,
            "SUMMARY",
            f"total_fzra_probability_matches: {total_matches}",
            f"total_six_hour_fzra_probability_matches: {six_hour_matches}",
            "",
            "Next step:",
            "Use the exact 6-hour freezing-rain probability lines found here to build",
            "scripts/build_real_core_fzra_outputs.py.",
            "",
        ]
    )

    out_txt = DATA / "core_fzra_probability_scan.txt"
    out_txt.write_text("\n".join(lines_out))

    out_csv = DATA / "core_fzra_probability_scan.csv"
    with out_csv.open("w") as f:
        f.write("fxx,six_hour,classification,idx_line,url\n")
        for row in csv_rows:
            idx_line = row["idx_line"].replace('"', '""')
            classification = row["classification"].replace('"', '""')
            url = row["url"]
            f.write(
                f'{row["fxx"]},{row["six_hour"]},"{classification}","{idx_line}","{url}"\n'
            )

    print(f"Wrote {out_txt}")
    print(f"Wrote {out_csv}")
    print(f"Total freezing-rain probability matches: {total_matches}")
    print(f"Total 6-hour freezing-rain probability matches: {six_hour_matches}")


if __name__ == "__main__":
    main()
