from __future__ import annotations

import json
import math
import re
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
import xarray as xr


DOCS = Path("docs")
DATA = Path("data")
DOCS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

BUCKET_BASE = "https://noaa-rrfs-pds.s3.amazonaws.com"
ROOT_TEMPLATE = "rrfs_a/refs.{ymd}/{hh}/enspost/"

SITE_NAME = "KRNO"
SITE_LAT = 39.4991
SITE_LON = -119.7681

FORECAST_HOURS = list(range(1, 61))
BLOCK_HOURS = 3
BLOCK_COUNT = 20

MPS_TO_MPH = 2.2369362921
M_TO_SM = 0.000621371
M_TO_IN = 39.3700787402
K_TO_F = 9.0 / 5.0

TIMEOUT = 60


# ---------------------------------------------------------------------
# Impact thresholds.
# These are intentionally centralized so we can later make them event-configurable.
# ---------------------------------------------------------------------

THRESHOLDS = {
    "WIND": [
        {"key": "gt_30_mph", "label": ">30 mph", "threshold": 30.0, "impact": 2},
        {"key": "gt_45_mph", "label": ">45 mph", "threshold": 45.0, "impact": 3},
        {"key": "gt_58_mph", "label": ">58 mph", "threshold": 58.0, "impact": 4},
        {"key": "gt_65_mph", "label": ">65 mph", "threshold": 65.0, "impact": 5},
    ],
    "RAIN": [
        {"key": "gt_0p10_in_hr", "label": ">0.10 in/hr", "threshold": 0.10, "impact": 2},
        {"key": "gt_0p25_in_hr", "label": ">0.25 in/hr", "threshold": 0.25, "impact": 3},
        {"key": "gt_0p50_in_hr", "label": ">0.50 in/hr", "threshold": 0.50, "impact": 4},
        {"key": "gt_0p80_in_hr", "label": ">0.80 in/hr", "threshold": 0.80, "impact": 5},
    ],
    "SNOW": [
        {"key": "gt_0p10_in_hr", "label": ">0.10 in/hr", "threshold": 0.10, "impact": 2},
        {"key": "gt_0p50_in_hr", "label": ">0.50 in/hr", "threshold": 0.50, "impact": 3},
        {"key": "gt_1p00_in_hr", "label": ">1.00 in/hr", "threshold": 1.00, "impact": 4},
        {"key": "gt_2p00_in_hr", "label": ">2.00 in/hr", "threshold": 2.00, "impact": 5},
    ],
    "LIGHTNING": [
        {"key": "gt_5_pct", "label": "Lightning chance: 5-25%", "threshold": 5.0, "impact": 2},
        {"key": "gt_25_pct", "label": "Lightning chance: 25-50%", "threshold": 25.0, "impact": 3},
        {"key": "gt_50_pct", "label": "Lightning chance: 50-75%", "threshold": 50.0, "impact": 4},
        {"key": "gt_75_pct", "label": "Lightning chance: >75%", "threshold": 75.0, "impact": 5},
    ],
    "VISIBILITY": [
        {"key": "lt_5_sm", "label": "<5 SM", "threshold": 5.0, "impact": 2},
        {"key": "lt_3_sm", "label": "<3 SM", "threshold": 3.0, "impact": 3},
        {"key": "lt_1_sm", "label": "<1 SM", "threshold": 1.0, "impact": 4},
        {"key": "lt_0p5_sm", "label": "<0.5 SM", "threshold": 0.5, "impact": 5},
    ],
    "FZRA": [
        {"key": "gt_trace", "label": "Trace freezing rain", "threshold": 0.001, "impact": 2},
        {"key": "gt_0p01_in_hr", "label": ">0.01 in/hr", "threshold": 0.01, "impact": 3},
        {"key": "gt_0p05_in_hr", "label": ">0.05 in/hr", "threshold": 0.05, "impact": 4},
        {"key": "gt_0p10_in_hr", "label": ">0.10 in/hr", "threshold": 0.10, "impact": 5},
    ],
    "TEMPERATURE_HIGH": [
        {"key": "gt_90_f", "label": ">90°F", "threshold": 90.0, "impact": 2},
        {"key": "gt_95_f", "label": ">95°F", "threshold": 95.0, "impact": 3},
        {"key": "gt_100_f", "label": ">100°F", "threshold": 100.0, "impact": 4},
        {"key": "gt_105_f", "label": ">105°F", "threshold": 105.0, "impact": 5},
    ],
    "TEMPERATURE_LOW": [
        {"key": "lt_32_f", "label": "<32°F", "threshold": 32.0, "impact": 2},
        {"key": "lt_28_f", "label": "<28°F", "threshold": 28.0, "impact": 3},
        {"key": "lt_20_f", "label": "<20°F", "threshold": 20.0, "impact": 4},
        {"key": "lt_10_f", "label": "<10°F", "threshold": 10.0, "impact": 5},
    ],
    "FLASH_FREEZE": [
        {"key": "wet_tw_lt_32", "label": "Wet surface + Tw ≤32°F", "threshold": 32.0, "impact": 3},
        {"key": "wet_tw_lt_30", "label": "Wet surface + Tw ≤30°F", "threshold": 30.0, "impact": 4},
        {"key": "wet_tw_lt_28", "label": "Wet surface + Tw ≤28°F", "threshold": 28.0, "impact": 5},
    ],
}


