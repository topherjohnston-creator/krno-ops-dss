from __future__ import annotations

import argparse
import json
import math
import re
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

FXX_HOURS = list(range(1, 49))

# User-defined KRNO Ops DSS lightning categories.
# These are treated as the operational risk category for lightning because the
# airport decision threshold is the probability of lightning itself.
LIGHTNING_BINS = [
    {
        "key": "lt_5_percent",
        "min_prob": 0.0,
        "max_prob": 5.0,
        "impact_level": 1,
        "risk": 1,
        "metric": "<5%",
        "ops_label": "Ramp/safety closure unlikely",
    },
    {
        "key": "5_to_25_percent",
        "min_prob": 5.0,
        "max_prob": 25.0,
        "impact_level": 2,
        "risk": 2,
        "metric": "5-25%",
        "ops_label": "Ramp/safety closure possible",
    },
    {
        "key": "25_to_50_percent",
        "min_prob": 25.0,
        "max_prob": 50.0,
        "impact_level": 3,
        "risk": 3,
        "metric": "25-50%",
        "ops_label": "Ramp/safety closure increasingly likely",
    },
    {
        "key": "50_to_75_percent",
        "min_prob": 50.0,
        "max_prob": 75.0,
        "impact_level": 4,
        "risk": 4,
        "metric": "50-75%",
        "ops_label": "Ramp/safety closure likely",
    },
    {
        "key": "gt_75_percent",
        "min_prob": 75.0,
        "max_prob": 101.0,
        "impact_level": 5,
        "risk": 5,
        "metric": ">75%",
        "ops_label": "Ramp/safety closure very likely",
    },
]


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
    Use an older likely-complete NBM cycle.

    NOMADS can expose partial current-cycle files. Lagging by 12 hours keeps
    this builder aligned with the other backend builders and avoids most 404s.
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
    return response.text


