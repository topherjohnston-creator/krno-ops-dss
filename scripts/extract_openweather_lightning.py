from __future__ import annotations

import json
import math
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DOCS_OUT = Path("docs")
DATA_OUT = Path("data")
DOCS_OUT.mkdir(exist_ok=True)
DATA_OUT.mkdir(exist_ok=True)

SITE = "KRNO"
SITE_NAME = "Reno-Tahoe International Airport"
KRNO_LAT = 39.4991
KRNO_LON = -119.7681

RING_LIMITS_NM = [10, 15, 20, 25]
SEARCH_RADIUS_NM = 25
SEARCH_RADIUS_KM = SEARCH_RADIUS_NM * 1.852
SEARCH_WINDOW_MINUTES = 45

API_URL = os.getenv(
    "OPENWEATHER_LIGHTNING_URL",
    "https://demo.openweathermap.org/lightning/1.0/data",
)


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


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def build_empty(status: str, message: str) -> dict[str, Any]:
    now = utc_now_dt()
    return {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": KRNO_LAT,
        "lon": KRNO_LON,
        "source": "OpenWeather Lightning API",
        "generated_utc": iso_z(now),
        "status": status,
        "message": message,
        "window_minutes": SEARCH_WINDOW_MINUTES,
        "search_radius_nm": SEARCH_RADIUS_NM,
        "alert_level": "unavailable" if status != "ok" else "none",
        "alert_label": "Lightning feed unavailable" if status != "ok" else "No lightning detected",
        "last_strike": None,
        "nearest_strike": None,
        "rings": build_ring_summary([]),
    }


def build_ring_summary(strikes: list[dict[str, Any]]) -> dict[str, Any]:
    rings: dict[str, Any] = {}
    for limit in RING_LIMITS_NM:
        in_ring = [s for s in strikes if s["distance_nm"] <= limit]
        latest = max((s for s in in_ring if s.get("datetime")), key=lambda s: s["datetime"], default=None)
        rings[f"within_{limit}_nm"] = {
            "count": len(in_ring),
            "last_utc": iso_z(latest["datetime"]) if latest else None,
        }
    return rings


def strike_summary(strike: dict[str, Any], now: datetime) -> dict[str, Any]:
    age_minutes = (now - strike["datetime"]).total_seconds() / 60 if strike.get("datetime") else None
    return {
        "datetime": iso_z(strike["datetime"]) if strike.get("datetime") else None,
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "distance_nm": round(strike["distance_nm"], 1),
        "bearing_degrees": round(strike["bearing_degrees"]),
        "bearing_cardinal": strike["bearing_cardinal"],
        "quality": strike.get("quality"),
        "error_km": strike.get("error_km"),
    }


def classify_alert(strikes: list[dict[str, Any]]) -> tuple[str, str]:
    if not strikes:
        return "none", "No lightning detected"

    nearest = min(strikes, key=lambda s: s["distance_nm"])
    dist = nearest["distance_nm"]
    if dist <= 10:
        return "inside_10_nm", "Lightning inside 10 nm"
    if dist <= 15:
        return "monitor_15_nm", "Lightning inside 15 nm"
    if dist <= 20:
        return "monitor_20_nm", "Lightning inside 20 nm"
    if dist <= 25:
        return "monitor_25_nm", "Lightning inside 25 nm"
    return "none", "No lightning detected"


def fetch_openweather_lightning(api_key: str, now: datetime) -> dict[str, Any]:
    start = now - timedelta(minutes=SEARCH_WINDOW_MINUTES)
    params = {
        "lat": KRNO_LAT,
        "lon": KRNO_LON,
        "radius": round(SEARCH_RADIUS_KM, 2),
        "start_date": iso_z(start),
        "end_date": iso_z(now),
        "apikey": api_key,
    }
    url = f"{API_URL}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "KRNO-DSS-Dashboard"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def build_payload(raw: dict[str, Any], now: datetime) -> dict[str, Any]:
    strikes: list[dict[str, Any]] = []
    for item in raw.get("lightnings", []) or []:
        strike_dt = parse_dt(item.get("datetime"))
        lat = item.get("lat")
        lon = item.get("lon")
        if strike_dt is None or lat is None or lon is None:
            continue
        try:
            strike_lat = float(lat)
            strike_lon = float(lon)
        except (TypeError, ValueError):
            continue
        distance_nm = haversine_nm(KRNO_LAT, KRNO_LON, strike_lat, strike_lon)
        if distance_nm > SEARCH_RADIUS_NM:
            continue
        bearing = bearing_degrees(KRNO_LAT, KRNO_LON, strike_lat, strike_lon)
        strikes.append(
            {
                "datetime": strike_dt,
                "distance_nm": distance_nm,
                "bearing_degrees": bearing,
                "bearing_cardinal": bearing_cardinal(bearing),
                "quality": item.get("quality"),
                "error_km": item.get("error"),
            }
        )

    strikes.sort(key=lambda s: s["datetime"], reverse=True)
    last = strikes[0] if strikes else None
    nearest = min(strikes, key=lambda s: s["distance_nm"], default=None)
    alert_level, alert_label = classify_alert(strikes)

    return {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": KRNO_LAT,
        "lon": KRNO_LON,
        "source": "OpenWeather Lightning API",
        "generated_utc": iso_z(now),
        "status": "ok",
        "window_minutes": SEARCH_WINDOW_MINUTES,
        "search_radius_nm": SEARCH_RADIUS_NM,
        "alert_level": alert_level,
        "alert_label": alert_label,
        "total_count": len(strikes),
        "last_strike": strike_summary(last, now) if last else None,
        "nearest_strike": strike_summary(nearest, now) if nearest else None,
        "rings": build_ring_summary(strikes),
        "strikes": [strike_summary(s, now) for s in strikes[:25]],
    }


def main() -> None:
    api_key = os.getenv("OPENWEATHER_API_KEY")
    now = utc_now_dt()

    if not api_key:
        payload = build_empty(
            "missing_key",
            "Missing OPENWEATHER_API_KEY environment variable. Add it as a GitHub Actions secret.",
        )
    else:
        try:
            raw = fetch_openweather_lightning(api_key, now)
            payload = build_payload(raw, now)
        except Exception as exc:
            payload = build_empty("error", f"OpenWeather lightning fetch failed: {exc}")

    text = json.dumps(payload, indent=2)
    for output_path in [DOCS_OUT / "lightning.json", DATA_OUT / "lightning.json"]:
        output_path.write_text(text)
        print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
