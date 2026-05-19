from __future__ import annotations

import argparse
import json
import math
import tempfile
from datetime import datetime, timezone, timedelta
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

M_TO_IN = 39.37007874015748

# NBM Core ASNOW is 1-hour accumulated snow probability.
# Thresholds below are meters of snow accumulation.
SNOW_THRESHOLDS = {
    "gt_0p10_in_hr": {
        "threshold_in": 0.10,
        "threshold_m": 0.00254,
        "impact_level": 2,
        "label": ">0.10 in / hr",
        "ops_label": "Plow/treatment operations possible",
    },
    "gt_0p50_in_hr": {
        "threshold_in": 0.50,
        "threshold_m": 0.0127,
        "impact_level": 3,
        "label": ">0.50 in / hr",
        "ops_label": "Active plow operations",
    },
    "gt_1p00_in_hr": {
        "threshold_in": 1.00,
        "threshold_m": 0.0254,
        "impact_level": 4,
        "label": ">1.00 in / hr",
        "ops_label": "Significant plow operations",
    },
    "gt_2p00_in_hr": {
        "threshold_in": 2.00,
        "threshold_m": 0.0508,
        "impact_level": 5,
        "label": ">2.00 in / hr",
        "ops_label": "High-impact plow operations",
    },
}

FXX_HOURS = list(range(1, 49))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_cycle_arg(cycle_arg: str | None) -> datetime | None:
    if not cycle_arg:
        return None

    cleaned = cycle_arg.strip()
    if not cleaned:
        return None

    if len(cleaned) != 10 or not cleaned.isdigit():
        raise ValueError("Cycle must use YYYYMMDDHH format, for example 2026051712")

    return datetime.strptime(cleaned, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def latest_cycle_utc() -> datetime:
    """
    Use the most recent likely complete 6-hour NBM cycle.
    Lag one cycle to reduce failures from partially available NOMADS files.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def core_grib_url(cycle: datetime, fxx: int) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
        f"blend.{ymd}/{hh}/core/blend.t{hh}z.core.f{fxx:03d}.co.grib2"
    )


def core_idx_url(cycle: datetime, fxx: int) -> str:
    return core_grib_url(cycle, fxx) + ".idx"


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


def threshold_text_variants(value: float) -> list[str]:
    variants = {
        f"{value:g}",
        f"{value:.4f}",
        f"{value:.5f}",
        f"{value:.6f}",
    }

    if abs(value - 0.00254) < 0.000001:
        variants.update({"0.00254", "0.002540"})
    if abs(value - 0.0127) < 0.000001:
        variants.update({"0.0127", "0.01270", "0.012700"})
    if abs(value - 0.0254) < 0.000001:
        variants.update({"0.0254", "0.02540", "0.025400"})
    if abs(value - 0.0508) < 0.000001:
        variants.update({"0.0508", "0.05080", "0.050800"})

    return sorted(variants, key=len, reverse=True)


def line_matches_one_hour_asnow(line: str, fxx: int) -> bool:
    upper = line.upper()

    if ":ASNOW:" not in upper:
        return False

    if "PROB >" not in upper:
        return False

    # Core uses exact forecast-period wording like:
    # ASNOW:surface:0-1 hour acc fcst:prob >0.00254
    # ASNOW:surface:1-2 hour acc fcst:prob >0.00254
    start = fxx - 1
    end = fxx
    expected = f"{start}-{end} hour acc fcst".lower()

    return expected in line.lower()


def find_asnow_probability_row(
    idx_rows: list[dict[str, Any]],
    fxx: int,
    threshold_m: float,
) -> dict[str, Any] | None:
    for row in idx_rows:
        line = row["line"]

        if not line_matches_one_hour_asnow(line, fxx):
            continue

        lower = line.lower()
        for variant in threshold_text_variants(threshold_m):
            if f"prob >{variant}" in lower:
                return row

    return None


def find_asnow_percentile_or_deterministic_row(
    idx_rows: list[dict[str, Any]],
    fxx: int,
) -> dict[str, Any] | None:
    """
    Optional display value. Use deterministic ASNOW if available.
    Most important output is probability; display amount can be null.
    """
    start = fxx - 1
    end = fxx
    expected = f"{start}-{end} hour acc fcst".lower()

    for row in idx_rows:
        line = row["line"]
        upper = line.upper()
        lower = line.lower()

        if ":ASNOW:" not in upper:
            continue
        if expected not in lower:
            continue
        if "PROB >" in upper:
            continue

        return row

    return None


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
    if lon < 0:
        return lon + 360.0
    return lon


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

        if lat.dims:
            for dim, idx in zip(lat.dims, [iy, ix]):
                if dim in dims:
                    indexers[dim] = idx

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


def extract_core_value(grib_url: str, row: dict[str, Any], label: str) -> tuple[str, float]:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / f"{label}.grib2"
        download_grib_message(grib_url, row, path)
        return extract_value_from_message(path)


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


def no_snow_best(hour: dict[str, Any]) -> dict[str, Any]:
    return {
        "threshold_key": "zero_snow",
        "threshold_in": 0.0,
        "threshold_m": 0.0,
        "impact_level": 1,
        "probability": 0.0,
        "risk": 0,
        "risk_label": "None",
        "label": '0" / hr',
        "ops_label": "No plow/treatment operations",
        "fxx": hour["fxx"],
        "valid_utc": hour["valid_utc"],
    }


def evaluate_hour_risk(hour: dict[str, Any]) -> dict[str, Any]:
    candidates = []

    for key, threshold in SNOW_THRESHOLDS.items():
        probability = float(hour["threshold_probabilities"].get(key, {}).get("probability_percent", 0.0))
        impact_level = int(threshold["impact_level"])
        risk = matrix_risk(probability, impact_level)

        candidates.append(
            {
                "threshold_key": key,
                "threshold_in": threshold["threshold_in"],
                "threshold_m": threshold["threshold_m"],
                "impact_level": impact_level,
                "probability": probability,
                "risk": risk,
                "risk_label": risk_label(risk),
                "label": threshold["label"],
                "ops_label": threshold["ops_label"],
                "fxx": hour["fxx"],
                "valid_utc": hour["valid_utc"],
            }
        )

    if all(float(c["probability"]) <= 0 for c in candidates):
        return {
            "best": no_snow_best(hour),
            "candidates": candidates,
        }

    # Highest matrix risk wins. Tie-breaker favors higher probability, then higher impact.
    best = max(candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))

    return {
        "best": best,
        "candidates": candidates,
    }


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def extract_snow_hours(cycle: datetime) -> list[dict[str, Any]]:
    results = []

    for fxx in FXX_HOURS:
        print(f"Processing Core snow f{fxx:03d}")

        grib_url = core_grib_url(cycle, fxx)
        idx_url = core_idx_url(cycle, fxx)
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
                    "threshold_probabilities": {},
                    "display_asnow_in": None,
                }
            )
            continue

        threshold_probs = {}

        for key, threshold in SNOW_THRESHOLDS.items():
            row = find_asnow_probability_row(
                idx_rows=idx_rows,
                fxx=fxx,
                threshold_m=threshold["threshold_m"],
            )

            if row is None:
                threshold_probs[key] = {
                    **threshold,
                    "probability_percent": 0.0,
                    "status": "missing",
                    "idx_line": None,
                }
                continue

            try:
                var_name, probability = extract_core_value(
                    grib_url=grib_url,
                    row=row,
                    label=f"core_snow_f{fxx:03d}_{key}",
                )

                threshold_probs[key] = {
                    **threshold,
                    "probability_percent": round(float(probability), 1),
                    "status": "ok",
                    "variable": var_name,
                    "idx_line": row["line"],
                }

            except Exception as exc:
                threshold_probs[key] = {
                    **threshold,
                    "probability_percent": 0.0,
                    "status": "error",
                    "message": str(exc),
                    "idx_line": row["line"],
                }

        display_asnow_in = None
        display_source = None
        display_idx_line = None

        det_row = find_asnow_percentile_or_deterministic_row(idx_rows, fxx)
        if det_row is not None:
            try:
                _, snow_m = extract_core_value(
                    grib_url=grib_url,
                    row=det_row,
                    label=f"core_snow_f{fxx:03d}_display",
                )
                display_asnow_in = round(float(snow_m) * M_TO_IN, 3)
                display_source = "NBM Core deterministic 1-hour ASNOW"
                display_idx_line = det_row["line"]
            except Exception as exc:
                print(f"Warning: failed display ASNOW f{fxx:03d}: {exc}")

        result = {
            "fxx": fxx,
            "status": "ok",
            "valid_utc": valid_utc,
            "grib_url": grib_url,
            "idx_url": idx_url,
            "threshold_probabilities": threshold_probs,
            "display_asnow_in": display_asnow_in,
            "display_source": display_source,
            "display_idx_line": display_idx_line,
        }

        result["risk_evaluation"] = evaluate_hour_risk(result)
        results.append(result)

    return results


def best_snow_result(ok_hours: list[dict[str, Any]]) -> dict[str, Any]:
    all_candidates = []
    for hour in ok_hours:
        all_candidates.extend(hour["risk_evaluation"]["candidates"])

    if not all_candidates or all(float(c["probability"]) <= 0 for c in all_candidates):
        return no_snow_best(ok_hours[0])

    return max(all_candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))


def block_snow_risk(block_hours: list[dict[str, Any]]) -> dict[str, Any]:
    if not block_hours:
        return {
            "prob": 0.0,
            "risk": 0,
            "level": 1,
            "metric": '0" / hr',
            "driver": "No snow signal",
        }

    candidates = []
    for hour in block_hours:
        candidates.extend(hour["risk_evaluation"]["candidates"])

    if not candidates or all(float(c["probability"]) <= 0 for c in candidates):
        best = no_snow_best(block_hours[0])
    else:
        best = max(candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))

    display_values = [h.get("display_asnow_in") for h in block_hours if h.get("display_asnow_in") is not None]
    block_max_snow = max(display_values) if display_values else None

    return {
        "prob": round(float(best["probability"]), 1),
        "risk": int(best["risk"]),
        "level": int(best["impact_level"]),
        "threshold_in": best["threshold_in"],
        "threshold_m": best["threshold_m"],
        "metric": best["label"],
        "ops_label": best["ops_label"],
        "snow_1hr_in": block_max_snow,
        "source_fxx": best["fxx"],
        "driver": f"{best['probability']:.1f}% chance {best['label']}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", default="", help="Optional NBM cycle in YYYYMMDDHH format")
    args = parser.parse_args()

    cycle = parse_cycle_arg(args.cycle) or latest_cycle_utc()
    generated = utc_now()

    print(f"Building Core snow outputs for cycle {cycle:%Y-%m-%d %HZ}")

    hours = extract_snow_hours(cycle)
    ok_hours = [h for h in hours if h.get("status") == "ok"]

    if not ok_hours:
        raise RuntimeError("No Core snow hours extracted successfully.")

    best = best_snow_result(ok_hours)
    best_hour = next((h for h in ok_hours if h["fxx"] == best["fxx"]), ok_hours[0])

    display_snow_in = best_hour.get("display_asnow_in")
    if display_snow_in is None:
        available = [h for h in ok_hours if h.get("display_asnow_in") is not None]
        if available:
            best_hour = max(available, key=lambda h: h.get("display_asnow_in") or 0.0)
            display_snow_in = best_hour.get("display_asnow_in")

    if display_snow_in is None:
        if best["threshold_key"] == "zero_snow":
            display_value = '0"'
        else:
            display_value = best["label"]
    elif display_snow_in <= 0:
        display_value = '0"'
    elif display_snow_in < 0.1:
        display_value = "Trace"
    else:
        display_value = f'{display_snow_in:.2f}"'

    peak_fxx = int(best["fxx"])
    peak_start_fxx = max(1, peak_fxx - 1)
    peak_end_fxx = min(48, peak_fxx + 1)

    # Update threats.json.
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
    threats_payload["cycle"] = f"NBM Core {cycle.strftime('%HZ')}"
    threats_payload.setdefault("threats", {})

    snow_payload = {
        "prob": round(float(best["probability"]), 1),
        "risk": int(best["risk"]),
        "risk_label": risk_label(int(best["risk"])),
        "level": int(best["impact_level"]),
        "metric": best["label"],
        "display_label": "1-hr snow",
        "display_value": display_value,
        "snow_1hr_in": round(float(display_snow_in), 3) if display_snow_in is not None else None,
        "threshold_in": best["threshold_in"],
        "threshold_m": best["threshold_m"],
        "window": "1 hr",
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "ops_label": best["ops_label"],
        "driver": f"{best['probability']:.1f}% chance {best['label']}",
        "risk_candidates": [
            c
            for h in ok_hours
            for c in h["risk_evaluation"]["candidates"]
        ],
        "methodology": (
            "Snow risk uses official NBM Core 1-hour ASNOW exceedance probabilities. "
            "KRNO plow/treatment operations begin at trace/light snow. Thresholds are "
            ">0.10, >0.50, >1.00, and >2.00 inches in 1 hour, mapped to impact levels "
            "2 through 5. Each threshold probability is passed through the probability x "
            "impact risk matrix. If all probabilities are zero, selected risk is 0 inches "
            "per hour with None risk."
        ),
    }

    threats_payload["threats"]["SNOW"] = snow_payload

    hazards = threats_payload.setdefault("hazards", [])
    found = False

    for hazard in hazards:
        if hazard.get("id") == "SNOW":
            hazard.update(
                {
                    "id": "SNOW",
                    "name": "Snow",
                    "risk_level": int(best["risk"]),
                    "risk_label": risk_label(int(best["risk"])),
                    "impact_level": int(best["impact_level"]),
                    "probability": round(float(best["probability"]), 1),
                    "peak_start_fxx": peak_start_fxx,
                    "peak_end_fxx": peak_end_fxx,
                    "metric": best["label"],
                    "display_label": "1-hr snow",
                    "display_value": display_value,
                    "snow_1hr_in": round(float(display_snow_in), 3) if display_snow_in is not None else None,
                    "ops_label": best["ops_label"],
                    "driver": f"{best['probability']:.1f}% chance {best['label']}",
                }
            )
            found = True
            break

    if not found:
        hazards.append(
            {
                "id": "SNOW",
                "name": "Snow",
                "risk_level": int(best["risk"]),
                "risk_label": risk_label(int(best["risk"])),
                "impact_level": int(best["impact_level"]),
                "probability": round(float(best["probability"]), 1),
                "peak_start_fxx": peak_start_fxx,
                "peak_end_fxx": peak_end_fxx,
                "metric": best["label"],
                "display_label": "1-hr snow",
                "display_value": display_value,
                "snow_1hr_in": round(float(display_snow_in), 3) if display_snow_in is not None else None,
                "ops_label": best["ops_label"],
                "driver": f"{best['probability']:.1f}% chance {best['label']}",
            }
        )

    # Update timeline.json.
    timeline_path = DOCS / "timeline.json"
    timeline_payload = load_json(
        timeline_path,
        {
            "site": "KRNO",
            "block_hours": 6,
            "blocks": [],
            "block_hazards": [],
        },
    )

    timeline_payload["generated_utc"] = generated
    timeline_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    timeline_payload["cycle"] = f"NBM Core {cycle.strftime('%HZ')}"
    timeline_payload["block_hours"] = 6

    blocks = timeline_payload.setdefault("blocks", [])
    block_hazards = timeline_payload.setdefault("block_hazards", [])

    while len(blocks) < 8:
        bi = len(blocks)
        blocks.append({"start_fxx": bi * 6, "end_fxx": bi * 6 + 6})

    while len(block_hazards) < 8:
        block_hazards.append({})

    for i in range(8):
        start_fxx = i * 6 + 1
        end_fxx = min((i + 1) * 6, 48)

        block_hours = [h for h in ok_hours if start_fxx <= h["fxx"] <= end_fxx]
        block_eval = block_snow_risk(block_hours)

        blocks[i]["start_fxx"] = start_fxx
        blocks[i]["end_fxx"] = end_fxx
        blocks[i]["SNOW"] = block_eval.get("snow_1hr_in")

        block_hazards[i]["SNOW"] = block_eval

    threats_path.write_text(json.dumps(threats_payload, indent=2))
    timeline_path.write_text(json.dumps(timeline_payload, indent=2))

    diagnostic = {
        "site": "KRNO",
        "source": "NBM Core direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "hazard": "SNOW",
        "selected_risk": best,
        "selected_hour": {
            "fxx": best_hour["fxx"],
            "valid_utc": best_hour["valid_utc"],
            "display_asnow_in": best_hour.get("display_asnow_in"),
            "display_source": best_hour.get("display_source"),
        },
        "thresholds": SNOW_THRESHOLDS,
        "hours": hours,
        "methodology": (
            "Core 1-hour ASNOW probabilities are used directly for KRNO snow/plow risk. "
            "Plow/treatment operations begin with trace/light snow. Thresholds are >0.10, "
            ">0.50, >1.00, and >2.00 inches per hour."
        ),
    }

    (DATA / "nbm_core_snow.json").write_text(json.dumps(diagnostic, indent=2))

    # Also write the filenames expected by the standalone snow workflow if you used build-snow.yml.
    (DATA / "krno_snow_risk.json").write_text(json.dumps(snow_payload, indent=2))
    (DATA / "krno_snow_hourly.json").write_text(json.dumps({"hours": hours}, indent=2))
    (DATA / "krno_snow_debug.json").write_text(json.dumps(diagnostic, indent=2))

    print("Updated docs/threats.json SNOW")
    print("Updated docs/timeline.json SNOW")
    print("Wrote data/nbm_core_snow.json")
    print("Wrote data/krno_snow_risk.json")
    print("Wrote data/krno_snow_hourly.json")
    print("Wrote data/krno_snow_debug.json")


if __name__ == "__main__":
    main()
