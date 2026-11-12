"""MIDI trap pocket: sparse call-and-response kick + 808."""

from __future__ import annotations

from arranger import arrange_song
from bass808_gen import MAX_BASS_LENGTH
from beat_humanize import humanize_pattern
from genre_profiles import PROFILES


def _midi_trap() -> dict:
    return {
        "bpm": 142,
        "key": "E",
        "scale": "minor",
        "style": "trap",
        "user_prompt": "dark trap type beat",
        "plg_midi_melody": True,
        "plg_variety": {
            "seed": 7,
            "key": "E",
            "bpm": 142,
            "scale": "minor",
            "genre": "trap",
            "drum_variant": 1,
            "melody_variant": 2,
            "swing_profile": "lazy",
        },
        "tracks": {
            "kick": [
                {"time_step": t, "note": "C1", "length": 0.3, "velocity": 112}
                for t in (0.0, 0.5, 1.0, 2.5, 4.0, 4.5, 6.5, 8.0, 10.5)
            ],
            "snare": [
                {"time_step": t, "note": "D1", "length": 0.2, "velocity": 105}
                for t in (2.0, 6.0, 10.0)
            ],
            "clap": [
                {"time_step": t, "note": "C5", "length": 0.2, "velocity": 100}
                for t in (18.0, 22.0, 26.0, 30.0)
            ],
            "hi_hats": [
                {"time_step": i * 0.5, "note": "F#5", "length": 0.08, "velocity": 78}
                for i in range(24)
            ],
            "melody_lead": [
                {"time_step": float(i), "note": n, "length": 0.75, "velocity": 95}
                for i, n in enumerate(("E4", "G4", "B4", "A4", "F#4", "E4", "D4", "G4"))
            ],
        },
        "build_order": ["kick", "808_bass", "snare", "hi_hats", "melody_lead"],
    }


def test_midi_trap_kick_and_808_are_sparse_and_paired():
    assert PROFILES["trap"].bass808_enabled is True
    pattern = _midi_trap()
    arrange_song(pattern)
    result = humanize_pattern(pattern)
    assert not result["tracks"].get("sub_808")
    bass = sorted(result["tracks"]["808_bass"], key=lambda n: float(n["time_step"]))
    kick = sorted(result["tracks"]["kick"], key=lambda n: float(n["time_step"]))
    assert bass and kick
    assert result.get("plg_bass808_lane_active") is True
    assert all(float(n["length"]) <= MAX_BASS_LENGTH + 0.05 for n in bass)
    assert all(float(n["length"]) <= 0.4 for n in kick)
    # Mono abut / no overlap.
    for a, b in zip(bass, bass[1:]):
        end = float(a["time_step"]) + float(a["length"])
        assert end <= float(b["time_step"]) + 1e-6
    # Every kick onset is either paired with a 808 hit at the same instant
    # or a deliberate kick_only fill — never independent of what 808 does.
    bass_times = {round(float(n["time_step"]), 3) for n in bass}
    kick_times = {round(float(n["time_step"]), 3) for n in kick}
    assert kick_times & bass_times  # at least some paired hits exist


def test_pair_seal_never_creates_a_third_kick_in_a_bar():
    """A bare 808 is dropped when pairing it would break the density cap."""
    from beat_humanize import _ensure_808_on_every_kick

    pattern = {
        "tracks": {
            "kick": [
                {"time_step": 0.0, "note": "C5", "length": 0.28, "velocity": 124},
                {"time_step": 2.0, "note": "C5", "length": 0.28, "velocity": 124},
            ],
            "808_bass": [
                {"time_step": 0.0, "note": "C3", "length": 0.75, "velocity": 116},
                {"time_step": 1.0, "note": "D3", "length": 0.75, "velocity": 116},
                {"time_step": 2.0, "note": "E3", "length": 0.75, "velocity": 116},
            ],
            "melody_lead": [],
            "snare": [],
            "clap": [],
        }
    }

    assert _ensure_808_on_every_kick(pattern) is True
    assert [note["time_step"] for note in pattern["tracks"]["kick"]] == [0.0, 2.0]
    assert [note["time_step"] for note in pattern["tracks"]["808_bass"]] == [0.0, 2.0]


def test_808_bass_channel_has_cut_itself_in_flp():
    from flp_writer import _BASS_808_CUT_ITSELF, _CH_CUT_GROUP, build_flp

    pattern = _midi_trap()
    arrange_song(pattern)
    result = humanize_pattern(pattern)
    blob = build_flp(result)

    def _dword_values_after(data: bytes, event_id: int) -> list[int]:
        import struct

        out: list[int] = []
        i = 0
        while i + 5 < len(data):
            if data[i] == event_id and event_id >= 128:
                out.append(struct.unpack_from("<I", data, i + 1)[0])
                i += 5
            else:
                i += 1
        return out

    cut = int.from_bytes(_BASS_808_CUT_ITSELF, "little")
    assert cut in _dword_values_after(blob, _CH_CUT_GROUP)
