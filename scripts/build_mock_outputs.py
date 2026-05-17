from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


OUT = Path("docs")
OUT.mkdir(exist_ok=True)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def round_down_to_3hr(dt: datetime) -> datetime:
    hour = (dt.hour // 3) * 3
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


now = utc_now()
cycle_dt = round_down_to_3hr(now)
cycle_iso = cycle_dt.isoformat().replace("+00:00", "Z")
generated_iso = now.isoformat().replace("+00:00", "Z")


# This schema matches the existing docs/index.html expectations.
# index.html expects:
# threats["WIND"].prob
# threats["WIND"].risk
# threats["WIND"].level
# threats["WIND"].metric
# threats["WIND"].peak_start_fxx
# threats["WIND"].peak_end_fxx

threats = {
    "WIND": {
        "prob": 72,
        "risk": 4,
        "level": 4,
        "metric": "58-65 mph",
        "peak_start_fxx": 6,
        "peak_end_fxx": 9,
        "driver": "P50 24-hr max gust near 62 mph",
        "g24_p10_mph": 45,
        "g24_p50_mph": 62,
        "g24_p90_mph": 72,
    },
    "VISIBILITY": {
        "prob": 68,
        "risk": 3,
        "level": 3,
        "metric": "1-3 SM",
        "peak_start_fxx": 6,
        "peak_end_fxx": 10,
        "driver": "Visibility restriction possible",
    },
    "SNOW": {
        "prob": 45,
        "risk": 2,
        "level": 2,
        "metric": "Trace",
        "peak_start_fxx": 12,
        "peak_end_fxx": 15,
        "driver": "Trace to light snow accumulation possible",
    },
    "RAIN": {
        "prob": 42,
        "risk": 2,
        "level": 2,
        "metric": "0.10-0.25 in/hr",
        "peak_start_fxx": 9,
        "peak_end_fxx": 15,
        "driver": "Light to moderate rainfall rate possible",
    },
    "LIGHTNING": {
        "prob": 35,
        "risk": 2,
        "level": 2,
        "metric": "5-25%",
        "peak_start_fxx": 9,
        "peak_end_fxx": 15,
        "driver": "Thunder probability elevated",
    },
    "FLASH_FREEZE": {
        "prob": 20,
        "risk": 1,
        "level": 1,
        "metric": "Dry and >32°F Tw",
        "peak_start_fxx": 12,
        "peak_end_fxx": 15,
        "driver": "Weak wet-surface plus freezing temperature signal",
    },
    "TEMPERATURE": {
        "prob": 70,
        "risk": 1,
        "level": 1,
        "metric": "32-95°F",
        "peak_start_fxx": 12,
        "peak_end_fxx": 18,
        "driver": "Temperatures within low-impact operational range",
    },
    "FZRA": {
        "prob": 5,
        "risk": 0,
        "level": 0,
        "metric": "None",
        "peak_start_fxx": None,
        "peak_end_fxx": None,
        "driver": "No meaningful freezing rain signal",
    },
}


# Keep this list too for future backend use, but index.html mainly uses threats.
hazards = [
    {
        "id": key,
        "name": key,
        "risk_level": value["risk"],
        "impact_level": value["level"],
        "probability": value["prob"],
        "peak_start_fxx": value["peak_start_fxx"],
        "peak_end_fxx": value["peak_end_fxx"],
        "metric": value["metric"],
        "driver": value["driver"],
    }
    for key, value in threats.items()
]


# 16 blocks x 3 hr = 48 hr timeline.
# index.html expects:
# timeline.blocks
# timeline.block_hazards
# block_hazards[block_index]["WIND"].risk/prob/level
blocks = []
block_hazards = []

for bi in range(16):
    start_fxx = bi * 3 + 1
    end_fxx = bi * 3 + 3

    block = {
        "start_fxx": start_fxx,
        "end_fxx": end_fxx,
        "GST": 20,
        "WSP": 10,
        "WDR": 33,
        "TMP": 59,
        "DPT": 16,
        "VIS": 100,
        "Q01": 0,
        "I01": 0,
        "s3hr_in": 0.0,
    }

    hazards_for_block = {
        "WIND": {"prob": 5, "risk": 1, "level": 1},
        "VISIBILITY": {"prob": 5, "risk": 1, "level": 1},
        "SNOW": {"prob": 0, "risk": 0, "level": 0},
        "RAIN": {"prob": 0, "risk": 0, "level": 0},
        "LIGHTNING": {"prob": 0, "risk": 0, "level": 0},
        "FLASH_FREEZE": {"prob": 0, "risk": 0, "level": 0},
        "TEMPERATURE": {"prob": 5, "risk": 1, "level": 1},
        "FZRA": {"prob": 0, "risk": 0, "level": 0},
    }

    # Mock peak wind/visibility/rain/lightning period.
    if 2 <= bi <= 3:
        block["GST"] = 48
        block["WSP"] = 26
        hazards_for_block["WIND"] = {"prob": 55, "risk": 3, "level": 3}

    if 4 <= bi <= 5:
        block["GST"] = 62
        block["WSP"] = 33
        block["VIS"] = 25
        block["Q01"] = 18
        hazards_for_block["WIND"] = {"prob": 72, "risk": 4, "level": 4}
        hazards_for_block["VISIBILITY"] = {"prob": 68, "risk": 3, "level": 3}
        hazards_for_block["RAIN"] = {"prob": 42, "risk": 2, "level": 2}
        hazards_for_block["LIGHTNING"] = {"prob": 35, "risk": 2, "level": 2}

    if 6 <= bi <= 7:
        block["GST"] = 42
        block["WSP"] = 22
        hazards_for_block["WIND"] = {"prob": 45, "risk": 2, "level": 2}
        hazards_for_block["RAIN"] = {"prob": 30, "risk": 2, "level": 2}

    if 8 <= bi <= 9:
        block["s3hr_in"] = 0.2
        hazards_for_block["SNOW"] = {"prob": 45, "risk": 2, "level": 2}

    blocks.append(block)
    block_hazards.append(hazards_for_block)


timeline = {
    "site": "KRNO",
    "generated_utc": generated_iso,
    "cycle_utc_iso": cycle_iso,
    "cycle": f"MOCK {cycle_dt.strftime('%HZ')}",
    "block_hours": 3,
    "blocks": blocks,
    "block_hazards": block_hazards,
}


threats_payload = {
    "site": "KRNO",
    "generated_utc": generated_iso,
    "cycle_utc_iso": cycle_iso,
    "cycle": f"MOCK {cycle_dt.strftime('%HZ')}",
    "valid_period": "next_48_hours",
    "threats": threats,
    "hazards": hazards,
}


(OUT / "threats.json").write_text(json.dumps(threats_payload, indent=2))
(OUT / "timeline.json").write_text(json.dumps(timeline, indent=2))

print("Wrote docs/threats.json and docs/timeline.json")