HAZARD_ORDER = [
    "WIND",
    "LIGHTNING",
    "SNOW",
    "VISIBILITY",
    "FZRA",
    "FLASH_FREEZE",
    "RAIN",
    "TEMPERATURE",
]


# ---------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_cycle_floor() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return now.replace(hour=(now.hour // 6) * 6)


def cycle_candidates(hours_back: int = 48) -> list[datetime]:
    base = latest_cycle_floor()
    return [base - timedelta(hours=h) for h in range(0, hours_back + 1, 6)]


def refs_prefix(cycle: datetime) -> str:
    return ROOT_TEMPLATE.format(ymd=cycle.strftime("%Y%m%d"), hh=cycle.strftime("%H"))


def refs_key(cycle: datetime, product: str, fxx: int, suffix: str = "grib2") -> str:
    hh = cycle.strftime("%H")
    return f"{refs_prefix(cycle)}refs.t{hh}z.{product}.f{fxx:02d}.conus.{suffix}"


def refs_url(cycle: datetime, product: str, fxx: int, suffix: str = "grib2") -> str:
    return f"{BUCKET_BASE}/{refs_key(cycle, product, fxx, suffix)}"


def request_text(url: str) -> str:
    response = requests.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text


def s3_list(prefix: str) -> list[str]:
    keys: list[str] = []
    token = None

    while True:
        params = {
            "list-type": "2",
            "prefix": prefix,
            "max-keys": "1000",
        }

        if token:
            params["continuation-token"] = token

        url = f"{BUCKET_BASE}/?{urllib.parse.urlencode(params)}"
        response = requests.get(url, timeout=TIMEOUT)
        response.raise_for_status()

        root = ET.fromstring(response.text)
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        for item in root.findall(f"{ns}Contents"):
            key = item.findtext(f"{ns}Key")
            if key:
                keys.append(key)

        truncated = (root.findtext(f"{ns}IsTruncated") or "").lower() == "true"
        token = root.findtext(f"{ns}NextContinuationToken")

        if not truncated or not token:
            break

    return keys


def parse_idx(idx_text: str) -> list[dict[str, Any]]:
    rows = []
    lines = idx_text.splitlines()

    for i, line in enumerate(lines):
        parts = line.split(":")
        if len(parts) < 3:
            continue

        try:
            msg_no = int(parts[0])
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
                "message_no": msg_no,
                "start_byte": start_byte,
                "end_byte": end_byte,
                "line": line,
            }
        )

    return rows


def download_message(grib_url: str, row: dict[str, Any], out_path: Path) -> None:
    if row.get("end_byte") is not None:
        headers = {"Range": f"bytes={row['start_byte']}-{row['end_byte']}"}
    else:
        headers = {"Range": f"bytes={row['start_byte']}-"}

    response = requests.get(grib_url, headers=headers, timeout=TIMEOUT)
    response.raise_for_status()

    if len(response.content) < 100:
        raise RuntimeError(f"Downloaded GRIB message too small: {len(response.content)} bytes")

    out_path.write_bytes(response.content)


def normalize_lon(lon: float) -> float:
    return lon + 360.0 if lon < 0 else lon


