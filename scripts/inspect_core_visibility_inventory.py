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


def core_idx_url(cycle: datetime, fxx: int) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
        f"blend.{ymd}/{hh}/core/blend.t{hh}z.core.f{fxx:03d}.co.grib2.idx"
    )


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def line_matches_visibility(line: str) -> bool:
    upper = line.upper()

    terms = [
        ":VIS:",
        "VISIBILITY",
        "VSBY",
        "VISB",
        "IFR",
        "LIFR",
        "MVFR",
        "FLIGHT",
        "CIG",
        "CEILING",
    ]

    return any(term in upper for term in terms)


def line_matches_probability(line: str) -> bool:
    upper = line.upper()

    probability_terms = [
        ":PROB:",
        "PROBABILITY",
        "PROB",
        "%",
    ]

    return any(term in upper for term in probability_terms)


def classify_line(line: str) -> dict[str, Any]:
    upper = line.upper()

    return {
        "has_probability": line_matches_probability(line),
        "has_vis": ":VIS:" in upper or "VISIBILITY" in upper or "VSBY" in upper or "VISB" in upper,
        "has_ceiling": ":CEIL:" in upper or "CEILING" in upper or ":CIG:" in upper or "CIG" in upper,
        "has_flight_category": "IFR" in upper or "LIFR" in upper or "MVFR" in upper or "FLIGHT" in upper,
        "line": line,
    }


def main() -> None:
    cycle = latest_cycle_utc()

    print(f"Scanning NBM Core visibility fields for cycle {cycle:%Y-%m-%d %HZ}")

    hours_to_scan = list(range(1, 25))

    summary = {
        "site": "KRNO",
        "source": "NBM Core IDX scan",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "hours": [],
    }

    report_lines = []

    for fxx in hours_to_scan:
        print(f"Scanning Core f{fxx:03d}")

        url = core_idx_url(cycle, fxx)

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

        all_visibility_matches = []
        probability_visibility_matches = []

        for line in lines:
            if line_matches_visibility(line):
                item = classify_line(line)
                all_visibility_matches.append(item)

                if item["has_probability"]:
                    probability_visibility_matches.append(item)

        report_lines.append("=" * 100)
        report_lines.append(f"f{fxx:03d}")
        report_lines.append(f"url: {url}")
        report_lines.append(f"total_idx_lines: {len(lines)}")
        report_lines.append(f"visibility_related_matches: {len(all_visibility_matches)}")
        report_lines.append(f"probability_visibility_matches: {len(probability_visibility_matches)}")
        report_lines.append("")
        report_lines.append("PROBABILITY VISIBILITY MATCHES:")
        report_lines.append("-" * 100)

        for match in probability_visibility_matches:
            report_lines.append(match["line"])

        report_lines.append("")
        report_lines.append("ALL VISIBILITY MATCHES:")
        report_lines.append("-" * 100)

        for match in all_visibility_matches:
            report_lines.append(match["line"])

        summary["hours"].append(
            {
                "fxx": fxx,
                "status": "ok",
                "url": url,
                "total_idx_lines": len(lines),
                "visibility_related_matches": len(all_visibility_matches),
                "probability_visibility_matches": len(probability_visibility_matches),
                "probability_matches": probability_visibility_matches,
                "all_matches": all_visibility_matches,
            }
        )

    txt_path = OUT / "core_visibility_inventory_scan.txt"
    json_path = OUT / "core_visibility_inventory_scan.json"

    txt_path.write_text("\n".join(report_lines))
    json_path.write_text(json.dumps(summary, indent=2))

    total_vis = sum(hour.get("visibility_related_matches", 0) for hour in summary["hours"])
    total_prob = sum(hour.get("probability_visibility_matches", 0) for hour in summary["hours"])

    print(f"Wrote {txt_path}")
    print(f"Wrote {json_path}")
    print(f"Total visibility-related matches: {total_vis}")
    print(f"Total probability visibility matches: {total_prob}")


if __name__ == "__main__":
    main()