def parse_idx(idx_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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
        for j in range(i + 1, len(lines)):
            next_parts = lines[j].split(":")
            if len(next_parts) < 2:
                continue
            try:
                end_byte = int(next_parts[1]) - 1
                break
            except ValueError:
                continue

        rows.append(
            {
                "message_no": message_no,
                "start_byte": start_byte,
                "end_byte": end_byte,
                "line": line,
            }
        )

    return rows


def forecast_period_from_line(line: str) -> tuple[int | None, int | None]:
    """Return start/end forecast-hour window if present in the IDX line."""
    lower = line.lower()

    match = re.search(r"(\d+)\s*-\s*(\d+)\s*hour", lower)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = re.search(r"(\d+)\s*hour", lower)
    if match:
        hour = int(match.group(1))
        return hour, hour

    return None, None


def is_lightning_probability_line(line: str) -> bool:
    upper = line.upper()
    lower = line.lower()

    has_lightning_term = any(
        term in upper
        for term in (
            ":TSTM:",
            ":LTNG:",
            ":LTP:",
            "LIGHTNING",
            "THUNDER",
            "TSTM",
            "LTNG",
        )
    )

    if not has_lightning_term:
        return False

    # NBM Core lightning/thunder fields should be probability-like. Keep this
    # broad because NBM IDX wording can vary by cycle/version.
    has_probability_term = (
        "PROB" in upper
        or "PROBABILITY" in upper
        or "%" in line
        or "thunder" in lower
        or "tstm" in lower
        or "ltng" in lower
    )

    if not has_probability_term:
        return False

    # Avoid unrelated products if NBM ever adds diagnostic thunder fields.
    if any(bad in upper for bad in ("APCP", "ASNOW", "GUST", "VIS", "TMP", "TMAX", "TMIN")):
        return False

    return True


def lightning_row_score(row: dict[str, Any], fxx: int) -> tuple[int, int, int]:
    """
    Higher score is better.

    Prefer rows ending at the target fxx and, when multiple valid periods exist,
    prefer the shortest period. Then prefer explicit probability wording.
    """
    line = row["line"]
    upper = line.upper()
    start, end = forecast_period_from_line(line)

    exact_end_score = 0
    duration_score = 0

    if end == fxx:
        exact_end_score = 100
        if start is not None:
            duration = max(0, end - start)
            duration_score = max(0, 50 - duration)

    probability_score = 10 if "PROB" in upper or "PROBABILITY" in upper else 0
    direct_term_score = 5 if ":TSTM:" in upper or ":LTNG:" in upper else 0

    return exact_end_score + duration_score + probability_score + direct_term_score, probability_score, direct_term_score


def find_lightning_probability_row(idx_rows: list[dict[str, Any]], fxx: int) -> dict[str, Any] | None:
    candidates = [row for row in idx_rows if is_lightning_probability_line(row["line"])]

    if not candidates:
        return None

    # First choice: line period ends at fxx.
    ending_at_fxx = []
    for row in candidates:
        _, end = forecast_period_from_line(row["line"])
        if end == fxx:
            ending_at_fxx.append(row)

    if ending_at_fxx:
        return max(ending_at_fxx, key=lambda r: lightning_row_score(r, fxx))

    # Fallback: first probability-like lightning row in the file.
    return max(candidates, key=lambda r: lightning_row_score(r, fxx))


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
        indexers: dict[str, int] = {}

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


def normalize_probability(value: float) -> float:
    """
    NBM probability fields are normally percent values, but this protects against
    0-1 probability fields if encountered.
    """
    if value <= 1.0:
        return max(0.0, min(100.0, value * 100.0))
    return max(0.0, min(100.0, value))


def risk_label(risk: int) -> str:
    return {
        0: "None",
        1: "Little to None",
        2: "Minor",
        3: "Moderate",
        4: "Major",
        5: "Extreme",
    }.get(risk, "Unknown")


def evaluate_lightning_probability(probability: float) -> dict[str, Any]:
    probability = round(float(probability), 1)

    if probability <= 0:
        return {
            "prob": 0.0,
            "risk": 0,
            "risk_label": "None",
            "level": 1,
            "metric": "<5%",
            "ops_label": "No lightning signal",
            "driver": "0.0% chance of lightning",
            "threshold_key": "zero_lightning",
        }

    for item in LIGHTNING_BINS:
        if item["min_prob"] <= probability < item["max_prob"]:
            return {
                "prob": probability,
                "risk": int(item["risk"]),
                "risk_label": risk_label(int(item["risk"])),
                "level": int(item["impact_level"]),
                "metric": item["metric"],
                "ops_label": item["ops_label"],
                "driver": f"{probability:.1f}% chance of lightning",
                "threshold_key": item["key"],
            }

    top = LIGHTNING_BINS[-1]
    return {
        "prob": probability,
        "risk": int(top["risk"]),
        "risk_label": risk_label(int(top["risk"])),
        "level": int(top["impact_level"]),
        "metric": top["metric"],
        "ops_label": top["ops_label"],
        "driver": f"{probability:.1f}% chance of lightning",
        "threshold_key": top["key"],
    }


def no_lightning_hour(fxx: int, valid_utc: str, message: str = "No lightning probability row found") -> dict[str, Any]:
    evaluated = evaluate_lightning_probability(0.0)
    return {
        "fxx": fxx,
        "valid_utc": valid_utc,
        "status": "missing",
        "message": message,
        "probability_percent": 0.0,
        "risk_evaluation": evaluated,
    }


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def extract_lightning_hours(cycle: datetime) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for fxx in FXX_HOURS:
        print(f"Processing Core lightning f{fxx:03d}")

        grib_url = core_grib_url(cycle, fxx)
        idx_url = core_idx_url(cycle, fxx)
        valid_utc = (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")

        try:
            idx_text = fetch_text(idx_url)
            idx_rows = parse_idx(idx_text)
        except Exception as exc:
            results.append(
                no_lightning_hour(
                    fxx=fxx,
                    valid_utc=valid_utc,
                    message=f"Could not fetch/parse IDX: {exc}",
                )
            )
            continue

        row = find_lightning_probability_row(idx_rows, fxx)

        if row is None:
            results.append(no_lightning_hour(fxx=fxx, valid_utc=valid_utc))
            continue

        try:
            var_name, raw_probability = extract_core_value(
                grib_url=grib_url,
                row=row,
                label=f"core_lightning_f{fxx:03d}",
            )
            probability = round(normalize_probability(float(raw_probability)), 1)
            evaluated = evaluate_lightning_probability(probability)

            results.append(
                {
                    "fxx": fxx,
                    "valid_utc": valid_utc,
                    "status": "ok",
                    "grib_url": grib_url,
                    "idx_url": idx_url,
                    "idx_line": row["line"],
                    "variable": var_name,
                    "raw_probability_value": float(raw_probability),
                    "probability_percent": probability,
                    "risk_evaluation": evaluated,
                }
            )

        except Exception as exc:
            results.append(
                {
                    "fxx": fxx,
                    "valid_utc": valid_utc,
                    "status": "error",
                    "message": str(exc),
                    "grib_url": grib_url,
                    "idx_url": idx_url,
                    "idx_line": row["line"],
                    "probability_percent": 0.0,
                    "risk_evaluation": evaluate_lightning_probability(0.0),
                }
            )

    return results


def best_lightning_result(hours: list[dict[str, Any]]) -> dict[str, Any]:
    if not hours:
        return evaluate_lightning_probability(0.0) | {"fxx": 1, "valid_utc": None}

    best_hour = max(
        hours,
        key=lambda h: (
            int(h.get("risk_evaluation", {}).get("risk", 0)),
            float(h.get("probability_percent", 0.0)),
        ),
    )

    result = dict(best_hour["risk_evaluation"])
    result["fxx"] = best_hour["fxx"]
    result["valid_utc"] = best_hour["valid_utc"]
    result["source_status"] = best_hour.get("status")
    result["source_idx_line"] = best_hour.get("idx_line")
    return result


def block_lightning_risk(block_hours: list[dict[str, Any]]) -> dict[str, Any]:
    if not block_hours:
        return evaluate_lightning_probability(0.0)

    max_prob = max(float(h.get("probability_percent", 0.0)) for h in block_hours)
    evaluated = evaluate_lightning_probability(max_prob)

    source_hour = max(block_hours, key=lambda h: float(h.get("probability_percent", 0.0)))
    evaluated["source_fxx"] = source_hour.get("fxx")
    evaluated["source_valid_utc"] = source_hour.get("valid_utc")

    return evaluated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", default="", help="Optional NBM cycle in YYYYMMDDHH format")
    args = parser.parse_args()

    cycle = parse_cycle_arg(args.cycle) or latest_cycle_utc()
    generated = utc_now()

    print(f"Building Core lightning outputs for cycle {cycle:%Y-%m-%d %HZ}")

    hours = extract_lightning_hours(cycle)
    ok_or_missing_hours = [h for h in hours if h.get("status") in {"ok", "missing", "error"}]

    if not ok_or_missing_hours:
        raise RuntimeError("No Core lightning hours processed successfully.")

    best = best_lightning_result(ok_or_missing_hours)

    peak_fxx = int(best.get("fxx") or 1)
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

    lightning_payload = {
        "prob": round(float(best["prob"]), 1),
        "risk": int(best["risk"]),
        "risk_label": best["risk_label"],
        "level": int(best["level"]),
        "metric": best["metric"],
        "display_label": "Lightning chance",
        "display_value": best["metric"],
        "window": "1 hr",
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "ops_label": best["ops_label"],
        "driver": best["driver"],
        "source_fxx": peak_fxx,
        "source_valid_utc": best.get("valid_utc"),
        "threshold_bins": LIGHTNING_BINS,
        "methodology": (
            "Lightning risk uses official NBM Core lightning/thunder probability fields at KRNO. "
            "Operational categories are <5%, 5-25%, 25-50%, 50-75%, and >75% chance, "
            "corresponding to impact/risk levels 1 through 5 for ramp/safety closure decisions. "
            "If probability is zero, selected risk is None."
        ),
    }

    threats_payload["threats"]["LIGHTNING"] = lightning_payload

    hazards = threats_payload.setdefault("hazards", [])
    found = False

    hazard_update = {
        "id": "LIGHTNING",
        "name": "Lightning",
        "risk_level": int(best["risk"]),
        "risk_label": best["risk_label"],
        "impact_level": int(best["level"]),
        "probability": round(float(best["prob"]), 1),
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "metric": best["metric"],
        "display_label": "Lightning chance",
        "display_value": best["metric"],
        "ops_label": best["ops_label"],
        "driver": best["driver"],
    }

    for hazard in hazards:
        if hazard.get("id") == "LIGHTNING":
            hazard.update(hazard_update)
            found = True
            break

    if not found:
        hazards.append(hazard_update)

    # Update timeline.json.
    # Frontend expects 16 blocks x 3 hours = 48 hours.
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

        block_hours = [h for h in ok_or_missing_hours if start_fxx <= int(h["fxx"]) <= end_fxx]
        block_eval = block_lightning_risk(block_hours)

        new_block = dict(old_block)
        new_block["start_fxx"] = start_fxx
        new_block["end_fxx"] = end_fxx
        new_block["LIGHTNING"] = round(float(block_eval["prob"]), 1)
        new_block["lightning_prob"] = round(float(block_eval["prob"]), 1)

        new_hazard_block = dict(old_hazards)
        new_hazard_block["LIGHTNING"] = block_eval

        new_blocks.append(new_block)
        new_block_hazards.append(new_hazard_block)

    timeline_payload["blocks"] = new_blocks
    timeline_payload["block_hazards"] = new_block_hazards

    threats_path.write_text(json.dumps(threats_payload, indent=2))
    timeline_path.write_text(json.dumps(timeline_payload, indent=2))

    diagnostic = {
        "site": "KRNO",
        "source": "NBM Core direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "hazard": "LIGHTNING",
        "selected_risk": best,
        "threshold_bins": LIGHTNING_BINS,
        "hours": hours,
        "methodology": (
            "Core lightning/thunder probabilities are used directly for KRNO ramp/safety closure risk. "
            "Categories are <5%, 5-25%, 25-50%, 50-75%, and >75% chance."
        ),
    }

    (DATA / "nbm_core_lightning.json").write_text(json.dumps(diagnostic, indent=2))
    (DATA / "krno_lightning_risk.json").write_text(json.dumps(lightning_payload, indent=2))
    (DATA / "krno_lightning_hourly.json").write_text(json.dumps({"hours": hours}, indent=2))
    (DATA / "krno_lightning_debug.json").write_text(json.dumps(diagnostic, indent=2))

    print("Updated docs/threats.json LIGHTNING")
    print("Updated docs/timeline.json LIGHTNING")
    print("Wrote data/nbm_core_lightning.json")
    print("Wrote data/krno_lightning_risk.json")
    print("Wrote data/krno_lightning_hourly.json")
    print("Wrote data/krno_lightning_debug.json")


if __name__ == "__main__":
    main()
