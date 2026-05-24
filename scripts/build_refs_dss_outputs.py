from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
import xarray as xr


# =============================================================================
# KRNO / DSS CONFIG
# =============================================================================

DOCS = Path("docs")
DATA = Path("data")
DOCS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

SITE = os.getenv("DSS_SITE", "KRNO")
SITE_NAME = os.getenv("DSS_SITE_NAME", "KRNO Ops")
LAT = float(os.getenv("DSS_LAT", "39.4991"))
LON = float(os.getenv("DSS_LON", "-119.7681"))

SELECTED_CYCLE_PATH = DATA / "rrfs_refs_selected_cycle.json"

S3_HTTP_BASE = "https://noaa-rrfs-pds.s3.amazonaws.com"

FORECAST_HOURS = list(range(1, 61))
BLOCK_HOURS = 3
BLOCK_COUNT = 20

MPS_TO_MPH = 2.2369362920544
MPS_TO_KT = 1.9438444924406
M_TO_SM = 0.00062137119223733
M_TO_IN = 39.37007874015748
K_TO_F = 9.0 / 5.0
C_TO_F = 9.0 / 5.0


# =============================================================================
# THRESHOLDS
# =============================================================================

# Probability categories:
# 0 = None
# 1 = Little to None
# 2 = Minor
# 3 = Moderate
# 4 = Major
# 5 = Extreme

