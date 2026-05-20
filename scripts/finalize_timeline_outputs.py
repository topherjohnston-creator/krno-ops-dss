from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


DOCS = Path("docs")
DATA = Path("data")

DOCS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

TIMELINE_PATH = DOCS / "timeline.json"
THREATS_PATH = DOCS / "threats.json"

BLOCK_HOURS = 3
BLOCK_COUNT = 16


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text())
    except Exception:
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2))


def risk_label(risk: int | float | None) -> str:
    if risk is None:
        return "None"

    r = int(risk)

    return {
        0: "None",
        1: "Little to None",
        2: "Minor",
        3: "Moderate",
        4: "Major",
        5: "Extreme",
    }.get(r, "Unknown")


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def get_hours(payload: Any) -> list[dict[str, Any]]:
    """
    Supports common output structures:
      {"hours": [...]}
      {"hourly_timing": [...]}
      {"timeline": [...]}
      [...]
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ["hours", "hourly_timing", "timeline", "hourly"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]

    return []


def fxx_to_block_index(fxx: int) -> int:
    return max(0, min(BLOCK_COUNT - 1, (int(fxx) - 1) // BLOCK_HOURS))


def block_range(block_index: int) -> tuple[int, int]:
    start_fxx = block_index * BLOCK_HOURS + 1
    end_fxx = min((block_index + 1) * BLOCK_HOURS, BLOCK_COUNT * BLOCK_HOURS)
    return start_fxx, end_fxx


def hours_for_block(hours: list[dict[str, Any]], start_fxx: int, end_fxx: int) -> list[dict[str, Any]]:
    output = []
    for hour in hours:
        fxx = safe_int(hour.get("fxx"))
        if fxx is None:
            continue
        if start_fxx <= fxx <= end_fxx:
            output.append(hour)
    return output


def latest_cycle_from_existing(timeline: dict[str, Any], threats: dict[str, Any]) -> tuple[str | None, str | None]:
    cycle_iso = (
        timeline.get("cycle_utc_iso")
        or threats.get("cycle_utc_iso")
        or threats.get("cycle_utc")
        or None
    )

    cycle_label = (
        timeline.get("cycle")
        or threats.get("cycle")
        or None
    )

    return cycle_iso, cycle_label


def valid_time_from_cycle(cycle_iso: str | None, fxx: int) -> str | None:
    if not cycle_iso:
        return None

    try:
        cleaned = cycle_iso.replace("Z", "+00:00")
        cycle_dt = datetime.fromisoformat(cleaned)
        return (cycle_dt + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def matrix_risk(probability: float, impact_level: int) -> int:
    if probability <= 0:
        return 0

    if probability >= 90:
        likelihood = 5
    elif probability >= 66:
        likelihood = 4
    elif probability >= 33:
        likelihood = 3
    elif probability >= 10:
        likelihood = 2
    else:
        likelihood = 1

    matrix = {
        1: {1: 1, 2: 1, 3: 1, 4: 2, 5: 2},
        2: {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
        3: {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
        4: {1: 1, 2: 2, 3: 3, 4: 4, 5: 4},
        5: {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
    }

    impact = max(1, min(5, int(impact_level)))
    return matrix[likelihood][impact]


def finalize_wind(block_hours: list[dict[str, Any]], start_utc: str | None, end_utc: str | None) -> dict[str, Any]:
    valid = [
        h for h in block_hours
        if h.get("status") == "ok" and safe_float(h.get("gust_mph")) is not None
    ]

    if not valid:
        return {
            "prob": None,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": "No data",
            "gust_mph": None,
            "gust_kt": None,
            "source_fxx": None,
            "peak_valid_utc": None,
            "valid_start_utc": start_utc,
            "valid_end_utc": end_utc,
            "driver": "No QMD hourly mean gust available",
            "timing_value": None,
            "timing_method": "Finalized from data/krno_wind_hourly.json",
        }

    peak = max(valid, key=lambda h: safe_float(h.get("gust_mph"), -9999) or -9999)
    gust_mph = safe_float(peak.get("gust_mph"), 0.0) or 0.0
    gust_kt = safe_float(peak.get("gust_kt"), None)

    if gust_mph >= 65:
        level = 5
        risk = 5
        metric = ">65 mph"
    elif gust_mph >= 58:
        level = 4
        risk = 4
        metric = "58-65 mph"
    elif gust_mph >= 45:
        level = 3
        risk = 3
        metric = "45-58 mph"
    elif gust_mph >= 30:
        level = 2
        risk = 2
        metric = "30-45 mph"
    else:
        level = 0
        risk = 0
        metric = "<30 mph"

    if risk == 0:
        driver = f"QMD hourly mean gust {gust_mph:.1f} mph; below wind-impact threshold"
    else:
        driver = f"QMD hourly mean gust {gust_mph:.1f} mph"

    return {
        "prob": None,
        "risk": risk,
        "risk_label": risk_label(risk),
        "level": level,
        "metric": metric,
        "gust_mph": round(gust_mph, 1),
        "gust_kt": round(gust_kt, 1) if gust_kt is not None else None,
        "source_fxx": safe_int(peak.get("fxx")),
        "peak_valid_utc": peak.get("valid_utc"),
        "valid_start_utc": start_utc,
        "valid_end_utc": end_utc,
        "driver": driver,
        "timing_value": round(gust_mph, 1),
        "timing_method": "Timeline timing uses highest QMD hourly mean gust within this 3-hour block",
    }


def extract_snow_candidate(hour: dict[str, Any]) -> dict[str, Any] | None:
    risk_eval = hour.get("risk_evaluation")

    if isinstance(risk_eval, dict):
        best = risk_eval.get("best")
        if isinstance(best, dict):
            return {
                "prob": safe_float(best.get("probability"), 0.0) or 0.0,
                "risk": safe_int(best.get("risk"), 0) or 0,
                "level": safe_int(best.get("impact_level"), 0) or 0,
                "metric": best.get("label") or '0" / hr',
                "driver": (
                    "No snow signal"
                    if (safe_float(best.get("probability"), 0.0) or 0.0) <= 0
                    else f"{safe_float(best.get('probability'), 0.0):.1f}% chance {best.get('label')}"
                ),
                "source_fxx": safe_int(hour.get("fxx")),
                "peak_valid_utc": hour.get("valid_utc"),
                "snow_1hr_in": safe_float(hour.get("display_asnow_in"), None),
            }

    return None


def finalize_snow(block_hours: list[dict[str, Any]], start_utc: str | None, end_utc: str | None) -> dict[str, Any]:
    candidates = []

    for hour in block_hours:
        candidate = extract_snow_candidate(hour)
        if candidate:
            candidates.append(candidate)

    if not candidates:
        return {
            "prob": 0.0,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": '0" / hr',
            "driver": "No snow signal",
            "source_fxx": None,
            "peak_valid_utc": None,
            "valid_start_utc": start_utc,
            "valid_end_utc": end_utc,
            "timing_value": 0.0,
        }

    best = max(candidates, key=lambda c: (c["risk"], c["prob"], c["level"]))

    if best["prob"] <= 0:
        best["risk"] = 0
        best["risk_label"] = "None"
        best["level"] = 0
        best["metric"] = '0" / hr'
        best["driver"] = "No snow signal"
    else:
        best["risk_label"] = risk_label(best["risk"])

    best["valid_start_utc"] = start_utc
    best["valid_end_utc"] = end_utc
    best["timing_value"] = best["prob"]
    best["timing_method"] = "Finalized from data/krno_snow_hourly.json"

    return best


def finalize_lightning(block_hours: list[dict[str, Any]], start_utc: str | None, end_utc: str | None) -> dict[str, Any]:
    valid = []

    for hour in block_hours:
        prob = (
            safe_float(hour.get("prob"), None)
            or safe_float(hour.get("probability"), None)
            or safe_float(hour.get("probability_percent"), None)
            or safe_float(hour.get("lightning_probability"), None)
        )

        if prob is None:
            # Some lightning builders store selected result.
            selected = hour.get("selected")
            if isinstance(selected, dict):
                prob = safe_float(selected.get("probability"), None)

        if prob is None:
            continue

        fxx = safe_int(hour.get("fxx"))
        if fxx is None:
            continue

        if prob >= 75:
            level = 5
        elif prob >= 50:
            level = 4
        elif prob >= 25:
            level = 3
        elif prob >= 5:
            level = 2
        elif prob > 0:
            level = 1
        else:
            level = 0

        risk = matrix_risk(prob, max(level, 1)) if prob > 0 else 0

        if prob >= 75:
            metric = "Lightning chance: >75%"
        elif prob >= 50:
            metric = "Lightning chance: 50-75%"
        elif prob >= 25:
            metric = "Lightning chance: 25-50%"
        elif prob >= 5:
            metric = "Lightning chance: 5-25%"
        elif prob > 0:
            metric = "Lightning chance: <5%"
        else:
            metric = "No lightning signal"

        valid.append(
            {
                "prob": round(prob, 1),
                "risk": risk,
                "level": level,
                "metric": metric,
                "source_fxx": fxx,
                "peak_valid_utc": hour.get("valid_utc"),
                "driver": "No lightning signal" if prob <= 0 else f"{prob:.1f}% chance of lightning",
                "timing_value": prob,
            }
        )

    if not valid:
        return {
            "prob": 0.0,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": "No lightning signal",
            "driver": "No lightning signal",
            "source_fxx": None,
            "peak_valid_utc": None,
            "valid_start_utc": start_utc,
            "valid_end_utc": end_utc,
            "timing_value": 0.0,
        }

    best = max(valid, key=lambda c: (c["risk"], c["prob"], c["level"]))
    best["risk_label"] = risk_label(best["risk"])
    best["valid_start_utc"] = start_utc
    best["valid_end_utc"] = end_utc
    best["timing_method"] = "Finalized from data/krno_lightning_hourly.json"

    return best


def finalize_visibility(block_hours: list[dict[str, Any]], start_utc: str | None, end_utc: str | None) -> dict[str, Any]:
    valid = []

    for hour in block_hours:
        vis = (
            safe_float(hour.get("visibility_sm"), None)
            or safe_float(hour.get("min_visibility_sm"), None)
            or safe_float(hour.get("vis_sm"), None)
        )

        if vis is None:
            severity = hour.get("severity")
            if isinstance(severity, dict):
                vis = safe_float(severity.get("min_visibility_sm"), None)

        if vis is None:
            continue

        fxx = safe_int(hour.get("fxx"))
        if fxx is None:
            continue

        valid.append(
            {
                "visibility_sm": vis,
                "source_fxx": fxx,
                "peak_valid_utc": hour.get("valid_utc"),
            }
        )

    if not valid:
        return {
            "prob": 0.0,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": "No data",
            "visibility_sm": None,
            "source_fxx": None,
            "peak_valid_utc": None,
            "valid_start_utc": start_utc,
            "valid_end_utc": end_utc,
            "driver": "No visibility data available",
            "timing_value": None,
        }

    worst = min(valid, key=lambda h: h["visibility_sm"])
    vis = worst["visibility_sm"]

    if vis < 0.5:
        level = 5
        risk = 5
        metric = "<1/2 SM"
    elif vis < 1.0:
        level = 4
        risk = 4
        metric = "1/2-1 SM"
    elif vis < 3.0:
        level = 3
        risk = 3
        metric = "1-3 SM"
    elif vis < 5.0:
        level = 2
        risk = 2
        metric = "3-5 SM"
    else:
        level = 0
        risk = 0
        metric = ">5 SM"

    if risk == 0:
        driver = f"Visibility remains unrestricted; minimum block visibility {vis:.2f} SM"
    else:
        driver = f"Minimum block visibility {vis:.2f} SM"

    return {
        "prob": 0.0,
        "risk": risk,
        "risk_label": risk_label(risk),
        "level": level,
        "metric": metric,
        "visibility_sm": round(vis, 2),
        "source_fxx": worst["source_fxx"],
        "peak_valid_utc": worst["peak_valid_utc"],
        "valid_start_utc": start_utc,
        "valid_end_utc": end_utc,
        "driver": driver,
        "timing_value": round(10.0 - min(vis, 10.0), 2),
        "timing_method": "Timeline timing uses lowest visibility within this 3-hour block",
    }


def finalize_rain_from_windows(rain_payload: Any, block_index: int, start_utc: str | None, end_utc: str | None) -> dict[str, Any]:
    if not isinstance(rain_payload, dict):
        return empty_rain(start_utc, end_utc)

    candidates = []

    # Common rain script structures.
    possible_lists = []
    for key in ["windows", "six_hour_windows", "hours", "hourly_timing", "rain_windows"]:
        value = rain_payload.get(key)
        if isinstance(value, list):
            possible_lists.append(value)

    diagnostic = rain_payload.get("windows")
    if isinstance(diagnostic, list):
        possible_lists.append(diagnostic)

    start_fxx, end_fxx = block_range(block_index)

    for rows in possible_lists:
        for row in rows:
            if not isinstance(row, dict):
                continue

            row_start = safe_int(row.get("start_fxx"), safe_int(row.get("fxx")))
            row_end = safe_int(row.get("end_fxx"), safe_int(row.get("fxx")))

            if row_start is None or row_end is None:
                continue

            overlaps = not (row_end < start_fxx or row_start > end_fxx)
            if not overlaps:
                continue

            risk_eval = row.get("risk_evaluation")
            if isinstance(risk_eval, dict) and isinstance(risk_eval.get("best"), dict):
                best = risk_eval["best"]
                prob = safe_float(best.get("probability"), 0.0) or 0.0
                risk = safe_int(best.get("risk"), 0) or 0
                level = safe_int(best.get("impact_level"), 0) or 0
                metric = best.get("label") or row.get("metric")
                driver = "No rain/flooding signal" if prob <= 0 else f"{prob:.1f}% chance {metric}"
            else:
                prob = (
                    safe_float(row.get("prob"), None)
                    or safe_float(row.get("probability"), None)
                    or safe_float(row.get("probability_percent"), 0.0)
                    or 0.0
                )
                risk = safe_int(row.get("risk"), 0) or 0
                level = safe_int(row.get("level"), safe_int(row.get("impact_level"), 0)) or 0
                metric = row.get("metric") or row.get("label") or ">0.10 in / 6 hr"
                driver = row.get("driver") or ("No rain/flooding signal" if prob <= 0 else f"{prob:.1f}% chance {metric}")

            candidates.append(
                {
                    "prob": round(prob, 1),
                    "risk": risk,
                    "level": level,
                    "metric": metric,
                    "driver": driver,
                    "source_fxx": row_end,
                    "peak_valid_utc": row.get("valid_utc"),
                    "timing_value": prob,
                }
            )

    if not candidates:
        return empty_rain(start_utc, end_utc)

    best = max(candidates, key=lambda c: (c["risk"], c["prob"], c["level"]))
    best["risk_label"] = risk_label(best["risk"])
    best["valid_start_utc"] = start_utc
    best["valid_end_utc"] = end_utc
    best["timing_method"] = "Finalized from data/nbm_qmd_rain.json"

    if best["prob"] <= 0:
        best["risk"] = 0
        best["risk_label"] = "None"

    return best


def empty_rain(start_utc: str | None, end_utc: str | None) -> dict[str, Any]:
    return {
        "prob": 0.0,
        "risk": 0,
        "risk_label": "None",
        "level": 0,
        "metric": "No rain/flooding signal",
        "driver": "No rain/flooding signal",
        "source_fxx": None,
        "peak_valid_utc": None,
        "valid_start_utc": start_utc,
        "valid_end_utc": end_utc,
        "timing_value": 0.0,
    }


def finalize_fzra(block_hours: list[dict[str, Any]], start_utc: str | None, end_utc: str | None) -> dict[str, Any]:
    candidates = []

    for hour in block_hours:
        prob = (
            safe_float(hour.get("prob"), None)
            or safe_float(hour.get("probability"), None)
            or safe_float(hour.get("probability_percent"), None)
        )

        if prob is None:
            risk_eval = hour.get("risk_evaluation")
            if isinstance(risk_eval, dict) and isinstance(risk_eval.get("best"), dict):
                prob = safe_float(risk_eval["best"].get("probability"), 0.0)

        if prob is None:
            continue

        fxx = safe_int(hour.get("fxx"))
        if fxx is None:
            continue

        # Freezing rain impact levels are intentionally conservative for airport ground ops.
        if prob > 0:
            level = 4
            risk = matrix_risk(prob, level)
            metric = "Freezing rain signal"
            driver = f"{prob:.1f}% chance freezing rain"
        else:
            level = 0
            risk = 0
            metric = "No freezing rain signal"
            driver = "No freezing rain signal"

        candidates.append(
            {
                "prob": round(prob, 1),
                "risk": risk,
                "level": level,
                "metric": metric,
                "driver": driver,
                "source_fxx": fxx,
                "peak_valid_utc": hour.get("valid_utc"),
                "timing_value": prob,
            }
        )

    if not candidates:
        return {
            "prob": 0.0,
            "risk": 0,
            "risk_label": "None",
            "level": 0,
            "metric": "No freezing rain signal",
            "driver": "No freezing rain signal",
            "source_fxx": None,
            "peak_valid_utc": None,
            "valid_start_utc": start_utc,
            "valid_end_utc": end_utc,
            "timing_value": 0.0,
        }

    best = max(candidates, key=lambda c: (c["risk"], c["prob"], c["level"]))
    best["risk_label"] = risk_label(best["risk"])
    best["valid_start_utc"] = start_utc
    best["valid_end_utc"] = end_utc
    best["timing_method"] = "Finalized from data/krno_fzra_hourly.json"

    return best


def load_fzra_hours() -> list[dict[str, Any]]:
    candidates = [
        DATA / "krno_fzra_hourly.json",
        DATA / "nbm_core_fzra.json",
        DATA / "krno_freezing_rain_hourly.json",
    ]

    for path in candidates:
        if path.exists():
            return get_hours(load_json(path, {}))

    return []


def build_final_timeline() -> None:
    timeline = load_json(
        TIMELINE_PATH,
        {
            "site": "KRNO",
            "block_hours": BLOCK_HOURS,
            "blocks": [],
            "block_hazards": [],
        },
    )

    threats = load_json(
        THREATS_PATH,
        {
            "site": "KRNO",
            "threats": {},
            "hazards": [],
        },
    )

    cycle_iso, cycle_label = latest_cycle_from_existing(timeline, threats)

    wind_hours = get_hours(load_json(DATA / "krno_wind_hourly.json", {}))
    snow_hours = get_hours(load_json(DATA / "krno_snow_hourly.json", {}))
    lightning_hours = get_hours(load_json(DATA / "krno_lightning_hourly.json", {}))
    visibility_hours = get_hours(load_json(DATA / "nbm_core_visibility.json", {}))
    rain_payload = load_json(DATA / "nbm_qmd_rain.json", {})
    fzra_hours = load_fzra_hours()

    old_blocks = timeline.get("blocks", [])
    old_hazard_blocks = timeline.get("block_hazards", [])

    new_blocks = []
    new_hazard_blocks = []

    debug = {
        "generated_utc": utc_now(),
        "cycle_utc_iso": cycle_iso,
        "cycle": cycle_label,
        "source_counts": {
            "wind_hours": len(wind_hours),
            "snow_hours": len(snow_hours),
            "lightning_hours": len(lightning_hours),
            "visibility_hours": len(visibility_hours),
            "fzra_hours": len(fzra_hours),
            "rain_payload_keys": list(rain_payload.keys()) if isinstance(rain_payload, dict) else [],
        },
        "blocks": [],
    }

    for i in range(BLOCK_COUNT):
        start_fxx, end_fxx = block_range(i)
        start_utc = valid_time_from_cycle(cycle_iso, start_fxx)
        end_utc = valid_time_from_cycle(cycle_iso, end_fxx)

        old_block = old_blocks[i] if i < len(old_blocks) and isinstance(old_blocks[i], dict) else {}
        old_hazard_block = (
            old_hazard_blocks[i]
            if i < len(old_hazard_blocks) and isinstance(old_hazard_blocks[i], dict)
            else {}
        )

        block = dict(old_block)
        block["block_index"] = i
        block["start_fxx"] = start_fxx
        block["end_fxx"] = end_fxx
        block["valid_start_utc"] = start_utc
        block["valid_end_utc"] = end_utc

        wind_eval = finalize_wind(hours_for_block(wind_hours, start_fxx, end_fxx), start_utc, end_utc)
        snow_eval = finalize_snow(hours_for_block(snow_hours, start_fxx, end_fxx), start_utc, end_utc)
        lightning_eval = finalize_lightning(hours_for_block(lightning_hours, start_fxx, end_fxx), start_utc, end_utc)
        visibility_eval = finalize_visibility(hours_for_block(visibility_hours, start_fxx, end_fxx), start_utc, end_utc)
        rain_eval = finalize_rain_from_windows(rain_payload, i, start_utc, end_utc)
        fzra_eval = finalize_fzra(hours_for_block(fzra_hours, start_fxx, end_fxx), start_utc, end_utc)

        # Preserve other hazards if they exist, but overwrite finalized hazards.
        hazard_block = dict(old_hazard_block)
        hazard_block["WIND"] = wind_eval
        hazard_block["SNOW"] = snow_eval
        hazard_block["LIGHTNING"] = lightning_eval
        hazard_block["VISIBILITY"] = visibility_eval
        hazard_block["RAIN"] = rain_eval
        hazard_block["FZRA"] = fzra_eval

        # Convenience values used by frontend timeline charts.
        block["GST"] = wind_eval.get("gust_mph")
        block["gust_mph"] = wind_eval.get("gust_mph")
        block["gust_kt"] = wind_eval.get("gust_kt")
        block["VIS"] = visibility_eval.get("visibility_sm")
        block["visibility_sm"] = visibility_eval.get("visibility_sm")
        block["SNOW"] = snow_eval.get("snow_1hr_in")
        block["LIGHTNING"] = lightning_eval.get("prob")
        block["RAIN"] = rain_eval.get("prob")
        block["FZRA"] = fzra_eval.get("prob")

        new_blocks.append(block)
        new_hazard_blocks.append(hazard_block)

        debug["blocks"].append(
            {
                "block_index": i,
                "start_fxx": start_fxx,
                "end_fxx": end_fxx,
                "WIND": wind_eval,
                "SNOW": snow_eval,
                "LIGHTNING": lightning_eval,
                "VISIBILITY": visibility_eval,
                "RAIN": rain_eval,
                "FZRA": fzra_eval,
            }
        )

    timeline["site"] = "KRNO"
    timeline["generated_utc"] = utc_now()
    timeline["cycle_utc_iso"] = cycle_iso
    timeline["cycle"] = cycle_label
    timeline["block_hours"] = BLOCK_HOURS
    timeline["blocks"] = new_blocks
    timeline["block_hazards"] = new_hazard_blocks

    write_json(TIMELINE_PATH, timeline)
    write_json(DATA / "finalize_timeline_debug.json", debug)

    print("Finalized docs/timeline.json")
    print("Wrote data/finalize_timeline_debug.json")
    print(f"Wind hours: {len(wind_hours)}")
    print(f"Snow hours: {len(snow_hours)}")
    print(f"Lightning hours: {len(lightning_hours)}")
    print(f"Visibility hours: {len(visibility_hours)}")
    print(f"FZRA hours: {len(fzra_hours)}")


def main() -> None:
    build_final_timeline()


if __name__ == "__main__":
    main()
