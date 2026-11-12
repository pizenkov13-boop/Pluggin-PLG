"""Final placement-only ensemble pass for MIDI-owned PLG beats.

This module deliberately changes *only* note ``time_step`` values.  It does
not add notes and never touches pitch, length, velocity, samples, FX, or
mixer state.  Existing lane generators remain the authors; this pass only
makes their already-generated attacks agree as one ensemble.  The single
exception (2026-08-20): a snare_layer hit stacked on a kick without the
full clap+snare trio is deleted — the layer pickup yields the impact
instant to the kick (see the layer-thinning block below).
"""

from __future__ import annotations

from typing import Any

from midi_phrase_drums import MIN_LOWEND_GAP_BEATS
from music_theory import name_for_midi
from pattern_utils import parse_note_name, track_notes

_PAIR_TOLERANCE = 0.16
_LAYER_TOLERANCE = 0.14
_HAT_CONFLICT_TOLERANCE = 0.045
# Rule 22's push is 2-5 ms; at 120-150 BPM that is 0.010-0.012 beats. This
# window catches it and nothing else — the next real subdivision, a
# sixteenth, is 0.25 beats away, twenty times wider.
_KICK_FLAM_TOLERANCE = 0.03
_SIXTEENTH = 0.25


def _step(note: dict[str, Any]) -> float:
    return float(note.get("time_step", 0.0) or 0.0)


def _nearest(notes: list[dict[str, Any]], point: float, tolerance: float) -> dict[str, Any] | None:
    candidates = [note for note in notes if abs(_step(note) - point) <= tolerance]
    return min(candidates, key=lambda note: abs(_step(note) - point)) if candidates else None


def _phrase_starts(pattern: dict[str, Any]) -> list[float]:
    plan = pattern.get("plg_midi_fullkit_plan") or pattern.get("plg_phrase_rhythm_plan") or {}
    rows = plan.get("phrases") if isinstance(plan, dict) else None
    if not isinstance(rows, list):
        return []
    return sorted({round(float(row.get("start", 0.0) or 0.0), 6) for row in rows if isinstance(row, dict)})


def _midi_root_at(melody: list[dict[str, Any]], point: float) -> int | None:
    """Lowest currently sounding MIDI note: the bass root for this moment."""
    sounding = []
    for note in melody:
        start = _step(note)
        end = start + float(note.get("length", 0.0) or 0.0)
        if start - 0.03 <= point < end + 0.03:
            try:
                sounding.append(parse_note_name(str(note.get("note") or "C3")))
            except ValueError:
                continue
    return min(sounding) % 12 if sounding else None


