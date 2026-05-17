from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from herbie import Herbie


DOCS = Path("docs")
DATA = Path("data")
DOCS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

KRNO_LAT = 39.4991
KRNO_LON = -119.7681

MPS_TO_MPH = 2.2369362921
MPS_TO_KT = 1.9438444924


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_cycle_utc() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def extract_point_value(cycle: datetime, fxx: int, search: str) -> float:
    H = Herbie(
        cycle.strftime("%Y-%m-%d %H:%M"),
        model="nbm",
        product="co",
        fxx=fxx,
    )

    ds = H.xarray(search)
    point = ds.herbie.nearest_points(points=[(KRNO_LON, KRNO_LAT)])

    data_vars = list(point.data_vars)
    if not data_vars:
        raise RuntimeError(f"No data variables returned for search: {search}")

    var = data_vars[0]
    return float(point[var].values.squeeze())


def wind_impact_level(gust_mph: float) -> int:
    if gust_mph > 65:
        return 5
    if gust_mph >= 58:
        return 4
    if gust_mph >= 45:
        return 3
    if gust_mph >= 30:
        return 2
    return 1


def wind_risk_level(impact_level: int, probability: float) -> int:
    """Simple temporary wind risk logic until direct NBM exceedance probs are wired.

    For now, probability is inferred from deterministic 24-hr GUST magnitude.
    This will be replaced with direct NBM probability products once identified.
    """
    if probability >= 90:
        likelihood = "very_likely"
    elif probability >= 66:
        likelihood = "likely"
    elif probability >= 33:
        likelihood = "as_likely"
    elif probability >= 10:
        likelihood = "unlikely"
    else:
        likelihood = "very_unlikely"

    matrix = {
        "very_likely": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
        "likely": {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
        "as_likely": {1: 1, 2: 1, 3: 2, 4: 3, 5: 4},
        "unlikely": {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
        "very_unlikely": {1: 1, 2: 1, 3: 1, 4: 2, 5: 2},
    }

    return matrix[likelihood][impact_level]


def inferred_probability_from_gust(gust_mph: float) -> int:
    """Temporary placeholder until direct NBM wind exceedance probabilities are added."""
    if gust_mph >= 65:
        return 80
    if gust_mph >= 58:
        return 70
    if gust_mph >= 45:
        return 60
    if gust_mph >= 30:
        return 50
    return 20


def wind_metric(gust_mph: float) -> str:
    if gust_mph > 65:
        return ">65 mph"
    if gust_mph >= 58:
        return "58-65 mph"
    if gust_mph >= 45:
        return "45-58 mph"
    if gust_mph >= 30:
        return "30-45 mph"
    return "<30 mph"


def block_risk_from_hourly_gust(gust_mph: float, max_24hr_impact: int) -> dict[str, int]:
    """Use hourly wind only to shape timing.

    The 24-hour GUST product controls peak severity. Hourly values control
    where risk appears in time. We cap hourly block risk at the 24-hour
    impact-derived risk envelope.
    """
    hourly_impact = wind_impact_level(gust_mph)

    if hourly_impact >= 4:
        risk = min(4, max_24hr_impact)
    elif hourly_impact == 3:
        risk = min(3, max_24hr_impact)
    elif hourly_impact == 2:
        risk = min(2, max_24hr_impact)
    else:
        risk = 1

    return {
        "prob": inferred_probability_from_gust(gust_mph),
        "risk": risk,
        "level": hourly_impact,
    }


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def main() -> None:
    cycle = latest_cycle_utc()
    generated = utc_now()

    print(f"Building real wind output from NBM cycle {cycle:%Y-%m-%d %HZ}")

    # 24-hour max gust product for severity.
    gust24_mps = extract_point_value(
        cycle=cycle,
        fxx=24,
        search=":GUST:10 m above ground:24 hour fcst:$",
    )
    gust24_mph = gust24_mps * MPS_TO_MPH
    gust24_kt = gust24_mps * MPS_TO_KT

    impact = wind_impact_level(gust24_mph)
    probability = inferred_probability_from_gust(gust24_mph)
    risk = wind_risk_level(impact, probability)

    # Hourly gusts for timing.
    hourly = []
    for fxx in range(1, 25):
        gust_mps = extract_point_value(
            cycle=cycle,
            fxx=fxx,
            search=":GUST:10 m above ground:",
        )
        gust_mph = gust_mps * MPS_TO_MPH
        gust_kt = gust_mps * MPS_TO_KT
        hourly.append(
            {
                "fxx": fxx,
                "valid_utc": (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z"),
                "gust_mps": round(gust_mps, 1),
                "gust_mph": round(gust_mph, 1),
                "gust_kt": round(gust_kt, 1),
            }
        )

    # Determine peak timing from hourly shape.
    peak_hour = max(hourly, key=lambda item: item["gust_mph"])
    peak_start_fxx = max(1, int(peak_hour["fxx"]) - 1)
    peak_end_fxx = min(24, int(peak_hour["fxx"]) + 1)

    # Update threats.json while preserving non-wind mock hazards.
    threats_path = DOCS / "threats.json"
    threats_payload = load_json(
        threats_path,
        {
            "site": "KRNO",
            "valid_period": "next_48_hours",
            "threats": {},
        },
    )

    threats_payload["generated_utc"] = generated
    threats_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    threats_payload["cycle"] = f"NBM {cycle.strftime('%HZ')}"
    threats_payload.setdefault("threats", {})

    threats_payload["threats"]["WIND"] = {
        "prob": probability,
        "risk": risk,
        "level": impact,
        "metric": wind_metric(gust24_mph),
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "driver": f"NBM 24-hr max gust {gust24_mph:.1f} mph / {gust24_kt:.1f} kt",
        "g24_mps": round(gust24_mps, 1),
        "g24_mph": round(gust24_mph, 1),
        "g24_kt": round(gust24_kt, 1),
        "methodology": "Wind severity uses NBM 24-hour GUST. Hourly GUST only shapes timing.",
    }

    # Update hazards array if present.
    hazards = threats_payload.get("hazards")
    if isinstance(hazards, list):
        found = False
        for hazard in hazards:
            if hazard.get("id") == "WIND" or hazard.get("id") == "Wind":
                hazard.update(
                    {
                        "id": "WIND",
                        "name": "Wind",
                        "risk_level": risk,
                        "impact_level": impact,
                        "probability": probability,
                        "peak_start_fxx": peak_start_fxx,
                        "peak_end_fxx": peak_end_fxx,
                        "metric": wind_metric(gust24_mph),
                        "driver": f"NBM 24-hr max gust {gust24_mph:.1f} mph",
                    }
                )
                found = True
                break

        if not found:
            hazards.append(
                {
                    "id": "WIND",
                    "name": "Wind",
                    "risk_level": risk,
                    "impact_level": impact,
                    "probability": probability,
                    "peak_start_fxx": peak_start_fxx,
                    "peak_end_fxx": peak_end_fxx,
                    "metric": wind_metric(gust24_mph),
                    "driver": f"NBM 24-hr max gust {gust24_mph:.1f} mph",
                }
            )

    threats_path.write_text(json.dumps(threats_payload, indent=2))

    # Update timeline.json WIND fields while preserving other hazards.
    timeline_path = DOCS / "timeline.json"
    timeline_payload = load_json(
        timeline_path,
        {
            "site": "KRNO",
            "block_hours": 3,
            "blocks": [],
            "block_hazards": [],
        },
    )

    timeline_payload["generated_utc"] = generated
    timeline_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    timeline_payload["cycle"] = f"NBM {cycle.strftime('%HZ')}"

    blocks = timeline_payload.setdefault("blocks", [])
    block_hazards = timeline_payload.setdefault("block_hazards", [])

    # Ensure 8 blocks for next 24 hours.
    while len(blocks) < 8:
        bi = len(blocks)
        blocks.append(
            {
                "start_fxx": bi * 3 + 1,
                "end_fxx": bi * 3 + 3,
            }
        )

    while len(block_hazards) < 8:
        block_hazards.append({})

    for bi in range(8):
        start_fxx = bi * 3 + 1
        end_fxx = bi * 3 + 3

        block_hours = [h for h in hourly if start_fxx <= h["fxx"] <= end_fxx]
        if not block_hours:
            continue

        peak_block_hour = max(block_hours, key=lambda item: item["gust_mph"])

        blocks[bi]["start_fxx"] = start_fxx
        blocks[bi]["end_fxx"] = end_fxx
        blocks[bi]["GST"] = round(peak_block_hour["gust_mph"], 1)
        blocks[bi]["WSP"] = None
        blocks[bi]["WDR"] = None

        block_hazards[bi]["WIND"] = block_risk_from_hourly_gust(
            gust_mph=peak_block_hour["gust_mph"],
            max_24hr_impact=impact,
        )

    timeline_path.write_text(json.dumps(timeline_payload, indent=2))

    # Write raw wind diagnostic file.
    diagnostic = {
        "site": "KRNO",
        "source": "NBM CONUS via Herbie",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "severity": {
            "field": "GUST 10 m above ground 24 hour fcst",
            "gust_mps": round(gust24_mps, 1),
            "gust_mph": round(gust24_mph, 1),
            "gust_kt": round(gust24_kt, 1),
            "impact_level": impact,
            "risk_level": risk,
            "probability_placeholder": probability,
        },
        "hourly_timing": hourly,
    }

    (DATA / "nbm_wind.json").write_text(json.dumps(diagnostic, indent=2))

    print("Updated docs/threats.json WIND")
    print("Updated docs/timeline.json WIND")
    print("Wrote data/nbm_wind.json")


if __name__ == "__main__":
    main()
