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

METERS_TO_SM = 0.000621371


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


def visibility_impact_level(vis_sm: float) -> int:
    if vis_sm < 0.5:
        return 5
    if vis_sm < 1.0:
        return 4
    if vis_sm <= 3.0:
        return 3
    if vis_sm <= 5.0:
        return 2
    return 1


def visibility_risk_level(impact_level: int) -> int:
    """Temporary deterministic visibility risk.

    Once visibility exceedance probabilities are wired, this should use:
    Impact x Likelihood = Risk.

    For now:
    - Impact 1 = Little to None
    - Impact 2 = Minor
    - Impact 3 = Moderate
    - Impact 4 = Major
    - Impact 5 = Extreme
    """
    return max(1, min(5, impact_level))


def visibility_metric(vis_sm: float) -> str:
    if vis_sm < 0.5:
        return "<1/2 SM"
    if vis_sm < 1.0:
        return "1/2-1 SM"
    if vis_sm <= 3.0:
        return "1-3 SM"
    if vis_sm <= 5.0:
        return "3-5 SM"
    return ">5 SM"


def block_risk_from_visibility(vis_sm: float) -> dict[str, int]:
    impact = visibility_impact_level(vis_sm)
    risk = visibility_risk_level(impact)

    # Temporary probability placeholder. This should be replaced with direct
    # NBM probability fields once we wire those in.
    if impact >= 5:
        probability = 80
    elif impact == 4:
        probability = 70
    elif impact == 3:
        probability = 60
    elif impact == 2:
        probability = 50
    else:
        probability = 10

    return {
        "prob": probability,
        "risk": risk,
        "level": impact,
    }


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def main() -> None:
    cycle = latest_cycle_utc()
    generated = utc_now()

    print(f"Building real visibility output from NBM cycle {cycle:%Y-%m-%d %HZ}")

    hourly = []

    for fxx in range(1, 25):
        vis_m = extract_point_value(
            cycle=cycle,
            fxx=fxx,
            search=":VIS:surface:",
        )

        vis_sm = vis_m * METERS_TO_SM

        hourly.append(
            {
                "fxx": fxx,
                "valid_utc": (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z"),
                "visibility_m": round(vis_m, 1),
                "visibility_sm": round(vis_sm, 2),
                "impact_level": visibility_impact_level(vis_sm),
            }
        )

    min_hour = min(hourly, key=lambda item: item["visibility_sm"])

    min_vis_sm = float(min_hour["visibility_sm"])
    impact = visibility_impact_level(min_vis_sm)
    risk = visibility_risk_level(impact)
    metric = visibility_metric(min_vis_sm)

    peak_start_fxx = max(1, int(min_hour["fxx"]) - 1)
    peak_end_fxx = min(24, int(min_hour["fxx"]) + 1)

    # Update threats.json while preserving other hazards.
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

    threats_payload["threats"]["VISIBILITY"] = {
        "prob": 10 if impact == 1 else 50,
        "risk": risk,
        "level": impact,
        "metric": metric,
        "display_label": "Min visibility",
        "display_value": f"{min_vis_sm:.1f} SM",
        "min_visibility_sm": round(min_vis_sm, 2),
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "driver": f"NBM minimum visibility {min_vis_sm:.1f} SM",
        "methodology": "Visibility severity uses minimum hourly NBM VIS through 24 hours. Timeline uses 3-hour block minimum visibility.",
    }

    # Update hazards array if present.
    hazards = threats_payload.get("hazards")
    if isinstance(hazards, list):
        found = False
        for hazard in hazards:
            if hazard.get("id") == "VISIBILITY":
                hazard.update(
                    {
                        "id": "VISIBILITY",
                        "name": "Visibility",
                        "risk_level": risk,
                        "impact_level": impact,
                        "probability": 10 if impact == 1 else 50,
                        "peak_start_fxx": peak_start_fxx,
                        "peak_end_fxx": peak_end_fxx,
                        "metric": metric,
                        "display_label": "Min visibility",
                        "display_value": f"{min_vis_sm:.1f} SM",
                        "driver": f"NBM minimum visibility {min_vis_sm:.1f} SM",
                    }
                )
                found = True
                break

        if not found:
            hazards.append(
                {
                    "id": "VISIBILITY",
                    "name": "Visibility",
                    "risk_level": risk,
                    "impact_level": impact,
                    "probability": 10 if impact == 1 else 50,
                    "peak_start_fxx": peak_start_fxx,
                    "peak_end_fxx": peak_end_fxx,
                    "metric": metric,
                    "display_label": "Min visibility",
                    "display_value": f"{min_vis_sm:.1f} SM",
                    "driver": f"NBM minimum visibility {min_vis_sm:.1f} SM",
                }
            )

    threats_path.write_text(json.dumps(threats_payload, indent=2))

    # Update timeline.json VISIBILITY fields while preserving other hazards.
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

        min_block_hour = min(block_hours, key=lambda item: item["visibility_sm"])

        blocks[bi]["start_fxx"] = start_fxx
        blocks[bi]["end_fxx"] = end_fxx
        blocks[bi]["VIS"] = round(float(min_block_hour["visibility_sm"]), 2)

        block_hazards[bi]["VISIBILITY"] = block_risk_from_visibility(
            vis_sm=float(min_block_hour["visibility_sm"])
        )

    timeline_path.write_text(json.dumps(timeline_payload, indent=2))

    diagnostic = {
        "site": "KRNO",
        "source": "NBM CONUS via Herbie",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "severity": {
            "field": "VIS surface hourly",
            "min_visibility_sm": round(min_vis_sm, 2),
            "impact_level": impact,
            "risk_level": risk,
            "metric": metric,
        },
        "hourly_timing": hourly,
    }

    (DATA / "nbm_visibility.json").write_text(json.dumps(diagnostic, indent=2))

    print("Updated docs/threats.json VISIBILITY")
    print("Updated docs/timeline.json VISIBILITY")
    print("Wrote data/nbm_visibility.json")


if __name__ == "__main__":
    main()
