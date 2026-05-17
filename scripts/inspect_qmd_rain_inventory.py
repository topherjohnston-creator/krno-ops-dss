from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests


OUT = Path("data")
OUT.mkdir(exist_ok=True)


def latest_cycle_utc() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def qmd_idx_url(cycle: datetime, fxx: int) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
        f"blend.{ymd}/{hh}/qmd/blend.t{hh}z.qmd.f{fxx:03d}.co.grib2.idx"
    )


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def line_matches_rain(line: str) -> bool:
    upper = line.upper()

    terms = [
        ":APCP:",
        "APCP",
        "PRECIP",
        "PRECIPITATION",
        "QPF",
        "RAIN",
        "WATER",
        "WEASD",
        "PWAT",
    ]

    return any(term in upper for term in terms)


def line_matches_percentile(line: str) -> bool:
    upper = line.upper()
    return "% LEVEL" in upper or "PERCENTILE" in upper or "PCTL" in upper


def line_matches_probability(line: str) -> bool:
    upper = line.upper()
    return ":PROB:" in upper or "PROB" in upper or "PROBABILITY" in upper


def classify_line(line: str) -> dict[str, Any]:
    upper = line.upper()

    return {
        "has_percentile": line_matches_percentile(line),
        "has_probability": line_matches_probability(line),
        "has_apcp": ":APCP:" in upper or "APCP" in upper,
        "has_qpf": "QPF" in upper,
        "has_rain": "RAIN" in upper,
        "has_precip": "PRECIP" in upper or "PRECIPITATION" in upper,
        "has_water": "WATER" in upper,
        "line": line,
    }


def main() -> None:
    cycle = latest_cycle_utc()

    print(f"Scanning NBM QMD rain/precip fields for cycle {cycle:%Y-%m-%d %HZ}")

    hours_to_scan = list(range(1, 25))

    summary = {
        "site": "KRNO",
        "source": "NBM QMD IDX scan",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "hours": [],
    }

    report_lines = []

    for fxx in hours_to_scan:
        print(f"Scanning QMD f{fxx:03d}")

        url = qmd_idx_url(cycle, fxx)

        try:
            idx_text = fetch_text(url)
        except Exception as exc:
            summary["hours"].append(
                {
                    "fxx": fxx,
                    "status": "error",
                    "url": url,
                    "message": str(exc),
                    "matches": [],
                }
            )
            continue

        lines = idx_text.splitlines()

        all_rain_matches = []
        percentile_rain_matches = []
        probability_rain_matches = []

        for line in lines:
            if line_matches_rain(line):
                item = classify_line(line)
                all_rain_matches.append(item)

                if item["has_percentile"]:
                    percentile_rain_matches.append(item)

                if item["has_probability"]:
                    probability_rain_matches.append(item)

        report_lines.append("=" * 100)
        report_lines.append(f"f{fxx:03d}")
        report_lines.append(f"url: {url}")
        report_lines.append(f"total_idx_lines: {len(lines)}")
        report_lines.append(f"rain_related_matches: {len(all_rain_matches)}")
        report_lines.append(f"percentile_rain_matches: {len(percentile_rain_matches)}")
        report_lines.append(f"probability_rain_matches: {len(probability_rain_matches)}")
        report_lines.append("")

        report_lines.append("PERCENTILE RAIN/PRECIP MATCHES:")
        report_lines.append("-" * 100)
        for match in percentile_rain_matches:
            report_lines.append(match["line"])

        report_lines.append("")
        report_lines.append("PROBABILITY RAIN/PRECIP MATCHES:")
        report_lines.append("-" * 100)
        for match in probability_rain_matches:
            report_lines.append(match["line"])

        report_lines.append("")
        report_lines.append("ALL RAIN/PRECIP MATCHES:")
        report_lines.append("-" * 100)
        for match in all_rain_matches:
            report_lines.append(match["line"])

        summary["hours"].append(
            {
                "fxx": fxx,
                "status": "ok",
                "url": url,
                "total_idx_lines": len(lines),
                "rain_related_matches": len(all_rain_matches),
                "percentile_rain_matches": len(percentile_rain_matches),
                "probability_rain_matches": len(probability_rain_matches),
                "percentile_matches": percentile_rain_matches,
                "probability_matches": probability_rain_matches,
                "all_matches": all_rain_matches,
            }
        )

    txt_path = OUT / "qmd_rain_inventory_scan.txt"
    json_path = OUT / "qmd_rain_inventory_scan.json"

    txt_path.write_text("\n".join(report_lines))
    json_path.write_text(json.dumps(summary, indent=2))

    total_rain = sum(hour.get("rain_related_matches", 0) for hour in summary["hours"])
    total_percentile = sum(hour.get("percentile_rain_matches", 0) for hour in summary["hours"])
    total_probability = sum(hour.get("probability_rain_matches", 0) for hour in summary["hours"])

    print(f"Wrote {txt_path}")
    print(f"Wrote {json_path}")
    print(f"Total rain/precip-related matches: {total_rain}")
    print(f"Total percentile rain/precip matches: {total_percentile}")
    print(f"Total probability rain/precip matches: {total_probability}")


if __name__ == "__main__":
    main()
