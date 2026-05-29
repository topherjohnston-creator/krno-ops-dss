from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DOCS_OUT = Path("docs")
DATA_OUT = Path("data")
DOCS_OUT.mkdir(exist_ok=True)
DATA_OUT.mkdir(exist_ok=True)

SITE = "KRNO"
KRNO_LAT = 39.4991
KRNO_LON = -119.7681

API_URL = "https://api.synopticdata.com/v2/stations/latest"
AWC_METAR_URL = "https://aviationweather.gov/api/data/metar"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_ob_value(observations: dict[str, Any], prefixes: list[str]) -> Any:
    """Return first matching Synoptic observation value by prefix."""
    entry = get_ob_entry(observations, prefixes)
    if isinstance(entry, dict) and "value" in entry:
        return entry["value"]
    return entry


def get_ob_entry(observations: dict[str, Any], prefixes: list[str]) -> Any:
    """Return first matching Synoptic observation record by prefix."""
    for prefix in prefixes:
        for key, value in observations.items():
            if key.startswith(prefix):
                return value
    return None


def recent_ob_value(
    observations: dict[str, Any],
    prefixes: list[str],
    reference_time: str | None,
    max_age_minutes: int,
) -> Any:
    entry = get_ob_entry(observations, prefixes)
    if not isinstance(entry, dict):
        return entry
    entry_time = entry.get("date_time")
    if entry_time and reference_time:
        age_seconds = iso_timestamp(reference_time) - iso_timestamp(entry_time)
        if age_seconds > max_age_minutes * 60:
            return None
    return entry.get("value")


def get_ob_time(observations: dict[str, Any]) -> str | None:
    """Return a reliable timestamp from the most relevant current observations."""
    preferred_keys = [
        "air_temp_value_1",
        "wind_speed_value_1",
        "wind_direction_value_1",
        "visibility_value_1",
        "metar_value_1",
        "relative_humidity_value_1",
        "dew_point_temperature_value_1d",
    ]

    for key in preferred_keys:
        value = observations.get(key)
        if isinstance(value, dict) and "date_time" in value:
            return value["date_time"]

    for value in observations.values():
        if isinstance(value, dict) and "date_time" in value:
            return value["date_time"]

    return None


def mph_to_kt(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return round(float(value) / 1.15078)
    except (TypeError, ValueError):
        return None


def kt_to_mph(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) * 1.15078, 1)
    except (TypeError, ValueError):
        return None


def c_to_f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) * 9 / 5 + 32, 2)
    except (TypeError, ValueError):
        return None


def m_to_ft(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) * 3.28084, 1)
    except (TypeError, ValueError):
        return None


def relative_humidity_from_c(temp_c: Any, dewpoint_c: Any) -> float | None:
    try:
        temp = float(temp_c)
        dewpoint = float(dewpoint_c)
    except (TypeError, ValueError):
        return None
    vapor_pressure = 6.112 * pow(2.718281828, (17.67 * dewpoint) / (dewpoint + 243.5))
    saturation = 6.112 * pow(2.718281828, (17.67 * temp) / (temp + 243.5))
    if saturation <= 0:
        return None
    return round(max(0, min(100, 100 * vapor_pressure / saturation)), 2)


def parse_awc_time(record: dict[str, Any]) -> str | None:
    obs_time = record.get("obsTime")
    if obs_time is not None:
        try:
            return datetime.fromtimestamp(float(obs_time), timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OSError):
            pass
    report_time = record.get("reportTime")
    if isinstance(report_time, str) and report_time:
        return report_time.replace("+00:00", "Z")
    return None


def iso_timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def build_error_obs(message: str, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "site": SITE,
        "source": "Synoptic API",
        "generated_utc": utc_now(),
        "status": "error",
        "message": message,
        "station": SITE,
        "observed_utc": None,
        "wind_dir_deg": None,
        "wind_speed_kt": None,
        "wind_gust_kt": None,
        "visibility_sm": None,
        "temperature_f": None,
        "dewpoint_f": None,
        "relative_humidity": None,
        "precip_1hr_in": None,
        "metar": None,
        "raw": raw or {},
    }


