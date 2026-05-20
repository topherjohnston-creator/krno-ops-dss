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

FXX_HOURS = list(range(1, 49))

# Lightning impact thresholds based on probability of lightning/thunder.
# These are not accumulation/exceedance thresholds. The NBM value itself is the probability.
LIGHTNING_IMPACT_THRESHOLDS = [
    {
        "min_prob": 75.0,
        "impact_level": 5,
        "metric": "Lightning chance: >75%",
        "ops_label": "Ramp/safety closure very likely",
    },
    {
        "min_prob": 50.0,
        "impact_level": 4,
        "metric": "Lightning chance: 50-75%",
        "ops_label": "Ramp/safety closure likely",
    },
    {
        "min_prob": 25.0,
        "impact_level": 3,
        "metric": "Lightning chance: 25-50%",
        "ops_label": "Ramp/safety closure possible",
    },
    {
        "min_prob": 5.0,
        "impact_level": 2,
        "metric": "Lightning chance: 5-25%",
        "ops_label": "Ramp/safety closure unlikely but possible",
    },
    {
        "min_prob": 0.0,
        "impact_level": 1,
        "metric": "Lightning chance: <5%",
        "ops_label": "No meaningful lightning operational impact",
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


def floor_to_6hr_cycle() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    return now.replace(hour=cycle_hour)


def core_grib_url(cycle: datetime, fxx: int) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
        f"blend.{ymd}/{hh}/core/blend.t{hh}z.core.f{fxx:03d}.co.grib2"
    )


def core_idx_url(cycle: datetime, fxx: int) -> str:
    return core_grib_url(cycle, fxx) + ".idx"


def url_exists(url: str, timeout: int = 15) -> bool:
    try:
        response = requests.get(url, timeout=timeout, stream=True)
        response.close()
        return response.status_code == 200
    except Exception:
        return False


def cycle_has_required_core_files(cycle: datetime, required_fxx: list[int]) -> bool:
    missing = []

    for fxx in required_fxx:
        url = core_idx_url(cycle, fxx)
        if not url_exists(url):
            missing.append(fxx)

    if missing:
        print(f"NBM Core {cycle:%Y-%m-%d %HZ} incomplete. Missing fxx: {missing}")
        return False

    return True


def latest_available_core_cycle_48hr() -> datetime:
    """
    Use the newest available NBM Core cycle that has f001-f048 IDX files.

    This replaces the old fixed 12-hour lag. It tries the latest synoptic
    cycle first, then steps backward only if needed.
    """
    latest = floor_to_6hr_cycle()
    required_fxx = list(range(1, 49))

    for lag_hours in [0, 6, 12, 18, 24, 30, 36, 42, 48]:
        candidate = latest - timedelta(hours=lag_hours)
        print(f"Checking NBM Core cycle {candidate:%Y-%m-%d %HZ}")

        if cycle_has_required_core_files(candidate, required_fxx):
            print(f"Using NBM Core cycle {candidate:%Y-%m-%d %HZ}")
            return candidate

    fallback = latest - timedelta(hours=12)
    print(f"Warning: no complete Core cycle found. Falling back to {fallback:%Y-%m-%d %HZ}")
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


def forecast_period_matches(line: str, fxx: int) -> bool:
    """
    Match forecast periods:
    f001 = 0-1 hour fcst
    f002 = 1-2 hour fcst
    etc.

    Also allow 3-hour thunder periods, such as:
    f003 = 0-3 hour acc fcst
    f006 = 3-6 hour acc fcst
    """
    lower = line.lower()
    start = fxx - 1
    end = fxx

    one_hour_periods = [
        f"{start}-{end} hour fcst",
        f"{start}-{end} hour acc fcst",
    ]

    if any(period in lower for period in one_hour_periods):
        return True

    # NBM thunder fields often appear as 3-hour probability forecast periods.
    if fxx % 3 == 0:
        three_hour_start = fxx - 3
        three_hour_end = fxx
        three_hour_periods = [
            f"{three_hour_start}-{three_hour_end} hour fcst",
            f"{three_hour_start}-{three_hour_end} hour acc fcst",
        ]
        if any(period in lower for period in three_hour_periods):
            return True

    return False


def is_probability_threshold_line(line: str) -> bool:
    lower = line.lower()

    # These are probability-of-exceedance/categorical threshold lines.
    # They are not the actual lightning probability value we want.
    bad_patterns = [
        "prob >",
        "prob >=",
        "prob <",
        "prob <=",
        "% level",
    ]

    return any(pattern in lower for pattern in bad_patterns)


def is_lightning_candidate_line(line: str, fxx: int) -> bool:
    """
    Prefer actual thunder/lightning probability fields, not threshold-probability lines.

    The line may contain "probability forecast"; that is okay for TSTM because
    the value itself is the probability. We only reject threshold exceedance
    strings like "prob >".
    """
    upper = line.upper()

    lightning_terms = [
        ":TSTM:",
        ":LTNG:",
        ":LTG:",
        ":LTP:",
        ":TSTORM:",
        ":THUNDER:",
    ]

    if not any(term in upper for term in lightning_terms):
        return False

    if not forecast_period_matches(line, fxx):
        return False

    if is_probability_threshold_line(line):
        return False

    return True


def find_lightning_row(idx_rows: list[dict[str, Any]], fxx: int) -> dict[str, Any] | None:
    candidates = [row for row in idx_rows if is_lightning_candidate_line(row["line"], fxx)]

    if not candidates:
        return None

    # Prefer TSTM because that is the standard thunder/lightning proxy.
    tstm_candidates = [row for row in candidates if ":TSTM:" in row["line"].upper()]
    if tstm_candidates:
        return tstm_candidates[0]

    return candidates[0]


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


def normalize_probability(raw_value: float) -> float:
    """
    NBM Core TSTM/lightning probability is already in percent.

    Do NOT multiply values <= 1 by 100. In this field, 1.0 means 1%,
    not 100%. Multiplying caused the false EXTREME lightning risk.
    """
    value = float(raw_value)

    if math.isnan(value):
        return 0.0

    return round(max(0.0, min(100.0, value)), 1)


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


def classify_lightning_probability(probability: float) -> dict[str, Any]:
    if probability <= 0:
        return {
            "probability": 0.0,
            "impact_level": 0,
            "risk": 0,
            "risk_label": "None",
            "metric": "Lightning chance: 0%",
            "ops_label": "No lightning signal",
        }

    for threshold in LIGHTNING_IMPACT_THRESHOLDS:
        if probability >= threshold["min_prob"]:
            impact_level = int(threshold["impact_level"])
            risk = matrix_risk(probability, impact_level)

            return {
                "probability": round(float(probability), 1),
                "impact_level": impact_level,
                "risk": risk,
                "risk_label": risk_label(risk),
                "metric": threshold["metric"],
                "ops_label": threshold["ops_label"],
            }

    return {
        "probability": round(float(probability), 1),
        "impact_level": 1,
        "risk": matrix_risk(probability, 1),
        "risk_label": risk_label(matrix_risk(probability, 1)),
        "metric": "Lightning chance: <5%",
        "ops_label": "No meaningful lightning operational impact",
    }


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback

    return json.loads(path.read_text())


def extract_lightning_hours(cycle: datetime) -> list[dict[str, Any]]:
    results = []

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
                {
                    "fxx": fxx,
                    "valid_utc": valid_utc,
                    "status": "error",
                    "message": f"Could not fetch/parse IDX: {exc}",
                    "probability": 0.0,
                    "raw_value": None,
                    "idx_line": None,
                    "risk_evaluation": classify_lightning_probability(0.0),
                }
            )
            continue

        row = find_lightning_row(idx_rows, fxx)

        if row is None:
            results.append(
                {
                    "fxx": fxx,
                    "valid_utc": valid_utc,
                    "status": "missing",
                    "message": "No Core thunder/lightning probability row found",
                    "probability": 0.0,
                    "raw_value": None,
                    "idx_line": None,
                    "risk_evaluation": classify_lightning_probability(0.0),
                    "candidate_lines": [
                        r["line"]
                        for r in idx_rows
                        if any(
                            term in r["line"].upper()
                            for term in [":TSTM:", ":LTNG:", ":LTG:", ":LTP:", ":TSTORM:", ":THUNDER:"]
                        )
                    ],
                }
            )
            continue

        try:
            var_name, raw_value = extract_core_value(
                grib_url=grib_url,
                row=row,
                label=f"core_lightning_f{fxx:03d}",
            )

            probability = normalize_probability(raw_value)
            evaluation = classify_lightning_probability(probability)

            results.append(
                {
                    "fxx": fxx,
                    "valid_utc": valid_utc,
                    "status": "ok",
                    "grib_url": grib_url,
                    "idx_url": idx_url,
                    "variable": var_name,
                    "raw_value": float(raw_value),
                    "probability": probability,
                    "idx_line": row["line"],
                    "risk_evaluation": evaluation,
                }
            )

        except Exception as exc:
            results.append(
                {
                    "fxx": fxx,
                    "valid_utc": valid_utc,
                    "status": "error",
                    "message": str(exc),
                    "probability": 0.0,
                    "raw_value": None,
                    "idx_line": row["line"],
                    "risk_evaluation": classify_lightning_probability(0.0),
                }
            )

    return results


