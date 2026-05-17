"""KRNO observation builder scaffold.

Future:
- Pull KRNO METAR from aviationweather.gov API.
- Parse wind, visibility, temperature, dewpoint, present weather.
- Write docs/obs.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    out = Path("docs")
    out.mkdir(exist_ok=True)
    obs = {
        "site": "KRNO",
        "source": "aviationweather.gov API placeholder",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "metar": None,
    }
    (out / "obs.json").write_text(json.dumps(obs, indent=2))
    print("Wrote docs/obs.json placeholder")


if __name__ == "__main__":
    main()
