from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import requests
import xarray as xr


DOCS = Path("docs")
DATA = Path("data")
AWS_BASE = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
DOMAIN = "co"
MPS_TO_MPH = 2.2369362921
M_TO_MI = 0.000621371192
M_TO_IN = 39.37007874
MM_TO_IN = 0.03937007874

SITE = {
    "site": "KRNO",
    "site_name": "Reno-Tahoe International Airport",
    "lat": 39.4991,
    "lon": -119.7681,
}

CORE_SOURCE_METHOD = "nbm_core_aws_gridpoint"
QMD_SOURCE_METHOD = "nbm_qmd_aws_gridpoint"

HAZARDS = ["WIND", "LIGHTNING", "SNOW", "VISIBILITY", "FZRA", "FLASH_FREEZE", "RAIN", "TEMPERATURE"]

RISK_LABELS = {
    0: "None",
    1: "Little to None",
    2: "Minor",
    3: "Moderate",
    4: "Major",
    5: "Extreme",
}

NATIVE_WINDOWS = {
    "WIND": [
        {"start_fxx": 1, "end_fxx": 48, "window_hours": 1, "source": "NBM hourly wind / QMD 24-hour max gust"},
        {"start_fxx": 49, "end_fxx": 72, "window_hours": 3, "source": "NBM 3-hour wind / QMD 24-hour max gust"},
    ],
    "RAIN": [
        {"start_fxx": 1, "end_fxx": 72, "window_hours": 1, "source": "NBM hourly deterministic rain"},
        {"start_fxx": 6, "end_fxx": 72, "window_hours": 6, "source": "NBM QMD 6-hour rain probabilities"},
    ],
    "FZRA": [
        {"start_fxx": 1, "end_fxx": 48, "window_hours": 1, "source": "NBM hourly deterministic freezing rain"},
        {"start_fxx": 6, "end_fxx": 72, "window_hours": 6, "source": "NBM QMD 6-hour freezing rain probabilities"},
    ],
    "SNOW": [
        {"start_fxx": 1, "end_fxx": 48, "window_hours": 1, "source": "NBM hourly snow amount/probability"},
        {"start_fxx": 54, "end_fxx": 72, "window_hours": 6, "source": "NBM 6-hour snow amount/probability"},
    ],
    "LIGHTNING": [
        {"start_fxx": 1, "end_fxx": 72, "window_hours": 3, "source": "NBM 3-hour thunder probability"},
    ],
    "VISIBILITY": [
        {"start_fxx": 1, "end_fxx": 36, "window_hours": 1, "source": "NBM hourly visibility probabilities"},
        {"start_fxx": 39, "end_fxx": 72, "window_hours": 3, "source": "NBM 3-hour visibility probabilities"},
    ],
    "FLASH_FREEZE": [
        {"start_fxx": 1, "end_fxx": 48, "window_hours": 1, "source": "NBM hourly temperature probabilities + wet-surface proxy"},
        {"start_fxx": 51, "end_fxx": 72, "window_hours": 3, "source": "NBM 3-hour temperature probabilities + wet-surface proxy"},
    ],
    "TEMPERATURE": [
        {"start_fxx": 1, "end_fxx": 48, "window_hours": 1, "source": "NBM hourly temperature probabilities / QMD max-min"},
        {"start_fxx": 51, "end_fxx": 72, "window_hours": 3, "source": "NBM 3-hour temperature probabilities / QMD max-min"},
    ],
}

