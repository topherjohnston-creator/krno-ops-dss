"""NBM Core extraction scaffold for KRNO Ops DSS.

Production methodology:
- Use Herbie to subset NBM Core GRIB2 by byte range.
- Extract nearest grid point to KRNO.
- Use 24-hour max/min products for peak severity.
- Use hourly products for timing.
- Write docs/threats.json and docs/timeline.json.

This file is intentionally not fully wired yet because the exact NBM Core
variable regex strings need to be validated against the live NBM inventory.
"""

from __future__ import annotations

from datetime import datetime, timezone


KRNO_LAT = 39.4991
KRNO_LON = -119.7681
KRNO_ID = "KRNO"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    print("NBM extraction scaffold")
    print(f"Site: {KRNO_ID}")
    print(f"Lat/Lon: {KRNO_LAT}, {KRNO_LON}")
    print("Next step: validate NBM Core variable regex strings with Herbie inventory.")
    print(f"Generated: {utc_now()}")


if __name__ == "__main__":
    main()
