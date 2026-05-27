from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


DOCS = Path("docs")
DATA = Path("data")

SITE = {
    "site": "KRNO",
    "site_name": "Reno-Tahoe International Airport",
    "lat": 39.4991,
    "lon": -119.7681,
}

HAZARDS = ["WIND", "LIGHTNING", "SNOW", "VISIBILITY", "FZRA", "FLASH_FREEZE", "RAIN", "TEMPERATURE"]

RISK_LABELS = {
    0: "None",
    1: "Little to None",
    2: "Minor",
    3: "Moderate",
    4: "Major",
    5: "Extreme",
}

NATIVE_WINDOWS = {
    "WIND": [
        {"start_fxx": 1, "end_fxx": 48, "window_hours": 1, "source": "NBM hourly wind / QMD 24-hour max gust"},
        {"start_fxx": 49, "end_fxx": 72, "window_hours": 3, "source": "NBM 3-hour wind / QMD 24-hour max gust"},
    ],
    "RAIN": [
        {"start_fxx": 1, "end_fxx": 72, "window_hours": 1, "source": "NBM hourly deterministic rain"},
        {"start_fxx": 6, "end_fxx": 72, "window_hours": 6, "source": "NBM QMD 6-hour rain probabilities"},
    ],
    "FZRA": [
        {"start_fxx": 1, "end_fxx": 48, "window_hours": 1, "source": "NBM hourly deterministic freezing rain"},
        {"start_fxx": 6, "end_fxx": 72, "window_hours": 6, "source": "NBM QMD 6-hour freezing rain probabilities"},
    ],
    "SNOW": [
        {"start_fxx": 1, "end_fxx": 48, "window_hours": 1, "source": "NBM hourly snow amount/probability"},
        {"start_fxx": 54, "end_fxx": 72, "window_hours": 6, "source": "NBM 6-hour snow amount/probability"},
    ],
    "LIGHTNING": [
        {"start_fxx": 1, "end_fxx": 36, "window_hours": 1, "source": "NBM 1-hour thunder probability"},
        {"start_fxx": 39, "end_fxx": 72, "window_hours": 3, "source": "NBM 3-hour thunder probability"},
    ],
    "VISIBILITY": [
        {"start_fxx": 1, "end_fxx": 36, "window_hours": 1, "source": "NBM hourly visibility probabilities"},
        {"start_fxx": 39, "end_fxx": 72, "window_hours": 3, "source": "NBM 3-hour visibility probabilities"},
    ],
    "FLASH_FREEZE": [
        {"start_fxx": 1, "end_fxx": 48, "window_hours": 1, "source": "NBM hourly temperature probabilities + wet-surface proxy"},
        {"start_fxx": 51, "end_fxx": 72, "window_hours": 3, "source": "NBM 3-hour temperature probabilities + wet-surface proxy"},
    ],
    "TEMPERATURE": [
        {"start_fxx": 1, "end_fxx": 48, "window_hours": 1, "source": "NBM hourly temperature probabilities / QMD max-min"},
        {"start_fxx": 51, "end_fxx": 72, "window_hours": 3, "source": "NBM 3-hour temperature probabilities / QMD max-min"},
    ],
}

