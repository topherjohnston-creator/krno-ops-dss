from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


OUT = Path("docs")
OUT.mkdir(exist_ok=True)

STATION = "KRNO"
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


def build_error_obs(message: str) -> dict[str, Any]:
    return {
        "site": STATION,
        "source": "Synoptic API",
        "generated_utc": utc_now(),
        "status": "error",
        "message": message,
        "station": STATION,
        "observed_utc": None,
        "wind_dir_deg": None,
        "wind_speed_kt": None,
        "wind_gust_kt": None,
        "visibility_sm": None,
        "temperature_f": None,
        "dewpoint_f": None,
        "relative_humidity": None,
        "precip_1hr_in": None,
        "raw": {},
    }


def fetch_synoptic_latest(token: str) -> dict[str, Any]:
    params = {
        "stid": STATION,
        "token": token,
        "units": "english,speed|knots,temp|fahrenheit,precip|inch",
        "output": "json",
    }

    response = requests.get(API_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_synoptic_response(payload: dict[str, Any]) -> dict[str, Any]:
    stations = payload.get("STATION", [])
    if not stations:
        return build_error_obs("No station data returned from Synoptic API.")

    station = stations[0]
    observations = station.get("OBSERVATIONS", {})

    observed_utc = get_ob_value(
        observations,
        [
            "date_time",
            "air_temp",
            "wind_speed",
            "wind_direction",
        ],
    )

    obs = {
        "site": STATION,
        "source": "Synoptic API",
        "generated_utc": utc_now(),
        "status": "ok",
        "station": station.get("STID", STATION),
        "name": station.get("NAME"),
        "latitude": station.get("LATITUDE"),
        "longitude": station.get("LONGITUDE"),
        "elevation_ft": station.get("ELEVATION"),
        "observed_utc": observed_utc,
        "wind_dir_deg": get_ob_value(observations, ["wind_direction"]),
        "wind_speed_kt": get_ob_value(observations, ["wind_speed"]),
        "wind_gust_kt": get_ob_value(observations, ["wind_gust"]),
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

    output_path = OUT / "obs.json"
    output_path.write_text(json.dumps(obs, indent=2))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
