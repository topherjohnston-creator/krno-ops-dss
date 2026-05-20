from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests


DATA = Path("data")
DATA.mkdir(exist_ok=True)

BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod"


def floor_to_6hr_cycle() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    return now.replace(hour=cycle_hour)


def qmd_idx_url(cycle: datetime, fxx: int) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return (
        f"{BASE}/blend.{ymd}/{hh}/qmd/"
        f"blend.t{hh}z.qmd.f{fxx:03d}.co.grib2.idx"
    )


def fetch_text(url: str) -> str | None:
    try:
        response = requests.get(url, timeout=45)
        if response.status_code != 200:
            return None
        return response.text
    except Exception:
        return None


def best_available_qmd_cycle() -> datetime:
    latest = floor_to_6hr_cycle()

    for lag in range(0, 49, 6):
        cycle = latest - timedelta(hours=lag)
        text = fetch_text(qmd_idx_url(cycle, 24))
        if text:
            return cycle

    raise RuntimeError("No available QMD f024 IDX found in the last 48 hours.")


def line_is_relevant(line: str) -> bool:
    upper = line.upper()

    terms = [
        "GUST",
        "WIND",
        "10 M",
        "10M",
        "MAX",
        "MAXIMUM",
        "24 HOUR",
        "0-24",
        "MEAN",
        "% LEVEL",
    ]

    return any(term in upper for term in terms)


def main() -> None:
    cycle = best_available_qmd_cycle()

    print(f"Scanning QMD wind inventory for cycle {cycle:%Y-%m-%d %HZ}")

    output_lines = []
    output_lines.append(f"QMD wind inventory scan")
    output_lines.append(f"Cycle: {cycle:%Y-%m-%d %HZ}")
    output_lines.append("")

    for fxx in [1, 2, 3, 6, 9, 12, 18, 24, 30, 36, 42, 48]:
        url = qmd_idx_url(cycle, fxx)
        text = fetch_text(url)

        output_lines.append("=" * 100)
        output_lines.append(f"f{fxx:03d}")
        output_lines.append(f"url: {url}")

        if not text:
            output_lines.append("status: missing/unavailable")
            output_lines.append("")
            continue

        lines = text.splitlines()
        relevant = [line for line in lines if line_is_relevant(line)]

        output_lines.append(f"total_idx_lines: {len(lines)}")
        output_lines.append(f"relevant_wind_lines: {len(relevant)}")
        output_lines.append("")

        for line in relevant:
            output_lines.append(line)

        output_lines.append("")

    report = "\n".join(output_lines)

    print(report)

    out_path = DATA / "qmd_wind_inventory_scan.txt"
    out_path.write_text(report)

    print("")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
