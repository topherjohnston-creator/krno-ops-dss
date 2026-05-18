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

M_TO_SM = 0.000621371192237334

# NBM Core visibility probability thresholds available from inventory.
VIS_THRESHOLDS = {
    "lt_5_sm": {
        "threshold_sm": 5.0,
        "threshold_m": 8046.73,
        "impact_level": 2,
        "label": "Visibility <5 SM",
    },
    "lt_3_sm": {
        "threshold_sm": 3.0,
        "threshold_m": 4828.03,
        "impact_level": 3,
        "label": "Visibility <3 SM",
    },
    "lt_1_sm": {
        "threshold_sm": 1.0,
        "threshold_m": 1609.34,
        "impact_level": 4,
        "label": "Visibility <1 SM",
    },
}

# Note:
# NBM Core inventory did not show a <0.5 SM field.
# We are not deriving <0.5 SM from <1 SM because that would be legally misleading.


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_cycle_utc() -> datetime:
    """
    Use an older likely-complete NBM cycle.

    QMD files can lag behind the current NBM cycle on NOMADS.
    Using the immediately previous 6-hour cycle can fail with 404s,
    especially for f001. Lag by 12 hours to avoid partially available
    QMD cycles.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)

    return cycle - timedelta(hours=12)


def extract_core_point_value(cycle: datetime, fxx: int, search: str) -> float:
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


def probability_to_likelihood(probability: float) -> int:
    """Return 1-5 likelihood category.

    1 = Extremely unlikely
    2 = Unlikely
    3 = About as likely as not
    4 = Likely
    5 = Very likely
    """
    if probability >= 90:
        return 5
    if probability >= 66:
        return 4
    if probability >= 33:
        return 3
    if probability >= 10:
        return 2
    return 1


def matrix_risk(probability: float, impact_level: int) -> int:
    """Probability x impact risk matrix.

    Returns:
    0 = None
    1 = Little to None
    2 = Minor
    3 = Moderate
    4 = Major
    5 = Extreme
    """

    if probability <= 0:
        return 0

    likelihood = probability_to_likelihood(probability)

    matrix = {
        1: {1: 1, 2: 1, 3: 1, 4: 2, 5: 2},
        2: {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
        3: {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
        4: {1: 1, 2: 2, 3: 3, 4: 4, 5: 4},
        5: {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
    }

    safe_impact = max(1, min(5, int(impact_level)))
    return matrix[likelihood][safe_impact]


def risk_label(risk: int) -> str:
    return {
        0: "None",
        1: "Little to None",
        2: "Minor",
        3: "Moderate",
        4: "Major",
        5: "Extreme",
    }.get(risk, "Unknown")


def visibility_metric_from_threshold(threshold_sm: float) -> str:
    if threshold_sm <= 1:
        return "<1 SM"
    if threshold_sm <= 3:
        return "<3 SM"
    if threshold_sm <= 5:
        return "<5 SM"
    return f"<{threshold_sm:g} SM"


def extract_hourly_visibility(cycle: datetime) -> list[dict[str, Any]]:
    hourly_results = []

    for fxx in range(1, 49):
        print(f"Processing Core visibility f{fxx:03d}")

        valid_utc = (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")

        # Deterministic/Core VIS field for display timing only.
        # Risk uses probabilities below.
        try:
            vis_m = extract_core_point_value(
                cycle=cycle,
                fxx=fxx,
                search=":VIS:surface:",
            )
            vis_sm = round(vis_m * M_TO_SM, 2)
        except Exception as exc:
            vis_m = None
            vis_sm = None
            print(f"Warning: deterministic VIS extraction failed for f{fxx:03d}: {exc}")

        threshold_probs = {}

        for key, config in VIS_THRESHOLDS.items():
            threshold_m = config["threshold_m"]

            # Pattern from Core inventory:
            # :VIS:surface:1 hour fcst:prob <1609.34:
            search = f":VIS:surface:{fxx} hour fcst:prob <{threshold_m}:"

            try:
                probability = extract_core_point_value(
                    cycle=cycle,
                    fxx=fxx,
                    search=search,
                )
            except Exception as exc:
                print(f"Warning: VIS probability extraction failed f{fxx:03d} {key}: {exc}")
                probability = 0.0

            threshold_probs[key] = {
                "threshold_sm": config["threshold_sm"],
                "threshold_m": threshold_m,
                "impact_level": config["impact_level"],
                "probability_percent": round(float(probability), 1),
                "label": config["label"],
            }

        hourly_results.append(
            {
                "fxx": fxx,
                "valid_utc": valid_utc,
                "status": "ok",
                "visibility_sm": vis_sm,
                "visibility_m": round(vis_m, 1) if vis_m is not None else None,
                "threshold_probabilities": threshold_probs,
            }
        )

    return hourly_results


def summarize_threshold_probabilities(ok_hours: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    max_probs = {}

    for key, config in VIS_THRESHOLDS.items():
        best_hour = max(
            ok_hours,
            key=lambda h: h["threshold_probabilities"][key]["probability_percent"],
        )

        max_probs[key] = {
            **best_hour["threshold_probabilities"][key],
            "max_probability_fxx": best_hour["fxx"],
            "max_probability_valid_utc": best_hour["valid_utc"],
        }

    return max_probs


def evaluate_visibility_risk(threshold_probs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidates = []

    for key, config in VIS_THRESHOLDS.items():
        probability = float(threshold_probs[key]["probability_percent"])
        impact_level = int(config["impact_level"])
        threshold_sm = float(config["threshold_sm"])
        risk = matrix_risk(probability, impact_level)

        candidates.append(
            {
                "threshold_key": key,
                "threshold_sm": threshold_sm,
                "impact_level": impact_level,
                "probability": probability,
                "risk": risk,
                "risk_label": risk_label(risk),
                "source_fxx": threshold_probs[key].get("max_probability_fxx"),
                "source_valid_utc": threshold_probs[key].get("max_probability_valid_utc"),
            }
        )

    # Highest risk wins. Tie-breaker: higher impact, then higher probability.
    best = max(candidates, key=lambda c: (c["risk"], c["impact_level"], c["probability"]))

    return {
        "best": best,
        "candidates": candidates,
    }


def block_visibility_risk(block_hours: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []

    for hour in block_hours:
        for key, config in VIS_THRESHOLDS.items():
            probability = float(hour["threshold_probabilities"][key]["probability_percent"])
            impact_level = int(config["impact_level"])
            risk = matrix_risk(probability, impact_level)

            candidates.append(
                {
                    "threshold_key": key,
                    "threshold_sm": config["threshold_sm"],
                    "impact_level": impact_level,
                    "probability": probability,
                    "risk": risk,
                    "fxx": hour["fxx"],
                    "valid_utc": hour["valid_utc"],
                }
            )

    if not candidates:
        return {"prob": 0, "risk": 0, "level": 0}

    best = max(candidates, key=lambda c: (c["risk"], c["impact_level"], c["probability"]))

    return {
        "prob": round(best["probability"], 1),
        "risk": int(best["risk"]),
        "level": int(best["impact_level"]),
        "threshold_sm": best["threshold_sm"],
        "source_fxx": best["fxx"],
    }


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def main() -> None:
    cycle = latest_cycle_utc()
    generated = utc_now()

    print(f"Building Core visibility outputs for cycle {cycle:%Y-%m-%d %HZ}")

    hourly_results = extract_hourly_visibility(cycle)
    ok_hours = [h for h in hourly_results if h.get("status") == "ok"]

    if not ok_hours:
        raise RuntimeError("No Core visibility hours extracted successfully.")

    valid_vis_hours = [h for h in ok_hours if h.get("visibility_sm") is not None]
    if valid_vis_hours:
        min_vis_hour = min(valid_vis_hours, key=lambda h: h["visibility_sm"])
        min_vis_sm = float(min_vis_hour["visibility_sm"])
    else:
        min_vis_hour = ok_hours[0]
        min_vis_sm = None

    threshold_probs = summarize_threshold_probabilities(ok_hours)
    risk_eval = evaluate_visibility_risk(threshold_probs)
    best = risk_eval["best"]

    peak_fxx = int(best.get("source_fxx") or min_vis_hour["fxx"])
    peak_start_fxx = max(1, peak_fxx - 1)
    peak_end_fxx = min(48, peak_fxx + 1)

    display_value = f"{min_vis_sm:.1f} SM" if min_vis_sm is not None else "N/A"
    metric = visibility_metric_from_threshold(float(best["threshold_sm"]))

    # Update threats.json.
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
    threats_payload["cycle"] = f"NBM Core {cycle.strftime('%HZ')}"
    threats_payload.setdefault("threats", {})

    threats_payload["threats"]["VISIBILITY"] = {
        "prob": round(float(best["probability"]), 1),
        "risk": int(best["risk"]),
        "risk_label": best["risk_label"],
        "level": int(best["impact_level"]),
        "metric": metric,
        "display_label": "Lowest visibility",
        "display_value": display_value,
        "min_visibility_sm": round(min_vis_sm, 2) if min_vis_sm is not None else None,
        "threshold_probabilities": threshold_probs,
        "risk_candidates": risk_eval["candidates"],
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "driver": (
            f"{best['probability']:.1f}% chance visibility <{best['threshold_sm']:.0f} SM"
        ),
        "methodology": (
            "Visibility risk uses NBM Core hourly probability fields for visibility below "
            "5, 3, and 1 SM. The maximum hourly probability from f001-f048 is used for each "
            "threshold, then probability x impact determines risk. NBM Core did not provide "
            "a <0.5 SM probability field in the inventory scan, so <0.5 SM is not derived."
        ),
    }

    hazards = threats_payload.get("hazards")
    if isinstance(hazards, list):
        found = False

        for hazard in hazards:
            if hazard.get("id") == "VISIBILITY":
                hazard.update(
                    {
                        "id": "VISIBILITY",
                        "name": "Visibility",
                        "risk_level": int(best["risk"]),
                        "risk_label": best["risk_label"],
                        "impact_level": int(best["impact_level"]),
                        "probability": round(float(best["probability"]), 1),
                        "peak_start_fxx": peak_start_fxx,
                        "peak_end_fxx": peak_end_fxx,
                        "metric": metric,
                        "display_label": "Lowest visibility",
                        "display_value": display_value,
                        "min_visibility_sm": round(min_vis_sm, 2) if min_vis_sm is not None else None,
                        "driver": (
                            f"{best['probability']:.1f}% chance visibility <{best['threshold_sm']:.0f} SM"
                        ),
                    }
                )
                found = True
                break

        if not found:
            hazards.append(
                {
                    "id": "VISIBILITY",
                    "name": "Visibility",
                    "risk_level": int(best["risk"]),
                    "risk_label": best["risk_label"],
                    "impact_level": int(best["impact_level"]),
                    "probability": round(float(best["probability"]), 1),
                    "peak_start_fxx": peak_start_fxx,
                    "peak_end_fxx": peak_end_fxx,
                    "metric": metric,
                    "display_label": "Lowest visibility",
                    "display_value": display_value,
                    "min_visibility_sm": round(min_vis_sm, 2) if min_vis_sm is not None else None,
                    "driver": (
                        f"{best['probability']:.1f}% chance visibility <{best['threshold_sm']:.0f} SM"
                    ),
                }
            )

    threats_path.write_text(json.dumps(threats_payload, indent=2))

    # Update timeline.json.
    # Frontend expects 16 blocks x 3 hours = 48 hours.
    # Normalize the timeline to prevent stale 8-block or mock structures from persisting.
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

    timeline_payload["site"] = "KRNO"
    timeline_payload["generated_utc"] = generated
    timeline_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    timeline_payload["cycle"] = f"NBM Core {cycle.strftime('%HZ')}"
    timeline_payload["block_hours"] = 3

    old_blocks = timeline_payload.get("blocks", [])
    old_block_hazards = timeline_payload.get("block_hazards", [])

    new_blocks = []
    new_block_hazards = []

    for bi in range(16):
        start_fxx = bi * 3 + 1
        end_fxx = min((bi + 1) * 3, 48)

        old_block = old_blocks[bi] if bi < len(old_blocks) and isinstance(old_blocks[bi], dict) else {}
        old_hazards = (
            old_block_hazards[bi]
            if bi < len(old_block_hazards) and isinstance(old_block_hazards[bi], dict)
            else {}
        )

        block_hours = [h for h in ok_hours if start_fxx <= h["fxx"] <= end_fxx]

        new_block = dict(old_block)
        new_block["start_fxx"] = start_fxx
        new_block["end_fxx"] = end_fxx

        new_hazard_block = dict(old_hazards)

        if block_hours:
            valid_block_vis = [h for h in block_hours if h.get("visibility_sm") is not None]
            if valid_block_vis:
                block_min_vis = min(valid_block_vis, key=lambda h: h["visibility_sm"])
                new_block["VIS"] = round(float(block_min_vis["visibility_sm"]), 2)
            else:
                new_block["VIS"] = None

            new_hazard_block["VISIBILITY"] = block_visibility_risk(block_hours)
        else:
            new_block["VIS"] = None
            new_hazard_block["VISIBILITY"] = {
                "prob": 0.0,
                "risk": 0,
                "level": 0,
                "threshold_sm": None,
                "source_fxx": None,
                "metric": "N/A",
                "driver": "No visibility data available for this block",
            }

        new_blocks.append(new_block)
        new_block_hazards.append(new_hazard_block)

    timeline_payload["blocks"] = new_blocks
    timeline_payload["block_hazards"] = new_block_hazards

    timeline_path.write_text(json.dumps(timeline_payload, indent=2))

    diagnostic = {
        "site": "KRNO",
        "source": "NBM Core via Herbie",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "display_value": {
            "label": "Lowest visibility",
            "method": "minimum hourly NBM Core deterministic VIS from f001-f048",
            "source_fxx": min_vis_hour["fxx"],
            "valid_utc": min_vis_hour["valid_utc"],
            "visibility_sm": round(min_vis_sm, 2) if min_vis_sm is not None else None,
        },
        "airport_threshold_probabilities": {
            "method": (
                "For each visibility threshold, probability is the maximum hourly probability "
                "from f001-f048. Probabilities use direct NBM Core probability fields."
            ),
            "thresholds": threshold_probs,
        },
        "risk": risk_eval,
        "hourly_results": hourly_results,
        "methodology": (
            "Visibility display is the minimum hourly deterministic NBM Core visibility. "
            "Visibility risk probabilities use direct NBM Core probability fields for "
            "VIS <5, <3, and <1 SM. Risk is calculated using probability x impact."
        ),
    }

    (DATA / "nbm_core_visibility.json").write_text(json.dumps(diagnostic, indent=2))

    print("Updated docs/threats.json VISIBILITY")
    print("Updated docs/timeline.json VISIBILITY")
    print("Wrote data/nbm_core_visibility.json")


if __name__ == "__main__":
    main()
