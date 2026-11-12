"""Read-only, source-first passport for an uploaded MIDI file.

The profiler deliberately does not compose, quantize or attach a FLEX preset.
It is the evidence card a later planner may consult after it has been audited
on a varied MIDI corpus.
"""
from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any

from midi_corpus import bpm_from_name, key_from_name
from midi_genre_detect import read_key_signature, read_midi_instrument_hints, read_tempo_bpm
from midi_ingest import read_midi_notes
from midi_structure_analysis import analyze_midi_structure
from music_theory import detect_root_pc, detect_scale
from pattern_utils import parse_note_name


def _safe_pitch(note: dict[str, Any]) -> int | None:
    try:
        return parse_note_name(str(note.get("note")))
    except (TypeError, ValueError):
        return None


def _musical_role(notes: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify musical behaviour, never speculate about sample timbre."""
    grouped: dict[float, list[dict[str, Any]]] = {}
    for note in notes:
        grouped.setdefault(round(float(note.get("time_step", 0.0) or 0.0), 4), []).append(note)
    events = list(grouped.values())
    polyphony = mean(len(event) for event in events) if events else 0.0
    lengths = [max(0.03, float(note.get("length", 0.25) or 0.25)) for note in notes]
    pitches = [pitch for note in notes if (pitch := _safe_pitch(note)) is not None]
    average_length = mean(lengths) if lengths else 0.0
    pitch_span = max(pitches) - min(pitches) if pitches else 0
    if polyphony >= 1.7:
        role = "chordal"
    elif average_length >= 1.25:
        role = "sustained_melody"
    elif len(notes) >= 18 and average_length <= 0.5:
        role = "fast_melodic_pattern"
    else:
        role = "melodic_phrase"
    return {
        "musical_role": role,
        "average_polyphony": round(polyphony, 3),
        "average_note_length_beats": round(average_length, 3),
        "pitch_span_semitones": pitch_span,
    }


def profile_midi_file(path: Path | str) -> dict[str, Any]:
    """Return a JSON-safe MIDI passport without writing to the input file."""
    file_path = Path(path)
    notes = read_midi_notes(file_path)
    embedded_tempo = read_tempo_bpm(file_path)
    named_tempo = bpm_from_name(file_path.stem)
    bpm = embedded_tempo if embedded_tempo is not None else named_tempo
    tempo_source = "midi_tempo" if embedded_tempo is not None else (
        "filename_bpm" if named_tempo is not None else "unknown"
    )
    key_meta = read_key_signature(file_path)
    named_key = key_from_name(file_path.stem)
    if key_meta is not None:
        root_pc, scale = int(key_meta[0]) % 12, str(key_meta[1])
        key_source = "midi_key_signature"
    elif notes:
        root_pc = detect_root_pc(notes)
        scale = str(detect_scale(notes, root_pc) or "unknown")
        key_source = "note_analysis"
    else:
        root_pc, scale, key_source = None, "unknown", "unknown"
    structure = analyze_midi_structure(
        notes, bpm=float(bpm or 140.0), root_pc=root_pc, scale=None if scale == "unknown" else scale
    )
    hints = read_midi_instrument_hints(file_path)
    declared_family = (
        hints.get("dna_from_text")
        or hints.get("dna_from_name")
        or hints.get("dna_from_bank_map")
        or hints.get("dna_from_program")
        or hints.get("dna_from_track")
    )
    warnings: list[str] = []
    if bpm is None:
        warnings.append("tempo_missing")
    elif embedded_tempo is not None and named_tempo is not None and abs(embedded_tempo - named_tempo) > 0.5:
        warnings.append("tempo_conflict_embedded_vs_filename")
    note_key = (root_pc, scale)
    if named_key is not None and key_meta is not None and tuple(key_meta) != tuple(named_key):
        warnings.append("key_conflict_embedded_vs_filename")
    elif named_key is not None and key_meta is None and note_key != tuple(named_key):
        # A note-only MIDI can be intentionally modal, so this is a listening
        # flag rather than a claim that either source is wrong.
        warnings.append("key_hint_differs_from_note_analysis")
    return {
        "version": 1,
        "file": {"name": file_path.name, "path": str(file_path)},
        "status": "ok" if notes else "empty",
        "tempo": {
            "bpm": round(float(bpm), 3) if bpm is not None else None,
            "source": tempo_source,
            "embedded_bpm": round(float(embedded_tempo), 3) if embedded_tempo is not None else None,
            "filename_bpm": round(float(named_tempo), 3) if named_tempo is not None else None,
        },
        "key": {
            "root_pc": root_pc,
            "scale": scale,
            "source": key_source,
            "filename_hint": {"root_pc": named_key[0], "scale": named_key[1]} if named_key is not None else None,
        },
        "instrument": {
            # Do not pretend a MIDI sounds like a piano/pluck without a real
            # program/name hint.  Musical role is still useful and reliable.
            "declared_family": str(declared_family) if declared_family else "unknown",
            "source": "midi_metadata" if declared_family else "unknown",
            "track_names": list(hints.get("track_names") or [])[:4],
        },
        "role": _musical_role(notes),
        "structure": structure,
        "safety": {
            "tempo_reliable": bpm is not None and "tempo_conflict_embedded_vs_filename" not in warnings,
            "key_reliable": "key_conflict_embedded_vs_filename" not in warnings,
            "warnings": warnings,
        },
        "read_only": True,
    }


__all__ = ["profile_midi_file"]
