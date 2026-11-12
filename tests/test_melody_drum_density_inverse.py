""""Trust the Process": drums answer melody density per section (postprocess)."""

from __future__ import annotations

from beat_humanize import _roll_eligible_windows, apply_melody_drum_density_inverse
from genre_profiles import PROFILES


def _arrangement():
    return [
        {"name": "verse", "start_bar": 0, "bars": 8},    # sparse melody
        {"name": "chorus", "start_bar": 8, "bars": 8},   # dense melody
    ]


def test_kick_sync_extras_thinned_under_busy_melody_section():
    pattern = {
        "plg_arrangement": _arrangement(),
        "tracks": {
            "melody_lead": (
                # verse: 2 notes/bar (sparse)
                [{"time_step": b * 4.0, "note": "E5", "length": 1.0, "velocity": 95} for b in range(8)]
                # chorus: 8 notes/bar (dense — well over the 1.15x average threshold)
                + [{"time_step": 32.0 + i * 0.5, "note": "E5", "length": 0.4, "velocity": 95} for i in range(64)]
            ),
            "kick": [
                {"time_step": 3.5, "note": "C1", "length": 0.4, "velocity": 90, "kick_sync": True},  # verse
                {"time_step": 35.5, "note": "C1", "length": 0.4, "velocity": 90, "kick_sync": True},  # chorus
                {"time_step": 0.0, "note": "C1", "length": 0.3, "velocity": 112},  # core grid, never tagged
            ],
        },
    }
    removed = apply_melody_drum_density_inverse(pattern)
    kick = pattern["tracks"]["kick"]
    steps = sorted(n["time_step"] for n in kick)
    assert removed == 1
    assert 35.5 not in steps  # dense (chorus) extra removed
    assert 3.5 in steps       # sparse (verse) extra kept
    assert 0.0 in steps       # core grid hit untouched


def test_hat_roll_burst_removed_as_a_whole_group():
    pattern = {
        "plg_arrangement": _arrangement(),
        "tracks": {
            "melody_lead": (
                [{"time_step": b * 4.0, "note": "E5", "length": 1.0, "velocity": 95} for b in range(8)]
                + [{"time_step": 32.0 + i * 0.5, "note": "E5", "length": 0.4, "velocity": 95} for i in range(64)]
            ),
            "hi_hats": [
                {"time_step": 0.0, "note": "F#5", "length": 0.1, "velocity": 80},
                {"time_step": 0.5, "note": "F#5", "length": 0.1, "velocity": 80},
                # a 3-note roll burst inside the dense chorus section
                {"time_step": 33.8, "note": "F#5", "length": 0.05, "velocity": 88, "hat_roll": True},
                {"time_step": 33.85, "note": "F#5", "length": 0.05, "velocity": 95, "hat_roll": True},
                {"time_step": 33.9, "note": "F#5", "length": 0.05, "velocity": 102, "hat_roll": True},
            ],
        },
    }
    removed = apply_melody_drum_density_inverse(pattern)
    hats = pattern["tracks"]["hi_hats"]
    assert removed == 3  # the whole burst, not a partial gap
    assert all(not n.get("hat_roll") for n in hats)
    assert len(hats) == 2  # the two steady untagged hats survive


def test_no_threshold_crossed_leaves_everything_untouched():
    pattern = {
        "plg_arrangement": _arrangement(),
        "tracks": {
            # identical density (1 note/bar) in both the verse and the chorus ->
            # nothing exceeds the 1.15x average, so nothing gets thinned.
            "melody_lead": [{"time_step": b * 4.0, "note": "E5", "length": 1.0, "velocity": 95} for b in range(16)],
            "kick": [{"time_step": 3.5, "note": "C1", "length": 0.4, "velocity": 90, "kick_sync": True}],
        },
    }
    assert apply_melody_drum_density_inverse(pattern) == 0


def test_fewer_than_two_sections_is_a_noop():
    pattern = {
        "plg_arrangement": [{"name": "hook", "start_bar": 0, "bars": 8}],
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "E5", "length": 1.0, "velocity": 95}],
            "kick": [{"time_step": 1.0, "note": "C1", "length": 0.4, "velocity": 90, "kick_sync": True}],
        },
    }
    assert apply_melody_drum_density_inverse(pattern) == 0


def test_genre_gate_matches_established_trap_family():
    gated = ("trap", "rage", "opium", "phonk", "drill")
    for name in gated:
        assert PROFILES[name].melody_drum_density_inverse is True
    for name in ("pop_rap", "toliver", "kanye", "lofi", "boombap"):
        assert PROFILES[name].melody_drum_density_inverse is False


# --- _roll_eligible_windows: real melody density replaces the fixed
# hook/chorus/drop section-name allow-list (arranger.ROLL_SECTIONS) --------


def test_sparse_verse_unlocks_rolls_a_named_roll_section_never_could():
    """The fix's whole point: verse is NOT in arranger.ROLL_SECTIONS, so the
    old fixed allow-list could never let it roll no matter how sparse its
    melody was. chorus IS in that list, so the old gate would allow rolls
    there even though this particular chorus pass is the busy one. Real
    density must invert that: sparse verse -> eligible, dense chorus -> not."""
    pattern = {
        "plg_arrangement": [
            {"name": "verse", "start_bar": 0, "bars": 8},   # sparse melody
            {"name": "chorus", "start_bar": 8, "bars": 8},  # dense melody
        ],
        "tracks": {
            "melody_lead": (
                [{"time_step": b * 4.0, "note": "E5", "length": 1.0, "velocity": 95} for b in range(8)]
                + [{"time_step": 32.0 + i * 0.5, "note": "E5", "length": 0.4, "velocity": 95} for i in range(64)]
            ),
        },
    }
    windows = _roll_eligible_windows(pattern)
    assert windows is not None
    verse_start, verse_end = 0.0, 32.0
    chorus_start, chorus_end = 32.0, 64.0
    assert any(s <= verse_start and e >= verse_end for s, e in windows), "sparse verse should be roll-eligible"
    assert not any(s <= chorus_start and e >= chorus_end for s, e in windows), "dense chorus should NOT be roll-eligible"


def test_dense_verse_sparse_chorus_still_matches_density_not_the_name():
    """Mirror scenario: a dense verse and a sparse chorus. The old name-based
    gate would have (wrongly, for the wrong reason) allowed the chorus and
    blocked the verse — real density agrees with that outcome here, but for
    the correct reason (density), not because of what the section is called."""
    pattern = {
        "plg_arrangement": [
            {"name": "verse", "start_bar": 0, "bars": 8},   # dense melody
            {"name": "chorus", "start_bar": 8, "bars": 8},  # sparse melody
        ],
        "tracks": {
            "melody_lead": (
                [{"time_step": i * 0.5, "note": "E5", "length": 0.4, "velocity": 95} for i in range(64)]
                + [{"time_step": 32.0 + b * 4.0, "note": "E5", "length": 1.0, "velocity": 95} for b in range(8)]
            ),
        },
    }
    windows = _roll_eligible_windows(pattern)
    assert windows is not None
    verse_start, verse_end = 0.0, 32.0
    chorus_start, chorus_end = 32.0, 64.0
    assert not any(s <= verse_start and e >= verse_end for s, e in windows), "dense verse should NOT be roll-eligible"
    assert any(s <= chorus_start and e >= chorus_end for s, e in windows), "sparse chorus should be roll-eligible"


def test_roll_eligible_windows_none_without_melody_or_arrangement():
    assert _roll_eligible_windows({"tracks": {}}) is None
    assert _roll_eligible_windows({"plg_arrangement": [], "tracks": {"melody_lead": []}}) is None
