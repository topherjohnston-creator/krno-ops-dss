from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DOCS = Path("docs")
DATA = Path("data")
DOCS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

SITE = os.getenv("DSS_SITE", "KRNO")
SITE_NAME = os.getenv("DSS_SITE_NAME", "KRNO Ops")
LAT = float(os.getenv("DSS_LAT", "39.4991"))
LON = float(os.getenv("DSS_LON", "-119.7681"))

SELECTED_CYCLE_PATH = DATA / "rrfs_refs_selected_cycle.json"
FIELD_MAP_SUMMARY_PATH = DATA / "refs_field_map_summary.json"
BUILDER_SUMMARY_PATH = DATA / "refs_builder_summary.json"

FORECAST_HOURS = list(range(1, 61))
BLOCK_HOURS = 3
BLOCK_COUNT = 20

HAZARD_ORDER = [
    "WIND",
    "LIGHTNING",
    "SNOW",
    "VISIBILITY",
    "FZRA",
    "FLASH_FREEZE",
    "RAIN",
    "TEMPERATURE",
]

CARD_LABELS = {
    "WIND": "WIND",
    "LIGHTNING": "LIGHTNING",
    "SNOW": "SNOW",
    "VISIBILITY": "VISIBILITY",
    "FZRA": "FZRA",
    "FLASH_FREEZE": "FLASH FREEZE",
    "RAIN": "RAIN",
    "TEMPERATURE": "TEMPERATURE",
}

TIMELINE_LABELS = {
    "WIND": "WIND",
    "LIGHTNING": "LIGHTNING",
    "SNOW": "SNOW",
    "VISIBILITY": "VISIBILITY",
    "FZRA": "FZRA",
    "FLASH_FREEZE": "FLASH FZ",
    "RAIN": "RAIN",
    "TEMPERATURE": "TEMP",
}

FULL_NAMES = {
    "WIND": "WIND",
    "LIGHTNING": "LIGHTNING",
    "SNOW": "SNOW",
    "VISIBILITY": "VISIBILITY",
    "FZRA": "FREEZING RAIN",
    "FLASH_FREEZE": "FLASH FREEZE",
    "RAIN": "RAIN",
    "TEMPERATURE": "TEMPERATURE",
}

RISK_LABELS = {
    0: "None",
    1: "Little to None",
    2: "Minor",
    3: "Moderate",
    4: "Major",
    5: "Extreme",
}

FXX_RE = re.compile(r"\.f(\d{1,3})\.", re.IGNORECASE)
PRODUCT_RE = re.compile(r"refs\.t\d{2}z\.([a-zA-Z0-9_]+)\.f\d{1,3}\.", re.IGNORECASE)
PREFIX_CYCLE_RE = re.compile(r"refs\.(\d{8})/(\d{2})")