def apply_midi_placement_symbiosis(pattern: dict[str, Any]) -> dict[str, int]:
    """Align existing drum attacks around MIDI phrase anchors without rewriting lanes."""
    if not pattern.get("plg_midi_melody"):
        return {"enabled": 0, "kick_808_pairs": 0, "backbeat_layers": 0, "hat_spaces": 0, "phrase_entries": 0}

    tracks = pattern.get("tracks")
    if not isinstance(tracks, dict):
        return {"enabled": 0, "kick_808_pairs": 0, "backbeat_layers": 0, "hat_spaces": 0, "phrase_entries": 0}

    kick = list(track_notes(pattern, "kick"))
    bass = list(track_notes(pattern, "808_bass") or track_notes(pattern, "sub_808"))
    melody = list(track_notes(pattern, "melody_lead"))
    hats = list(track_notes(pattern, "hi_hats"))
    snare = list(track_notes(pattern, "snare"))
    clap = list(track_notes(pattern, "clap"))
    layer = list(track_notes(pattern, "snare_layer"))
    changes = {"enabled": 1, "kick_808_pairs": 0, "backbeat_layers": 0, "hat_spaces": 0,
               "phrase_entries": 0, "backbeat_flams_resolved": 0}

    # Harmonic ownership: every existing 808 keeps its octave neighbourhood,
    # velocity and tail, but its pitch class follows the lowest active MIDI
    # chord note instead of an unrelated earlier scale estimate.
    root_changes = 0
    for note in bass:
        root = _midi_root_at(melody, _step(note))
        if root is None:
            continue
        try:
            current = parse_note_name(str(note.get("note") or "C3"))
        except ValueError:
            continue
        target = root + 12 * round((current - root) / 12)
        target = max(24, min(71, target))
        if target != current:
            note["note"] = name_for_midi(target)
            note["placement_symbiosis"] = "midi_root"
            root_changes += 1
    changes["bass_roots"] = root_changes

    # Rebuild the low-end as a single gesture: every meaningful 808 attack
    # receives a kick at its onset unless that instant is occupied by a fast
    # hat-roll. Existing kick notes are reused first; only then is a matching
    # kick cloned, preserving its authored sound/dynamics.
    #
    # Two deliberate exceptions (2026-08-20, the kick-only double):
    # * a bass note the phrase composer tagged as its own bass_only response
    #   is call-and-response, not an orphan — the pairing seal's bass_only
    #   branch owns it, and "repairing" it here would clone a kick on top of
    #   a gesture that never had one. Measured on Baton Rouge: the clone
    #   inherited its ghost template's tags, and the audit read the result
    #   as a kick_roll;
    # * a double's halves are never moved and never used as clone material —
    #   the pair is ONE onset, and tearing it leaves a pickup pointing at
    #   nothing while the leftover spacing reads as a roll.
    from bass808_gen import is_double_member, is_ghost_kick
    from beat_humanize import _intentional_solo_lowend, _lowend_anchor_times

    lowend_anchors = _lowend_anchor_times(pattern)

    def hat_roll_at(point: float) -> bool:
        return sum(1 for note in hats if abs(_step(note) - point) <= 0.18) >= 3

    # A ghost carries no 808 of its own and may not be reused as anybody's
    # mate: moving it would tear its double.
    available_kicks = [note for note in kick if not is_ghost_kick(note)]
    paired = 0
    added = 0
    for bass_note in bass:
        point = _step(bass_note)
        if hat_roll_at(point):
            continue
        if _intentional_solo_lowend(bass_note, "bass_only", lowend_anchors):
            continue
        mate = _nearest(available_kicks, point, 0.75)
        if mate is not None and is_double_member(mate) and abs(_step(mate) - point) > 1e-6:
            mate = None  # never tear a double: it moves as a pair or not at all
        if mate is not None:
            available_kicks.remove(mate)
            if abs(_step(mate) - point) > 1e-6:
                mate["time_step"] = point
                mate["placement_symbiosis"] = "bass_attack"
            paired += 1
        elif kick:
            # The near/clone paths read the FULL live kick list (a mate
            # consumed above is still a legitimate neighbour/template here —
            # the original semantics), minus the ghosts: they carry no 808
            # and must never be moved or copied.
            movable = [note for note in kick if not is_ghost_kick(note)]
            near = [note for note in movable
                    if abs(_step(note) - point) < MIN_LOWEND_GAP_BEATS]
            if len(near) == 1 and not is_double_member(near[0]):
                # A kick already sits inside the minimum spacing of this
                # attack (typically a paired section_drive/foundation hit the
                # pocket left no room next to). Moving that single neighbour
                # onto the attack pairs the 808 without breaking the spacing
                # invariant; appending a clone here used to land 0.5 beats
                # from the neighbour and fail the kick_roll gate
                # (2026-08-15, Sahara corpus case, after the density card
                # started thinning pocket shapes). A neighbour that belongs
                # to a double is NOT movable for the same reason as above —
                # the 808 then stays solo and the later weld owners answer.
                near[0]["time_step"] = point
                near[0]["placement_symbiosis"] = "bass_attack_added"
                paired += 1
            elif not near:
                # A ghost-only kick list cannot offer a non-ghost donor; a
                # ghost then serves as template, stripped to a plain paired
                # hit below.
                donors = movable or kick
                clone = dict(min(donors, key=lambda note: abs(_step(note) - point)))
                # A clone is a plain paired hit: whatever double tags the
                # template carried must not leak into an unrelated repair kick.
                clone.pop("intentional_double", None)
                clone.pop("intentional_double_ghost", None)
                clone["lowend_role"] = "paired"
                clone["time_step"] = point
                clone["placement_symbiosis"] = "bass_attack_added"
                kick.append(clone)
                paired += 1
                added += 1
            # Two or more kicks already sit within the minimum spacing of
            # this attack (or the only neighbour is a double's member): any
            # move or clone would create a spacing violation or tear a pair,
            # so the 808 answer is deliberately left solo.
    changes["kick_808_pairs"] = paired
    changes["kick_attacks_added"] = added

    # Kick/808 form one low-end gesture. When the generators already place a
    # near-pair, make their attacks exactly coincide without altering either
    # lane's pitch, sustain, dynamics, or note count.
    for hit in kick:
        if is_ghost_kick(hit):
            continue  # the double's pickup carries no 808 of its own
        mate = _nearest(bass, _step(hit), _PAIR_TOLERANCE)
        if mate is not None and abs(_step(mate) - _step(hit)) > 1e-6:
            mate["time_step"] = _step(hit)
            mate["placement_symbiosis"] = "kick_pair"
            changes["kick_808_pairs"] += 1

    # Clap and snare may be deliberately offset by humanization, but their
    # primary backbeat and layer must land as one accent. Fills/ghosts keep
    # their authored placement untouched.
    core_snare = [note for note in snare if not (note.get("fill") or note.get("ghost") or note.get("riser"))]
    core_clap = [note for note in clap if not (note.get("fill") or note.get("ghost") or note.get("riser"))]
    anchor_backbeat = core_clap or core_snare
    for note in core_snare:
        mate = _nearest(core_clap, _step(note), _LAYER_TOLERANCE)
        if mate is not None and abs(_step(mate) - _step(note)) > 1e-6:
            mate["time_step"] = _step(note)
            mate["placement_symbiosis"] = "backbeat_pair"
            changes["backbeat_layers"] += 1
    # Pulling the layer onto the backbeat must not stack two layer notes on
    # one instant. It had no such guard: measured 2026-08-19, Shuttle shipped
    # 5 duplicated layer positions and Detroit 11, each a plain layer hit
    # sitting on top of a clap_double/pre_clap_roll one — a doubled
    # transient, not a layer. Latent until the kick-flam work shifted the
    # geometry and made it fire more often.
    layer_taken = {round(_step(note), 5) for note in layer}
    for note in layer:
        mate = _nearest(anchor_backbeat, _step(note), _LAYER_TOLERANCE)
        if mate is None or abs(_step(mate) - _step(note)) <= 1e-6:
            continue
        target = round(_step(mate), 5)
        if target in layer_taken:
            continue
        layer_taken.discard(round(_step(note), 5))
        layer_taken.add(target)
        note["time_step"] = _step(mate)
        note["placement_symbiosis"] = "backbeat_layer"
        changes["backbeat_layers"] += 1

    # Rule 22 pushes clap/snare 2-5 ms early for the Atlanta feel. Where the
    # backbeat shares a sixteenth with a KICK that never moved, that push does
    # not read as a push — it reads as a flam, two broadband transients a few
    # milliseconds apart, which the ear hears as one smeared hit.
    #
    # Measured 2026-08-19 on Graveyard: its kick sits on 1.75 in 33 bars and
    # its snare_layer's own main position is that same 1.75 (60 hits, every one
    # carrying pre_snare_shift_ms). Every single one of the 35 resulting pairs
    # landed 1-40 ms apart, median 3.8 ms, none exact. Corpus-wide the share of
    # kicks in that state ran 17-36 %. The user heard it as mush at the moment
    # of impact, on a file whose other lanes were fine.
    #
    # The kick does not move: it is phase-locked to 808_bass at birth and
    # PHASE_DELAY_BEATS is 0.0 because any offset measured ~1.9 dB off the
    # kick's own fundamental. So the backbeat gives up its push HERE, on the
    # instants where it collides, and keeps it everywhere else. The whole
    # cluster at that instant moves together, or the snare/clap/layer
    # alignment done just above would be undone.
    if kick:
        # The quieter half of a deliberate double is not an impact for the
        # backbeat to align to — the pair's MAIN hit is. Snapping a backbeat
        # onto the ghost would put it an eighth early.
        from bass808_gen import is_ghost_kick

        kick_times = sorted(_step(note) for note in kick if not is_ghost_kick(note))
        lanes_backbeat = ([("snare", n) for n in core_snare]
                          + [("clap", n) for n in core_clap]
                          + [("snare_layer", n) for n in layer
                             if not (n.get("fill") or n.get("ghost") or n.get("riser"))])
        # One note per lane per instant: this pass may only MOVE a hit onto
        # a kick, never stack two of the same lane on it. Stacking is what a
        # first version of this did — measured, snare_layer duplicates went
        # 8 -> 12 on Graveyard and 5 -> 10 on Shuttle. The occupancy has to
        # count EVERY note of the lane, fills and ghosts included: a
        # duplicate is a duplicate whatever tag it carries, and building
        # this from the core hits alone left Shuttle still growing.
        occupied = {
            (lane_name, round(_step(n), 5))
            for lane_name, notes in (("snare", snare), ("clap", clap), ("snare_layer", layer))
            for n in notes
        }
        for lane, note in lanes_backbeat:
            if note.get("pre_snare_shift_ms") is None:
                continue  # never shifted, nothing to give up
            point = _step(note)
            mate = min(kick_times, key=lambda t: abs(t - point))
            if not (1e-9 < abs(mate - point) <= _KICK_FLAM_TOLERANCE):
                continue
            target = round(mate, 5)
            if (lane, target) in occupied:
                continue  # that lane already speaks on this instant
            occupied.discard((lane, round(point, 5)))
            occupied.add((lane, target))
            note["time_step"] = target
            note["placement_symbiosis"] = "kick_flam_resolved"
            changes["backbeat_flams_resolved"] += 1

    # A kick stacked on the snare_layer's pickup half a beat before the full
    # backbeat is mush, not punch: listened 2026-08-20, Graveyard's Alt
    # pattern carried 36 of these kick+layer stacks against 0-2 on the three
    # approved files. The layer is the cheapest transient of the four and a
    # derivative of the clap, so where a layer note shares an instant with a
    # kick it gives the instant to the kick — the same "let the kick own the
    # impact" principle as impact_space for hats. A FULL unison
    # (kick+clap+snare+layer) is the approved backbeat punch and stays
    # untouched. The ghost of a deliberate double counts as a kick here: its
    # new positions land on layer pickups too.
    if kick:
        clap_times = [round(_step(n), 5) for n in core_clap]
        snare_times = [round(_step(n), 5) for n in core_snare]
        kept_layer: list[dict[str, Any]] = []
        thinned = 0
        for note in layer:
            t = round(_step(note), 5)
            on_kick = any(abs(t - _step(k)) <= _KICK_FLAM_TOLERANCE for k in kick)
            full_unison = (
                any(abs(t - c) <= _KICK_FLAM_TOLERANCE for c in clap_times)
                and any(abs(t - s) <= _KICK_FLAM_TOLERANCE for s in snare_times)
            )
            if on_kick and not full_unison:
                thinned += 1
                continue
            kept_layer.append(note)
        if thinned and kept_layer:
            layer = kept_layer
            changes["layer_thinned_for_kick"] = thinned

    # A phrase entry is a shared arrival: only an existing near-entry low-end
    # pair is snapped to the phrase start. Nothing is added when the composer
    # intentionally left the phrase empty.
    for start in _phrase_starts(pattern):
        k = _nearest(kick, start, _SIXTEENTH)
        b = _nearest(bass, start, _SIXTEENTH)
        if k is not None and b is not None and abs(_step(k) - _step(b)) <= _PAIR_TOLERANCE:
            if abs(_step(k) - start) > 1e-6 or abs(_step(b) - start) > 1e-6:
                k["time_step"] = start
                b["time_step"] = start
                k["placement_symbiosis"] = b["placement_symbiosis"] = "phrase_entry"
                changes["phrase_entries"] += 1

    # Let kick/808 own an impact instant. A generated closed hat exactly on a
    # shared low-end attack moves by one sixteenth, retaining its note, length
    # and velocity while giving the phrase a readable foreground/background.
    impact_times = {_step(note) for note in kick if _nearest(bass, _step(note), 1e-6) is not None}
    spaced_bars: set[int] = set()
    existing_hat_times = {_step(note) for note in hats}
    for note in hats:
        if note.get("hat_roll") or note.get("roll") or note.get("midi_motif_answer"):
            continue
        bar = int(_step(note) // 4)
        target = round(_step(note) + _SIXTEENTH, 6)
        if (
            bar not in spaced_bars
            and any(abs(_step(note) - impact) <= _HAT_CONFLICT_TOLERANCE for impact in impact_times)
            and not any(abs(target - occupied) <= _HAT_CONFLICT_TOLERANCE for occupied in existing_hat_times)
        ):
            existing_hat_times.discard(_step(note))
            existing_hat_times.add(target)
            note["time_step"] = target
            note["placement_symbiosis"] = "impact_space"
            changes["hat_spaces"] += 1
            spaced_bars.add(bar)

    for key, notes in (("kick", kick), ("hi_hats", hats), ("snare", snare), ("clap", clap), ("snare_layer", layer)):
        if key in tracks:
            tracks[key] = sorted(notes, key=_step)
    if "808_bass" in tracks:
        tracks["808_bass"] = sorted(bass, key=_step)
    elif "sub_808" in tracks:
        tracks["sub_808"] = sorted(bass, key=_step)
    pattern["plg_placement_symbiosis"] = changes
    return changes


__all__ = ["apply_midi_placement_symbiosis"]
