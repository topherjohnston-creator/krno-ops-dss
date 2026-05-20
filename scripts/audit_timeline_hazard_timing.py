from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DOCS = Path("docs")
DATA = Path("data")
DATA.mkdir(exist_ok=True)

TIMELINE_PATH = DOCS / "timeline.json"

SOURCE_FILES = {
    "WIND": DATA / "krno_wind_hourly.json",
    "SNOW": DATA / "krno_snow_hourly.json",
    "LIGHTNING": DATA / "krno_lightning_hourly.json",
    "FZRA": DATA / "krno_fzra_hourly.json",
}

# Rain is usually 6-hour QMD window data, not true hourly.
# We audit it separately if the source file exists.
RAIN_SOURCE_CANDIDATES = [
    DATA / "krno_rain_hourly.json",
    DATA / "krno_rain_windows.json",
    DATA / "nbm_qmd_rain.json",
]

# Visibility file names have varied during development.
VIS_SOURCE_CANDIDATES = [
    DATA / "krno_visibility_hourly.json",
    DATA / "krno_vis_hourly.json",
    DATA / "nbm_core_visibility.json",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text())
    except Exception as exc:
        return {"_load_error": str(exc), "_path": str(path)}


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except Exception:
        return None


def get_hours_from_payload(payload: Any) -> list[dict[str, Any]]:
    """
    Normalize common hourly-output formats.

    Expected forms:
      {"hours": [...]}
      {"hourly": [...]}
      {"hourly_timing": [...]}
      {"hourly_results": [...]}
      [...]
    """
    if payload is None:
        return []

    if isinstance(payload, list):
        return [h for h in payload if isinstance(h, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ["hours", "hourly", "hourly_timing", "hourly_results", "data"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [h for h in value if isinstance(h, dict)]

    # Some diagnostic files bury hazard data one level down.
    for key in ["diagnostic", "debug", "source", "max24"]:
        value = payload.get(key)
        if isinstance(value, dict):
            hours = get_hours_from_payload(value)
            if hours:
                return hours

    return []


def get_windows_from_payload(payload: Any) -> list[dict[str, Any]]:
    """
    Normalize rain/window-type outputs.

    Expected forms:
      {"windows": [...]}
      {"qmd_windows": [...]}
      {"six_hour_windows": [...]}
      {"periods": [...]}
    """
    if payload is None:
        return []

    if isinstance(payload, list):
        return [w for w in payload if isinstance(w, dict)]

    if not isinstance(payload, dict):
        return []

    for key in [
        "windows",
        "qmd_windows",
        "six_hour_windows",
        "periods",
        "rain_windows",
        "results",
    ]:
        value = payload.get(key)
        if isinstance(value, list):
            return [w for w in value if isinstance(w, dict)]

    # Fallback: some diagnostics may include nested windows.
    for value in payload.values():
        if isinstance(value, dict):
            windows = get_windows_from_payload(value)
            if windows:
                return windows

    return []


def numeric(value: Any) -> float | None:
    if value is None:
        return None

    try:
        out = float(value)
        return out
    except Exception:
        return None


def choose_value_for_hazard(hazard: str, hour: dict[str, Any]) -> float | None:
    """
    Pick the source value that should drive timeline timing.

    This is intentionally strict enough to catch wrong/missing fields,
    but flexible enough for the file names/keys used during development.
    """
    hazard = hazard.upper()

    if hazard == "WIND":
        for key in ["gust_mph", "GST", "value", "wind_gust_mph"]:
            val = numeric(hour.get(key))
            if val is not None:
                return val

    if hazard == "SNOW":
        # Prefer risk-relevant probability when available.
        risk_eval = hour.get("risk_evaluation")
        if isinstance(risk_eval, dict):
            best = risk_eval.get("best")
            if isinstance(best, dict):
                val = numeric(best.get("probability"))
                if val is not None:
                    return val

        for key in ["probability", "prob", "snow_1hr_in", "display_asnow_in", "s1hr_in"]:
            val = numeric(hour.get(key))
            if val is not None:
                return val

    if hazard == "LIGHTNING":
        for key in ["probability", "prob", "ltng_prob", "value"]:
            val = numeric(hour.get(key))
            if val is not None:
                return val

    if hazard == "FZRA":
        risk_eval = hour.get("risk_evaluation")
        if isinstance(risk_eval, dict):
            best = risk_eval.get("best")
            if isinstance(best, dict):
                val = numeric(best.get("probability"))
                if val is not None:
                    return val

        for key in ["probability", "prob", "fzra_prob", "value"]:
            val = numeric(hour.get(key))
            if val is not None:
                return val

    if hazard == "VISIBILITY":
        # For visibility, lower is worse. Audit still checks peak block by "worst" value.
        for key in ["visibility_sm", "VIS", "min_visibility_sm", "value"]:
            val = numeric(hour.get(key))
            if val is not None:
                return val

    return None


def choose_rain_value(window: dict[str, Any]) -> float | None:
    """
    Rain timing is usually based on the highest risk/probability window.
    """
    risk_eval = window.get("risk_evaluation")
    if isinstance(risk_eval, dict):
        best = risk_eval.get("best")
        if isinstance(best, dict):
            val = numeric(best.get("probability"))
            if val is not None:
                return val

    for key in [
        "probability",
        "prob",
        "qpf_6hr_in",
        "rain_6hr_in",
        "amount_in",
        "value",
    ]:
        val = numeric(window.get(key))
        if val is not None:
            return val

    return None


def source_peak_hour(hazard: str, hours: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = []

    for hour in hours:
        value = choose_value_for_hazard(hazard, hour)
        fxx = numeric(hour.get("fxx"))
        valid_utc = hour.get("valid_utc")

        if value is None or fxx is None:
            continue

        valid.append(
            {
                "source": hour,
                "value": value,
                "fxx": int(fxx),
                "valid_utc": valid_utc,
            }
        )

    if not valid:
        return None

    if hazard.upper() == "VISIBILITY":
        # Lower visibility is the worse/peak impact.
        return min(valid, key=lambda h: (h["value"], h["fxx"]))

    return max(valid, key=lambda h: (h["value"], -h["fxx"]))


def source_peak_window(windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = []

    for window in windows:
        value = choose_rain_value(window)

        start_fxx = numeric(window.get("start_fxx"))
        end_fxx = numeric(window.get("end_fxx"))
        fxx = numeric(window.get("fxx"))

        if start_fxx is None and fxx is not None:
            start_fxx = max(1, int(fxx) - 5)
        if end_fxx is None and fxx is not None:
            end_fxx = int(fxx)

        if value is None:
            continue

        valid.append(
            {
                "source": window,
                "value": value,
                "start_fxx": int(start_fxx) if start_fxx is not None else None,
                "end_fxx": int(end_fxx) if end_fxx is not None else None,
                "fxx": int(fxx) if fxx is not None else None,
                "valid_utc": window.get("valid_utc"),
            }
        )

    if not valid:
        return None

    return max(valid, key=lambda w: (w["value"], -(w["fxx"] or 999)))


def block_index_for_fxx(fxx: int) -> int:
    """
    16 blocks x 3 hours:
      f001-f003 = block 0
      f004-f006 = block 1
      ...
      f046-f048 = block 15
    """
    return max(0, min(15, (int(fxx) - 1) // 3))


def expected_block_range_for_window(start_fxx: int | None, end_fxx: int | None) -> list[int]:
    if start_fxx is None and end_fxx is None:
        return []

    if start_fxx is None:
        start_fxx = end_fxx
    if end_fxx is None:
        end_fxx = start_fxx

    start_block = block_index_for_fxx(int(start_fxx))
    end_block = block_index_for_fxx(int(end_fxx))

    return list(range(start_block, end_block + 1))


def timeline_hazard_blocks(timeline: dict[str, Any], hazard: str) -> list[dict[str, Any]]:
    blocks = timeline.get("block_hazards", [])
    out = []

    for idx, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue

        hazard_obj = block.get(hazard)
        if hazard_obj is None and hazard == "VISIBILITY":
            hazard_obj = block.get("VIS")

        if isinstance(hazard_obj, dict):
            out.append(
                {
                    "block_index": idx,
                    "hazard": hazard,
                    "hazard_obj": hazard_obj,
                    "risk": numeric(hazard_obj.get("risk")),
                    "level": numeric(hazard_obj.get("level")),
                    "prob": numeric(hazard_obj.get("prob")),
                    "source_fxx": numeric(hazard_obj.get("source_fxx")),
                    "metric": hazard_obj.get("metric"),
                    "driver": hazard_obj.get("driver"),
                }
            )

    return out


def timeline_peak_block(timeline: dict[str, Any], hazard: str) -> dict[str, Any] | None:
    blocks = timeline_hazard_blocks(timeline, hazard)

    if not blocks:
        return None

    # Prefer risk, then probability, then level.
    return max(
        blocks,
        key=lambda b: (
            b["risk"] if b["risk"] is not None else -1,
            b["prob"] if b["prob"] is not None else -1,
            b["level"] if b["level"] is not None else -1,
            -(b["block_index"]),
        ),
    )


def audit_hourly_hazard(
    timeline: dict[str, Any],
    hazard: str,
    source_path: Path,
) -> dict[str, Any]:
    payload = load_json(source_path)

    if payload is None:
        return {
            "hazard": hazard,
            "source_path": str(source_path),
            "status": "missing_source_file",
            "pass": False,
        }

    if isinstance(payload, dict) and "_load_error" in payload:
        return {
            "hazard": hazard,
            "source_path": str(source_path),
            "status": "source_json_load_error",
            "error": payload["_load_error"],
            "pass": False,
        }

    hours = get_hours_from_payload(payload)

    if not hours:
        return {
            "hazard": hazard,
            "source_path": str(source_path),
            "status": "no_hours_found",
            "pass": False,
        }

    source_peak = source_peak_hour(hazard, hours)
    timeline_peak = timeline_peak_block(timeline, hazard)

    if source_peak is None:
        return {
            "hazard": hazard,
            "source_path": str(source_path),
            "status": "no_valid_source_values",
            "pass": False,
            "hours_count": len(hours),
        }

    if timeline_peak is None:
        return {
            "hazard": hazard,
            "source_path": str(source_path),
            "status": "missing_timeline_hazard",
            "pass": False,
            "source_peak": source_peak_summary(source_peak),
            "hours_count": len(hours),
        }

    expected_block = block_index_for_fxx(source_peak["fxx"])
    timeline_block = int(timeline_peak["block_index"])

    source_fxx_in_timeline = timeline_peak.get("source_fxx")
    source_fxx_matches = (
        int(source_fxx_in_timeline) == int(source_peak["fxx"])
        if source_fxx_in_timeline is not None
        else None
    )

    # Allow same block even if the exact source_fxx is missing/mismatch.
    block_matches = timeline_block == expected_block

    return {
        "hazard": hazard,
        "source_path": str(source_path),
        "status": "ok" if block_matches else "timing_mismatch",
        "pass": bool(block_matches),
        "hours_count": len(hours),
        "source_peak": source_peak_summary(source_peak),
        "expected_block_index": expected_block,
        "timeline_peak_block_index": timeline_block,
        "timeline_peak": timeline_peak_summary(timeline_peak),
        "source_fxx_matches": source_fxx_matches,
        "block_matches": block_matches,
    }


def source_peak_summary(source_peak: dict[str, Any]) -> dict[str, Any]:
    return {
        "fxx": source_peak.get("fxx"),
        "valid_utc": source_peak.get("valid_utc"),
        "value": source_peak.get("value"),
    }


def window_peak_summary(window_peak: dict[str, Any]) -> dict[str, Any]:
    return {
        "fxx": window_peak.get("fxx"),
        "start_fxx": window_peak.get("start_fxx"),
        "end_fxx": window_peak.get("end_fxx"),
        "valid_utc": window_peak.get("valid_utc"),
        "value": window_peak.get("value"),
    }


def timeline_peak_summary(timeline_peak: dict[str, Any]) -> dict[str, Any]:
    hazard_obj = timeline_peak.get("hazard_obj", {})

    return {
        "block_index": timeline_peak.get("block_index"),
        "risk": timeline_peak.get("risk"),
        "level": timeline_peak.get("level"),
        "prob": timeline_peak.get("prob"),
        "source_fxx": timeline_peak.get("source_fxx"),
        "metric": timeline_peak.get("metric"),
        "driver": timeline_peak.get("driver"),
        "peak_valid_utc": hazard_obj.get("peak_valid_utc"),
        "valid_start_utc": hazard_obj.get("valid_start_utc"),
        "valid_end_utc": hazard_obj.get("valid_end_utc"),
    }


def audit_rain(timeline: dict[str, Any]) -> dict[str, Any]:
    source_path = next((path for path in RAIN_SOURCE_CANDIDATES if path.exists()), None)

    if source_path is None:
        return {
            "hazard": "RAIN",
            "source_path": None,
            "status": "missing_source_file",
            "pass": False,
            "checked_candidates": [str(p) for p in RAIN_SOURCE_CANDIDATES],
        }

    payload = load_json(source_path)

    if isinstance(payload, dict) and "_load_error" in payload:
        return {
            "hazard": "RAIN",
            "source_path": str(source_path),
            "status": "source_json_load_error",
            "error": payload["_load_error"],
            "pass": False,
        }

    windows = get_windows_from_payload(payload)

    if not windows:
        # Try treating it as hourly if no windows found.
        hours = get_hours_from_payload(payload)
        if hours:
            return audit_hourly_hazard(timeline, "RAIN", source_path)

        return {
            "hazard": "RAIN",
            "source_path": str(source_path),
            "status": "no_windows_or_hours_found",
            "pass": False,
        }

    source_peak = source_peak_window(windows)
    timeline_peak = timeline_peak_block(timeline, "RAIN")

    if source_peak is None:
        return {
            "hazard": "RAIN",
            "source_path": str(source_path),
            "status": "no_valid_source_values",
            "pass": False,
            "windows_count": len(windows),
        }

    if timeline_peak is None:
        return {
            "hazard": "RAIN",
            "source_path": str(source_path),
            "status": "missing_timeline_hazard",
            "pass": False,
            "source_peak": window_peak_summary(source_peak),
            "windows_count": len(windows),
        }

    expected_blocks = expected_block_range_for_window(
        source_peak.get("start_fxx"),
        source_peak.get("end_fxx"),
    )

    timeline_block = int(timeline_peak["block_index"])
    block_matches = timeline_block in expected_blocks if expected_blocks else False

    return {
        "hazard": "RAIN",
        "source_path": str(source_path),
        "status": "ok" if block_matches else "timing_mismatch",
        "pass": bool(block_matches),
        "windows_count": len(windows),
        "source_peak": window_peak_summary(source_peak),
        "expected_block_indices": expected_blocks,
        "timeline_peak_block_index": timeline_block,
        "timeline_peak": timeline_peak_summary(timeline_peak),
        "block_matches": block_matches,
    }


def audit_visibility(timeline: dict[str, Any]) -> dict[str, Any]:
    source_path = next((path for path in VIS_SOURCE_CANDIDATES if path.exists()), None)

    if source_path is None:
        return {
            "hazard": "VISIBILITY",
            "source_path": None,
            "status": "missing_source_file",
            "pass": False,
            "checked_candidates": [str(p) for p in VIS_SOURCE_CANDIDATES],
        }

    return audit_hourly_hazard(timeline, "VISIBILITY", source_path)


def count_blocks(timeline: dict[str, Any]) -> dict[str, Any]:
    blocks = timeline.get("blocks", [])
    block_hazards = timeline.get("block_hazards", [])

    return {
        "blocks_count": len(blocks) if isinstance(blocks, list) else None,
        "block_hazards_count": len(block_hazards) if isinstance(block_hazards, list) else None,
        "block_hours": timeline.get("block_hours"),
        "blocks_16": isinstance(blocks, list) and len(blocks) == 16,
        "block_hazards_16": isinstance(block_hazards, list) and len(block_hazards) == 16,
        "block_hours_3": timeline.get("block_hours") == 3,
    }


def make_text_report(report: dict[str, Any]) -> str:
    lines = []

    lines.append("KRNO Timeline Timing Audit")
    lines.append(f"Generated: {report.get('generated_utc')}")
    lines.append("")

    structure = report.get("timeline_structure", {})
    lines.append("Timeline structure:")
    lines.append(f"  blocks_count: {structure.get('blocks_count')}")
    lines.append(f"  block_hazards_count: {structure.get('block_hazards_count')}")
    lines.append(f"  block_hours: {structure.get('block_hours')}")
    lines.append(f"  structure_pass: {report.get('structure_pass')}")
    lines.append("")

    lines.append("Hazard timing checks:")

    for result in report.get("hazards", []):
        status = "PASS" if result.get("pass") else "FAIL"
        hazard = result.get("hazard")
        lines.append(f"  {status} - {hazard}: {result.get('status')}")

        source_peak = result.get("source_peak")
        if source_peak:
            lines.append(f"    source_peak: {source_peak}")

        expected_block = result.get("expected_block_index")
        expected_blocks = result.get("expected_block_indices")

        if expected_block is not None:
            lines.append(f"    expected_block_index: {expected_block}")

        if expected_blocks is not None:
            lines.append(f"    expected_block_indices: {expected_blocks}")

        lines.append(f"    timeline_peak_block_index: {result.get('timeline_peak_block_index')}")

        timeline_peak = result.get("timeline_peak")
        if timeline_peak:
            lines.append(f"    timeline_peak: {timeline_peak}")

        if result.get("source_path"):
            lines.append(f"    source_path: {result.get('source_path')}")

        lines.append("")

    lines.append(f"overall_pass: {report.get('overall_pass')}")

    return "\n".join(lines)


def main() -> None:
    timeline = load_json(TIMELINE_PATH)

    if timeline is None:
        report = {
            "generated_utc": utc_now(),
            "status": "missing_timeline_json",
            "overall_pass": False,
            "timeline_path": str(TIMELINE_PATH),
        }

        DATA.joinpath("timeline_timing_audit.json").write_text(json.dumps(report, indent=2))
        DATA.joinpath("timeline_timing_audit.txt").write_text("Missing docs/timeline.json\n")
        raise RuntimeError("Missing docs/timeline.json")

    if isinstance(timeline, dict) and "_load_error" in timeline:
        report = {
            "generated_utc": utc_now(),
            "status": "timeline_json_load_error",
            "error": timeline["_load_error"],
            "overall_pass": False,
            "timeline_path": str(TIMELINE_PATH),
        }

        DATA.joinpath("timeline_timing_audit.json").write_text(json.dumps(report, indent=2))
        DATA.joinpath("timeline_timing_audit.txt").write_text(f"timeline.json load error: {timeline['_load_error']}\n")
        raise RuntimeError("Could not load docs/timeline.json")

    structure = count_blocks(timeline)
    structure_pass = (
        structure.get("blocks_16") is True
        and structure.get("block_hazards_16") is True
        and structure.get("block_hours_3") is True
    )

    hazard_results = []

    for hazard, source_path in SOURCE_FILES.items():
        hazard_results.append(audit_hourly_hazard(timeline, hazard, source_path))

    hazard_results.append(audit_visibility(timeline))
    hazard_results.append(audit_rain(timeline))

    overall_pass = structure_pass and all(result.get("pass") is True for result in hazard_results)

    report = {
        "generated_utc": utc_now(),
        "status": "complete",
        "timeline_path": str(TIMELINE_PATH),
        "timeline_structure": structure,
        "structure_pass": structure_pass,
        "hazards": hazard_results,
        "overall_pass": overall_pass,
        "notes": [
            "This audit compares the peak timing in each hazard source file against the peak block in docs/timeline.json.",
            "For hourly hazards, f001-f003 maps to block 0, f004-f006 to block 1, and so on through f046-f048 block 15.",
            "For visibility, lower visibility is treated as worse.",
            "For rain, the audit supports 6-hour window outputs when present.",
            "A failure means either the builder wrote the wrong timeline block, the frontend is reading the wrong fields, or the source file schema was not recognized by the audit.",
        ],
    }

    json_path = DATA / "timeline_timing_audit.json"
    txt_path = DATA / "timeline_timing_audit.txt"

    json_path.write_text(json.dumps(report, indent=2))
    txt_path.write_text(make_text_report(report))

    print(txt_path.read_text())

    if not overall_pass:
        print("Timeline timing audit completed with failures.")
    else:
        print("Timeline timing audit passed.")


if __name__ == "__main__":
    main()
