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

SITE = "KRNO"
KRNO_LAT = 39.4991
KRNO_LON = -119.7681

MM_TO_IN = 0.03937007874015748

# NBM Core FICEAC = freezing rain accumulation probability.
# IDX fields found:
# FICEAC:surface:0-6 hour acc fcst:prob >0.254
# FICEAC:surface:0-6 hour acc fcst:prob >2.54
# FICEAC:surface:0-6 hour acc fcst:prob >6.35
# FICEAC:surface:0-6 hour acc fcst:prob >12.7
# FICEAC:surface:0-6 hour acc fcst:prob >25.4
#
# Thresholds are in mm liquid equivalent.
FZRA_THRESHOLDS = {
    "gt_0p01_in_6hr": {
        "threshold_mm": 0.254,
        "threshold_in": 0.01,
        "impact_level": 2,
        "label": ">0.01 in / 6 hr",
        "ops_label": "Treatment awareness / slick surfaces possible",
    },
    "gt_0p10_in_6hr": {
        "threshold_mm": 2.54,
        "threshold_in": 0.10,
        "impact_level": 3,
        "label": ">0.10 in / 6 hr",
        "ops_label": "Active pavement treatment likely",
    },
    "gt_0p25_in_6hr": {
        "threshold_mm": 6.35,
        "threshold_in": 0.25,
        "impact_level": 4,
        "label": ">0.25 in / 6 hr",
        "ops_label": "Significant icing / ground ops impacts",
    },
    "gt_0p50_in_6hr": {
        "threshold_mm": 12.7,
        "threshold_in": 0.50,
        "impact_level": 5,
        "label": ">0.50 in / 6 hr",
        "ops_label": "Major icing risk",
    },
}

# Keep 1.00" as diagnostic only, not used for primary risk.
FZRA_DIAGNOSTIC_THRESHOLDS = {
    "gt_1p00_in_6hr": {
        "threshold_mm": 25.4,
        "threshold_in": 1.00,
        "impact_level": 5,
        "label": ">1.00 in / 6 hr",
        "ops_label": "Extreme icing diagnostic threshold",
    }
}

