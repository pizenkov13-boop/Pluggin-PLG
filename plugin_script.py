"""
PLG Pluggin' — FL Studio File Bridge reader.

Copy to: .../FL Studio/Settings/Piano roll scripts/
Run: Piano roll -> Scripts -> Pluggin' Import
Reads: output_pattern.json (BRIDGE_PATH is rewritten by fl_setup.py on install)

The lane list matches the active seven-lane contract
(midi_validate.MIDI_ALLOWED_LANES). ``sub_808`` is permanently disabled and is
deliberately absent here.
"""

from __future__ import annotations

import json
import os

import flpianoroll as flp

BRIDGE_PATH = r"C:\PLG\output_pattern.json"
TRACK_KEYS = ("kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer", "melody_lead")
TRACK_LABELS = {
    "kick": "1. Kick",
    "808_bass": "2. 808 Bass",
    "hi_hats": "3. Hi-Hats",
    "clap": "4. Clap",
    "snare": "5. Snare",
    "snare_layer": "6. Snare Layer",
    "melody_lead": "7. Melody / Lead",
    "guide": "BUILD GUIDE (all layers)",
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_ALIASES = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
NOTE_INDEX = {name: idx for idx, name in enumerate(NOTE_NAMES)}
NOTE_INDEX.update({alias: NOTE_INDEX[mapped] for alias, mapped in NOTE_ALIASES.items()})


def parse_note_name(note_str: str) -> int:
    text = note_str.strip()
    if len(text) >= 3 and text[1] in "#b":
        pitch = text[:2]
        if pitch[1] == "b":
            pitch = NOTE_ALIASES.get(pitch[0] + "b", pitch)
        octave = int(text[2:])
    elif len(text) >= 2:
        pitch = text[0]
        octave = int(text[1:])
    else:
        raise ValueError(f"Invalid note: {note_str}")

    if pitch in NOTE_ALIASES:
        pitch = NOTE_ALIASES[pitch]
    if pitch not in NOTE_INDEX:
        raise ValueError(f"Unknown pitch: {pitch}")
    return octave * 12 + NOTE_INDEX[pitch]


def step_to_beats(time_step: float) -> float:
    return float(time_step)


def step_to_ticks(time_step: float) -> int:
    return int(round(step_to_beats(time_step) * flp.score.PPQ))


def load_bridge_data() -> dict:
    if not os.path.isfile(BRIDGE_PATH):
        raise FileNotFoundError(
            f"Missing {BRIDGE_PATH}\nRun: python backend_core.py -p \"your prompt\""
        )
    with open(BRIDGE_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_track_notes(data: dict, track_key: str) -> list:
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return []

    notes = tracks.get(track_key)
    if isinstance(notes, list):
        return notes
    return []


def add_note_from_json(entry: dict, track_key: str) -> None:
    note = flp.Note()
    note.number = parse_note_name(entry["note"])
    note.time = step_to_ticks(entry["time_step"])
    note.length = max(1, step_to_ticks(entry["length"]))
    velocity = int(entry["velocity"])
    note.velocity = max(0, min(127, velocity)) / 127.0
    note.selected = False
    flp.score.addNote(note)


def format_samples(data: dict) -> str:
    samples = data.get("samples") or []
    if not samples:
        return ""

    root = data.get("sample_library", "your sample folder")
    lines = ["SAMPLES (drag into FL channels):", f"Root: {root}", ""]
    for item in samples[:20]:
        file_name = item.get("file", "?")
        track = item.get("track", "?")
        step = item.get("time_step", 0)
        lines.append(f"- [{track}] {file_name} @ step {step}")
    if len(samples) > 20:
        lines.append(f"... +{len(samples) - 20} more in JSON")
    return "\n".join(lines)


def format_fx(fx: dict) -> str:
    lines = ["FX on 808/bass channel:", ""]
    pre = fx.get("Channel_Precomputed", {})
    if pre:
        lines.append(f"- Precomputed Boost: {pre.get('boost', 0.3) * 100:.0f}%")
    dist = fx.get("Fruity_Fast_Dist", {})
    if dist:
        lines.append(f"- Fruity Fast Dist: Drive {dist.get('drive', 0.9) * 100:.0f}%")
    ws = fx.get("Fruity_WaveShaper", {})
    if ws:
        lines.append(f"- Fruity WaveShaper: Boost {ws.get('boost', 0.4) * 100:.0f}%")
    return "\n".join(lines)


def format_vocal_fx(vocal: dict) -> str:
    lines = ["VOCAL FX (your voice, not AI clone):", ""]
    for key in ("reference", "pitch_correction", "autotune_retune", "reverb", "delay"):
        if key in vocal:
            lines.append(f"- {key}: {vocal[key]}")
    lines.append("")
    lines.append("Record your vocals, then apply these settings on vocal channel.")
    return "\n".join(lines)


def format_build_guide(data: dict) -> str:
    order = data.get("build_order") or list(TRACK_KEYS) + ["samples", "fx_automation"]
    bpm = data.get("bpm", "?")
    style = data.get("style", "?")

    lines = [
        f"Pluggin' BUILD GUIDE | BPM {bpm} | {style}",
        "",
        "Do in order:",
    ]

    step_num = 1
    for layer in order:
        if layer in TRACK_KEYS:
            count = len(resolve_track_notes(data, layer))
            lines.append(f"{step_num}. Open {layer} channel -> Piano roll -> import '{TRACK_LABELS[layer]}'")
            lines.append(f"   ({count} notes)")
            step_num += 1
        elif layer == "samples":
            sample_text = format_samples(data)
            if sample_text:
                lines.append(f"{step_num}. Load samples:")
                lines.extend(["   " + row for row in sample_text.splitlines()])
                step_num += 1
        elif layer == "fx_automation" and data.get("fx_automation"):
            lines.append(f"{step_num}. Apply FX:")
            lines.extend(["   " + row for row in format_fx(data["fx_automation"]).splitlines()])
            step_num += 1
        elif layer == "vocal_fx" and data.get("vocal_fx"):
            lines.append(f"{step_num}. Vocal FX after recording:")
            lines.extend(["   " + row for row in format_vocal_fx(data["vocal_fx"]).splitlines()])
            step_num += 1

    manual = data.get("manual_steps") or []
    if manual:
        lines.extend(["", "Manual steps:"])
        lines.extend([f"- {item}" for item in manual])

    return "\n".join(lines)


def createDialog() -> flp.ScriptDialog:
    form = flp.ScriptDialog(
        "Pluggin'",
        "Import the beat PLG just generated (output_pattern.json)",
    )
    form.addInputCombo("Layer", list(TRACK_LABELS.values()), 0)
    form.addInputCheckbox("Clear Piano Roll first", False)
    form.addInputCheckbox("Show full build guide after import", True)
    return form


def apply(form: flp.ScriptDialog) -> None:
    labels = list(TRACK_LABELS.values())
    keys = list(TRACK_LABELS.keys())
    selected = form.getInputValue("Layer")
    key = keys[labels.index(selected)] if selected in labels else keys[0]

    try:
        data = load_bridge_data()
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        flp.Utils.ShowMessage(f"Pluggin' error:\n{exc}")
        return

    if key == "guide":
        flp.Utils.ShowMessage(format_build_guide(data))
        return

    notes = resolve_track_notes(data, key)
    if not notes:
        flp.Utils.ShowMessage(f"No notes for: {key}")
        return

    if form.getInputValue("Clear Piano Roll first"):
        flp.score.clearNotes()

    placed = 0
    for entry in notes:
        try:
            add_note_from_json(entry, key)
            placed += 1
        except (KeyError, ValueError, TypeError):
            continue

    flp.Utils.log(f"Pluggin': {placed} notes imported -> {key}")

    if form.getInputValue("Show full build guide after import"):
        msg_parts = [f"Imported {placed} notes to {key}.", "", format_build_guide(data)]
        sample_text = format_samples(data)
        if sample_text:
            msg_parts.extend(["", sample_text])
        flp.Utils.ShowMessage("\n".join(msg_parts))
