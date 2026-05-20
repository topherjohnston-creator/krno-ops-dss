from __future__ import annotations

import argparse
import json
import math
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import xarray as xr


DOCS = Path("docs")
DATA = Path("data")
DOCS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

KRNO_LAT = 39.4991
KRNO_LON = -119.7681

FXX_HOURS = list(range(1, 49))

MPS_TO_MPH = 2.2369362920544
MPS_TO_KT = 1.9438444924406

WIND_THRESHOLDS = {
    "gt_30_mph": {
        "threshold_mph": 30.0,
        "threshold_mps": 30.0 / MPS_TO_MPH,
        "impact_level": 2,
        "label": ">30 mph",
        "ops_label": "Elevated ground operations wind impact",
    },
    "gt_45_mph": {
        "threshold_mph": 45.0,
        "threshold_mps": 45.0 / MPS_TO_MPH,
        "impact_level": 3,
        "label": ">45 mph",
        "ops_label": "Operational wind restrictions possible",
    },
    "gt_58_mph": {
        "threshold_mph": 58.0,
        "threshold_mps": 58.0 / MPS_TO_MPH,
        "impact_level": 4,
        "label": ">58 mph",
        "ops_label": "High-impact ground operations wind risk",
    },
    "gt_65_mph": {
        "threshold_mph": 65.0,
        "threshold_mps": 65.0 / MPS_TO_MPH,
        "impact_level": 5,
        "label": ">65 mph",
        "ops_label": "Extreme wind risk for ground operations",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_cycle_arg(cycle_arg: str | None) -> datetime | None:
    if not cycle_arg:
        return None

    cleaned = cycle_arg.strip()
    if not cleaned:
        return None

    if len(cleaned) != 10 or not cleaned.isdigit():
        raise ValueError("Cycle must be YYYYMMDDHH, for example 2026052012")

    return datetime.strptime(cleaned, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def floor_to_6hr_cycle() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    return now.replace(hour=cycle_hour)


def qmd_grib_url(cycle: datetime, fxx: int) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
        f"blend.{ymd}/{hh}/qmd/blend.t{hh}z.qmd.f{fxx:03d}.co.grib2"
    )


def qmd_idx_url(cycle: datetime, fxx: int) -> str:
    return qmd_grib_url(cycle, fxx) + ".idx"


def url_exists(url: str, timeout: int = 20) -> bool:
    try:
        response = requests.get(url, timeout=timeout, stream=True)
        status = response.status_code == 200
        response.close()
        return status
    except Exception:
        return False


def cycle_has_required_qmd_files(cycle: datetime, required_fxx: list[int]) -> bool:
    missing = []
    for fxx in required_fxx:
        if not url_exists(qmd_idx_url(cycle, fxx)):
            missing.append(fxx)

    if missing:
        print(f"NBM QMD {cycle:%Y-%m-%d %HZ} incomplete. Missing fxx: {missing}")
        return False

    return True


def latest_available_qmd_cycle() -> datetime:
    """
    Use the newest QMD cycle with enough files to build:
      - 24-hr max gust card/probabilities from f024
      - timeline through 48 hours from hourly mean gusts
    """
    latest = floor_to_6hr_cycle()
    required_fxx = [24, 48]

    for lag_hours in [0, 6, 12, 18, 24, 30, 36, 42, 48]:
        candidate = latest - timedelta(hours=lag_hours)
        print(f"Checking NBM QMD cycle {candidate:%Y-%m-%d %HZ}")
        if cycle_has_required_qmd_files(candidate, required_fxx):
            print(f"Using NBM QMD cycle {candidate:%Y-%m-%d %HZ}")
            return candidate

    fallback = latest - timedelta(hours=12)
    print(f"Warning: no complete QMD cycle found. Falling back to {fallback:%Y-%m-%d %HZ}")
    return fallback


def fetch_text(url: str, timeout: int = 60) -> str:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def parse_idx(idx_text: str) -> list[dict[str, Any]]:
    rows = []
    lines = idx_text.splitlines()

    for i, line in enumerate(lines):
        parts = line.split(":")
        if len(parts) < 3:
            continue

        try:
            message_no = int(parts[0])
            start_byte = int(parts[1])
        except ValueError:
            # Skip malformed HTML/CSS/error lines.
            continue

        end_byte = None
        if i + 1 < len(lines):
            next_parts = lines[i + 1].split(":")
            if len(next_parts) >= 2:
                try:
                    end_byte = int(next_parts[1]) - 1
                except ValueError:
                    end_byte = None

        rows.append(
            {
                "message_no": message_no,
                "start_byte": start_byte,
                "end_byte": end_byte,
                "line": line,
            }
        )

    return rows


def download_grib_message(grib_url: str, row: dict[str, Any], path: Path) -> None:
    if row.get("end_byte") is not None:
        headers = {"Range": f"bytes={row['start_byte']}-{row['end_byte']}"}
    else:
        headers = {"Range": f"bytes={row['start_byte']}-"}

    last_error = None

    for attempt in range(1, 4):
        try:
            response = requests.get(grib_url, headers=headers, timeout=90)
            response.raise_for_status()
            content = response.content

            if len(content) < 100:
                raise RuntimeError(f"Downloaded GRIB message too small: {len(content)} bytes")

            path.write_bytes(content)
            return

        except Exception as exc:
            last_error = exc
            if attempt == 3:
                raise RuntimeError(f"Failed to download GRIB message after 3 attempts: {exc}") from exc

    raise RuntimeError(f"Failed to download GRIB message: {last_error}")


def normalize_lon(lon: float) -> float:
    return lon + 360.0 if lon < 0 else lon


def find_lat_lon_names(ds: xr.Dataset) -> tuple[str, str]:
    lat_candidates = ["latitude", "lat", "gridlat_0"]
    lon_candidates = ["longitude", "lon", "gridlon_0"]

    lat_name = None
    lon_name = None

    for name in lat_candidates:
        if name in ds.coords or name in ds.variables:
            lat_name = name
            break

    for name in lon_candidates:
        if name in ds.coords or name in ds.variables:
            lon_name = name
            break

    if lat_name is None or lon_name is None:
        raise RuntimeError(f"Could not find lat/lon coordinates. Variables: {list(ds.variables)}")

    return lat_name, lon_name


def nearest_grid_value(ds: xr.Dataset) -> tuple[str, float]:
    data_vars = list(ds.data_vars)
    if not data_vars:
        raise RuntimeError("No data variables in GRIB message.")

    var_name = data_vars[0]

    lat_name, lon_name = find_lat_lon_names(ds)
    lat = ds[lat_name]
    lon = ds[lon_name]

    target_lon_360 = normalize_lon(KRNO_LON)

    if lat.ndim == 1 and lon.ndim == 1:
        lat_idx = int(abs(lat - KRNO_LAT).argmin())
        lon_idx = int(abs(lon - target_lon_360).argmin())
        value = ds[var_name].isel({lat_name: lat_idx, lon_name: lon_idx}).values
    else:
        lon_values = lon.values
        lat_values = lat.values

        if float(lon_values.max()) > 180:
            target_lon_for_grid = target_lon_360
        else:
            target_lon_for_grid = KRNO_LON

        dist2 = (lat_values - KRNO_LAT) ** 2 + (lon_values - target_lon_for_grid) ** 2
        iy, ix = [int(v) for v in divmod(int(dist2.argmin()), dist2.shape[1])]

        dims = ds[var_name].dims
        indexers = {}

        if len(lat.dims) == 2:
            for dim, idx in zip(lat.dims, [iy, ix]):
                if dim in dims:
                    indexers[dim] = idx
        elif len(lat.dims) == 1 and len(lon.dims) == 1:
            if lat.dims[0] in dims:
                indexers[lat.dims[0]] = iy
            if lon.dims[0] in dims:
                indexers[lon.dims[0]] = ix

        value = ds[var_name].isel(indexers).values

    value_float = float(value.squeeze())
    if math.isnan(value_float):
        raise RuntimeError(f"Nearest value for {var_name} is NaN.")

    return var_name, value_float


def extract_value_from_message(message_path: Path) -> tuple[str, float]:
    try:
        ds = xr.open_dataset(
            message_path,
            engine="cfgrib",
            backend_kwargs={
                "indexpath": "",
                "errors": "ignore",
            },
        )
        try:
            return nearest_grid_value(ds)
        finally:
            ds.close()
    except Exception as exc:
        raise RuntimeError(f"Could not read GRIB message {message_path}: {exc}") from exc


def extract_qmd_value(grib_url: str, row: dict[str, Any], label: str) -> tuple[str, float]:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / f"{label}.grib2"
        download_grib_message(grib_url, row, path)
        return extract_value_from_message(path)


def is_gust_10m_line(line: str) -> bool:
    upper = line.upper()
    lower = line.lower()
    return ":GUST:" in upper and "10 m above ground" in lower


def is_excluded_stat_line(line: str) -> bool:
    lower = line.lower()
    excluded = [
        "prob >",
        "prob >=",
        "prob <",
        "prob <=",
        "stddev",
        "std dev",
    ]
    return any(term in lower for term in excluded)


def extract_percentile_from_line(line: str) -> float | None:
    lower = line.lower()
    match = re.search(r":\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*level", lower)
    if match:
        return float(match.group(1))
    return None


def find_24hr_max_gust_mean_row(idx_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Target row is the DESI-like QMD 24-hour max gust mean field:
      GUST:10 m above ground:0-1 day max fcst:
    It often does not explicitly say 'ens mean'.
    """
    for row in idx_rows:
        line = row.get("line", "")
        lower = line.lower()

        if not is_gust_10m_line(line):
            continue
        if "0-1 day max fcst" not in lower:
            continue
        if "% level" in lower:
            continue
        if is_excluded_stat_line(line):
            continue

        return row

    return None


def find_24hr_max_gust_percentile_rows(idx_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []

    for row in idx_rows:
        line = row.get("line", "")
        lower = line.lower()

        if not is_gust_10m_line(line):
            continue
        if "0-1 day max fcst" not in lower:
            continue
        if "% level" not in lower:
            continue

        percentile = extract_percentile_from_line(line)
        if percentile is None:
            continue

        row2 = dict(row)
        row2["percentile"] = percentile
        out.append(row2)

    return sorted(out, key=lambda r: float(r["percentile"]))


def find_hourly_gust_mean_row(idx_rows: list[dict[str, Any]], fxx: int) -> dict[str, Any] | None:
    """
    We want the QMD hourly mean gust row for a given hour.
    Common line formats can vary, so this matcher is intentionally flexible.
    """
    start = fxx - 1
    end = fxx

    period_patterns = [
        f":{fxx} hour fcst:",
        f":{start}-{end} hour fcst:",
        f":{start}-{end} hour acc fcst:",
        f":{start}-{end} hour max fcst:",
    ]

    preferred = []
    candidates = []

    for row in idx_rows:
        line = row.get("line", "")
        lower = line.lower()

        if not is_gust_10m_line(line):
            continue
        if "% level" in lower:
            continue
        if is_excluded_stat_line(line):
            continue
        if "0-1 day max fcst" in lower:
            continue

        if not any(pattern in lower for pattern in period_patterns):
            continue

        candidates.append(row)
        if "ens mean" in lower:
            preferred.append(row)

    if preferred:
        return preferred[0]
    if candidates:
        return candidates[0]

    return None


def mps_to_output(value_mps: float) -> dict[str, float]:
    return {
        "gust_mps": round(float(value_mps), 2),
        "gust_mph": round(float(value_mps) * MPS_TO_MPH, 1),
        "gust_kt": round(float(value_mps) * MPS_TO_KT, 1),
    }


def probability_to_likelihood(probability: float) -> int:
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


def impact_level_from_gust(gust_mph: float) -> int:
    if gust_mph >= 65:
        return 5
    if gust_mph >= 58:
        return 4
    if gust_mph >= 45:
        return 3
    if gust_mph >= 30:
        return 2
    return 1


def metric_from_gust(gust_mph: float) -> str:
    if gust_mph >= 65:
        return ">65 mph"
    if gust_mph >= 58:
        return "58-65 mph"
    if gust_mph >= 45:
        return "45-58 mph"
    if gust_mph >= 30:
        return "30-45 mph"
    return "<30 mph"


def interpolate_percentile_at_threshold(curve: list[dict[str, Any]], threshold_mps: float) -> float | None:
    clean = [
        {
            "percentile": float(row["percentile"]),
            "gust_mps": float(row["gust_mps"]),
        }
        for row in curve
        if row.get("gust_mps") is not None
    ]

    if not clean:
        return None

    clean.sort(key=lambda r: (r["gust_mps"], r["percentile"]))

    if threshold_mps <= clean[0]["gust_mps"]:
        return clean[0]["percentile"]

    if threshold_mps > clean[-1]["gust_mps"]:
        return 100.0

    for i in range(len(clean) - 1):
        left = clean[i]
        right = clean[i + 1]

        g0 = left["gust_mps"]
        g1 = right["gust_mps"]
        p0 = left["percentile"]
        p1 = right["percentile"]

        if g0 <= threshold_mps <= g1:
            if abs(g1 - g0) < 1e-9:
                return max(p0, p1)

            frac = (threshold_mps - g0) / (g1 - g0)
            return p0 + frac * (p1 - p0)

    return None


def exceedance_probability_from_curve(curve: list[dict[str, Any]], threshold_mps: float) -> float:
    percentile = interpolate_percentile_at_threshold(curve, threshold_mps)
    if percentile is None:
        return 0.0

    probability = 100.0 - percentile
    return round(max(0.0, min(100.0, probability)), 1)


def evaluate_threshold_risks(probabilities: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates = []

    for key, threshold in WIND_THRESHOLDS.items():
        probability = float(probabilities.get(key, {}).get("exceedance_probability_percent", 0.0))
        impact_level = int(threshold["impact_level"])
        risk = matrix_risk(probability, impact_level)

        candidates.append(
            {
                "threshold_key": key,
                "threshold_mph": threshold["threshold_mph"],
                "threshold_mps": threshold["threshold_mps"],
                "probability": probability,
                "impact_level": impact_level,
                "risk": risk,
                "risk_label": risk_label(risk),
                "label": threshold["label"],
                "ops_label": threshold["ops_label"],
            }
        )

    if all(float(c["probability"]) <= 0 for c in candidates):
        best = {
            "threshold_key": "no_wind_threshold",
            "threshold_mph": 0.0,
            "threshold_mps": 0.0,
            "probability": 0.0,
            "impact_level": 0,
            "risk": 0,
            "risk_label": "None",
            "label": "<30 mph",
            "ops_label": "No meaningful wind threshold signal",
        }
        return best, candidates

    best = max(candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))
    return best, candidates


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def extract_24hr_max_wind(cycle: datetime) -> dict[str, Any]:
    fxx = 24
    grib_url = qmd_grib_url(cycle, fxx)
    idx_url = qmd_idx_url(cycle, fxx)

    idx_text = fetch_text(idx_url)
    idx_rows = parse_idx(idx_text)

    mean_row = find_24hr_max_gust_mean_row(idx_rows)
    if mean_row is None:
        debug_lines = [
            row["line"]
            for row in idx_rows
            if is_gust_10m_line(row["line"]) and "0-1 day max fcst" in row["line"].lower()
        ]
        (DATA / "qmd_wind_24hr_max_missing_debug.txt").write_text("\n".join(debug_lines))
        raise RuntimeError(
            "Could not find QMD 24-hour maximum 10-meter gust mean row in f024 IDX. "
            "Wrote data/qmd_wind_24hr_max_missing_debug.txt"
        )

    var_name, mean_mps = extract_qmd_value(
        grib_url=grib_url,
        row=mean_row,
        label="qmd_24hr_max_gust_mean_f024",
    )

    percentile_rows = find_24hr_max_gust_percentile_rows(idx_rows)
    percentile_curve = []

    for row in percentile_rows:
        percentile = float(row["percentile"])
        try:
            var_name_p, gust_mps = extract_qmd_value(
                grib_url=grib_url,
                row=row,
                label=f"qmd_24hr_max_gust_p{percentile:g}",
            )
            percentile_curve.append(
                {
                    "percentile": percentile,
                    "variable": var_name_p,
                    **mps_to_output(gust_mps),
                    "idx_line": row["line"],
                }
            )
        except Exception as exc:
            print(f"Warning: skipped 24-hr max gust percentile p{percentile:g}: {exc}")

    probabilities = {}
    for key, threshold in WIND_THRESHOLDS.items():
        prob = exceedance_probability_from_curve(percentile_curve, threshold["threshold_mps"])
        probabilities[key] = {
            "threshold_mph": threshold["threshold_mph"],
            "threshold_mps": round(threshold["threshold_mps"], 3),
            "exceedance_probability_percent": prob,
            "method": "linear interpolation across QMD 24-hour max gust percentile levels",
        }

    best, candidates = evaluate_threshold_risks(probabilities)
    mean_output = mps_to_output(mean_mps)

    return {
        "fxx": fxx,
        "valid_utc": (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z"),
        "grib_url": grib_url,
        "idx_url": idx_url,
        "variable": var_name,
        "source_idx_line": mean_row["line"],
        "display_value": {
            "label": "24-hr max gust",
            "method": "mean value from QMD 24-hour maximum 10-meter wind gust grid",
            **mean_output,
        },
        "airport_threshold_probabilities": probabilities,
        "percentile_curve": percentile_curve,
        "selected_risk": best,
        "risk_candidates": candidates,
    }


def extract_hourly_qmd_mean_wind(cycle: datetime) -> list[dict[str, Any]]:
    results = []

    for fxx in FXX_HOURS:
        print(f"Processing QMD hourly mean wind f{fxx:03d}")

        grib_url = qmd_grib_url(cycle, fxx)
        idx_url = qmd_idx_url(cycle, fxx)
        valid_utc = (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")

        try:
            idx_text = fetch_text(idx_url)
            idx_rows = parse_idx(idx_text)
        except Exception as exc:
            results.append(
                {
                    "fxx": fxx,
                    "valid_utc": valid_utc,
                    "status": "error",
                    "message": f"Could not fetch/parse IDX: {exc}",
                    "gust_mps": None,
                    "gust_mph": None,
                    "gust_kt": None,
                }
            )
            continue

        row = find_hourly_gust_mean_row(idx_rows, fxx)
        if row is None:
            results.append(
                {
                    "fxx": fxx,
                    "valid_utc": valid_utc,
                    "status": "missing",
                    "message": "No QMD hourly mean gust row found",
                    "gust_mps": None,
                    "gust_mph": None,
                    "gust_kt": None,
                }
            )
            continue

        try:
            var_name, gust_mps = extract_qmd_value(
                grib_url=grib_url,
                row=row,
                label=f"qmd_hourly_mean_gust_f{fxx:03d}",
            )
            out = mps_to_output(gust_mps)
            results.append(
                {
                    "fxx": fxx,
                    "valid_utc": valid_utc,
                    "status": "ok",
                    "variable": var_name,
                    "idx_line": row["line"],
                    **out,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "fxx": fxx,
                    "valid_utc": valid_utc,
                    "status": "error",
                    "message": str(exc),
                    "idx_line": row["line"],
                    "gust_mps": None,
                    "gust_mph": None,
                    "gust_kt": None,
                }
            )

    return results


def block_wind_timing(
    block_hours: list[dict[str, Any]],
    block_start_valid_utc: str,
    block_end_valid_utc: str,
) -> dict[str, Any]:
    valid = [
        h
        for h in block_hours
        if h.get("status") == "ok" and h.get("gust_mph") is not None
    ]

    if not valid:
        return {
            "prob": None,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": "No data",
            "gust_mph": None,
            "gust_kt": None,
            "source_fxx": None,
            "peak_valid_utc": None,
            "valid_start_utc": block_start_valid_utc,
            "valid_end_utc": block_end_valid_utc,
            "driver": "No QMD hourly mean gust available",
            "timing_value": None,
            "timing_method": "No hourly QMD mean gust available",
        }

    peak = max(valid, key=lambda h: float(h["gust_mph"]))
    gust_mph = float(peak["gust_mph"])
    gust_kt = float(peak["gust_kt"])
    impact = impact_level_from_gust(gust_mph)

    # Important fix:
    # Sub-30 mph wind is not a ground-ops wind hazard.
    # Keep risk at 0 so the dashboard does not imply a hazard,
    # but still preserve timing_value/gust_mph so timing can be audited/displayed.
    if gust_mph < 30:
        risk = 0
        risk_lbl = "None"
        metric = "<30 mph"
        driver = f"QMD hourly mean gust {gust_mph:.1f} mph; below wind-impact threshold"
        level = 0
    else:
        risk = impact
        risk_lbl = risk_label(risk)
        metric = metric_from_gust(gust_mph)
        driver = f"QMD hourly mean gust {gust_mph:.1f} mph"
        level = impact

    return {
        "prob": None,
        "risk": int(risk),
        "risk_label": risk_lbl,
        "level": int(level),
        "metric": metric,
        "gust_mph": round(gust_mph, 1),
        "gust_kt": round(gust_kt, 1),
        "source_fxx": int(peak["fxx"]),
        "peak_valid_utc": peak.get("valid_utc"),
        "valid_start_utc": block_start_valid_utc,
        "valid_end_utc": block_end_valid_utc,
        "driver": driver,
        "timing_value": round(gust_mph, 1),
        "timing_method": "Timeline timing uses highest QMD hourly mean gust within this 3-hour block",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", default="", help="Optional NBM cycle in YYYYMMDDHH format")
    args = parser.parse_args()

    cycle = parse_cycle_arg(args.cycle) or latest_available_qmd_cycle()
    generated = utc_now()

    print(f"Building QMD wind outputs for cycle {cycle:%Y-%m-%d %HZ}")

    max24 = extract_24hr_max_wind(cycle)
    hourly = extract_hourly_qmd_mean_wind(cycle)

    best = max24["selected_risk"]
    display = max24["display_value"]

    mean_gust_mph = float(display["gust_mph"])
    mean_gust_kt = float(display["gust_kt"])
    mean_gust_mps = float(display["gust_mps"])

    ok_hourly = [h for h in hourly if h.get("status") == "ok" and h.get("gust_mph") is not None]
    if ok_hourly:
        peak_hour = max(ok_hourly, key=lambda h: float(h["gust_mph"]))
        peak_fxx = int(peak_hour["fxx"])
        peak_valid_utc = peak_hour.get("valid_utc")
    else:
        peak_fxx = 24
        peak_valid_utc = None

    peak_start_fxx = max(1, peak_fxx - 1)
    peak_end_fxx = min(48, peak_fxx + 1)

    threats_path = DOCS / "threats.json"
    threats_payload = load_json(
        threats_path,
        {
            "site": "KRNO",
            "valid_period": "next_48_hours",
            "threats": {},
            "hazards": [],
        },
    )

    threats_payload["generated_utc"] = generated
    threats_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    threats_payload["cycle"] = f"NBM QMD {cycle.strftime('%HZ')}"
    threats_payload.setdefault("threats", {})

    wind_payload = {
        "prob": round(float(best["probability"]), 1),
        "risk": int(best["risk"]),
        "risk_label": best["risk_label"],
        "level": int(best["impact_level"]),
        "metric": f"24-hr max gust: {mean_gust_mph:.0f} mph",
        "display_label": "24-hr max gust",
        "display_value": f"{mean_gust_mph:.0f} mph",
        "g24_mps": round(mean_gust_mps, 2),
        "g24_mph": mean_gust_mph,
        "g24_kt": mean_gust_kt,
        "window": "24 hr",
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "peak_valid_utc": peak_valid_utc,
        "ops_label": best["ops_label"],
        "driver": (
            f"QMD 24-hr max 10m gust mean {mean_gust_mph:.1f} mph; "
            f"{best['probability']:.1f}% chance {best['label']}"
        ),
        "source_fxx": 24,
        "source_idx_line": max24["source_idx_line"],
        "airport_threshold_probabilities": max24["airport_threshold_probabilities"],
        "risk_candidates": max24["risk_candidates"],
        "methodology": (
            "Wind card magnitude uses the mean value from the QMD 24-hour maximum "
            "10-meter wind gust grid, labeled in the IDX as "
            "'GUST:10 m above ground:0-1 day max fcst:'. Wind risk probabilities "
            "are calculated from the QMD 24-hour max gust percentile curve using "
            "linear interpolation for >30, >45, >58, and >65 mph. Wind timeline "
            "timing uses QMD hourly mean 10-meter gusts."
        ),
    }

    threats_payload["threats"]["WIND"] = wind_payload

    hazards = threats_payload.setdefault("hazards", [])
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
                    "metric": f"24-hr max gust: {mean_gust_mph:.0f} mph",
                    "display_label": "24-hr max gust",
                    "display_value": f"{mean_gust_mph:.0f} mph",
                    "g24_mph": mean_gust_mph,
                    "g24_kt": mean_gust_kt,
                    "ops_label": best["ops_label"],
                    "driver": (
                        f"QMD 24-hr max 10m gust mean {mean_gust_mph:.1f} mph; "
                        f"{best['probability']:.1f}% chance {best['label']}"
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
                "metric": f"24-hr max gust: {mean_gust_mph:.0f} mph",
                "display_label": "24-hr max gust",
                "display_value": f"{mean_gust_mph:.0f} mph",
                "g24_mph": mean_gust_mph,
                "g24_kt": mean_gust_kt,
                "ops_label": best["ops_label"],
                "driver": (
                    f"QMD 24-hr max 10m gust mean {mean_gust_mph:.1f} mph; "
                    f"{best['probability']:.1f}% chance {best['label']}"
                ),
            }
        )

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

    timeline_payload["site"] = "KRNO"
    timeline_payload["generated_utc"] = generated
    timeline_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    timeline_payload["cycle"] = f"NBM QMD {cycle.strftime('%HZ')}"
    timeline_payload["block_hours"] = 3

    old_blocks = timeline_payload.get("blocks", [])
    old_block_hazards = timeline_payload.get("block_hazards", [])

    new_blocks = []
    new_block_hazards = []

    for i in range(16):
        start_fxx = i * 3 + 1
        end_fxx = min((i + 1) * 3, 48)

        old_block = old_blocks[i] if i < len(old_blocks) and isinstance(old_blocks[i], dict) else {}
        old_hazards = old_block_hazards[i] if i < len(old_block_hazards) and isinstance(old_block_hazards[i], dict) else {}

        block_hours = [h for h in hourly if start_fxx <= int(h.get("fxx", 999)) <= end_fxx]

        block_start_valid_utc = (cycle + timedelta(hours=start_fxx)).isoformat().replace("+00:00", "Z")
        block_end_valid_utc = (cycle + timedelta(hours=end_fxx)).isoformat().replace("+00:00", "Z")

        block_eval = block_wind_timing(block_hours, block_start_valid_utc, block_end_valid_utc)

        new_block = dict(old_block)
        new_block["start_fxx"] = start_fxx
        new_block["end_fxx"] = end_fxx
        new_block["GST"] = block_eval.get("gust_mph")
        new_block["gust_mph"] = block_eval.get("gust_mph")
        new_block["gust_kt"] = block_eval.get("gust_kt")

        new_hazard_block = dict(old_hazards)
        new_hazard_block["WIND"] = block_eval

        new_blocks.append(new_block)
        new_block_hazards.append(new_hazard_block)

    timeline_payload["blocks"] = new_blocks
    timeline_payload["block_hazards"] = new_block_hazards

    threats_path.write_text(json.dumps(threats_payload, indent=2))
    timeline_path.write_text(json.dumps(timeline_payload, indent=2))

    diagnostic = {
        "site": "KRNO",
        "source": "NBM QMD direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "hazard": "WIND",
        "max24": max24,
        "hourly_timing": hourly,
        "methodology": (
            "Card value is the QMD 24-hour maximum 10-meter gust mean field. "
            "Risk probabilities are interpolated from the QMD 24-hour maximum gust "
            "percentile curve. Timeline timing is from hourly QMD mean gust fields."
        ),
    }

    (DATA / "nbm_qmd_wind.json").write_text(json.dumps(diagnostic, indent=2))
    (DATA / "krno_wind_risk.json").write_text(json.dumps(wind_payload, indent=2))
    (DATA / "krno_wind_hourly.json").write_text(json.dumps({"hours": hourly}, indent=2))
    (DATA / "krno_wind_debug.json").write_text(json.dumps(diagnostic, indent=2))

    print("Updated docs/threats.json WIND")
    print("Updated docs/timeline.json WIND")
    print("Wrote data/nbm_qmd_wind.json")
    print("Wrote data/krno_wind_risk.json")
    print("Wrote data/krno_wind_hourly.json")
    print("Wrote data/krno_wind_debug.json")


if __name__ == "__main__":
    main()
