from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import requests
import xarray as xr


DOCS = Path("docs")
DATA = Path("data")
AWS_BASE = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
DOMAIN = "co"
MPS_TO_MPH = 2.2369362921

SITE = {
    "site": "KRNO",
    "site_name": "Reno-Tahoe International Airport",
    "lat": 39.4991,
    "lon": -119.7681,
}

WIND_THRESHOLDS_MPH = [
    (65, 5),
    (58, 4),
    (45, 3),
    (30, 2),
    (20, 1),
]

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


def candidate_cycles(dt: datetime | None = None, count: int = 8) -> list[datetime]:
    cycle = latest_cycle_floor(dt)
    return [cycle - timedelta(hours=6 * i) for i in range(count)]


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def aws_grib_url(cycle: datetime, product: str, fxx: int) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return f"{AWS_BASE}/blend.{ymd}/{hh}/{product}/blend.t{hh}z.{product}.f{fxx:03d}.{DOMAIN}.grib2"


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    return response.text


def parse_idx(idx_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lines = idx_text.splitlines()

    for i, line in enumerate(lines):
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue

        start_byte = int(parts[1])
        end_byte = None
        if i + 1 < len(lines):
            end_byte = int(lines[i + 1].split(":", 2)[1]) - 1

        rows.append({"msg_num": int(parts[0]), "start_byte": start_byte, "end_byte": end_byte, "line": line})

    return rows


def find_latest_available_cycle(product: str, required_fxx: list[int]) -> datetime:
    for cycle in candidate_cycles():
        ok = True
        for fxx in required_fxx:
            try:
                response = requests.head(aws_grib_url(cycle, product, fxx) + ".idx", timeout=15)
                if response.status_code != 200:
                    ok = False
                    break
            except requests.RequestException:
                ok = False
                break
        if ok:
            return cycle
    raise RuntimeError(f"No recent complete NBM {product} AWS cycle found for {required_fxx}")


def select_core_wind_rows(rows: list[dict[str, Any]], fxx: int) -> dict[str, dict[str, Any]]:
    expected_time = f":{fxx} hour fcst:"
    selected: dict[str, dict[str, Any]] = {}

    for row in rows:
        line = row["line"]
        if expected_time not in line or "ens std dev" in line:
            continue
        if ":GUST:10 m above ground:" in line:
            selected["gust"] = row
        elif ":WIND:10 m above ground:" in line:
            selected["wind"] = row
        elif ":WDIR:10 m above ground:" in line:
            selected["direction"] = row

    return selected


def download_one_message(grib_url: str, row: dict[str, Any], out_path: Path) -> None:
    end = row["end_byte"]
    headers = {"Range": f"bytes={row['start_byte']}-{end}"} if end is not None else {"Range": f"bytes={row['start_byte']}-"}
    response = requests.get(grib_url, headers=headers, timeout=120)
    response.raise_for_status()
    out_path.write_bytes(response.content)


def find_lat_lon_names(ds: xr.Dataset) -> tuple[str, str]:
    lat_name = next((name for name in ["latitude", "lat", "LAT"] if name in ds), None)
    lon_name = next((name for name in ["longitude", "lon", "LON"] if name in ds), None)
    if lat_name is None or lon_name is None:
        raise RuntimeError(f"Could not find latitude/longitude variables in {list(ds.variables)}")
    return lat_name, lon_name


def nearest_grid_indices(ds: xr.Dataset) -> tuple[int, int]:
    lat_name, lon_name = find_lat_lon_names(ds)
    lat = ds[lat_name].values
    lon = ds[lon_name].values
    target_lon = SITE["lon"] + 360 if np.nanmax(lon) > 180 and SITE["lon"] < 0 else SITE["lon"]
    distance = (lat - SITE["lat"]) ** 2 + (lon - target_lon) ** 2
    iy, ix = np.unravel_index(np.nanargmin(distance), distance.shape)
    return int(iy), int(ix)


def extract_gridpoint_value(grib_path: Path) -> float:
    ds = xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    try:
        iy, ix = nearest_grid_indices(ds)
        data_vars = list(ds.data_vars)
        if not data_vars:
            raise RuntimeError("No data variables found in GRIB message")
        values = ds[data_vars[0]].values.squeeze()
        if values.ndim != 2:
            raise RuntimeError(f"Expected 2D grid after squeeze, got {values.shape}")
        return float(values[iy, ix])
    finally:
        ds.close()


def extract_core_wind_hour(cycle: datetime, fxx: int, tmp: Path) -> dict[str, Any] | None:
    grib_url = aws_grib_url(cycle, "core", fxx)
    idx_text = fetch_text(grib_url + ".idx")
    selected = select_core_wind_rows(parse_idx(idx_text), fxx)
    if "gust" not in selected:
        return None

    values: dict[str, float | None] = {"gust_mps": None, "wind_mps": None, "direction_deg": None}
    # First AWS pass keeps runtime reasonable by decoding the primary timeline
    # driver only. Sustained wind/direction can be layered in once caching lands.
    for key, row in {"gust": selected["gust"]}.items():
        msg_path = tmp / f"nbm_core_{cycle:%Y%m%d%H}_f{fxx:03d}_{key}.grib2"
        download_one_message(grib_url, row, msg_path)
        value = extract_gridpoint_value(msg_path)
        if key == "gust":
            values["gust_mps"] = value
        elif key == "wind":
            values["wind_mps"] = value
        elif key == "direction":
            values["direction_deg"] = value

    gust_mph = (values["gust_mps"] or 0.0) * MPS_TO_MPH
    wind_mph = (values["wind_mps"] or 0.0) * MPS_TO_MPH
    return {
        "fxx": fxx,
        "valid_utc": iso(cycle + timedelta(hours=fxx)),
        "gust_mph": round(gust_mph, 1),
        "wind_mph": round(wind_mph, 1) if values["wind_mps"] is not None else None,
        "direction_deg": int(round(values["direction_deg"] or 0)) if values["direction_deg"] is not None else None,
    }


def wind_risk_from_gust(gust_mph: float) -> int:
    for threshold, level in WIND_THRESHOLDS_MPH:
        if gust_mph >= threshold:
            return level
    return 0


def block_label_for_wind(hourly_values: list[dict[str, Any]]) -> str:
    if not hourly_values:
        return "No forecast value"
    peak = max(hourly_values, key=lambda row: row.get("gust_mph") or 0)
    gust = peak.get("gust_mph")
    wind = peak.get("wind_mph")
    direction = peak.get("direction_deg")
    parts = []
    if gust is not None:
        parts.append(f"Gust {gust:.0f} mph")
    if wind is not None:
        parts.append(f"Wind {wind:.0f} mph")
    if direction is not None:
        parts.append(f"{direction:03d} deg")
    return " / ".join(parts)


def apply_core_wind(timeline: dict[str, Any], threats_payload: dict[str, Any]) -> None:
    cycle = find_latest_available_cycle("core", [1, 72])
    cycle_iso = iso(cycle)
    fxx_values = list(range(3, 73, 3))

    hourly_by_fxx: dict[int, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for fxx in fxx_values:
            row = extract_core_wind_hour(cycle, fxx, tmp)
            if row:
                hourly_by_fxx[fxx] = row

    if not hourly_by_fxx:
        raise RuntimeError("NBM core wind extraction returned no gridpoint values")

    best_block: dict[str, Any] | None = None
    best_hazard: dict[str, Any] | None = None
    max_gust = 0.0
    max_risk = 0

    for block, hazards in zip(timeline["blocks"], timeline["block_hazards"]):
        start_fxx = int(block["start_fxx"])
        end_fxx = int(block["end_fxx"])
        block_hours = [row for fxx, row in hourly_by_fxx.items() if start_fxx <= fxx <= end_fxx]
        if not block_hours:
            nearest = min(hourly_by_fxx, key=lambda fxx: abs(fxx - end_fxx))
            if abs(nearest - end_fxx) <= 2:
                block_hours = [hourly_by_fxx[nearest]]

        if not block_hours:
            continue

        peak = max(block_hours, key=lambda row: row.get("gust_mph") or 0)
        gust = float(peak.get("gust_mph") or 0.0)
        risk = wind_risk_from_gust(gust)
        max_gust = max(max_gust, gust)
        max_risk = max(max_risk, risk)
        block["WIND"] = risk

        hazard = hazards["WIND"]
        hazard.update(
            {
                "label": "Wind",
                "name": "Wind",
                "risk": risk,
                "risk_label": RISK_LABELS[risk],
                "level": risk,
                "impact_level": risk,
                "prob": None,
                "probability": None,
                "metric": block_label_for_wind(block_hours),
                "driver": "NBM core deterministic wind/gust at KRNO",
                "source_fxx": int(peak["fxx"]),
                "peak_valid_utc": peak["valid_utc"],
                "data_status": "live",
                "method": "nbm_core_aws_gridpoint",
                "source": f"NOAA NBM core AWS {cycle:%HZ}",
                "hourly_values": block_hours,
                "values": {
                    "peak_gust_mph": round(gust, 1),
                    "wind_mph": peak.get("wind_mph"),
                    "direction_deg": peak.get("direction_deg"),
                },
            }
        )

        if best_hazard is None or risk > best_hazard["risk"] or (risk == best_hazard["risk"] and gust > max_gust):
            best_block = block
            best_hazard = hazard

    threat = threats_payload["threats"]["WIND"]
    threat.update(
        {
            "title": "Wind",
            "name": "Wind",
            "prob": None,
            "probability": None,
            "risk": max_risk,
            "risk_level": max_risk,
            "risk_label": RISK_LABELS[max_risk],
            "level": max_risk,
            "impact_level": max_risk,
            "metric": f"Peak gust {max_gust:.0f} mph",
            "display_label": "72-hr peak gust",
            "display_value": f"{max_gust:.0f} mph",
            "window": "72 hr",
            "peak_start_fxx": best_block.get("start_fxx") if best_block else None,
            "peak_end_fxx": best_block.get("end_fxx") if best_block else None,
            "source_fxx": best_hazard.get("source_fxx") if best_hazard else None,
            "peak_valid_utc": best_hazard.get("peak_valid_utc") if best_hazard else None,
            "driver": "NBM core deterministic wind/gust at KRNO",
            "methodology": "Initial NBM AWS implementation uses live core wind/gust for the timeline. QMD probabilities will be layered into risk cards next.",
            "data_status": "live",
            "method": "nbm_core_aws_gridpoint",
            "source": f"NOAA NBM core AWS {cycle:%HZ}",
            "source_cycle_utc_iso": cycle_iso,
        }
    )

    for hazard in threats_payload["hazards"]:
        if hazard["id"] == "WIND":
            hazard.update({"risk": max_risk, "probability": None, "level": max_risk})

    timeline["source"] = f"NOAA NBM core AWS {cycle:%HZ}"
    timeline["cycle_utc_iso"] = cycle_iso
    timeline["cycle"] = f"NBM {cycle:%HZ}"
    threats_payload["source"] = timeline["source"]
    threats_payload["cycle_utc_iso"] = cycle_iso
    threats_payload["cycle"] = timeline["cycle"]


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

    try:
        apply_core_wind(timeline, threats_payload)
    except Exception as exc:
        timeline.setdefault("extraction_errors", {})["WIND"] = str(exc)
        threats_payload.setdefault("extraction_errors", {})["WIND"] = str(exc)

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