def fetch_synoptic_latest(token: str) -> dict[str, Any]:
    params = {
        "radius": f"{KRNO_LAT},{KRNO_LON},10",
        "token": token,
        "units": "english",
        "output": "json",
    }

    response = requests.get(API_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_awc_metar() -> dict[str, Any] | None:
    params = {
        "ids": SITE,
        "format": "json",
        "hours": 2,
    }
    response = requests.get(AWC_METAR_URL, params=params, timeout=30)
    response.raise_for_status()
    records = response.json()
    if not isinstance(records, list) or not records:
        return None
    return records[0]


def parse_awc_metar(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    observed_utc = parse_awc_time(record)
    if not observed_utc:
        return None
    wind_speed_mph = kt_to_mph(record.get("wspd"))
    wind_gust_mph = kt_to_mph(record.get("wgst"))
    visibility_raw = str(record.get("visib") or "").replace("+", "")
    try:
        visibility_sm = float(visibility_raw)
    except ValueError:
        visibility_sm = None
    temp_f = c_to_f(record.get("temp"))
    dewpoint_f = c_to_f(record.get("dewp"))
    return {
        "site": SITE,
        "source": "Aviation Weather Center METAR API",
        "generated_utc": utc_now(),
        "status": "ok",
        "station": record.get("icaoId") or SITE,
        "name": record.get("name"),
        "latitude": record.get("lat"),
        "longitude": record.get("lon"),
        "elevation_ft": m_to_ft(record.get("elev")),
        "observed_utc": observed_utc,
        "wind_dir_deg": record.get("wdir"),
        "wind_speed_kt": record.get("wspd"),
        "wind_gust_kt": record.get("wgst"),
        "wind_speed_mph": wind_speed_mph,
        "wind_gust_mph": wind_gust_mph,
        "visibility_sm": visibility_sm,
        "temperature_f": temp_f,
        "dewpoint_f": dewpoint_f,
        "relative_humidity": relative_humidity_from_c(record.get("temp"), record.get("dewp")),
        "precip_1hr_in": None,
        "metar": record.get("rawOb"),
        "raw": {"awc": record},
    }


def merge_awc_if_newer(synoptic_obs: dict[str, Any], awc_obs: dict[str, Any] | None) -> dict[str, Any]:
    if not awc_obs or awc_obs.get("status") != "ok":
        return synoptic_obs
    if synoptic_obs.get("status") != "ok":
        return awc_obs
    if iso_timestamp(awc_obs.get("observed_utc")) <= iso_timestamp(synoptic_obs.get("observed_utc")):
        return synoptic_obs

    merged = dict(synoptic_obs)
    synoptic_raw = synoptic_obs.get("raw", {})
    merged.update({
        "source": "Synoptic API + Aviation Weather Center METAR API",
        "generated_utc": utc_now(),
        "status": "ok",
        "station": awc_obs.get("station") or synoptic_obs.get("station"),
        "name": awc_obs.get("name") or synoptic_obs.get("name"),
        "latitude": awc_obs.get("latitude") or synoptic_obs.get("latitude"),
        "longitude": awc_obs.get("longitude") or synoptic_obs.get("longitude"),
        "elevation_ft": awc_obs.get("elevation_ft") or synoptic_obs.get("elevation_ft"),
        "observed_utc": awc_obs.get("observed_utc"),
        "wind_dir_deg": awc_obs.get("wind_dir_deg"),
        "wind_speed_kt": awc_obs.get("wind_speed_kt"),
        "wind_gust_kt": awc_obs.get("wind_gust_kt"),
        "wind_speed_mph": awc_obs.get("wind_speed_mph"),
        "wind_gust_mph": awc_obs.get("wind_gust_mph"),
        "visibility_sm": awc_obs.get("visibility_sm"),
        "temperature_f": awc_obs.get("temperature_f"),
        "dewpoint_f": awc_obs.get("dewpoint_f"),
        "relative_humidity": awc_obs.get("relative_humidity"),
        "metar": awc_obs.get("metar"),
        "raw": {
            "synoptic": synoptic_raw,
            "awc": awc_obs.get("raw", {}).get("awc"),
        },
    })
    if awc_obs.get("precip_1hr_in") is not None:
        merged["precip_1hr_in"] = awc_obs.get("precip_1hr_in")
    return merged


def parse_synoptic_response(payload: dict[str, Any]) -> dict[str, Any]:
    stations = payload.get("STATION", [])

    if not stations:
        return build_error_obs("No stations returned within 10 miles of KRNO.", payload)

    # Prefer KRNO if returned; otherwise use the first/nearest returned station.
    station = None
    for candidate in stations:
        if candidate.get("STID") == "KRNO":
            station = candidate
            break

    if station is None:
        station = stations[0]

    observations = station.get("OBSERVATIONS", {})
    observed_utc = get_ob_time(observations)
    wind_speed_mph = get_ob_value(observations, ["wind_speed"])
    wind_gust_mph = recent_ob_value(observations, ["wind_gust"], observed_utc, 15)

    obs = {
        "site": SITE,
        "source": "Synoptic API",
        "generated_utc": utc_now(),
        "status": "ok",
        "station": station.get("STID"),
        "name": station.get("NAME"),
        "latitude": station.get("LATITUDE"),
        "longitude": station.get("LONGITUDE"),
        "elevation_ft": station.get("ELEVATION"),
        "observed_utc": observed_utc,
        "wind_dir_deg": get_ob_value(observations, ["wind_direction"]),
        "wind_speed_kt": mph_to_kt(wind_speed_mph),
        "wind_gust_kt": mph_to_kt(wind_gust_mph),
        "wind_speed_mph": wind_speed_mph,
        "wind_gust_mph": wind_gust_mph,
        "visibility_sm": get_ob_value(observations, ["visibility"]),
        "temperature_f": get_ob_value(observations, ["air_temp"]),
        "dewpoint_f": get_ob_value(observations, ["dew_point_temperature"]),
        "relative_humidity": get_ob_value(observations, ["relative_humidity"]),
        "precip_1hr_in": recent_ob_value(
            observations,
            [
                "precip_accum_one_hour",
                "precip_accum_since_local_midnight",
                "precip_accum",
            ],
            observed_utc,
            90,
        ),
        "metar": get_ob_value(observations, ["metar"]),
        "raw": station,
    }

    return obs


def main() -> None:
    token = os.getenv("SYNOPTIC_TOKEN")

    if not token:
        obs = build_error_obs(
            "Missing SYNOPTIC_TOKEN environment variable. Add it as a GitHub Actions secret."
        )
    else:
        try:
            payload = fetch_synoptic_latest(token)
            obs = parse_synoptic_response(payload)
        except Exception as exc:
            obs = build_error_obs(f"Synoptic API fetch failed: {exc}")

    try:
        awc_obs = parse_awc_metar(fetch_awc_metar())
        obs = merge_awc_if_newer(obs, awc_obs)
    except Exception as exc:
        if obs.get("status") != "ok":
            obs = build_error_obs(f"Synoptic and AWC METAR fetch failed: {exc}", obs.get("raw"))
        else:
            obs["awc_fallback_error"] = str(exc)

    payload = json.dumps(obs, indent=2)
    output_paths = [
        DOCS_OUT / "obs.json",
        DOCS_OUT / "observations.json",
        DATA_OUT / "observations.json",
    ]
    for output_path in output_paths:
        output_path.write_text(payload)
        print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
