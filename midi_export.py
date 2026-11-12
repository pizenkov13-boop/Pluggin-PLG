"""Export PLG pattern JSON to per-track MIDI files (Stem Export Mixer)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mido

from mix_blueprint import session_slug
from pattern_utils import CHANNEL_NAMES, TRACK_KEYS, parse_note_name, step_to_beats, track_notes

# 960 pulses-per-quarter — atomic timing so humanize micro-shifts (swing, melody
# groove-lag, 808 attack) survive export instead of snapping to a coarse grid.
PPQ = 960
_MIN_NOTE_BEATS = 0.0625

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "output_midi"
DEFAULT_JSON = PROJECT_DIR / "output_pattern.json"
COMBINED_MIDI = OUTPUT_DIR / "PLG_Beat.mid"

TRACK_EXPORT_NAMES = {key: CHANNEL_NAMES.get(key, key) for key in TRACK_KEYS}

# Friendly drag-and-drop names for FL mixer channels
STEM_FILENAMES = {
    "kick": "Kick.mid",
    "snare": "Snare.mid",
    "snare_layer": "Snare_Layer.mid",
    "clap": "Clap.mid",
    "sub_808": "808_Bass.mid",
    "hi_hats": "HiHats.mid",
    "melody_lead": "Melody.mid",
    "counter_melody": "Counter_Melody.mid",
}


def _steps_to_beats(steps: float) -> float:
    return step_to_beats(steps)


def _beats_to_ticks(beats: float) -> int:
    return max(0, int(round(beats * PPQ)))


def _normalize_notes_for_export(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim same-pitch overlaps so MIDI writers never emit orphan note-offs."""
    cleaned: list[dict[str, Any]] = []
    for raw in notes:
        try:
            pitch = parse_note_name(str(raw["note"]))
            if not 0 <= pitch <= 127:
                continue
            start = float(raw.get("time_step", 0))
            length = max(_MIN_NOTE_BEATS, float(raw.get("length", 0.25)))
            velocity = max(1, min(127, int(raw.get("velocity", 100))))
        except (KeyError, TypeError, ValueError):
            continue
        entry = dict(raw)
        entry["time_step"] = round(start, 5)
        entry["length"] = round(length, 5)
        entry["velocity"] = velocity
        cleaned.append(entry)

    cleaned.sort(
        key=lambda n: (
            float(n["time_step"]),
            -float(n["length"]),
            -int(n.get("velocity", 0)),
        )
    )

    accepted: list[dict[str, Any]] = []
    for entry in cleaned:
        pitch = parse_note_name(str(entry["note"]))
        start = float(entry["time_step"])
        length = float(entry["length"])

        for other in accepted:
            if parse_note_name(str(other["note"])) != pitch:
                continue
            o_start = float(other["time_step"])
            o_end = o_start + float(other["length"])
            if start < o_end and (start + length) > o_start:
                if o_start <= start:
                    other["length"] = max(_MIN_NOTE_BEATS, round(start - o_start, 5))
                else:
                    length = max(_MIN_NOTE_BEATS, round(o_start - start, 5))

        if length >= _MIN_NOTE_BEATS:
            fixed = dict(entry)
            fixed["time_step"] = round(start, 5)
            fixed["length"] = round(length, 5)
            accepted.append(fixed)

    return sorted(accepted, key=lambda n: (float(n["time_step"]), str(n.get("note", ""))))


def _pitch_bends_for_track(
    events: list[dict[str, Any]],
    *,
    track_key: str,
) -> list[tuple[float, int]]:
    out: list[tuple[float, int]] = []
    for event in events:
        if event.get("track") not in (track_key, None):
            continue
        when = _steps_to_beats(float(event.get("time_step", 0)))
        value = max(0, min(16383, int(event.get("value", 8192))))
        out.append((when, value - 8192))
    return out


def _append_delta_messages(
    track: mido.MidiTrack,
    events: list[tuple[float, str, tuple[Any, ...]]],
) -> None:
    """Write sorted absolute-time events into a mido track with delta times."""
    events.sort(key=lambda item: (item[0], 0 if item[1] == "off" else 1 if item[1] == "on" else 2))
    last_tick = 0
    for abs_beats, kind, args in events:
        tick = _beats_to_ticks(abs_beats)
        delta = max(0, tick - last_tick)
        last_tick = tick
        if kind == "on":
            pitch, velocity, channel = args
            track.append(
                mido.Message("note_on", note=pitch, velocity=velocity, channel=channel, time=delta)
            )
        elif kind == "off":
            pitch, channel = args
            track.append(
                mido.Message("note_off", note=pitch, velocity=0, channel=channel, time=delta)
            )
        elif kind == "pan":
            pan, channel = args
            track.append(
                mido.Message("control_change", control=10, value=pan, channel=channel, time=delta)
            )
        elif kind == "bend":
            pitch, channel = args
            track.append(mido.Message("pitchwheel", pitch=pitch, channel=channel, time=delta))