@dataclass(frozen=True)
class RefsFile:
    key: str
    product: str
    fxx: int
    kind: str
    size: int | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def risk_label(risk: int) -> str:
    return RISK_LABELS.get(int(risk), "Unknown")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def parse_cycle_string(value: Any) -> datetime | None:
    if not value:
        return None

    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    for fmt in ("%Y%m%d%H", "%Y-%m-%d %H", "%Y-%m-%d %HZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def load_selected_cycle() -> dict[str, Any]:
    if not SELECTED_CYCLE_PATH.exists():
        raise FileNotFoundError(
            "Missing data/rrfs_refs_selected_cycle.json. "
            "Run scripts/scan_rrfs_refs_inventory.py first."
        )

    payload = json.loads(SELECTED_CYCLE_PATH.read_text())
    selected = payload.get("selected_cycle") if isinstance(payload, dict) else None

    if isinstance(selected, dict):
        merged = dict(selected)
        merged.setdefault("bucket", payload.get("bucket"))
        merged.setdefault("wrapper_generated_utc", payload.get("generated_utc"))
        return merged

    if isinstance(payload, dict):
        return payload

    raise RuntimeError("Could not parse data/rrfs_refs_selected_cycle.json as an object.")


def flatten_selected_items(selected: dict[str, Any]) -> list[Any]:
    if isinstance(selected.get("selected_cycle"), dict):
        selected = selected["selected_cycle"]

    items: list[Any] = []
    for field in ("parsed_objects", "keys", "files", "objects", "idx_keys", "sample_keys"):
        value = selected.get(field)
        if isinstance(value, list):
            items.extend(value)
    return items


def infer_cycle_dt(selected: dict[str, Any]) -> datetime:
    for field in ("cycle_utc", "cycle", "cycle_label", "selected_cycle_utc", "selected_cycle"):
        parsed = parse_cycle_string(selected.get(field))
        if parsed:
            return parsed

    for field in ("prefix", "s3_prefix"):
        match = PREFIX_CYCLE_RE.search(str(selected.get(field, "")))
        if match:
            return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H").replace(tzinfo=timezone.utc)

    for item in flatten_selected_items(selected)[:1000]:
        key = item if isinstance(item, str) else item.get("key") if isinstance(item, dict) else ""
        match = PREFIX_CYCLE_RE.search(str(key))
        if match:
            return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H").replace(tzinfo=timezone.utc)

    raise RuntimeError(f"Could not infer REFS cycle time. Selected-cycle keys: {list(selected.keys())}")


def parse_product_from_key(key: str) -> str:
    match = PRODUCT_RE.search(key)
    if match:
        return match.group(1).lower()
    return "unknown"


def parse_fxx_from_key(key: str) -> int | None:
    match = FXX_RE.search(key)
    if match:
        return int(match.group(1))
    return None


def parse_kind_from_key(key: str) -> str | None:
    lower = key.lower()
    if lower.endswith(".grib2.idx"):
        return "idx"
    if lower.endswith(".grib2") or lower.endswith(".grb2"):
        return "grib"
    return None


def is_conus_key(key: str) -> bool:
    lower = key.lower()
    return ".conus.grib2" in lower or ".conus.grib2.idx" in lower


def build_file_index(selected: dict[str, Any]) -> dict[tuple[str, int, str], RefsFile]:
    index: dict[tuple[str, int, str], RefsFile] = {}

    for item in flatten_selected_items(selected):
        if isinstance(item, str):
            key = item
            product = parse_product_from_key(key)
            fxx = parse_fxx_from_key(key)
            kind = parse_kind_from_key(key)
            size = None
        elif isinstance(item, dict):
            key = str(item.get("key") or item.get("name") or item.get("path") or "")
            product = str(item.get("product") or parse_product_from_key(key)).lower()
            fxx = item.get("fxx")
            fxx = int(fxx) if fxx is not None else parse_fxx_from_key(key)
            if item.get("is_idx") is True:
                kind = "idx"
            elif item.get("is_grib2") is True:
                kind = "grib"
            else:
                kind = parse_kind_from_key(key)
            size = item.get("size")
        else:
            continue

        if not key or fxx is None or kind not in {"idx", "grib"}:
            continue
        if not is_conus_key(key):
            continue

        index[(product, int(fxx), kind)] = RefsFile(
            key=key,
            product=product,
            fxx=int(fxx),
            kind=kind,
            size=int(size) if isinstance(size, int) else None,
        )

    return index


def load_field_map() -> dict[str, Any]:
    if not FIELD_MAP_SUMMARY_PATH.exists():
        return {
            "status": "missing",
            "files": [],
            "category_counts": {},
            "errors": ["data/refs_field_map_summary.json not found"],
        }

    try:
        payload = json.loads(FIELD_MAP_SUMMARY_PATH.read_text())
    except Exception as exc:
        return {
            "status": "error",
            "files": [],
            "category_counts": {},
            "errors": [str(exc)],
        }

    if not isinstance(payload, dict):
        return {
            "status": "error",
            "files": [],
            "category_counts": {},
            "errors": ["field map summary is not a JSON object"],
        }

    payload.setdefault("status", "ok")
    payload.setdefault("files", [])
    payload.setdefault("category_counts", {})
    payload.setdefault("errors", [])
    return payload


def summarize_file_index(file_index: dict[tuple[str, int, str], RefsFile]) -> dict[str, Any]:
    products: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"idx_hours": [], "grib_hours": []})

    for (product, fxx, kind), _ in sorted(file_index.items()):
        field = "idx_hours" if kind == "idx" else "grib_hours"
        products[product][field].append(fxx)

    return {
        product: {
            "idx_hours": sorted(set(values["idx_hours"])),
            "grib_hours": sorted(set(values["grib_hours"])),
        }
        for product, values in sorted(products.items())
    }


