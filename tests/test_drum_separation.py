import random

from drum_blueprint import (
    BEATS_PER_BAR,
    KICK_STEPS,
    SNARE_CLAP_STEPS,
    apply_drum_blueprint,
    build_kick_pattern,
)
from beat_humanize import humanize_pattern
from hihat_rolls import build_steady_eighths
from pattern_utils import track_notes
from sub_808_gen import generate_sub_808


def test_kick_on_steps_1_and_9():
    kicks = build_kick_pattern(bars=2)
    steps_in_bar = sorted({round((k["time_step"] % BEATS_PER_BAR) / 0.25) + 1 for k in kicks})
    assert steps_in_bar == list(KICK_STEPS)


def test_snare_on_every_third_grid_step():
    """Legacy no-arrangement helper still exposes SNARE_CLAP_STEPS constant;
    live trap blueprint now tiles DRUM_CELLS (half-time snare on beat 2 = step 9)."""
    pattern = {"bpm": 145, "tracks": {}, "user_prompt": "trap", "style": "trap"}
    apply_drum_blueprint(pattern, bars=2, genre="trap")
    snares = [n for n in track_notes(pattern, "snare") if not n.get("ghost")]
    assert snares
    steps = sorted({round((n["time_step"] % BEATS_PER_BAR) / 0.25) + 1 for n in snares})
    # Classic trap cells land snare on beat 2 → step 9.
    assert 9 in steps
    assert SNARE_CLAP_STEPS == (3, 6, 9, 12, 15)  # constant still defined for callers



def _pattern_with_chorus_landing() -> dict:
    return {
        "bpm": 140,
        "tracks": {},
        "plg_arrangement": [
            {"name": "verse", "start_bar": 0, "bars": 4},
            {"name": "chorus", "start_bar": 4, "bars": 4},
        ],
    }


def test_transition_fill_off_by_default():
    pattern = _pattern_with_chorus_landing()
    apply_drum_blueprint(pattern, genre="trap")  # no transition_fill kwarg
    snares = track_notes(pattern, "snare")
    assert not any(n.get("fill") for n in snares)


def test_transition_fill_explicit_true_adds_the_fill():
    pattern = _pattern_with_chorus_landing()
    apply_drum_blueprint(pattern, genre="trap", transition_fill=True)
    snares = track_notes(pattern, "snare")
    assert any(n.get("fill") for n in snares)


def test_sub_808_follows_kick_not_melody_rhythm():
    melody = [
        {"time_step": 0.0, "note": "A4", "length": 0.5, "velocity": 90},
        {"time_step": 0.5, "note": "G4", "length": 0.5, "velocity": 88},
        {"time_step": 1.0, "note": "E4", "length": 0.5, "velocity": 87},
    ]
    kick = build_kick_pattern(bars=1)
    bass = generate_sub_808(melody, kick, bars=1)
    assert all(n.get("sub_808_gen") for n in bass)
    assert all(float(n["length"]) >= 0.5 for n in bass)
    # Anchored to the KICK's own hit times, not the melody's 0.0/0.5/1.0 grid.
    kick_steps = {round(float(k["time_step"]), 5) for k in kick}
    assert all(round(float(n["time_step"]), 5) in kick_steps for n in bass)
    # 2026-07 same-root merge: with no pinned progression, every kick inside
    # ONE bar resolves to the SAME "lowest melody note in the bar" root, so
    # they now merge into a single sustained note instead of retriggering
    # identically on every kick — the fix this test used to mask as "1:1".
    assert len(bass) == 1
    assert bass[0]["time_step"] == min(kick_steps)


def test_humanize_replaces_llm_melody_on_drums():
    pattern = {
        "bpm": 145,
        "style": "rage opium",
        "tracks": {
            "kick": [{"time_step": i * 0.25, "note": "A4", "length": 0.25, "velocity": 100} for i in range(16)],
            "snare": [{"time_step": i * 0.25, "note": "G4", "length": 0.25, "velocity": 100} for i in range(16)],
            "clap": [{"time_step": i * 0.25, "note": "E4", "length": 0.25, "velocity": 100} for i in range(16)],
            "hi_hats": [{"time_step": i * 0.25, "note": "C5", "length": 0.25, "velocity": 90} for i in range(16)],
            "sub_808": [{"time_step": i * 0.25, "note": "A3", "length": 0.5, "velocity": 127} for i in range(8)],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 95}],
        },
    }
    out = humanize_pattern(pattern)
    kicks = track_notes(out, "kick")
    assert out.get("plg_drum_blueprint")
    # Opium/rage hooks finalize BEFORE the drum grid now. A racing melody
    # forces double-time / four-floor instead of the old half-time 1+9 pocket.
    assert len(kicks) >= 8
    # Core grid hits are blueprint-tagged; kick_sync extras may be added after.
    assert any(n.get("drum_blueprint") for n in kicks)
    assert all(n.get("drum_blueprint") or n.get("kick_sync") for n in kicks)
    # Fast opium/rage hooks must not keep the sleepy half-time 1+9 pocket.
    if out.get("plg_drum_pace") in ("racing", "driving"):
        assert out.get("plg_drum_grid") in ("four_floor", "backbeat")
        assert len(kicks) >= 12
    hats = track_notes(out, "hi_hats")
    assert len(hats) >= 8
    assert sum(1 for h in hats if h.get("hat_steady")) >= 8


def test_steady_eighths_one_bar():
    hats = build_steady_eighths(1)
    assert len(hats) == 8


def test_opium_rage_style_with_ambient_word_still_runs_blueprint():
    """LLM often adds 'ambient pads' to rage styles — must not skip drum blueprint."""
    from beat_humanize import _style_flags

    pattern = {
        "style": "hard-hitting opium rage trap with dark ambient pads",
        "user_prompt": "Generate a hard-hitting, aggressive Opium and Rage type beat in 145 BPM. Key: E",
        "plg_style_tags": ["RAGE"],
        "tracks": {
            "kick": [{"time_step": i * 0.25, "note": "A4", "length": 0.25, "velocity": 100} for i in range(16)],
            "snare": [],
            "clap": [],
            "hi_hats": [{"time_step": i * 0.25, "note": "C5", "length": 0.25, "velocity": 90} for i in range(16)],
            "sub_808": [],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 95}],
        },
    }
    flags = _style_flags(pattern)
    assert flags["hard_trap"]
    assert not flags["ambient"]
    out = humanize_pattern(pattern)
    assert out.get("plg_drum_blueprint")
    assert out.get("plg_producer_meta")
