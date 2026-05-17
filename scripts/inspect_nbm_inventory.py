from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

from herbie import Herbie


OUT = Path("data")
OUT.mkdir(exist_ok=True)


def latest_cycle_utc() -> datetime:
    """Return a safe recent NBM cycle.

    NBM cycles are generally 00/06/12/18Z. Step back one full cycle
    to avoid requesting a cycle that has not fully populated yet.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def write_inventory(cycle: datetime, fxx: int) -> None:
    print(f"Inspecting NBM CONUS inventory for cycle: {cycle:%Y-%m-%d %HZ} f{fxx:03d}")

    H = Herbie(
        cycle.strftime("%Y-%m-%d %H:%M"),
        model="nbm",
        product="co",
        fxx=fxx,
    )

    inv = H.inventory()

    out_csv = OUT / f"nbm_conus_f{fxx:03d}_inventory.csv"
    out_txt = OUT / f"nbm_conus_f{fxx:03d}_search_this.txt"

    inv.to_csv(out_csv, index=False)

    if "search_this" in inv.columns:
        lines = inv["search_this"].astype(str).tolist()
    else:
        lines = inv.astype(str).apply(" | ".join, axis=1).tolist()

    out_txt.write_text("\n".join(lines))

    print(f"Wrote {out_csv}")
    print(f"Wrote {out_txt}")

    keywords = [
        "GUST",
        "WIND",
        "MAX",
        "MX",
        "MAXGUST",
        "MAX WIND",
        "WIND GUST",
        "10 m above ground",
        "24 hour",
        "0-24",
        "1-24",
        "acc",
    ]

    print(f"\nPossible relevant f{fxx:03d} inventory lines:")
    for line in lines:
        upper = line.upper()
        if any(k in upper for k in keywords):
            print(line)


def main() -> None:
    cycle = latest_cycle_utc()

    # f001: hourly variables for timing
    # f024: likely location for 24-hour max/min variables
    for fxx in [1, 24]:
        write_inventory(cycle, fxx)


if __name__ == "__main__":
    main()
