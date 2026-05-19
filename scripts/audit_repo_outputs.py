from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(".")
SEARCH_DIRS = [
    Path("scripts"),
    Path(".github/workflows"),
    Path("docs"),
    Path("data"),
]

TEXT_EXTENSIONS = {".py", ".yml", ".yaml", ".json", ".html", ".js", ".css"}


BAD_PATTERNS = {
    "block_hours_6": re.compile(r'["\']?block_hours["\']?\s*[:=]\s*6'),
    "range_8_blocks": re.compile(r"range\s*\(\s*8\s*\)"),
    "while_len_blocks_8": re.compile(r"while\s+len\s*\(\s*blocks\s*\)\s*<\s*8"),
    "rain_flooding_key": re.compile(r"RAIN_FLOODING"),
    "mock_builder_reference": re.compile(r"build_mock_outputs\.py"),
    "timeline_writer": re.compile(r"timeline_path\.write_text|docs/timeline\.json|timeline\.json"),
    "threats_writer": re.compile(r"threats_path\.write_text|docs/threats\.json|threats\.json"),
    "little_to_none_literal": re.compile(r"Little to None"),
}


def iter_files() -> list[Path]:
    files: list[Path] = []

    for directory in SEARCH_DIRS:
        if not directory.exists():
            continue

        for path in directory.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS:
                files.append(path)

    return sorted(files)


def scan_text_files() -> list[dict]:
    findings = []

    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            findings.append(
                {
                    "file": str(path),
                    "type": "read_error",
                    "message": str(exc),
                }
            )
            continue

        lines = text.splitlines()

        for pattern_name, pattern in BAD_PATTERNS.items():
            for line_no, line in enumerate(lines, start=1):
                if pattern.search(line):
                    findings.append(
                        {
                            "file": str(path),
                            "line": line_no,
                            "pattern": pattern_name,
                            "text": line.strip(),
                        }
                    )

    return findings


def inspect_timeline_json() -> list[dict]:
    findings = []
    path = Path("docs/timeline.json")

    if not path.exists():
        return [
            {
                "file": str(path),
                "type": "missing_file",
                "message": "docs/timeline.json does not exist",
            }
        ]

    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        return [
            {
                "file": str(path),
                "type": "json_error",
                "message": str(exc),
            }
        ]

    block_hours = payload.get("block_hours")
    blocks = payload.get("blocks", [])
    block_hazards = payload.get("block_hazards", [])

    if block_hours != 3:
        findings.append(
            {
                "file": str(path),
                "type": "bad_timeline_block_hours",
                "value": block_hours,
                "expected": 3,
            }
        )

    if len(blocks) != 16:
        findings.append(
            {
                "file": str(path),
                "type": "bad_timeline_block_count",
                "value": len(blocks),
                "expected": 16,
            }
        )

    if len(block_hazards) != 16:
        findings.append(
            {
                "file": str(path),
                "type": "bad_block_hazards_count",
                "value": len(block_hazards),
                "expected": 16,
            }
        )

    expected_pairs = [(i * 3 + 1, min((i + 1) * 3, 48)) for i in range(16)]

    for i, expected in enumerate(expected_pairs):
        if i >= len(blocks):
            continue

        block = blocks[i]
        actual = (block.get("start_fxx"), block.get("end_fxx"))

        if actual != expected:
            findings.append(
                {
                    "file": str(path),
                    "type": "bad_timeline_block_range",
                    "block_index": i,
                    "actual": actual,
                    "expected": expected,
                }
            )

    for i, hazard_block in enumerate(block_hazards):
        if not isinstance(hazard_block, dict):
            continue

        for hazard_id, hazard in hazard_block.items():
            if not isinstance(hazard, dict):
                continue

            prob = hazard.get("prob")
            risk = hazard.get("risk")
            risk_label = hazard.get("risk_label")
            level = hazard.get("level")

            try:
                prob_float = float(prob)
            except Exception:
                continue

            if prob_float <= 0 and (risk not in (0, None) or risk_label not in ("None", None) or level not in (0, None)):
                findings.append(
                    {
                        "file": str(path),
                        "type": "zero_probability_not_none_in_timeline",
                        "block_index": i,
                        "hazard": hazard_id,
                        "prob": prob,
                        "risk": risk,
                        "risk_label": risk_label,
                        "level": level,
                    }
                )

    return findings


