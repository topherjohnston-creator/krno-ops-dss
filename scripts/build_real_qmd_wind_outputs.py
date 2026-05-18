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


DOCS = Path("docs")
DATA = Path("data")
DOCS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

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

# Impact levels tied to your KRNO Ops wind thresholds.
WIND_IMPACT_LEVELS = {
    "gt_30_mph": 2,
    "gt_45_mph": 3,
    "gt_58_mph": 4,
    "gt_65_mph": 5,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_cycle_utc() -> datetime:
    """
    Use an older likely-complete NBM cycle.

    QMD files can lag behind the current NBM cycle on NOMADS.
    Using the immediately previous 6-hour cycle can fail with 404s,
    especially for f001. Lag by 12 hours to avoid partially available
    QMD cycles.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)

    return cycle - timedelta(hours=12)


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


def select_hourly_gust_percentile_messages(rows: list[dict[str, Any]], fxx: int) -> list[dict[str, Any]]:
    selected = []
    expected_time = f":{fxx} hour fcst:"

    for row in rows:
        line = row["line"]

        if ":GUST:10 m above ground:" not in line:
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

        value_mps = float(values[iy, ix])
        return var_name, value_mps

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
            msg_path = tmp / f"qmd_gust_f{fxx:03d}_p{percentile:g}.grib2"

            download_one_message(grib_url, row, msg_path)
            var_name, value_mps = extract_value_from_message(msg_path)

            percentile_rows.append(
                {
                    "percentile": percentile,
                    "gust_mps": round(value_mps, 3),
                    "gust_mph": round(value_mps * MPS_TO_MPH, 2),
                    "gust_kt": round(value_mps * MPS_TO_KT, 2),
                    "variable": var_name,
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


def probability_to_likelihood(probability: float) -> int:
    """Return 1-5 likelihood category.

    1 = Extremely unlikely
    2 = Unlikely
    3 = About as likely as not
    4 = Likely
    5 = Very likely
    """
    if probability >= 90:
        return 5
    if probability >= 66:
        return 4
    if probability >= 33:
        return 3
    if probability >= 10:
        return 2
    return 1


def matrix_risk(probability: float, impact_level: int) -> int:
    """Probability x impact risk matrix.

    Returns:
    0 = None
    1 = Little to None
    2 = Minor
    3 = Moderate
    4 = Major
    5 = Extreme
    """

    # Critical fix:
    # A zero-probability high-impact threshold must not drive the risk card.
    if probability <= 0:
        return 0

    likelihood = probability_to_likelihood(probability)

    matrix = {
        1: {1: 1, 2: 1, 3: 1, 4: 2, 5: 2},
        2: {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
        3: {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
        4: {1: 1, 2: 2, 3: 3, 4: 4, 5: 4},
        5: {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
    }

    safe_impact = max(1, min(5, int(impact_level)))
    return matrix[likelihood][safe_impact]


def risk_label(risk: int) -> str:
    return {
        0: "None",
        1: "Little to None",
        2: "Minor",
        3: "Moderate",
        4: "Major",
        5: "Extreme",
    }.get(risk, "Unknown")


def evaluate_wind_risk(threshold_probs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidates = []

    for key, impact_level in WIND_IMPACT_LEVELS.items():
        probability = float(threshold_probs[key]["exceedance_probability_percent"])
        threshold_mph = float(threshold_probs[key]["threshold_mph"])
        risk = matrix_risk(probability, impact_level)

        candidates.append(
            {
                "threshold_key": key,
                "threshold_mph": threshold_mph,
                "impact_level": impact_level,
                "probability": probability,
                "risk": risk,
                "risk_label": risk_label(risk),
                "source_fxx": threshold_probs[key].get("max_probability_fxx"),
                "source_valid_utc": threshold_probs[key].get("max_probability_valid_utc"),
            }
        )

    # Highest risk wins. Tie-breaker: higher impact, then higher probability.
    best = max(candidates, key=lambda c: (c["risk"], c["impact_level"], c["probability"]))

    return {
        "best": best,
        "candidates": candidates,
    }


def extract_hourly_qmd_wind(cycle: datetime) -> list[dict[str, Any]]:
    hourly_results = []

    for fxx in range(1, 25):
        print(f"Processing QMD wind f{fxx:03d}")

        grib_url, idx_url = qmd_urls(cycle, fxx)
        idx_text = fetch_text(idx_url)
        idx_rows = parse_idx(idx_text)

        selected_rows = select_hourly_gust_percentile_messages(idx_rows, fxx)

        if not selected_rows:
            hourly_results.append(
                {
                    "fxx": fxx,
                    "valid_utc": (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z"),
                    "status": "error",
                    "message": f"No QMD hourly GUST percentile messages found for f{fxx:03d}",
                    "percentile_curve": [],
                }
            )
            continue

        percentile_rows = extract_percentile_rows_for_hour(grib_url, selected_rows, fxx)
        p50 = get_p50(percentile_rows)

        probs = {}
        for key, threshold_mph in AIRPORT_THRESHOLDS_MPH.items():
            probs[key] = {
                "threshold_mph": threshold_mph,
                "threshold_mps": round(threshold_mph / MPS_TO_MPH, 3),
                "exceedance_probability_percent": probability_exceeding_from_percentiles(
                    percentile_rows,
                    threshold_mph,
                ),
            }

        hourly_results.append(
            {
                "fxx": fxx,
                "valid_utc": (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z"),
                "status": "ok",
                "p50_gust_mph": p50["gust_mph"] if p50 else None,
                "p50_gust_kt": p50["gust_kt"] if p50 else None,
                "airport_threshold_probabilities": probs,
                "percentile_curve": percentile_rows,
            }
        )

    return hourly_results


def summarize_threshold_probabilities(ok_hours: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    max_probs = {}

    for key, threshold_mph in AIRPORT_THRESHOLDS_MPH.items():
        best_hour = max(
            ok_hours,
            key=lambda h: h["airport_threshold_probabilities"][key]["exceedance_probability_percent"],
        )

        max_probs[key] = {
            **best_hour["airport_threshold_probabilities"][key],
            "max_probability_fxx": best_hour["fxx"],
            "max_probability_valid_utc": best_hour["valid_utc"],
        }

    return max_probs


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def block_wind_risk(block_hours: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []

    for hour in block_hours:
        for key, impact_level in WIND_IMPACT_LEVELS.items():
            probability = float(hour["airport_threshold_probabilities"][key]["exceedance_probability_percent"])
            risk = matrix_risk(probability, impact_level)

            candidates.append(
                {
                    "threshold_key": key,
                    "threshold_mph": AIRPORT_THRESHOLDS_MPH[key],
                    "impact_level": impact_level,
                    "probability": probability,
                    "risk": risk,
                    "fxx": hour["fxx"],
                    "valid_utc": hour["valid_utc"],
                }
            )

    if not candidates:
        return {"prob": 0, "risk": 0, "level": 0}

    best = max(candidates, key=lambda c: (c["risk"], c["impact_level"], c["probability"]))

    return {
        "prob": round(best["probability"], 1),
        "risk": int(best["risk"]),
        "level": int(best["impact_level"]),
        "threshold_mph": best["threshold_mph"],
        "source_fxx": best["fxx"],
    }


def main() -> None:
    cycle = latest_cycle_utc()
    generated = utc_now()

    print(f"Building QMD wind outputs for cycle {cycle:%Y-%m-%d %HZ}")

    hourly_results = extract_hourly_qmd_wind(cycle)
    ok_hours = [h for h in hourly_results if h.get("status") == "ok"]

    if not ok_hours:
        raise RuntimeError("No QMD wind hours extracted successfully.")

    peak_p50_hour = max(ok_hours, key=lambda h: h.get("p50_gust_mph") or -999)
    threshold_probs = summarize_threshold_probabilities(ok_hours)
    risk_eval = evaluate_wind_risk(threshold_probs)
    best = risk_eval["best"]

    # Use timing from the threshold driving the risk, not necessarily the P50 max.
    peak_start_fxx = max(1, int(best.get("source_fxx") or peak_p50_hour["fxx"]) - 1)
    peak_end_fxx = min(24, int(best.get("source_fxx") or peak_p50_hour["fxx"]) + 1)

    display_gust_mph = float(peak_p50_hour["p50_gust_mph"])
    display_gust_kt = float(peak_p50_hour["p50_gust_kt"])

    # Update threats.json.
    threats_path = DOCS / "threats.json"
    threats_payload = load_json(
        threats_path,
        {
            "site": "KRNO",
            "valid_period": "next_48_hours",
            "threats": {},
        },
    )

    threats_payload["generated_utc"] = generated
    threats_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    threats_payload["cycle"] = f"NBM QMD {cycle.strftime('%HZ')}"
    threats_payload.setdefault("threats", {})

    threats_payload["threats"]["WIND"] = {
        "prob": round(float(best["probability"]), 1),
        "risk": int(best["risk"]),
        "risk_label": best["risk_label"],
        "level": int(best["impact_level"]),
        "metric": f">{int(best['threshold_mph'])} mph threshold",
        "display_label": "24-hr max gust",
        "display_value": f"{display_gust_mph:.0f} mph",
        "g24_p50_mph": round(display_gust_mph, 1),
        "g24_p50_kt": round(display_gust_kt, 1),
        "threshold_probabilities": threshold_probs,
        "risk_candidates": risk_eval["candidates"],
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "driver": (
            f"QMD max hourly P50 gust {display_gust_mph:.1f} mph; "
            f"{best['probability']:.1f}% chance >{best['threshold_mph']:.0f} mph"
        ),
        "methodology": (
            "Wind display uses maximum hourly QMD P50 gust from f001-f024. "
            "Wind risk uses exact threshold probabilities for 30, 45, 58, and 65 mph "
            "derived from QMD hourly percentile curves, then applies probability x impact matrix."
        ),
    }

    hazards = threats_payload.get("hazards")
    if isinstance(hazards, list):
        found = False

        for hazard in hazards:
            if hazard.get("id") == "WIND":
                hazard.update(
                    {
                        "id": "WIND",
                        "name": "Wind",
                        "risk_level": int(best["risk"]),
                        "risk_label": best["risk_label"],
                        "impact_level": int(best["impact_level"]),
                        "probability": round(float(best["probability"]), 1),
                        "peak_start_fxx": peak_start_fxx,
                        "peak_end_fxx": peak_end_fxx,
                        "metric": f">{int(best['threshold_mph'])} mph threshold",
                        "display_label": "24-hr max gust",
                        "display_value": f"{display_gust_mph:.0f} mph",
                        "g24_p50_mph": round(display_gust_mph, 1),
                        "driver": (
                            f"QMD max hourly P50 gust {display_gust_mph:.1f} mph; "
                            f"{best['probability']:.1f}% chance >{best['threshold_mph']:.0f} mph"
                        ),
                    }
                )
                found = True
                break

        if not found:
            hazards.append(
                {
                    "id": "WIND",
                    "name": "Wind",
                    "risk_level": int(best["risk"]),
                    "risk_label": best["risk_label"],
                    "impact_level": int(best["impact_level"]),
                    "probability": round(float(best["probability"]), 1),
                    "peak_start_fxx": peak_start_fxx,
                    "peak_end_fxx": peak_end_fxx,
                    "metric": f">{int(best['threshold_mph'])} mph threshold",
                    "display_label": "24-hr max gust",
                    "display_value": f"{display_gust_mph:.0f} mph",
                    "g24_p50_mph": round(display_gust_mph, 1),
                    "driver": (
                        f"QMD max hourly P50 gust {display_gust_mph:.1f} mph; "
                        f"{best['probability']:.1f}% chance >{best['threshold_mph']:.0f} mph"
                    ),
                }
            )

    threats_path.write_text(json.dumps(threats_payload, indent=2))

    # Update timeline.json.
    timeline_path = DOCS / "timeline.json"
    timeline_payload = load_json(
        timeline_path,
        {
            "site": "KRNO",
            "block_hours": 3,
            "blocks": [],
            "block_hazards": [],
        },
    )

    timeline_payload["generated_utc"] = generated
    timeline_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    timeline_payload["cycle"] = f"NBM QMD {cycle.strftime('%HZ')}"

    blocks = timeline_payload.setdefault("blocks", [])
    block_hazards = timeline_payload.setdefault("block_hazards", [])

    while len(blocks) < 8:
        bi = len(blocks)
        blocks.append({"start_fxx": bi * 3 + 1, "end_fxx": bi * 3 + 3})

    while len(block_hazards) < 8:
        block_hazards.append({})

    for bi in range(8):
        start_fxx = bi * 3 + 1
        end_fxx = bi * 3 + 3

        block_hours = [h for h in ok_hours if start_fxx <= h["fxx"] <= end_fxx]
        if not block_hours:
            continue

        block_peak_p50 = max(block_hours, key=lambda h: h.get("p50_gust_mph") or -999)
        block_eval = block_wind_risk(block_hours)

        blocks[bi]["start_fxx"] = start_fxx
        blocks[bi]["end_fxx"] = end_fxx
        blocks[bi]["GST"] = round(float(block_peak_p50["p50_gust_mph"]), 1)

        block_hazards[bi]["WIND"] = block_eval

    timeline_path.write_text(json.dumps(timeline_payload, indent=2))

    diagnostic = {
        "site": "KRNO",
        "source": "NBM QMD direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "display_value": {
            "label": "24-hr max gust",
            "method": "maximum hourly QMD P50 gust from f001-f024",
            "source_fxx": peak_p50_hour["fxx"],
            "valid_utc": peak_p50_hour["valid_utc"],
            "gust_mph": round(display_gust_mph, 1),
            "gust_kt": round(display_gust_kt, 1),
        },
        "airport_threshold_probabilities": {
            "method": (
                "For each airport threshold, probability is the maximum hourly exceedance "
                "probability from f001-f024. Hourly exceedance probabilities are derived "
                "by linear interpolation across official QMD percentile levels."
            ),
            "thresholds": threshold_probs,
        },
        "risk": risk_eval,
        "hourly_results": hourly_results,
        "methodology": (
            "Wind display is the maximum hourly QMD P50 gust across f001-f024. "
            "Wind risk probabilities use the maximum hourly exceedance probability for "
            "30, 45, 58, and 65 mph thresholds across f001-f024. Risk is calculated using "
            "probability x impact."
        ),
    }

    (DATA / "nbm_qmd_wind.json").write_text(json.dumps(diagnostic, indent=2))

    print("Updated docs/threats.json WIND")
    print("Updated docs/timeline.json WIND")
    print("Wrote data/nbm_qmd_wind.json")


if __name__ == "__main__":
    main()