RISK_MATRIX = {
    1: {1: 1, 2: 1, 3: 1, 4: 2, 5: 2},
    2: {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
    3: {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
    4: {1: 1, 2: 2, 3: 3, 4: 4, 5: 4},
    5: {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
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

HAZARD_DISPLAY = {
    "WIND": "Wind",
    "LIGHTNING": "Lightning",
    "SNOW": "Snow",
    "VISIBILITY": "Visibility",
    "FZRA": "Freezing Rain",
    "FLASH_FREEZE": "Flash Freeze",
    "RAIN": "Rain",
    "TEMPERATURE": "Temperature",
}

# These are the impact thresholds. Probability comes from REFS probability fields
# when available. If probability fields are missing, the script falls back to
# mean-field deterministic screening with low-confidence probability labels.
THRESHOLDS = {
    "WIND": [
        {"key": "gt_30_mph", "threshold": 30.0, "unit": "mph", "impact": 2, "label": ">30 mph"},
        {"key": "gt_45_mph", "threshold": 45.0, "unit": "mph", "impact": 3, "label": ">45 mph"},
        {"key": "gt_58_mph", "threshold": 58.0, "unit": "mph", "impact": 4, "label": ">58 mph"},
        {"key": "gt_65_mph", "threshold": 65.0, "unit": "mph", "impact": 5, "label": ">65 mph"},
    ],
    "LIGHTNING": [
        {"key": "gt_5_pct", "threshold": 5.0, "unit": "pct", "impact": 2, "label": "Lightning chance: 5-25%"},
        {"key": "gt_25_pct", "threshold": 25.0, "unit": "pct", "impact": 3, "label": "Lightning chance: 25-50%"},
        {"key": "gt_50_pct", "threshold": 50.0, "unit": "pct", "impact": 4, "label": "Lightning chance: 50-75%"},
        {"key": "gt_75_pct", "threshold": 75.0, "unit": "pct", "impact": 5, "label": "Lightning chance: >75%"},
    ],
    "SNOW": [
        {"key": "gt_0p10_in_hr", "threshold": 0.10, "unit": "in", "impact": 2, "label": ">0.10 in/hr"},
        {"key": "gt_0p50_in_hr", "threshold": 0.50, "unit": "in", "impact": 3, "label": ">0.50 in/hr"},
        {"key": "gt_1p00_in_hr", "threshold": 1.00, "unit": "in", "impact": 4, "label": ">1.00 in/hr"},
        {"key": "gt_2p00_in_hr", "threshold": 2.00, "unit": "in", "impact": 5, "label": ">2.00 in/hr"},
    ],
    "VISIBILITY": [
        {"key": "lt_5_sm", "threshold": 5.0, "unit": "sm", "impact": 2, "label": "<5 SM"},
        {"key": "lt_3_sm", "threshold": 3.0, "unit": "sm", "impact": 3, "label": "<3 SM"},
        {"key": "lt_1_sm", "threshold": 1.0, "unit": "sm", "impact": 4, "label": "<1 SM"},
        {"key": "lt_0p5_sm", "threshold": 0.5, "unit": "sm", "impact": 5, "label": "<0.5 SM"},
    ],
    "FZRA": [
        {"key": "gt_5_pct", "threshold": 5.0, "unit": "pct", "impact": 2, "label": "FZRA chance: 5-25%"},
        {"key": "gt_25_pct", "threshold": 25.0, "unit": "pct", "impact": 3, "label": "FZRA chance: 25-50%"},
        {"key": "gt_50_pct", "threshold": 50.0, "unit": "pct", "impact": 4, "label": "FZRA chance: 50-75%"},
        {"key": "gt_75_pct", "threshold": 75.0, "unit": "pct", "impact": 5, "label": "FZRA chance: >75%"},
    ],
    "FLASH_FREEZE": [
        {"key": "joint_ff_5", "threshold": 5.0, "unit": "pct", "impact": 2, "label": "Flash freeze signal: 5-25%"},
        {"key": "joint_ff_25", "threshold": 25.0, "unit": "pct", "impact": 3, "label": "Flash freeze signal: 25-50%"},
        {"key": "joint_ff_50", "threshold": 50.0, "unit": "pct", "impact": 4, "label": "Flash freeze signal: 50-75%"},
        {"key": "joint_ff_75", "threshold": 75.0, "unit": "pct", "impact": 5, "label": "Flash freeze signal: >75%"},
    ],
    "RAIN": [
        {"key": "gt_0p10_in_hr", "threshold": 0.10, "unit": "in", "impact": 2, "label": ">0.10 in/hr"},
        {"key": "gt_0p25_in_hr", "threshold": 0.25, "unit": "in", "impact": 3, "label": ">0.25 in/hr"},
        {"key": "gt_0p50_in_hr", "threshold": 0.50, "unit": "in", "impact": 4, "label": ">0.50 in/hr"},
        {"key": "gt_0p80_in_hr", "threshold": 0.80, "unit": "in", "impact": 5, "label": ">0.80 in/hr"},
    ],
    "TEMPERATURE": [
        {"key": "lt_32_f", "threshold": 32.0, "unit": "f", "impact": 2, "label": "<32°F"},
        {"key": "lt_20_f", "threshold": 20.0, "unit": "f", "impact": 3, "label": "<20°F"},
        {"key": "gt_95_f", "threshold": 95.0, "unit": "f", "impact": 3, "label": ">95°F"},
        {"key": "gt_105_f", "threshold": 105.0, "unit": "f", "impact": 4, "label": ">105°F"},
    ],
}


# =============================================================================
# BASIC UTILITIES
# =============================================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def risk_label(risk: int) -> str:
    return {
        0: "None",
        1: "Little to None",
        2: "Minor",
        3: "Moderate",
        4: "Major",
        5: "Extreme",
    }.get(int(risk), "Unknown")


def probability_to_likelihood(probability: float | None) -> int:
    if probability is None:
        return 1
    if probability >= 90:
        return 5
    if probability >= 66:
        return 4
    if probability >= 33:
        return 3
    if probability >= 10:
        return 2
    return 1


def matrix_risk(probability: float | None, impact_level: int) -> int:
    if probability is None or probability <= 0:
        return 0

    likelihood = probability_to_likelihood(probability)
    impact = max(1, min(5, int(impact_level)))
    return RISK_MATRIX[likelihood][impact]


def parse_cycle_string(value: str | None) -> datetime | None:
    if not value:
        return None

    value = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    for fmt in ("%Y%m%d%H", "%Y-%m-%d %H", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue

    return None


def s3_to_http(key: str) -> str:
    key = key.lstrip("/")
    if key.startswith("http://") or key.startswith("https://"):
        return key
    return f"{S3_HTTP_BASE}/{key}"


def fetch_text(url: str, timeout: int = 90) -> str:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def normalize_lon(lon: float) -> float:
    return lon + 360.0 if lon < 0 else lon


# =============================================================================
# INVENTORY HANDLING
# =============================================================================

@dataclass
class RefsFile:
    key: str
    product: str
    fxx: int
    kind: str


def load_selected_cycle() -> dict[str, Any]:
    if not SELECTED_CYCLE_PATH.exists():
        raise FileNotFoundError(
            "Missing data/rrfs_refs_selected_cycle.json. "
            "Run scripts/scan_rrfs_refs_inventory.py first."
        )

    payload = json.loads(SELECTED_CYCLE_PATH.read_text())

    # scan_rrfs_refs_inventory.py writes a compact wrapper shaped like:
    # {
    #   "generated_utc": "...",
    #   "bucket": "https://noaa-rrfs-pds.s3.amazonaws.com",
    #   "selected_cycle": { ... actual cycle metadata/files ... }
    # }
    # Older builder versions expected the selected cycle fields at top level.
    # Normalize here so the rest of this builder receives one consistent shape.
    if isinstance(payload, dict) and isinstance(payload.get("selected_cycle"), dict):
        selected = dict(payload["selected_cycle"])
        selected["_wrapper_generated_utc"] = payload.get("generated_utc")
        selected["_wrapper_bucket"] = payload.get("bucket")
        return selected

    return payload


def infer_cycle_dt(selected: dict[str, Any]) -> datetime:
    for field in ("cycle_utc", "cycle", "cycle_label", "date", "run", "runtime"):
        dt = parse_cycle_string(selected.get(field))
        if dt:
            return dt

    # Common cycle label format from scanner: "2026-05-22 00Z".
    label = str(selected.get("cycle_label", "") or selected.get("cycle", ""))
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2})Z", label)
    if match:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            tzinfo=timezone.utc,
        )

    # S3 prefix format: rrfs_a/refs.20260522/00/enspost/
    prefix = str(selected.get("prefix", "") or selected.get("s3_prefix", ""))
    match = re.search(r"refs\.(\d{8})/(\d{2})", prefix)
    if match:
        return datetime.strptime(
            match.group(1) + match.group(2),
            "%Y%m%d%H",
        ).replace(tzinfo=timezone.utc)

    # Last fallback: inspect the file keys themselves.
    for item in flatten_selected_items(selected):
        if isinstance(item, dict):
            key = str(item.get("key") or item.get("name") or item.get("path") or "")
        else:
            key = str(item)

        match = re.search(r"refs\.(\d{8})/(\d{2})", key)
        if match:
            return datetime.strptime(
                match.group(1) + match.group(2),
                "%Y%m%d%H",
            ).replace(tzinfo=timezone.utc)

    raise RuntimeError(
        "Could not infer REFS cycle time from data/rrfs_refs_selected_cycle.json. "
        f"Top-level keys after normalization: {list(selected.keys())}"
    )


def flatten_selected_items(selected: dict[str, Any]) -> list[Any]:
    for field in ("keys", "files", "parsed_objects", "objects"):
        value = selected.get(field)
        if isinstance(value, list) and value:
            return value
    return []


def parse_product_from_key(key: str) -> str:
    lower = key.lower()

    # Common REFS product names.
    for product in ("mean", "prob", "avrg", "sprd", "lpmm", "pmmn", "ffri", "eas"):
        if f".{product}." in lower or lower.endswith(f".{product}.grib2") or f"/{product}/" in lower:
            return product

    # Fallback: second-to-last token before .grib2/.idx often contains product.
    parts = lower.split(".")
    for part in parts:
        if part in ("mean", "prob", "avrg", "sprd", "lpmm", "pmmn", "ffri", "eas"):
            return part

    return "unknown"


def parse_fxx_from_key(key: str) -> int | None:
    lower = key.lower()
    patterns = [
        r"\.f(\d{3})\.",
        r"f(\d{3})",
        r"fcst(\d{3})",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            return int(match.group(1))
    return None


def parse_kind_from_key(key: str) -> str:
    lower = key.lower()
    if lower.endswith(".idx"):
        return "idx"
    if lower.endswith(".grib2") or lower.endswith(".grb2"):
        return "grib"
    return "other"


def build_file_index(selected: dict[str, Any]) -> dict[tuple[str, int, str], RefsFile]:
    items = flatten_selected_items(selected)
    file_index: dict[tuple[str, int, str], RefsFile] = {}

    for item in items:
        if isinstance(item, str):
            key = item
        elif isinstance(item, dict):
            key = str(item.get("key") or item.get("name") or item.get("path") or "")
        else:
            continue

        if not key:
            continue

        kind = parse_kind_from_key(key)
        if kind not in ("idx", "grib"):
            continue

        fxx = parse_fxx_from_key(key)
        if fxx is None:
            continue

        product = parse_product_from_key(key)
        file_index[(product, fxx, kind)] = RefsFile(
            key=key,
            product=product,
            fxx=fxx,
            kind=kind,
        )

    return file_index


def get_file(file_index: dict[tuple[str, int, str], RefsFile], product: str, fxx: int, kind: str) -> RefsFile | None:
    direct = file_index.get((product, fxx, kind))
    if direct:
        return direct

    # fallback for product naming weirdness
    for (p, h, k), item in file_index.items():
        if h == fxx and k == kind and product in p:
            return item

    return None


# =============================================================================
# IDX / GRIB EXTRACTION
# =============================================================================

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

    response = requests.get(grib_url, headers=headers, timeout=120)
    response.raise_for_status()

    content = response.content
    if len(content) < 100:
        raise RuntimeError(f"GRIB byte-range download too small: {len(content)} bytes")

    path.write_bytes(content)


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

    if not lat_name or not lon_name:
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

    target_lon_360 = normalize_lon(LON)

    if lat.ndim == 1 and lon.ndim == 1:
        lat_idx = int(abs(lat - LAT).argmin())
        lon_target = target_lon_360 if float(lon.max()) > 180 else LON
        lon_idx = int(abs(lon - lon_target).argmin())
        value = ds[var_name].isel({lat_name: lat_idx, lon_name: lon_idx}).values
    else:
        lon_values = lon.values
        lat_values = lat.values

        lon_target = target_lon_360 if float(lon_values.max()) > 180 else LON
        dist2 = (lat_values - LAT) ** 2 + (lon_values - lon_target) ** 2

        flat_index = int(dist2.argmin())
        iy, ix = [int(v) for v in divmod(flat_index, dist2.shape[1])]

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


def extract_value_from_grib_message(grib_url: str, row: dict[str, Any], label: str) -> tuple[str, float]:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / f"{label}.grib2"
        download_grib_message(grib_url, row, path)

        ds = xr.open_dataset(
            path,
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


def read_idx_rows(file_index: dict[tuple[str, int, str], RefsFile], product: str, fxx: int) -> tuple[list[dict[str, Any]], str] | tuple[None, None]:
    idx_file = get_file(file_index, product, fxx, "idx")
    if not idx_file:
        return None, None

    idx_url = s3_to_http(idx_file.key)
    try:
        return parse_idx(fetch_text(idx_url)), idx_url
    except Exception:
        return None, None


def grib_url_for(file_index: dict[tuple[str, int, str], RefsFile], product: str, fxx: int) -> str | None:
    grib_file = get_file(file_index, product, fxx, "grib")
    if not grib_file:
        return None
    return s3_to_http(grib_file.key)


# =============================================================================
# FIELD MATCHING
# =============================================================================

def row_contains(row: dict[str, Any], includes: list[str], excludes: list[str] | None = None) -> bool:
    line = row["line"].upper()
    excludes = excludes or []

    for item in includes:
        if item.upper() not in line:
            return False

    for item in excludes:
        if item.upper() in line:
            return False

    return True


def find_first_row(rows: list[dict[str, Any]], includes: list[str], excludes: list[str] | None = None) -> dict[str, Any] | None:
    for row in rows:
        if row_contains(row, includes, excludes):
            return row
    return None


def normalize_probability(value: float) -> float:
    # Many probability fields are 0-100; some are 0-1.
    if value <= 1.0:
        return round(value * 100.0, 1)
    return round(value, 1)


def value_for_mean_field(
    file_index: dict[tuple[str, int, str], RefsFile],
    fxx: int,
    includes: list[str],
    excludes: list[str] | None = None,
    product: str = "mean",
) -> dict[str, Any]:
    rows, idx_url = read_idx_rows(file_index, product, fxx)
    grib_url = grib_url_for(file_index, product, fxx)

    if not rows or not grib_url:
        return {
            "status": "missing_file",
            "value": None,
            "idx_line": None,
            "idx_url": idx_url,
            "grib_url": grib_url,
        }

    row = find_first_row(rows, includes, excludes)
    if row is None:
        return {
            "status": "missing_row",
            "value": None,
            "idx_line": None,
            "idx_url": idx_url,
            "grib_url": grib_url,
        }

    try:
        var_name, value = extract_value_from_grib_message(
            grib_url,
            row,
            label=f"{product}_f{fxx:03d}_{'_'.join(includes)}",
        )
        return {
            "status": "ok",
            "variable": var_name,
            "value": value,
            "idx_line": row["line"],
            "idx_url": idx_url,
            "grib_url": grib_url,
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
            "value": None,
            "idx_line": row["line"],
            "idx_url": idx_url,
            "grib_url": grib_url,
        }


def probability_field_value(
    file_index: dict[tuple[str, int, str], RefsFile],
    fxx: int,
    includes: list[str],
    excludes: list[str] | None = None,
) -> dict[str, Any]:
    rows, idx_url = read_idx_rows(file_index, "prob", fxx)
    grib_url = grib_url_for(file_index, "prob", fxx)

    if not rows or not grib_url:
        return {
            "status": "missing_file",
            "probability": None,
            "idx_line": None,
        }

    row = find_first_row(rows, includes, excludes)
    if row is None:
        return {
            "status": "missing_row",
            "probability": None,
            "idx_line": None,
        }

    try:
        var_name, value = extract_value_from_grib_message(
            grib_url,
            row,
            label=f"prob_f{fxx:03d}_{'_'.join(includes)}",
        )
        return {
            "status": "ok",
            "variable": var_name,
            "probability": normalize_probability(value),
            "raw_value": value,
            "idx_line": row["line"],
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
            "probability": None,
            "idx_line": row["line"],
        }


# =============================================================================
# HAZARD EXTRACTION
# =============================================================================

def convert_mean_value(hazard: str, raw_value: float | None, field: str) -> float | None:
    if raw_value is None:
        return None

    if hazard == "WIND":
        # REFS WIND field is assumed m/s.
        return round(raw_value * MPS_TO_MPH, 1)

    if hazard == "VISIBILITY":
        # VIS is usually meters.
        return round(raw_value * M_TO_SM, 2)

    if hazard == "SNOW":
        # ASNOW is meters water/snow depending field. Field map suggests ASNOW accumulation.
        return round(raw_value * M_TO_IN, 3)

    if hazard == "RAIN":
        # APCP is meters.
        return round(raw_value * M_TO_IN, 3)

    if hazard == "TEMPERATURE":
        # TMP is Kelvin.
        return round((raw_value - 273.15) * K_TO_F + 32.0, 1)

    if hazard in ("FZRA", "LIGHTNING", "FLASH_FREEZE"):
        return round(raw_value, 3)

    return round(raw_value, 3)


def mean_field_spec(hazard: str) -> tuple[str, list[str], list[str]]:
    if hazard == "WIND":
        return "mean", ["WIND", "10 m above ground"], []
    if hazard == "VISIBILITY":
        return "mean", ["VIS", "surface"], []
    if hazard == "SNOW":
        return "mean", ["ASNOW", "surface"], []
    if hazard == "RAIN":
        return "avrg", ["APCP", "surface"], []
    if hazard == "TEMPERATURE":
        return "mean", ["TMP", "2 m above ground"], []
    if hazard == "FZRA":
        return "mean", ["CFRZR", "surface"], []
    if hazard == "LIGHTNING":
        return "prob", ["LTNG"], []
    return "mean", ["TMP", "2 m above ground"], []


def probability_spec_for_threshold(hazard: str, threshold: dict[str, Any]) -> tuple[list[str], list[str]]:
    label = threshold["label"]
    threshold_value = threshold["threshold"]

    if hazard == "WIND":
        # Match probability WIND fields. If thresholds are encoded in idx text,
        # these broad terms usually find the closest official probability record.
        return ["WIND", "10 m above ground", "prob"], []

    if hazard == "VISIBILITY":
        return ["VIS", "surface", "prob"], []

    if hazard == "SNOW":
        return ["ASNOW", "surface", "prob"], []

    if hazard == "RAIN":
        return ["APCP", "surface", "prob"], []

    if hazard == "FZRA":
        return ["FRZR", "prob"], []

    if hazard == "LIGHTNING":
        return ["LTNG"], []

    if hazard == "TEMPERATURE":
        if ">" in label:
            return ["TMP", "2 m above ground", "prob"], []
        return ["TMP", "2 m above ground", "prob"], []

    if hazard == "FLASH_FREEZE":
        return ["TMP", "2 m above ground", "prob"], []

    return [], []


def deterministic_probability_from_value(hazard: str, threshold: dict[str, Any], value: float | None) -> float:
    """
    Fallback only. This is intentionally conservative.
    If REFS probability fields are missing or not matched, use deterministic
    mean value to avoid empty cards. This should be replaced by exact probability
    fields once field-map names are fully locked down.
    """
    if value is None:
        return 0.0

    threshold_value = float(threshold["threshold"])

    if hazard in ("WIND", "RAIN", "SNOW"):
        return 100.0 if value >= threshold_value else 0.0

    if hazard == "VISIBILITY":
        return 100.0 if value <= threshold_value else 0.0

    if hazard == "TEMPERATURE":
        key = threshold["key"]
        if key.startswith("gt_"):
            return 100.0 if value >= threshold_value else 0.0
        return 100.0 if value <= threshold_value else 0.0

    if hazard in ("FZRA", "LIGHTNING"):
        # CFRZR/LTNG mean-like fields may already be probability-ish.
        if value > 1:
            return min(100.0, max(0.0, value))
        return min(100.0, max(0.0, value * 100.0))

    return 0.0


def evaluate_thresholds(hazard: str, fxx: int, mean_value: float | None, file_index: dict[tuple[str, int, str], RefsFile]) -> list[dict[str, Any]]:
    candidates = []

    for threshold in THRESHOLDS[hazard]:
        prob_includes, prob_excludes = probability_spec_for_threshold(hazard, threshold)
        prob_result = {"status": "not_attempted", "probability": None, "idx_line": None}

        if prob_includes:
            prob_result = probability_field_value(
                file_index=file_index,
                fxx=fxx,
                includes=prob_includes,
                excludes=prob_excludes,
            )

        probability = prob_result.get("probability")

        source = "refs_probability"
        if probability is None:
            probability = deterministic_probability_from_value(hazard, threshold, mean_value)
            source = "deterministic_mean_fallback"

        impact = int(threshold["impact"])
        risk = matrix_risk(float(probability), impact)

        candidates.append(
            {
                "threshold_key": threshold["key"],
                "threshold": threshold["threshold"],
                "unit": threshold["unit"],
                "impact_level": impact,
                "probability": round(float(probability), 1),
                "risk": risk,
                "risk_label": risk_label(risk),
                "label": threshold["label"],
                "source": source,
                "probability_status": prob_result.get("status"),
                "probability_idx_line": prob_result.get("idx_line"),
                "fxx": fxx,
            }
        )

    return candidates


def best_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {
            "threshold_key": "none",
            "impact_level": 0,
            "probability": 0.0,
            "risk": 0,
            "risk_label": "None",
            "label": "No data",
            "fxx": None,
        }

    return max(candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))


def extract_hourly_hazard(
    hazard: str,
    file_index: dict[tuple[str, int, str], RefsFile],
    cycle_dt: datetime,
) -> list[dict[str, Any]]:
    hourly = []

    product, includes, excludes = mean_field_spec(hazard)

    for fxx in FORECAST_HOURS:
        valid_utc = (cycle_dt + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")

        mean_result = value_for_mean_field(
            file_index=file_index,
            fxx=fxx,
            includes=includes,
            excludes=excludes,
            product=product,
        )

        raw_value = mean_result.get("value")
        converted_value = convert_mean_value(hazard, raw_value, includes[0] if includes else "")

        candidates = evaluate_thresholds(
            hazard=hazard,
            fxx=fxx,
            mean_value=converted_value,
            file_index=file_index,
        )

        best = best_candidate(candidates)

        hourly.append(
            {
                "fxx": fxx,
                "valid_utc": valid_utc,
                "hazard": hazard,
                "mean_status": mean_result.get("status"),
                "mean_raw_value": raw_value,
                "mean_value": converted_value,
                "mean_idx_line": mean_result.get("idx_line"),
                "candidates": candidates,
                "best": best,
            }
        )

    return hourly


def compute_flash_freeze_hourly(
    file_index: dict[tuple[str, int, str], RefsFile],
    cycle_dt: datetime,
    rain_hourly: list[dict[str, Any]],
    snow_hourly: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hourly = []

    for fxx in FORECAST_HOURS:
        valid_utc = (cycle_dt + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")

        tmp = value_for_mean_field(file_index, fxx, ["TMP", "2 m above ground"], [], "mean")
        dpt = value_for_mean_field(file_index, fxx, ["DPT", "2 m above ground"], [], "mean")

        tmp_f = convert_mean_value("TEMPERATURE", tmp.get("value"), "TMP")
        dpt_f = convert_mean_value("TEMPERATURE", dpt.get("value"), "DPT")

        # Approximate wet bulb using a simple low-level approximation.
        # Good enough as first pass for screening; can be replaced later.
        wet_bulb_f = None
        if tmp_f is not None and dpt_f is not None:
            wet_bulb_f = round(tmp_f - ((tmp_f - dpt_f) / 3.0), 1)

        rain_prob = 0.0
        snow_prob = 0.0

        if fxx - 1 < len(rain_hourly):
            rain_prob = float(rain_hourly[fxx - 1]["best"].get("probability", 0.0))
        if fxx - 1 < len(snow_hourly):
            snow_prob = float(snow_hourly[fxx - 1]["best"].get("probability", 0.0))

        wet_prob = max(rain_prob, snow_prob)

        if wet_bulb_f is None:
            freeze_prob = 0.0
        elif wet_bulb_f <= 30:
            freeze_prob = 100.0
        elif wet_bulb_f <= 32:
            freeze_prob = 50.0
        else:
            freeze_prob = 0.0

        joint_prob = round((wet_prob / 100.0) * (freeze_prob / 100.0) * 100.0, 1)

        candidates = []
        for threshold in THRESHOLDS["FLASH_FREEZE"]:
            probability = joint_prob if joint_prob >= threshold["threshold"] else 0.0
            risk = matrix_risk(probability, int(threshold["impact"]))
            candidates.append(
                {
                    "threshold_key": threshold["key"],
                    "threshold": threshold["threshold"],
                    "unit": threshold["unit"],
                    "impact_level": threshold["impact"],
                    "probability": probability,
                    "risk": risk,
                    "risk_label": risk_label(risk),
                    "label": threshold["label"],
                    "source": "joint_wet_bulb_precip_probability",
                    "fxx": fxx,
                }
            )

        best = best_candidate(candidates)

        hourly.append(
            {
                "fxx": fxx,
                "valid_utc": valid_utc,
                "hazard": "FLASH_FREEZE",
                "wet_bulb_f": wet_bulb_f,
                "wet_prob": wet_prob,
                "freeze_prob": freeze_prob,
                "joint_probability": joint_prob,
                "candidates": candidates,
                "best": best,
            }
        )

    return hourly


# =============================================================================
# OUTPUT BUILDING
# =============================================================================

def no_data_payload(hazard: str) -> dict[str, Any]:
    return {
        "prob": 0.0,
        "risk": 0,
        "risk_label": "None",
        "level": 0,
        "metric": "No data",
        "display_label": HAZARD_DISPLAY[hazard],
        "display_value": "No data",
        "window": "60 hr",
        "peak_start_fxx": None,
        "peak_end_fxx": None,
        "driver": "No REFS data matched",
    }


def format_display_value(hazard: str, value: float | None, best: dict[str, Any]) -> tuple[str, str]:
    if value is None:
        return HAZARD_DISPLAY[hazard], "No data"

    if hazard == "WIND":
        return "60-hr max wind", f"{value:.0f} mph"

    if hazard == "RAIN":
        return "1-hr rain", f'{value:.2f}"'

    if hazard == "SNOW":
        if value <= 0:
            return "1-hr snow", '0"'
        if value < 0.1:
            return "1-hr snow", "Trace"
        return "1-hr snow", f'{value:.2f}"'

    if hazard == "VISIBILITY":
        return "Min visibility", f"{value:.1f} SM"

    if hazard == "TEMPERATURE":
        return "Temperature", f"{value:.0f}°F"

    if hazard == "FZRA":
        return "Freezing rain", f"{best.get('probability', 0.0):.0f}%"

    if hazard == "LIGHTNING":
        return "Lightning", f"{best.get('probability', 0.0):.0f}%"

    if hazard == "FLASH_FREEZE":
        return "Flash freeze", f"{best.get('probability', 0.0):.0f}%"

    return HAZARD_DISPLAY[hazard], str(value)


def choose_card_hour(hazard: str, hourly: list[dict[str, Any]]) -> dict[str, Any]:
    if not hourly:
        return {}

    if hazard == "VISIBILITY":
        # Card magnitude should emphasize minimum visibility, but risk still comes
        # from probability/impact candidate.
        return min(hourly, key=lambda h: h["mean_value"] if h.get("mean_value") is not None else 999999)

    # Default: highest risk, then probability, then mean magnitude.
    return max(
        hourly,
        key=lambda h: (
            h["best"].get("risk", 0),
            h["best"].get("probability", 0.0),
            h.get("mean_value") if h.get("mean_value") is not None else -999999,
        ),
    )


def build_card_payload(hazard: str, hourly: list[dict[str, Any]]) -> dict[str, Any]:
    if not hourly:
        return no_data_payload(hazard)

    card_hour = choose_card_hour(hazard, hourly)
    best = card_hour.get("best", {})
    risk = int(best.get("risk", 0))
    impact = int(best.get("impact_level", 0))
    prob = round(float(best.get("probability", 0.0)), 1)

    display_label, display_value = format_display_value(
        hazard,
        card_hour.get("mean_value") if hazard != "FLASH_FREEZE" else card_hour.get("wet_bulb_f"),
        best,
    )

    fxx = card_hour.get("fxx")
    if fxx is None:
        peak_start = None
        peak_end = None
    else:
        peak_start = max(1, int(fxx) - 1)
        peak_end = min(60, int(fxx) + 1)

    return {
        "prob": prob,
        "risk": risk,
        "risk_label": risk_label(risk),
        "level": impact,
        "metric": best.get("label", "No signal") if risk > 0 else "No signal",
        "display_label": display_label,
        "display_value": display_value,
        "window": "60 hr",
        "peak_start_fxx": peak_start,
        "peak_end_fxx": peak_end,
        "source_fxx": fxx,
        "peak_valid_utc": card_hour.get("valid_utc"),
        "driver": (
            f"{prob:.1f}% / {best.get('label', 'No signal')}"
            if risk > 0
            else "No meaningful signal"
        ),
        "methodology": (
            "REFS 60-hour DSS risk card. Card selection uses the highest risk/probability "
            "hour in the 60-hour period. Timeline blocks use the highest-risk 1-hour value "
            "within each 3-hour block."
        ),
    }


def build_block_payload(hazard: str, block_hours: list[dict[str, Any]]) -> dict[str, Any]:
    if not block_hours:
        return {
            "prob": 0.0,
            "risk": 0,
            "level": 0,
            "metric": "No data",
            "driver": "No data in block",
            "source_fxx": None,
            "peak_valid_utc": None,
        }

    best_hour = max(
        block_hours,
        key=lambda h: (
            h["best"].get("risk", 0),
            h["best"].get("probability", 0.0),
            h.get("mean_value") if h.get("mean_value") is not None else -999999,
        ),
    )

    best = best_hour["best"]
    risk = int(best.get("risk", 0))
    prob = round(float(best.get("probability", 0.0)), 1)

    return {
        "prob": prob,
        "risk": risk,
        "risk_label": risk_label(risk),
        "level": int(best.get("impact_level", 0)),
        "metric": best.get("label", "No signal") if risk > 0 else "No signal",
        "driver": (
            f"{prob:.1f}% / {best.get('label', 'No signal')}"
            if risk > 0
            else "No meaningful signal"
        ),
        "source_fxx": best_hour.get("fxx"),
        "peak_valid_utc": best_hour.get("valid_utc"),
        "mean_value": best_hour.get("mean_value"),
    }


def build_outputs(
    cycle_dt: datetime,
    selected: dict[str, Any],
    all_hourly: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    generated = utc_now()

    threats: dict[str, Any] = {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": LAT,
        "lon": LON,
        "source": "NOAA RRFS / REFS via AWS S3",
        "generated_utc": generated,
        "cycle_utc_iso": cycle_dt.isoformat().replace("+00:00", "Z"),
        "cycle": f"REFS {cycle_dt.strftime('%HZ')}",
        "valid_period": "next_60_hours",
        "threats": {},
        "hazards": [],
        "methodology": (
            "REFS-only DSS backend. Risk cards use the 60-hour hazard period. "
            "Timeline uses 20 three-hour blocks; each block is assigned the highest-risk "
            "one-hour value inside that block."
        ),
    }

    for hazard in HAZARD_ORDER:
        payload = build_card_payload(hazard, all_hourly.get(hazard, []))
        threats["threats"][hazard] = payload

        threats["hazards"].append(
            {
                "id": hazard,
                "name": HAZARD_DISPLAY[hazard],
                "risk_level": payload["risk"],
                "risk_label": payload["risk_label"],
                "impact_level": payload["level"],
                "probability": payload["prob"],
                "peak_start_fxx": payload["peak_start_fxx"],
                "peak_end_fxx": payload["peak_end_fxx"],
                "metric": payload["metric"],
                "display_label": payload["display_label"],
                "display_value": payload["display_value"],
                "driver": payload["driver"],
            }
        )

    # Sort cards highest to lowest risk, then probability.
    threats["hazards"] = sorted(
        threats["hazards"],
        key=lambda h: (h["risk_level"], h["probability"], h["impact_level"]),
        reverse=True,
    )

    timeline: dict[str, Any] = {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": LAT,
        "lon": LON,
        "source": "NOAA RRFS / REFS via AWS S3",
        "generated_utc": generated,
        "cycle_utc_iso": cycle_dt.isoformat().replace("+00:00", "Z"),
        "cycle": f"REFS {cycle_dt.strftime('%HZ')}",
        "valid_period": "next_60_hours",
        "block_hours": BLOCK_HOURS,
        "blocks": [],
        "block_hazards": [],
    }

    for block_index in range(BLOCK_COUNT):
        start_fxx = block_index * BLOCK_HOURS + 1
        end_fxx = start_fxx + BLOCK_HOURS - 1
        start_valid = cycle_dt + timedelta(hours=start_fxx)
        end_valid = cycle_dt + timedelta(hours=end_fxx)

        block = {
            "block_index": block_index,
            "start_fxx": start_fxx,
            "end_fxx": end_fxx,
            "valid_start_utc": start_valid.isoformat().replace("+00:00", "Z"),
            "valid_end_utc": end_valid.isoformat().replace("+00:00", "Z"),
        }

        hazard_block: dict[str, Any] = {
            "block_index": block_index,
            "start_fxx": start_fxx,
            "end_fxx": end_fxx,
            "valid_start_utc": start_valid.isoformat().replace("+00:00", "Z"),
            "valid_end_utc": end_valid.isoformat().replace("+00:00", "Z"),
        }

        for hazard in HAZARD_ORDER:
            hourly = all_hourly.get(hazard, [])
            block_hours = [
                h for h in hourly
                if start_fxx <= int(h.get("fxx", -999)) <= end_fxx
            ]
            block_eval = build_block_payload(hazard, block_hours)
            hazard_block[hazard] = block_eval
            block[hazard] = block_eval.get("risk", 0)

        timeline["blocks"].append(block)
        timeline["block_hazards"].append(hazard_block)

    return threats, timeline


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("Building REFS DSS outputs")

    selected = load_selected_cycle()
    cycle_dt = infer_cycle_dt(selected)
    file_index = build_file_index(selected)

    print(f"Selected cycle: {cycle_dt:%Y-%m-%d %HZ}")
    print(f"Indexed files: {len(file_index)}")

    if not file_index:
        raise RuntimeError("No REFS files parsed from data/rrfs_refs_selected_cycle.json")

    all_hourly: dict[str, list[dict[str, Any]]] = {}

    # Extract independent hazards first.
    for hazard in ["WIND", "LIGHTNING", "SNOW", "VISIBILITY", "FZRA", "RAIN", "TEMPERATURE"]:
        print(f"Extracting {hazard}")
        all_hourly[hazard] = extract_hourly_hazard(hazard, file_index, cycle_dt)

    print("Computing FLASH_FREEZE joint probability")
    all_hourly["FLASH_FREEZE"] = compute_flash_freeze_hourly(
        file_index=file_index,
        cycle_dt=cycle_dt,
        rain_hourly=all_hourly.get("RAIN", []),
        snow_hourly=all_hourly.get("SNOW", []),
    )

    threats, timeline = build_outputs(cycle_dt, selected, all_hourly)

    (DOCS / "threats.json").write_text(json.dumps(threats, indent=2))
    (DOCS / "timeline.json").write_text(json.dumps(timeline, indent=2))

    # Keep compact diagnostics only.
    compact = {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": LAT,
        "lon": LON,
        "source": "NOAA RRFS / REFS via AWS S3",
        "generated_utc": utc_now(),
        "cycle_utc_iso": cycle_dt.isoformat().replace("+00:00", "Z"),
        "hazards": {
            hazard: {
                "hours": len(hours),
                "ok_mean_hours": sum(1 for h in hours if h.get("mean_status") == "ok"),
                "max_risk": max((h["best"].get("risk", 0) for h in hours), default=0),
                "max_probability": max((h["best"].get("probability", 0.0) for h in hours), default=0.0),
            }
            for hazard, hours in all_hourly.items()
        },
    }

    (DATA / "refs_dss_summary.json").write_text(json.dumps(compact, indent=2))

    print("Wrote docs/threats.json")
    print("Wrote docs/timeline.json")
    print("Wrote data/refs_dss_summary.json")


if __name__ == "__main__":
    main()
