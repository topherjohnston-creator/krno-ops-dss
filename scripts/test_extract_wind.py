from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from herbie import Herbie


OUT = Path("data")
OUT.mkdir(exist_ok=True)

KRNO_LAT = 39.4991
KRNO_LON = -119.7681


def latest_cycle_utc() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def main() -> None:
    cycle = latest_cycle_utc()

    results = []

    for fxx in range(1, 7):
        print(f"Extracting GUST f{fxx:03d} from {cycle:%Y-%m-%d %HZ}")

        H = Herbie(
            cycle.strftime("%Y-%m-%d %H:%M"),
            model="nbm",
            product="co",
            fxx=fxx,
        )

        ds = H.xarray(":GUST:10 m above ground:")
        point = ds.herbie.nearest_points(points=[(KRNO_LON, KRNO_LAT)])

        # Try to find the gust variable automatically.
        data_vars = list(point.data_vars)
        gust_var = data_vars[0]

        value = float(point[gust_var].values.squeeze())

        results.append(
            {
                "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
                "fxx": fxx,
                "variable": gust_var,
                "gust_raw": value,
                "note": "Raw units from GRIB; verify m/s vs kt before operational use.",
            }
        )

    output = {
        "site": "KRNO",
        "source": "NBM CONUS via Herbie",
        "variable": "GUST",
        "points": results,
    }

    out_path = OUT / "test_wind_extract.json"
    out_path.write_text(json.dumps(output, indent=2))

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
