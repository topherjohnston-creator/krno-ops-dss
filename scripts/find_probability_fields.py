from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

from herbie import Herbie


OUT = Path("data")
OUT.mkdir(exist_ok=True)


def latest_cycle_utc() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def get_inventory_lines(cycle: datetime, fxx: int) -> list[str]:
    H = Herbie(
        cycle.strftime("%Y-%m-%d %H:%M"),
        model="nbm",
        product="co",
        fxx=fxx,
    )

    inv = H.inventory()

    if "search_this" in inv.columns:
        return inv["search_this"].astype(str).tolist()

    return inv.astype(str).apply(" | ".join, axis=1).tolist()


def main() -> None:
    cycle = latest_cycle_utc()

    fxx_values = [1, 3, 6, 12, 24]

    hazard_keywords = {
        "wind": ["GUST", "WIND"],
        "visibility": ["VIS"],
        "rain": ["APCP"],
        "snow": ["ASNOW", "SNOW"],
        "freezing_rain": ["FICEAC", "FRZSPR", "FZRA", "ICE"],
        "lightning": ["TSTM", "LTNG", "LIGHTNING"],
        "temperature": ["TMP", "MAXT", "MINT"],
        "flash_freeze": ["WETGLBT", "TMP", "APCP", "FRZSPR"],
    }

    probability_words = [
        "PROB",
        "prob",
        "probability",
        "PERCENT",
        ">",
        "<",
    ]

    full_report: list[str] = []
    summary_report: list[str] = []

    full_report.append(f"NBM CONUS probability field scan")
    full_report.append(f"Cycle: {cycle:%Y-%m-%d %HZ}")
    full_report.append("")
    summary_report.append(f"NBM CONUS probability field scan")
    summary_report.append(f"Cycle: {cycle:%Y-%m-%d %HZ}")
    summary_report.append("")

    for fxx in fxx_values:
        print(f"Scanning NBM CONUS f{fxx:03d}")
        lines = get_inventory_lines(cycle, fxx)

        inv_path = OUT / f"nbm_probability_scan_f{fxx:03d}_all.txt"
        inv_path.write_text("\n".join(lines))

        full_report.append("=" * 80)
        full_report.append(f"f{fxx:03d}")
        full_report.append("=" * 80)
        full_report.append("")

        summary_report.append("=" * 80)
        summary_report.append(f"f{fxx:03d}")
        summary_report.append("=" * 80)

        for hazard, keywords in hazard_keywords.items():
            matches = []

            for line in lines:
                upper = line.upper()
                has_hazard_keyword = any(k.upper() in upper for k in keywords)
                has_probability = any(p.upper() in upper for p in probability_words)

                if has_hazard_keyword and has_probability:
                    matches.append(line)

            full_report.append(f"[{hazard.upper()}]")
            if matches:
                for match in matches:
                    full_report.append(match)
            else:
                full_report.append("No probability-like fields found.")
            full_report.append("")

            summary_report.append(f"{hazard}: {len(matches)} probability-like matches")

        summary_report.append("")

    full_path = OUT / "nbm_probability_fields_full.txt"
    summary_path = OUT / "nbm_probability_fields_summary.txt"

    full_path.write_text("\n".join(full_report))
    summary_path.write_text("\n".join(summary_report))

    print(f"Wrote {full_path}")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
