from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import requests
import xarray as xr


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


def qmd_urls(cycle: datetime, fxx: int) -> tuple[str, str]:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    base = (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
        f"blend.{ymd}/{hh}/qmd/blend.t{hh}z.qmd.f{fxx:03d}.co.grib2"
    )
    return base, base + ".idx"


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def parse_idx(idx_text: str) -> list[dict[str, Any]]:
    rows = []
    lines = idx_text.splitlines()

    for i, line in enumerate(lines):
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue

        msg_num = int(parts[0])
        start_byte = int(parts[1])

        if i + 1 < len(lines):
            next_start = int(lines[i + 1].split(":", 2)[1])
            end_byte = next_start - 1
        else:
            end_byte = None

        rows.append(
            {
                "msg_num": msg_num,
                "start_byte": start_byte,
                "end_byte": end_byte,
                "line": line,
            }
        )

    return rows


def percentile_from_idx_line(line: str) -> float | None:
    match = re.search(r":(\d+(?:\.\d+)?)%\s+level", line)
    if not match:
        return None
    return float(match.group(1))


def select_qmd_gust_percentile_messages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []

    for row in rows:
        line = row["line"]

        if ":GUST:10 m above ground:24 hour fcst:" not in line:
            continue

        if "% level" not in line:
            continue

        percentile = percentile_from_idx_line(line)
        if percentile is None:
            continue

        new_row = dict(row)
        new_row["percentile"] = percentile
        selected.append(new_row)

    selected.sort(key=lambda r: r["percentile"])
    return selected


def download_one_message(grib_url: str, row: dict[str, Any], out_path: Path) -> None:
    start = row["start_byte"]
    end = row["end_byte"]

    if end is not None:
        headers = {"Range": f"bytes={start}-{end}"}
    else:
        headers = {"Range": f"bytes={start}-"}

    response = requests.get(grib_url, headers=headers, timeout=120)
    response.raise_for_status()

    out_path.write_bytes(response.content)


def find_lat_lon_names(ds: xr.Dataset) -> tuple[str, str]:
    lat_candidates = ["latitude", "lat", "LAT"]
    lon_candidates = ["longitude", "lon", "LON"]

    lat_name = None
    lon_name = None

    for name in lat_candidates:
        if name in ds:
            lat_name = name
            break

    for name in lon_candidates:
        if name in ds:
            lon_name = name
            break

    if lat_name is None or lon_name is None:
        raise RuntimeError(
            f"Could not find latitude/longitude variables. Dataset variables: {list(ds.variables)}"
        )

    return lat_name, lon_name


def nearest_grid_indices(ds: xr.Dataset) -> tuple[int, int]:
    lat_name, lon_name = find_lat_lon_names(ds)

    lat = ds[lat_name].values
    lon = ds[lon_name].values

    target_lon = KRNO_LON

    if np.nanmax(lon) > 180 and target_lon < 0:
        target_lon = target_lon + 360

    distance = (lat - KRNO_LAT) ** 2 + (lon - target_lon) ** 2
    iy, ix = np.unravel_index(np.nanargmin(distance), distance.shape)

    return int(iy), int(ix)


def extract_value_from_message(grib_path: Path) -> tuple[str, float]:
    ds = xr.open_dataset(
        grib_path,
        engine="cfgrib",
        backend_kwargs={"indexpath": ""},
    )

    try:
        iy, ix = nearest_grid_indices(ds)

        data_vars = list(ds.data_vars)
        if not data_vars:
            raise RuntimeError("No data variables found in QMD GRIB message.")

        var_name = data_vars[0]
        da = ds[var_name]

        values = da.values.squeeze()

        if values.ndim != 2:
            raise RuntimeError(f"Expected 2D grid after squeeze, got shape {values.shape}")

        value_mps = float(values[iy, ix])

        return var_name, value_mps

    finally:
        ds.close()


def extract_percentile_rows(grib_url: str, selected_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    percentile_rows = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        for row in selected_rows:
            percentile = float(row["percentile"])
            msg_path = tmp / f"qmd_gust_p{percentile:g}.grib2"

            print(f"Downloading/extracting QMD GUST percentile {percentile:g}%")
            download_one_message(grib_url, row, msg_path)

            var_name, value_mps = extract_value_from_message(msg_path)

            percentile_rows.append(
                {
                    "percentile": percentile,
                    "gust_mps": round(value_mps, 3),
                    "gust_mph": round(value_mps * MPS_TO_MPH, 2),
                    "gust_kt": round(value_mps * MPS_TO_KT, 2),
                    "variable": var_name,
                    "idx_line": row["line"],
                }
            )

    percentile_rows.sort(key=lambda r: r["percentile"])
    return percentile_rows


def probability_exceeding_from_percentiles(
    percentile_rows: list[dict[str, Any]],
    threshold_mph: float,
) -> float:
    points = [
        (float(row["percentile"]), float(row["gust_mph"]))
        for row in percentile_rows
        if row.get("percentile") is not None and row.get("gust_mph") is not None
    ]

    points.sort(key=lambda item: item[0])

    if len(points) < 2:
        raise RuntimeError("Need at least two percentile points to interpolate probability.")

    lowest_p, lowest_v = points[0]
    highest_p, highest_v = points[-1]

    if threshold_mph <= lowest_v:
        return round(max(0.0, min(100.0, 100.0 - lowest_p)), 1)

    if threshold_mph >= highest_v:
        return round(max(0.0, min(100.0, 100.0 - highest_p)), 1)

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
    for row in percentile_rows:
        if float(row["percentile"]) == 50.0:
            return row

    if not percentile_rows:
        return None

    return min(percentile_rows, key=lambda r: abs(float(r["percentile"]) - 50.0))


def main() -> None:
    cycle = latest_cycle_utc()
    fxx = 24

    print(f"Extracting QMD wind percentile curve for cycle {cycle:%Y-%m-%d %HZ}")

    grib_url, idx_url = qmd_urls(cycle, fxx)

    idx_text = fetch_text(idx_url)
    idx_rows = parse_idx(idx_text)
    selected_rows = select_qmd_gust_percentile_messages(idx_rows)

    if not selected_rows:
        raise RuntimeError("No QMD GUST 24-hour percentile messages found in IDX.")

    selected_idx_path = OUT / "test_qmd_wind_selected_idx_lines.txt"
    selected_idx_path.write_text("\n".join(row["line"] for row in selected_rows))

    percentile_rows = extract_percentile_rows(grib_url, selected_rows)
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
        "source": "NBM QMD direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "valid_utc": (cycle + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        "grib_url": grib_url,
        "idx_url": idx_url,
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
            "and 65 mph are derived by linear interpolation across official QMD "
            "percentile levels downloaded by GRIB2 byte range from the QMD IDX file."
        ),
    }

    out_path = OUT / "test_qmd_wind_percentiles.json"
    out_path.write_text(json.dumps(output, indent=2))

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
