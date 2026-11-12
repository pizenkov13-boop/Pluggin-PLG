"""One shared phrase contract for hats, backbeat and low-end around MIDI."""
from __future__ import annotations

from typing import Any

from pattern_utils import track_notes

BEATS_PER_BAR = 4.0
_ACTIVE_MIDI_LANES = (
    "melody_lead",
    "kick",
    "808_bass",
    "hi_hats",
    "clap",
    "snare",
    "snare_layer",
)
_PAIR_TOLERANCE = 0.04
_BACKBEAT_TOLERANCE = 0.14


def _bar_brief(
    pattern: dict[str, Any],
    rows: list[dict[str, Any]],
    arrangement: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One row per bar of the song: the shared answer to "what happens here?".

    Every field is SEWN from an owner that already computed it — the
    arrangement (which lanes a section permits), the phrase plan (the role,
    and where inside its phrase this bar sits), ``plg_midi_structure``'s
    tension curve and the drum density card's per-bar melody onsets. Nothing
    here is a new musical decision and nothing is re-derived locally.

    It exists because the contract above it is per PHRASE and a real phrase
    can be eight bars long. The low end already had a per-bar authority
    (``midi_phrase_drums._active_lowend_bars``), the hats read their phrase's
    ``hat_mode``, and the backbeat read neither — so the three voices were
    never answering the same question about the same bar. This is that
    question, asked once.
    """
    span = 0
    for section in arrangement:
        if isinstance(section, dict):
            span = max(span, int(section.get("start_bar", 0) or 0) + int(section.get("bars", 0) or 0))
    if not span:
        last_end = max((float(row.get("end", 0.0) or 0.0) for row in rows), default=0.0)
        span = int(max(0.0, last_end - 1e-6) // BEATS_PER_BAR) + 1 if last_end > 0 else 0
    if span <= 0:
        return []

    structure = pattern.get("plg_midi_structure")
    tension: dict[int, float] = {}
    if isinstance(structure, dict):
        for item in structure.get("tension_curve") or []:
            if not isinstance(item, dict):
                continue
            try:
                tension[int(item.get("bar", 0) or 0)] = float(item.get("value", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
    card = pattern.get("plg_drum_density")
    onsets = card.get("melody_onsets_per_bar") if isinstance(card, dict) else None
    onsets = onsets if isinstance(onsets, list) else []
    onsets_late = card.get("melody_onsets_late_per_bar") if isinstance(card, dict) else None
    onsets_late = onsets_late if isinstance(onsets_late, list) else []

    def _section_of(bar: int) -> dict[str, Any] | None:
        for entry in arrangement:
            if not isinstance(entry, dict):
                continue
            start = int(entry.get("start_bar", 0) or 0)
            if start <= bar < start + int(entry.get("bars", 0) or 0):
                return entry
        return None

    def _phrase_of(bar: int) -> dict[str, Any] | None:
        """The phrase owning most of this bar — phrases need not tile the song."""
        lo, hi = bar * BEATS_PER_BAR, (bar + 1) * BEATS_PER_BAR
        best: dict[str, Any] | None = None
        best_overlap = 0.0
        for row in rows:
            overlap = min(hi, float(row.get("end", 0.0) or 0.0)) - max(lo, float(row.get("start", 0.0) or 0.0))
            if overlap > best_overlap + 1e-9:
                best, best_overlap = row, overlap
        return best

    brief: list[dict[str, Any]] = []
    for bar in range(span):
        section = _section_of(bar)
        active = section.get("active_tracks") if isinstance(section, dict) else None
        if isinstance(active, list):
            allowed = {str(key) for key in active}
            backbeat_allowed = bool({"snare", "clap"} & allowed)
            lowend_allowed = "kick" in allowed and bool({"808_bass", "sub_808"} & allowed)
        elif section is not None or not arrangement:
            # A section with no lane gate, or a standalone unit composition
            # with no song form at all: no authority says no.
            backbeat_allowed = lowend_allowed = True
        else:
            # Inside the song span but outside every section — nothing plays.
            backbeat_allowed = lowend_allowed = False

        row = _phrase_of(bar)
        phrase_id = role = position = hat_mode = None
        phrase_bars = into = None
        strong_entry = False
        accent: float | None = None
        if row is not None:
            phrase_id = row.get("id")
            role = row.get("role")
            hat_mode = row.get("hat_mode")
            strong_entry = bool(row.get("strong_entry"))
            first_bar = int(float(row.get("start", 0.0) or 0.0) // BEATS_PER_BAR)
            last_bar = int(max(0.0, float(row.get("end", 0.0) or 0.0) - 1e-6) // BEATS_PER_BAR)
            phrase_bars = max(1, last_bar - first_bar + 1)
            into = max(0, bar - first_bar)
            if phrase_bars == 1 or into == 0:
                # A phrase states before it can answer: a one-bar phrase is an
                # entry, never an exit.
                position = "entry"
            elif bar >= last_bar:
                position = "exit"
            else:
                position = "body"
            anchor = row.get("response_anchor")
            if isinstance(anchor, (int, float)) and bar * BEATS_PER_BAR <= float(anchor) < (bar + 1) * BEATS_PER_BAR:
                accent = round(float(anchor), 5)

        brief.append({
            "bar": bar,
            "section": str(section.get("name") or "verse") if isinstance(section, dict) else None,
            "section_occurrence": int(section.get("occurrence", 1) or 1) if isinstance(section, dict) else None,
            "phrase_id": phrase_id,
            "role": role,
            "phrase_position": position,
            "bars_into_phrase": into,
            "phrase_bars": phrase_bars,
            "hat_mode": hat_mode,
            "strong_entry": strong_entry,
            "accent_beat": accent,
            "tension": round(tension.get(bar, 0.0), 4),
            "melody_onsets": int(onsets[bar]) if 0 <= bar < len(onsets) and isinstance(onsets[bar], int) else 0,
            # Onsets in the HALF OF THE BAR THE BACKBEAT LANDS IN. Sewn from
            # the same density-card owner as melody_onsets; see that card for
            # why the whole-bar count is flat on this corpus.
            "melody_onsets_late": (
                int(onsets_late[bar])
                if 0 <= bar < len(onsets_late) and isinstance(onsets_late[bar], int)
                else 0
            ),
            "backbeat_allowed": backbeat_allowed,
            "lowend_allowed": lowend_allowed,
        })
    return brief


def bar_brief(pattern: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the stamped per-bar brief. The ONLY accessor consumers may use."""
    plan = pattern.get("plg_midi_fullkit_plan")
    if not isinstance(plan, dict):
        return []
    rows = plan.get("bars")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def attach_fullkit_plan(pattern: dict[str, Any]) -> dict[str, Any]:
    phrases = (pattern.get("plg_phrase_rhythm_plan") or {}).get("phrases")
    beat_phrases = (pattern.get("plg_midi_beat_plan") or {}).get("phrases")
    if not isinstance(phrases, list):
        plan = {"version": 2, "enabled": False, "reason": "missing_phrase_plan", "phrases": [], "bars": []}
        pattern["plg_midi_fullkit_plan"] = plan
        return plan
    responses = {
        str(row.get("phrase_id")): row.get("response_anchor")
        for row in beat_phrases if isinstance(row, dict)
    } if isinstance(beat_phrases, list) else {}
    arrangement = pattern.get("plg_arrangement")
    arrangement = arrangement if isinstance(arrangement, list) else []
    def _section_occurrence(section: str, beat: float) -> int:
        for entry in arrangement:
            if not isinstance(entry, dict) or str(entry.get("name") or "") != section:
                continue
            start = float(entry.get("start_bar", 0) or 0) * BEATS_PER_BAR
            end = start + float(entry.get("bars", 0) or 0) * BEATS_PER_BAR
            if start <= beat < end:
                return int(entry.get("occurrence", 1) or 1)
        return 1
    def _section_at(beat: float) -> str:
        for entry in arrangement:
            if not isinstance(entry, dict):
                continue
            start = float(entry.get("start_bar", 0) or 0) * BEATS_PER_BAR
            end = start + float(entry.get("bars", 0) or 0) * BEATS_PER_BAR
            if start <= beat < end:
                return str(entry.get("name") or "verse")
        return "verse"
    rows: list[dict[str, Any]] = []
    previous_section = ""
    for phrase in phrases:
        if not isinstance(phrase, dict):
            continue
        role = str(phrase.get("role") or "statement")
        section = str(phrase.get("section") or "verse")
        if role == "space" or section in ("intro", "outro"):
            hat_mode = "breathe"
        elif role in ("lift", "landing") or section in ("hook", "chorus", "drop"):
            hat_mode = "drive"
        else:
            hat_mode = "support"
        phrase_id = str(phrase.get("id") or "")
        strong_entry = section in ("hook", "chorus", "drop") and section != previous_section
        occurrence = _section_occurrence(section, float(phrase.get("start", 0.0) or 0.0))
        interaction = "space" if hat_mode == "breathe" else ("impact" if strong_entry else ("answer" if responses.get(phrase_id) is not None else "support"))
        hat_pulse = "drive" if hat_mode == "drive" else ("offbeat" if section == "verse" and occurrence % 2 == 0 else "straight")
        rows.append({
            "id": phrase_id,
            "start": float(phrase.get("start", 0.0) or 0.0),
            "end": float(phrase.get("end", 0.0) or 0.0),
            "role": role,
            "section": section,
            "hat_mode": hat_mode,
            "response_anchor": responses.get(phrase_id),
            "backbeat_required": section not in ("intro", "outro") and _section_at(max(float(phrase.get("start", 0.0) or 0.0), float(phrase.get("end", 0.0) or 0.0) - 0.05)) != "outro",
            "strong_entry": strong_entry,
            "interaction": interaction,
            "section_occurrence": occurrence,
            "hat_pulse": hat_pulse,
        })
        previous_section = section
    plan = {"version": 2, "enabled": bool(rows), "phrases": rows, "bars": _bar_brief(pattern, rows, arrangement)}
    pattern["plg_midi_fullkit_plan"] = plan
    return plan


def apply_generated_hat_contract(pattern: dict[str, Any]) -> int:
    """Make generated hats breathe in MIDI-rest phrases; preserve user MIDI hats."""
    if pattern.get("plg_midi_hats"):
        return 0
    plan = pattern.get("plg_midi_fullkit_plan")
    hats = track_notes(pattern, "hi_hats")
    if not isinstance(plan, dict) or not plan.get("enabled") or not hats:
        return 0
    # Outro already has a dedicated four-bar fade in the arranger. Do not
    # double-thin it here: this contract only clears actual MIDI pause phrases.
    breathe = [
        (float(row["start"]), float(row["end"]))
        for row in plan["phrases"]
        if isinstance(row, dict) and row.get("role") == "space"
    ]
    if not breathe:
        return 0
    kept: list[dict[str, Any]] = []
    removed = 0
    for note in hats:
        step = float(note.get("time_step", 0.0) or 0.0)
        in_breathe = any(start <= step < end for start, end in breathe)
        # Preserve the downbeat/response pulse, only clear the in-between
        # chatter. This creates room without turning a phrase into silence.
        if in_breathe and (round((step % BEATS_PER_BAR) * 2) % 2 == 1):
            removed += 1
            continue
        kept.append(note)
    if removed:
        pattern.setdefault("tracks", {})["hi_hats"] = kept
    return removed


def apply_section_pulse_switch(pattern: dict[str, Any]) -> int:
    """Give later verses a different hat pulse without simply adding density."""
    if pattern.get("plg_midi_hats"):
        return 0
    plan = pattern.get("plg_midi_fullkit_plan")
    tracks = pattern.get("tracks")
    if not isinstance(plan, dict) or not plan.get("enabled") or not isinstance(tracks, dict):
        return 0
    hats = list(track_notes(pattern, "hi_hats"))
    switches = [
        (float(row["start"]), float(row["end"]))
        for row in plan.get("phrases", [])
        if isinstance(row, dict) and row.get("hat_pulse") == "offbeat"
    ]
    if not hats or not switches:
        return 0
    changed = 0
    for note in hats:
        step = float(note.get("time_step", 0.0) or 0.0)
        if not any(start <= step < end for start, end in switches):
            continue
        eighth = int(round(step * 2))
        base = int(note.get("velocity", 80) or 80)
        note["velocity"] = max(35, min(112, base + (14 if eighth % 2 else -12)))
        note["midi_section_pulse_switch"] = True
        changed += 1
    return changed


def apply_lowend_roll_yield(pattern: dict[str, Any]) -> dict[str, int]:
    """Where a protected kick sits inside a hi-hat roll, the ROLL gives way.

    This is the other half of ``midi_lowend_director.roll_may_take_kick``.
    That predicate decides the roll may NOT take this kick — because taking it
    would leave the bar with no low end at all — and stamps the kick
    ``lowend_roll_priority``. Left there, the kick would simply sit buried
    inside the roll, which is the exact thing "hats own the pulse" exists to
    prevent, and ``beat_humanize._audit_midi_kick808_context`` would count it
    as a ``kick_over_hat_roll`` collision. So the roll yields instead: the
    roll's own onsets over that kick are removed, the kick keeps its place,
    and both rules stay true.

    Only ROLL onsets are removed — a hat is taken only if it is itself part of
    a dense cluster (its onset is in ``roll_times``). The ordinary 1/8 - 1/16
    carpet is untouched, so this thins a machine-gun run around one impact
    rather than punching a hole in the pulse. The bar's downbeat hat and a
    phrase motif answer are never taken: those carry their own meaning, the
    same exemptions the kick side of the negotiation grants.

    Hats are a lane with several legitimate modifiers (the hat contract above
    thins them too); this adds no second hat GENERATOR.
    """
    empty = {"protected_kicks": 0, "hats_removed": 0}
    tracks = pattern.get("tracks")
    if not pattern.get("plg_midi_melody") or not isinstance(tracks, dict):
        return empty
    from midi_lowend_director import HAT_ROLL_TOLERANCE, in_hat_roll, rhythmic_anchors

    hats = list(track_notes(pattern, "hi_hats"))
    protected = [
        float(note.get("time_step", 0.0) or 0.0)
        for note in track_notes(pattern, "kick")
        if note.get("lowend_roll_priority")
    ]
    if not hats or not protected:
        return empty
    roll_times = rhythmic_anchors(hats=hats)["roll_times"]
    if not roll_times:
        return empty
    protected = sorted({point for point in protected if in_hat_roll(point, roll_times)})
    if not protected:
        return empty

    kept: list[dict[str, Any]] = []
    removed = 0
    for note in hats:
        step = float(note.get("time_step", 0.0) or 0.0)
        if (
            in_hat_roll(step, roll_times)
            and any(abs(step - point) <= HAT_ROLL_TOLERANCE for point in protected)
            and abs(step % BEATS_PER_BAR) > 1e-5
            and not note.get("midi_motif_answer")
        ):
            removed += 1
            continue
        kept.append(note)
    if removed:
        tracks["hi_hats"] = kept
    result = {"protected_kicks": len(protected), "hats_removed": removed}
    pattern["plg_lowend_roll_yield"] = result
    return result


def apply_strong_phrase_answers(pattern: dict[str, Any]) -> dict[str, int]:
    """Create one recurring hat/snare answer at real MIDI phrase responses."""
    plan = pattern.get("plg_midi_fullkit_plan")
    tracks = pattern.get("tracks")
    if not isinstance(plan, dict) or not plan.get("enabled") or not isinstance(tracks, dict):
        return {"hat_breaths": 0, "clap_stops": 0, "hat_answers": 0, "snare_pickups": 0, "signature_perc": 0, "boundary_fills": 0}
    hats = list(track_notes(pattern, "hi_hats"))
    snares = list(track_notes(pattern, "snare"))
    claps = list(track_notes(pattern, "clap"))
    layers = list(track_notes(pattern, "snare_layer"))
    # Both hats below are appended at stage 16, six stages AFTER the lane's
    # only leveller, so their level has to come from the lane instead of a
    # literal — see drum_density.late_hat_velocity and the project issue log (not included) 0v.8.
    # This writer's own palette survives intact: the answer keeps its
    # 108/103/98 ramp and the boundary flick stays below the answer it
    # belongs to. Taken once, from the lane as the leveller left it, so the
    # notes this loop appends cannot drag their own reference upwards.
    from drum_density import late_hat_velocity

    answer_vel = [late_hat_velocity(hats, 108 - v * 5, peak=108) for v in range(3)]
    flick_vel = late_hat_velocity(hats, 76, peak=108)
    breaths = clap_stops = answers = pickups = signature_perc = boundary_fills = 0
    motif_index = 0
    for row in plan.get("phrases", []):
        if not isinstance(row, dict) or not row.get("strong_entry"):
            continue
        start, end = float(row["start"]), float(row["end"])
        # Half a beat of air immediately before the strong phrase.  Low-end is
        # left intact: its paired kick+808 is the impact we are setting up.
        before = start - 0.5
        kept_hats = []
        for note in hats:
            step = float(note.get("time_step", 0.0) or 0.0)
            if before <= step < start and not note.get("hat_roll"):
                breaths += 1
                continue
            kept_hats.append(note)
        hats = kept_hats
        kept_claps = []
        for note in claps:
            step = float(note.get("time_step", 0.0) or 0.0)
            if before <= step < start and not note.get("fill"):
                clap_stops += 1
                continue
            kept_claps.append(note)
        claps = kept_claps

        response = row.get("response_anchor")
        if not isinstance(response, (int, float)) or not start <= float(response) < end:
            continue
        response = round(float(response), 5)
        variant = motif_index % 3
        motif_index += 1
        nearby_hats = [note for note in hats if abs(float(note.get("time_step", 0.0)) - response) <= 0.14]
        if nearby_hats:
            nearest = min(nearby_hats, key=lambda note: abs(float(note.get("time_step", 0.0)) - response))
            nearest["velocity"] = max(int(nearest.get("velocity", 80) or 80), answer_vel[variant])
            nearest["midi_motif_answer"] = True
            nearest["midi_motif_variant"] = variant
            answers += 1
        else:
            hats.append({"time_step": response, "note": ("D5", "C#5", "D5")[variant], "length": 0.10, "velocity": answer_vel[variant], "midi_motif_answer": True, "midi_motif_variant": variant})
            answers += 1
        pickup = round(response - (0.25 if variant != 2 else 0.125), 5)
        if pickup >= start and not any(abs(float(note.get("time_step", 0.0)) - pickup) <= 0.12 for note in snares):
            snares.append({"time_step": pickup, "note": "D1", "length": 0.10, "velocity": 56 - variant * 4, "ghost": True, "midi_motif_pickup": True, "midi_motif_variant": variant})
            pickups += 1
        # The signature percussion is the same kit colour, not a new random
        # cowbell/sample. It returns only on strong entries and stays below
        # the backbeat, giving the beat an identity without a new melody.
        perc_at = round(response + 0.5, 5)
        if perc_at < end and not any(abs(float(note.get("time_step", 0.0)) - perc_at) <= 0.10 for note in layers):
            layers.append({"time_step": perc_at, "note": "D1", "length": 0.08, "velocity": 46 + variant * 3, "ghost": True, "midi_signature_perc": True, "midi_motif_variant": variant})
            signature_perc += 1
        # A short boundary flick every other return replaces blanket fills.
        if variant in (0, 2) and response + 0.25 < end:
            fill_at = round(response + 0.25, 5)
            if not any(abs(float(note.get("time_step", 0.0)) - fill_at) <= 0.08 for note in hats):
                hats.append({"time_step": fill_at, "note": "C#5", "length": 0.07, "velocity": flick_vel, "hat_fill": True, "midi_boundary_fill": True})
                boundary_fills += 1
    tracks["hi_hats"] = sorted(hats, key=lambda note: float(note.get("time_step", 0.0)))
    tracks["snare"] = sorted(snares, key=lambda note: float(note.get("time_step", 0.0)))
    tracks["clap"] = sorted(claps, key=lambda note: float(note.get("time_step", 0.0)))
    tracks["snare_layer"] = sorted(layers, key=lambda note: float(note.get("time_step", 0.0)))
    return {"hat_breaths": breaths, "clap_stops": clap_stops, "hat_answers": answers, "snare_pickups": pickups, "signature_perc": signature_perc, "boundary_fills": boundary_fills}


def _longest_run(bars: list[int]) -> int:
    """Longest stretch of consecutive empty bars — a 10-bar hole is not 10 misses."""
    longest = run = 0
    previous: int | None = None
    for bar in sorted(bars):
        run = run + 1 if previous is not None and bar == previous + 1 else 1
        longest = max(longest, run)
        previous = bar
    return longest


def audit_fullkit_contract(pattern: dict[str, Any]) -> dict[str, Any]:
    plan = pattern.get("plg_midi_fullkit_plan")
    if not isinstance(plan, dict) or not plan.get("enabled"):
        return {"passed": True, "reason": "not_applicable", "phrases": []}
    hats, snares, claps = (track_notes(pattern, key) for key in ("hi_hats", "snare", "clap"))
    rows = []
    reasons: list[str] = []
    for phrase in plan.get("phrases", []):
        if not isinstance(phrase, dict):
            continue
        start, end = float(phrase["start"]), float(phrase["end"])
        hat_count = sum(start <= float(note.get("time_step", 0.0)) < end for note in hats)
        backbeat_count = sum(start <= float(note.get("time_step", 0.0)) < end for note in snares + claps)
        row = {"id": phrase.get("id"), "hat_mode": phrase.get("hat_mode"), "hats": hat_count, "backbeat": backbeat_count}
        rows.append(row)
        if phrase.get("hat_mode") != "breathe" and hat_count == 0:
            reasons.append(f"no_hats:{phrase.get('id')}")
        if phrase.get("backbeat_required") and backbeat_count == 0:
            reasons.append(f"no_backbeat:{phrase.get('id')}")
    # Per-BAR coverage, deliberately a meter and not a reason. The gate above
    # counts a whole phrase, so an eight-bar phrase carrying two snares reads
    # as "has a backbeat" while six of its bars ship empty — which is how the
    # 2026-08-19 audit found 52 of 72 bars silent on Sahara with this audit
    # green. Promoting it to a hard reason would let one miss reject an entire
    # generation through beat_quality._symbiosis_failures; same precedent as
    # _correct_808_vertical_clashes. Watch the number instead.
    backbeat_times = [float(note.get("time_step", 0.0) or 0.0) for note in snares + claps]
    allowed = [row for row in bar_brief(pattern) if row.get("backbeat_allowed")]
    empty = [
        int(row.get("bar", 0) or 0)
        for row in allowed
        if not any(
            int(row.get("bar", 0) or 0) * BEATS_PER_BAR <= time < (int(row.get("bar", 0) or 0) + 1) * BEATS_PER_BAR
            for time in backbeat_times
        )
    ]
    return {
        "passed": not reasons,
        "reasons": reasons,
        "phrases": rows,
        "bars_backbeat_allowed": len(allowed),
        "bars_without_backbeat": len(empty),
        "longest_backbeat_gap_bars": _longest_run(empty),
    }


def _paired_count(left: list[dict[str, Any]], right: list[dict[str, Any]], tolerance: float) -> int:
    """Count one-to-one near-simultaneous attacks without mutating either lane."""
    remaining = sorted(float(note.get("time_step", 0.0) or 0.0) for note in right)
    matched = 0
    for note in sorted(left, key=lambda item: float(item.get("time_step", 0.0) or 0.0)):
        point = float(note.get("time_step", 0.0) or 0.0)
        candidates = [
            (abs(other - point), index)
            for index, other in enumerate(remaining)
            if abs(other - point) <= tolerance
        ]
        if candidates:
            _distance, index = min(candidates)
            remaining.pop(index)
            matched += 1
    return matched


def audit_global_midi_symbiosis(pattern: dict[str, Any]) -> dict[str, Any]:
    """One final read-only health check for the seven active MIDI lanes.

    This is intentionally an audit, not another note generator: the existing
    melody/kit, placement and humanize owners remain the only writers.  It
    makes a broken interaction visible before FLP export instead of letting a
    later change quietly revive a second, conflicting lane.
    """
    tracks = pattern.get("tracks")
    if not pattern.get("plg_midi_melody") or not isinstance(tracks, dict):
        return {"passed": True, "reason": "not_midi_route"}

    notes = {lane: list(track_notes(pattern, lane)) for lane in _ACTIVE_MIDI_LANES}
    counts = {lane: len(items) for lane, items in notes.items()}
    reasons: list[str] = []
    for lane, count in counts.items():
        if count == 0:
            reasons.append(f"empty:{lane}")

    kick_808_pairs = _paired_count(notes["kick"], notes["808_bass"], _PAIR_TOLERANCE)
    # A deliberate double (2026-08-20, variant B) renders one onset as two
    # KICKS: its quieter pickup is kick_only by design, so it legitimately has
    # no 808 of its own — the pair's existing 808 tail keeps ringing through
    # both taps (_PAIR_TOLERANCE is far too small for the ghost to match that
    # tail anyway). The unpaired requirement counts onsets, not kick notes.
    from bass808_gen import is_ghost_kick

    ghost_kicks = sum(1 for note in notes["kick"] if is_ghost_kick(note))
    if notes["kick"] and kick_808_pairs != len(notes["kick"]) - ghost_kicks:
        reasons.append(f"unpaired_kick:{len(notes['kick']) - ghost_kicks - kick_808_pairs}")
    # A slide is a bend on a tail that is already sounding, not an attack, so
    # it has no kick of its own by definition. Every other owner of this rule
    # already skips slides — _correct_808_vertical_clashes reports them as
    # skipped_slides, _audit_midi_kick808_context leaves them out of its
    # bass_by_time index — and this audit was the one place still counting
    # them, which made the one deliberate slide in the engine
    # (_vary_repeated_lowend_cycle's variation_slide) read as an unpaired 808
    # and hard-fail the export the moment that pass started running
    # (2026-08-20).
    slide_808 = sum(1 for note in notes["808_bass"] if note.get("slide"))
    attack_808 = len(notes["808_bass"]) - slide_808
    if notes["808_bass"] and kick_808_pairs != attack_808:
        reasons.append(f"unpaired_808:{attack_808 - kick_808_pairs}")

    backbeat = notes["clap"] or notes["snare"]
    snare_clap_pairs = _paired_count(notes["snare"], notes["clap"], _BACKBEAT_TOLERANCE)
    layer_pairs = _paired_count(notes["snare_layer"], backbeat, _BACKBEAT_TOLERANCE)
    if notes["snare"] and notes["clap"] and snare_clap_pairs == 0:
        reasons.append("backbeat_unpaired")
    if notes["snare_layer"] and backbeat and layer_pairs == 0:
        reasons.append("layer_unpaired")

    return {
        "passed": not reasons,
        "active_lanes": list(_ACTIVE_MIDI_LANES),
        "counts": counts,
        "kick_808_pairs": kick_808_pairs,
        "snare_clap_pairs": snare_clap_pairs,
        "snare_layer_pairs": layer_pairs,
        "reasons": reasons,
    }


__all__ = ["attach_fullkit_plan", "bar_brief", "apply_generated_hat_contract", "apply_section_pulse_switch", "apply_lowend_roll_yield", "apply_strong_phrase_answers", "audit_fullkit_contract", "audit_global_midi_symbiosis"]
