from __future__ import annotations

import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path


OUT = Path("data")
OUT.mkdir(exist_ok=True)


def safe_cycles_utc() -> list[datetime]:
    """Return several recent 00/06/12/18Z cycles.

    QMD only runs at 00/06/12/18Z. Probe several cycles because some
    servers may lag or retain different windows.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    base = now.replace(hour=cycle_hour)

    return [base - timedelta(hours=6 * i) for i in range(1, 9)]


def candidate_idx_urls(cycle: datetime, fxx: int) -> list[str]:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    fname = f"blend.t{hh}z.qmd.f{fxx:03d}.co.grib2.idx"

    return [
        # NOMADS likely paths
        f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.{ymd}/{hh}/qmd/{fname}",
        f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.{ymd}/{hh}/{fname}",
        f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.{ymd}/qmd/{fname}",
        f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.{ymd}/{fname}",

        # AWS public bucket likely paths
        f"https://noaa-nbm-pds.s3.amazonaws.com/blend.{ymd}/{hh}/qmd/{fname}",
        f"https://noaa-nbm-pds.s3.amazonaws.com/blend.{ymd}/{hh}/{fname}",
        f"https://noaa-nbm-pds.s3.amazonaws.com/blend.{ymd}/qmd/{fname}",
        f"https://noaa-nbm-pds.s3.amazonaws.com/blend.{ymd}/{fname}",
    ]


def fetch_first_available_idx(cycle: datetime, fxx: int) -> tuple[str | None, str | None, list[str]]:
    attempts = []

    for url in candidate_idx_urls(cycle, fxx):
        try:
            response = requests.get(url, timeout=45)
            attempts.append(f"{response.status_code} {url}")

            if response.status_code == 200 and response.text.strip():
                return url, response.text, attempts

        except Exception as exc:
            attempts.append(f"ERROR {url} :: {exc}")

    return None, None, attempts


def main() -> None:
    fxx_values = [1, 3, 6, 12, 24, 48]

    keywords = [
        "GUST",
        "WIND",
        "WINDPROB",
        "PROB",
        "PERCENT",
        "PCTL",
        "PERCENTILE",
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
    probe_report: list[str] = []

    full_report.append("NBM QMD inventory scan")
    full_report.append("")
    summary_report.append("NBM QMD inventory scan")
    summary_report.append("")

    cycles = safe_cycles_utc()

    for fxx in fxx_values:
        found_url = None
        found_text = None
        found_cycle = None
        all_attempts = []

        for cycle in cycles:
            print(f"Probing QMD f{fxx:03d} for cycle {cycle:%Y-%m-%d %HZ}")
            url, text, attempts = fetch_first_available_idx(cycle, fxx)
            all_attempts.extend([f"Cycle {cycle:%Y-%m-%d %HZ}"] + attempts + [""])

            if url and text:
                found_url = url
                found_text = text
                found_cycle = cycle
                break

        probe_path = OUT / f"nbm_qmd_f{fxx:03d}_url_probe.txt"
        probe_path.write_text("\n".join(all_attempts))

        probe_report.append("=" * 80)
        probe_report.append(f"f{fxx:03d}")
        probe_report.extend(all_attempts)

        if found_text is None or found_cycle is None or found_url is None:
            raw_path = OUT / f"nbm_qmd_f{fxx:03d}_inventory.txt"
            match_path = OUT / f"nbm_qmd_f{fxx:03d}_wind_matches.txt"
            raw_path.write_text("")
            match_path.write_text("")

            summary_report.append(f"f{fxx:03d}: NOT FOUND after probing {len(cycles)} cycles")
            full_report.append("=" * 80)
            full_report.append(f"f{fxx:03d}")
            full_report.append("=" * 80)
            full_report.append("NOT FOUND")
            full_report.append("")
            continue

        lines = found_text.splitlines()

        raw_path = OUT / f"nbm_qmd_f{fxx:03d}_inventory.txt"
        raw_path.write_text(found_text)

        matches = []
        for line in lines:
            upper = line.upper()
            if any(k.upper() in upper for k in keywords):
                matches.append(line)

        match_path = OUT / f"nbm_qmd_f{fxx:03d}_wind_matches.txt"
        match_path.write_text("\n".join(matches))

        summary_report.append(
            f"f{fxx:03d}: FOUND cycle={found_cycle:%Y-%m-%d %HZ}; "
            f"total_lines={len(lines)}; matches={len(matches)}; url={found_url}"
        )

        full_report.append("=" * 80)
        full_report.append(f"f{fxx:03d}")
        full_report.append("=" * 80)
        full_report.append(f"Cycle: {found_cycle:%Y-%m-%d %HZ}")
        full_report.append(f"URL: {found_url}")
        full_report.append("")

        if matches:
            full_report.extend(matches)
        else:
            full_report.append("No wind/probability/percentile-like matches found.")

        full_report.append("")

    (OUT / "nbm_qmd_inventory_full.txt").write_text("\n".join(full_report))
    (OUT / "nbm_qmd_inventory_summary.txt").write_text("\n".join(summary_report))
    (OUT / "nbm_qmd_url_probe_full.txt").write_text("\n".join(probe_report))

    print("Wrote QMD inventory/probe outputs")


if __name__ == "__main__":
    main()