def inspect_threats_json() -> list[dict]:
    findings = []
    path = Path("docs/threats.json")

    if not path.exists():
        return [
            {
                "file": str(path),
                "type": "missing_file",
                "message": "docs/threats.json does not exist",
            }
        ]

    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        return [
            {
                "file": str(path),
                "type": "json_error",
                "message": str(exc),
            }
        ]

    threats = payload.get("threats", {})
    hazards = payload.get("hazards", [])

    if "RAIN_FLOODING" in threats:
        findings.append(
            {
                "file": str(path),
                "type": "deprecated_key_present",
                "key": "RAIN_FLOODING",
                "message": "Use RAIN only unless frontend explicitly needs RAIN_FLOODING.",
            }
        )

    for hazard_id, hazard in threats.items():
        if not isinstance(hazard, dict):
            continue

        prob = hazard.get("prob")
        risk = hazard.get("risk")
        risk_label = hazard.get("risk_label")
        level = hazard.get("level")

        try:
            prob_float = float(prob)
        except Exception:
            continue

        if prob_float <= 0 and (risk not in (0, None) or risk_label not in ("None", None) or level not in (0, None)):
            findings.append(
                {
                    "file": str(path),
                    "type": "zero_probability_not_none_in_threats",
                    "hazard": hazard_id,
                    "prob": prob,
                    "risk": risk,
                    "risk_label": risk_label,
                    "level": level,
                }
            )

    for hazard in hazards:
        if not isinstance(hazard, dict):
            continue

        hazard_id = hazard.get("id")
        probability = hazard.get("probability")
        risk_level = hazard.get("risk_level")
        risk_label = hazard.get("risk_label")
        impact_level = hazard.get("impact_level")

        try:
            prob_float = float(probability)
        except Exception:
            continue

        if prob_float <= 0 and (
            risk_level not in (0, None)
            or risk_label not in ("None", None)
            or impact_level not in (0, None)
        ):
            findings.append(
                {
                    "file": str(path),
                    "type": "zero_probability_not_none_in_hazards_array",
                    "hazard": hazard_id,
                    "probability": probability,
                    "risk_level": risk_level,
                    "risk_label": risk_label,
                    "impact_level": impact_level,
                }
            )

    return findings


def summarize_timeline_writers(findings: list[dict]) -> list[dict]:
    writers = []

    for finding in findings:
        if finding.get("pattern") == "timeline_writer":
            writers.append(finding)

    return writers


def main() -> None:
    text_findings = scan_text_files()
    timeline_findings = inspect_timeline_json()
    threats_findings = inspect_threats_json()

    all_findings = text_findings + timeline_findings + threats_findings

    report = {
        "summary": {
            "total_findings": len(all_findings),
            "text_findings": len(text_findings),
            "timeline_json_findings": len(timeline_findings),
            "threats_json_findings": len(threats_findings),
        },
        "timeline_writers": summarize_timeline_writers(text_findings),
        "findings": all_findings,
    }

    print(json.dumps(report, indent=2))

    Path("data").mkdir(exist_ok=True)
    Path("data/repo_audit_report.json").write_text(json.dumps(report, indent=2))

    critical_types = {
        "bad_timeline_block_hours",
        "bad_timeline_block_count",
        "bad_block_hazards_count",
        "bad_timeline_block_range",
        "zero_probability_not_none_in_timeline",
        "zero_probability_not_none_in_threats",
        "zero_probability_not_none_in_hazards_array",
    }

    has_critical = any(f.get("type") in critical_types for f in all_findings)

    if has_critical:
        raise SystemExit("Repo audit found critical output issues. See data/repo_audit_report.json.")

    print("Repo audit passed.")


if __name__ == "__main__":
    main()
