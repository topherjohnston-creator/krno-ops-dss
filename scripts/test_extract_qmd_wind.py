from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from herbie import Herbie


OUT = Path("data")
OUT.mkdir(exist_ok=True)

KRNO_LAT = 39.4991
KRNO_LON = -119.7681

MPS_TO_MPH = 2.2369362921
MPS_TO_KT = 1.9438444924


AIRPORT_THRESHOLDS_MPH = {
    "gt_30_mph": 30.0,
    "gt_45_mph": 45.0,
    "gt_58_mph": 58.0,
    "gt_65_mph": 65.0,
}


def latest_cycle_utc() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def clean_attr_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def extract_percentile_from_attrs(attrs: dict[str, Any]) -> float | None:
    """Try to identify the QMD percentile level from GRIB metadata."""
    candidates = [
        attrs.get("percentileValue"),
        attrs.get("GRIB_percentileValue"),
        attrs.get("GRIB_percentile"),
        attrs.get("percentile"),
    ]

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return float(candidate)
        except Exception:
            pass

    # Fallback: search all attribute text for strings like "50% level".
    text = " ".join(str(v) for v in attrs.values())
    match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*level", text, re.IGNORECASE)
    if match:
        return float(match.group(1))

    return None


def nearest_point_dataset(cycle: datetime, fxx: int, search: str):
    H = Herbie(
        cycle.strftime("%Y-%m-%d %H:%M"),
        model="nbm",
        product="qmd",
        fxx=fxx,
    )

    ds = H.xarray(search)
    point = ds.herbie.nearest_points(points=[(KRNO_LON, KRNO_LAT)])

    return ds, point


def extract_qmd_gust_percentiles(cycle: datetime) -> list[dict[str, Any]]:
    """Extract QMD 24-hour max gust percentile fields at KRNO."""
    search = ":GUST:10 m above ground:24 hour fcst:.*% level"

    ds, point = nearest_point_dataset(cycle, 24, search)

    rows = []

    for var in list(point.data_vars):
        da = point[var]
        attrs = {k: clean_attr_value(v) for k, v in da.attrs.items()}

        percentile = extract_percentile_from_attrs(attrs)

        gust_mps = float(da.values.squeeze())
        gust_mph = gust_mps * MPS_TO_MPH
        gust_kt = gust_mps * MPS_TO_KT

        rows.append(
            {
                "variable": var,
                "percentile": percentile,
                "gust_mps": round(gust_mps, 3),
                "gust_mph": round(gust_mph, 2),
                "gust_kt": round(gust_kt, 2),
                "attrs": attrs,
            }
        )

    # Remove rows where percentile could not be identified, then sort.
    rows = [r for r in rows if r["percentile"] is not None]
    rows.sort(key=lambda r: r["percentile"])

    return rows


def probability_exceeding_from_percentiles(
    percentile_rows: list[dict[str, Any]],
    threshold_mph: float,
) -> float:
    """Derive exact exceedance probability from QMD percentile curve.

    Percentile curve gives gust magnitude at percentile p.
    We estimate CDF(threshold) by linear interpolation between surrounding
    percentile levels, then return exceedance probability = 100 - CDF.

    Assumption:
    - Percentile rows are official QMD percentile levels.
    - Linear interpolation is used between adjacent percentile levels.
    """

    points = [
        (float(row["percentile"]), float(row["gust_mph"]))
        for row in percentile_rows
        if row.get("percentile") is not None and row.get("gust_mph") is not None
    ]

    points.sort(key=lambda item: item[0])

    if not points:
        raise RuntimeError("No percentile points available.")

    # If threshold is below or equal to the lowest percentile magnitude,
    # exceedance is essentially 100 minus the lowest percentile.
    lowest_p, lowest_v = points[0]
    if threshold_mph <= lowest_v:
        return round(100.0 - lowest_p, 1)

    # If threshold is above the highest percentile magnitude,
    # exceedance is essentially 0.
    highest_p, highest_v = points[-1]
    if threshold_mph >= highest_v:
        return round(max(0.0, 100.0 - highest_p), 1)

    # Find the bracketing percentile magnitudes.
    for (p0, v0), (p1, v1) in zip(points[:-1], points[1:]):
        if v0 <= threshold_mph <= v1:
            if v1 == v0:
                cdf_p = p1
            else:
                fraction = (threshold_mph - v0) / (v1 - v0)
                cdf_p = p0 + fraction * (p1 - p0)

            exceedance = 100.0 - cdf_p
            return round(max(0.0, min(100.0, exceedance)), 1)

    raise RuntimeError(f"Could not interpolate threshold {threshold_mph} mph.")


def get_p50(percentile_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    exact = [r for r in percentile_rows if float(r["percentile"]) == 50.0]
    if exact:
        return exact[0]

    # Fallback: nearest percentile to 50.
    if not percentile_rows:
        return None

    return min(percentile_rows, key=lambda r: abs(float(r["percentile"]) - 50.0))


def main() -> None:
    cycle = latest_cycle_utc()

    print(f"Extracting QMD wind percentile curve for cycle {cycle:%Y-%m-%d %HZ}")

    percentile_rows = extract_qmd_gust_percentiles(cycle)
    p50 = get_p50(percentile_rows)

    exact_probabilities = {}

    for key, threshold_mph in AIRPORT_THRESHOLDS_MPH.items():
        exact_probabilities[key] = {
            "threshold_mph": threshold_mph,
            "threshold_mps": round(threshold_mph / MPS_TO_MPH, 3),
            "exceedance_probability_percent": probability_exceeding_from_percentiles(
                percentile_rows,
                threshold_mph,
            ),
            "method": "linear interpolation across official QMD percentile levels",
        }

    output = {
        "site": "KRNO",
        "source": "NBM QMD via Herbie",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "valid_utc": (cycle + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        "variable": "QMD 24-hour maximum 10-meter wind gust percentile curve",
        "display_value": {
            "label": "24-hr max gust",
            "source_percentile": p50["percentile"] if p50 else None,
            "gust_mps": p50["gust_mps"] if p50 else None,
            "gust_mph": p50["gust_mph"] if p50 else None,
            "gust_kt": p50["gust_kt"] if p50 else None,
        },
        "airport_threshold_probabilities": exact_probabilities,
        "percentile_curve": [
            {
                "percentile": row["percentile"],
                "gust_mps": row["gust_mps"],
                "gust_mph": row["gust_mph"],
                "gust_kt": row["gust_kt"],
                "variable": row["variable"],
            }
            for row in percentile_rows
        ],
        "methodology": (
            "Displayed wind magnitude is the QMD 50th percentile of the 24-hour maximum "
            "10-meter wind gust. Exact airport exceedance probabilities for 30, 45, 58, "
            "and 65 mph are derived by linear interpolation across the official QMD "
            "percentile curve."
        ),
    }

    out_path = OUT / "test_qmd_wind_percentiles.json"
    out_path.write_text(json.dumps(output, indent=2))

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