def _write_midi_file(
    path: Path,
    *,
    bpm: float,
    notes: list[dict[str, Any]],
    channel: int = 0,
    track_name: str | None = None,
    pitch_bends: list[tuple[float, int]] | None = None,
) -> None:
    mid = mido.MidiFile(ticks_per_beat=PPQ)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    if track_name:
        track.append(mido.MetaMessage("track_name", name=track_name, time=0))

    events: list[tuple[float, str, tuple[Any, ...]]] = []
    for entry in _normalize_notes_for_export(notes):
        pitch = parse_note_name(str(entry["note"]))
        start = _steps_to_beats(float(entry["time_step"]))
        duration = max(_MIN_NOTE_BEATS, _steps_to_beats(float(entry["length"])))
        velocity = int(entry.get("velocity", 100))
        ch = max(0, min(15, int(channel)))
        events.append((start, "on", (pitch, velocity, ch)))
        events.append((start + duration, "off", (pitch, ch)))
        if "pan" in entry:
            pan = max(0, min(127, int(entry["pan"])))
            events.append((start, "pan", (pan, ch)))

    for when, pitch in pitch_bends or []:
        events.append((when, "bend", (pitch, max(0, min(15, int(channel))))))

    _append_delta_messages(track, events)
    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(path))


def export_pattern_to_midi(
    data: dict[str, Any],
    output_dir: Path = OUTPUT_DIR,
    *,
    friendly_names: bool = False,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    bpm = float(data.get("bpm", 120))
    written: list[Path] = []
    bends_all = data.get("pitch_bend_automation") or []

    for track_key in TRACK_KEYS:
        notes = track_notes(data, track_key)
        if not notes:
            continue

        bends = _pitch_bends_for_track(bends_all, track_key=track_key)
        filename = STEM_FILENAMES.get(track_key, f"{track_key}.mid") if friendly_names else f"{track_key}.mid"
        out_path = output_dir / filename
        _write_midi_file(out_path, bpm=bpm, notes=notes, channel=0, pitch_bends=bends)
        written.append(out_path)

    return written


def _stems_readme(session_dir: Path, files: list[Path]) -> None:
    lines = [
        "PLG Stem Export Mixer",
        "=====================",
        "",
        "Drag each .mid onto its own FL mixer channel:",
        "",
    ]
    for path in sorted(files):
        if path.name == "PLG_Beat.mid":
            continue
        lines.append(f"  {path.name}")
    lines.extend([
        "",
        "Combined: PLG_Beat.mid (all tracks in one file)",
        "",
    ])
    (session_dir / "_DRAG_TO_MIXER.txt").write_text("\n".join(lines), encoding="utf-8")


def export_stem_session(
    data: dict[str, Any],
    base_dir: Path = OUTPUT_DIR,
) -> dict[str, Any]:
    """Export a session folder with separated stems + combined MIDI."""
    session_name = session_slug(data)
    session_dir = base_dir / session_name
    session_dir.mkdir(parents=True, exist_ok=True)

    stems = export_pattern_to_midi(data, session_dir, friendly_names=True)
    combined = export_combined_midi(data, session_dir / "PLG_Beat.mid")
    manifest = {
        "session": session_name,
        "bpm": data.get("bpm"),
        "style": data.get("style"),
        "stems": [p.name for p in stems],
        "combined": combined.name,
    }
    (session_dir / "stems_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _stems_readme(session_dir, stems + [combined])

    return {
        "session_dir": session_dir,
        "session_name": session_name,
        "stem_paths": stems,
        "combined_path": combined,
        "manifest_path": session_dir / "stems_manifest.json",
    }


def export_combined_midi(
    data: dict[str, Any],
    output_path: Path = COMBINED_MIDI,
) -> Path:
    """Single multi-track MIDI for FL Studio import."""
    tracks = [(key, track_notes(data, key)) for key in TRACK_KEYS if track_notes(data, key)]
    if not tracks:
        raise ValueError("No MIDI notes to export.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpm = float(data.get("bpm", 120))
    bends_all = data.get("pitch_bend_automation") or []

    mid = mido.MidiFile(ticks_per_beat=PPQ)
    for track_index, (track_key, notes) in enumerate(tracks):
        track = mido.MidiTrack()
        mid.tracks.append(track)
        if track_index == 0:
            track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
        track.append(
            mido.MetaMessage(
                "track_name",
                name=TRACK_EXPORT_NAMES.get(track_key, track_key),
                time=0,
            )
        )

        bends = _pitch_bends_for_track(bends_all, track_key=track_key)
        events: list[tuple[float, str, tuple[Any, ...]]] = []
        channel = max(0, min(15, track_index))
        for entry in _normalize_notes_for_export(notes):
            pitch = parse_note_name(str(entry["note"]))
            start = _steps_to_beats(float(entry["time_step"]))
            duration = max(_MIN_NOTE_BEATS, _steps_to_beats(float(entry["length"])))
            velocity = int(entry.get("velocity", 100))
            events.append((start, "on", (pitch, velocity, channel)))
            events.append((start + duration, "off", (pitch, channel)))
            if "pan" in entry:
                pan = max(0, min(127, int(entry["pan"])))
                events.append((start, "pan", (pan, channel)))
        for when, pitch in bends:
            events.append((when, "bend", (pitch, channel)))
        _append_delta_messages(track, events)

    mid.save(str(output_path))
    return output_path


def export_from_json(json_path: Path = DEFAULT_JSON, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    session = export_stem_session(data, output_dir)
    # Legacy flat export (latest stems also at output_midi root)
    export_pattern_to_midi(data, output_dir, friendly_names=True)
    export_combined_midi(data, output_dir / "PLG_Beat.mid")
    return session
