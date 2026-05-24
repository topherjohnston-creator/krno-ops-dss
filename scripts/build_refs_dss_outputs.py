from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


DOCS = Path("docs")
DATA = Path("data")
DOCS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

SITE = os.getenv("DSS_SITE", "KRNO")
SITE_NAME = os.getenv("DSS_SITE_NAME", "KRNO Ops")
LAT = float(os.getenv("DSS_LAT", "39.4991"))
LON = float(os.getenv("DSS_LON", "-119.7681"))

SELECTED_CYCLE_PATH = DATA / "rrfs_refs_selected_cycle.json"
FIELD_MAP_SUMMARY_PATH = DATA / "refs_field_map_summary.json"
BUILDER_SUMMARY_PATH = DATA / "refs_builder_summary.json"
S3_HTTP_BASE = "https://noaa-rrfs-pds.s3.amazonaws.com"

FORECAST_HOURS = list(range(1, 61))
BLOCK_HOURS = 3
BLOCK_COUNT = 20
MPS_TO_MPH = 2.2369362920544
EXTRACT_WIND_PROBABILITIES = os.getenv("DSS_WIND_PROBABILITIES", "0") == "1"
ALLOW_MEAN_WIND_AS_GUST_PROXY = os.getenv("DSS_ALLOW_MEAN_WIND_AS_GUST_PROXY", "0") == "1"
EXTRACT_DESI_REFS_GUSTS = os.getenv("DSS_EXTRACT_DESI_REFS_GUSTS", "1") == "1"
RRFSENS_MEMBERS = [
    member.strip()
    for member in os.getenv("DSS_RRFSENS_MEMBERS", "m001,m002,m003,m004,m005").split(",")
    if member.strip()
]
GUST_WORKERS = max(1, int(os.getenv("DSS_GUST_WORKERS", "8")))
HRRR_HTTP_BASE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"

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

CARD_LABELS = {
    "WIND": "WIND",
    "LIGHTNING": "LIGHTNING",
    "SNOW": "SNOW",
    "VISIBILITY": "VISIBILITY",
    "FZRA": "FZRA",
    "FLASH_FREEZE": "FLASH FREEZE",
    "RAIN": "RAIN",
    "TEMPERATURE": "TEMPERATURE",
}

TIMELINE_LABELS = {
    "WIND": "WIND",
    "LIGHTNING": "LIGHTNING",
    "SNOW": "SNOW",
    "VISIBILITY": "VISIBILITY",
    "FZRA": "FZRA",
    "FLASH_FREEZE": "FLASH FZ",
    "RAIN": "RAIN",
    "TEMPERATURE": "TEMP",
}

FULL_NAMES = {
    "WIND": "WIND",
    "LIGHTNING": "LIGHTNING",
    "SNOW": "SNOW",
    "VISIBILITY": "VISIBILITY",
    "FZRA": "FREEZING RAIN",
    "FLASH_FREEZE": "FLASH FREEZE",
    "RAIN": "RAIN",
    "TEMPERATURE": "TEMPERATURE",
}

RISK_LABELS = {
    0: "None",
    1: "Little to None",
    2: "Minor",
    3: "Moderate",
    4: "Major",
    5: "Extreme",
}