FXX_HOURS = [6, 12, 18, 24, 30, 36, 42, 48]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_cycle_arg(cycle_arg: str | None) -> datetime | None:
    if not cycle_arg:
        return None

    cleaned = cycle_arg.strip()
    if not cleaned:
        return None

    if len(cleaned) != 10 or not cleaned.isdigit():
        raise ValueError("Cycle must use YYYYMMDDHH format, for example 2026051812")

    return datetime.strptime(cleaned, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def latest_cycle_utc() -> datetime:
    """
    Use a likely-complete NBM cycle.

    NOMADS can lag or briefly return partial files. Use a 12-hour lag to reduce
    partial-cycle failures.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=12)


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

    text = response.text
    lowered = text[:500].lower()

    if "<html" in lowered or "<!doctype html" in lowered:
        raise RuntimeError(f"Non-IDX HTML response from {url}")

    return text


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
        f"{value:.2f}",
        f"{value:.3f}",
        f"{value:.4f}",
        f"{value:.5f}",
    }

    if abs(value - 0.254) < 0.000001:
        variants.update({"0.254", "0.2540", "0.25400"})
    if abs(value - 2.54) < 0.000001:
        variants.update({"2.54", "2.540", "2.5400"})
    if abs(value - 6.35) < 0.000001:
        variants.update({"6.35", "6.350", "6.3500"})
    if abs(value - 12.7) < 0.000001:
        variants.update({"12.7", "12.70", "12.700"})
    if abs(value - 25.4) < 0.000001:
        variants.update({"25.4", "25.40", "25.400"})

    return sorted(variants, key=len, reverse=True)


def line_matches_six_hour_ficeac(line: str, fxx: int) -> bool:
    upper = line.upper()

    if ":FICEAC:" not in upper:
        return False

    if "PROB >" not in upper:
        return False

    start = fxx - 6
    end = fxx
    expected = f"{start}-{end} hour acc fcst".lower()

    return expected in line.lower()


def find_ficeac_probability_row(
    idx_rows: list[dict[str, Any]],
    fxx: int,
    threshold_mm: float,
) -> dict[str, Any] | None:
    for row in idx_rows:
        line = row["line"]

        if not line_matches_six_hour_ficeac(line, fxx):
            continue

        lower = line.lower()

        for variant in threshold_text_variants(threshold_mm):
            if f"prob >{variant}" in lower:
                return row

    return None


def find_ficeac_deterministic_row(
    idx_rows: list[dict[str, Any]],
    fxx: int,
) -> dict[str, Any] | None:
    start = fxx - 6
    end = fxx
    expected = f"{start}-{end} hour acc fcst".lower()

    for row in idx_rows:
        line = row["line"]
        upper = line.upper()
        lower = line.lower()

        if ":FICEAC:" not in upper:
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
            backend_kwargs={"indexpath": "", "errors": "ignore"},
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


def no_fzra_best(period: dict[str, Any]) -> dict[str, Any]:
    return {
        "threshold_key": "zero_fzra",
        "threshold_mm": 0.0,
        "threshold_in": 0.0,
        "impact_level": 0,
        "probability": 0.0,
        "risk": 0,
        "risk_label": "None",
        "label": '0" / 6 hr',
        "ops_label": "No freezing rain signal",
        "fxx": period["fxx"],
        "valid_utc": period["valid_utc"],
        "start_fxx": period["start_fxx"],
        "end_fxx": period["end_fxx"],
    }


def evaluate_period_risk(period: dict[str, Any]) -> dict[str, Any]:
    candidates = []

    for key, threshold in FZRA_THRESHOLDS.items():
        probability = float(period["threshold_probabilities"].get(key, {}).get("probability_percent", 0.0))
        impact_level = int(threshold["impact_level"])
        risk = matrix_risk(probability, impact_level)

        candidates.append(
            {
                "threshold_key": key,
                "threshold_mm": threshold["threshold_mm"],
                "threshold_in": threshold["threshold_in"],
                "impact_level": impact_level,
                "probability": probability,
                "risk": risk,
                "risk_label": risk_label(risk),
                "label": threshold["label"],
                "ops_label": threshold["ops_label"],
                "fxx": period["fxx"],
                "valid_utc": period["valid_utc"],
                "start_fxx": period["start_fxx"],
                "end_fxx": period["end_fxx"],
            }
        )

    if all(float(c["probability"]) <= 0 for c in candidates):
        return {"best": no_fzra_best(period), "candidates": candidates}

    best = max(candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))
    return {"best": best, "candidates": candidates}


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def extract_fzra_periods(cycle: datetime) -> list[dict[str, Any]]:
    results = []

    for fxx in FXX_HOURS:
        print(f"Processing Core FZRA f{fxx:03d}")

        grib_url = core_grib_url(cycle, fxx)
        idx_url = core_idx_url(cycle, fxx)
        valid_utc = (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")
        start_fxx = fxx - 5
        end_fxx = fxx

        try:
            idx_text = fetch_text(idx_url)
            idx_rows = parse_idx(idx_text)
        except Exception as exc:
            results.append(
                {
                    "fxx": fxx,
                    "start_fxx": start_fxx,
                    "end_fxx": end_fxx,
                    "valid_utc": valid_utc,
                    "status": "error",
                    "message": f"Could not fetch/parse IDX: {exc}",
                    "threshold_probabilities": {},
                    "diagnostic_probabilities": {},
                    "display_fzra_in": None,
                }
            )
            continue

        threshold_probs = {}

        for key, threshold in FZRA_THRESHOLDS.items():
            row = find_ficeac_probability_row(idx_rows, fxx, threshold["threshold_mm"])

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
                    label=f"core_fzra_f{fxx:03d}_{key}",
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

        diagnostic_probs = {}

        for key, threshold in FZRA_DIAGNOSTIC_THRESHOLDS.items():
            row = find_ficeac_probability_row(idx_rows, fxx, threshold["threshold_mm"])

            if row is None:
                diagnostic_probs[key] = {
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
                    label=f"core_fzra_f{fxx:03d}_{key}",
                )

                diagnostic_probs[key] = {
                    **threshold,
                    "probability_percent": round(float(probability), 1),
                    "status": "ok",
                    "variable": var_name,
                    "idx_line": row["line"],
                }

            except Exception as exc:
                diagnostic_probs[key] = {
                    **threshold,
                    "probability_percent": 0.0,
                    "status": "error",
                    "message": str(exc),
                    "idx_line": row["line"],
                }

        display_fzra_in = None
        display_source = None
        display_idx_line = None

        det_row = find_ficeac_deterministic_row(idx_rows, fxx)
        if det_row is not None:
            try:
                _, fzra_mm = extract_core_value(
                    grib_url=grib_url,
                    row=det_row,
                    label=f"core_fzra_f{fxx:03d}_display",
                )
                display_fzra_in = round(float(fzra_mm) * MM_TO_IN, 3)
                display_source = "NBM Core deterministic 6-hour FICEAC"
                display_idx_line = det_row["line"]
            except Exception as exc:
                print(f"Warning: failed display FICEAC f{fxx:03d}: {exc}")

        result = {
            "fxx": fxx,
            "start_fxx": start_fxx,
            "end_fxx": end_fxx,
            "status": "ok",
            "valid_utc": valid_utc,
            "grib_url": grib_url,
            "idx_url": idx_url,
            "threshold_probabilities": threshold_probs,
            "diagnostic_probabilities": diagnostic_probs,
            "display_fzra_in": display_fzra_in,
            "display_source": display_source,
            "display_idx_line": display_idx_line,
        }

        result["risk_evaluation"] = evaluate_period_risk(result)
        results.append(result)

    return results


def best_fzra_result(ok_periods: list[dict[str, Any]]) -> dict[str, Any]:
    all_candidates = []
    for period in ok_periods:
        all_candidates.extend(period["risk_evaluation"]["candidates"])

    if not all_candidates or all(float(c["probability"]) <= 0 for c in all_candidates):
        return no_fzra_best(ok_periods[0])

    return max(all_candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))


def period_for_block(ok_periods: list[dict[str, Any]], start_fxx: int, end_fxx: int) -> dict[str, Any] | None:
    midpoint = (start_fxx + end_fxx) / 2.0

    for period in ok_periods:
        if period["start_fxx"] <= midpoint <= period["end_fxx"]:
            return period

    return None


def block_fzra_risk(ok_periods: list[dict[str, Any]], start_fxx: int, end_fxx: int) -> dict[str, Any]:
    period = period_for_block(ok_periods, start_fxx, end_fxx)

    if period is None:
        return {
            "prob": 0.0,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": '0" / 6 hr',
            "ops_label": "No freezing rain signal",
            "fzra_6hr_in": None,
            "source_fxx": None,
            "source_window": None,
            "driver": "No freezing rain signal",
        }

    best = period["risk_evaluation"]["best"]
    prob = round(float(best["probability"]), 1)
    risk = int(best["risk"])
    level = int(best["impact_level"])

    return {
        "prob": prob,
        "risk": risk,
        "risk_label": risk_label(risk),
        "level": level,
        "threshold_in": best["threshold_in"],
        "threshold_mm": best["threshold_mm"],
        "metric": best["label"],
        "ops_label": best["ops_label"],
        "fzra_6hr_in": period.get("display_fzra_in"),
        "source_fxx": best["fxx"],
        "source_window": f"f{period['start_fxx']:03d}-f{period['end_fxx']:03d}",
        "driver": "No freezing rain signal" if prob <= 0 else f"{prob:.1f}% chance {best['label']}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", default="", help="Optional NBM cycle in YYYYMMDDHH format")
    args = parser.parse_args()

    cycle = parse_cycle_arg(args.cycle) or latest_cycle_utc()
    generated = utc_now()

    print(f"Building Core FZRA outputs for cycle {cycle:%Y-%m-%d %HZ}")

    periods = extract_fzra_periods(cycle)
    ok_periods = [p for p in periods if p.get("status") == "ok"]

    if not ok_periods:
        raise RuntimeError("No Core FZRA periods extracted successfully.")

    best = best_fzra_result(ok_periods)
    best_period = next((p for p in ok_periods if p["fxx"] == best["fxx"]), ok_periods[0])

    display_fzra_in = best_period.get("display_fzra_in")

    if display_fzra_in is None:
        if best["threshold_key"] == "zero_fzra":
            display_value = '0"'
        else:
            display_value = best["label"]
    elif display_fzra_in <= 0:
        display_value = '0"'
    elif display_fzra_in < 0.01:
        display_value = "Trace"
    else:
        display_value = f'{display_fzra_in:.2f}"'

    peak_start_fxx = int(best["start_fxx"])
    peak_end_fxx = int(best["end_fxx"])

    selected_prob = round(float(best["probability"]), 1)
    selected_risk = int(best["risk"])
    selected_level = int(best["impact_level"])
    selected_driver = "No freezing rain signal" if selected_prob <= 0 else f"{selected_prob:.1f}% chance {best['label']}"

    fzra_payload = {
        "site": SITE,
        "source": "NBM Core direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "hazard": "FZRA",
        "prob": selected_prob,
        "risk": selected_risk,
        "risk_label": risk_label(selected_risk),
        "level": selected_level,
        "metric": best["label"],
        "display_label": "6-hr freezing rain",
        "display_value": display_value,
        "fzra_6hr_in": round(float(display_fzra_in), 3) if display_fzra_in is not None else None,
        "threshold_in": best["threshold_in"],
        "threshold_mm": best["threshold_mm"],
        "window": "6 hr",
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "ops_label": best["ops_label"],
        "driver": selected_driver,
        "diagnostic_probabilities": best_period.get("diagnostic_probabilities", {}),
        "methodology": (
            "Freezing rain risk uses official NBM Core 6-hour FICEAC exceedance probabilities. "
            "Thresholds are >0.01, >0.10, >0.25, and >0.50 inches in 6 hours, mapped to "
            "impact levels 2 through 5. Each threshold probability is passed through the "
            "probability x impact risk matrix. If all probabilities are zero, selected risk is None."
        ),
    }

    diagnostic = {
        "site": SITE,
        "source": "NBM Core direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "hazard": "FZRA",
        "selected_risk": best,
        "selected_period": {
            "fxx": best_period["fxx"],
            "start_fxx": best_period["start_fxx"],
            "end_fxx": best_period["end_fxx"],
            "valid_utc": best_period["valid_utc"],
            "display_fzra_in": best_period.get("display_fzra_in"),
            "display_source": best_period.get("display_source"),
        },
        "thresholds": FZRA_THRESHOLDS,
        "diagnostic_thresholds": FZRA_DIAGNOSTIC_THRESHOLDS,
        "periods": periods,
        "methodology": fzra_payload["methodology"],
    }

    periods_output = {
        "site": SITE,
        "source": "NBM Core direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "periods": periods,
    }

    # Update docs/threats.json.
    threats_path = DOCS / "threats.json"
    threats_payload = load_json(
        threats_path,
        {
            "site": SITE,
            "valid_period": "next_48_hours",
            "threats": {},
            "hazards": [],
        },
    )

    threats_payload["generated_utc"] = generated
    threats_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    threats_payload["cycle"] = f"NBM Core {cycle.strftime('%HZ')}"
    threats_payload.setdefault("threats", {})
    threats_payload["threats"]["FZRA"] = fzra_payload

    hazards = threats_payload.setdefault("hazards", [])
    found = False

    hazard_update = {
        "id": "FZRA",
        "name": "Freezing Rain",
        "risk_level": selected_risk,
        "risk_label": risk_label(selected_risk),
        "impact_level": selected_level,
        "probability": selected_prob,
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "metric": best["label"],
        "display_label": "6-hr freezing rain",
        "display_value": display_value,
        "fzra_6hr_in": round(float(display_fzra_in), 3) if display_fzra_in is not None else None,
        "ops_label": best["ops_label"],
        "driver": selected_driver,
    }

    for hazard in hazards:
        if hazard.get("id") == "FZRA":
            hazard.update(hazard_update)
            found = True
            break

    if not found:
        hazards.append(hazard_update)

    write_json(threats_path, threats_payload)

    # Update docs/timeline.json.
    # Frontend expects 16 blocks x 3 hours = 48 hours.
    timeline_path = DOCS / "timeline.json"
    timeline_payload = load_json(
        timeline_path,
        {
            "site": SITE,
            "block_hours": 3,
            "blocks": [],
            "block_hazards": [],
        },
    )

    timeline_payload["site"] = SITE
    timeline_payload["generated_utc"] = generated
    timeline_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    timeline_payload["cycle"] = f"NBM Core {cycle.strftime('%HZ')}"
    timeline_payload["block_hours"] = 3

    old_blocks = timeline_payload.get("blocks", [])
    old_block_hazards = timeline_payload.get("block_hazards", [])

    new_blocks = []
    new_block_hazards = []

    for i in range(16):
        start_fxx = i * 3 + 1
        end_fxx = min((i + 1) * 3, 48)

        old_block = old_blocks[i] if i < len(old_blocks) and isinstance(old_blocks[i], dict) else {}
        old_hazards = (
            old_block_hazards[i]
            if i < len(old_block_hazards) and isinstance(old_block_hazards[i], dict)
            else {}
        )

        block_eval = block_fzra_risk(ok_periods, start_fxx, end_fxx)

        new_block = dict(old_block)
        new_block["start_fxx"] = start_fxx
        new_block["end_fxx"] = end_fxx
        new_block["FZRA"] = block_eval.get("fzra_6hr_in")

        new_hazard_block = dict(old_hazards)
        new_hazard_block["FZRA"] = block_eval

        new_blocks.append(new_block)
        new_block_hazards.append(new_hazard_block)

    timeline_payload["blocks"] = new_blocks
    timeline_payload["block_hazards"] = new_block_hazards

    write_json(timeline_path, timeline_payload)

    # Write output files.
    write_json(DATA / "nbm_core_fzra.json", diagnostic)
    write_json(DATA / "krno_fzra_risk.json", fzra_payload)
    write_json(DATA / "krno_fzra_periods.json", periods_output)
    write_json(DATA / "krno_fzra_debug.json", diagnostic)

    print("Updated docs/threats.json FZRA")
    print("Updated docs/timeline.json FZRA")
    print("Wrote data/nbm_core_fzra.json")
    print("Wrote data/krno_fzra_risk.json")
    print("Wrote data/krno_fzra_periods.json")
    print("Wrote data/krno_fzra_debug.json")


if __name__ == "__main__":
    main()
