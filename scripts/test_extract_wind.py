from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from herbie import Herbie


OUT = Path("data")
OUT.mkdir(exist_ok=True)

KRNO_LAT = 39.4991
KRNO_LON = -119.7681

MPS_TO_MPH = 2.2369362921
MPS_TO_KT = 1.9438444924


def latest_cycle_utc() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def main() -> None:
    cycle = latest_cycle_utc()

    results = []

    for fxx in range(1, 25):
        print(f"Extracting GUST f{fxx:03d} from {cycle:%Y-%m-%d %HZ}")

        H = Herbie(
            cycle.strftime("%Y-%m-%d %H:%M"),
            model="nbm",
            product="co",
            fxx=fxx,
        )

        ds = H.xarray(":GUST:10 m above ground:")
        point = ds.herbie.nearest_points(points=[(KRNO_LON, KRNO_LAT)])

        data_vars = list(point.data_vars)
        gust_var = data_vars[0]

        gust_mps = float(point[gust_var].values.squeeze())
        gust_mph = gust_mps * MPS_TO_MPH
        gust_kt = gust_mps * MPS_TO_KT

        valid_time = cycle + timedelta(hours=fxx)

        results.append(
            {
                "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
                "valid_utc": valid_time.isoformat().replace("+00:00", "Z"),
                "fxx": fxx,
                "variable": gust_var,
                "gust_mps": round(gust_mps, 1),
                "gust_mph": round(gust_mph, 1),
                "gust_kt": round(gust_kt, 1),
            }
        )

    output = {
        "site": "KRNO",
        "source": "NBM CONUS via Herbie",
        "variable": "GUST",
        "units_assumption": "GRIB gust values are meters per second; converted to mph and knots.",
        "points": results,
    }

    out_path = OUT / "test_wind_extract.json"
    out_path.write_text(json.dumps(output, indent=2))

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
