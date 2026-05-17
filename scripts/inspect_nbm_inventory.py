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

    # Use previous cycle for safety.
    cycle = cycle - timedelta(hours=6)

    return cycle


def main() -> None:
    cycle = latest_cycle_utc()

    print(f"Inspecting NBM CONUS inventory for cycle: {cycle:%Y-%m-%d %HZ}")

    # Herbie NBM valid products include:
    # pr = Puerto Rico
    # gu = Guam
    # hi = Hawaii
    # co = CONUS
    # ak = Alaska
    #
    # For KRNO, use CONUS: product="co"
    H = Herbie(
        cycle.strftime("%Y-%m-%d %H:%M"),
        model="nbm",
        product="co",
        fxx=1,
    )

    inv = H.inventory()

    out_csv = OUT / "nbm_conus_f001_inventory.csv"
    out_txt = OUT / "nbm_conus_f001_search_this.txt"

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
        "VIS",
        "APCP",
        "SNOW",
        "TMP",
        "TCDC",
        "TSTM",
        "LTNG",
        "ICE",
        "FZRA",
        "FRZR",
        "PROB",
        "PERCENT",
        "MAX",
        "MIN",
    ]

    print("\nPossible relevant inventory lines:")
    for line in lines:
        if any(k in line.upper() for k in keywords):
            print(line)


if __name__ == "__main__":
    main()
