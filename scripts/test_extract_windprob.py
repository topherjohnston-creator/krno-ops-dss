from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

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


def clean_attr_value(value: Any) -> Any:
    """Make xarray/cfgrib attrs JSON-safe."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def extract_windprob(cycle: datetime, fxx: int, search: str) -> dict[str, Any]:
    print(f"Extracting WINDPROB f{fxx:03d} using search: {search}")

    H = Herbie(
        cycle.strftime("%Y-%m-%d %H:%M"),
        model="nbm",
        product="co",
        fxx=fxx,
    )

    ds = H.xarray(search)

    output: dict[str, Any] = {
        "fxx": fxx,
        "search": search,
        "data_vars": [],
        "points": [],
        "dataset_attrs": {k: clean_attr_value(v) for k, v in ds.attrs.items()},
    }

    point = ds.herbie.nearest_points(points=[(KRNO_LON, KRNO_LAT)])

    for var in list(point.data_vars):
        da = point[var]
        raw_value = float(da.values.squeeze())

        attrs = {k: clean_attr_value(v) for k, v in da.attrs.items()}

        output["data_vars"].append(
            {
                "name": var,
                "attrs": attrs,
            }
        )

        output["points"].append(
            {
                "variable": var,
                "raw_value": raw_value,
                "attrs": attrs,
            }
        )

    return output


def main() -> None:
    cycle = latest_cycle_utc()

    searches = {
        # These are intentionally broad. We want to inspect what cfgrib returns.
        "windprob_any": ":WINDPROB:surface:",
        "windprob_prob_gt0": ":WINDPROB:surface:.*prob >0:",
    }

    results = []

    for fxx in [6, 12, 24]:
        for label, search in searches.items():
            try:
                result = extract_windprob(cycle, fxx, search)
                result["label"] = label
                result["status"] = "ok"
                results.append(result)
            except Exception as exc:
                results.append(
                    {
                        "label": label,
                        "fxx": fxx,
                        "search": search,
                        "status": "error",
                        "message": str(exc),
                    }
                )

    output = {
        "site": "KRNO",
        "source": "NBM CONUS via Herbie",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "purpose": "Inspect WINDPROB raw values and metadata before using it for probability-based wind risk.",
        "results": results,
    }

    out_path = OUT / "test_windprob_extract.json"
    out_path.write_text(json.dumps(output, indent=2))

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
