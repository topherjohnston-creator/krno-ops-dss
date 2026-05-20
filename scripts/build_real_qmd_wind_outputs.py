from __future__ import annotations

import argparse
import json
import math
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

MPS_TO_MPH = 2.2369362920544
MPS_TO_KT = 1.9438444924406

AIRPORT_THRESHOLDS_MPH = {
    "gt_30_mph": 30.0,
    "gt_45_mph": 45.0,
    "gt_58_mph": 58.0,
    "gt_65_mph": 65.0,
}

WIND_IMPACT_LEVELS = {
    "gt_30_mph": 2,
    "gt_45_mph": 3,
    "gt_58_mph": 4,
    "gt_65_mph": 5,
}

FXX_HOURS = list(range(1, 49))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_cycle_arg(cycle_arg: str | None) -> datetime | None:
    if not cycle_arg:
        return None
    cleaned = cycle_arg.strip()
    if not cleaned:
        return None
    if len(cleaned) != 10 or not cleaned.isdigit():
        raise ValueError("Cycle must use YYYYMMDDHH format, for example 2026052012")
    return datetime.strptime(cleaned, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def candidate_cycles(max_back_hours: int = 36) -> list[datetime]:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    base_hour = (now.hour // 6) * 6
    base = now.replace(hour=base_hour)
    return [base - timedelta(hours=lag) for lag in range(6, max_back_hours + 1, 6)]


def latest_cycle_utc() -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle_hour = (now.hour // 6) * 6
    return now.replace(hour=cycle_hour) - timedelta(hours=12)


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
    rows: list[dict[str, Any]] = []
    lines = idx_text.splitlines()
    for i, line in enumerate(lines):
        parts = line.split(":")
        if len(parts) < 5:
            continue
        try:
            message_no = int(parts[0])
            start_byte = int(parts[1])
        except ValueError:
            continue

        end_byte = None
        for j in range(i + 1, len(lines)):
            next_parts = lines[j].split(":")
            if len(next_parts) < 2:
                continue
            try:
                end_byte = int(next_parts[1]) - 1
                break
            except ValueError:
                continue

        rows.append({"message_no": message_no, "start_byte": start_byte, "end_byte": end_byte, "line": line})
    return rows


def is_gust_10m_line(line: str) -> bool:
    upper = line.upper()
    return ":GUST:" in upper and "10 M ABOVE GROUND" in upper


def parse_percentile_level(line: str) -> float | None:
    lower = line.lower()
    if "% level" not in lower:
        return None
    for field in reversed(line.split(":")):
        text = field.strip().lower()
        if text.endswith("% level"):
            try:
                return float(text.replace("% level", "").strip())
            except ValueError:
                return None
    return None


def is_mean_or_deterministic_line(line: str) -> bool:
    upper = line.upper()
    lower = line.lower()
    return "PROB >" not in upper and "% level" not in lower


def is_24hr_max_gust_line(line: str) -> bool:
    lower = line.lower()
    if not is_gust_10m_line(line):
        return False
    if "max" not in lower:
        return False
    return (
        "0-24 hour" in lower
        or "24 hour max" in lower
        or "24-hour max" in lower
        or "24 hr max" in lower
    )


def find_24hr_max_mean_row(idx_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in idx_rows:
        line = row["line"]
        if is_24hr_max_gust_line(line) and is_mean_or_deterministic_line(line):
            return row
    return None


def find_24hr_max_percentile_rows(idx_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in idx_rows:
        line = row["line"]
        if not is_24hr_max_gust_line(line):
            continue
        percentile = parse_percentile_level(line)
        if percentile is None:
            continue
        rows.append({**row, "percentile": percentile})
    rows.sort(key=lambda r: float(r["percentile"]))
    return rows


def hourly_period_matches(line: str, fxx: int) -> bool:
    lower = line.lower()
    start = fxx - 1
    end = fxx
    patterns = [
        f"{start}-{end} hour fcst",
        f"{start}-{end} hour max fcst",
        f"{start}-{end} hour ave fcst",
    ]
    return any(p in lower for p in patterns)


def find_hourly_mean_gust_row(idx_rows: list[dict[str, Any]], fxx: int) -> dict[str, Any] | None:
    for row in idx_rows:
        line = row["line"]
        if not is_gust_10m_line(line):
            continue
        if not hourly_period_matches(line, fxx):
            continue
        if not is_mean_or_deterministic_line(line):
            continue
        if is_24hr_max_gust_line(line):
            continue
        return row
    return None


def download_grib_message(grib_url: str, row: dict[str, Any], path: Path) -> None:
    if row.get("end_byte") is not None:
        headers = {"Range": f"bytes={row['start_byte']}-{row['end_byte']}"}
    else:
        headers = {"Range": f"bytes={row['start_byte']}-"}

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
    return lon + 360.0 if lon < 0 else lon


def find_lat_lon_names(ds: xr.Dataset) -> tuple[str, str]:
    lat_candidates = ["latitude", "lat", "gridlat_0"]
    lon_candidates = ["longitude", "lon", "gridlon_0"]
    lat_name = next((name for name in lat_candidates if name in ds.coords or name in ds.variables), None)
    lon_name = next((name for name in lon_candidates if name in ds.coords or name in ds.variables), None)
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
    target_lon_360 = normalize_lon(KRNO_LON)

    if lat.ndim == 1 and lon.ndim == 1:
        lat_idx = int(abs(lat - KRNO_LAT).argmin())
        lon_idx = int(abs(lon - target_lon_360).argmin())
        value = ds[var_name].isel({lat_name: lat_idx, lon_name: lon_idx}).values
    else:
        lon_values = lon.values
        lat_values = lat.values
        target_lon_for_grid = target_lon_360 if float(lon_values.max()) > 180 else KRNO_LON
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
            backend_kwargs={"indexpath": "", "errors": "ignore"},
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
    return {0: "None", 1: "Little to None", 2: "Minor", 3: "Moderate", 4: "Major", 5: "Extreme"}.get(risk, "Unknown")


def exceedance_from_percentile_curve(percentile_curve: list[dict[str, Any]], threshold_mph: float) -> float:
    pts = [
        (float(p["percentile"]), float(p["gust_mph"]))
        for p in percentile_curve
        if p.get("gust_mph") is not None and not math.isnan(float(p["gust_mph"]))
    ]
    if not pts:
        return 0.0
    pts.sort(key=lambda x: x[0])
    min_pct, min_val = pts[0]
    max_pct, max_val = pts[-1]
    if threshold_mph <= min_val:
        return 100.0
    if threshold_mph > max_val:
        return 0.0
    for (p0, v0), (p1, v1) in zip(pts[:-1], pts[1:]):
        if v0 <= threshold_mph <= v1:
            if abs(v1 - v0) < 1e-9:
                threshold_pct = p1
            else:
                frac = (threshold_mph - v0) / (v1 - v0)
                threshold_pct = p0 + frac * (p1 - p0)
            return round(max(0.0, min(100.0, 100.0 - threshold_pct)), 1)
    return 0.0


def calculate_24hr_threshold_probabilities(percentile_curve: list[dict[str, Any]]) -> dict[str, Any]:
    probabilities = {}
    for key, threshold_mph in AIRPORT_THRESHOLDS_MPH.items():
        prob = exceedance_from_percentile_curve(percentile_curve, threshold_mph)
        probabilities[key] = {
            "threshold_mph": threshold_mph,
            "threshold_mps": round(threshold_mph / MPS_TO_MPH, 3),
            "exceedance_probability_percent": prob,
            "impact_level": WIND_IMPACT_LEVELS[key],
            "method": "linear interpolation across QMD 24-hour maximum gust percentile curve",
        }
    return probabilities


def evaluate_wind_risk(threshold_probs: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for key, threshold_mph in AIRPORT_THRESHOLDS_MPH.items():
        probability = float(threshold_probs[key]["exceedance_probability_percent"])
        impact_level = WIND_IMPACT_LEVELS[key]
        risk = matrix_risk(probability, impact_level)
        candidates.append({
            "threshold_key": key,
            "threshold_mph": threshold_mph,
            "impact_level": impact_level,
            "probability": probability,
            "risk": risk,
            "risk_label": risk_label(risk),
        })
    if all(float(c["probability"]) <= 0 for c in candidates):
        return {"best": {"threshold_key": "none", "threshold_mph": 0.0, "impact_level": 0, "probability": 0.0, "risk": 0, "risk_label": "None"}, "candidates": candidates}
    best = max(candidates, key=lambda c: (c["risk"], c["probability"], c["impact_level"]))
    return {"best": best, "candidates": candidates}


def deterministic_wind_block_risk(block_peak_mph: float | None) -> dict[str, Any]:
    if block_peak_mph is None:
        return {"prob": 0.0, "risk": 0, "risk_label": "None", "level": 0, "threshold_mph": None, "driver": "No QMD hourly mean gust available"}
    if block_peak_mph >= 65:
        level, threshold = 5, 65
    elif block_peak_mph >= 58:
        level, threshold = 4, 58
    elif block_peak_mph >= 45:
        level, threshold = 3, 45
    elif block_peak_mph >= 30:
        level, threshold = 2, 30
    else:
        level, threshold = 0, None
    risk = level if level > 0 else 0
    return {
        "prob": 100.0 if level > 0 else 0.0,
        "risk": risk,
        "risk_label": risk_label(risk),
        "level": level,
        "threshold_mph": threshold,
        "gust_mph": round(block_peak_mph, 1),
        "driver": f"Peak QMD hourly mean gust {block_peak_mph:.1f} mph",
        "methodology": "Timeline wind timing uses QMD hourly mean gust. Risk color is based on the block peak mean gust threshold.",
    }


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def select_cycle(explicit_cycle: datetime | None = None) -> datetime:
    if explicit_cycle:
        return explicit_cycle
    for cycle in candidate_cycles():
        try:
            idx_text = fetch_text(qmd_idx_url(cycle, 24), timeout=30)
            idx_rows = parse_idx(idx_text)
            if find_24hr_max_mean_row(idx_rows) is not None:
                return cycle
        except Exception:
            continue
    return latest_cycle_utc()


def extract_24hr_max_wind(cycle: datetime) -> dict[str, Any]:
    fxx = 24
    grib_url = qmd_grib_url(cycle, fxx)
    idx_url = qmd_idx_url(cycle, fxx)
    idx_text = fetch_text(idx_url)
    idx_rows = parse_idx(idx_text)

    mean_row = find_24hr_max_mean_row(idx_rows)
    if mean_row is None:
        raise RuntimeError("Could not find QMD 24-hour maximum 10-meter gust mean row in f024 IDX.")

    var_name, mean_mps = extract_qmd_value(grib_url=grib_url, row=mean_row, label="qmd_24hr_max_gust_mean")

    percentile_curve = []
    for row in find_24hr_max_percentile_rows(idx_rows):
        pct = float(row["percentile"])
        try:
            _, value_mps = extract_qmd_value(grib_url=grib_url, row=row, label=f"qmd_24hr_max_gust_p{pct:g}")
            percentile_curve.append({
                "percentile": pct,
                "gust_mps": round(float(value_mps), 3),
                "gust_mph": round(float(value_mps) * MPS_TO_MPH, 2),
                "gust_kt": round(float(value_mps) * MPS_TO_KT, 2),
                "idx_line": row["line"],
            })
        except Exception as exc:
            print(f"Warning: skipped 24-hr max gust percentile p{pct:g}: {exc}")

    return {
        "fxx": fxx,
        "valid_utc": (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z"),
        "grib_url": grib_url,
        "idx_url": idx_url,
        "variable": var_name,
        "mean_mps": round(float(mean_mps), 3),
        "mean_mph": round(float(mean_mps) * MPS_TO_MPH, 1),
        "mean_kt": round(float(mean_mps) * MPS_TO_KT, 1),
        "mean_idx_line": mean_row["line"],
        "percentile_curve": percentile_curve,
    }


def extract_hourly_mean_wind(cycle: datetime) -> list[dict[str, Any]]:
    results = []
    for fxx in FXX_HOURS:
        print(f"Processing QMD hourly mean wind f{fxx:03d}")
        grib_url = qmd_grib_url(cycle, fxx)
        idx_url = qmd_idx_url(cycle, fxx)
        valid_utc = (cycle + timedelta(hours=fxx)).isoformat().replace("+00:00", "Z")
        try:
            idx_text = fetch_text(idx_url)
            idx_rows = parse_idx(idx_text)
            row = find_hourly_mean_gust_row(idx_rows, fxx)
            if row is None:
                results.append({"fxx": fxx, "valid_utc": valid_utc, "status": "missing", "message": "No QMD hourly mean gust row found", "mean_gust_mps": None, "mean_gust_mph": None, "mean_gust_kt": None})
                continue
            var_name, gust_mps = extract_qmd_value(grib_url=grib_url, row=row, label=f"qmd_hourly_mean_gust_f{fxx:03d}")
            results.append({
                "fxx": fxx,
                "valid_utc": valid_utc,
                "status": "ok",
                "variable": var_name,
                "mean_gust_mps": round(float(gust_mps), 3),
                "mean_gust_mph": round(float(gust_mps) * MPS_TO_MPH, 1),
                "mean_gust_kt": round(float(gust_mps) * MPS_TO_KT, 1),
                "idx_line": row["line"],
                "grib_url": grib_url,
                "idx_url": idx_url,
            })
        except Exception as exc:
            print(f"Warning: failed QMD hourly mean wind f{fxx:03d}: {exc}")
            results.append({"fxx": fxx, "valid_utc": valid_utc, "status": "error", "message": str(exc), "mean_gust_mps": None, "mean_gust_mph": None, "mean_gust_kt": None})
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", default="", help="Optional NBM cycle in YYYYMMDDHH format")
    args = parser.parse_args()

    explicit_cycle = parse_cycle_arg(args.cycle)
    cycle = select_cycle(explicit_cycle)
    generated = utc_now()
    print(f"Building QMD wind outputs for cycle {cycle:%Y-%m-%d %HZ}")

    max24 = extract_24hr_max_wind(cycle)
    hourly_results = extract_hourly_mean_wind(cycle)
    ok_hours = [h for h in hourly_results if h.get("status") == "ok"]

    threshold_probs = calculate_24hr_threshold_probabilities(max24["percentile_curve"])
    risk_eval = evaluate_wind_risk(threshold_probs)
    best = risk_eval["best"]

    if ok_hours:
        peak_hour = max(ok_hours, key=lambda h: h.get("mean_gust_mph") or -999)
        peak_start_fxx = max(1, int(peak_hour["fxx"]) - 1)
        peak_end_fxx = min(48, int(peak_hour["fxx"]) + 1)
    else:
        peak_hour = None
        peak_start_fxx = 1
        peak_end_fxx = 24

    display_gust_mph = float(max24["mean_mph"])
    display_gust_kt = float(max24["mean_kt"])

    threats_path = DOCS / "threats.json"
    threats_payload = load_json(threats_path, {"site": "KRNO", "valid_period": "next_48_hours", "threats": {}, "hazards": []})
    threats_payload["generated_utc"] = generated
    threats_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    threats_payload["cycle"] = f"NBM QMD {cycle.strftime('%HZ')}"
    threats_payload.setdefault("threats", {})

    wind_payload = {
        "prob": round(float(best["probability"]), 1),
        "risk": int(best["risk"]),
        "risk_label": best["risk_label"],
        "level": int(best["impact_level"]),
        "metric": f">{int(best['threshold_mph'])} mph threshold" if best["threshold_mph"] else "Below 30 mph",
        "display_label": "24-hr max gust",
        "display_value": f"{display_gust_mph:.0f} mph",
        "g24_mean_mph": round(display_gust_mph, 1),
        "g24_mean_kt": round(display_gust_kt, 1),
        "threshold_probabilities": threshold_probs,
        "risk_candidates": risk_eval["candidates"],
        "peak_start_fxx": peak_start_fxx,
        "peak_end_fxx": peak_end_fxx,
        "source_idx_line": max24["mean_idx_line"],
        "driver": (
            f"QMD 24-hr max 10m gust mean {display_gust_mph:.1f} mph; "
            f"{best['probability']:.1f}% chance >{best['threshold_mph']:.0f} mph"
            if best["threshold_mph"]
            else f"QMD 24-hr max 10m gust mean {display_gust_mph:.1f} mph"
        ),
        "methodology": (
            "Wind risk card display uses the mean value from the QMD 24-hour maximum 10-meter gust grid. "
            "Wind risk probabilities use the QMD 24-hour maximum gust percentile curve to derive exceedance "
            "probabilities for 30, 45, 58, and 65 mph. Timeline timing uses QMD hourly mean 10-meter gust."
        ),
    }
    threats_payload["threats"]["WIND"] = wind_payload

    hazards = threats_payload.setdefault("hazards", [])
    found = False
    for hazard in hazards:
        if hazard.get("id") == "WIND":
            hazard.update({
                "id": "WIND",
                "name": "Wind",
                "risk_level": int(best["risk"]),
                "risk_label": best["risk_label"],
                "impact_level": int(best["impact_level"]),
                "probability": round(float(best["probability"]), 1),
                "peak_start_fxx": peak_start_fxx,
                "peak_end_fxx": peak_end_fxx,
                "metric": wind_payload["metric"],
                "display_label": "24-hr max gust",
                "display_value": f"{display_gust_mph:.0f} mph",
                "driver": wind_payload["driver"],
            })
            found = True
            break
    if not found:
        hazards.append({
            "id": "WIND",
            "name": "Wind",
            "risk_level": int(best["risk"]),
            "risk_label": best["risk_label"],
            "impact_level": int(best["impact_level"]),
            "probability": round(float(best["probability"]), 1),
            "peak_start_fxx": peak_start_fxx,
            "peak_end_fxx": peak_end_fxx,
            "metric": wind_payload["metric"],
            "display_label": "24-hr max gust",
            "display_value": f"{display_gust_mph:.0f} mph",
            "driver": wind_payload["driver"],
        })
    threats_path.write_text(json.dumps(threats_payload, indent=2))

    timeline_path = DOCS / "timeline.json"
    timeline_payload = load_json(timeline_path, {"site": "KRNO", "block_hours": 3, "blocks": [], "block_hazards": []})
    timeline_payload["site"] = "KRNO"
    timeline_payload["generated_utc"] = generated
    timeline_payload["cycle_utc_iso"] = cycle.isoformat().replace("+00:00", "Z")
    timeline_payload["cycle"] = f"NBM QMD {cycle.strftime('%HZ')}"
    timeline_payload["block_hours"] = 3

    old_blocks = timeline_payload.get("blocks", [])
    old_block_hazards = timeline_payload.get("block_hazards", [])
    new_blocks = []
    new_block_hazards = []
    for i in range(16):
        start_fxx = i * 3 + 1
        end_fxx = min((i + 1) * 3, 48)
        old_block = old_blocks[i] if i < len(old_blocks) and isinstance(old_blocks[i], dict) else {}
        old_hazard = old_block_hazards[i] if i < len(old_block_hazards) and isinstance(old_block_hazards[i], dict) else {}
        block_hours = [h for h in ok_hours if start_fxx <= h["fxx"] <= end_fxx]
        if block_hours:
            block_peak = max(block_hours, key=lambda h: h.get("mean_gust_mph") or -999)
            block_peak_mph = float(block_peak["mean_gust_mph"])
        else:
            block_peak = None
            block_peak_mph = None
        new_block = dict(old_block)
        new_block["start_fxx"] = start_fxx
        new_block["end_fxx"] = end_fxx
        new_block["GST"] = round(block_peak_mph, 1) if block_peak_mph is not None else None
        new_hazard = dict(old_hazard)
        new_hazard["WIND"] = deterministic_wind_block_risk(block_peak_mph)
        if block_peak is not None:
            new_hazard["WIND"]["source_fxx"] = block_peak["fxx"]
            new_hazard["WIND"]["valid_utc"] = block_peak["valid_utc"]
        new_blocks.append(new_block)
        new_block_hazards.append(new_hazard)
    timeline_payload["blocks"] = new_blocks
    timeline_payload["block_hazards"] = new_block_hazards
    timeline_path.write_text(json.dumps(timeline_payload, indent=2))

    diagnostic = {
        "site": "KRNO",
        "source": "NBM QMD direct byte-range download",
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "generated_utc": generated,
        "display_value": {
            "label": "24-hr max gust",
            "method": "mean value from QMD 24-hour maximum 10-meter wind gust grid",
            "source_fxx": 24,
            "valid_utc": max24["valid_utc"],
            "gust_mps": max24["mean_mps"],
            "gust_mph": round(display_gust_mph, 1),
            "gust_kt": round(display_gust_kt, 1),
            "source_idx_line": max24["mean_idx_line"],
        },
        "airport_threshold_probabilities": {
            "method": "Exceedance probabilities are derived from the QMD 24-hour maximum 10-meter gust percentile curve using linear interpolation.",
            "thresholds": threshold_probs,
            "percentile_curve": max24["percentile_curve"],
        },
        "timeline": {
            "method": "QMD hourly mean 10-meter wind gust",
            "hourly_results": hourly_results,
        },
        "risk": risk_eval,
        "methodology": (
            "Wind risk card display uses the mean value from the QMD 24-hour maximum 10-meter gust grid. "
            "Wind risk probabilities use the QMD 24-hour maximum gust percentile curve for 30, 45, 58, and 65 mph thresholds. "
            "Wind timeline uses QMD hourly mean gusts for timing."
        ),
    }
    (DATA / "nbm_qmd_wind.json").write_text(json.dumps(diagnostic, indent=2))

    print("Updated docs/threats.json WIND")
    print("Updated docs/timeline.json WIND")
    print("Wrote data/nbm_qmd_wind.json")


if __name__ == "__main__":
    main()
