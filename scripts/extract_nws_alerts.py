#!/usr/bin/env python3
"""Build the KRNO official-alert JSON used by the static dashboards."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


KRNO_POINT = "39.4986,-119.7681"
POINT_ALERTS_URL = f"https://api.weather.gov/alerts/active?point={KRNO_POINT}"
AWW_PRODUCTS_URL = "https://api.weather.gov/products/types/AWW"
AWW_LOOKBACK_HOURS = 8
OUTPUT_PATHS = (Path("docs/alerts.json"), Path("data/alerts.json"))

HEADERS = {
    "Accept": "application/geo+json, application/json",
    "User-Agent": "KRNO Ops DSS alerts generator (https://github.com/topherjohnston-creator/krno-ops-dss)",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        try:
            return parsedate_to_datetime(value).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None


def get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def cap_alert(feature: dict[str, Any]) -> dict[str, Any]:
    props = feature.get("properties") or {}
    event = props.get("event") or "NWS Alert"
    ends = props.get("ends") or props.get("expires")
    return {
        "id": feature.get("id") or props.get("id") or props.get("@id") or f"{event}-{props.get('sent', '')}",
        "event": event,
        "headline": props.get("headline") or event,
        "description": props.get("description") or "",
        "instruction": props.get("instruction") or "",
        "sent": props.get("sent"),
        "effective": props.get("effective") or props.get("sent"),
        "expires": ends,
        "ends": ends,
        "source": "NWS CAP",
        "uri": feature.get("id") or props.get("@id"),
    }


def parse_aww_expiration(product_text: str, issuance_time: str | None) -> str | None:
    match = re.search(r"\b[A-Z]{2}[CZ]\d{3}(?:-\d{3})*-(\d{2})(\d{2})(\d{2})-", product_text or "")
    issued = parse_time(issuance_time)
    if not match or not issued:
        return None
    day, hour, minute = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    expires = datetime(issued.year, issued.month, day, hour, minute, tzinfo=timezone.utc)
    if expires + timedelta(days=15) < issued:
        month = 1 if issued.month == 12 else issued.month + 1
        year = issued.year + 1 if issued.month == 12 else issued.year
        expires = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    return expires.isoformat().replace("+00:00", "Z")


def is_aww_for_krno(product_text: str) -> bool:
    return bool(re.search(r"AWWRNO|Reno-Tahoe International Airport|/RNO/|KRNO", product_text or "", re.I))


def aww_alert(product: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any] | None:
    text = detail.get("productText") or ""
    if not is_aww_for_krno(text):
        return None
    issuance_time = detail.get("issuanceTime") or product.get("issuanceTime")
    expires = parse_aww_expiration(text, issuance_time)
    hazard_lines = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"airport weather warning|lightning|wind gust|hail|torrential rainfall", line, re.I)
    ][:6]
    return {
        "id": detail.get("id") or product.get("id"),
        "event": "Airport Weather Warning",
        "headline": "Airport Weather Warning for Reno-Tahoe International Airport",
        "description": " ".join(hazard_lines),
        "instruction": "Review airport weather warning and local ramp procedures.",
        "sent": issuance_time,
        "effective": issuance_time,
        "expires": expires,
        "ends": expires,
        "source": "NWS AWW",
        "uri": detail.get("@id") or f"https://api.weather.gov/products/{product.get('id')}",
        "productText": text,
    }


def is_current(alert: dict[str, Any], now: datetime) -> bool:
    ends = parse_time(alert.get("ends") or alert.get("expires"))
    sent = parse_time(alert.get("sent") or alert.get("effective"))
    if ends and ends > now:
        return True
    if alert.get("event") == "Airport Weather Warning" and sent:
        return now - sent <= timedelta(hours=AWW_LOOKBACK_HOURS)
    return ends is None


def priority(alert: dict[str, Any]) -> int:
    text = " ".join(str(alert.get(key) or "") for key in ("event", "headline", "description"))
    if re.search(r"airport weather warning", text, re.I):
        return 100
    if re.search(r"severe thunderstorm warning|tornado warning|flash flood warning|snow squall warning", text, re.I):
        return 90
    if re.search(r"warning", text, re.I):
        return 70
    if re.search(r"watch", text, re.I):
        return 50
    if re.search(r"advisory|special weather statement", text, re.I):
        return 30
    return 10


def collect_alerts() -> dict[str, Any]:
    now = utcnow()
    alerts: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        point_data = get_json(POINT_ALERTS_URL)
        alerts.extend(cap_alert(feature) for feature in point_data.get("features", []))
    except (HTTPError, URLError, TimeoutError) as exc:
        errors.append(f"point alerts: {exc}")

    try:
        aww_data = get_json(AWW_PRODUCTS_URL)
        products = [
            product
            for product in aww_data.get("@graph", [])
            if product.get("productCode") == "AWW"
            and product.get("issuingOffice") == "KREV"
            and (issued := parse_time(product.get("issuanceTime")))
            and now - issued <= timedelta(hours=AWW_LOOKBACK_HOURS)
        ][:6]
        for product in products:
            try:
                detail = get_json(f"https://api.weather.gov/products/{product.get('id')}")
                alert = aww_alert(product, detail)
                if alert:
                    alerts.append(alert)
            except (HTTPError, URLError, TimeoutError) as exc:
                errors.append(f"AWW detail {product.get('id')}: {exc}")
    except (HTTPError, URLError, TimeoutError) as exc:
        errors.append(f"AWW products: {exc}")

    merged: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        if not is_current(alert, now):
            continue
        key = str(alert.get("id") or f"{alert.get('event')}-{alert.get('sent')}")
        merged[key] = alert

    active = sorted(merged.values(), key=priority, reverse=True)
    return {
        "site": "KRNO",
        "point": KRNO_POINT,
        "generated_utc": now.isoformat().replace("+00:00", "Z"),
        "sources": ["NWS CAP active point alerts", "NWS AWW products"],
        "errors": errors,
        "alerts": active,
    }


def main() -> None:
    payload = collect_alerts()
    for path in OUTPUT_PATHS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload['alerts'])} active alerts to {', '.join(str(p) for p in OUTPUT_PATHS)}")
    for alert in payload["alerts"]:
        print(f"- {alert.get('event')}: {alert.get('headline')}")
    if payload["errors"]:
        print("Warnings:")
        for error in payload["errors"]:
            print(f"- {error}")


if __name__ == "__main__":
    main()
