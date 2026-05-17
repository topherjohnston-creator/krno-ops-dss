from __future__ import annotations

import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path


OUT = Path("data")
OUT.mkdir(exist_ok=True)


def latest_cycle_utc() -> datetime:
    """Return a safe recent NBM cycle.

    Step back one full NBM cycle to avoid requesting files that have not
    fully populated yet.
    """
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


def fetch_idx(url: str) -> str:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def main() -> None:
    cycle = latest_cycle_utc()

    fxx_values = [1, 3, 6, 12, 24, 48]

    keywords = [
        "GUST",
        "WIND",
        "WINDPROB",
        "PROB",
        "PERCENT",
        "PCTL",
        "PERCENTILE",
        "10",
        "25",
        "50",
        "75",
        "90",
        "MAX",
        "24 hour",
        "0-24",
        "1 day",
    ]

    full_report: list[str] = []
    summary_report: list[str] = []

    full_report.append("NBM QMD inventory scan")
    full_report.append(f"Cycle: {cycle:%Y-%m-%d %HZ}")
    full_report.append("")

    summary_report.append("NBM QMD inventory scan")
    summary_report.append(f"Cycle: {cycle:%Y-%m-%d %HZ}")
    summary_report.append("")

    for fxx in fxx_values:
        url = qmd_idx_url(cycle, fxx)

        print(f"Fetching QMD f{fxx:03d}: {url}")

        try:
            text = fetch_idx(url)
            status = "ok"
        except Exception as exc:
            text = ""
            status = f"error: {exc}"

        raw_path = OUT / f"nbm_qmd_f{fxx:03d}_inventory.txt"
        raw_path.write_text(text)

        lines = text.splitlines()

        matches = []
        for line in lines:
            upper = line.upper()
            if any(k.upper() in upper for k in keywords):
                matches.append(line)

        match_path = OUT / f"nbm_qmd_f{fxx:03d}_wind_matches.txt"
        match_path.write_text("\n".join(matches))

        full_report.append("=" * 80)
        full_report.append(f"f{fxx:03d}")
        full_report.append("=" * 80)
        full_report.append(f"URL: {url}")
        full_report.append(f"Status: {status}")
        full_report.append("")

        if matches:
            full_report.extend(matches)
        else:
            full_report.append("No wind/probability/percentile-like matches found.")

        full_report.append("")

        summary_report.append(
            f"f{fxx:03d}: status={status}; total_lines={len(lines)}; matches={len(matches)}"
        )

    full_path = OUT / "nbm_qmd_inventory_full.txt"
    summary_path = OUT / "nbm_qmd_inventory_summary.txt"

    full_path.write_text("\n".join(full_report))
    summary_path.write_text("\n".join(summary_report))

    print(f"Wrote {full_path}")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
