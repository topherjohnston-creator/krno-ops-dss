from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from herbie import Herbie


OUT = Path("data")
OUT.mkdir(exist_ok=True)


def latest_cycle_utc() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    # NBM cycles are commonly available at 00/06/12/18Z.
    # Step back 6 hours to avoid grabbing a cycle that has not fully populated yet.
    safe = now.replace(hour=(now.hour // 6) * 6) 
    if now.hour - safe.hour < 3:
        safe = safe.replace(hour=safe.hour - 6)

    return safe


def main() -> None:
    cycle = latest_cycle_utc()

    print(f"Inspecting NBM Core inventory for cycle: {cycle:%Y-%m-%d %HZ}")

    # Start with fxx=1 to identify hourly variables.
    H = Herbie(
        cycle.strftime("%Y-%m-%d %H:%M"),
        model="nbm",
        product="core",
        fxx=1,
    )

    inv = H.inventory()

    out_csv = OUT / "nbm_core_f001_inventory.csv"
    out_txt = OUT / "nbm_core_f001_search_this.txt"

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
    ]

    print("\nPossible relevant inventory lines:")
    for line in lines:
        if any(k in line.upper() for k in keywords):
            print(line)


if __name__ == "__main__":
    main()
