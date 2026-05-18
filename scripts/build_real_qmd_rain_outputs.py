from __future__ import annotations

import json
import math
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
import xarray as xr


DOCS = Path("docs")
DATA = Path("data")
DOCS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

KRNO_LAT = 39.4991
KRNO_LON = -119.7681

MM_TO_IN = 0.03937007874015748

# KRNO / Reno-area rain/flooding thresholds using official QMD 6-hour accumulation probabilities.
# QMD thresholds are in millimeters.
RAIN_THRESHOLDS = {
    "gt_0p10_in_6hr": {
        "threshold_in": 0.10,
        "threshold_mm": 2.54,
        "impact_level": 2,
        "label": ">0.10 in / 6 hr",
    },
    "gt_0p25_in_6hr": {
        "threshold_in": 0.25,
        "threshold_mm": 6.35,
        "impact_level": 3,
        "label": ">0.25 in / 6 hr",
    },
    "gt_0p50_in_6hr": {
        "threshold_in": 0.50,
        "threshold_mm": 12.7,
        "impact_level": 4,
        "label": ">0.50 in / 6 hr",
    },
    "gt_1p00_in_6hr": {
        "threshold_in": 1.00,
        "threshold_mm": 25.4,
        "impact_level": 5,
        "label": ">1.00 in / 6 hr",
    },
}