METHODOLOGY = {
    "version": "nbm_dss_schema_v1",
    "horizon_hours": 72,
    "risk_matrix": "Risk follows the KRNO DSS method: determine hazard impact level from the approved threshold table, then apply the likelihood-vs-impact matrix when a probability is available. Deterministic-only fields use the impact category directly and are flagged with deterministic likelihood metadata.",
    "likelihood_bins": [
        {"id": "extremely_unlikely", "label": "Extremely Unlikely", "probability": "<10%"},
        {"id": "unlikely", "label": "Unlikely", "probability": "10-32%"},
        {"id": "about_as_likely_as_not", "label": "About as Likely as Not", "probability": "33-66%"},
        {"id": "likely", "label": "Likely", "probability": "67-90%"},
        {"id": "very_likely", "label": "Very Likely", "probability": ">90%"},
    ],
    "risk_labels": RISK_LABELS,
    "native_windows": NATIVE_WINDOWS,
    "snow": {
        "basis": "Core NBM snow amount/probability fields are mapped to the KRNO snow impact table, then probability thresholds are passed through the likelihood-vs-impact matrix.",
        "impact_thresholds": [
            {"level": 1, "label": "Little to None", "threshold": "0 inches"},
            {"level": 2, "label": "Minor", "threshold": "Trace to 0.5 inches per native window"},
            {"level": 3, "label": "Moderate", "threshold": "0.5 to 1 inch per native window"},
            {"level": 4, "label": "Major", "threshold": "1 to 2 inches per native window"},
            {"level": 5, "label": "Extreme", "threshold": "Greater than 2 inches per native window"},
        ],
    },
    "rain": {
        "basis": "Core NBM 1-hour precipitation drives the timeline. QMD precipitation probabilities are used where available for accumulation summary products. All rain values are mapped to the KRNO rain/flooding impact table.",
        "impact_thresholds": [
            {"level": 1, "label": "Little to None", "threshold": "Less than 0.10 inches per hour"},
            {"level": 2, "label": "Minor", "threshold": "0.10 to 0.25 inches per hour"},
            {"level": 3, "label": "Moderate", "threshold": "0.25 to 0.50 inches per hour"},
            {"level": 4, "label": "Major", "threshold": "0.50 to 1.00 inches per hour"},
            {"level": 5, "label": "Extreme", "threshold": "Greater than 1 inch per hour"},
        ],
    },
    "fzra": {
        "basis": "Core NBM freezing-rain/ice fields are mapped to the KRNO freezing rain impact table, then probability thresholds are passed through the likelihood-vs-impact matrix where available.",
        "impact_thresholds": [
            {"level": 1, "label": "Little to None", "threshold": "None"},
            {"level": 2, "label": "Minor", "threshold": "Trace"},
            {"level": 3, "label": "Moderate", "threshold": "Trace to 0.01 inches"},
            {"level": 4, "label": "Major", "threshold": "0.01 to 0.10 inches"},
            {"level": 5, "label": "Extreme", "threshold": "Greater than 0.10 inches"},
        ],
    },
    "precip_type_conflict": [
        "Evaluate freezing rain first when probability/amount exceeds threshold.",
        "Then evaluate snow when probability/amount and temperature support snow.",
        "Otherwise classify precipitation as rain.",
        "Allow mixed/transition wording when rain and snow signals overlap near the temperature threshold.",
    ],
    "future_admin_config": {
        "site": "Location, name, coordinates, branding",
        "sources": "NBM, REFS, QMD, observations, alerts",
        "hazards": "Enabled variables, row order, labels, thresholds, tooltips",
        "timeline": "Horizon, native windows, display windows, card aggregation rules",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_cycle_floor(dt: datetime | None = None) -> datetime:
    if dt is None:
        dt = datetime.now(timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return dt.replace(hour=(dt.hour // 6) * 6)


def candidate_cycles(dt: datetime | None = None, count: int = 8) -> list[datetime]:
    cycle = latest_cycle_floor(dt)
    return [cycle - timedelta(hours=6 * i) for i in range(count)]


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def aws_grib_url(cycle: datetime, product: str, fxx: int) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return f"{AWS_BASE}/blend.{ymd}/{hh}/{product}/blend.t{hh}z.{product}.f{fxx:03d}.{DOMAIN}.grib2"


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    return response.text


def parse_idx(idx_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lines = idx_text.splitlines()

    for i, line in enumerate(lines):
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue

        start_byte = int(parts[1])
        end_byte = None
        if i + 1 < len(lines):
            end_byte = int(lines[i + 1].split(":", 2)[1]) - 1

        rows.append({"msg_num": int(parts[0]), "start_byte": start_byte, "end_byte": end_byte, "line": line})

    return rows


def find_latest_available_cycle(product: str, required_fxx: list[int]) -> datetime:
    for cycle in candidate_cycles():
        ok = True
        for fxx in required_fxx:
            try:
                response = requests.head(aws_grib_url(cycle, product, fxx) + ".idx", timeout=15)
                if response.status_code != 200:
                    ok = False
                    break
            except requests.RequestException:
                ok = False
                break
        if ok:
            return cycle
    raise RuntimeError(f"No recent complete NBM {product} AWS cycle found for {required_fxx}")


def select_core_wind_rows(rows: list[dict[str, Any]], fxx: int) -> dict[str, dict[str, Any]]:
    expected_time = f":{fxx} hour fcst:"
    selected: dict[str, dict[str, Any]] = {}

    for row in rows:
        line = row["line"]
        if expected_time not in line or "ens std dev" in line:
            continue
        if ":GUST:10 m above ground:" in line:
            selected["gust"] = row
        elif ":WIND:10 m above ground:" in line:
            selected["wind"] = row
        elif ":WDIR:10 m above ground:" in line:
            selected["direction"] = row

    return selected


def one_hour_accum_window(fxx: int) -> str:
    return f":{max(0, fxx - 1)}-{fxx} hour acc fcst:"


def three_hour_accum_window(fxx: int) -> str | None:
    if fxx < 3 or fxx % 3 != 0:
        return None
    return f":{fxx - 3}-{fxx} hour acc fcst:"


def six_hour_accum_window(fxx: int) -> str:
    return f":{max(0, fxx - 6)}-{fxx} hour acc fcst:"


def twelve_hour_accum_window(fxx: int) -> str:
    return f":{max(0, fxx - 12)}-{fxx} hour acc fcst:"


def select_first_row(rows: list[dict[str, Any]], predicate) -> dict[str, Any] | None:
    return next((row for row in rows if predicate(row["line"])), None)


def is_deterministic(line: str) -> bool:
    return "prob" not in line and "level" not in line and "ens std dev" not in line and "@(" not in line


def download_one_message(grib_url: str, row: dict[str, Any], out_path: Path) -> None:
    end = row["end_byte"]
    headers = {"Range": f"bytes={row['start_byte']}-{end}"} if end is not None else {"Range": f"bytes={row['start_byte']}-"}
    response = requests.get(grib_url, headers=headers, timeout=120)
    response.raise_for_status()
    out_path.write_bytes(response.content)


def find_lat_lon_names(ds: xr.Dataset) -> tuple[str, str]:
    lat_name = next((name for name in ["latitude", "lat", "LAT"] if name in ds), None)
    lon_name = next((name for name in ["longitude", "lon", "LON"] if name in ds), None)
    if lat_name is None or lon_name is None:
        raise RuntimeError(f"Could not find latitude/longitude variables in {list(ds.variables)}")
    return lat_name, lon_name


def nearest_grid_indices(ds: xr.Dataset) -> tuple[int, int]:
    lat_name, lon_name = find_lat_lon_names(ds)
    lat = ds[lat_name].values
    lon = ds[lon_name].values
    target_lon = SITE["lon"] + 360 if np.nanmax(lon) > 180 and SITE["lon"] < 0 else SITE["lon"]
    distance = (lat - SITE["lat"]) ** 2 + (lon - target_lon) ** 2
    iy, ix = np.unravel_index(np.nanargmin(distance), distance.shape)
    return int(iy), int(ix)


def extract_gridpoint_value(grib_path: Path) -> float:
    ds = xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    try:
        iy, ix = nearest_grid_indices(ds)
        data_vars = list(ds.data_vars)
        if not data_vars:
            raise RuntimeError("No data variables found in GRIB message")
        values = ds[data_vars[0]].values.squeeze()
        if values.ndim != 2:
            raise RuntimeError(f"Expected 2D grid after squeeze, got {values.shape}")
        return float(values[iy, ix])
    finally:
        ds.close()


def extract_row_value(grib_url: str, row: dict[str, Any], tmp: Path, label: str) -> float:
    msg_path = tmp / f"{label}_{row['msg_num']}.grib2"
    download_one_message(grib_url, row, msg_path)
    return extract_gridpoint_value(msg_path)


def get_core_rows(cycle: datetime, fxx: int) -> list[dict[str, Any]]:
    return parse_idx(fetch_text(aws_grib_url(cycle, "core", fxx) + ".idx"))


def value_risk(value: float, thresholds: list[tuple[float, int]], reverse: bool = False) -> int:
    for threshold, level in thresholds:
        if reverse:
            if value <= threshold:
                return level
        elif value >= threshold:
            return level
    return 0


RISK_MATRIX_BY_LIKELIHOOD = {
    "very_likely": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
    "likely": {1: 1, 2: 2, 3: 3, 4: 4, 5: 4},
    "about_as_likely_as_not": {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
    "unlikely": {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
    "extremely_unlikely": {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
}


def likelihood_bin(probability: float | None) -> str | None:
    if probability is None:
        return None
    prob = float(probability)
    if prob > 90:
        return "very_likely"
    if prob > 66:
        return "likely"
    if prob >= 33:
        return "about_as_likely_as_not"
    if prob >= 10:
        return "unlikely"
    return "extremely_unlikely"


def risk_from_matrix(impact_level: int, probability: float | None = None) -> int:
    impact = max(1, min(5, int(impact_level or 1)))
    likelihood = likelihood_bin(probability)
    if likelihood is None:
        return impact
    return RISK_MATRIX_BY_LIKELIHOOD[likelihood][impact]


def prob_risk(prob: float, thresholds: list[tuple[float, int]]) -> int:
    return value_risk(prob, thresholds)


def threshold_probability_risk(values: dict[str, float], thresholds: list[tuple[str, int]], trigger_prob: float = 10.0) -> int:
    for key, level in thresholds:
        prob = float(values.get(key, 0.0) or 0.0)
        if prob >= trigger_prob:
            return level
    return 0


def matrix_probability_risk(values: dict[str, float], thresholds: list[tuple[str, int]]) -> int:
    risk = 1
    for key, impact_level in thresholds:
        prob = float(values.get(key, 0.0) or 0.0)
        if prob <= 0:
            continue
        risk = max(risk, risk_from_matrix(impact_level, prob))
    return risk


def rain_impact_level(rain_in_per_hour: float) -> int:
    if rain_in_per_hour > 1.0:
        return 5
    if rain_in_per_hour >= 0.50:
        return 4
    if rain_in_per_hour >= 0.25:
        return 3
    if rain_in_per_hour >= 0.10:
        return 2
    return 1


def snow_impact_level(snow_in_per_hour: float) -> int:
    if snow_in_per_hour > 2.0:
        return 5
    if snow_in_per_hour >= 1.0:
        return 4
    if snow_in_per_hour >= 0.5:
        return 3
    if snow_in_per_hour > 0.0:
        return 2
    return 1


def fzra_impact_level(fzra_in: float) -> int:
    if fzra_in > 0.10:
        return 5
    if fzra_in >= 0.01:
        return 4
    if fzra_in > 0.0:
        return 3 if fzra_in >= 0.005 else 2
    return 1


def lightning_impact_level(probability: float) -> int:
    if probability > 75:
        return 5
    if probability >= 50:
        return 4
    if probability >= 25:
        return 3
    if probability >= 5:
        return 2
    return 1


def wind_impact_level(gust_mph: float) -> int:
    if gust_mph > 65:
        return 5
    if gust_mph >= 58:
        return 4
    if gust_mph >= 45:
        return 3
    if gust_mph >= 30:
        return 2
    return 1


def visibility_impact_level(visibility_mi: float) -> int:
    if visibility_mi < 0.50:
        return 5
    if visibility_mi <= 1.0:
        return 4
    if visibility_mi <= 3.0:
        return 3
    if visibility_mi <= 5.0:
        return 2
    return 1


def temperature_impact_level(temp_f: float) -> int:
    cold = 1
    if temp_f < 10:
        cold = 5
    elif temp_f < 20:
        cold = 4
    elif temp_f < 32:
        cold = 3
    elif temp_f < 40:
        cold = 2

    heat = 1
    if temp_f > 105:
        heat = 5
    elif temp_f >= 100:
        heat = 4
    elif temp_f >= 95:
        heat = 3
    elif temp_f >= 90:
        heat = 2

    return max(cold, heat)


def flash_freeze_impact_level(temp_f: float, wet_surface: bool) -> int:
    if not wet_surface:
        return 1
    if temp_f <= 25:
        return 5
    if temp_f <= 28:
        return 4
    if temp_f <= 32:
        return 3
    if temp_f <= 36:
        return 2
    return 1


def k_to_f(value_k: float) -> float:
    return (value_k - 273.15) * 9 / 5 + 32


def set_hazard_live(
    timeline: dict[str, Any],
    bi: int,
    hazard_id: str,
    risk: int,
    metric: str,
    values: dict[str, Any],
    hourly_values: list[dict[str, Any]],
    source: str,
    driver: str,
) -> None:
    block = timeline["blocks"][bi]
    hdata = timeline["block_hazards"][bi][hazard_id]
    timeline["blocks"][bi][hazard_id] = risk
    hdata.update(
        {
            "label": hazard_id.replace("_", " ").title(),
            "name": hazard_id.replace("_", " ").title(),
            "risk": risk,
            "risk_label": RISK_LABELS[risk],
            "level": risk,
            "impact_level": values.get("impact_level", risk),
            "likelihood_category": values.get("likelihood_category"),
            "risk_method": values.get("risk_method", "krno_impact_threshold"),
            "prob": values.get("probability"),
            "probability": values.get("probability"),
            "metric": metric,
            "driver": driver,
            "source_fxx": values.get("fxx", block.get("end_fxx")),
            "peak_valid_utc": values.get("valid_utc", block.get("valid_end_utc")),
            "data_status": "live",
            "method": CORE_SOURCE_METHOD,
            "source": source,
            "hourly_values": hourly_values,
            "values": values,
        }
    )


def update_threat_from_blocks(
    threats_payload: dict[str, Any],
    timeline: dict[str, Any],
    hazard_id: str,
    display_label: str,
    display_value: str,
    metric: str,
    driver: str,
    source: str,
    magnitude_key: str | None = None,
    lower_is_worse: bool = False,
) -> None:
    best = None
    for bi, hazards in enumerate(timeline["block_hazards"]):
        hdata = hazards.get(hazard_id)
        if not hdata or hdata.get("data_status") != "live":
            continue
        risk = int(hdata.get("risk") or 0)
        values = hdata.get("values") or {}
        mag = values.get(magnitude_key) if magnitude_key else None
        mag_num = float(mag) if mag is not None and np.isfinite(float(mag)) else 0.0
        score_mag = -mag_num if lower_is_worse else mag_num
        if (
            best is None
            or risk > best["risk"]
            or (risk == best["risk"] and score_mag > best["score_mag"])
        ):
            best = {"risk": risk, "score_mag": score_mag, "hdata": hdata, "bi": bi}

    risk = int(best["risk"]) if best else 0
    hdata = best["hdata"] if best else {}
    block = timeline["blocks"][best["bi"]] if best else {}
    threat = threats_payload["threats"][hazard_id]
    threat.update(
        {
            "title": hazard_id.replace("_", " ").title(),
            "name": hazard_id.replace("_", " ").title(),
            "prob": hdata.get("prob"),
            "probability": hdata.get("probability"),
            "risk": risk,
            "risk_level": risk,
            "risk_label": RISK_LABELS[risk],
            "level": risk,
            "impact_level": hdata.get("impact_level", risk),
            "likelihood_category": hdata.get("likelihood_category"),
            "risk_method": hdata.get("risk_method", "krno_impact_threshold"),
            "metric": metric,
            "display_label": display_label,
            "display_value": display_value or hdata.get("metric") or metric,
            "window": "72 hr",
            "peak_start_fxx": block.get("start_fxx"),
            "peak_end_fxx": block.get("end_fxx"),
            "source_fxx": hdata.get("source_fxx"),
            "peak_valid_utc": hdata.get("peak_valid_utc"),
            "driver": driver,
            "methodology": "NBM AWS core gridpoint values summarized over the 72-hour DSS window.",
            "data_status": "live",
            "method": CORE_SOURCE_METHOD,
            "source": source,
        }
    )
    for hazard in threats_payload["hazards"]:
        if hazard["id"] == hazard_id:
            hazard.update({"risk": risk, "probability": hdata.get("probability"), "level": risk})


def extract_core_wind_hour(cycle: datetime, fxx: int, tmp: Path) -> dict[str, Any] | None:
    grib_url = aws_grib_url(cycle, "core", fxx)
    idx_text = fetch_text(grib_url + ".idx")
    selected = select_core_wind_rows(parse_idx(idx_text), fxx)
    if "gust" not in selected:
        return None

    values: dict[str, float | None] = {"gust_mps": None, "wind_mps": None, "direction_deg": None}
    # First AWS pass keeps runtime reasonable by decoding the primary timeline
    # driver only. Sustained wind/direction can be layered in once caching lands.
    for key, row in {"gust": selected["gust"]}.items():
        msg_path = tmp / f"nbm_core_{cycle:%Y%m%d%H}_f{fxx:03d}_{key}.grib2"
        download_one_message(grib_url, row, msg_path)
        value = extract_gridpoint_value(msg_path)
        if key == "gust":
            values["gust_mps"] = value
        elif key == "wind":
            values["wind_mps"] = value
        elif key == "direction":
            values["direction_deg"] = value

    gust_mph = (values["gust_mps"] or 0.0) * MPS_TO_MPH
    wind_mph = (values["wind_mps"] or 0.0) * MPS_TO_MPH
    return {
        "fxx": fxx,
        "valid_utc": iso(cycle + timedelta(hours=fxx)),
        "gust_mph": round(gust_mph, 1),
        "wind_mph": round(wind_mph, 1) if values["wind_mps"] is not None else None,
        "direction_deg": int(round(values["direction_deg"] or 0)) if values["direction_deg"] is not None else None,
    }


def wind_risk_from_gust(gust_mph: float) -> int:
    return risk_from_matrix(wind_impact_level(gust_mph))


def block_label_for_wind(hourly_values: list[dict[str, Any]]) -> str:
    if not hourly_values:
        return "No forecast value"
    peak = max(hourly_values, key=lambda row: row.get("gust_mph") or 0)
    gust = peak.get("gust_mph")
    wind = peak.get("wind_mph")
    direction = peak.get("direction_deg")
    parts = []
    if gust is not None:
        parts.append(f"Gust {gust:.0f} mph")
    if wind is not None:
        parts.append(f"Wind {wind:.0f} mph")
    if direction is not None:
        parts.append(f"{direction:03d} deg")
    return " / ".join(parts)


def apply_core_wind(timeline: dict[str, Any], threats_payload: dict[str, Any]) -> None:
    cycle = find_latest_available_cycle("core", [1, 72])
    cycle_iso = iso(cycle)
    fxx_values = sorted({*range(1, 49), *range(51, 73, 3)})

    hourly_by_fxx: dict[int, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for fxx in fxx_values:
            row = extract_core_wind_hour(cycle, fxx, tmp)
            if row:
                hourly_by_fxx[fxx] = row

    if not hourly_by_fxx:
        raise RuntimeError("NBM core wind extraction returned no gridpoint values")

    best_block: dict[str, Any] | None = None
    best_hazard: dict[str, Any] | None = None
    best_gust = -1.0
    max_gust = 0.0
    max_risk = 0

    for block, hazards in zip(timeline["blocks"], timeline["block_hazards"]):
        start_fxx = int(block["start_fxx"])
        end_fxx = int(block["end_fxx"])
        block_hours = [row for fxx, row in hourly_by_fxx.items() if start_fxx <= fxx <= end_fxx]
        if not block_hours:
            nearest = min(hourly_by_fxx, key=lambda fxx: abs(fxx - end_fxx))
            if abs(nearest - end_fxx) <= 2:
                block_hours = [hourly_by_fxx[nearest]]

        if not block_hours:
            continue

        peak = max(block_hours, key=lambda row: row.get("gust_mph") or 0)
        gust = float(peak.get("gust_mph") or 0.0)
        impact = wind_impact_level(gust)
        risk = risk_from_matrix(impact)

        max_gust = max(max_gust, gust)
        max_risk = max(max_risk, risk)
        block["WIND"] = risk

        hazard = hazards["WIND"]
        hazard.update(
            {
                "label": "Wind",
                "name": "Wind",
                "risk": risk,
                "risk_label": RISK_LABELS[risk],
                "level": risk,
                "impact_level": impact,
                "likelihood_category": None,
                "risk_method": "krno_threshold_deterministic_core",
                "prob": None,
                "probability": None,
                "metric": block_label_for_wind(block_hours),
                "driver": "NBM core deterministic wind/gust at KRNO",
                "source_fxx": int(peak["fxx"]),
                "peak_valid_utc": peak["valid_utc"],
                "data_status": "live",
                "method": "nbm_core_aws_gridpoint",
                "source": f"NOAA NBM core AWS {cycle:%HZ}",
                "gust_max_mph": round(gust, 1),
                "hourly_values": [
                    {
                        **hour,
                        "gust_max_mph": hour.get("gust_mph"),
                        "value": hour.get("gust_mph"),
                        "unit": "mph",
                        "label": "gust",
                        "window_hours": 1 if int(hour.get("fxx") or 0) <= 48 else 3,
                    }
                    for hour in block_hours
                ],
                "values": {
                    "impact_level": impact,
                    "risk_method": "krno_threshold_deterministic_core",
                    "peak_gust_mph": round(gust, 1),
                    "gust_max_mph": round(gust, 1),
                    "wind_mph": peak.get("wind_mph"),
                    "direction_deg": peak.get("direction_deg"),
                    "value": round(gust, 1),
                    "unit": "mph",
                },
            }
        )
        if best_hazard is None or risk > best_hazard["risk"] or (risk == best_hazard["risk"] and gust > best_gust):
            best_block = block
            best_hazard = hazard
            best_gust = gust

    threat = threats_payload["threats"]["WIND"]
    threat.update(
        {
            "title": "Wind",
            "name": "Wind",
            "prob": None,
            "probability": None,
            "risk": max_risk,
            "risk_level": max_risk,
            "risk_label": RISK_LABELS[max_risk],
            "level": max_risk,
            "impact_level": best_hazard.get("impact_level") if best_hazard else wind_impact_level(max_gust),
            "likelihood_category": None,
            "risk_method": "krno_threshold_deterministic_core",
            "metric": f"Peak gust {max_gust:.0f} mph",
            "display_label": "72-hr peak gust",
            "display_value": f"{max_gust:.0f} mph",
            "window": "72 hr",
            "peak_start_fxx": best_block.get("start_fxx") if best_block else None,
            "peak_end_fxx": best_block.get("end_fxx") if best_block else None,
            "source_fxx": best_hazard.get("source_fxx") if best_hazard else None,
            "peak_valid_utc": best_hazard.get("peak_valid_utc") if best_hazard else None,
            "driver": "NBM core deterministic wind/gust at KRNO",
            "methodology": "Core NBM wind/gust is mapped to the KRNO wind impact table for the timeline; QMD 24-hour max gust is used for the summary card when available.",
            "data_status": "live",
            "method": "nbm_core_aws_gridpoint",
            "source": f"NOAA NBM core AWS {cycle:%HZ}",
            "source_cycle_utc_iso": cycle_iso,
        }
    )

    for hazard in threats_payload["hazards"]:
        if hazard["id"] == "WIND":
            hazard.update({"risk": max_risk, "probability": None, "level": max_risk})

    timeline["source"] = f"NOAA NBM core AWS {cycle:%HZ}"
    timeline["cycle_utc_iso"] = cycle_iso
    timeline["cycle"] = f"NBM {cycle:%HZ}"
    threats_payload["source"] = timeline["source"]
    threats_payload["cycle_utc_iso"] = cycle_iso
    threats_payload["cycle"] = timeline["cycle"]


def extract_core_block_values(cycle: datetime, fxx: int, tmp: Path) -> dict[str, Any]:
    grib_url = aws_grib_url(cycle, "core", fxx)
    rows = get_core_rows(cycle, fxx)
    one_hr = one_hour_accum_window(fxx)
    three_hr = three_hour_accum_window(fxx)
    six_hr = six_hour_accum_window(fxx)

    selectors = {
        "rain": select_first_row(rows, lambda line: ":APCP:surface:" in line and one_hr in line and is_deterministic(line)),
        "rain6": select_first_row(rows, lambda line: ":APCP:surface:" in line and six_hr in line and is_deterministic(line)),
        "rain_prob_trace": select_first_row(rows, lambda line: ":APCP:surface:" in line and one_hr in line and "prob >0.254" in line),
        "snow": select_first_row(rows, lambda line: ":ASNOW:surface:" in line and (one_hr in line or six_hr in line) and is_deterministic(line)),
        "snow_prob_trace": select_first_row(rows, lambda line: ":ASNOW:surface:" in line and (one_hr in line or six_hr in line) and "prob >0.00254" in line),
        "snow_prob_0p5": select_first_row(rows, lambda line: ":ASNOW:surface:" in line and (one_hr in line or six_hr in line) and "prob >0.0127" in line),
        "snow_prob_1": select_first_row(rows, lambda line: ":ASNOW:surface:" in line and (one_hr in line or six_hr in line) and "prob >0.0254" in line),
        "snow_prob_2": select_first_row(rows, lambda line: ":ASNOW:surface:" in line and (one_hr in line or six_hr in line) and "prob >0.0508" in line),
        "fzra": select_first_row(rows, lambda line: ":FICEAC:surface:" in line and (one_hr in line or six_hr in line) and is_deterministic(line)),
        "fzra_prob_trace": select_first_row(rows, lambda line: ":FICEAC:surface:" in line and (one_hr in line or six_hr in line) and "prob >0.254" in line),
        "fzra_prob_0p1": select_first_row(rows, lambda line: ":FICEAC:surface:" in line and (one_hr in line or six_hr in line) and "prob >2.54" in line),
        "fzra_prob_0p2": select_first_row(rows, lambda line: ":FICEAC:surface:" in line and (one_hr in line or six_hr in line) and ("prob >5.08" in line or "prob >6.35" in line)),
        "tstm": select_first_row(rows, lambda line: bool(three_hr) and ":TSTM:surface:" in line and three_hr in line and "probability forecast" in line),
        "temp": select_first_row(rows, lambda line: ":TMP:2 m above ground:" in line and f":{fxx} hour fcst:" in line and is_deterministic(line)),
        "vis": select_first_row(rows, lambda line: ":VIS:surface:" in line and f":{fxx} hour fcst:" in line and is_deterministic(line)),
        "vis_lt1": select_first_row(rows, lambda line: ":VIS:surface:" in line and f":{fxx} hour fcst:" in line and "prob <1609.34" in line),
        "vis_lt3": select_first_row(rows, lambda line: ":VIS:surface:" in line and f":{fxx} hour fcst:" in line and "prob <4828.03" in line),
        "vis_lt5": select_first_row(rows, lambda line: ":VIS:surface:" in line and f":{fxx} hour fcst:" in line and "prob <8046.73" in line),
    }

    out: dict[str, Any] = {"fxx": fxx, "valid_utc": iso(cycle + timedelta(hours=fxx))}
    for key, row in selectors.items():
        if row is None:
            continue
        try:
            out[key] = extract_row_value(grib_url, row, tmp, f"core_{fxx:03d}_{key}")
        except Exception as exc:
            out.setdefault("errors", {})[key] = str(exc)

    return out


def apply_core_remaining_hazards(timeline: dict[str, Any], threats_payload: dict[str, Any]) -> None:
    cycle = find_latest_available_cycle("core", [1, 72])
    source = f"NOAA NBM core AWS {cycle:%HZ}"
    fxx_values = sorted({*range(1, 49), *range(51, 73, 3)})

    decoded: dict[int, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for fxx in fxx_values:
            try:
                decoded[fxx] = extract_core_block_values(cycle, fxx, tmp)
            except Exception as exc:
                decoded[fxx] = {"fxx": fxx, "valid_utc": iso(cycle + timedelta(hours=fxx)), "errors": {"core": str(exc)}}

    totals = {"RAIN": 0.0, "SNOW": 0.0, "FZRA": 0.0}
    temp_values: list[dict[str, Any]] = []

    for bi, block in enumerate(timeline["blocks"]):
        start_fxx = int(block["start_fxx"])
        end_fxx = int(block["end_fxx"])
        block_rows = [decoded[fxx] for fxx in sorted(decoded) if start_fxx <= fxx <= end_fxx and decoded.get(fxx)]
        row = decoded.get(end_fxx) or (block_rows[-1] if block_rows else decoded.get(min(decoded, key=lambda fxx: abs(fxx - end_fxx))))
        if not row:
            continue
        if not block_rows:
            block_rows = [row]

        valid_utc = row["valid_utc"]
        fxx = int(row["fxx"])

        def row_rain_in(src: dict[str, Any]) -> float:
            return float(src.get("rain", 0.0) or 0.0) * MM_TO_IN

        def row_snow_in(src: dict[str, Any]) -> float:
            return float(src.get("snow", 0.0) or 0.0) * M_TO_IN

        def row_fzra_in(src: dict[str, Any]) -> float:
            return float(src.get("fzra", 0.0) or 0.0) * MM_TO_IN

        def row_temp_f(src: dict[str, Any]) -> float:
            return k_to_f(float(src.get("temp", 273.15) or 273.15))

        rain_in = row_rain_in(row)
        rain6_in = float(row.get("rain6", 0.0) or 0.0) * MM_TO_IN
        rain_prob_trace = float(row.get("rain_prob_trace", 0.0) or 0.0)
        rain_impact = rain_impact_level(rain_in)
        rain_risk = max(
            risk_from_matrix(rain_impact),
            risk_from_matrix(1, rain_prob_trace),
        )
        rain_hourly_values = [
            {
                "fxx": int(src["fxx"]),
                "valid_utc": src["valid_utc"],
                "rain_in": round(row_rain_in(src), 3),
                "rain_rate_in_hr": round(row_rain_in(src), 3),
                "impact_level": rain_impact_level(row_rain_in(src)),
                "prob_gt_trace": round(float(src.get("rain_prob_trace", 0.0) or 0.0), 1),
                "value": round(row_rain_in(src), 3),
                "unit": "in",
                "label": "rain",
                "window_hours": 1,
            }
            for src in block_rows
        ]
        totals["RAIN"] += sum(item["rain_in"] for item in rain_hourly_values)
        set_hazard_live(
            timeline,
            bi,
            "RAIN",
            rain_risk,
            f"Rain {rain_in:.2f} in",
            {"fxx": fxx, "valid_utc": valid_utc, "impact_level": rain_impact, "risk_method": "krno_threshold_core_with_measurable_probability", "likelihood_category": likelihood_bin(rain_prob_trace), "rain_in": round(rain_in, 3), "rain_rate_in_hr": round(rain_in, 3), "rain_6hr_in": round(rain6_in, 3), "prob_gt_trace": round(rain_prob_trace, 1), "probability": round(rain_prob_trace, 1), "value": round(rain_in, 3), "unit": "in"},
            rain_hourly_values,
            source,
            "NBM core precipitation amount and probability of measurable rain at KRNO",
        )

        snow_in = row_snow_in(row)
        snow_probs = {
            "trace": float(row.get("snow_prob_trace", 0.0) or 0.0),
            "0p5": float(row.get("snow_prob_0p5", 0.0) or 0.0),
            "1": float(row.get("snow_prob_1", 0.0) or 0.0),
            "2": float(row.get("snow_prob_2", 0.0) or 0.0),
        }
        snow_window_hours = 1 if fxx <= 48 else 6
        snow_rate_in_hr = snow_in / snow_window_hours if snow_window_hours else snow_in
        snow_impact = snow_impact_level(snow_rate_in_hr)
        snow_risk = max(
            matrix_probability_risk(snow_probs, [("2", 5), ("1", 4), ("0p5", 3), ("trace", 2)]),
            risk_from_matrix(snow_impact),
        )
        snow_hourly_values = [
            {
                "fxx": int(src["fxx"]),
                "valid_utc": src["valid_utc"],
                "snow_in": round(row_snow_in(src), 3),
                "snow_rate_in_hr": round(row_snow_in(src) / (1 if int(src["fxx"]) <= 48 else 6), 3),
                "impact_level": snow_impact_level(row_snow_in(src) / (1 if int(src["fxx"]) <= 48 else 6)),
                "prob_gt_trace": round(float(src.get("snow_prob_trace", 0.0) or 0.0), 1),
                "prob_gt_0p5_in": round(float(src.get("snow_prob_0p5", 0.0) or 0.0), 1),
                "prob_gt_1_in": round(float(src.get("snow_prob_1", 0.0) or 0.0), 1),
                "prob_gt_2_in": round(float(src.get("snow_prob_2", 0.0) or 0.0), 1),
                "value": round(row_snow_in(src), 3),
                "unit": "in",
                "label": "snow",
                "window_hours": 1 if int(src["fxx"]) <= 48 else 6,
            }
            for src in block_rows
        ]
        totals["SNOW"] += sum(item["snow_in"] for item in snow_hourly_values)
        set_hazard_live(
            timeline,
            bi,
            "SNOW",
            snow_risk,
            "Snow Trace" if 0 < snow_in < 0.01 else f"Snow {snow_in:.2f} in",
            {"fxx": fxx, "valid_utc": valid_utc, "impact_level": snow_impact, "risk_method": "krno_threshold_probability_matrix_core", "likelihood_category": likelihood_bin(max(snow_probs.values())), "snow_in": round(snow_in, 3), "snow_rate_in_hr": round(snow_rate_in_hr, 3), "prob_gt_trace": round(snow_probs["trace"], 1), "prob_gt_0p5_in": round(snow_probs["0p5"], 1), "prob_gt_1_in": round(snow_probs["1"], 1), "prob_gt_2_in": round(snow_probs["2"], 1), "probability": round(max(snow_probs.values()), 1), "value": round(snow_in, 3), "unit": "in"},
            snow_hourly_values,
            source,
            "NBM core snowfall amount and probability-of-threshold exceedance at KRNO",
        )

        fzra_in = row_fzra_in(row)
        fzra_probs = {
            "trace": float(row.get("fzra_prob_trace", 0.0) or 0.0),
            "0p1": float(row.get("fzra_prob_0p1", 0.0) or 0.0),
            "0p2": float(row.get("fzra_prob_0p2", 0.0) or 0.0),
        }
        fzra_impact = fzra_impact_level(fzra_in)
        fzra_risk = max(
            matrix_probability_risk(fzra_probs, [("0p2", 5), ("0p1", 5), ("trace", 4)]),
            risk_from_matrix(fzra_impact),
        )
        fzra_hourly_values = [
            {
                "fxx": int(src["fxx"]),
                "valid_utc": src["valid_utc"],
                "fzra_in": round(row_fzra_in(src), 3),
                "impact_level": fzra_impact_level(row_fzra_in(src)),
                "prob_gt_trace": round(float(src.get("fzra_prob_trace", 0.0) or 0.0), 1),
                "prob_gt_0p1_in": round(float(src.get("fzra_prob_0p1", 0.0) or 0.0), 1),
                "prob_gt_0p2_in": round(float(src.get("fzra_prob_0p2", 0.0) or 0.0), 1),
                "value": round(row_fzra_in(src), 3),
                "unit": "in",
                "label": "fzra",
                "window_hours": 1 if int(src["fxx"]) <= 48 else 6,
            }
            for src in block_rows
        ]
        totals["FZRA"] += sum(item["fzra_in"] for item in fzra_hourly_values)
        set_hazard_live(
            timeline,
            bi,
            "FZRA",
            fzra_risk,
            "Freezing rain Trace" if 0 < fzra_in < 0.01 else f"Freezing rain {fzra_in:.2f} in",
            {"fxx": fxx, "valid_utc": valid_utc, "impact_level": fzra_impact, "risk_method": "krno_threshold_probability_matrix_core", "likelihood_category": likelihood_bin(max(fzra_probs.values())), "fzra_in": round(fzra_in, 3), "prob_gt_trace": round(fzra_probs["trace"], 1), "prob_gt_0p01_in": round(fzra_probs["trace"], 1), "prob_gt_0p1_in": round(fzra_probs["0p1"], 1), "prob_gt_0p2_in": round(fzra_probs["0p2"], 1), "probability": round(max(fzra_probs.values()), 1), "value": round(fzra_in, 3), "unit": "in"},
            fzra_hourly_values,
            source,
            "NBM core freezing rain/ice accretion amount and probability-of-threshold exceedance at KRNO",
        )

        tstm_prob = float(row.get("tstm", 0.0) or 0.0)
        ltg_impact = lightning_impact_level(tstm_prob)
        ltg_risk = risk_from_matrix(ltg_impact)
        set_hazard_live(
            timeline,
            bi,
            "LIGHTNING",
            ltg_risk,
            f"Thunder {tstm_prob:.0f}%",
            {"fxx": fxx, "valid_utc": valid_utc, "impact_level": ltg_impact, "risk_method": "krno_lightning_probability_threshold_core", "likelihood_category": likelihood_bin(tstm_prob), "probability": round(tstm_prob, 1), "prob": round(tstm_prob, 1), "value": round(tstm_prob, 1), "unit": "%"},
            [
                {
                    "fxx": int(src["fxx"]),
                    "valid_utc": src["valid_utc"],
                    "prob": round(float(src.get("tstm", 0.0) or 0.0), 1),
                    "value": round(float(src.get("tstm", 0.0) or 0.0), 1),
                    "unit": "%",
                    "label": "prob",
                    "window_hours": 3,
                }
                for src in block_rows
                if "tstm" in src
            ],
            source,
            "NBM core 3-hour thunder probability at KRNO",
        )

        vis_mi = float(row.get("vis", 16093.4) or 16093.4) * M_TO_MI
        vis_lt1 = float(row.get("vis_lt1", 0.0) or 0.0)
        vis_lt3 = float(row.get("vis_lt3", 0.0) or 0.0)
        vis_lt5 = float(row.get("vis_lt5", 0.0) or 0.0)
        vis_impact = visibility_impact_level(vis_mi)
        vis_risk = max(
            risk_from_matrix(vis_impact),
            matrix_probability_risk({"lt1": vis_lt1, "lt3": vis_lt3, "lt5": vis_lt5}, [("lt1", 4), ("lt3", 3), ("lt5", 2)]),
        )
        set_hazard_live(
            timeline,
            bi,
            "VISIBILITY",
            vis_risk,
            f"Visibility {min(vis_mi, 10.0):.1f} mi",
            {
                "fxx": fxx,
                "valid_utc": valid_utc,
                "impact_level": vis_impact,
                "risk_method": "krno_threshold_probability_matrix_core",
                "likelihood_category": likelihood_bin(max(vis_lt1, vis_lt3, vis_lt5)),
                "visibility_mi": round(min(vis_mi, 10.0), 2),
                "prob_lt_1mi": round(vis_lt1, 1),
                "prob_lt_3mi": round(vis_lt3, 1),
                "prob_lt_5mi": round(vis_lt5, 1),
                "probability": round(max(vis_lt1, vis_lt3, vis_lt5), 1),
                "value": round(min(vis_mi, 10.0), 2),
                "unit": "mi",
            },
            [
                {
                    "fxx": int(src["fxx"]),
                    "valid_utc": src["valid_utc"],
                    "visibility_mi": round(min(float(src.get("vis", 16093.4) or 16093.4) * M_TO_MI, 10.0), 2),
                    "impact_level": visibility_impact_level(float(src.get("vis", 16093.4) or 16093.4) * M_TO_MI),
                    "prob_lt_1mi": round(float(src.get("vis_lt1", 0.0) or 0.0), 1),
                    "prob_lt_3mi": round(float(src.get("vis_lt3", 0.0) or 0.0), 1),
                    "prob_lt_5mi": round(float(src.get("vis_lt5", 0.0) or 0.0), 1),
                    "value": round(min(float(src.get("vis", 16093.4) or 16093.4) * M_TO_MI, 10.0), 2),
                    "unit": "mi",
                    "label": "visibility",
                    "window_hours": 1 if int(src["fxx"]) <= 36 else 3,
                }
                for src in block_rows
            ],
            source,
            "NBM core visibility and low-visibility probabilities at KRNO",
        )

        temp_f = row_temp_f(row)
        temp_values.append({"fxx": fxx, "valid_utc": valid_utc, "temp_f": temp_f, "impact_level": temperature_impact_level(temp_f)})
        temp_impact = temperature_impact_level(temp_f)
        temp_risk = risk_from_matrix(temp_impact)
        set_hazard_live(
            timeline,
            bi,
            "TEMPERATURE",
            temp_risk,
            f"Temp {temp_f:.0f}°F",
            {"fxx": fxx, "valid_utc": valid_utc, "impact_level": temp_impact, "risk_method": "krno_threshold_deterministic_core", "temp_f": round(temp_f, 1), "value": round(temp_f, 1), "unit": "°F"},
            [
                {
                    "fxx": int(src["fxx"]),
                    "valid_utc": src["valid_utc"],
                    "temp_f": round(row_temp_f(src), 1),
                    "impact_level": temperature_impact_level(row_temp_f(src)),
                    "value": round(row_temp_f(src), 1),
                    "unit": "°F",
                    "label": "temp",
                    "window_hours": 1 if int(src["fxx"]) <= 48 else 3,
                }
                for src in block_rows
            ],
            source,
            "NBM core 2-meter temperature at KRNO",
        )

        wet_surface = rain_in >= 0.01 or fzra_in >= 0.001
        flash_impact = flash_freeze_impact_level(temp_f, wet_surface)
        flash_risk = risk_from_matrix(flash_impact)
        set_hazard_live(
            timeline,
            bi,
            "FLASH_FREEZE",
            flash_risk,
            f"Temp {temp_f:.0f}°F",
            {"fxx": fxx, "valid_utc": valid_utc, "impact_level": flash_impact, "risk_method": "krno_threshold_deterministic_core_wet_surface_proxy", "temp_f": round(temp_f, 1), "wet_surface": wet_surface, "value": round(temp_f, 1), "unit": "°F"},
            [
                {
                    "fxx": int(src["fxx"]),
                    "valid_utc": src["valid_utc"],
                    "temp_f": round(row_temp_f(src), 1),
                    "rain_in": round(row_rain_in(src), 3),
                    "fzra_in": round(row_fzra_in(src), 3),
                    "wet_surface": row_rain_in(src) >= 0.01 or row_fzra_in(src) >= 0.001,
                    "impact_level": flash_freeze_impact_level(row_temp_f(src), row_rain_in(src) >= 0.01 or row_fzra_in(src) >= 0.001),
                    "value": round(row_temp_f(src), 1),
                    "unit": "°F",
                    "label": "temp",
                    "window_hours": 1 if int(src["fxx"]) <= 48 else 3,
                }
                for src in block_rows
            ],
            source,
            "NBM core temperature used as flash-freeze proxy with precipitation as wet-surface signal",
        )

    for hazard_id, key in [("RAIN", "rain_in"), ("SNOW", "snow_in"), ("FZRA", "fzra_in")]:
        total = totals[hazard_id]
        update_threat_from_blocks(
            threats_payload,
            timeline,
            hazard_id,
            "72-hr total",
            "Trace" if 0 < total < 0.01 else f"{total:.2f} in",
            "Trace" if 0 < total < 0.01 else f"72-hr total {total:.2f} in",
            f"NBM core {hazard_id.lower()} amount summarized over 72 hours",
            source,
            key,
        )

    update_threat_from_blocks(threats_payload, timeline, "LIGHTNING", "Peak 3-hour thunder probability", "", "Peak 3-hour thunder probability", "NBM core 3-hour thunder probability at KRNO", source, "probability")
    update_threat_from_blocks(threats_payload, timeline, "VISIBILITY", "Lowest visibility", "", "Lowest visibility", "NBM core visibility at KRNO", source, "visibility_mi", lower_is_worse=True)
    update_threat_from_blocks(threats_payload, timeline, "FLASH_FREEZE", "Lowest temperature", "", "Temperature/wet-surface freeze risk", "NBM core temperature and precipitation proxy at KRNO", source, "temp_f", lower_is_worse=True)

    if temp_values:
        card_temp_values = [row for row in temp_values if int(row.get("fxx", 999)) <= 24] or temp_values
        max_temp = max(card_temp_values, key=lambda row: row["temp_f"])
        min_temp = min(card_temp_values, key=lambda row: row["temp_f"])
        max_impact = temperature_impact_level(float(max_temp["temp_f"]))
        min_impact = temperature_impact_level(float(min_temp["temp_f"]))
        impact = max(max_impact, min_impact)
        risk = risk_from_matrix(impact)
        driver_row = min_temp if min_impact >= max_impact else max_temp
        threat = threats_payload["threats"]["TEMPERATURE"]
        threat.update(
            {
                "risk": risk,
                "risk_level": risk,
                "risk_label": RISK_LABELS[risk],
                "level": risk,
                "impact_level": impact,
                "likelihood_category": None,
                "risk_method": "krno_threshold_deterministic_core_24hr_max_min",
                "metric": f"Max {max_temp['temp_f']:.0f}°F / Min {min_temp['temp_f']:.0f}°F",
                "display_label": "24-hr max/min",
                "display_value": f"{max_temp['temp_f']:.0f}/{min_temp['temp_f']:.0f}°F",
                "peak_start_fxx": driver_row["fxx"],
                "peak_end_fxx": driver_row["fxx"],
                "source_fxx": driver_row["fxx"],
                "peak_valid_utc": driver_row["valid_utc"],
                "driver": "NBM core 2-meter 24-hour temperature max/min at KRNO",
                "data_status": "live",
                "method": CORE_SOURCE_METHOD,
                "source": source,
            }
        )
        for hazard in threats_payload["hazards"]:
            if hazard["id"] == "TEMPERATURE":
                hazard.update({"risk": risk, "probability": None, "level": risk})


def select_qmd_daymax_gust_row(rows: list[dict[str, Any]], day_index: int) -> dict[str, Any] | None:
    return select_first_row(rows, lambda line: ":GUST:10 m above ground:" in line and "ens mean" in line)


def select_qmd_temp_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    max_row = select_first_row(rows, lambda line: ":TMP:2 m above ground:" in line and "hour fcst:ens mean" in line)
    min_row = select_first_row(rows, lambda line: ":TMP:2 m above ground:" in line and "min fcst:" in line and is_deterministic(line))
    if max_row:
        selected["max"] = max_row
    if min_row:
        selected["min"] = min_row
    return selected


def select_qmd_rain_rows(rows: list[dict[str, Any]], fxx: int) -> dict[str, dict[str, Any]]:
    six_hr = six_hour_accum_window(fxx)
    return {
        "amount": select_first_row(rows, lambda line: ":APCP:surface:" in line and six_hr in line and is_deterministic(line)),
        "prob_0p5_6hr": select_first_row(rows, lambda line: ":APCP:surface:" in line and six_hr in line and "prob >12.7" in line),
        "prob_1p5_6hr": select_first_row(rows, lambda line: ":APCP:surface:" in line and six_hr in line and "prob >38.1" in line),
        "prob_3p0_6hr": select_first_row(rows, lambda line: ":APCP:surface:" in line and six_hr in line and "prob >76.2" in line),
        "prob_5p0_6hr": select_first_row(rows, lambda line: ":APCP:surface:" in line and six_hr in line and "prob >127" in line),
    }


def apply_qmd_wind_card(timeline: dict[str, Any], threats_payload: dict[str, Any]) -> None:
    qmd_cycle = None
    for cycle in candidate_cycles(count=8):
        try:
            rows = parse_idx(fetch_text(aws_grib_url(cycle, "qmd", 24) + ".idx"))
        except Exception:
            continue
        if select_qmd_daymax_gust_row(rows, 0):
            qmd_cycle = cycle
            break
    if not qmd_cycle:
        return

    day_rows = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for day_index, fxx in enumerate([24, 48, 72]):
            try:
                grib_url = aws_grib_url(qmd_cycle, "qmd", fxx)
                rows = parse_idx(fetch_text(grib_url + ".idx"))
                row = select_qmd_daymax_gust_row(rows, day_index)
                if not row:
                    continue
                gust_mph = extract_row_value(grib_url, row, tmp, f"qmd_gust_day{day_index}") * MPS_TO_MPH
                day_rows.append({"day_index": day_index, "fxx": fxx, "gust_mph": gust_mph, "valid_utc": iso(qmd_cycle + timedelta(hours=fxx))})
            except Exception:
                continue

    if not day_rows:
        return

    peak = max(day_rows, key=lambda row: row["gust_mph"])
    impact = wind_impact_level(float(peak["gust_mph"]))
    risk = risk_from_matrix(impact)
    threat = threats_payload["threats"]["WIND"]
    threat.update(
        {
            "risk": risk,
            "risk_level": risk,
            "risk_label": RISK_LABELS[risk],
            "level": risk,
            "impact_level": impact,
            "likelihood_category": None,
            "risk_method": "krno_threshold_deterministic_qmd_24hr_max_gust",
            "metric": f"Max gust {peak['gust_mph']:.0f} mph",
            "display_label": "Max gust",
            "display_value": f"{peak['gust_mph']:.0f} mph",
            "peak_start_fxx": max(1, int(peak["fxx"]) - 23),
            "peak_end_fxx": peak["fxx"],
            "source_fxx": peak["fxx"],
            "peak_valid_utc": peak["valid_utc"],
            "driver": "NBM QMD 24-hour maximum gust at KRNO",
            "methodology": "Wind risk card uses QMD 24-hour maximum gust at KRNO mapped to the KRNO wind impact table.",
            "data_status": "live",
            "method": QMD_SOURCE_METHOD,
            "source": f"NOAA NBM QMD AWS {qmd_cycle:%HZ}",
            "daily_values": [{"fxx": row["fxx"], "gust_mph": round(row["gust_mph"], 1), "valid_utc": row["valid_utc"]} for row in day_rows],
        }
    )
    for hazard in threats_payload["hazards"]:
        if hazard["id"] == "WIND":
            hazard.update({"risk": risk, "probability": None, "level": risk})


def apply_qmd_rain_card(timeline: dict[str, Any], threats_payload: dict[str, Any]) -> None:
    qmd_cycle = find_latest_available_cycle("qmd", [6, 72])
    source = f"NOAA NBM QMD AWS {qmd_cycle:%HZ}"
    six_hour_rows: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for fxx in range(6, 73, 6):
            grib_url = aws_grib_url(qmd_cycle, "qmd", fxx)
            rows = parse_idx(fetch_text(grib_url + ".idx"))
            selected = select_qmd_rain_rows(rows, fxx)
            amount_row = selected.get("amount")
            if not amount_row:
                continue
            amount_in = extract_row_value(grib_url, amount_row, tmp, f"qmd_rain_f{fxx:03d}") * MM_TO_IN
            probs: dict[str, float] = {}
            for key in ["prob_0p5_6hr", "prob_1p5_6hr", "prob_3p0_6hr", "prob_5p0_6hr"]:
                row = selected.get(key)
                if row:
                    probs[key] = extract_row_value(grib_url, row, tmp, f"qmd_rain_{key}_f{fxx:03d}")
            impact = rain_impact_level(amount_in / 6.0)
            risk = max(
                risk_from_matrix(impact),
                matrix_probability_risk(probs, [("prob_5p0_6hr", 5), ("prob_3p0_6hr", 4), ("prob_1p5_6hr", 3), ("prob_0p5_6hr", 2)]),
            )
            six_hour_rows.append(
                {
                    "fxx": fxx,
                    "valid_utc": iso(qmd_cycle + timedelta(hours=fxx)),
                    "start_fxx": max(1, fxx - 5),
                    "end_fxx": fxx,
                    "amount_in": round(amount_in, 3),
                    "rate_in_hr": round(amount_in / 6.0, 3),
                    "impact_level": impact,
                    "risk": risk,
                    "probabilities": {key: round(value, 1) for key, value in probs.items()},
                }
            )

    if not six_hour_rows:
        return

    total_in = sum(row["amount_in"] for row in six_hour_rows)
    best = max(six_hour_rows, key=lambda row: (row["risk"], row["rate_in_hr"]))
    probability = max(best.get("probabilities", {}).values() or [0.0])
    threat = threats_payload["threats"]["RAIN"]
    threat.update(
        {
            "prob": probability,
            "probability": probability,
            "risk": best["risk"],
            "risk_level": best["risk"],
            "risk_label": RISK_LABELS[best["risk"]],
            "level": best["risk"],
            "impact_level": best["impact_level"],
            "likelihood_category": likelihood_bin(probability),
            "risk_method": "krno_threshold_probability_matrix_qmd_6hr_precip",
            "metric": "Trace" if 0 < total_in < 0.01 else f"72-hr total {total_in:.2f} in",
            "display_label": "QMD 72-hr total",
            "display_value": "Trace" if 0 < total_in < 0.01 else f"{total_in:.2f} in",
            "window": "72 hr",
            "peak_start_fxx": best["start_fxx"],
            "peak_end_fxx": best["end_fxx"],
            "source_fxx": best["fxx"],
            "peak_valid_utc": best["valid_utc"],
            "driver": "NBM QMD 6-hour precipitation probabilities and deterministic precipitation at KRNO",
            "methodology": "Rain summary uses QMD 6-hour precipitation windows mapped to the KRNO rain/flooding impact table and likelihood-vs-impact matrix.",
            "data_status": "live",
            "method": QMD_SOURCE_METHOD,
            "source": source,
            "six_hour_values": six_hour_rows,
        }
    )
    for hazard in threats_payload["hazards"]:
        if hazard["id"] == "RAIN":
            hazard.update({"risk": best["risk"], "probability": probability, "level": best["risk"]})


def apply_qmd_temperature_card(timeline: dict[str, Any], threats_payload: dict[str, Any]) -> None:
    qmd_cycle = find_latest_available_cycle("qmd", [24, 48, 72])
    source = f"NOAA NBM QMD AWS {qmd_cycle:%HZ}"
    daily_values: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for fxx in [24, 48, 72]:
            grib_url = aws_grib_url(qmd_cycle, "qmd", fxx)
            rows = parse_idx(fetch_text(grib_url + ".idx"))
            selected = select_qmd_temp_rows(rows)
            if "max" not in selected or "min" not in selected:
                continue
            max_f = k_to_f(extract_row_value(grib_url, selected["max"], tmp, f"qmd_temp_max_f{fxx:03d}"))
            min_f = k_to_f(extract_row_value(grib_url, selected["min"], tmp, f"qmd_temp_min_f{fxx:03d}"))
            max_impact = temperature_impact_level(max_f)
            min_impact = temperature_impact_level(min_f)
            impact = max(max_impact, min_impact)
            daily_values.append(
                {
                    "fxx": fxx,
                    "valid_utc": iso(qmd_cycle + timedelta(hours=fxx)),
                    "max_f": round(max_f, 1),
                    "min_f": round(min_f, 1),
                    "impact_level": impact,
                    "risk": risk_from_matrix(impact),
                }
            )

    if not daily_values:
        return

    max_temp = max(daily_values, key=lambda row: row["max_f"])
    min_temp = min(daily_values, key=lambda row: row["min_f"])
    hot_impact = temperature_impact_level(float(max_temp["max_f"]))
    cold_impact = temperature_impact_level(float(min_temp["min_f"]))
    impact = max(hot_impact, cold_impact)
    risk = risk_from_matrix(impact)
    driver_row = min_temp if cold_impact >= hot_impact else max_temp
    threat = threats_payload["threats"]["TEMPERATURE"]
    threat.update(
        {
            "prob": None,
            "probability": None,
            "risk": risk,
            "risk_level": risk,
            "risk_label": RISK_LABELS[risk],
            "level": risk,
            "impact_level": impact,
            "likelihood_category": None,
            "risk_method": "krno_threshold_deterministic_qmd_max_min_temperature",
            "metric": f"Max {max_temp['max_f']:.0f}°F / Min {min_temp['min_f']:.0f}°F",
            "display_label": "QMD max/min",
            "display_value": f"{max_temp['max_f']:.0f}/{min_temp['min_f']:.0f}°F",
            "peak_start_fxx": driver_row["fxx"],
            "peak_end_fxx": driver_row["fxx"],
            "source_fxx": driver_row["fxx"],
            "peak_valid_utc": driver_row["valid_utc"],
            "driver": "NBM QMD max/min temperature at KRNO",
            "methodology": "Temperature summary uses QMD max/min temperature mapped to the KRNO cold/heat impact table.",
            "data_status": "live",
            "method": QMD_SOURCE_METHOD,
            "source": source,
            "daily_values": daily_values,
        }
    )
    for hazard in threats_payload["hazards"]:
        if hazard["id"] == "TEMPERATURE":
            hazard.update({"risk": risk, "probability": None, "level": risk})


def empty_hazard(hazard: str, start_fxx: int, end_fxx: int, start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "id": hazard,
        "label": hazard,
        "name": hazard,
        "risk": 0,
        "risk_label": RISK_LABELS[0],
        "level": 0,
        "impact_level": 0,
        "prob": 0,
        "probability": 0,
        "metric": "Awaiting NBM/QMD extraction",
        "driver": "NBM schema is wired; hazard extraction is pending",
        "source_fxx": start_fxx,
        "peak_valid_utc": iso(start),
        "valid_start_utc": iso(start),
        "valid_end_utc": iso(end),
        "start_fxx": start_fxx,
        "end_fxx": end_fxx,
        "window_hours": end_fxx - start_fxx + 1,
        "data_status": "schema_pending",
        "method": "nbm_dss_schema_v1",
        "source": "NBM/QMD pending",
        "hourly_values": [],
    }


def build_outputs() -> tuple[dict[str, Any], dict[str, Any]]:
    generated = utc_now()
    cycle = find_latest_available_cycle("core", [1, 72])
    cycle_iso = iso(cycle)
    cycle_label = f"NBM {cycle:%HZ}"

    blocks: list[dict[str, Any]] = []
    block_hazards: list[dict[str, Any]] = []

    for block_index, start_fxx in enumerate(range(1, 73, 3)):
        end_fxx = min(start_fxx + 2, 72)
        start = cycle + timedelta(hours=start_fxx)
        end = cycle + timedelta(hours=end_fxx)
        block: dict[str, Any] = {
            "block_index": block_index,
            "start_fxx": start_fxx,
            "end_fxx": end_fxx,
            "valid_start_utc": iso(start),
            "valid_end_utc": iso(end),
        }

        hazards_for_block: dict[str, Any] = {}
        for hazard in HAZARDS:
            block[hazard] = 0
            hazards_for_block[hazard] = empty_hazard(hazard, start_fxx, end_fxx, start, end)

        blocks.append(block)
        block_hazards.append(hazards_for_block)

    threats = {}
    hazards_list = []
    for hazard in HAZARDS:
        threat = {
            "id": hazard,
            "title": hazard,
            "name": hazard,
            "prob": 0,
            "probability": 0,
            "risk": 0,
            "risk_level": 0,
            "risk_label": RISK_LABELS[0],
            "level": 0,
            "impact_level": 0,
            "metric": "Awaiting NBM/QMD extraction",
            "display_label": "72-hr probabilistic risk",
            "display_value": "Pending",
            "window": "72 hr",
            "peak_start_fxx": None,
            "peak_end_fxx": None,
            "source_fxx": None,
            "peak_valid_utc": None,
            "driver": "NBM schema is wired; hazard extraction is pending",
            "methodology": METHODOLOGY["risk_matrix"],
            "data_status": "schema_pending",
            "method": "nbm_dss_schema_v1",
            "native_windows": NATIVE_WINDOWS.get(hazard, []),
        }
        threats[hazard] = threat
        hazards_list.append({"id": hazard, "risk": 0, "probability": 0, "level": 0})

    common = {
        **SITE,
        "source": "NBM/QMD schema scaffold",
        "generated_utc": generated,
        "cycle_utc_iso": cycle_iso,
        "cycle": cycle_label,
        "valid_period": {"hours": 72, "start_utc": iso(cycle + timedelta(hours=1)), "end_utc": iso(cycle + timedelta(hours=72))},
    }

    timeline = {
        **common,
        "block_hours": "mixed_native_windows",
        "blocks": blocks,
        "block_hazards": block_hazards,
        "metadata": METHODOLOGY,
    }

    threats_payload = {
        **common,
        "threats": threats,
        "hazards": hazards_list,
        "methodology": METHODOLOGY["risk_matrix"],
        "metadata": METHODOLOGY,
    }

    try:
        apply_core_wind(timeline, threats_payload)
    except Exception as exc:
        timeline.setdefault("extraction_errors", {})["WIND"] = str(exc)
        threats_payload.setdefault("extraction_errors", {})["WIND"] = str(exc)

    try:
        apply_core_remaining_hazards(timeline, threats_payload)
    except Exception as exc:
        timeline.setdefault("extraction_errors", {})["CORE_HAZARDS"] = str(exc)
        threats_payload.setdefault("extraction_errors", {})["CORE_HAZARDS"] = str(exc)

    try:
        apply_qmd_wind_card(timeline, threats_payload)
    except Exception as exc:
        timeline.setdefault("extraction_errors", {})["QMD_WIND"] = str(exc)
        threats_payload.setdefault("extraction_errors", {})["QMD_WIND"] = str(exc)

    try:
        apply_qmd_rain_card(timeline, threats_payload)
    except Exception as exc:
        timeline.setdefault("extraction_errors", {})["QMD_RAIN"] = str(exc)
        threats_payload.setdefault("extraction_errors", {})["QMD_RAIN"] = str(exc)

    try:
        apply_qmd_temperature_card(timeline, threats_payload)
    except Exception as exc:
        timeline.setdefault("extraction_errors", {})["QMD_TEMPERATURE"] = str(exc)
        threats_payload.setdefault("extraction_errors", {})["QMD_TEMPERATURE"] = str(exc)

    return timeline, threats_payload


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)

    timeline, threats = build_outputs()
    (DOCS / "nbm_timeline.json").write_text(json.dumps(timeline, indent=2))
    (DOCS / "nbm_threats.json").write_text(json.dumps(threats, indent=2))
    (DATA / "nbm_dss_methodology.json").write_text(json.dumps(METHODOLOGY, indent=2))
    print("Wrote docs/nbm_timeline.json")
    print("Wrote docs/nbm_threats.json")
    print("Wrote data/nbm_dss_methodology.json")


if __name__ == "__main__":
    main()
