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
    for prefix in prefixes:
        for key, value in observations.items():
            if key.startswith(prefix):
                if isinstance(value, dict) and "value" in value:
                    return value["value"]
                return value
    return None


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
    wind_gust_mph = get_ob_value(observations, ["wind_gust"])

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
        "precip_1hr_in": get_ob_value(
            observations,
            [
                "precip_accum_one_hour",
                "precip_accum_since_local_midnight",
                "precip_accum",
            ],
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