def field_map_by_hazard(field_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    hazard_categories = {
        "WIND": {"wind"},
        "LIGHTNING": {"lightning_convection"},
        "SNOW": {"snow"},
        "VISIBILITY": {"visibility"},
        "FZRA": {"freezing_rain"},
        "RAIN": {"rain_precip"},
        "TEMPERATURE": {"temperature_wetbulb"},
    }

    summary = {
        hazard: {
            "field_map_matches": 0,
            "mapped_hours": [],
            "sample_lines": [],
        }
        for hazard in HAZARD_ORDER
    }
    summary["FLASH_FREEZE"]["note"] = "Derived later from cold/wet overlap; no direct REFS extraction in lean builder."

    for file_record in field_map.get("files", []) or []:
        if not isinstance(file_record, dict):
            continue
        fxx = file_record.get("fxx")
        for match in file_record.get("matched_lines", []) or []:
            if not isinstance(match, dict):
                continue
            categories = set(match.get("categories") or [])
            line = str(match.get("line") or "")
            for hazard, wanted_categories in hazard_categories.items():
                if not categories.intersection(wanted_categories):
                    continue
                item = summary[hazard]
                item["field_map_matches"] += 1
                if isinstance(fxx, int):
                    item["mapped_hours"].append(fxx)
                if len(item["sample_lines"]) < 3:
                    item["sample_lines"].append(line)

    for hazard, item in summary.items():
        item["mapped_hours"] = sorted(set(item.get("mapped_hours", [])))

    return summary


def empty_threat(hazard: str, cycle_dt: datetime, reason: str) -> dict[str, Any]:
    return {
        "id": hazard,
        "title": CARD_LABELS[hazard],
        "name": FULL_NAMES[hazard],
        "prob": 0.0,
        "probability": 0.0,
        "risk": 0,
        "risk_level": 0,
        "risk_label": "None",
        "level": 0,
        "impact_level": 0,
        "metric": "No signal",
        "display_label": CARD_LABELS[hazard],
        "display_value": "None",
        "window": "60 hr",
        "peak_start_fxx": None,
        "peak_end_fxx": None,
        "source_fxx": None,
        "peak_valid_utc": None,
        "driver": reason,
        "methodology": (
            "REFS lean builder generated a valid low-risk placeholder. "
            "Exact field extraction is intentionally disabled until field selection is locked down."
        ),
        "data_status": "not_extracted",
    }


def empty_timeline_hazard(
    hazard: str,
    start_fxx: int,
    end_fxx: int,
    valid_start: datetime,
    valid_end: datetime,
    reason: str,
) -> dict[str, Any]:
    return {
        "id": hazard,
        "label": TIMELINE_LABELS[hazard],
        "name": FULL_NAMES[hazard],
        "risk": 0,
        "risk_label": "None",
        "level": 0,
        "impact_level": 0,
        "prob": 0.0,
        "probability": 0.0,
        "metric": "No signal",
        "driver": reason,
        "source_fxx": None,
        "peak_valid_utc": None,
        "valid_start_utc": valid_start.isoformat().replace("+00:00", "Z"),
        "valid_end_utc": valid_end.isoformat().replace("+00:00", "Z"),
        "start_fxx": start_fxx,
        "end_fxx": end_fxx,
        "data_status": "not_extracted",
    }


def hazard_reason(hazard: str, field_summary: dict[str, Any]) -> str:
    matches = int(field_summary.get(hazard, {}).get("field_map_matches", 0))
    if matches:
        return f"REFS field-map found {matches} candidate IDX lines; value extraction not enabled in lean builder"
    return "No matched REFS field-map candidates; risk set to None"


def build_outputs(
    cycle_dt: datetime,
    selected: dict[str, Any],
    file_index: dict[tuple[str, int, str], RefsFile],
    field_map: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    generated = utc_now()
    cycle_iso = cycle_dt.isoformat().replace("+00:00", "Z")
    file_summary = summarize_file_index(file_index)
    field_summary = field_map_by_hazard(field_map)

    common_meta = {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": LAT,
        "lon": LON,
        "source": "NOAA RRFS / REFS via AWS S3",
        "generated_utc": generated,
        "cycle_utc_iso": cycle_iso,
        "cycle": f"REFS {cycle_dt.strftime('%HZ')}",
        "valid_period": "next_60_hours",
    }

    threats: dict[str, Any] = {
        **common_meta,
        "threats": {},
        "hazards": [],
        "methodology": (
            "REFS-only DSS backend. This lean pass indexes the selected cycle and compact field map, "
            "then writes complete dashboard JSON without broad GRIB parsing. Hazards remain risk 0 "
            "until exact byte-range extraction is enabled per field."
        ),
        "metadata": {
            "builder_mode": "lean_idx_field_map",
            "extraction": "disabled",
            "selected_prefix": selected.get("prefix"),
            "available_products": sorted(file_summary.keys()),
            "field_map_status": field_map.get("status", "unknown"),
        },
    }

    for hazard in HAZARD_ORDER:
        threat = empty_threat(hazard, cycle_dt, hazard_reason(hazard, field_summary))
        threats["threats"][hazard] = threat
        threats["hazards"].append(
            {
                "id": hazard,
                "name": CARD_LABELS[hazard],
                "full_name": FULL_NAMES[hazard],
                "risk_level": threat["risk_level"],
                "risk_label": threat["risk_label"],
                "impact_level": threat["impact_level"],
                "probability": threat["probability"],
                "peak_start_fxx": threat["peak_start_fxx"],
                "peak_end_fxx": threat["peak_end_fxx"],
                "metric": threat["metric"],
                "display_label": threat["display_label"],
                "display_value": threat["display_value"],
                "driver": threat["driver"],
            }
        )

    threats["hazards"].sort(
        key=lambda h: (h["risk_level"], h["probability"], h["impact_level"]),
        reverse=True,
    )

    timeline: dict[str, Any] = {
        **common_meta,
        "block_hours": BLOCK_HOURS,
        "blocks": [],
        "block_hazards": [],
        "metadata": {
            "builder_mode": "lean_idx_field_map",
            "extraction": "disabled",
        },
    }

    for block_index in range(BLOCK_COUNT):
        start_fxx = block_index * BLOCK_HOURS + 1
        end_fxx = start_fxx + BLOCK_HOURS - 1
        valid_start = cycle_dt + timedelta(hours=start_fxx)
        valid_end = cycle_dt + timedelta(hours=end_fxx)

        block = {
            "block_index": block_index,
            "start_fxx": start_fxx,
            "end_fxx": end_fxx,
            "valid_start_utc": valid_start.isoformat().replace("+00:00", "Z"),
            "valid_end_utc": valid_end.isoformat().replace("+00:00", "Z"),
        }
        hazard_block: dict[str, Any] = dict(block)

        for hazard in HAZARD_ORDER:
            payload = empty_timeline_hazard(
                hazard=hazard,
                start_fxx=start_fxx,
                end_fxx=end_fxx,
                valid_start=valid_start,
                valid_end=valid_end,
                reason=hazard_reason(hazard, field_summary),
            )
            hazard_block[hazard] = payload
            block[hazard] = payload["risk"]

        timeline["blocks"].append(block)
        timeline["block_hazards"].append(hazard_block)

    builder_summary = {
        "site": SITE,
        "site_name": SITE_NAME,
        "lat": LAT,
        "lon": LON,
        "generated_utc": generated,
        "cycle_utc_iso": cycle_iso,
        "selected_prefix": selected.get("prefix"),
        "builder_mode": "lean_idx_field_map",
        "candidate_file_count": len(file_index),
        "products": file_summary,
        "field_map": {
            "status": field_map.get("status", "unknown"),
            "file_count": len(field_map.get("files") or []),
            "category_counts": field_map.get("category_counts", {}),
            "errors": field_map.get("errors", [])[:10],
        },
        "hazards": {
            hazard: {
                "risk": 0,
                "risk_label": "None",
                "field_map_matches": field_summary[hazard].get("field_map_matches", 0),
                "mapped_hours": field_summary[hazard].get("mapped_hours", []),
                "sample_lines": field_summary[hazard].get("sample_lines", []),
                "status": "not_extracted",
            }
            for hazard in HAZARD_ORDER
        },
        "notes": [
            "No full GRIB files are downloaded by this builder.",
            "No byte-range GRIB values are extracted in this lean pass.",
            "Outputs are complete and low-risk when exact REFS fields are missing or not yet enabled.",
        ],
    }

    return threats, timeline, builder_summary


def main() -> None:
    print("Building REFS DSS outputs")

    selected = load_selected_cycle()
    cycle_dt = infer_cycle_dt(selected)
    file_index = build_file_index(selected)
    field_map = load_field_map()

    print(f"Selected cycle: {cycle_dt:%Y-%m-%d %HZ}")
    print(f"Candidate CONUS REFS files indexed: {len(file_index)}")
    print(f"Field-map status: {field_map.get('status', 'unknown')}")
    print(f"Field-map files: {len(field_map.get('files') or [])}")

    threats, timeline, builder_summary = build_outputs(cycle_dt, selected, file_index, field_map)

    write_json(DOCS / "threats.json", threats)
    write_json(DOCS / "timeline.json", timeline)
    write_json(BUILDER_SUMMARY_PATH, builder_summary)

    print("Wrote docs/threats.json")
    print("Wrote docs/timeline.json")
    print(f"Wrote {BUILDER_SUMMARY_PATH}")


if __name__ == "__main__":
    main()
