"""Nettspend: register collision avoidance between melody_lead and
counter_melody. normalize_registers (beat_humanize.py) independently
octave-wraps both lanes into the SAME MELODY_REGISTER band with no
cross-lane awareness, so two simultaneous notes can land at the same pitch
class or a semitone apart — mush, not harmony. Two separate algorithms:
detect_register_collisions (pure analysis) and resolve_register_collisions
(policy — melody_lead is the anchor, counter_melody yields via octave
shift, re-verified against every note it now overlaps)."""

from __future__ import annotations

from beat_humanize import (
    apply_register_collision_avoidance,
    detect_register_collisions,
    resolve_register_collisions,
)
from genre_profiles import PROFILES


def _note(t: float, note: str, length: float = 1.0, velocity: int = 100) -> dict:
    return {"time_step": t, "note": note, "length": length, "velocity": velocity}


def test_overlapping_unison_is_flagged():
    melody = [_note(0.0, "C5", length=2.0)]
    counter = [_note(0.5, "C5", length=1.0)]
    collisions = detect_register_collisions(melody, counter)
    assert collisions == [(0, 0)]


def test_overlapping_minor_second_is_flagged():
    melody = [_note(0.0, "C5", length=2.0)]
    counter = [_note(0.5, "C#5", length=1.0)]
    assert detect_register_collisions(melody, counter) == [(0, 0)]


def test_overlapping_major_second_is_not_flagged():
    melody = [_note(0.0, "C5", length=2.0)]
    counter = [_note(0.5, "D5", length=1.0)]
    assert detect_register_collisions(melody, counter) == []


def test_same_pitch_non_overlapping_time_is_not_flagged():
    melody = [_note(0.0, "C5", length=1.0)]
    counter = [_note(4.0, "C5", length=1.0)]
    assert detect_register_collisions(melody, counter) == []


def test_touching_boundary_is_not_flagged():
    # melody ends exactly when counter starts — a clean hand-off, not a collision.
    melody = [_note(0.0, "C5", length=1.0)]
    counter = [_note(1.0, "C5", length=1.0)]
    assert detect_register_collisions(melody, counter) == []


def test_resolve_octave_shifts_the_colliding_counter_note():
    melody = [_note(0.0, "C5", length=2.0)]
    counter = [_note(0.5, "C5", length=1.0)]
    resolved = resolve_register_collisions(melody, counter)
    assert resolved[0]["note"] != "C5"
    assert detect_register_collisions(melody, resolved) == []


def test_resolve_never_touches_melody_lead():
    melody = [_note(0.0, "C5", length=2.0, velocity=120)]
    counter = [_note(0.5, "C5", length=1.0)]
    original_melody = [dict(n) for n in melody]
    resolve_register_collisions(melody, counter)
    assert melody == original_melody


def test_resolve_shift_does_not_trade_for_a_new_collision():
    # C5 collides with the counter note; C5+12=C6 is clear, so it should land there
    # rather than C5-12=C4 if C4 were also somehow colliding (not the case here,
    # but the winning pitch must have zero remaining overlaps).
    melody = [_note(0.0, "C5", length=2.0), _note(0.0, "C6", length=2.0)]
    counter = [_note(0.5, "C5", length=1.0)]
    resolved = resolve_register_collisions(melody, counter)
    assert detect_register_collisions(melody, resolved) == []
    # neither C5 nor C6 works, so the note should fall back to C4 (only clear option)
    assert resolved[0]["note"] == "C4"


def test_unresolvable_collision_leaves_note_untouched_without_raising():
    # melody occupies C4, C5(=collision target), and C6 simultaneously —
    # both octave directions from the colliding pitch still collide.
    melody = [_note(0.0, "C4", length=2.0), _note(0.0, "C5", length=2.0), _note(0.0, "C6", length=2.0)]
    counter = [_note(0.5, "C5", length=1.0)]
    resolved = resolve_register_collisions(melody, counter)
    assert resolved[0]["note"] == "C5"  # left as-is, no crash


def test_apply_register_collision_avoidance_noop_without_both_lanes():
    assert apply_register_collision_avoidance({"tracks": {}}) == 0
    assert apply_register_collision_avoidance(
        {"tracks": {"melody_lead": [_note(0.0, "C5")]}}
    ) == 0


def test_apply_register_collision_avoidance_writes_back_and_counts():
    data = {
        "tracks": {
            "melody_lead": [_note(0.0, "C5", length=2.0)],
            "counter_melody": [_note(0.5, "C5", length=1.0)],
        }
    }
    adjusted = apply_register_collision_avoidance(data)
    assert adjusted == 1
    assert data["tracks"]["counter_melody"][0]["note"] != "C5"


def test_bell_octave_counter_melody_relationship_is_untouched():
    # Pluggnb's "bell cheat code" (materialize_bell_octave_counter_melody)
    # deliberately puts counter_melody a full octave above melody_lead — well
    # outside COLLISION_INTERVAL_SEMITONES, so it must never trigger a shift
    # even if both flags were ever enabled on the same genre.
    melody = [_note(0.0, "C5", length=2.0)]
    counter = [_note(0.0, "C6", length=2.0)]
    resolved = resolve_register_collisions(melody, counter)
    assert resolved[0]["note"] == "C6"


def test_underground_profile_has_register_collision_avoidance_enabled():
    assert PROFILES["underground"].register_collision_avoidance is True
    for name, other in PROFILES.items():
        if name == "underground":
            continue
        assert other.register_collision_avoidance is False, name
