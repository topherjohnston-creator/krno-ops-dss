from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Iterable

import requests


NOMADS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod"


def floor_to_6hr_cycle(dt: datetime | None = None) -> datetime:
    """Return the latest 00/06/12/18Z cycle time at or before now."""
    if dt is None:
        dt = datetime.now(timezone.utc)

    dt = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (dt.hour // 6) * 6
    return dt.replace(hour=cycle_hour)


def nbm_idx_url(cycle: datetime, product: str, fxx: int, domain: str = "co") -> str:
    """
    Build NBM IDX URL.

    product examples:
      core
      qmd
    """
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")

    return (
        f"{NOMADS_BASE}/blend.{ymd}/{hh}/{product}/"
        f"blend.t{hh}z.{product}.f{fxx:03d}.{domain}.grib2.idx"
    )


def url_exists(url: str, timeout: int = 15) -> bool:
    """
    HEAD is faster, but NOMADS sometimes behaves better with GET.
    Use streaming GET and close immediately.
    """
    try:
        response = requests.get(url, timeout=timeout, stream=True)
        response.close()
        return response.status_code == 200
    except Exception:
        return False


def check_cycle_availability(
    cycle: datetime,
    product: str,
    required_fxx: Iterable[int],
    domain: str = "co",
) -> tuple[bool, list[str]]:
    """
    Check whether a cycle has all required forecast-hour IDX files.

    Returns:
      (available, missing_urls)
    """
    missing = []

    for fxx in required_fxx:
        url = nbm_idx_url(cycle=cycle, product=product, fxx=fxx, domain=domain)
        if not url_exists(url):
            missing.append(url)

    return len(missing) == 0, missing


def latest_available_cycle(
    product: str,
    required_fxx: Iterable[int],
    domain: str = "co",
    max_cycle_lag_hours: int = 48,
    verbose: bool = True,
) -> datetime:
    """
    Find the newest NBM cycle with all required IDX files available.

    This avoids hard-coding a 12-hour lag. It checks the current 6-hour cycle,
    then steps backward by 6 hours until it finds a complete cycle.

    Example:
      latest_available_cycle("core", range(1, 49))
      latest_available_cycle("qmd", [6, 12, 18, 24, 30, 36, 42, 48])
    """
    latest = floor_to_6hr_cycle()
    required_fxx = list(required_fxx)

    for lag in range(0, max_cycle_lag_hours + 1, 6):
        cycle = latest - timedelta(hours=lag)

        if verbose:
            print(
                f"Checking NBM {product.upper()} cycle "
                f"{cycle:%Y-%m-%d %HZ} for fxx={required_fxx[0]:03d}-{required_fxx[-1]:03d}"
            )

        available, missing = check_cycle_availability(
            cycle=cycle,
            product=product,
            required_fxx=required_fxx,
            domain=domain,
        )

        if available:
            if verbose:
                print(f"Using NBM {product.upper()} cycle {cycle:%Y-%m-%d %HZ}")
            return cycle

        if verbose:
            print(
                f"Cycle {cycle:%Y-%m-%d %HZ} incomplete: "
                f"{len(missing)} missing IDX files"
            )

    raise RuntimeError(
        f"No complete NBM {product.upper()} cycle found within "
        f"{max_cycle_lag_hours} hours for required forecast hours: {required_fxx}"
    )


def latest_core_cycle_48hr() -> datetime:
    """Latest complete NBM Core cycle through f048."""
    return latest_available_cycle(
        product="core",
        required_fxx=range(1, 49),
        domain="co",
    )


def latest_qmd_cycle_48hr_6hr_blocks() -> datetime:
    """Latest complete NBM QMD cycle for 6-hour block products through f048."""
    return latest_available_cycle(
        product="qmd",
        required_fxx=[6, 12, 18, 24, 30, 36, 42, 48],
        domain="co",
    )


def latest_qmd_cycle_hourly_48hr() -> datetime:
    """Latest complete NBM QMD cycle through hourly f048."""
    return latest_available_cycle(
        product="qmd",
        required_fxx=range(1, 49),
        domain="co",
    )