def best_lightning_result(ok_hours: list[dict[str, Any]]) -> dict[str, Any]:
    if not ok_hours:
        return {
            **classify_lightning_probability(0.0),
            "fxx": 1,
            "valid_utc": None,
            "raw_value": None,
            "idx_line": None,
            "variable": None,
        }

    best_hour = max(
        ok_hours,
        key=lambda h: (
            h["risk_evaluation"]["risk"],
            h["risk_evaluation"]["probability"],
            h["risk_evaluation"]["impact_level"],
        ),
    )

    return {
        **best_hour["risk_evaluation"],
        "fxx": best_hour["fxx"],
        "valid_utc": best_hour["valid_utc"],
        "raw_value": best_hour.get("raw_value"),
        "idx_line": best_hour.get("idx_line"),
        "variable": best_hour.get("variable"),
    }


def block_lightning_risk(block_hours: list[dict[str, Any]]) -> dict[str, Any]:
    if not block_hours:
        return {
            "prob": 0.0,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": "Lightning chance: 0%",
            "ops_label": "No lightning signal",
            "driver": "0.0% chance of lightning",
        }

    best_hour = max(
        block_hours,
        key=lambda h: (
            h["risk_evaluation"]["risk"],
            h["risk_evaluation"]["probability"],
            h["risk_evaluation"]["impact_level"],
        ),
    )

    evaluation = best_hour["risk_evaluation"]

    return {
        "prob": round(float(evaluation["probability"]), 1),
        "risk": int(evaluation["risk"]),
        "risk_label": evaluation["risk_label"],
        "level": int(evaluation["impact_level"]),
        "metric": evaluation["metric"],
        "ops_label": evaluation["ops_label"],
        "source_fxx": best_hour["fxx"],
        "driver": f"{evaluation['probability']:.1f}% chance of lightning",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", default="", help="Optional NBM cycle in YYYYMMDDHH format")
    args = parser.parse_args()

    cycle = parse_cycle_arg(args.cycle) or latest_available_core_cycle_48hr()
    generated = utc_now()

    print(f"Building Core lightning outputs for cycle {cycle:%Y-%m-%d %HZ}")

    hours = extract_lightning_hours(cycle)
    ok_hours = [h for h in hours if h.get("status") == "ok"]

    if not ok_hours:
        print("Warning: No Core lightning hours extracted successfully. Writing zero lightning risk.")
        ok_hours = [
            {
                "fxx": 1,
                "valid_utc": (cycle + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                "status": "fallback_zero",
                "probability": 0.0,
                "risk_evaluation": classify_lightning_probability(0.0),
            }
        ]

    best = best_lightning_result(ok_hours)

    peak_fxx = int(best.get("fxx", 1))
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
    threats_payload["cycle"] = f"NBM Core {cycle.strftime('%HZ')}"
    threats_payload.setdefault("threats", {})

    lightning_payload = {
        "prob": round(float(best["probability"]), 1),
        "risk": int(best["risk"]),
        "risk_label": best["risk_label"],
        "level": int(best["impact_level"]),
        "metric": best["metric"],
        "display_label": "Lightning chance",
        "display_value": f"{best['probability']:.0f}%",
        "window": "1-3 hr",
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "ops_label": best["ops_label"],
        "driver": f"{best['probability']:.1f}% chance of lightning",
        "source_fxx": peak_fxx,
        "source_variable": best.get("variable"),
        "source_idx_line": best.get("idx_line"),
        "methodology": (
            "Lightning risk uses the NBM Core thunderstorm/lightning probability field directly. "
            "The extracted value is already percent, so 1.0 is treated as 1%, not 100%. "
            "The probability is classified into lightning thresholds: 0%, <5%, 5-25%, "
            "25-50%, 50-75%, and >75%. Probability-threshold lines such as 'prob >' are excluded."
        ),
    }

    threats_payload["threats"]["LIGHTNING"] = lightning_payload

    hazards = threats_payload.setdefault("hazards", [])
    found = False

    for hazard in hazards:
        if hazard.get("id") == "LIGHTNING":
            hazard.update(
                {
                    "id": "LIGHTNING",
                    "name": "Lightning",
                    "risk_level": int(best["risk"]),
                    "risk_label": best["risk_label"],
                    "impact_level": int(best["impact_level"]),
                    "probability": round(float(best["probability"]), 1),
                    "peak_start_fxx": peak_start_fxx,
                    "peak_end_fxx": peak_end_fxx,
                    "metric": best["metric"],
                    "display_label": "Lightning chance",
                    "display_value": f"{best['probability']:.0f}%",
                    "ops_label": best["ops_label"],
                    "driver": f"{best['probability']:.1f}% chance of lightning",
                }
            )
            found = True
            break

    if not found:
        hazards.append(
            {
                "id": "LIGHTNING",
                "name": "Lightning",
                "risk_level": int(best["risk"]),
                "risk_label": best["risk_label"],
                "impact_level": int(best["impact_level"]),
                "probability": round(float(best["probability"]), 1),
                "peak_start_fxx": peak_start_fxx,
                "peak_end_fxx": peak_end_fxx,
                "metric": best["metric"],
                "display_label": "Lightning chance",
                "display_value": f"{best['probability']:.0f}%",
                "ops_label": best["ops_label"],
                "driver": f"{best['probability']:.1f}% chance of lightning",
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

        block_hours = [h for h in ok_hours if start_fxx <= h["fxx"] <= end_fxx]
        block_eval = block_lightning_risk(block_hours)

        new_block = dict(old_block)
        new_block["start_fxx"] = start_fxx
        new_block["end_fxx"] = end_fxx
        new_block["LIGHTNING"] = block_eval["prob"]
        new_block["ltng_prob"] = block_eval["prob"]

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
        "thresholds": LIGHTNING_IMPACT_THRESHOLDS,
        "hours": hours,
        "methodology": (
            "Uses NBM Core thunderstorm/lightning probability directly. The value is already percent. "
            "A raw value of 1.0 is 1%, not 100%. The script excludes probability-threshold lines "
            "containing 'prob >', 'prob >=', 'prob <', 'prob <=', or percentile levels."
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
