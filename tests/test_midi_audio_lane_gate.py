"""MIDI-owned beats must not resurrect disabled musical lanes downstream."""

from __future__ import annotations

import dataclasses

import beat_humanize
from flp_template import (
    active_export_channels,
    build_playlist_clips,
    playlist_track_map_for_export,
)
from genre_profiles import PROFILES


def _note(time_step: float, note: str) -> dict:
    return {"time_step": time_step, "note": note, "length": 0.5, "velocity": 100}


def test_midi_gate_removes_stale_lanes_and_blocks_their_fallbacks(monkeypatch):
    called: list[str] = []

    monkeypatch.setattr(
        beat_humanize,
        "_apply_sub_808_lane",
        lambda *_args: called.append("sub_808"),
    )
    monkeypatch.setattr(
        beat_humanize,
        "materialize_sparse_counter_melody_fallback",
        lambda *_args, **_kwargs: called.append("counter_melody"),
    )

    import fx_layer

    monkeypatch.setattr(
        fx_layer,
        "materialize_fx_notes",
        lambda *_args: called.append("fx"),
    )

    pattern = {
        "bpm": 140,
        "style": "trap",
        "plg_midi_melody": True,
        "plg_sample_picks": {"sub_808": "old_808.wav", "kick": "kick.wav"},
        "plg_sound_paths": {"sub_808": "C:/old_808.wav", "kick": "C:/kick.wav"},
        "samples": [
            {"track": "sub_808", "file": "C:/old_808.wav"},
            {"track": "kick", "file": "C:/kick.wav"},
        ],
        "tracks": {
            "kick": [_note(0.0, "C1")],
            "melody_lead": [_note(0.0, "C4")],
            "sub_808": [_note(0.0, "C2")],
            "counter_melody": [_note(0.0, "E4")],
            "fx": [_note(0.0, "C4")],
            "chords": [_note(0.0, "C3")],
            "texture": [_note(0.0, "C5")],
        },
    }
    profile = dataclasses.replace(PROFILES["trap"], bass808_enabled=True)

    out = beat_humanize.humanize_pattern(pattern, profile=profile)

    assert called == []
    for lane in ("sub_808", "counter_melody", "fx", "chords", "texture"):
        assert not out["tracks"].get(lane), lane
    assert "sub_808" not in active_export_channels(out)
    assert "sub_808" not in out["plg_sample_picks"]
    assert "sub_808" not in out["plg_sound_paths"]
    assert all(item.get("track") != "sub_808" for item in out["samples"])
    assert out["tracks"].get("808_bass")
    assert out.get("plg_bass808_lane_active") is True


def test_genre_without_808_bass_still_ships_exactly_one_808():
    """Never two 808 channels, and never zero.

    lofi/boombap have an 808 but no punchy 808_bass instrument. Suppressing
    sub_808 for every MIDI-owned beat left those genres with no low end at all,
    so sub_808 stays as their single bass lane.
    """
    pattern = {
        "bpm": 84,
        "style": "lofi",
        "plg_midi_melody": True,
        "tracks": {
            "kick": [_note(float(b) * 4.0, "C1") for b in range(8)],
            "melody_lead": [_note(float(i), "C4") for i in range(8)],
            "hi_hats": [_note(i * 0.5, "C5") for i in range(16)],
            "sub_808": [],
        },
    }
    profile = PROFILES["lofi"]
    assert profile.eight08 and not profile.bass808_enabled

    out = beat_humanize.humanize_pattern(pattern, profile=profile)
    assert out["tracks"].get("sub_808"), "lofi lost its only bass lane"
    assert not out["tracks"].get("808_bass")

    channels = active_export_channels(out)
    assert [c for c in channels if "808" in c] == ["sub_808"]


def test_midi_hiphop_channel_rack_is_compact_no_sub_808_hole():
    """Kick → 808 Bass → Hats with no blank index between them."""
    import dataclasses

    from beat_humanize import humanize_pattern
    from genre_profiles import PROFILES

    pattern = {
        "bpm": 140,
        "style": "trap",
        "plg_midi_melody": True,
        "tracks": {
            "melody_lead": [_note(float(i), "C4") for i in range(8)],
            "kick": [_note(float(b) * 4.0, "C1") for b in range(8)],
            "hi_hats": [_note(i * 0.5, "C5") for i in range(16)],
            "snare": [_note(float(b) * 4.0 + 1.0, "D1") for b in range(8)],
            "clap": [_note(float(b) * 4.0 + 1.0, "C5") for b in range(8)],
            "sub_808": [],
        },
    }
    out = humanize_pattern(
        pattern, profile=dataclasses.replace(PROFILES["trap"], bass808_enabled=True)
    )
    channels = active_export_channels(out)
    assert "sub_808" not in channels
    drums = [key for key in channels if key != "melody_lead"]
    assert drums[:3] == ["kick", "808_bass", "hi_hats"]
    # Dense 0..N — no reserved hole where Sub 808 used to sit.
    rows = playlist_track_map_for_export(out)
    assert rows["kick"] + 1 == rows["808_bass"]
    assert rows["808_bass"] + 1 == rows["hi_hats"]
    assert sorted(rows.values()) == list(range(len(rows)))


def test_midi_playlist_closes_the_removed_sub_808_gap():
    pattern = {
        "bpm": 140,
        "plg_midi_melody": True,
        "plg_total_bars": 16,
        "tracks": {
            "melody_lead": [_note(0.0, "C4")],
            "kick": [_note(32.0, "C1")],
            "808_bass": [_note(32.0, "C2")],
            "sub_808": [_note(32.0, "C2")],
            "hi_hats": [_note(32.25, "C5")],
            "clap": [_note(33.0, "C5")],
            "counter_melody": [_note(0.0, "E4")],
            "fx": [_note(0.0, "C4")],
            "chords": [_note(0.0, "C3")],
            "texture": [_note(0.0, "C5")],
        },
    }

    clips = build_playlist_clips(pattern)
    rows = {clip.track_key: clip.playlist_track for clip in clips}

    assert "sub_808" not in rows
    assert "counter_melody" not in rows
    assert "fx" not in rows
    assert "chords" not in rows
    assert "texture" not in rows
    assert rows["808_bass"] == 2
    assert rows["hi_hats"] == 3
    assert rows["clap"] == 4
    assert sorted(rows.values()) == list(range(len(rows)))

    from flp_writer import _PLG_COLORS, _track_color_map

    colors = _track_color_map(clips)
    assert colors[rows["hi_hats"] + 1] == _PLG_COLORS["hi_hats"]


def test_save_pattern_refreshes_stale_midi_playlist_metadata(tmp_path):
    from backend_core import save_pattern

    pattern = {
        "bpm": 140,
        "plg_midi_melody": True,
        "plg_total_bars": 16,
        "plg_playlist_clips": [
            {"track_key": "sub_808", "playlist_track": 3},
            {"track_key": "counter_melody", "playlist_track": 8},
        ],
        "tracks": {
            "melody_lead": [_note(0.0, "C4")],
            "kick": [_note(32.0, "C1")],
            "808_bass": [_note(32.0, "C2")],
            "hi_hats": [_note(32.25, "C5")],
        },
    }
    out = tmp_path / "output_pattern.json"

    save_pattern(pattern, out)

    import json

    saved = json.loads(out.read_text(encoding="utf-8"))
    rows = {clip["track_key"]: clip["playlist_track"] for clip in saved["plg_playlist_clips"]}
    assert "sub_808" not in rows
    assert "counter_melody" not in rows
    assert rows == {"melody_lead": 0, "kick": 1, "808_bass": 2, "hi_hats": 3}

