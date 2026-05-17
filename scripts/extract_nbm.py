"""NBM Core extraction scaffold for KRNO Ops DSS.

Future production flow:
- Use Herbie to subset NBM Core GRIB2 by byte range.
- Extract nearest grid point to KRNO.
- Use 24-hour max/min products for severity.
- Use hourly products for timing.
- Write docs/threats.json and docs/timeline.json.
"""

from __future__ import annotations

from datetime import datetime, timezone


KRNO_LAT = 39.4991
KRNO_LON = -119.7681


def main() -> None:
    print("NBM extraction scaffold")
    print("Next step: map exact NBM Core variable regex strings and wire to risk_engine.py")
    print(f"Generated at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
