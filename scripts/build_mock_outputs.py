from __future__ import annotations

import json
from pathlib import Path

from risk_engine import make_hazard, sort_hazards


OUT = Path("docs")
OUT.mkdir(exist_ok=True)

hazards = [
    make_hazard("Wind", "Wind", 4, 72, "21Z-00Z", "P50 24-hr max gust near 62 mph", "58-65 mph"),
    make_hazard("Visibility", "Visibility", 3, 68, "20Z-23Z", "Visibility 1-3 SM possible", "1-3 SM"),
    make_hazard("Snow", "Snow", 2, 45, "09Z-15Z", "Trace to light accumulation possible", "Trace"),
    make_hazard("Rain", "Rain/Flooding", 2, 42, "18Z-00Z", "0.10-0.25 in/hr rainfall rate possible", "0.10-0.25 in/hr"),
    make_hazard("Lightning", "Lightning", 2, 35, "20Z-02Z", "Thunder probability elevated", "5-25% chance"),
    make_hazard("FlashFreeze", "Flash Freeze", 1, 20, "12Z-15Z", "Wet surface plus freezing temps signal weak", "Dry and >32F Tw"),
    make_hazard("Temperature", "Temperature", 1, 70, "12Z-16Z", "Temperatures near normal operational range", "32-95F"),
    make_hazard("FZRA", "Freezing Rain", 1, 5, "None", "No meaningful freezing rain signal", "None"),
]

threats = {
    "site": "KRNO",
    "valid_period": "next_24_hours",
    "hazards": sort_hazards(hazards),
}

timeline = {
    "site": "KRNO",
    "block_hours": 3,
    "blocks": [
        {"time": "00Z-03Z", "Wind": 1, "Visibility": 1, "Snow": 1, "Rain": 1, "Lightning": 1, "FlashFreeze": 1, "Temperature": 1, "FZRA": 1},
        {"time": "03Z-06Z", "Wind": 1, "Visibility": 1, "Snow": 1, "Rain": 1, "Lightning": 1, "FlashFreeze": 1, "Temperature": 1, "FZRA": 1},
        {"time": "06Z-09Z", "Wind": 2, "Visibility": 1, "Snow": 2, "Rain": 1, "Lightning": 1, "FlashFreeze": 1, "Temperature": 1, "FZRA": 1},
        {"time": "09Z-12Z", "Wind": 2, "Visibility": 2, "Snow": 2, "Rain": 1, "Lightning": 1, "FlashFreeze": 1, "Temperature": 1, "FZRA": 1},
        {"time": "12Z-15Z", "Wind": 3, "Visibility": 2, "Snow": 2, "Rain": 2, "Lightning": 1, "FlashFreeze": 1, "Temperature": 1, "FZRA": 1},
        {"time": "15Z-18Z", "Wind": 3, "Visibility": 2, "Snow": 1, "Rain": 2, "Lightning": 2, "FlashFreeze": 1, "Temperature": 1, "FZRA": 1},
        {"time": "18Z-21Z", "Wind": 4, "Visibility": 3, "Snow": 1, "Rain": 2, "Lightning": 2, "FlashFreeze": 1, "Temperature": 1, "FZRA": 1},
        {"time": "21Z-00Z", "Wind": 4, "Visibility": 3, "Snow": 1, "Rain": 2, "Lightning": 2, "FlashFreeze": 1, "Temperature": 1, "FZRA": 1},
    ],
}

(OUT / "threats.json").write_text(json.dumps(threats, indent=2))
(OUT / "timeline.json").write_text(json.dumps(timeline, indent=2))

print("Wrote docs/threats.json and docs/timeline.json")
