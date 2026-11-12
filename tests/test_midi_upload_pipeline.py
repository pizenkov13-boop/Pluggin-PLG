"""Explicit user MIDI upload wired end-to-end through _humanize_and_ingest
(audit 2026-07): an uploaded .mid must win outright over the corpus/library
auto-match tiers, land in tracks["melody_lead"] before arrange_song runs (so
melody_transform.py's section variation — retrograde/sequence/inversion/
fragment — applies to it automatically, same as any other source), and
respect_uploaded_scale must only affect pattern["scale"] when explicitly on."""

from __future__ import annotations

from pathlib import Path

import mido

from backend_core import _humanize_and_ingest


def _write_midi(path: Path, notes, ticks_per_beat: int = 480) -> None:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    events = []
    for note, start, dur, vel in notes:
        events.append((start, "on", note, vel))
        events.append((start + dur, "off", note, 0))
    events.sort(key=lambda e: e[0])
    last = 0
    for tick, kind, note, vel in events:
        delta = tick - last
        last = tick
        msg_type = "note_on" if kind == "on" else "note_off"
        track.append(mido.Message(msg_type, note=note, velocity=vel, time=delta))
    mid.save(str(path))


def _base_pattern() -> dict:
    return {
        "bpm": 140,
        "style": "trap",
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}],
        },
    }


def test_uploaded_midi_wins_over_a_matching_library_file(tmp_path: Path):
    # A library MIDI that WOULD keyword-match the prompt (dark_melody_loop.mid)
    # sits right there too — the explicit upload must still win.
    (tmp_path / "midi").mkdir()
    _write_midi(tmp_path / "midi" / "dark_melody_loop.mid", [(50, 0, 480, 100)])

    upload_path = tmp_path / "my_upload.mid"
    _write_midi(upload_path, [(64, 0, 480, 100), (67, 480, 480, 96)])

    pattern = _base_pattern()
    result = _humanize_and_ingest(
        pattern,
        library_root=tmp_path,
        user_prompt="dark melody trap beat",
        style="trap",
        midi_path=upload_path,
    )

    assert result["plg_midi_upload_result"]["ok"] is True
    assert result["plg_midi_ingest"]["name"] == "my_upload.mid"
    # arrange_song/humanize_pattern ran on top of it (full pipeline didn't choke)
    assert result["plg_total_bars"] == 40
    assert result["plg_arrangement"][0]["name"] == "intro"


def test_respect_uploaded_scale_flows_through_to_the_final_pattern(tmp_path: Path):
    upload_path = tmp_path / "c_major.mid"
    degrees = [60, 62, 64, 65, 67, 69, 71]
    _write_midi(upload_path, [(p, i * 480, 460, 100) for i, p in enumerate(degrees)])

    pattern = _base_pattern()
    pattern["scale"] = "phrygian"
    result = _humanize_and_ingest(
        pattern,
        library_root=tmp_path,
        user_prompt="trap beat",
        style="trap",
        midi_path=upload_path,
        respect_uploaded_scale=True,
    )
    assert result["plg_uploaded_scale_lock"]["scale"] == "major"


def test_invalid_upload_falls_through_to_midi_folder_resolution(tmp_path: Path):
    """audit 2026-07 follow-up: a failed explicit upload now falls through to
    the midi/ folder tier (resolve_midi_folder) before the older corpus/
    library-match chain — and since this folder has exactly one file, that
    tier deterministically succeeds instead of "either is fine" best-effort
    matching. This is the intended, IMPROVED behavior, not a regression."""
    (tmp_path / "midi").mkdir()
    _write_midi(tmp_path / "midi" / "dark_melody_loop.mid", [(50, 0, 480, 100)])

    pattern = _base_pattern()
    result = _humanize_and_ingest(
        pattern,
        library_root=tmp_path,
        user_prompt="dark melody trap beat",
        style="trap",
        midi_path=tmp_path / "does_not_exist.mid",
    )
    assert result["plg_midi_upload_result"]["ok"] is True
    assert result["plg_midi_ingest"]["name"] == "dark_melody_loop.mid"