RISK_MATRIX = {
    1: {1: 1, 2: 1, 3: 1, 4: 2, 5: 2},
    2: {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
    3: {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
    4: {1: 1, 2: 2, 3: 3, 4: 4, 5: 4},
    5: {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
}

WIND_THRESHOLDS = [
    {"key": "gt_30_mph", "mph": 30.0, "prob_mps": 15.4, "impact": 2, "label": ">30 mph"},
    {"key": "gt_45_mph", "mph": 45.0, "prob_mps": 20.6, "impact": 3, "label": ">45 mph"},
    {"key": "gt_58_mph", "mph": 58.0, "prob_mps": 25.72, "impact": 4, "label": ">58 mph"},
    {"key": "gt_65_mph", "mph": 65.0, "prob_mps": 30.9, "impact": 5, "label": ">65 mph"},
]

FXX_RE = re.compile(r"\.f(\d{1,3})\.", re.IGNORECASE)
PRODUCT_RE = re.compile(r"refs\.t\d{2}z\.([a-zA-Z0-9_]+)\.f\d{1,3}\.", re.IGNORECASE)
PREFIX_CYCLE_RE = re.compile(r"refs\.(\d{8})/(\d{2})")


@dataclass(frozen=True)
class RefsFile:
    key: str
    product: str
    fxx: int
    kind: str
    size: int | None = None


@dataclass(frozen=True)
class GustMemberSource:
    name: str
    model: str
    init_dt: datetime
    lag_hours: int
    member: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def risk_label(risk: int) -> str:
    return RISK_LABELS.get(int(risk), "Unknown")


def probability_to_likelihood(probability: float | None) -> int:
    if probability is None or probability <= 0:
        return 0
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
    likelihood = probability_to_likelihood(probability)
    if likelihood == 0:
        return 0
    impact = max(1, min(5, int(impact_level)))
    return RISK_MATRIX[likelihood][impact]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def s3_to_http(key: str) -> str:
    if key.startswith("http://") or key.startswith("https://"):
        return key
    return f"{S3_HTTP_BASE}/{quote(key.lstrip('/'), safe='/')}"


def parse_cycle_string(value: Any) -> datetime | None:
    if not value:
        return None

    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    for fmt in ("%Y%m%d%H", "%Y-%m-%d %H", "%Y-%m-%d %HZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def load_selected_cycle() -> dict[str, Any]:
    if not SELECTED_CYCLE_PATH.exists():
        raise FileNotFoundError(
            "Missing data/rrfs_refs_selected_cycle.json. "
            "Run scripts/scan_rrfs_refs_inventory.py first."
        )

    payload = json.loads(SELECTED_CYCLE_PATH.read_text())
    selected = payload.get("selected_cycle") if isinstance(payload, dict) else None

    if isinstance(selected, dict):
        merged = dict(selected)
        merged.setdefault("bucket", payload.get("bucket"))
        merged.setdefault("wrapper_generated_utc", payload.get("generated_utc"))
        return merged

    if isinstance(payload, dict):
        return payload

    raise RuntimeError("Could not parse data/rrfs_refs_selected_cycle.json as an object.")


def flatten_selected_items(selected: dict[str, Any]) -> list[Any]:
    if isinstance(selected.get("selected_cycle"), dict):
        selected = selected["selected_cycle"]

    items: list[Any] = []
    for field in ("parsed_objects", "keys", "files", "objects", "idx_keys", "sample_keys"):
        value = selected.get(field)
        if isinstance(value, list):
            items.extend(value)
    return items


def infer_cycle_dt(selected: dict[str, Any]) -> datetime:
    for field in ("cycle_utc", "cycle", "cycle_label", "selected_cycle_utc", "selected_cycle"):
        parsed = parse_cycle_string(selected.get(field))
        if parsed:
            return parsed

    for field in ("prefix", "s3_prefix"):
        match = PREFIX_CYCLE_RE.search(str(selected.get(field, "")))
        if match:
            return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H").replace(tzinfo=timezone.utc)

    for item in flatten_selected_items(selected)[:1000]:
        key = item if isinstance(item, str) else item.get("key") if isinstance(item, dict) else ""
        match = PREFIX_CYCLE_RE.search(str(key))
        if match:
            return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H").replace(tzinfo=timezone.utc)

    raise RuntimeError(f"Could not infer REFS cycle time. Selected-cycle keys: {list(selected.keys())}")


def parse_product_from_key(key: str) -> str:
    match = PRODUCT_RE.search(key)
    if match:
        return match.group(1).lower()
    return "unknown"


def parse_fxx_from_key(key: str) -> int | None:
    match = FXX_RE.search(key)
    if match:
        return int(match.group(1))
    return None


def parse_kind_from_key(key: str) -> str | None:
    lower = key.lower()
    if lower.endswith(".grib2.idx"):
        return "idx"
    if lower.endswith(".grib2") or lower.endswith(".grb2"):
        return "grib"
    return None


def is_conus_key(key: str) -> bool:
    lower = key.lower()
    return ".conus.grib2" in lower or ".conus.grib2.idx" in lower


def build_file_index(selected: dict[str, Any]) -> dict[tuple[str, int, str], RefsFile]:
    index: dict[tuple[str, int, str], RefsFile] = {}

    for item in flatten_selected_items(selected):
        if isinstance(item, str):
            key = item
            product = parse_product_from_key(key)
            fxx = parse_fxx_from_key(key)
            kind = parse_kind_from_key(key)
            size = None
        elif isinstance(item, dict):
            key = str(item.get("key") or item.get("name") or item.get("path") or "")
            product = str(item.get("product") or parse_product_from_key(key)).lower()
            fxx = item.get("fxx")
            fxx = int(fxx) if fxx is not None else parse_fxx_from_key(key)
            if item.get("is_idx") is True:
                kind = "idx"
            elif item.get("is_grib2") is True:
                kind = "grib"
            else:
                kind = parse_kind_from_key(key)
            size = item.get("size")
        else:
            continue

        if not key or fxx is None or kind not in {"idx", "grib"}:
            continue
        if not is_conus_key(key):
            continue

        index[(product, int(fxx), kind)] = RefsFile(
            key=key,
            product=product,
            fxx=int(fxx),
            kind=kind,
            size=int(size) if isinstance(size, int) else None,
        )

    return index


def load_field_map() -> dict[str, Any]:
    if not FIELD_MAP_SUMMARY_PATH.exists():
        return {
            "status": "missing",
            "files": [],
            "category_counts": {},
            "errors": ["data/refs_field_map_summary.json not found"],
        }

    try:
        payload = json.loads(FIELD_MAP_SUMMARY_PATH.read_text())
    except Exception as exc:
        return {
            "status": "error",
            "files": [],
            "category_counts": {},
            "errors": [str(exc)],
        }

    if not isinstance(payload, dict):
        return {
            "status": "error",
            "files": [],
            "category_counts": {},
            "errors": ["field map summary is not a JSON object"],
        }

    payload.setdefault("status", "ok")
    payload.setdefault("files", [])
    payload.setdefault("category_counts", {})
    payload.setdefault("errors", [])
    return payload


def summarize_file_index(file_index: dict[tuple[str, int, str], RefsFile]) -> dict[str, Any]:
    products: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"idx_hours": [], "grib_hours": []})

    for (product, fxx, kind), _ in sorted(file_index.items()):
        field = "idx_hours" if kind == "idx" else "grib_hours"
        products[product][field].append(fxx)

    return {
        product: {
            "idx_hours": sorted(set(values["idx_hours"])),
            "grib_hours": sorted(set(values["grib_hours"])),
        }
        for product, values in sorted(products.items())
    }


def get_file(file_index: dict[tuple[str, int, str], RefsFile], product: str, fxx: int, kind: str) -> RefsFile | None:
    return file_index.get((product, int(fxx), kind))


def parse_idx_text(idx_text: str) -> list[dict[str, Any]]:
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


def load_idx_rows(
    file_index: dict[tuple[str, int, str], RefsFile],
    idx_cache: dict[tuple[str, int], list[dict[str, Any]]],
    product: str,
    fxx: int,
) -> list[dict[str, Any]]:
    cache_key = (product, int(fxx))
    if cache_key in idx_cache:
        return idx_cache[cache_key]

    idx_file = get_file(file_index, product, fxx, "idx")
    if not idx_file:
        idx_cache[cache_key] = []
        return []

    import requests

    response = requests.get(s3_to_http(idx_file.key), timeout=30)
    response.raise_for_status()
    rows = parse_idx_text(response.text)
    idx_cache[cache_key] = rows
    return rows


def build_desi_gust_sources(cycle_dt: datetime) -> list[GustMemberSource]:
    lag_dt = cycle_dt - timedelta(hours=6)
    sources = [
        GustMemberSource("HRRR", "hrrr", cycle_dt, 0),
        GustMemberSource("HRRR-6", "hrrr", lag_dt, 6),
        GustMemberSource("RRFS", "rrfs", cycle_dt, 0),
        GustMemberSource("RRFS-6", "rrfs", lag_dt, 6),
    ]

    for member in RRFSENS_MEMBERS:
        member_label = str(int(member.lstrip("m") or "0"))
        sources.append(GustMemberSource(f"RRFS-{member_label}", "rrfsens", cycle_dt, 0, member))
        sources.append(GustMemberSource(f"RRFS-{member_label}-6", "rrfsens", lag_dt, 6, member))

    return sources


def gust_idx_url(source: GustMemberSource, target_fxx: int) -> tuple[str, str, int]:
    fxx = target_fxx + source.lag_hours
    ymd = source.init_dt.strftime("%Y%m%d")
    hh = source.init_dt.strftime("%H")

    if source.model == "hrrr":
        key = f"hrrr.{ymd}/conus/hrrr.t{hh}z.wrfsfcf{fxx:02d}.grib2.idx"
        return f"{HRRR_HTTP_BASE}/{quote(key, safe='/')}", f"{HRRR_HTTP_BASE}/{quote(key.removesuffix('.idx'), safe='/')}", fxx

    if source.model == "rrfs":
        key = f"rrfs_a/rrfs.{ymd}/{hh}/rrfs.t{hh}z.2dfld.3km.f{fxx:03d}.conus.grib2.idx"
        return s3_to_http(key), s3_to_http(key.removesuffix(".idx")), fxx

    if source.model == "rrfsens" and source.member:
        key = (
            f"rrfs_a/rrfsens.{ymd}/{hh}/{source.member}/"
            f"rrfs.t{hh}z.{source.member}.2dfld.3km.f{fxx:03d}.conus.grib2.idx"
        )
        return s3_to_http(key), s3_to_http(key.removesuffix(".idx")), fxx

    raise RuntimeError(f"Unsupported gust source: {source}")


def load_idx_rows_from_url(idx_url: str, idx_cache: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if idx_url in idx_cache:
        return idx_cache[idx_url]

    import requests

    response = requests.get(idx_url, timeout=30)
    if response.status_code == 404:
        idx_cache[idx_url] = []
        return []
    response.raise_for_status()
    rows = parse_idx_text(response.text)
    idx_cache[idx_url] = rows
    return rows


def find_10m_wind_mean_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        line = row["line"].upper()
        if ":WIND:10 M ABOVE GROUND:" in line and "WT ENS MEAN" in line:
            return row
    return None


def find_10m_wind_prob_row(rows: list[dict[str, Any]], threshold_mps: float) -> dict[str, Any] | None:
    target = f"PROB >{threshold_mps:g}"
    for row in rows:
        line = row["line"].upper()
        if ":WIND:10 M ABOVE GROUND:" not in line:
            continue
        if "PROB FCST" not in line:
            continue
        if target in line:
            return row
    return None


def find_surface_gust_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        line = row["line"].upper()
        if ":GUST:SURFACE:" in line:
            return row
    return None


def nearest_grid_value(grib_url: str, row: dict[str, Any], label: str) -> float:
    import requests
    import xarray as xr

    headers = (
        {"Range": f"bytes={row['start_byte']}-{row['end_byte']}"}
        if row.get("end_byte") is not None
        else {"Range": f"bytes={row['start_byte']}-"}
    )
    response = requests.get(grib_url, headers=headers, timeout=30)
    response.raise_for_status()
    if len(response.content) < 100:
        raise RuntimeError(f"GRIB byte range for {label} was too small: {len(response.content)} bytes")

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / f"{label}.grib2"
        path.write_bytes(response.content)
        ds = xr.open_dataset(
            path,
            engine="cfgrib",
            backend_kwargs={"indexpath": "", "errors": "ignore"},
        )

        try:
            data_vars = list(ds.data_vars)
            if not data_vars:
                raise RuntimeError(f"No data variables in {label}")
            var_name = data_vars[0]

            lat_name = next((name for name in ("latitude", "lat", "gridlat_0") if name in ds.coords or name in ds.variables), None)
            lon_name = next((name for name in ("longitude", "lon", "gridlon_0") if name in ds.coords or name in ds.variables), None)
            if not lat_name or not lon_name:
                raise RuntimeError(f"No lat/lon coordinates in {label}")

            lat = ds[lat_name]
            lon = ds[lon_name]
            target_lon = LON + 360.0 if LON < 0 else LON

            if lat.ndim == 1 and lon.ndim == 1:
                lat_idx = int(abs(lat - LAT).argmin())
                lon_target = target_lon if float(lon.max()) > 180 else LON
                lon_idx = int(abs(lon - lon_target).argmin())
                value = ds[var_name].isel({lat_name: lat_idx, lon_name: lon_idx}).values
            else:
                lon_values = lon.values
                lat_values = lat.values
                lon_target = target_lon if float(lon_values.max()) > 180 else LON
                dist2 = (lat_values - LAT) ** 2 + (lon_values - lon_target) ** 2
                flat_index = int(dist2.argmin())
                iy, ix = [int(v) for v in divmod(flat_index, dist2.shape[1])]
                indexers: dict[str, int] = {}
                dims = ds[var_name].dims
                for dim, idx in zip(lat.dims, [iy, ix]):
                    if dim in dims:
                        indexers[dim] = idx
                value = ds[var_name].isel(indexers).values

            value_float = float(value.squeeze())
            if math.isnan(value_float):
                raise RuntimeError(f"Nearest value for {label} is NaN")
            return value_float
        finally:
            ds.close()


def normalize_probability(value: float) -> float:
    if value <= 1.0:
        return round(max(0.0, min(100.0, value * 100.0)), 1)
    return round(max(0.0, min(100.0, value)), 1)


def evaluate_wind_thresholds(mean_mph: float | None, threshold_probs: dict[str, float | None]) -> dict[str, Any]:
    candidates = []
    for threshold in WIND_THRESHOLDS:
        probability = threshold_probs.get(threshold["key"])
        source = "refs_probability"
        if probability is None:
            probability = 100.0 if mean_mph is not None and mean_mph >= threshold["mph"] else 0.0
            source = "deterministic_mean_fallback"

        risk = matrix_risk(probability, int(threshold["impact"]))
        candidates.append(
            {
                "threshold_key": threshold["key"],
                "threshold_mph": threshold["mph"],
                "prob_threshold_mps": threshold["prob_mps"],
                "impact_level": threshold["impact"],
                "probability": round(float(probability), 1),
                "risk": risk,
                "risk_label": risk_label(risk),
                "label": threshold["label"],
                "source": source,
            }
        )

    return max(candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))


def gust_threshold_probabilities(values_mph: list[float]) -> dict[str, float]:
    if not values_mph:
        return {threshold["key"]: 0.0 for threshold in WIND_THRESHOLDS}

    count = len(values_mph)
    return {
        threshold["key"]: round(
            100.0 * sum(1 for value in values_mph if value >= float(threshold["mph"])) / count,
            1,
        )
        for threshold in WIND_THRESHOLDS
    }


def evaluate_gust_thresholds(values_mph: list[float]) -> dict[str, Any]:
    probabilities = gust_threshold_probabilities(values_mph)
    candidates = []

    for threshold in WIND_THRESHOLDS:
        probability = probabilities[threshold["key"]]
        risk = matrix_risk(probability, int(threshold["impact"]))
        candidates.append(
            {
                "threshold_key": threshold["key"],
                "threshold_mph": threshold["mph"],
                "impact_level": threshold["impact"],
                "probability": probability,
                "risk": risk,
                "risk_label": risk_label(risk),
                "label": threshold["label"],
                "source": "desi_refs_time_lagged_gust",
            }
        )

    return max(candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))


def extract_desi_refs_gust_hourly(cycle_dt: datetime) -> dict[str, Any]:
    sources = build_desi_gust_sources(cycle_dt)

    if not EXTRACT_DESI_REFS_GUSTS:
        return {
            "status": "disabled",
            "method": "desi_refs_time_lagged_gust_disabled",
            "member_method_available": False,
            "probability_extraction_enabled": False,
            "hourly": [],
            "ok_gust_values": 0,
            "errors": [],
        }

    try:
        import requests  # noqa: F401
        import xarray  # noqa: F401
        import cfgrib  # noqa: F401
    except Exception as exc:
        return {
            "status": "missing_dependencies",
            "method": "desi_refs_time_lagged_gust",
            "message": str(exc),
            "hourly": [],
            "ok_gust_values": 0,
            "errors": [{"stage": "import", "message": str(exc)}],
        }

    hourly_by_fxx: dict[int, dict[str, Any]] = {}
    member_maxes: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []

    for fxx in FORECAST_HOURS:
        valid_utc = (cycle_dt + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")
        hourly_by_fxx[fxx] = {
            "fxx": fxx,
            "valid_utc": valid_utc,
            "gust_values_mph": [],
            "members": [],
            "best": {
                "risk": 0,
                "risk_label": "None",
                "probability": 0.0,
                "impact_level": 0,
                "label": "No signal",
            },
        }

    def read_member_hour(source: GustMemberSource, target_fxx: int) -> dict[str, Any]:
        idx_url, grib_url, source_fxx = gust_idx_url(source, target_fxx)
        rows = load_idx_rows_from_url(idx_url, {})
        gust_row = find_surface_gust_row(rows)
        if not gust_row:
            raise RuntimeError("No GUST:surface row in source IDX")

        raw_mps = nearest_grid_value(grib_url, gust_row, f"{source.name.lower().replace('-', '_')}_gust_f{target_fxx:03d}")
        return {
            "member": source.name,
            "model": source.model,
            "target_fxx": target_fxx,
            "source_fxx": source_fxx,
            "lag_hours": source.lag_hours,
            "gust_mph": round(raw_mps * MPS_TO_MPH, 1),
            "idx_line": gust_row["line"],
        }

    tasks = [(source, fxx) for source in sources for fxx in FORECAST_HOURS]
    with ThreadPoolExecutor(max_workers=GUST_WORKERS) as executor:
        future_map = {
            executor.submit(read_member_hour, source, fxx): (source, fxx)
            for source, fxx in tasks
        }

        for future in as_completed(future_map):
            source, fxx = future_map[future]
            try:
                result = future.result()
                gust_mph = float(result["gust_mph"])
                valid_utc = hourly_by_fxx[fxx]["valid_utc"]
                hourly_by_fxx[fxx]["gust_values_mph"].append(gust_mph)
                hourly_by_fxx[fxx]["members"].append(
                    {
                        "member": source.name,
                        "model": source.model,
                        "source_fxx": result["source_fxx"],
                        "lag_hours": source.lag_hours,
                        "gust_mph": gust_mph,
                        "idx_line": result["idx_line"],
                    }
                )

                current = member_maxes.get(source.name)
                if current is None or gust_mph > float(current["max_gust_mph"]):
                    member_maxes[source.name] = {
                        "member": source.name,
                        "model": source.model,
                        "lag_hours": source.lag_hours,
                        "max_gust_mph": gust_mph,
                        "source_fxx": fxx,
                        "source_model_fxx": result["source_fxx"],
                        "peak_valid_utc": valid_utc,
                    }
            except Exception as exc:
                errors.append(
                    {
                        "member": source.name,
                        "model": source.model,
                        "fxx": fxx,
                        "source_fxx": fxx + source.lag_hours,
                        "stage": "gust",
                        "message": str(exc),
                    }
                )

    hourly: list[dict[str, Any]] = []
    for fxx in FORECAST_HOURS:
        hour = hourly_by_fxx[fxx]
        hour["members"] = sorted(hour["members"], key=lambda item: item["member"])
        values = [float(value) for value in hour["gust_values_mph"]]
        if values:
            hour["gust_mean_mph"] = round(sum(values) / len(values), 1)
            hour["gust_max_mph"] = round(max(values), 1)
            hour["member_count"] = len(values)
            hour["probabilities"] = gust_threshold_probabilities(values)
            hour["best"] = evaluate_gust_thresholds(values)
            hour["status"] = "ok"
        else:
            hour["gust_mean_mph"] = None
            hour["gust_max_mph"] = None
            hour["member_count"] = 0
            hour["probabilities"] = gust_threshold_probabilities([])
            hour["status"] = "missing"
        hourly.append(hour)

    member_max_list = sorted(member_maxes.values(), key=lambda item: item["member"])
    member_max_values = [float(item["max_gust_mph"]) for item in member_max_list]
    ok_gust_values = sum(len(hour.get("gust_values_mph", [])) for hour in hourly)

    if not member_max_values:
        return {
            "status": "no_values",
            "method": "desi_refs_time_lagged_gust",
            "member_method_available": False,
            "probability_extraction_enabled": True,
            "hourly": hourly,
            "members_requested": [source.name for source in sources],
            "members_found": [],
            "ok_gust_values": ok_gust_values,
            "errors": errors[:30],
        }

    mean_member_max = round(sum(member_max_values) / len(member_max_values), 1)
    best_60hr = evaluate_gust_thresholds(member_max_values)
    peak_member = max(member_max_list, key=lambda item: float(item["max_gust_mph"]))

    return {
        "status": "ok",
        "method": "desi_refs_time_lagged_gust",
        "member_method_available": True,
        "probability_extraction_enabled": True,
        "member_method_note": (
            "Wind uses the DESI-style 14-member time-lagged REFS gust set: current and 6-hour-lagged "
            "HRRR, RRFS, and RRFSENS m001-m005 GUST:surface fields. Each member's 60-hour max gust "
            "is found first, then those member maxima are averaged."
        ),
        "hourly": hourly,
        "members_requested": [source.name for source in sources],
        "members_found": [item["member"] for item in member_max_list],
        "member_max_gusts": member_max_list,
        "mean_member_max_gust_mph": mean_member_max,
        "threshold_probabilities_60hr": gust_threshold_probabilities(member_max_values),
        "best_60hr": best_60hr,
        "peak_member": peak_member,
        "ok_gust_values": ok_gust_values,
        "ok_mean_hours": 0,
        "ok_probability_hours": sum(1 for hour in hourly if hour.get("status") == "ok"),
        "errors": errors[:30],
    }


def extract_wind_hourly(
    file_index: dict[tuple[str, int, str], RefsFile],
    cycle_dt: datetime,
) -> dict[str, Any]:
    gust_result = extract_desi_refs_gust_hourly(cycle_dt)
    if gust_result.get("status") == "ok":
        return gust_result

    if not ALLOW_MEAN_WIND_AS_GUST_PROXY:
        return {
            "status": "missing_gust_field",
            "method": "gust_required_no_proxy",
            "member_method_available": False,
            "probability_extraction_enabled": False,
            "member_method_note": (
                "WIND requires wind gust, not 10 m mean WIND. The selected REFS mean/prob/sprd products "
                "expose 10 m WIND threshold fields but no GUST/member gust fields, so the WIND hazard is "
                "left at risk 0 until the DESI gust source path is identified."
            ),
            "hourly": [],
            "ok_mean_hours": 0,
            "ok_probability_hours": 0,
            "gust_attempt": {
                "status": gust_result.get("status"),
                "method": gust_result.get("method"),
                "members_requested": gust_result.get("members_requested", [source.name for source in build_desi_gust_sources(cycle_dt)]),
                "ok_gust_values": gust_result.get("ok_gust_values", 0),
            },
            "errors": (
                gust_result.get("errors", [])[:10]
                or [{"stage": "field_selection", "message": "No DESI-style time-lagged GUST field values could be extracted."}]
            ),
        }

    hourly: list[dict[str, Any]] = []
    idx_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}
    errors: list[dict[str, Any]] = []

    try:
        import requests  # noqa: F401
        import xarray  # noqa: F401
        import cfgrib  # noqa: F401
    except Exception as exc:
        return {
            "status": "missing_dependencies",
            "message": str(exc),
            "hourly": [],
            "errors": [{"stage": "import", "message": str(exc)}],
        }

    for fxx in FORECAST_HOURS:
        valid_utc = (cycle_dt + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")
        hour: dict[str, Any] = {
            "fxx": fxx,
            "valid_utc": valid_utc,
            "mean_wind_mph": None,
            "mean_status": "missing",
            "prob_status": "missing",
            "probabilities": {},
            "best": {
                "risk": 0,
                "risk_label": "None",
                "probability": 0.0,
                "impact_level": 0,
                "label": "No signal",
            },
        }

        try:
            mean_rows = load_idx_rows(file_index, idx_cache, "mean", fxx)
            mean_row = find_10m_wind_mean_row(mean_rows)
            mean_file = get_file(file_index, "mean", fxx, "grib")
            if mean_row and mean_file:
                raw_mps = nearest_grid_value(s3_to_http(mean_file.key), mean_row, f"wind_mean_f{fxx:03d}")
                hour["mean_wind_mph"] = round(raw_mps * MPS_TO_MPH, 1)
                hour["mean_status"] = "ok"
                hour["mean_idx_line"] = mean_row["line"]
            else:
                hour["mean_status"] = "missing_row_or_file"
        except Exception as exc:
            msg = str(exc)
            hour["mean_status"] = "error"
            hour["mean_error"] = msg
            errors.append({"fxx": fxx, "stage": "mean", "message": msg})

        threshold_probs: dict[str, float | None] = {}
        try:
            if not EXTRACT_WIND_PROBABILITIES:
                hour["prob_status"] = "skipped"
                hour["prob_note"] = "Set DSS_WIND_PROBABILITIES=1 to enable probability byte-range reads."
                hour["best"] = evaluate_wind_thresholds(hour.get("mean_wind_mph"), threshold_probs)
                hourly.append(hour)
                continue

            prob_rows = load_idx_rows(file_index, idx_cache, "prob", fxx)
            prob_file = get_file(file_index, "prob", fxx, "grib")
            if prob_rows and prob_file:
                extracted_any = False
                for threshold in WIND_THRESHOLDS:
                    prob_row = find_10m_wind_prob_row(prob_rows, float(threshold["prob_mps"]))
                    if not prob_row:
                        threshold_probs[threshold["key"]] = None
                        continue
                    raw_prob = nearest_grid_value(s3_to_http(prob_file.key), prob_row, f"wind_prob_{threshold['key']}_f{fxx:03d}")
                    probability = normalize_probability(raw_prob)
                    threshold_probs[threshold["key"]] = probability
                    hour["probabilities"][threshold["key"]] = {
                        "probability": probability,
                        "idx_line": prob_row["line"],
                    }
                    extracted_any = True
                hour["prob_status"] = "ok" if extracted_any else "missing_rows"
            else:
                hour["prob_status"] = "missing_file"
        except Exception as exc:
            msg = str(exc)
            hour["prob_status"] = "error"
            hour["prob_error"] = msg
            errors.append({"fxx": fxx, "stage": "probability", "message": msg})

        hour["best"] = evaluate_wind_thresholds(hour.get("mean_wind_mph"), threshold_probs)
        hourly.append(hour)

    ok_mean_hours = sum(1 for h in hourly if h.get("mean_status") == "ok")
    ok_prob_hours = sum(1 for h in hourly if h.get("prob_status") == "ok")

    return {
        "status": "ok" if ok_mean_hours or ok_prob_hours else "no_values",
        "method": (
            "refs_mean_prob_10m_wind_fallback"
            if EXTRACT_WIND_PROBABILITIES
            else "refs_mean_10m_wind_deterministic_fallback"
        ),
        "probability_extraction_enabled": EXTRACT_WIND_PROBABILITIES,
        "member_method_available": False,
        "member_method_note": (
            "Selected REFS inventory exposes mean/prob/sprd products but no member-level gust fields. "
            "Wind card uses REFS 10 m WIND mean and deterministic threshold screening until member gust files are available."
        ),
        "hourly": hourly,
        "ok_mean_hours": ok_mean_hours,
        "ok_probability_hours": ok_prob_hours,
        "errors": errors[:20],
    }


def field_map_by_hazard(field_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    hazard_categories = {
        "WIND": {"wind"},
        "LIGHTNING": {"lightning_convection"},
        "SNOW": {"snow"},
        "VISIBILITY": {"visibility"},
        "FZRA": {"freezing_rain"},
        "RAIN": {"rain_precip"},
        "TEMPERATURE": {"temperature_wetbulb"},
    }

    summary = {
        hazard: {
            "field_map_matches": 0,
            "mapped_hours": [],
            "sample_lines": [],
        }
        for hazard in HAZARD_ORDER
    }
    summary["FLASH_FREEZE"]["note"] = "Derived later from cold/wet overlap; no direct REFS extraction in lean builder."

    for file_record in field_map.get("files", []) or []:
        if not isinstance(file_record, dict):
            continue
        fxx = file_record.get("fxx")
        for match in file_record.get("matched_lines", []) or []:
            if not isinstance(match, dict):
                continue
            categories = set(match.get("categories") or [])
            line = str(match.get("line") or "")
            for hazard, wanted_categories in hazard_categories.items():
                if not categories.intersection(wanted_categories):
                    continue
                item = summary[hazard]
                item["field_map_matches"] += 1
                if isinstance(fxx, int):
                    item["mapped_hours"].append(fxx)
                if len(item["sample_lines"]) < 3:
                    item["sample_lines"].append(line)

    for hazard, item in summary.items():
        item["mapped_hours"] = sorted(set(item.get("mapped_hours", [])))

    return summary


def empty_threat(hazard: str, cycle_dt: datetime, reason: str) -> dict[str, Any]:
    return {
        "id": hazard,
        "title": CARD_LABELS[hazard],
        "name": FULL_NAMES[hazard],
        "prob": 0.0,
        "probability": 0.0,
        "risk": 0,
        "risk_level": 0,
        "risk_label": "None",
        "level": 0,
        "impact_level": 0,
        "metric": "No signal",
        "display_label": CARD_LABELS[hazard],
        "display_value": "None",
        "window": "60 hr",
        "peak_start_fxx": None,
        "peak_end_fxx": None,
        "source_fxx": None,
        "peak_valid_utc": None,
        "driver": reason,
        "methodology": (
            "REFS lean builder generated a valid low-risk placeholder. "
            "Exact field extraction is intentionally disabled until field selection is locked down."
        ),
        "data_status": "not_extracted",
    }


def empty_timeline_hazard(
    hazard: str,
    start_fxx: int,
    end_fxx: int,
    valid_start: datetime,
    valid_end: datetime,
    reason: str,
) -> dict[str, Any]:
    return {
        "id": hazard,
        "label": TIMELINE_LABELS[hazard],
        "name": FULL_NAMES[hazard],
        "risk": 0,
        "risk_label": "None",
        "level": 0,
        "impact_level": 0,
        "prob": 0.0,
        "probability": 0.0,
        "metric": "No signal",
        "driver": reason,
        "source_fxx": None,
        "peak_valid_utc": None,
        "valid_start_utc": valid_start.isoformat().replace("+00:00", "Z"),
        "valid_end_utc": valid_end.isoformat().replace("+00:00", "Z"),
        "start_fxx": start_fxx,
        "end_fxx": end_fxx,
        "data_status": "not_extracted",
    }


def wind_threat_payload(wind_result: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    if wind_result.get("method") == "desi_refs_time_lagged_gust":
        best = wind_result.get("best_60hr") or {}
        risk = int(best.get("risk", 0))
        prob = round(float(best.get("probability", 0.0)), 1)
        mean_member_max = wind_result.get("mean_member_max_gust_mph")
        peak_member = wind_result.get("peak_member") or {}

        return {
            **fallback,
            "prob": prob,
            "probability": prob,
            "risk": risk,
            "risk_level": risk,
            "risk_label": risk_label(risk),
            "level": int(best.get("impact_level", 0)),
            "impact_level": int(best.get("impact_level", 0)),
            "metric": best.get("label", "No signal") if risk > 0 else "No signal",
            "display_label": "60-hr max gust",
            "display_value": f"{float(mean_member_max):.0f} mph" if mean_member_max is not None else "No gust value",
            "peak_start_fxx": peak_member.get("source_fxx"),
            "peak_end_fxx": peak_member.get("source_fxx"),
            "source_fxx": peak_member.get("source_fxx"),
            "peak_valid_utc": peak_member.get("peak_valid_utc"),
            "driver": (
                f"{prob:.0f}% of members exceed {best.get('label', 'gust threshold')}; "
                f"mean member 60-hr max gust {float(mean_member_max):.1f} mph"
                if risk > 0 and mean_member_max is not None
                else "DESI-style time-lagged member gusts stay below DSS wind thresholds"
            ),
            "methodology": (
                "WIND uses DESI-style time-lagged GUST:surface fields from HRRR, RRFS, and RRFSENS. "
                "For each member, the builder finds that member's maximum gust from f001-f060, then averages those member maxima. "
                "Threshold probabilities are the share of member maxima exceeding each gust threshold."
            ),
            "data_status": wind_result.get("status", "unknown"),
            "method": wind_result.get("method"),
            "member_method_available": True,
            "member_method_note": wind_result.get("member_method_note"),
            "member_count": len(wind_result.get("member_max_gusts") or []),
            "members_found": wind_result.get("members_found", []),
            "member_max_gusts": wind_result.get("member_max_gusts", []),
            "mean_member_max_gust_mph": mean_member_max,
            "threshold_probabilities_60hr": wind_result.get("threshold_probabilities_60hr", {}),
            "g24_p50_mph": mean_member_max,
        }

    hourly = wind_result.get("hourly") or []
    valid_hours = [
        h for h in hourly
        if h.get("mean_wind_mph") is not None or h.get("best", {}).get("probability", 0) > 0
    ]
    if not valid_hours:
        return fallback

    best_hour = max(
        valid_hours,
        key=lambda h: (
            h.get("best", {}).get("risk", 0),
            h.get("best", {}).get("probability", 0.0),
            h.get("mean_wind_mph") if h.get("mean_wind_mph") is not None else -999.0,
        ),
    )
    best = best_hour.get("best", {})
    risk = int(best.get("risk", 0))
    prob = round(float(best.get("probability", 0.0)), 1)
    mean_mph = best_hour.get("mean_wind_mph")
    fxx = best_hour.get("fxx")

    return {
        **fallback,
        "prob": prob,
        "probability": prob,
        "risk": risk,
        "risk_level": risk,
        "risk_label": risk_label(risk),
        "level": int(best.get("impact_level", 0)),
        "impact_level": int(best.get("impact_level", 0)),
        "metric": best.get("label", "No signal") if risk > 0 else "No signal",
        "display_label": "60-hr max wind",
        "display_value": f"{mean_mph:.0f} mph" if mean_mph is not None else "No mean value",
        "peak_start_fxx": fxx,
        "peak_end_fxx": fxx,
        "source_fxx": fxx,
        "peak_valid_utc": best_hour.get("valid_utc"),
        "driver": (
            f"{prob:.0f}% {best.get('label', 'wind threshold')}; "
            f"10 m mean wind {mean_mph:.1f} mph"
            if risk > 0 and mean_mph is not None
            else wind_result.get("member_method_note", "No meaningful wind signal")
        ),
        "methodology": (
            "Target method is member-by-member 60-hour max wind gust, then mean of member maxima. "
            "Member-level gust fields were not present in the selected REFS products, so this run uses "
            "REFS 10 m WIND mean with deterministic threshold screening as the documented fallback. "
            "Set DSS_WIND_PROBABILITIES=1 to also read REFS 10 m exceedance probability fields."
        ),
        "data_status": wind_result.get("status", "unknown"),
        "method": wind_result.get("method"),
        "member_method_available": False,
        "member_method_note": wind_result.get("member_method_note"),
        "mean_wind_60hr_max_mph": mean_mph,
        "g24_p50_mph": mean_mph,
    }


def wind_block_payload(
    wind_result: dict[str, Any],
    start_fxx: int,
    end_fxx: int,
    valid_start: datetime,
    valid_end: datetime,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    hourly = [
        h for h in wind_result.get("hourly", [])
        if start_fxx <= int(h.get("fxx", -999)) <= end_fxx
    ]
    if not hourly:
        return fallback

    best_hour = max(
        hourly,
        key=lambda h: (
            h.get("best", {}).get("risk", 0),
            h.get("best", {}).get("probability", 0.0),
            h.get("gust_max_mph") if h.get("gust_max_mph") is not None else -999.0,
            h.get("mean_wind_mph") if h.get("mean_wind_mph") is not None else -999.0,
        ),
    )
    best = best_hour.get("best", {})
    risk = int(best.get("risk", 0))
    prob = round(float(best.get("probability", 0.0)), 1)
    mean_mph = best_hour.get("mean_wind_mph")
    gust_mean_mph = best_hour.get("gust_mean_mph")
    gust_max_mph = best_hour.get("gust_max_mph")
    uses_gust = wind_result.get("method") == "desi_refs_time_lagged_gust"

    return {
        **fallback,
        "risk": risk,
        "risk_label": risk_label(risk),
        "level": int(best.get("impact_level", 0)),
        "impact_level": int(best.get("impact_level", 0)),
        "prob": prob,
        "probability": prob,
        "metric": best.get("label", "No signal") if risk > 0 else "No signal",
        "driver": (
            f"{prob:.0f}% of members exceed {best.get('label', 'gust threshold')}; "
            f"block max gust {float(gust_max_mph):.1f} mph"
            if uses_gust and risk > 0 and gust_max_mph is not None
            else
            f"{prob:.0f}% {best.get('label', 'wind threshold')}; "
            f"10 m mean wind {mean_mph:.1f} mph"
            if risk > 0 and mean_mph is not None
            else "No meaningful wind signal in this 3-hour block"
        ),
        "source_fxx": best_hour.get("fxx"),
        "peak_valid_utc": best_hour.get("valid_utc"),
        "valid_start_utc": valid_start.isoformat().replace("+00:00", "Z"),
        "valid_end_utc": valid_end.isoformat().replace("+00:00", "Z"),
        "mean_wind_mph": mean_mph,
        "gust_mean_mph": gust_mean_mph,
        "gust_max_mph": gust_max_mph,
        "member_count": best_hour.get("member_count"),
        "members": best_hour.get("members", []),
        "data_status": wind_result.get("status", "unknown"),
        "method": wind_result.get("method"),
    }


def hazard_reason(hazard: str, field_summary: dict[str, Any]) -> str:
    matches = int(field_summary.get(hazard, {}).get("field_map_matches", 0))
    if matches:
        return f"REFS field-map found {matches} candidate IDX lines; value extraction not enabled in lean builder"
    return "No matched REFS field-map candidates; risk set to None"


def build_outputs(
    cycle_dt: datetime,
    selected: dict[str, Any],
    file_index: dict[tuple[str, int, str], RefsFile],
    field_map: dict[str, Any],
    wind_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    generated = utc_now()
    cycle_iso = cycle_dt.isoformat().replace("+00:00", "Z")
    file_summary = summarize_file_index(file_index)
    field_summary = field_map_by_hazard(field_map)

    common_meta = {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": LAT,
        "lon": LON,
        "source": "NOAA RRFS / REFS via AWS S3",
        "generated_utc": generated,
        "cycle_utc_iso": cycle_iso,
        "cycle": f"REFS {cycle_dt.strftime('%HZ')}",
        "valid_period": "next_60_hours",
    }

    threats: dict[str, Any] = {
        **common_meta,
        "threats": {},
        "hazards": [],
        "methodology": (
            "REFS-only DSS backend. This lean pass indexes the selected cycle and compact field map, "
            "then writes complete dashboard JSON without broad GRIB parsing. Hazards remain risk 0 "
            "until exact byte-range extraction is enabled per field."
        ),
        "metadata": {
            "builder_mode": "refs_wind_first_pass",
            "extraction": "wind_enabled",
            "selected_prefix": selected.get("prefix"),
            "available_products": sorted(file_summary.keys()),
            "field_map_status": field_map.get("status", "unknown"),
        },
    }

    for hazard in HAZARD_ORDER:
        threat = empty_threat(hazard, cycle_dt, hazard_reason(hazard, field_summary))
        if hazard == "WIND" and wind_result and wind_result.get("status") == "ok":
            threat = wind_threat_payload(wind_result, threat)
        elif hazard == "WIND" and wind_result and wind_result.get("status") == "missing_gust_field":
            threat["driver"] = wind_result.get("member_method_note", threat["driver"])
            threat["data_status"] = "missing_gust_field"
            threat["method"] = "gust_required_no_proxy"
            threat["methodology"] = (
                "WIND is intentionally not populated from 10 m mean WIND. "
                "Operational WIND risk requires gust/member-gust data."
            )
        threats["threats"][hazard] = threat
        threats["hazards"].append(
            {
                "id": hazard,
                "name": CARD_LABELS[hazard],
                "full_name": FULL_NAMES[hazard],
                "risk_level": threat["risk_level"],
                "risk_label": threat["risk_label"],
                "impact_level": threat["impact_level"],
                "probability": threat["probability"],
                "peak_start_fxx": threat["peak_start_fxx"],
                "peak_end_fxx": threat["peak_end_fxx"],
                "metric": threat["metric"],
                "display_label": threat["display_label"],
                "display_value": threat["display_value"],
                "driver": threat["driver"],
            }
        )

    threats["hazards"].sort(
        key=lambda h: (h["risk_level"], h["probability"], h["impact_level"]),
        reverse=True,
    )

    timeline: dict[str, Any] = {
        **common_meta,
        "block_hours": BLOCK_HOURS,
        "blocks": [],
        "block_hazards": [],
        "metadata": {
            "builder_mode": "refs_wind_first_pass",
            "extraction": "wind_enabled",
        },
    }

    for block_index in range(BLOCK_COUNT):
        start_fxx = block_index * BLOCK_HOURS + 1
        end_fxx = start_fxx + BLOCK_HOURS - 1
        valid_start = cycle_dt + timedelta(hours=start_fxx)
        valid_end = cycle_dt + timedelta(hours=end_fxx)

        block = {
            "block_index": block_index,
            "start_fxx": start_fxx,
            "end_fxx": end_fxx,
            "valid_start_utc": valid_start.isoformat().replace("+00:00", "Z"),
            "valid_end_utc": valid_end.isoformat().replace("+00:00", "Z"),
        }
        hazard_block: dict[str, Any] = dict(block)

        for hazard in HAZARD_ORDER:
            payload = empty_timeline_hazard(
                hazard=hazard,
                start_fxx=start_fxx,
                end_fxx=end_fxx,
                valid_start=valid_start,
                valid_end=valid_end,
                reason=hazard_reason(hazard, field_summary),
            )
            if hazard == "WIND" and wind_result and wind_result.get("status") == "ok":
                payload = wind_block_payload(wind_result, start_fxx, end_fxx, valid_start, valid_end, payload)
            elif hazard == "WIND" and wind_result and wind_result.get("status") == "missing_gust_field":
                payload["driver"] = "No REFS gust field found; mean wind is not used as a gust proxy"
                payload["data_status"] = "missing_gust_field"
                payload["method"] = "gust_required_no_proxy"
            hazard_block[hazard] = payload
            block[hazard] = payload["risk"]

        timeline["blocks"].append(block)
        timeline["block_hazards"].append(hazard_block)

    builder_summary = {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": LAT,
        "lon": LON,
        "generated_utc": generated,
        "cycle_utc_iso": cycle_iso,
        "selected_prefix": selected.get("prefix"),
        "builder_mode": "refs_wind_first_pass",
        "candidate_file_count": len(file_index),
        "products": file_summary,
        "field_map": {
            "status": field_map.get("status", "unknown"),
            "file_count": len(field_map.get("files") or []),
            "category_counts": field_map.get("category_counts", {}),
            "errors": field_map.get("errors", [])[:10],
        },
        "hazards": {
            hazard: {
                "risk": 0,
                "risk_label": "None",
                "field_map_matches": field_summary[hazard].get("field_map_matches", 0),
                "mapped_hours": field_summary[hazard].get("mapped_hours", []),
                "sample_lines": field_summary[hazard].get("sample_lines", []),
                "status": "not_extracted",
            }
            for hazard in HAZARD_ORDER
        },
        "notes": [
            "No full GRIB files are downloaded by this builder.",
            "Wind uses IDX-selected byte ranges for DESI-style time-lagged GUST:surface fields when available.",
            "Outputs are complete and low-risk when exact REFS fields are missing or not yet enabled.",
        ],
    }

    if wind_result:
        wind_threat = threats["threats"]["WIND"]
        builder_summary["hazards"]["WIND"].update(
            {
                "risk": wind_threat.get("risk", 0),
                "risk_label": wind_threat.get("risk_label", "None"),
                "status": wind_result.get("status"),
                "method": wind_result.get("method"),
                "member_method_available": wind_result.get("member_method_available", False),
                "probability_extraction_enabled": wind_result.get("probability_extraction_enabled", False),
                "mean_wind_proxy_allowed": ALLOW_MEAN_WIND_AS_GUST_PROXY,
                "ok_mean_hours": wind_result.get("ok_mean_hours", 0),
                "ok_probability_hours": wind_result.get("ok_probability_hours", 0),
                "ok_gust_values": wind_result.get("ok_gust_values", 0),
                "mean_wind_60hr_max_mph": wind_threat.get("mean_wind_60hr_max_mph"),
                "mean_member_max_gust_mph": wind_threat.get("mean_member_max_gust_mph"),
                "members_found": wind_result.get("members_found", []),
                "threshold_probabilities_60hr": wind_result.get("threshold_probabilities_60hr", {}),
                "errors": wind_result.get("errors", [])[:10],
            }
        )

    return threats, timeline, builder_summary


def main() -> None:
    print("Building REFS DSS outputs")

    selected = load_selected_cycle()
    cycle_dt = infer_cycle_dt(selected)
    file_index = build_file_index(selected)
    field_map = load_field_map()

    print(f"Selected cycle: {cycle_dt:%Y-%m-%d %HZ}")
    print(f"Candidate CONUS REFS files indexed: {len(file_index)}")
    print(f"Field-map status: {field_map.get('status', 'unknown')}")
    print(f"Field-map files: {len(field_map.get('files') or [])}")

    print("Checking WIND gust source availability")
    wind_result = extract_wind_hourly(file_index, cycle_dt)
    print(
        "Wind extraction: "
        f"status={wind_result.get('status')} "
        f"gust_values={wind_result.get('ok_gust_values', 0)} "
        f"mean_hours={wind_result.get('ok_mean_hours', 0)} "
        f"prob_hours={wind_result.get('ok_probability_hours', 0)}"
    )

    threats, timeline, builder_summary = build_outputs(cycle_dt, selected, file_index, field_map, wind_result)

    write_json(DOCS / "threats.json", threats)
    write_json(DOCS / "timeline.json", timeline)
    write_json(BUILDER_SUMMARY_PATH, builder_summary)

    print("Wrote docs/threats.json")
    print("Wrote docs/timeline.json")
    print(f"Wrote {BUILDER_SUMMARY_PATH}")


if __name__ == "__main__":
    main()
