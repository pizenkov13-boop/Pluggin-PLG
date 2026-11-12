"""Small, deterministic quality signal for a MIDI-led song arrangement.

This is deliberately a *ranking* signal, not another composer.  It lets the
existing best-of pass prefer an arrangement that has space, a recognisable
return and a section change, while refusing to reward blanket density.
"""
from __future__ import annotations

from typing import Any

from pattern_utils import track_notes


def score_song_dramaturgy(pattern: dict[str, Any]) -> float:
    """Return a bounded bonus for audible, MIDI-derived development.

    A pattern that does not use the MIDI phrase contract gets zero: ordinary
    non-MIDI generation must not be biased by metadata it does not own.
    """
    plan = pattern.get("plg_midi_fullkit_plan")
    if not isinstance(plan, dict) or not plan.get("enabled"):
        return 0.0
    rows = [row for row in plan.get("phrases", []) if isinstance(row, dict)]
    if not rows:
        return 0.0

    strong_entries = sum(bool(row.get("strong_entry")) for row in rows)
    interaction_kinds = {str(row.get("interaction")) for row in rows}
    hats = track_notes(pattern, "hi_hats")
    snares = track_notes(pattern, "snare")
    layers = track_notes(pattern, "snare_layer")

    answers = sum(bool(note.get("midi_motif_answer")) for note in hats)
    pickups = sum(bool(note.get("midi_motif_pickup")) for note in snares)
    signatures = sum(bool(note.get("midi_signature_perc")) for note in layers)
    fills = sum(bool(note.get("midi_boundary_fill")) for note in hats)
    pulse_switches = sum(bool(note.get("midi_section_pulse_switch")) for note in hats)

    # Every signal is capped: spamming the same thing never outranks a clear
    # phrase-level idea.  The total 12 points is large enough to break a
    # close best-of tie but not enough to hide a faulty pocket or bad harmony.
    score = 0.0
    if strong_entries:
        score += min(2.0, strong_entries * 0.5)
    score += min(2.5, answers * 1.25)
    score += min(2.0, pickups * 1.0)
    score += min(1.5, signatures * 0.75)
    if pulse_switches:
        score += 1.5
    if {"space", "impact", "answer", "support"}.issubset(interaction_kinds):
        score += 1.5

    # A boundary fill is a punctuation mark, not a second hat pattern.  More
    # than one per strong entry is a negative selection signal.
    fill_budget = max(1, strong_entries)
    if fills <= fill_budget:
        score += min(1.0, fills * 0.5)
    else:
        score -= min(2.0, (fills - fill_budget) * 0.5)
    return round(max(0.0, min(12.0, score)), 3)


__all__ = ["score_song_dramaturgy"]
