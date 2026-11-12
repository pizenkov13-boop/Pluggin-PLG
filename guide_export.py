"""Export human-readable build guide to a text file."""

from __future__ import annotations

import json
from pathlib import Path

from pattern_utils import format_build_guide

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_JSON = PROJECT_DIR / "output_pattern.json"
GUIDE_FILE = PROJECT_DIR / "build_guide.txt"


def export_build_guide(
    data: dict | None = None,
    json_path: Path = DEFAULT_JSON,
    output_path: Path = GUIDE_FILE,
) -> Path:
    if data is None:
        data = json.loads(json_path.read_text(encoding="utf-8"))

    lines = [
        format_build_guide(data),
        "",
        "Files:",
        f"  - {PROJECT_DIR / 'output_pattern.json'}",
        f"  - {PROJECT_DIR / 'output_midi'}/<session>/Kick.mid, 808_Bass.mid, …",
        "",
    ]

    manual = data.get("manual_steps") or []
    if manual:
        lines.append("Detailed manual steps:")
        lines.extend(manual)

    fx = data.get("fx_automation")
    if fx:
        lines.extend(["", "FX automation:"])
        lines.append(json.dumps(fx, ensure_ascii=False, indent=2))

    vocal = data.get("vocal_fx")
    if vocal:
        lines.extend(["", "Vocal FX (your voice):"])
        lines.append(json.dumps(vocal, ensure_ascii=False, indent=2))

    samples = data.get("samples") or []
    if samples:
        lines.extend(["", "Sample placements:"])
        for item in samples:
            lines.append(f"  - [{item.get('track')}] {item.get('file')} @ {item.get('time_step')}")

    refs = data.get("library_refs") or []
    if refs:
        lines.extend(["", "Library refs (MIDI / presets / projects / plugins):"])
        for item in refs:
            lines.append(f"  - [{item.get('type')}] {item.get('file')}")
            if item.get("note"):
                lines.append(f"      {item['note']}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path
