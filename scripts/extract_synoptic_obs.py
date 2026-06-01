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
    """Return the newest timestamp from the live Synoptic observation fields."""
    preferred_keys = [
        "wind_speed_value_1",
        "wind_direction_value_1",
        "air_temp_value_1",
        "dew_point_temperature_value_1d",
        "relative_humidity_value_1",
        "visibility_value_1",
        "metar_value_1",
    ]

    times: list[str] = []
    for key in preferred_keys:
        value = observations.get(key)
        if isinstance(value, dict) and "date_time" in value:
            times.append(value["date_time"])

    if times:
        return max(times, key=iso_timestamp)

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


def display_weather_text(value: Any) -> str:
    if not value:
        return "None"
    return str(value).replace("_", " ").strip().title()


def sky_from_summary(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip().lower()
    if "clear" in text:
        return "Clear"
    if "few" in text:
        return "Few"
    if "scattered" in text:
        return "Scattered"
    if "broken" in text:
        return "Broken"
    if "overcast" in text:
        return "Overcast"
    return display_weather_text(text)


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
        "sky_condition": None,
        "present_weather": "None",
        "metar": None,
        "raw": raw or {},
    }


def fetch_synoptic_latest(token: str) -> dict[str, Any]:
    params = {
        "stid": SITE,
        "token": token,
        "units": "english",
        "output": "json",
        "vars": ",".join(
            [
                "air_temp",
                "dew_point_temperature",
                "relative_humidity",
                "wind_speed",
                "wind_direction",
                "wind_gust",
                "visibility",
                "metar",
                "weather_condition",
                "weather_summary",
                "precip_accum_one_hour",
            ]
        ),
    }

    response = requests.get(API_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


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
    generated_utc = utc_now()
    field_times = {
        key: value.get("date_time")
        for key, value in observations.items()
        if isinstance(value, dict) and value.get("date_time")
    }
    wind_speed_mph = recent_ob_value(observations, ["wind_speed"], observed_utc, 20)
    wind_gust_mph = recent_ob_value(observations, ["wind_gust"], observed_utc, 15)
    sky_summary = recent_ob_value(observations, ["weather_summary"], observed_utc, 20)
    present_weather = recent_ob_value(observations, ["weather_condition"], observed_utc, 20)

    obs = {
        "site": SITE,
        "source": "Synoptic API",
        "generated_utc": generated_utc,
        "status": "ok",
        "station": station.get("STID"),
        "name": station.get("NAME"),
        "latitude": station.get("LATITUDE"),
        "longitude": station.get("LONGITUDE"),
        "elevation_ft": station.get("ELEVATION"),
        "observed_utc": observed_utc,
        "wind_dir_deg": recent_ob_value(observations, ["wind_direction"], observed_utc, 20),
        "wind_speed_kt": mph_to_kt(wind_speed_mph),
        "wind_gust_kt": mph_to_kt(wind_gust_mph),
        "wind_speed_mph": wind_speed_mph,
        "wind_gust_mph": wind_gust_mph,
        "visibility_sm": recent_ob_value(observations, ["visibility"], observed_utc, 20),
        "temperature_f": recent_ob_value(observations, ["air_temp"], observed_utc, 20),
        "dewpoint_f": recent_ob_value(observations, ["dew_point_temperature"], observed_utc, 20),
        "relative_humidity": recent_ob_value(observations, ["relative_humidity"], observed_utc, 20),
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
        "sky_condition": sky_from_summary(sky_summary),
        "present_weather": display_weather_text(present_weather),
        "metar": get_ob_value(observations, ["metar"]),
        "field_times_utc": field_times,
        "age_minutes_at_build": round((iso_timestamp(generated_utc) - iso_timestamp(observed_utc)) / 60, 1) if observed_utc else None,
        "refresh_note": "Synoptic latest endpoint queried by GitHub Actions. No secondary observation fallback is used.",
        "raw": station,
    }

    return obs


def main() -> None:
    token = os.getenv("SYNOPTIC_TOKEN")

    if not token:
        obs = build_error_obs("Missing SYNOPTIC_TOKEN environment variable. Observation generation is Synoptic-only.")
    else:
        try:
            payload = fetch_synoptic_latest(token)
            obs = parse_synoptic_response(payload)
        except Exception as exc:
            obs = build_error_obs(f"Synoptic API fetch failed: {exc}")

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