METHODOLOGY = {
    "version": "nbm_dss_schema_v1",
    "horizon_hours": 72,
    "risk_matrix": "Risk is probability-first. Timeline blocks represent windowed probability of exceeding operational thresholds. Risk cards summarize 72-hour probabilistic risk.",
    "risk_labels": RISK_LABELS,
    "native_windows": NATIVE_WINDOWS,
    "snow": {
        "basis": "12-hour snowfall probability thresholds using operational impact language, not NWS product language.",
        "impact_thresholds": [
            {"level": 0, "label": "None", "threshold": "No meaningful snow signal"},
            {"level": 1, "label": "Little to None", "threshold": "Trace/light snow signal"},
            {"level": 2, "label": "Minor", "threshold": "Near-threshold snow or low probability"},
            {"level": 3, "label": "Moderate", "threshold": "Meaningful chance of >=2 inches / 12 hr"},
            {"level": 4, "label": "Major", "threshold": "Meaningful chance of >=4 inches / 12 hr"},
            {"level": 5, "label": "Extreme", "threshold": "High confidence >4 inches / 12 hr or substantially above threshold"},
        ],
    },
    "precip_type_conflict": [
        "Evaluate freezing rain first when probability/amount exceeds threshold.",
        "Then evaluate snow when probability/amount and temperature support snow.",
        "Otherwise classify precipitation as rain.",
        "Allow mixed/transition wording when rain and snow signals overlap near the temperature threshold.",
    ],
    "future_admin_config": {
        "site": "Location, name, coordinates, branding",
        "sources": "NBM, REFS, QMD, observations, alerts",
        "hazards": "Enabled variables, row order, labels, thresholds, tooltips",
        "timeline": "Horizon, native windows, display windows, card aggregation rules",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_cycle_floor(dt: datetime | None = None) -> datetime:
    if dt is None:
        dt = datetime.now(timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return dt.replace(hour=(dt.hour // 6) * 6)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def empty_hazard(hazard: str, start_fxx: int, end_fxx: int, start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "id": hazard,
        "label": hazard,
        "name": hazard,
        "risk": 0,
        "risk_label": RISK_LABELS[0],
        "level": 0,
        "impact_level": 0,
        "prob": 0,
        "probability": 0,
        "metric": "Awaiting NBM/QMD extraction",
        "driver": "NBM schema is wired; hazard extraction is pending",
        "source_fxx": start_fxx,
        "peak_valid_utc": iso(start),
        "valid_start_utc": iso(start),
        "valid_end_utc": iso(end),
        "start_fxx": start_fxx,
        "end_fxx": end_fxx,
        "window_hours": end_fxx - start_fxx + 1,
        "data_status": "schema_pending",
        "method": "nbm_dss_schema_v1",
        "source": "NBM/QMD pending",
        "hourly_values": [],
    }


def build_outputs() -> tuple[dict[str, Any], dict[str, Any]]:
    generated = utc_now()
    cycle = latest_cycle_floor()
    cycle_iso = iso(cycle)
    cycle_label = f"NBM {cycle:%HZ}"

    blocks: list[dict[str, Any]] = []
    block_hazards: list[dict[str, Any]] = []

    for block_index, start_fxx in enumerate(range(1, 73, 3)):
        end_fxx = min(start_fxx + 2, 72)
        start = cycle + timedelta(hours=start_fxx)
        end = cycle + timedelta(hours=end_fxx)
        block: dict[str, Any] = {
            "block_index": block_index,
            "start_fxx": start_fxx,
            "end_fxx": end_fxx,
            "valid_start_utc": iso(start),
            "valid_end_utc": iso(end),
        }

        hazards_for_block: dict[str, Any] = {}
        for hazard in HAZARDS:
            block[hazard] = 0
            hazards_for_block[hazard] = empty_hazard(hazard, start_fxx, end_fxx, start, end)

        blocks.append(block)
        block_hazards.append(hazards_for_block)

    threats = {}
    hazards_list = []
    for hazard in HAZARDS:
        threat = {
            "id": hazard,
            "title": hazard,
            "name": hazard,
            "prob": 0,
            "probability": 0,
            "risk": 0,
            "risk_level": 0,
            "risk_label": RISK_LABELS[0],
            "level": 0,
            "impact_level": 0,
            "metric": "Awaiting NBM/QMD extraction",
            "display_label": "72-hr probabilistic risk",
            "display_value": "Pending",
            "window": "72 hr",
            "peak_start_fxx": None,
            "peak_end_fxx": None,
            "source_fxx": None,
            "peak_valid_utc": None,
            "driver": "NBM schema is wired; hazard extraction is pending",
            "methodology": METHODOLOGY["risk_matrix"],
            "data_status": "schema_pending",
            "method": "nbm_dss_schema_v1",
            "native_windows": NATIVE_WINDOWS.get(hazard, []),
        }
        threats[hazard] = threat
        hazards_list.append({"id": hazard, "risk": 0, "probability": 0, "level": 0})

    common = {
        **SITE,
        "source": "NBM/QMD schema scaffold",
        "generated_utc": generated,
        "cycle_utc_iso": cycle_iso,
        "cycle": cycle_label,
        "valid_period": {"hours": 72, "start_utc": iso(cycle + timedelta(hours=1)), "end_utc": iso(cycle + timedelta(hours=72))},
    }

    timeline = {
        **common,
        "block_hours": "mixed_native_windows",
        "blocks": blocks,
        "block_hazards": block_hazards,
        "metadata": METHODOLOGY,
    }

    threats_payload = {
        **common,
        "threats": threats,
        "hazards": hazards_list,
        "methodology": METHODOLOGY["risk_matrix"],
        "metadata": METHODOLOGY,
    }

    return timeline, threats_payload


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)

    timeline, threats = build_outputs()
    (DOCS / "nbm_timeline.json").write_text(json.dumps(timeline, indent=2))
    (DOCS / "nbm_threats.json").write_text(json.dumps(threats, indent=2))
    (DATA / "nbm_dss_methodology.json").write_text(json.dumps(METHODOLOGY, indent=2))
    print("Wrote docs/nbm_timeline.json")
    print("Wrote docs/nbm_threats.json")
    print("Wrote data/nbm_dss_methodology.json")


if __name__ == "__main__":
    main()
