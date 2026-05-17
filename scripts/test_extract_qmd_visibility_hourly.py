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

M_TO_SM = 0.000621371192237334

# KRNO ground ops visibility thresholds.
# Lower visibility is worse.
AIRPORT_THRESHOLDS_SM = {
    "lt_5_sm": 5.0,
    "lt_3_sm": 3.0,
    "lt_1_sm": 1.0,
    "lt_0_5_sm": 0.5,
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

        start_byte = int(parts[1])

        if i + 1 < len(lines):
            next_start = int(lines[i + 1].split(":", 2)[1])
            end_byte = next_start - 1
        else:
            end_byte = None

        rows.append(
            {
                "msg_num": int(parts[0]),
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


def select_hourly_visibility_percentile_messages(
    rows: list[dict[str, Any]],
    fxx: int,
) -> list[dict[str, Any]]:
    selected = []
    expected_time = f":{fxx} hour fcst:"

    for row in rows:
        line = row["line"]

        if ":VIS:surface:" not in line:
            continue

        if expected_time not in line:
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

    headers = {"Range": f"bytes={start}-{end}"} if end is not None else {"Range": f"bytes={start}-"}

    response = requests.get(grib_url, headers=headers, timeout=120)
    response.raise_for_status()
    out_path.write_bytes(response.content)


def find_lat_lon_names(ds: xr.Dataset) -> tuple[str, str]:
    lat_name = None
    lon_name = None

    for name in ["latitude", "lat", "LAT"]:
        if name in ds:
            lat_name = name
            break

    for name in ["longitude", "lon", "LON"]:
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

        value_m = float(values[iy, ix])
        return var_name, value_m

    finally:
        ds.close()


def extract_percentile_rows_for_hour(
    grib_url: str,
    selected_rows: list[dict[str, Any]],
    fxx: int,
) -> list[dict[str, Any]]:
    percentile_rows = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        for row in selected_rows:
            percentile = float(row["percentile"])
            msg_path = tmp / f"qmd_vis_f{fxx:03d}_p{percentile:g}.grib2"

            download_one_message(grib_url, row, msg_path)
            var_name, value_m = extract_value_from_message(msg_path)

            percentile_rows.append(
                {
                    "percentile": percentile,
                    "visibility_m": round(value_m, 1),
                    "visibility_sm": round(value_m * M_TO_SM, 2),
                    "variable": var_name,
                }
            )

    percentile_rows.sort(key=lambda r: r["percentile"])
    return percentile_rows


def probability_less_than_from_percentiles(
    percentile_rows: list[dict[str, Any]],
    threshold_sm: float,
) -> float:
    """Derive probability VIS < threshold from QMD percentile curve.

    Percentile curve gives visibility magnitude at percentile p.
    Since lower visibility is worse, probability VIS < threshold is CDF(threshold).
    """

    points = [
        (float(row["percentile"]), float(row["visibility_sm"]))
        for row in percentile_rows
        if row.get("percentile") is not None and row.get("visibility_sm") is not None
    ]

    points.sort(key=lambda item: item[0])

    if len(points) < 2:
        raise RuntimeError("Need at least two percentile points to interpolate probability.")

    lowest_p, lowest_v = points[0]
    highest_p, highest_v = points[-1]

    # If threshold is below the lowest visibility percentile, probability is near lowest percentile.
    if threshold_sm <= lowest_v:
        return round(max(0.0, min(100.0, lowest_p)), 1)

    # If threshold is above highest visibility percentile, probability is near 100%.
    if threshold_sm >= highest_v:
        return round(100.0, 1)

    for (p0, v0), (p1, v1) in zip(points[:-1], points[1:]):
        if v0 <= threshold_sm <= v1:
            if v1 == v0:
                cdf_p = p1
            else:
                fraction = (threshold_sm - v0) / (v1 - v0)
                cdf_p = p0 + fraction * (p1 - p0)

            return round(max(0.0, min(100.0, cdf_p)), 1)

    raise RuntimeError(f"Could not interpolate threshold {threshold_sm} SM.")


def get_p50(percentile_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in percentile_rows:
        if float(row["percentile"]) == 50.0:
            return row

    if not percentile_rows:
        return None

    return min(percentile_rows, key=lambda r: abs(float(r["percentile"]) - 50.0))


def main() -> None:
    cycle = latest_cycle_utc()

    print(f"Extracting hourly QMD visibility percentile curves for cycle {cycle:%Y-%m-%d %HZ}")

    hourly_results = []
    selected_line_report = []

    for fxx in range(1, 25):
        print(f"Processing QMD visibility f{fxx:03d}")

        grib_url, idx_url = qmd_urls(cycle, fxx)
        idx_text = fetch_text(idx_url)
        idx_rows = parse_idx(idx_text)

        selected_rows = select_hourly_visibility_percentile_messages(idx_rows, fxx)

        if not selected_rows:
            hourly_results.append(
                {
                    "fxx": fxx,
                    "valid_utc": (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z"),
                    "status": "error",
                    "message": f"No QMD hourly VIS percentile messages found for f{fxx:03d}",
                    "percentile_curve": [],
                }
            )
            continue

        selected_line_report.append("=" * 80)
        selected_line_report.append(f"f{fxx:03d}")
        selected_line_report.extend(row["line"] for row in selected_rows)

        percentile_rows = extract_percentile_rows_for_hour(grib_url, selected_rows, fxx)
        p50 = get_p50(percentile_rows)

        probs = {}
        for key, threshold_sm in AIRPORT_THRESHOLDS_SM.items():
            probs[key] = {
                "threshold_sm": threshold_sm,
                "threshold_m": round(threshold_sm / M_TO_SM, 1),
                "less_than_probability_percent": probability_less_than_from_percentiles(
                    percentile_rows,
                    threshold_sm,
                ),
            }

        hourly_results.append(
            {
                "fxx": fxx,
                "valid_utc": (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z"),
                "status": "ok",
                "p50_visibility_sm": p50["visibility_sm"] if p50 else None,
                "airport_threshold_probabilities": probs,
                "percentile_curve": percentile_rows,
            }
        )

    ok_hours = [h for h in hourly_results if h.get("status") == "ok"]

    if not ok_hours:
        raise RuntimeError("No hourly QMD visibility percentile hours extracted successfully.")

    min_p50_hour = min(ok_hours, key=lambda h: h.get("p50_visibility_sm") if h.get("p50_visibility_sm") is not None else 999)

    max_probs = {}
    for key in AIRPORT_THRESHOLDS_SM:
        best_hour = max(
            ok_hours,
            key=lambda h: h["airport_threshold_probabilities"][key]["less_than_probability_percent"],
        )
        max_probs[key] = {
            **best_hour["airport_threshold_probabilities"][key],
            "max_probability_fxx": best_hour["fxx"],
            "max_probability_valid_utc": best_hour["valid_utc"],
        }

    output = {
        "site": "KRNO",
        "source": "NBM QMD direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "variable": "Hourly QMD surface visibility percentile curves, f001-f024",
        "display_value": {
            "label": "Lowest visibility",
            "method": "minimum hourly QMD P50 visibility from f001-f024",
            "source_fxx": min_p50_hour["fxx"],
            "valid_utc": min_p50_hour["valid_utc"],
            "visibility_sm": min_p50_hour["p50_visibility_sm"],
        },
        "airport_threshold_probabilities": {
            "method": (
                "For each airport threshold, probability is the maximum hourly probability "
                "of visibility below threshold from f001-f024. Hourly probabilities are "
                "derived by linear interpolation across official QMD percentile levels."
            ),
            "thresholds": max_probs,
        },
        "hourly_results": hourly_results,
        "methodology": (
            "Visibility display is the minimum hourly QMD P50 visibility across f001-f024. "
            "Risk probabilities use maximum hourly probability of visibility below "
            "5, 3, 1, and 0.5 SM thresholds across f001-f024."
        ),
    }

    (OUT / "test_qmd_visibility_hourly_selected_idx_lines.txt").write_text(
        "\n".join(selected_line_report)
    )

    out_path = OUT / "test_qmd_visibility_hourly_percentiles.json"
    out_path.write_text(json.dumps(output, indent=2))

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