def lat_lon_names(ds: xr.Dataset) -> tuple[str, str]:
    lat_candidates = ["latitude", "lat", "gridlat_0"]
    lon_candidates = ["longitude", "lon", "gridlon_0"]

    lat_name = next((n for n in lat_candidates if n in ds.coords or n in ds.variables), None)
    lon_name = next((n for n in lon_candidates if n in ds.coords or n in ds.variables), None)

    if not lat_name or not lon_name:
        raise RuntimeError(f"Could not find lat/lon names. Variables: {list(ds.variables)}")

    return lat_name, lon_name


def nearest_value(ds: xr.Dataset) -> tuple[str, float]:
    data_vars = list(ds.data_vars)
    if not data_vars:
        raise RuntimeError("No data variables found in GRIB message.")

    var_name = data_vars[0]
    lat_name, lon_name = lat_lon_names(ds)

    lat = ds[lat_name]
    lon = ds[lon_name]

    target_lon_360 = normalize_lon(SITE_LON)

    if lat.ndim == 1 and lon.ndim == 1:
        lat_idx = int(abs(lat - SITE_LAT).argmin())
        lon_idx = int(abs(lon - target_lon_360).argmin())
        value = ds[var_name].isel({lat_name: lat_idx, lon_name: lon_idx}).values
    else:
        lat_values = lat.values
        lon_values = lon.values

        target_lon = target_lon_360 if float(lon_values.max()) > 180 else SITE_LON
        dist2 = (lat_values - SITE_LAT) ** 2 + (lon_values - target_lon) ** 2
        flat_idx = int(dist2.argmin())
        iy, ix = divmod(flat_idx, dist2.shape[1])

        indexers = {}
        for dim, idx in zip(lat.dims, [iy, ix]):
            if dim in ds[var_name].dims:
                indexers[dim] = idx

        value = ds[var_name].isel(indexers).values

    value_float = float(value.squeeze())

    if math.isnan(value_float):
        raise RuntimeError(f"Nearest value for {var_name} is NaN.")

    return var_name, value_float


def extract_value(grib_url: str, row: dict[str, Any], label: str) -> tuple[str, float]:
    with tempfile.TemporaryDirectory() as tmpdir:
        msg_path = Path(tmpdir) / f"{label}.grib2"
        download_message(grib_url, row, msg_path)

        ds = xr.open_dataset(
            msg_path,
            engine="cfgrib",
            backend_kwargs={"indexpath": "", "errors": "ignore"},
        )

        try:
            return nearest_value(ds)
        finally:
            ds.close()


def k_to_f(k: float) -> float:
    return (k - 273.15) * K_TO_F + 32.0


def c_to_f(c: float) -> float:
    return c * K_TO_F + 32.0


def probability_to_likelihood(prob: float) -> int:
    if prob >= 90:
        return 5
    if prob >= 66:
        return 4
    if prob >= 33:
        return 3
    if prob >= 10:
        return 2
    if prob > 0:
        return 1
    return 0


