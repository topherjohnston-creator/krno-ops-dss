"""KRNO Ops DSS risk engine.

Methodology:
- 24-hour max/min NBM products determine peak hazard severity.
- Hourly NBM products determine timing.
- Impact x Likelihood determines operational risk.
- Hazards are sorted highest-to-lowest for the frontend matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable


RISK_MATRIX = {
    "very_likely": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
    "likely": {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
    "as_likely_as_not": {1: 1, 2: 1, 3: 2, 4: 3, 5: 4},
    "unlikely": {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
    "extremely_unlikely": {1: 1, 2: 1, 3: 1, 4: 2, 5: 2},
}

RISK_LABELS = {
    1: "Little to None",
    2: "Minor",
    3: "Moderate",
    4: "Major",
    5: "Extreme",
}


@dataclass
class HazardRisk:
    id: str
    name: str
    risk_level: int
    risk_label: str
    impact_level: int
    likelihood: str
    probability: float
    peak_time: str
    driver: str
    criteria: str


def likelihood_key(probability: float) -> str:
    if probability >= 90:
        return "very_likely"
    if probability >= 66:
        return "likely"
    if probability >= 33:
        return "as_likely_as_not"
    if probability >= 10:
        return "unlikely"
    return "extremely_unlikely"


def likelihood_label(probability: float) -> str:
    return {
        "very_likely": "Very Likely",
        "likely": "Likely",
        "as_likely_as_not": "As Likely As Not",
        "unlikely": "Unlikely",
        "extremely_unlikely": "Extremely Unlikely",
    }[likelihood_key(probability)]


def risk_level(impact_level: int, probability: float) -> int:
    impact_level = max(1, min(5, int(impact_level)))
    return RISK_MATRIX[likelihood_key(probability)][impact_level]


def make_hazard(
    id: str,
    name: str,
    impact_level: int,
    probability: float,
    peak_time: str,
    driver: str,
    criteria: str,
) -> HazardRisk:
    r = risk_level(impact_level, probability)
    return HazardRisk(
        id=id,
        name=name,
        risk_level=r,
        risk_label=RISK_LABELS[r],
        impact_level=impact_level,
        likelihood=likelihood_label(probability),
        probability=round(float(probability), 1),
        peak_time=peak_time,
        driver=driver,
        criteria=criteria,
    )


def sort_hazards(hazards: Iterable[HazardRisk]) -> list[dict]:
    return [
        asdict(h)
        for h in sorted(
            hazards,
            key=lambda h: (h.risk_level, h.impact_level, h.probability),
            reverse=True,
        )
    ]


def impact_wind_mph(gust_mph: float) -> int:
    if gust_mph > 65:
        return 5
    if gust_mph >= 58:
        return 4
    if gust_mph >= 45:
        return 3
    if gust_mph >= 30:
        return 2
    return 1


def impact_visibility_sm(vis_sm: float) -> int:
    if vis_sm < 0.50:
        return 5
    if vis_sm < 1.0:
        return 4
    if vis_sm <= 3.0:
        return 3
    if vis_sm <= 5.0:
        return 2
    return 1


def impact_rain_rate_in_hr(rate: float) -> int:
    if rate > 1.00:
        return 5
    if rate >= 0.50:
        return 4
    if rate >= 0.25:
        return 3
    if rate >= 0.10:
        return 2
    return 1


def impact_temperature_f(temp_f: float) -> int:
    if temp_f < 10 or temp_f > 105:
        return 5
    if temp_f < 20 or temp_f >= 100:
        return 4
    if temp_f < 32 or temp_f >= 95:
        return 3
    if temp_f < 40 or temp_f >= 90:
        return 2
    return 1