# Use official QMD 6-hour windows.
# f006 = 0-6 hr, f012 = 6-12 hr, etc.
QMD_RAIN_WINDOWS = [
    {"fxx": 6, "start_hour": 0, "end_hour": 6},
    {"fxx": 12, "start_hour": 6, "end_hour": 12},
    {"fxx": 18, "start_hour": 12, "end_hour": 18},
    {"fxx": 24, "start_hour": 18, "end_hour": 24},
    {"fxx": 30, "start_hour": 24, "end_hour": 30},
    {"fxx": 36, "start_hour": 30, "end_hour": 36},
    {"fxx": 42, "start_hour": 36, "end_hour": 42},
    {"fxx": 48, "start_hour": 42, "end_hour": 48},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_cycle_utc() -> datetime:
    """
    Use the most recent likely complete 6-hour NBM cycle.

    We lag one cycle to reduce failures from partially available NOMADS files.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    cycle = now.replace(hour=cycle_hour)
    return cycle - timedelta(hours=6)


def qmd_grib_url(cycle: datetime, fxx: int) -> str:
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
        f"blend.{ymd}/{hh}/qmd/blend.t{hh}z.qmd.f{fxx:03d}.co.grib2"
    )


def qmd_idx_url(cycle: datetime, fxx: int) -> str:
    return qmd_grib_url(cycle, fxx) + ".idx"


def fetch_text(url: str, timeout: int = 60) -> str:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def parse_idx(idx_text: str) -> list[dict[str, Any]]:
    rows = []
    lines = idx_text.splitlines()

    for i, line in enumerate(lines):
        parts = line.split(":")
        if len(parts) < 3:
            continue

        try:
            message_no = int(parts[0])
            start_byte = int(parts[1])
        except ValueError:
            continue

        end_byte = None
        if i + 1 < len(lines):
            next_parts = lines[i + 1].split(":")
            if len(next_parts) >= 2:
                try:
                    end_byte = int(next_parts[1]) - 1
                except ValueError:
                    end_byte = None

        rows.append(
            {
                "message_no": message_no,
                "start_byte": start_byte,
                "end_byte": end_byte,
                "line": line,
            }
        )

    return rows


def threshold_text_variants(mm: float) -> list[str]:
    """
    Build text variants because IDX thresholds can appear as:
    2.54, 2.540, 12.7, 12.70, etc.
    """
    variants = {
        f"{mm:g}",
        f"{mm:.1f}",
        f"{mm:.2f}",
        f"{mm:.3f}",
    }

    # Avoid variants like 2.5 for 2.54 when .1f rounds too coarsely.
    if abs(mm - 2.54) < 0.001:
        variants.update({"2.54", "2.540"})
    if abs(mm - 6.35) < 0.001:
        variants.update({"6.35", "6.350"})
    if abs(mm - 12.7) < 0.001:
        variants.update({"12.7", "12.70", "12.700"})
    if abs(mm - 25.4) < 0.001:
        variants.update({"25.4", "25.40", "25.400"})
    if abs(mm - 50.8) < 0.001:
        variants.update({"50.8", "50.80", "50.800"})

    return sorted(variants, key=len, reverse=True)


def line_matches_window(line: str, start_hour: int, end_hour: int) -> bool:
    upper = line.upper()
    expected = f"{start_hour}-{end_hour} HOUR ACC FCST".upper()
    return expected in upper


def find_qmd_apcp_probability_row(
    idx_rows: list[dict[str, Any]],
    start_hour: int,
    end_hour: int,
    threshold_mm: float,
) -> dict[str, Any] | None:
    for row in idx_rows:
        line = row["line"]
        upper = line.upper()

        if ":APCP:" not in upper:
            continue
        if "PROB >" not in upper:
            continue
        if not line_matches_window(line, start_hour, end_hour):
            continue

        for variant in threshold_text_variants(threshold_mm):
            if f"prob >{variant}" in line:
                return row
            if f"PROB >{variant}" in upper:
                return row

    return None


def find_qmd_apcp_percentile_row(
    idx_rows: list[dict[str, Any]],
    start_hour: int,
    end_hour: int,
    percentile: int = 50,
) -> dict[str, Any] | None:
    wanted_a = f"{percentile}% level".upper()
    wanted_b = f":{percentile}% LEVEL".upper()

    for row in idx_rows:
        line = row["line"]
        upper = line.upper()

        if ":APCP:" not in upper:
            continue
        if not line_matches_window(line, start_hour, end_hour):
            continue
        if wanted_a in upper or wanted_b in upper:
            return row

    return None


def find_qmd_apcp_deterministic_row(
    idx_rows: list[dict[str, Any]],
    start_hour: int,
    end_hour: int,
) -> dict[str, Any] | None:
    for row in idx_rows:
        line = row["line"]
        upper = line.upper()

        if ":APCP:" not in upper:
            continue
        if "PROB >" in upper:
            continue
        if "% LEVEL" in upper:
            continue
        if "PERCENTILE" in upper:
            continue
        if not line_matches_window(line, start_hour, end_hour):
            continue

        return row

    return None


def download_grib_message(grib_url: str, row: dict[str, Any], path: Path) -> None:
    headers = {}
    if row.get("end_byte") is not None:
        headers["Range"] = f"bytes={row['start_byte']}-{row['end_byte']}"
    else:
        headers["Range"] = f"bytes={row['start_byte']}-"

    last_error = None

    for attempt in range(1, 4):
        try:
            response = requests.get(grib_url, headers=headers, timeout=90)
            response.raise_for_status()

            content = response.content
            if len(content) < 100:
                raise RuntimeError(f"Downloaded GRIB message too small: {len(content)} bytes")

            path.write_bytes(content)
            return

        except Exception as exc:
            last_error = exc
            if attempt == 3:
                raise RuntimeError(f"Failed to download GRIB message after 3 attempts: {exc}") from exc

    raise RuntimeError(f"Failed to download GRIB message: {last_error}")


def normalize_lon(lon: float) -> float:
    if lon < 0:
        return lon + 360.0
    return lon


def find_lat_lon_names(ds: xr.Dataset) -> tuple[str, str]:
    lat_candidates = ["latitude", "lat", "gridlat_0"]
    lon_candidates = ["longitude", "lon", "gridlon_0"]

    lat_name = None
    lon_name = None

    for name in lat_candidates:
        if name in ds.coords or name in ds.variables:
            lat_name = name
            break

    for name in lon_candidates:
        if name in ds.coords or name in ds.variables:
            lon_name = name
            break

    if lat_name is None or lon_name is None:
        raise RuntimeError(f"Could not find lat/lon coordinates. Variables: {list(ds.variables)}")

    return lat_name, lon_name


def nearest_grid_value(ds: xr.Dataset) -> tuple[str, float]:
    data_vars = list(ds.data_vars)
    if not data_vars:
        raise RuntimeError("No data variables in GRIB message.")

    var_name = data_vars[0]

    lat_name, lon_name = find_lat_lon_names(ds)
    lat = ds[lat_name]
    lon = ds[lon_name]

    target_lon = normalize_lon(KRNO_LON)

    # Handle 1D or 2D coordinate grids.
    if lat.ndim == 1 and lon.ndim == 1:
        lat_idx = int(abs(lat - KRNO_LAT).argmin())
        lon_idx = int(abs(lon - target_lon).argmin())
        value = ds[var_name].isel({lat_name: lat_idx, lon_name: lon_idx}).values
    else:
        lon_values = lon.values
        lat_values = lat.values

        # Some grids use -180 to 180, others 0 to 360.
        if float(lon_values.max()) > 180:
            target_lon_for_grid = target_lon
        else:
            target_lon_for_grid = KRNO_LON

        dist2 = (lat_values - KRNO_LAT) ** 2 + (lon_values - target_lon_for_grid) ** 2
        iy, ix = [int(v) for v in divmod(int(dist2.argmin()), dist2.shape[1])]

        dims = ds[var_name].dims
        indexers = {}
        if lat.dims:
            for dim, idx in zip(lat.dims, [iy, ix]):
                if dim in dims:
                    indexers[dim] = idx

        value = ds[var_name].isel(indexers).values

    value_float = float(value.squeeze())

    if math.isnan(value_float):
        raise RuntimeError(f"Nearest value for {var_name} is NaN.")

    return var_name, value_float


def extract_value_from_message(message_path: Path) -> tuple[str, float]:
    try:
        ds = xr.open_dataset(
            message_path,
            engine="cfgrib",
            backend_kwargs={
                "indexpath": "",
                "errors": "ignore",
            },
        )

        try:
            return nearest_grid_value(ds)
        finally:
            ds.close()

    except Exception as exc:
        raise RuntimeError(f"Could not read GRIB message {message_path}: {exc}") from exc


def extract_qmd_value(grib_url: str, row: dict[str, Any], label: str) -> tuple[str, float]:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / f"{label}.grib2"
        download_grib_message(grib_url, row, path)
        return extract_value_from_message(path)


def probability_to_likelihood(probability: float) -> int:
    """
    Return 1-5 likelihood category.

    1 = Extremely unlikely, <10%
    2 = Unlikely, 10-32%
    3 = About as likely as not, 33-65%
    4 = Likely, 66-89%
    5 = Very likely, >=90%
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
    """
    Probability x impact risk matrix.

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


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def evaluate_window_risk(window: dict[str, Any]) -> dict[str, Any]:
    candidates = []

    for key, threshold in RAIN_THRESHOLDS.items():
        probability = float(window["threshold_probabilities"].get(key, {}).get("probability_percent", 0.0))
        impact_level = int(threshold["impact_level"])
        risk = matrix_risk(probability, impact_level)

        candidates.append(
            {
                "threshold_key": key,
                "threshold_in": threshold["threshold_in"],
                "threshold_mm": threshold["threshold_mm"],
                "impact_level": impact_level,
                "probability": probability,
                "risk": risk,
                "risk_label": risk_label(risk),
                "label": threshold["label"],
                "fxx": window["fxx"],
                "start_hour": window["start_hour"],
                "end_hour": window["end_hour"],
                "valid_utc": window["valid_utc"],
            }
        )

    best = max(candidates, key=lambda c: (c["risk"], c["impact_level"], c["probability"]))

    return {
        "best": best,
        "candidates": candidates,
    }


def extract_rain_windows(cycle: datetime) -> list[dict[str, Any]]:
    results = []

    for window in QMD_RAIN_WINDOWS:
        fxx = window["fxx"]
        start_hour = window["start_hour"]
        end_hour = window["end_hour"]

        print(f"Processing QMD rain f{fxx:03d} ({start_hour}-{end_hour} hr)")

        grib_url = qmd_grib_url(cycle, fxx)
        idx_url = qmd_idx_url(cycle, fxx)

        try:
            idx_text = fetch_text(idx_url)
            idx_rows = parse_idx(idx_text)
        except Exception as exc:
            results.append(
                {
                    **window,
                    "valid_utc": (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z"),
                    "status": "error",
                    "message": f"Could not fetch/parse IDX: {exc}",
                    "threshold_probabilities": {},
                    "p50_apcp_in": None,
                    "deterministic_apcp_in": None,
                }
            )
            continue

        threshold_probs = {}

        for key, threshold in RAIN_THRESHOLDS.items():
            row = find_qmd_apcp_probability_row(
                idx_rows=idx_rows,
                start_hour=start_hour,
                end_hour=end_hour,
                threshold_mm=threshold["threshold_mm"],
            )

            if row is None:
                threshold_probs[key] = {
                    **threshold,
                    "probability_percent": 0.0,
                    "status": "missing",
                    "idx_line": None,
                }
                continue

            try:
                var_name, probability = extract_qmd_value(
                    grib_url=grib_url,
                    row=row,
                    label=f"qmd_rain_f{fxx:03d}_{key}",
                )

                threshold_probs[key] = {
                    **threshold,
                    "probability_percent": round(float(probability), 1),
                    "status": "ok",
                    "variable": var_name,
                    "idx_line": row["line"],
                }

            except Exception as exc:
                threshold_probs[key] = {
                    **threshold,
                    "probability_percent": 0.0,
                    "status": "error",
                    "message": str(exc),
                    "idx_line": row["line"],
                }

        # Display value: prefer QMD P50 6-hour APCP. Fallback to deterministic APCP.
        p50_apcp_in = None
        p50_idx_line = None
        deterministic_apcp_in = None
        deterministic_idx_line = None

        p50_row = find_qmd_apcp_percentile_row(
            idx_rows=idx_rows,
            start_hour=start_hour,
            end_hour=end_hour,
            percentile=50,
        )

        if p50_row is not None:
            try:
                _, p50_mm = extract_qmd_value(
                    grib_url=grib_url,
                    row=p50_row,
                    label=f"qmd_rain_f{fxx:03d}_p50",
                )
                p50_apcp_in = round(float(p50_mm) * MM_TO_IN, 3)
                p50_idx_line = p50_row["line"]
            except Exception as exc:
                print(f"Warning: failed to extract QMD rain P50 f{fxx:03d}: {exc}")

        deterministic_row = find_qmd_apcp_deterministic_row(
            idx_rows=idx_rows,
            start_hour=start_hour,
            end_hour=end_hour,
        )

        if deterministic_row is not None:
            try:
                _, deterministic_mm = extract_qmd_value(
                    grib_url=grib_url,
                    row=deterministic_row,
                    label=f"qmd_rain_f{fxx:03d}_det",
                )
                deterministic_apcp_in = round(float(deterministic_mm) * MM_TO_IN, 3)
                deterministic_idx_line = deterministic_row["line"]
            except Exception as exc:
                print(f"Warning: failed to extract deterministic rain f{fxx:03d}: {exc}")

        display_apcp_in = p50_apcp_in
        display_source = "QMD 50th percentile 6-hour APCP"

        if display_apcp_in is None:
            display_apcp_in = deterministic_apcp_in
            display_source = "QMD deterministic 6-hour APCP"

        result = {
            **window,
            "status": "ok",
            "valid_utc": (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z"),
            "grib_url": grib_url,
            "idx_url": idx_url,
            "threshold_probabilities": threshold_probs,
            "p50_apcp_in": p50_apcp_in,
            "p50_idx_line": p50_idx_line,
            "deterministic_apcp_in": deterministic_apcp_in,
            "deterministic_idx_line": deterministic_idx_line,
            "display_apcp_in": display_apcp_in,
            "display_source": display_source,
        }

        result["risk_evaluation"] = evaluate_window_risk(result)

        results.append(result)

    return results


def main() -> None:
    cycle = latest_cycle_utc()
    generated = utc_now()

    print(f"Building QMD rain/flooding outputs for cycle {cycle:%Y-%m-%d %HZ}")

    windows = extract_rain_windows(cycle)
    ok_windows = [w for w in windows if w.get("status") == "ok"]

    if not ok_windows:
        raise RuntimeError("No QMD rain windows extracted successfully.")

    all_candidates = []
    for window in ok_windows:
        all_candidates.extend(window["risk_evaluation"]["candidates"])

    best = max(all_candidates, key=lambda c: (c["risk"], c["impact_level"], c["probability"]))

    best_window = next(
        w for w in ok_windows
        if w["fxx"] == best["fxx"]
    )

    display_apcp_in = best_window.get("display_apcp_in")
    if display_apcp_in is None:
        # Fallback: use max available display amount.
        available = [w for w in ok_windows if w.get("display_apcp_in") is not None]
        if available:
            best_window = max(available, key=lambda w: w["display_apcp_in"])
            display_apcp_in = best_window.get("display_apcp_in")

    if display_apcp_in is None:
        display_value = "N/A"
    else:
        display_value = f'{display_apcp_in:.2f}"'

    peak_start_fxx = int(best_window["start_hour"])
    peak_end_fxx = int(best_window["end_hour"])

    # Update threats.json.
    threats_path = DOCS / "threats.json"
    threats_payload = load_json(
        threats_path,
        {
            "site": "KRNO",
            "valid_period": "next_48_hours",
            "threats": {},
            "hazards": [],
        },
    )

    threats_payload["generated_utc"] = generated
    threats_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    threats_payload["cycle"] = f"NBM QMD {cycle.strftime('%HZ')}"
    threats_payload.setdefault("threats", {})

    rain_payload = {
        "prob": round(float(best["probability"]), 1),
        "risk": int(best["risk"]),
        "risk_label": risk_label(int(best["risk"])),
        "level": int(best["impact_level"]),
        "metric": best["label"],
        "display_label": "6-hr rainfall",
        "display_value": display_value,
        "rainfall_6hr_in": round(float(display_apcp_in), 3) if display_apcp_in is not None else None,
        "threshold_in": best["threshold_in"],
        "threshold_mm": best["threshold_mm"],
        "window": "6 hr",
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "driver": (
            f"{best['probability']:.1f}% chance {best['label']}"
        ),
        "threshold_probabilities": {
            f"f{w['fxx']:03d}_{w['start_hour']}_{w['end_hour']}hr": w["threshold_probabilities"]
            for w in ok_windows
        },
        "risk_candidates": all_candidates,
        "methodology": (
            "Rain/flooding risk uses official NBM QMD 6-hour APCP exceedance probabilities. "
            "KRNO/Reno drainage thresholds are >0.10, >0.25, >0.50, and >1.00 inches in 6 hours, "
            "mapped to impact levels 2 through 5. Each threshold probability is passed through "
            "the probability x impact risk matrix. Display rainfall uses the QMD 50th percentile "
            "6-hour APCP when available, otherwise deterministic APCP."
        ),
    }

    # Use both keys for compatibility while front-end naming is settled.
    threats_payload["threats"]["RAIN"] = rain_payload
    threats_payload["threats"]["RAIN_FLOODING"] = rain_payload

    hazards = threats_payload.setdefault("hazards", [])

    def update_hazard(hazard_id: str, hazard_name: str) -> None:
        found = False

        for hazard in hazards:
            if hazard.get("id") == hazard_id:
                hazard.update(
                    {
                        "id": hazard_id,
                        "name": hazard_name,
                        "risk_level": int(best["risk"]),
                        "risk_label": risk_label(int(best["risk"])),
                        "impact_level": int(best["impact_level"]),
                        "probability": round(float(best["probability"]), 1),
                        "peak_start_fxx": peak_start_fxx,
                        "peak_end_fxx": peak_end_fxx,
                        "metric": best["label"],
                        "display_label": "6-hr rainfall",
                        "display_value": display_value,
                        "rainfall_6hr_in": round(float(display_apcp_in), 3) if display_apcp_in is not None else None,
                        "driver": f"{best['probability']:.1f}% chance {best['label']}",
                    }
                )
                found = True
                break

        if not found:
            hazards.append(
                {
                    "id": hazard_id,
                    "name": hazard_name,
                    "risk_level": int(best["risk"]),
                    "risk_label": risk_label(int(best["risk"])),
                    "impact_level": int(best["impact_level"]),
                    "probability": round(float(best["probability"]), 1),
                    "peak_start_fxx": peak_start_fxx,
                    "peak_end_fxx": peak_end_fxx,
                    "metric": best["label"],
                    "display_label": "6-hr rainfall",
                    "display_value": display_value,
                    "rainfall_6hr_in": round(float(display_apcp_in), 3) if display_apcp_in is not None else None,
                    "driver": f"{best['probability']:.1f}% chance {best['label']}",
                }
            )

    update_hazard("RAIN", "Rain/Flooding")

    # Update timeline.json.
    timeline_path = DOCS / "timeline.json"
    timeline_payload = load_json(
        timeline_path,
        {
            "site": "KRNO",
            "block_hours": 6,
            "blocks": [],
            "block_hazards": [],
        },
    )

    timeline_payload["generated_utc"] = generated
    timeline_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    timeline_payload["cycle"] = f"NBM QMD {cycle.strftime('%HZ')}"
    timeline_payload["block_hours"] = 6

    blocks = timeline_payload.setdefault("blocks", [])
    block_hazards = timeline_payload.setdefault("block_hazards", [])

    while len(blocks) < 8:
        bi = len(blocks)
        blocks.append({"start_fxx": bi * 6, "end_fxx": bi * 6 + 6})

    while len(block_hazards) < 8:
        block_hazards.append({})

    for i, window in enumerate(ok_windows[:8]):
        block_risk = window["risk_evaluation"]["best"]

        blocks[i]["start_fxx"] = int(window["start_hour"])
        blocks[i]["end_fxx"] = int(window["end_hour"])
        blocks[i]["RAIN"] = window.get("display_apcp_in")

        block_hazards[i]["RAIN"] = {
            "prob": round(float(block_risk["probability"]), 1),
            "risk": int(block_risk["risk"]),
            "level": int(block_risk["impact_level"]),
            "threshold_in": block_risk["threshold_in"],
            "threshold_mm": block_risk["threshold_mm"],
            "metric": block_risk["label"],
            "window": "6 hr",
            "rainfall_6hr_in": window.get("display_apcp_in"),
            "driver": f"{block_risk['probability']:.1f}% chance {block_risk['label']}",
        }

        block_hazards[i]["RAIN_FLOODING"] = block_hazards[i]["RAIN"]

    threats_path.write_text(json.dumps(threats_payload, indent=2))
    timeline_path.write_text(json.dumps(timeline_payload, indent=2))

    diagnostic = {
        "site": "KRNO",
        "source": "NBM QMD direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "hazard": "RAIN_FLOODING",
        "selected_risk": best,
        "selected_window": {
            "fxx": best_window["fxx"],
            "start_hour": best_window["start_hour"],
            "end_hour": best_window["end_hour"],
            "valid_utc": best_window["valid_utc"],
            "display_apcp_in": best_window.get("display_apcp_in"),
            "display_source": best_window.get("display_source"),
        },
        "thresholds": RAIN_THRESHOLDS,
        "windows": windows,
        "methodology": (
            "QMD 6-hour APCP probabilities are used directly for KRNO/Reno drainage risk. "
            "Thresholds are >0.10, >0.25, >0.50, and >1.00 inches in 6 hours. "
            "The highest probability x impact matrix result determines the rain/flooding risk."
        ),
    }

    (DATA / "nbm_qmd_rain.json").write_text(json.dumps(diagnostic, indent=2))

    print("Updated docs/threats.json RAIN / RAIN_FLOODING")
    print("Updated docs/timeline.json RAIN / RAIN_FLOODING")
    print("Wrote data/nbm_qmd_rain.json")


if __name__ == "__main__":
    main()