def matrix_risk(prob: float, impact: int) -> int:
    if prob <= 0:
        return 0

    likelihood = probability_to_likelihood(prob)

    matrix = {
        1: {1: 1, 2: 1, 3: 1, 4: 2, 5: 2},
        2: {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
        3: {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
        4: {1: 1, 2: 2, 3: 3, 4: 4, 5: 4},
        5: {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
    }

    impact = max(1, min(5, int(impact)))
    return matrix[likelihood][impact]


def risk_label(risk: int) -> str:
    return {
        0: "None",
        1: "Little to None",
        2: "Minor",
        3: "Moderate",
        4: "Major",
        5: "Extreme",
    }.get(risk, "Unknown")


# ---------------------------------------------------------------------
# REFS field search
# ---------------------------------------------------------------------

def find_row(idx_rows: list[dict[str, Any]], include_terms: list[str], exclude_terms: list[str] | None = None) -> dict[str, Any] | None:
    exclude_terms = exclude_terms or []

    for row in idx_rows:
        line = row["line"]
        upper = line.upper()

        if all(term.upper() in upper for term in include_terms) and not any(term.upper() in upper for term in exclude_terms):
            return row

    return None


def find_any_row(idx_rows: list[dict[str, Any]], term_sets: list[list[str]], exclude_terms: list[str] | None = None) -> dict[str, Any] | None:
    for terms in term_sets:
        row = find_row(idx_rows, terms, exclude_terms)
        if row:
            return row
    return None


def get_avrg_idx_rows(cycle: datetime, fxx: int) -> list[dict[str, Any]]:
    idx_url = refs_url(cycle, "avrg", fxx, "grib2.idx")
    idx_text = request_text(idx_url)
    return parse_idx(idx_text)


def get_avrg_grib_url(cycle: datetime, fxx: int) -> str:
    return refs_url(cycle, "avrg", fxx, "grib2")


def extract_avrg_field(
    cycle: datetime,
    fxx: int,
    idx_rows: list[dict[str, Any]],
    term_sets: list[list[str]],
    label: str,
    exclude_terms: list[str] | None = None,
) -> dict[str, Any]:
    row = find_any_row(idx_rows, term_sets, exclude_terms=exclude_terms)

    if row is None:
        return {
            "status": "missing",
            "value": None,
            "variable": None,
            "idx_line": None,
        }

    try:
        var_name, value = extract_value(
            grib_url=get_avrg_grib_url(cycle, fxx),
            row=row,
            label=f"{label}_f{fxx:02d}",
        )

        return {
            "status": "ok",
            "value": float(value),
            "variable": var_name,
            "idx_line": row["line"],
        }

    except Exception as exc:
        return {
            "status": "error",
            "value": None,
            "variable": None,
            "idx_line": row["line"],
            "message": str(exc),
        }


# ---------------------------------------------------------------------
# Hazard evaluation
# ---------------------------------------------------------------------

def exceedance_probability_from_mean(value: float | None, threshold: float, mode: str = "gt") -> float:
    """
    Temporary deterministic fallback.

    REFS probability products will replace this where exact probability fields are mapped.
    For now:
      - if the mean/average field crosses threshold, use 100%
      - otherwise use 0%

    This keeps the dashboard running while we inventory exact probability fields.
    """
    if value is None:
        return 0.0

    if mode == "gt":
        return 100.0 if value >= threshold else 0.0

    if mode == "lt":
        return 100.0 if value <= threshold else 0.0

    return 0.0


def best_threshold_eval(
    hazard: str,
    value: float | None,
    thresholds: list[dict[str, Any]],
    mode: str = "gt",
) -> dict[str, Any]:
    candidates = []

    for threshold in thresholds:
        prob = exceedance_probability_from_mean(value, float(threshold["threshold"]), mode=mode)
        risk = matrix_risk(prob, int(threshold["impact"]))

        candidates.append(
            {
                "threshold_key": threshold["key"],
                "label": threshold["label"],
                "threshold": threshold["threshold"],
                "impact_level": threshold["impact"],
                "probability": prob,
                "risk": risk,
                "risk_label": risk_label(risk),
            }
        )

    if not candidates or all(c["probability"] <= 0 for c in candidates):
        return {
            "hazard": hazard,
            "prob": 0.0,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": "No signal",
            "driver": "No threshold exceeded",
            "candidates": candidates,
        }

    best = max(candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))

    return {
        "hazard": hazard,
        "prob": best["probability"],
        "risk": best["risk"],
        "risk_label": best["risk_label"],
        "level": best["impact_level"],
        "metric": best["label"],
        "driver": f"{best['probability']:.0f}% signal for {best['label']}",
        "candidates": candidates,
    }


def block_best(hourly_items: list[dict[str, Any]], hazard: str) -> dict[str, Any]:
    items = [h["hazards"].get(hazard) for h in hourly_items if h.get("hazards", {}).get(hazard)]

    if not items:
        return {
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "prob": 0.0,
            "metric": "No data",
            "driver": "No data",
            "source_fxx": None,
        }

    return max(
        items,
        key=lambda x: (
            int(x.get("risk", 0)),
            float(x.get("prob", 0.0)),
            int(x.get("level", 0)),
        ),
    )


def card_best(hourly: list[dict[str, Any]], hazard: str) -> dict[str, Any]:
    items = [h["hazards"].get(hazard) for h in hourly if h.get("hazards", {}).get(hazard)]

    if not items:
        return {
            "prob": 0.0,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": "No data",
            "driver": "No data",
            "source_fxx": None,
        }

    return max(
        items,
        key=lambda x: (
            int(x.get("risk", 0)),
            float(x.get("prob", 0.0)),
            int(x.get("level", 0)),
        ),
    )


def display_value_for_card(hazard: str, hourly: list[dict[str, Any]]) -> tuple[str, Any]:
    valid = [h for h in hourly if h.get("values")]

    if hazard == "WIND":
        vals = [(h["values"].get("wind_gust_mph"), h) for h in valid if h["values"].get("wind_gust_mph") is not None]
        if not vals:
            return "Max gust", "No data"
        max_val, _ = max(vals, key=lambda x: x[0])
        return "60-hr max gust", f"{max_val:.0f} mph"

    if hazard == "RAIN":
        vals = [(h["values"].get("rain_in"), h) for h in valid if h["values"].get("rain_in") is not None]
        if not vals:
            return "Rain", "No data"
        max_val, _ = max(vals, key=lambda x: x[0])
        return "1-hr rain", f'{max_val:.2f}"'

    if hazard == "SNOW":
        vals = [(h["values"].get("snow_in"), h) for h in valid if h["values"].get("snow_in") is not None]
        if not vals:
            return "Snow", "No data"
        max_val, _ = max(vals, key=lambda x: x[0])
        if max_val <= 0:
            return "1-hr snow", '0"'
        if max_val < 0.1:
            return "1-hr snow", "Trace"
        return "1-hr snow", f'{max_val:.2f}"'

    if hazard == "VISIBILITY":
        vals = [(h["values"].get("visibility_sm"), h) for h in valid if h["values"].get("visibility_sm") is not None]
        if not vals:
            return "Visibility", "No data"
        min_val, _ = min(vals, key=lambda x: x[0])
        return "Min visibility", f"{min_val:.1f} SM"

    if hazard == "TEMPERATURE":
        vals = [(h["values"].get("temperature_f"), h) for h in valid if h["values"].get("temperature_f") is not None]
        if not vals:
            return "Temperature", "No data"
        max_val = max(v for v, _ in vals)
        min_val = min(v for v, _ in vals)
        return "Temp range", f"{min_val:.0f}-{max_val:.0f}°F"

    if hazard == "LIGHTNING":
        vals = [(h["values"].get("lightning_pct"), h) for h in valid if h["values"].get("lightning_pct") is not None]
        if not vals:
            return "Lightning", "No data"
        max_val, _ = max(vals, key=lambda x: x[0])
        return "Lightning chance", f"{max_val:.0f}%"

    if hazard == "FZRA":
        vals = [(h["values"].get("fzra_in"), h) for h in valid if h["values"].get("fzra_in") is not None]
        if not vals:
            return "Freezing rain", "No data"
        max_val, _ = max(vals, key=lambda x: x[0])
        return "Freezing rain", f'{max_val:.2f}"'

    if hazard == "FLASH_FREEZE":
        vals = [(h["values"].get("wet_bulb_f"), h) for h in valid if h["values"].get("wet_bulb_f") is not None]
        if not vals:
            return "Wet bulb", "No data"
        min_val, _ = min(vals, key=lambda x: x[0])
        return "Min wet bulb", f"{min_val:.0f}°F"

    return hazard, "No data"


# ---------------------------------------------------------------------
# Hourly extraction
# ---------------------------------------------------------------------

def extract_hour(cycle: datetime, fxx: int) -> dict[str, Any]:
    valid_utc = (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")

    result = {
        "fxx": fxx,
        "valid_utc": valid_utc,
        "status": "ok",
        "values": {},
        "fields": {},
        "hazards": {},
    }

    try:
        idx_rows = get_avrg_idx_rows(cycle, fxx)
    except Exception as exc:
        result["status"] = "error"
        result["message"] = f"Could not fetch avrg IDX: {exc}"
        return result

    # Wind gust
    wind = extract_avrg_field(
        cycle,
        fxx,
        idx_rows,
        term_sets=[
            ["GUST", "10 m above ground"],
            ["GUST"],
        ],
        label="wind_gust",
    )
    result["fields"]["wind_gust"] = wind
    if wind["value"] is not None:
        result["values"]["wind_gust_mph"] = round(wind["value"] * MPS_TO_MPH, 1)

    # Temperature 2m
    temp = extract_avrg_field(
        cycle,
        fxx,
        idx_rows,
        term_sets=[
            ["TMP", "2 m above ground"],
            ["TMP"],
        ],
        label="temperature",
    )
    result["fields"]["temperature"] = temp
    if temp["value"] is not None:
        result["values"]["temperature_f"] = round(k_to_f(temp["value"]), 1)

    # Dewpoint 2m, useful for wet bulb approximation later
    dew = extract_avrg_field(
        cycle,
        fxx,
        idx_rows,
        term_sets=[
            ["DPT", "2 m above ground"],
            ["DEW", "2 m above ground"],
            ["DPT"],
        ],
        label="dewpoint",
    )
    result["fields"]["dewpoint"] = dew
    if dew["value"] is not None:
        result["values"]["dewpoint_f"] = round(k_to_f(dew["value"]), 1)

    # Visibility
    vis = extract_avrg_field(
        cycle,
        fxx,
        idx_rows,
        term_sets=[
            ["VIS", "surface"],
            ["VIS"],
        ],
        label="visibility",
    )
    result["fields"]["visibility"] = vis
    if vis["value"] is not None:
        result["values"]["visibility_sm"] = round(vis["value"] * M_TO_SM, 2)

    # Rain / precip
    rain = extract_avrg_field(
        cycle,
        fxx,
        idx_rows,
        term_sets=[
            ["APCP", "0-1 hour acc"],
            ["APCP", "hour acc"],
            ["APCP"],
        ],
        label="rain",
    )
    result["fields"]["rain"] = rain
    if rain["value"] is not None:
        result["values"]["rain_in"] = round(rain["value"] * M_TO_IN, 3)

    # Snow
    snow = extract_avrg_field(
        cycle,
        fxx,
        idx_rows,
        term_sets=[
            ["ASNOW", "0-1 hour acc"],
            ["ASNOW", "hour acc"],
            ["ASNOW"],
        ],
        label="snow",
    )
    result["fields"]["snow"] = snow
    if snow["value"] is not None:
        result["values"]["snow_in"] = round(snow["value"] * M_TO_IN, 3)

    # Freezing rain
    fzra = extract_avrg_field(
        cycle,
        fxx,
        idx_rows,
        term_sets=[
            ["FZRA", "0-1 hour acc"],
            ["FZRA", "hour acc"],
            ["FZRA"],
            ["FRZR"],
        ],
        label="fzra",
    )
    result["fields"]["fzra"] = fzra
    if fzra["value"] is not None:
        result["values"]["fzra_in"] = round(fzra["value"] * M_TO_IN, 3)

    # Lightning probability/chance, if present in avrg.
    lightning = extract_avrg_field(
        cycle,
        fxx,
        idx_rows,
        term_sets=[
            ["LTNG"],
            ["LIGHTNING"],
            ["TSTM"],
        ],
        label="lightning",
    )
    result["fields"]["lightning"] = lightning
    if lightning["value"] is not None:
        # Some model probability fields are already percent.
        # If value is 0-1, convert to percent.
        raw = lightning["value"]
        result["values"]["lightning_pct"] = round(raw * 100.0 if 0 <= raw <= 1 else raw, 1)

    # Wet bulb: direct field preferred. If missing, approximate with average of T/Td as a placeholder.
    wetbulb = extract_avrg_field(
        cycle,
        fxx,
        idx_rows,
        term_sets=[
            ["WETB"],
            ["TWET"],
            ["WET BULB"],
        ],
        label="wetbulb",
    )
    result["fields"]["wet_bulb"] = wetbulb

    if wetbulb["value"] is not None:
        result["values"]["wet_bulb_f"] = round(k_to_f(wetbulb["value"]), 1)
    else:
        t = result["values"].get("temperature_f")
        td = result["values"].get("dewpoint_f")
        if t is not None and td is not None:
            result["values"]["wet_bulb_f"] = round((float(t) + float(td)) / 2.0, 1)
            result["fields"]["wet_bulb"]["status"] = "estimated_from_temp_dewpoint"

    # Hazard evaluations
    wind_mph = result["values"].get("wind_gust_mph")
    result["hazards"]["WIND"] = {
        **best_threshold_eval("WIND", wind_mph, THRESHOLDS["WIND"], mode="gt"),
        "source_fxx": fxx,
        "value": wind_mph,
        "valid_utc": valid_utc,
    }

    rain_in = result["values"].get("rain_in")
    result["hazards"]["RAIN"] = {
        **best_threshold_eval("RAIN", rain_in, THRESHOLDS["RAIN"], mode="gt"),
        "source_fxx": fxx,
        "value": rain_in,
        "valid_utc": valid_utc,
    }

    snow_in = result["values"].get("snow_in")
    result["hazards"]["SNOW"] = {
        **best_threshold_eval("SNOW", snow_in, THRESHOLDS["SNOW"], mode="gt"),
        "source_fxx": fxx,
        "value": snow_in,
        "valid_utc": valid_utc,
    }

    vis_sm = result["values"].get("visibility_sm")
    result["hazards"]["VISIBILITY"] = {
        **best_threshold_eval("VISIBILITY", vis_sm, THRESHOLDS["VISIBILITY"], mode="lt"),
        "source_fxx": fxx,
        "value": vis_sm,
        "valid_utc": valid_utc,
    }

    fzra_in = result["values"].get("fzra_in")
    result["hazards"]["FZRA"] = {
        **best_threshold_eval("FZRA", fzra_in, THRESHOLDS["FZRA"], mode="gt"),
        "source_fxx": fxx,
        "value": fzra_in,
        "valid_utc": valid_utc,
    }

    lightning_pct = result["values"].get("lightning_pct")
    result["hazards"]["LIGHTNING"] = {
        **best_threshold_eval("LIGHTNING", lightning_pct, THRESHOLDS["LIGHTNING"], mode="gt"),
        "source_fxx": fxx,
        "value": lightning_pct,
        "valid_utc": valid_utc,
    }

    temp_f = result["values"].get("temperature_f")
    temp_high = best_threshold_eval("TEMPERATURE", temp_f, THRESHOLDS["TEMPERATURE_HIGH"], mode="gt")
    temp_low = best_threshold_eval("TEMPERATURE", temp_f, THRESHOLDS["TEMPERATURE_LOW"], mode="lt")

    temp_best = max([temp_high, temp_low], key=lambda x: (x["risk"], x["prob"], x["level"]))
    result["hazards"]["TEMPERATURE"] = {
        **temp_best,
        "source_fxx": fxx,
        "value": temp_f,
        "valid_utc": valid_utc,
    }

    wet_bulb_f = result["values"].get("wet_bulb_f")
    wet_signal = any(
        float(result["values"].get(k, 0.0) or 0.0) > 0.0
        for k in ["rain_in", "snow_in", "fzra_in"]
    )

    if wet_signal and wet_bulb_f is not None:
        flash_freeze_eval = best_threshold_eval(
            "FLASH_FREEZE",
            wet_bulb_f,
            THRESHOLDS["FLASH_FREEZE"],
            mode="lt",
        )
    else:
        flash_freeze_eval = {
            "hazard": "FLASH_FREEZE",
            "prob": 0.0,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": "No wet/freezing surface signal",
            "driver": "No joint wet surface + wet bulb freeze signal",
            "candidates": [],
        }

    result["hazards"]["FLASH_FREEZE"] = {
        **flash_freeze_eval,
        "source_fxx": fxx,
        "value": wet_bulb_f,
        "valid_utc": valid_utc,
        "wet_signal": wet_signal,
    }

    return result


# ---------------------------------------------------------------------
# Build outputs
# ---------------------------------------------------------------------

def select_cycle() -> datetime:
    for cycle in cycle_candidates(48):
        prefix = refs_prefix(cycle)

        try:
            keys = s3_list(prefix)
        except Exception:
            continue

        needed = [
            refs_key(cycle, "avrg", 1, "grib2.idx"),
            refs_key(cycle, "avrg", 60, "grib2.idx"),
        ]

        if all(k in keys for k in needed):
            return cycle

    raise RuntimeError("No usable REFS cycle found with avrg f01 and f60 IDX files.")


def build_timeline(cycle: datetime, hourly: list[dict[str, Any]], generated: str) -> dict[str, Any]:
    blocks = []
    block_hazards = []

    for block_index in range(BLOCK_COUNT):
        start_fxx = block_index * BLOCK_HOURS + 1
        end_fxx = start_fxx + BLOCK_HOURS - 1

        block_hours = [
            h for h in hourly
            if h.get("status") == "ok" and start_fxx <= int(h["fxx"]) <= end_fxx
        ]

        valid_start = (cycle + timedelta(hours=start_fxx)).isoformat().replace("+00:00", "Z")
        valid_end = (cycle + timedelta(hours=end_fxx)).isoformat().replace("+00:00", "Z")

        block = {
            "block_index": block_index,
            "start_fxx": start_fxx,
            "end_fxx": end_fxx,
            "valid_start_utc": valid_start,
            "valid_end_utc": valid_end,
        }

        hazard_block = {
            "block_index": block_index,
            "start_fxx": start_fxx,
            "end_fxx": end_fxx,
            "valid_start_utc": valid_start,
            "valid_end_utc": valid_end,
        }

        for hazard in HAZARD_ORDER:
            best = block_best(block_hours, hazard)
            best["valid_start_utc"] = valid_start
            best["valid_end_utc"] = valid_end
            hazard_block[hazard] = best

        blocks.append(block)
        block_hazards.append(hazard_block)

    return {
        "site": SITE_NAME,
        "source": "RRFS REFS AWS",
        "generated_utc": generated,
        "cycle_utc_iso": cycle.isoformat().replace("+00:00", "Z"),
        "cycle": f"REFS {cycle.strftime('%HZ')}",
        "valid_period": "next_60_hours",
        "block_hours": BLOCK_HOURS,
        "blocks": blocks,
        "block_hazards": block_hazards,
        "methodology": (
            "Timeline uses 60 one-hour REFS forecast hours grouped into 20 three-hour blocks. "
            "Each block uses the highest risk one-hour signal inside that three-hour block."
        ),
    }


def build_threats(cycle: datetime, hourly: list[dict[str, Any]], generated: str) -> dict[str, Any]:
    threats = {}
    hazards = []

    for hazard in HAZARD_ORDER:
        best = card_best(hourly, hazard)
        display_label, display_value = display_value_for_card(hazard, hourly)

        threat = {
            **best,
            "display_label": display_label,
            "display_value": display_value,
            "window": "60 hr",
            "methodology": (
                "REFS initial implementation uses available hourly REFS average fields from AWS. "
                "Exact REFS probability-field mapping will replace deterministic threshold fallback "
                "after inventory confirms variable/threshold names."
            ),
        }

        threats[hazard] = threat

        hazards.append(
            {
                "id": hazard,
                "name": hazard,
                "risk_level": int(best.get("risk", 0)),
                "risk_label": best.get("risk_label", "None"),
                "impact_level": int(best.get("level", 0)),
                "probability": float(best.get("prob", 0.0)),
                "metric": best.get("metric"),
                "driver": best.get("driver"),
                "display_label": display_label,
                "display_value": display_value,
                "source_fxx": best.get("source_fxx"),
            }
        )

    return {
        "site": SITE_NAME,
        "source": "RRFS REFS AWS",
        "generated_utc": generated,
        "cycle_utc_iso": cycle.isoformat().replace("+00:00", "Z"),
        "cycle": f"REFS {cycle.strftime('%HZ')}",
        "valid_period": "next_60_hours",
        "threats": threats,
        "hazards": hazards,
        "methodology": (
            "Risk cards use the highest risk signal over the next 60 hours. "
            "Timeline uses 3-hour blocks from 1-hour REFS fields."
        ),
    }


def main() -> None:
    generated = utc_now_iso()

    cycle = select_cycle()
    print(f"Using REFS cycle {cycle:%Y-%m-%d %HZ}")

    hourly = []

    for fxx in FORECAST_HOURS:
        print(f"Processing REFS f{fxx:02d}")
        hourly.append(extract_hour(cycle, fxx))

    threats = build_threats(cycle, hourly, generated)
    timeline = build_timeline(cycle, hourly, generated)

    debug = {
        "site": SITE_NAME,
        "source": "RRFS REFS AWS",
        "generated_utc": generated,
        "cycle_utc_iso": cycle.isoformat().replace("+00:00", "Z"),
        "prefix": refs_prefix(cycle),
        "hour_count": len(hourly),
        "hourly_status_counts": {
            "ok": sum(1 for h in hourly if h.get("status") == "ok"),
            "error": sum(1 for h in hourly if h.get("status") == "error"),
        },
        "thresholds": THRESHOLDS,
        "methodology_note": (
            "This is the first REFS builder. It is designed to get the dashboard running from "
            "REFS AWS data. It currently extracts avrg fields and applies deterministic threshold "
            "fallback when exact REFS probability fields are not yet mapped."
        ),
        "hourly": hourly,
    }

    (DOCS / "threats.json").write_text(json.dumps(threats, indent=2))
    (DOCS / "timeline.json").write_text(json.dumps(timeline, indent=2))
    (DATA / "refs_dss_debug.json").write_text(json.dumps(debug, indent=2))
    (DATA / "refs_hourly.json").write_text(json.dumps({"hours": hourly}, indent=2))

    print("Wrote docs/threats.json")
    print("Wrote docs/timeline.json")
    print("Wrote data/refs_dss_debug.json")
    print("Wrote data/refs_hourly.json")


if __name__ == "__main__":
    main()
