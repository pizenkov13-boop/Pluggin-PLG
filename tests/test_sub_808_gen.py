"""808 lane repair — hat-like LLM garbage → kick-locked sub sustains."""

from drum_blueprint import build_kick_pattern
from beat_humanize import humanize_pattern
from pattern_utils import parse_note_name, track_notes
from sub_808_gen import (
    SUB_HI,
    SUB_LO,
    clamp_sub_register_notes,
    is_hat_like_sub_808,
    should_replace_sub_808,
)


def _hat_like_c5_grid(count: int = 32) -> list[dict]:
    return [
        {"time_step": i * 0.25, "note": "C5", "length": 0.2, "velocity": 127}
        for i in range(count)
    ]


def test_is_hat_like_detects_dense_c5_micro_notes():
    assert is_hat_like_sub_808(_hat_like_c5_grid())


def test_should_replace_hat_like_or_unmarked_808():
    kick = build_kick_pattern(bars=2)
    garbage = _hat_like_c5_grid()
    assert should_replace_sub_808(garbage, kick)
    assert should_replace_sub_808([], kick)
    legit = [
        {"time_step": 0.0, "note": "C2", "length": 1.5, "velocity": 127, "sub_808_gen": True},
        {"time_step": 2.0, "note": "C2", "length": 1.5, "velocity": 127, "sub_808_gen": True},
    ]
    assert not should_replace_sub_808(legit, kick[:2])


def test_clamp_sub_register_notes():
    out = clamp_sub_register_notes(_hat_like_c5_grid(4))
    for note in out:
        midi = parse_note_name(note["note"])
        assert SUB_LO <= midi <= SUB_HI
        assert float(note["length"]) >= 0.5


def test_humanize_replaces_hat_like_808_with_kick_locked_bass():
    kick = build_kick_pattern(bars=4)
    pattern = {
        "bpm": 145,
        "style": "rage opium ken carson",
        "user_prompt": "dark trap 808",
        "tracks": {
            "kick": kick,
            "snare": [],
            "clap": [],
            "hi_hats": [],
            "sub_808": _hat_like_c5_grid(64),
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 8.0, "velocity": 95}],
        },
    }
    out = humanize_pattern(pattern)
    bass = track_notes(out, "sub_808")
    assert bass
    assert not is_hat_like_sub_808(bass)
    # Every note is either a regenerated sustain (sub_808_gen) or a legitimate
    # additive slide stab (rage's eight08_slides=True, see add_808_bar_slides)
    # — never a leftover hat-like note from the original garbage.
    assert all(n.get("sub_808_gen") or n.get("slide") for n in bass)
    # Duck/breath may split long sustains; just ensure we didn't keep the hat grid.
    assert len(bass) <= max(64, len(kick) * 4)
    assert not is_hat_like_sub_808(bass)
    for note in bass:
        midi = parse_note_name(note["note"])
        assert SUB_LO <= midi <= SUB_HI
        assert float(note["length"]) >= 0.5
