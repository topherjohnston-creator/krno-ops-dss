from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from herbie import Herbie


OUT = Path("data")
OUT.mkdir(exist_ok=True)

KRNO_LAT = 39.4991
KRNO_LON = -119.7681

METERS_TO_SM = 0.000621371


def latest_cycle_utc() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def extract_point_value(cycle: datetime, fxx: int, search: str) -> tuple[str, float]:
    H = Herbie(
        cycle.strftime("%Y-%m-%d %H:%M"),
        model="nbm",
        product="co",
        fxx=fxx,
    )

    ds = H.xarray(search)
    point = ds.herbie.nearest_points(points=[(KRNO_LON, KRNO_LAT)])

    data_vars = list(point.data_vars)
    if not data_vars:
        raise RuntimeError(f"No data variables returned for search: {search}")

    var = data_vars[0]
    value = float(point[var].values.squeeze())

    return var, value


def visibility_impact_level(vis_sm: float) -> int:
    if vis_sm < 0.5:
        return 5
    if vis_sm < 1.0:
        return 4
    if vis_sm <= 3.0:
        return 3
    if vis_sm <= 5.0:
        return 2
    return 1


def main() -> None:
    cycle = latest_cycle_utc()

    results = []

    for fxx in range(1, 25):
        print(f"Extracting VIS f{fxx:03d} from {cycle:%Y-%m-%d %HZ}")

        variable, vis_m = extract_point_value(
            cycle=cycle,
            fxx=fxx,
            search=":VIS:surface:",
        )

        vis_sm = vis_m * METERS_TO_SM
        valid_time = cycle + timedelta(hours=fxx)

        results.append(
            {
                "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
                "valid_utc": valid_time.isoformat().replace("+00:00", "Z"),
                "fxx": fxx,
                "variable": variable,
                "visibility_m": round(vis_m, 1),
                "visibility_sm": round(vis_sm, 2),
                "impact_level": visibility_impact_level(vis_sm),
            }
        )

    min_vis = min(results, key=lambda item: item["visibility_sm"])

    output = {
        "site": "KRNO",
        "source": "NBM CONUS via Herbie",
        "variable": "VIS",
        "units_assumption": "GRIB visibility values are meters; converted to statute miles.",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "minimum_visibility": {
            "fxx": min_vis["fxx"],
            "valid_utc": min_vis["valid_utc"],
            "visibility_m": min_vis["visibility_m"],
            "visibility_sm": min_vis["visibility_sm"],
            "impact_level": min_vis["impact_level"],
        },
        "points": results,
    }

    out_path = OUT / "test_visibility_extract.json"
    out_path.write_text(json.dumps(output, indent=2))

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
