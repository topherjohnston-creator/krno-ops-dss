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


def line_matches_visibility(line: str) -> bool:
    upper = line.upper()

    visibility_terms = [
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

    return any(term in upper for term in visibility_terms)


def classify_line(line: str) -> dict[str, Any]:
    upper = line.upper()

    return {
        "has_percentile": "% LEVEL" in upper,
        "has_probability": ":PROB:" in upper or "PROBABILITY" in upper or "%:" in upper,
        "has_vis": ":VIS:" in upper or "VISIBILITY" in upper or "VSBY" in upper or "VISB" in upper,
        "has_ceiling": ":CEIL:" in upper or "CEILING" in upper or ":CIG:" in upper or "CIG" in upper,
        "has_flight_category": "IFR" in upper or "LIFR" in upper or "MVFR" in upper or "FLIGHT" in upper,
    }


def main() -> None:
    cycle = latest_cycle_utc()

    print(f"Scanning QMD visibility-related fields for cycle {cycle:%Y-%m-%d %HZ}")

    summary = {
        "site": "KRNO",
        "source": "NBM QMD IDX scan",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "hours": [],
    }

    report_lines = []

    for fxx in [1, 2, 3, 6, 9, 12, 18, 24]:
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
        matches = []

        for line in lines:
            if line_matches_visibility(line):
                classification = classify_line(line)
                matches.append(
                    {
                        "line": line,
                        **classification,
                    }
                )

        report_lines.append("=" * 100)
        report_lines.append(f"f{fxx:03d}")
        report_lines.append(f"url: {url}")
        report_lines.append(f"total_idx_lines: {len(lines)}")
        report_lines.append(f"visibility_related_matches: {len(matches)}")
        report_lines.append("")

        for match in matches:
            report_lines.append(match["line"])

        summary["hours"].append(
            {
                "fxx": fxx,
                "status": "ok",
                "url": url,
                "total_idx_lines": len(lines),
                "visibility_related_matches": len(matches),
                "matches": matches,
            }
        )

    txt_path = OUT / "qmd_visibility_inventory_scan.txt"
    json_path = OUT / "qmd_visibility_inventory_scan.json"

    txt_path.write_text("\n".join(report_lines))
    json_path.write_text(json.dumps(summary, indent=2))

    print(f"Wrote {txt_path}")
    print(f"Wrote {json_path}")

    total_matches = sum(hour.get("visibility_related_matches", 0) for hour in summary["hours"])
    print(f"Total visibility-related matches: {total_matches}")

    if total_matches == 0:
        print("No QMD visibility-related fields found. Visibility may need to use NBM Core probability fields instead of QMD.")


if __name__ == "__main__":
    main()
