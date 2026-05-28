from __future__ import annotations

import json
import math
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

import numpy as np
from netCDF4 import Dataset, num2date


DOCS_OUT = Path("docs")
DATA_OUT = Path("data")
DOCS_OUT.mkdir(exist_ok=True)
DATA_OUT.mkdir(exist_ok=True)

SITE = "KRNO"
SITE_NAME = "Reno-Tahoe International Airport"
KRNO_LAT = 39.4991
KRNO_LON = -119.7681

SATELLITE = "GOES-18"
BUCKET = "noaa-goes18"
PRODUCT = "GLM-L2-LCFA"
SOURCE = "NOAA GOES-18 GLM-L2-LCFA"
S3_BASE_URL = f"https://{BUCKET}.s3.amazonaws.com"

RING_LIMITS_NM = [10, 15, 20, 25]
SEARCH_RADIUS_NM = 25
SEARCH_WINDOW_MINUTES = 15
SCAN_LOOKBACK_MINUTES = 20
MAX_FILES = 55


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_nm = 3440.065
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius_nm * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def bearing_cardinal(degrees: float) -> str:
    labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return labels[int((degrees + 22.5) // 45) % 8]


def build_ring_summary(features: list[dict[str, Any]]) -> dict[str, Any]:
    rings: dict[str, Any] = {}
    for limit in RING_LIMITS_NM:
        in_ring = [f for f in features if f["distance_nm"] <= limit]
        latest = max((f for f in in_ring if f.get("datetime")), key=lambda f: f["datetime"], default=None)
        rings[f"within_{limit}_nm"] = {
            "count": len(in_ring),
            "last_utc": iso_z(latest["datetime"]) if latest else None,
        }
    return rings


def build_empty(status: str, message: str, now: datetime | None = None) -> dict[str, Any]:
    generated = now or utc_now_dt()
    return {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": KRNO_LAT,
        "lon": KRNO_LON,
        "source": SOURCE,
        "satellite": SATELLITE,
        "product": PRODUCT,
        "generated_utc": iso_z(generated),
        "status": status,
        "message": message,
        "window_minutes": SEARCH_WINDOW_MINUTES,
        "search_radius_nm": SEARCH_RADIUS_NM,
        "alert_level": "unavailable" if status != "ok" else "none",
        "alert_label": "GLM feed unavailable" if status != "ok" else "No GLM lightning detected",
        "last_strike": None,
        "nearest_strike": None,
        "rings": build_ring_summary([]),
        "counts": {"groups": 0, "flashes": 0, "events": 0},
        "methodology": "NOAA GOES GLM total-lightning groups are counted inside KRNO proximity rings. GLM is not a ground-strike network.",
    }


def candidate_prefixes(now: datetime) -> list[str]:
    prefixes: list[str] = []
    cursor = now
    end = now - timedelta(minutes=SCAN_LOOKBACK_MINUTES + 10)
    while cursor >= end:
        prefix = f"{PRODUCT}/{cursor:%Y}/{cursor:%j}/{cursor:%H}/"
        if prefix not in prefixes:
            prefixes.append(prefix)
        cursor -= timedelta(hours=1)
    return prefixes


def list_s3_keys(prefix: str) -> list[str]:
    url = f"{S3_BASE_URL}/?list-type=2&prefix={quote(prefix)}&max-keys=1000"
    request = Request(url, headers={"User-Agent": "KRNO-DSS-Dashboard"})
    with urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8")
    root = ET.fromstring(text)
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    return [node.text for node in root.findall("s3:Contents/s3:Key", ns) if node.text]


KEY_TIME_RE = re.compile(r"_s(?P<year>\d{4})(?P<doy>\d{3})(?P<hms>\d{6})")


def key_start_time(key: str) -> datetime | None:
    match = KEY_TIME_RE.search(key)
    if not match:
        return None
    return datetime.strptime(
        f"{match.group('year')} {match.group('doy')} {match.group('hms')}",
        "%Y %j %H%M%S",
    ).replace(tzinfo=timezone.utc)


def recent_glm_keys(now: datetime) -> list[str]:
    start = now - timedelta(minutes=SCAN_LOOKBACK_MINUTES)
    keys: list[tuple[datetime, str]] = []
    for prefix in candidate_prefixes(now):
        for key in list_s3_keys(prefix):
            start_dt = key_start_time(key)
            if start_dt and start <= start_dt <= now + timedelta(minutes=1):
                keys.append((start_dt, key))
    keys.sort(key=lambda item: item[0], reverse=True)
    return [key for _, key in keys[:MAX_FILES]]


def download_key(key: str, output_dir: Path) -> Path:
    path = output_dir / Path(key).name
    url = f"{S3_BASE_URL}/{key}"
    request = Request(url, headers={"User-Agent": "KRNO-DSS-Dashboard"})
    with urlopen(request, timeout=45) as response:
        path.write_bytes(response.read())
    return path


def var_values(ds: Dataset, name: str) -> np.ndarray:
    values = ds.variables[name][:]
    if np.ma.isMaskedArray(values):
        values = values.filled(np.nan)
    return np.asarray(values)


def var_datetimes(ds: Dataset, name: str) -> list[datetime | None]:
    variable = ds.variables[name]
    values = variable[:]
    if np.ma.isMaskedArray(values):
        values = values.filled(np.nan)
    if len(values) == 0:
        return []
    converted = num2date(values, variable.units, only_use_cftime_datetimes=False, only_use_python_datetimes=True)
    datetimes: list[datetime | None] = []
    for item in converted:
        if item is None:
            datetimes.append(None)
        elif isinstance(item, datetime):
            datetimes.append(item.replace(tzinfo=timezone.utc) if item.tzinfo is None else item.astimezone(timezone.utc))
        else:
            datetimes.append(None)
    return datetimes


def extract_features(path: Path, now: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:
    features: list[dict[str, Any]] = []
    counts = {"groups": 0, "flashes": 0, "events": 0}
    with Dataset(path) as ds:
        counts["groups"] = int(np.size(ds.variables.get("group_lat", [])))
        counts["flashes"] = int(np.size(ds.variables.get("flash_lat", [])))
        counts["events"] = int(np.size(ds.variables.get("event_lat", [])))

        if "group_lat" not in ds.variables or "group_lon" not in ds.variables:
            return features, counts

        lats = var_values(ds, "group_lat")
        lons = var_values(ds, "group_lon")
        times = var_datetimes(ds, "group_time_offset")
        energy = var_values(ds, "group_energy") if "group_energy" in ds.variables else np.full_like(lats, np.nan)
        quality = var_values(ds, "group_quality_flag") if "group_quality_flag" in ds.variables else np.full_like(lats, np.nan)

        window_start = now - timedelta(minutes=SEARCH_WINDOW_MINUTES)
        for idx, (lat, lon) in enumerate(zip(lats, lons)):
            if not np.isfinite(lat) or not np.isfinite(lon):
                continue
            dt = times[idx] if idx < len(times) else None
            if not dt or dt < window_start or dt > now + timedelta(minutes=1):
                continue
            distance = haversine_nm(KRNO_LAT, KRNO_LON, float(lat), float(lon))
            if distance > SEARCH_RADIUS_NM:
                continue
            bearing = bearing_degrees(KRNO_LAT, KRNO_LON, float(lat), float(lon))
            features.append(
                {
                    "datetime": dt,
                    "distance_nm": distance,
                    "bearing_degrees": bearing,
                    "bearing_cardinal": bearing_cardinal(bearing),
                    "lat": float(lat),
                    "lon": float(lon),
                    "source_type": "GLM group",
                    "quality": int(quality[idx]) if idx < len(quality) and np.isfinite(quality[idx]) else None,
                    "energy_j": float(energy[idx]) if idx < len(energy) and np.isfinite(energy[idx]) else None,
                    "file": path.name,
                }
            )
    return features, counts


def feature_summary(feature: dict[str, Any], now: datetime) -> dict[str, Any]:
    age_minutes = (now - feature["datetime"]).total_seconds() / 60
    return {
        "datetime": iso_z(feature["datetime"]),
        "age_minutes": round(age_minutes, 1),
        "distance_nm": round(feature["distance_nm"], 1),
        "bearing_degrees": round(feature["bearing_degrees"]),
        "bearing_cardinal": feature["bearing_cardinal"],
        "source_type": feature.get("source_type", "GLM group"),
        "quality": feature.get("quality"),
        "energy_j": feature.get("energy_j"),
    }


def classify_alert(features: list[dict[str, Any]]) -> tuple[str, str]:
    if not features:
        return "none", "No GLM lightning detected"
    nearest = min(features, key=lambda item: item["distance_nm"])
    dist = nearest["distance_nm"]
    if dist <= 10:
        return "inside_10_nm", "GLM lightning inside 10 nm"
    if dist <= 15:
        return "monitor_15_nm", "GLM lightning inside 15 nm"
    if dist <= 20:
        return "monitor_20_nm", "GLM lightning inside 20 nm"
    return "monitor_25_nm", "GLM lightning inside 25 nm"


def build_payload(now: datetime) -> dict[str, Any]:
    keys = recent_glm_keys(now)
    if not keys:
        return build_empty("error", "No recent GOES-18 GLM files found in AWS.", now)

    features: list[dict[str, Any]] = []
    totals = {"groups": 0, "flashes": 0, "events": 0}
    processed_files: list[str] = []
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="krno-glm-") as temp_dir:
        temp_path = Path(temp_dir)
        for key in reversed(keys):
            try:
                path = download_key(key, temp_path)
                file_features, counts = extract_features(path, now)
                processed_files.append(key)
                features.extend(file_features)
                for count_key, value in counts.items():
                    totals[count_key] += value
            except Exception as exc:
                errors.append(f"{Path(key).name}: {exc}")

    features.sort(key=lambda item: item["datetime"], reverse=True)
    last = features[0] if features else None
    nearest = min(features, key=lambda item: item["distance_nm"], default=None)
    alert_level, alert_label = classify_alert(features)

    return {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": KRNO_LAT,
        "lon": KRNO_LON,
        "source": SOURCE,
        "satellite": SATELLITE,
        "product": PRODUCT,
        "generated_utc": iso_z(now),
        "status": "ok" if processed_files else "error",
        "message": None if processed_files else "No GOES-18 GLM files could be processed.",
        "window_minutes": SEARCH_WINDOW_MINUTES,
        "scan_lookback_minutes": SCAN_LOOKBACK_MINUTES,
        "search_radius_nm": SEARCH_RADIUS_NM,
        "alert_level": alert_level,
        "alert_label": alert_label,
        "total_count": len(features),
        "last_strike": feature_summary(last, now) if last else None,
        "nearest_strike": feature_summary(nearest, now) if nearest else None,
        "rings": build_ring_summary(features),
        "counts": totals,
        "processed_file_count": len(processed_files),
        "latest_file": processed_files[-1] if processed_files else None,
        "errors": errors[:10],
        "strikes": [feature_summary(item, now) for item in features[:25]],
        "methodology": (
            "NOAA GOES-18 GLM Level 2 total-lightning groups are counted inside KRNO "
            "10/15/20/25 nm proximity rings over the latest 15 minutes. GLM detects total lightning "
            "and is not a ground-strike network; distances are proximity estimates from GLM group centroids."
        ),
    }


def main() -> None:
    now = utc_now_dt()
    try:
        payload = build_payload(now)
    except Exception as exc:
        payload = build_empty("error", f"GOES GLM lightning fetch failed: {exc}", now)

    text = json.dumps(payload, indent=2)
    for output_path in [DOCS_OUT / "lightning.json", DATA_OUT / "lightning.json"]:
        output_path.write_text(text)
        print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
