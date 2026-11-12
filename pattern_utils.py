"""Shared track layout for PLG patterns and FL sessions."""

from __future__ import annotations

from typing import Any

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_ALIASES = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
NOTE_INDEX = {name: idx for idx, name in enumerate(NOTE_NAMES)}
NOTE_INDEX.update({alias: NOTE_INDEX[mapped] for alias, mapped in NOTE_ALIASES.items()})

# Channel rack order in .flp / MIDI export
TRACK_KEYS = (
    "kick",
    "snare",
    "snare_layer",
    "clap",
    "sub_808",
    "808_bass",
    "hi_hats",
    "melody_lead",
    "counter_melody",
)
DRUM_TRACK_KEYS = ("kick", "snare", "snare_layer", "clap", "sub_808", "808_bass", "hi_hats")
SAMPLE_TRACK_KEYS = (
    "kick",
    "snare",
    "snare_layer",
    "clap",
    "sub_808",
    "808_bass",
    "hi_hats",
    "melody_lead",
)
# snare_layer/counter_melody/808_bass only materialize when they have notes.
OPTIONAL_TRACK_KEYS = ("snare_layer", "counter_melody", "808_bass")
# Ear-candy lanes materialized after the core tracks (fx_layer.py, chord_bed.py) —
# not in TRACK_KEYS because they're dynamic/optional, but they still count as a
# "sound" for the commercial-simplicity cap (see GenreProfile.max_active_sounds).
EAR_CANDY_TRACK_KEYS = ("fx", "chords", "texture", "cowbell")


def count_active_sound_layers(data: dict[str, Any]) -> int:
    """Count distinct non-empty sound lanes (drums + melody + ear-candy) on a pattern."""
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0
    return sum(1 for key in TRACK_KEYS + EAR_CANDY_TRACK_KEYS if tracks.get(key))

TRACK_LABELS = {
    "kick": "1. Kick",
    "snare": "2. Snare",
    "snare_layer": "2b. Snare layer",
    "clap": "3. Clap",
    "sub_808": "4. Sub 808",
    "808_bass": "4b. 808 Bass",
    "hi_hats": "5. Hi-Hats",
    "melody_lead": "6. Melody / Lead",
    "counter_melody": "6b. Counter melody",
}

CHANNEL_NAMES = {
    "kick": "PLG Kick",
    "snare": "PLG Snare",
    "snare_layer": "PLG Snare Layer",
    "clap": "PLG Clap",
    "sub_808": "PLG Sub 808",
    "808_bass": "PLG 808 Bass",
    "hi_hats": "PLG Hi-Hats",
    "melody_lead": "PLG MIDI",
    "counter_melody": "PLG Counter Melody",
    "fx": "PLG FX",
    "chords": "PLG Chords",
    "texture": "PLG Texture",
    "cowbell": "PLG Cowbell",
}

DEFAULT_BUILD_ORDER = [
    "kick",
    "snare",
    "clap",
    "sub_808",
    "hi_hats",
    "melody_lead",
    "counter_melody",
    "samples",
    "fx_automation",
    "vocal_fx",
]


def step_to_beats(time_step: float) -> float:
    """PLG grid: 1.0 time_step = one beat (0.25 = sixteenth)."""
    return float(time_step)


def step_to_ms(time_step: float, bpm: float) -> int:
    return int(step_to_beats(time_step) * (60_000.0 / bpm))


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


def track_notes(data: dict[str, Any], track_key: str) -> list[dict[str, Any]]:
    tracks = data.get("tracks")
    if isinstance(tracks, dict) and track_key in tracks:
        raw = tracks[track_key]
        return raw if isinstance(raw, list) else []
    return []


def count_all_track_notes(data: dict[str, Any]) -> int:
    return sum(len(track_notes(data, key)) for key in TRACK_KEYS)


def format_build_guide(data: dict[str, Any]) -> str:
    order = data.get("build_order") or list(DEFAULT_BUILD_ORDER)
    lines = [
        f"PLG | BPM {data.get('bpm', '?')} | {data.get('style', 'unknown')}",
        "Producer mode: dark melodic trap FL workflow",
        "",
        "Build order:",
    ]

    step = 1
    for layer in order:
        if layer in TRACK_KEYS:
            count = len(track_notes(data, layer))
            lines.append(f"{step}. {TRACK_LABELS[layer]} -> {count} notes")
            step += 1
        elif layer == "samples" and data.get("samples"):
            lines.append(f"{step}. Load {len(data['samples'])} samples from library")
            step += 1
        elif layer == "fx_automation" and data.get("fx_automation"):
            lines.append(f"{step}. Apply FX on 808 channel")
            step += 1
        elif layer == "vocal_fx" and data.get("vocal_fx"):
            lines.append(f"{step}. Record vocals + apply vocal FX")
            step += 1

    picks = data.get("plg_sample_picks")
    if isinstance(picks, dict) and picks:
        lines.extend(["", "Matched kit:"])
        for key in TRACK_KEYS:
            name = picks.get(key)
            if name:
                lines.append(f"- {TRACK_LABELS.get(key, key)}: {name}")

    refs = data.get("library_refs") or []
    if refs:
        lines.extend(["", "Library assets:"])
        for item in refs:
            lines.append(f"- [{item.get('type')}] {item.get('file')} — {item.get('note', '')}".rstrip(" —"))

    manual = data.get("manual_steps") or []
    if manual:
        lines.extend(["", "Manual:"])
        lines.extend(f"- {item}" for item in manual)

    meta = data.get("plg_producer_meta")
    rough_mix = meta.get("vocal_ready_mix") if isinstance(meta, dict) else None
    if isinstance(rough_mix, dict):
        lines.extend(["", "Rough mix:"])
        profile = rough_mix.get("profile")
        if profile:
            lines.append(f"- Profile: {profile}")
        headroom = rough_mix.get("master_headroom_db")
        if headroom is not None:
            lines.append(f"- Master headroom: ~{headroom} dB before the template Soft Clipper")
        for note in rough_mix.get("notes") or []:
            lines.append(f"- {note}")

    if data.get("starter_mode"):
        workflow = (
            "Starter sounds are loaded in FL — press Play. "
            "Swap samples on PLG channels anytime for your sound."
        )
    else:
        workflow = "Don workflow: your kick/snare/clap/808/hats/lead on PLG channels → FX on 808 → Mixer F9"
    lines.extend([
        "",
        workflow,
        "FL: Piano roll -> Scripts -> PLG (Hat Roll, 808 Glide, Pan Spread)",
        "Full guide: FL_WORKFLOWS.md",
    ])
    return "\n".join(lines)
