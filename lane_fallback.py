"""Last rung of the sample ladder for the three required MIDI-mode lanes.

Selection (``starter_kit`` / ``sample_match``) stays strict and runs first. Only
when a required lane (kick, 808_bass, hi_hats) ends up with no sample that
passes the export gate does this module decide, ONCE, what plays instead:

  tier 1  the user's own closest file, but only when it fails SOFT reasons
          (too bright, slow sub, unclear name, or an 808 too short for the
          notes — the notes are then shortened to fit);
  tier 2  a synthetic one-shot that passes every gate by construction
          (``fallback_samples``). Only when ``sound_policy.substitutes_allowed()``
          (``PLG_SUBSTITUTE_SOUNDS=on``); by default there is no tier 2.

With no tier 2 (the default, variant B 2026-10) a lane the user has no usable
file for is recorded as ABSENT (``tier`` 0, ``sample`` None). Inside the
generation the lane and its notes live on (hats, 808 chords and the audits read
them); ``flp_template.midi_hidden_keys`` drops an absent lane from the .flp at
export (channel, pattern, playlist row), and ``midi_validate`` accepts the record
instead of a sample. The melody lane is never absent: it carries no sample by
design (``sound_policy.melody_sample_enabled``).

A HARD reason is never overridden — a pre-distorted 808 (``overdriven``), an
808 with no body, an unreadable file, a wrong-type name, a loop: those go
straight to tier 2 (or to absent). The decision is recorded in
``pattern["plg_sample_fallback"]`` and export reads it
(``sample_match.rejection_reason_for_pattern``) instead of second-guessing it.
Nothing here reaches the UI.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from sound_policy import substitutes_allowed

logger = logging.getLogger(__name__)

REQUIRED_LANES = ("kick", "808_bass", "hi_hats")

# Reasons a user's file may still serve the lane with. Everything not listed is
# hard. ``overdriven_808_bass`` is deliberately hard: it is the one 808 gate tied
# to the harsh-noise history, so an over-distorted file always gets the clean
# synthetic instead.
SOFT_REASONS: dict[str, frozenset[str]] = {
    "808_bass": frozenset({"too_bright_for_808_bass", "sub_only_slow_808_bass", "missing_track_identity"}),
    "hi_hats": frozenset({"metallic_closed_hat_tick", "missing_track_identity"}),
    "kick": frozenset({"missing_track_identity"}),
}
_GROUPS: dict[str, tuple[str, ...]] = {
    "808_bass": ("808", "kits"),
    "hi_hats": ("hats", "kits", "splice"),
    "kick": ("kits", "808"),
}
# What a user would recognise as "my own <lane> file". Narrower than _GROUPS on
# purpose: a kit with no 808 in it is not "an 808 that could not be used", and
# 808s in the library are not "kicks that could not be used".
_OWN_GROUPS: dict[str, tuple[str, ...]] = {
    "808_bass": ("808",),
    "hi_hats": ("hats",),
    "kick": ("kits",),
}


def _resolved(path: Any) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)


def _record(
    pattern: dict[str, Any],
    lane: str,
    path: Path,
    tier: int,
    reasons: list[str],
    user_files: int = 0,
) -> None:
    """Record the decision. ``user_files`` (tier 2 only) is how many of the
    user's own files of this kind existed and were all turned down — it is what
    lets the UI say "your 808 was replaced" only when that is true."""
    pattern.setdefault("plg_sample_fallback", {})[lane] = {
        "tier": tier,
        "sample": str(path),
        "reasons": list(reasons),
        "user_files": int(user_files),
    }
    if reasons:
        detail = f": {', '.join(reasons)}"
    elif user_files:
        detail = f", synthetic — none of the user's {user_files} own file(s) could be used"
    else:
        detail = ", synthetic"
    logger.warning("Lane fallback: %s <- %s (tier %d%s)", lane, Path(path).name, tier, detail)


def _record_absent(pattern: dict[str, Any], lane: str, reasons: list[str], user_files: int) -> None:
    """The lane has no sound: no file of the user's serves it and we put none of ours."""
    pattern.setdefault("plg_sample_fallback", {})[lane] = {
        "tier": 0,
        "sample": None,
        "reasons": list(reasons),
        "user_files": int(user_files),
    }
    logger.warning("Lane fallback: %s has no sample (%d own file(s) turned down)%s", lane, user_files,
                   f": {', '.join(reasons)}" if reasons else "")


#: The drum lanes that can be absent (and so be left out of the .flp). The melody is not among them.
ABSENT_LANES = ("kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer")


#: What a snare / clap file may still be turned down for and serve the lane: only a word in a FOLDER name (the
#: FILE name must not name another drum, and the audio must not clearly be another drum).
_BACKBEAT_SOFT_REASONS = frozenset({"name_or_path_conflicts_with_track"})


def _soft_backbeat_file(
    lane: str,
    used: set[str],
    catalog: dict[str, Any] | None,
    root: Path,
) -> tuple[Path, list[str]] | None:
    """The user's own file closest to a snare / clap lane that the strict pick could not fill.

    Same idea as tier 1 of the required lanes (``_best_user_file``): a file the strict gates turned down only for
    a SOFT reason still beats a silent lane. Only files of the user's library, never a built-in sound."""
    if not catalog:
        return None
    from sample_match import iter_track_candidates_grouped, score_candidate, track_candidate_rejection_reasons

    import sound_type

    seed = _variety_seed()
    ranked: list[tuple[tuple, Path, list[str]]] = []
    for rel, group in iter_track_candidates_grouped(catalog, lane):
        if group not in ("kits", "splice"):
            continue
        path = (Path(root) / rel).resolve()
        if not path.is_file() or str(path) in used:
            continue
        reasons = track_candidate_rejection_reasons(path, lane)
        if not reasons or any(reason not in _BACKBEAT_SOFT_REASONS for reason in reasons):
            continue
        if score_candidate(path.name, lane, prompt_tokens=set(), style_tokens=set(), prompt_raw="", group=group) <= -50_000:
            continue  # the FILE name itself names another drum
        if sound_type.conflicts(path, lane):
            continue
        band = -int(sound_type.fit(path, lane) * 5) if sound_type.enabled() else 0
        ranked.append(((band, _rotation(seed, lane, path), str(path)), path, reasons))
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[0])
    _key, path, reasons = ranked[0]
    return path, reasons


def mark_absent_backbeat_lanes(
    pattern: dict[str, Any],
    sound_map: dict[str, Path],
    catalog: dict[str, Any] | None = None,
    root: Path | None = None,
) -> None:
    """Give snare / clap their last-resort file, then record what still has no sound as absent.

    A snare or clap the strict pick left empty gets the user's closest soft-failing file (tier 1, like kick / 808 /
    hats), never a built-in sound. What still has none is recorded as absent, like the required lanes.

    The layer follows the snare: with no snare there is no layer, even when the matcher found
    the layer a file of its own (it would only double the clap on the snare's places) - unless the
    user picked that layer by hand. MIDI route only.
    Mutates ``sound_map`` (drops the layer) and ``pattern`` (the records).
    """
    if not pattern.get("plg_midi_melody"):
        return
    tracks = pattern.get("tracks") if isinstance(pattern.get("tracks"), dict) else {}
    if catalog and root is not None:
        for lane in ("snare", "clap"):
            if sound_map.get(lane) or not tracks.get(lane) or lane_is_absent(pattern, lane):
                continue
            used = {_resolved(p) for name, p in sound_map.items() if name != lane and p}
            found = _soft_backbeat_file(lane, used, catalog, Path(root))
            if found is not None:
                path, reasons = found
                sound_map[lane] = path
                _record(pattern, lane, path, 1, reasons)
    if not sound_map.get("snare") and "snare_layer" not in explicit_lanes(pattern):
        sound_map.pop("snare_layer", None)  # a layer the user picked by hand stays on its own
    for lane in ("snare", "clap", "snare_layer"):
        if sound_map.get(lane) or not tracks.get(lane) or lane_is_absent(pattern, lane):
            continue
        reasons = ["snare_absent"] if lane == "snare_layer" and not sound_map.get("snare") else []
        _record_absent(pattern, lane, reasons, 0)


def explicit_lanes(pattern: Any) -> set[str]:
    """Lanes whose sound the user chose by hand in the sound browser (``plg_explicit_lanes``)."""
    raw = pattern.get("plg_explicit_lanes") if isinstance(pattern, dict) else None
    return {str(lane) for lane in raw} if isinstance(raw, list) else set()


def lane_is_explicit(pattern: Any, lane: str) -> bool:
    """True when the lane plays a sound the user chose by hand (the gates do not judge it)."""
    record = pattern.get("plg_sample_fallback") if isinstance(pattern, dict) else None
    entry = record.get(lane) if isinstance(record, dict) else None
    return isinstance(entry, dict) and bool(entry.get("explicit"))


def _keep_explicit(lane: str, path: Path, pattern: dict[str, Any], notes: list[dict], bpm: float) -> None:
    """The user chose this file: keep it whatever the gates say, and record that so export agrees.

    The record is a tier-1 one listing EVERY reason the file fails (``sample_match.
    rejection_reason_for_pattern`` then has nothing left to refuse). An 808 shorter than its notes
    gets the notes shortened to fit, exactly as in tier 1."""
    from sample_match import track_candidate_rejection_reasons

    try:
        reasons = list(track_candidate_rejection_reasons(path, lane))
    except Exception:  # noqa: BLE001 - an unreadable file is the user's choice too; export decides
        reasons = []
    if lane == "808_bass":
        _fit_808_notes(path, notes, bpm)
    pattern.setdefault("plg_sample_fallback", {})[lane] = {
        "tier": 1,
        "sample": str(path),
        "reasons": reasons,
        "user_files": 0,
        "explicit": True,
    }
    logger.info("Lane %s: the user's own choice %s is kept (%s)", lane, path.name, ", ".join(reasons) or "no objection")


def _fit_808_notes(path: Path, notes: list[dict], bpm: float) -> None:
    from bass808_gen import MIN_BASS_LENGTH
    from library_paths import audio_duration_seconds
    from library_quality import fit_808_notes_to_duration
    from sample_pitch_passport import sample_pitch_passport

    fit_808_notes_to_duration(
        notes,
        bpm,
        sample_pitch_passport(path),
        audio_duration_seconds(path) or 0.0,
        min_length=MIN_BASS_LENGTH,
    )


def lane_is_absent(pattern: Any, lane: str) -> bool:
    """True when selection recorded that ``lane`` deliberately has no sample."""
    record = pattern.get("plg_sample_fallback") if isinstance(pattern, dict) else None
    entry = record.get(lane) if isinstance(record, dict) else None
    return isinstance(entry, dict) and entry.get("tier") == 0 and not entry.get("sample")


def _own_file_count(lane: str, catalog: dict[str, Any] | None, root: Path) -> int:
    """How many of the user's own files of this lane's kind are in the library."""
    if not catalog:
        return 0
    from sample_match import iter_track_candidates_grouped

    return sum(
        1
        for rel, group in iter_track_candidates_grouped(catalog, lane)
        if group in _OWN_GROUPS[lane] and (Path(root) / rel).is_file()
    )


#: The drum lanes whose sound the user supplies (or does not): what ``plg_sample_origins`` lists.
ORIGIN_LANES = ("kick", "snare", "clap", "snare_layer", "hi_hats", "808_bass")


def sample_origins(
    pattern: dict[str, Any],
    pre_bake: dict[str, Any] | None,
    root: Path,
) -> dict[str, dict[str, str]]:
    """Whose sound plays each drum lane: ``{lane: {"origin", "file", "path"}}``.

    ``path`` is the full path of the SOURCE file (before any baked copy; empty for an absent lane):
    ``tools/show_flp_sounds.py`` prints it next to the baked file a channel loads.

    ``pre_bake`` is the sound map BEFORE the warmth / phase / width copies, whose
    names would hide the source. Origins:

    * ``user``      - a file inside the user's library;
    * ``user_soft`` - the user's file, but one that only passed the last-resort
      ladder (tier 1: e.g. too bright for an 808, no name to say what it is);
    * ``builtin``   - a bundled starter sound (the library has no file for the lane);
    * ``synthetic`` - a generated 808 / hat (tier 2).
    * ``absent``    - no sound at all: the lane is not in the .flp, its notes stay in the data.

    The fallback record wins over the map: it is the final arbiter's word. Never
    raises: a notice must not fail a finished beat.
    """
    from library_paths import relative_in_library

    records = pattern.get("plg_sample_fallback") if isinstance(pattern.get("plg_sample_fallback"), dict) else {}
    out: dict[str, dict[str, str]] = {}
    for lane in ORIGIN_LANES:
        try:
            record = records.get(lane) if isinstance(records, dict) else None
            if isinstance(record, dict):
                if lane_is_absent(pattern, lane):
                    out[lane] = {"origin": "absent", "file": "", "path": ""}
                    continue
                sample = Path(str(record.get("sample") or ""))
                if record.get("explicit") and (pre_bake or {}).get(lane):
                    sample = Path(str((pre_bake or {})[lane]))  # the record holds the baked copy; the user's file is the source
                if record.get("explicit"):
                    origin = "user"  # chosen by hand in the sound browser: the user's own, not a soft pass
                elif int(record.get("tier") or 0) == 1:
                    origin = "user_soft"
                elif "fallback" in sample.parent.name.lower() or sample.name.startswith("PLG_fallback"):
                    origin = "synthetic"
                else:
                    origin = "builtin"
                out[lane] = {"origin": origin, "file": sample.name, "path": str(sample)}
                continue
            path = (pre_bake or {}).get(lane)
            if not path:
                out[lane] = {"origin": "absent", "file": "", "path": ""}
                continue
            path = Path(str(path))
            try:
                relative_in_library(path, Path(root))
                origin = "user"
            except (OSError, ValueError):
                origin = "builtin"
            out[lane] = {"origin": origin, "file": path.name, "path": str(path)}
        except Exception:  # noqa: BLE001 - one odd entry must not hide the others
            continue
    return out


def fallback_lanes_for_user(pattern: dict[str, Any]) -> list[str]:
    """Lanes where a synthetic sound replaced the user's own files — the ones
    worth telling the user about. Empty when nothing was replaced."""
    record = pattern.get("plg_sample_fallback") if isinstance(pattern, dict) else None
    if not isinstance(record, dict):
        return []
    return [
        lane
        for lane in REQUIRED_LANES
        if isinstance(record.get(lane), dict)
        and record[lane].get("tier") == 2
        and int(record[lane].get("user_files") or 0) > 0
    ]


def _best_user_file(
    lane: str,
    pattern: dict[str, Any],
    used: set[str],
    catalog: dict[str, Any] | None,
    root: Path,
    notes: list[dict],
    bpm: float,
) -> tuple[Path, list[str]] | None:
    if not catalog:
        return None
    from sample_match import (
        iter_track_candidates_grouped,
        score_candidate,
        track_candidate_rejection_reasons,
    )

    import sound_type

    soft = SOFT_REASONS[lane]
    seed = _variety_seed()
    ranked: list[tuple[tuple, Path, list[str]]] = []
    for rel, group in iter_track_candidates_grouped(catalog, lane):
        if group not in _GROUPS[lane]:
            continue
        path = (Path(root) / rel).resolve()
        if not path.is_file() or str(path) in used:
            continue
        # Judged exactly as export will judge it (no library root).
        reasons = track_candidate_rejection_reasons(path, lane)
        if any(reason not in soft for reason in reasons):
            continue
        if lane == "808_bass" and not _808_can_serve(path, notes, bpm):
            continue
        closeness = score_candidate(
            rel, lane, prompt_tokens=set(), style_tokens=set(), prompt_raw="", group=group,
            audio_vouches=sound_type.vouches(path, lane),
        )
        # Ties used to fall to the path, so the first file alphabetically played
        # on every generation, whatever it sounded like. Now: the sound decides
        # (sound_type.fit, in coarse bands so near-equals stay equal), then the
        # generation's variety seed rotates among those.
        band = -int(sound_type.fit(path, lane) * 5) if sound_type.enabled() else 0
        ranked.append(((len(reasons), -closeness, band, _rotation(seed, lane, path), str(path)), path, reasons))
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[0])
    _key, path, reasons = ranked[0]
    return path, reasons


def _variety_seed() -> int | None:
    try:
        from generation_variety import active_seed

        return active_seed()
    except Exception:  # noqa: BLE001 - rotation is a nicety, never a failure
        return None


def _rotation(seed: int | None, lane: str, path: Path) -> int:
    """A stable per-generation shuffle key; 0 for everyone without a seed (and
    with ``PLG_SOUND_TYPE=off``), which keeps the old path-ordered result."""
    import hashlib

    import sound_type

    if seed is None or not sound_type.enabled():
        return 0
    digest = hashlib.sha1(f"{seed}|{lane}|{Path(path).name}".encode("utf-8", "ignore")).digest()
    return int.from_bytes(digest[:4], "big")


def _808_can_serve(path: Path, notes: list[dict], bpm: float) -> bool:
    """Could this file play the song's notes once they are shortened to fit?"""
    from bass808_gen import MIN_BASS_LENGTH
    from library_paths import audio_duration_seconds
    from library_quality import fit_808_notes_to_duration
    from sample_pitch_passport import sample_pitch_passport

    duration = audio_duration_seconds(path)
    if duration is None or duration <= 0:
        return False
    try:
        passport = sample_pitch_passport(path)
    except Exception:  # noqa: BLE001
        return False
    needed = _needed_pitch_classes(notes)
    weak = set(passport.get("weak_pitch_classes") or ())
    if weak & needed:
        return False
    return fit_808_notes_to_duration(
        copy.deepcopy(notes), bpm, passport, duration, min_length=MIN_BASS_LENGTH
    )


def _needed_pitch_classes(notes: list[dict]) -> set[int]:
    from music_theory import parse_note_name

    pcs: set[int] = set()
    for note in notes or []:
        if not isinstance(note, dict) or note.get("slide"):
            continue
        try:
            pcs.add(parse_note_name(str(note.get("note") or "C2")) % 12)
        except (TypeError, ValueError):
            continue
    return pcs


def _current_reasons(lane: str, current: Any) -> list[str]:
    """Why the sample the map holds for ``lane`` was turned down (for the absent record)."""
    if not current:
        return []
    try:
        from sample_match import track_candidate_rejection_reasons

        return list(track_candidate_rejection_reasons(Path(current), lane))
    except Exception:  # noqa: BLE001 - a diagnostic must not fail a beat
        return []


def resolve_lane_sample(
    lane: str,
    pattern: dict[str, Any],
    sound_map: dict[str, Path],
    catalog: dict[str, Any] | None,
    root: Path,
) -> Path | None:
    """Pick the tier-1 or tier-2 sample for ``lane`` and record the decision.

    With substitutes allowed this always returns a path (tier 2 cannot fail). By
    default there is no tier 2: when no file of the user's serves the lane the
    result is ``None`` and the lane is recorded as absent. Mutates ``pattern`` (the
    fallback record, and the 808 notes when they are shortened to fit).
    """
    tracks = pattern.get("tracks") if isinstance(pattern.get("tracks"), dict) else {}
    notes = tracks.get(lane) or []
    bpm = float(pattern.get("bpm", 140) or 140)
    # The retired sub_808 lane still gets a pick in resolve_track_samples, but
    # export deletes it: it must never hold a file another lane needs.
    used = {_resolved(p) for name, p in sound_map.items() if name not in (lane, "sub_808") and p}

    found = _best_user_file(lane, pattern, used, catalog, Path(root), notes, bpm)
    if found is not None:
        path, reasons = found
        if lane == "808_bass":
            _fit_808_notes(path, notes, bpm)
        _record(pattern, lane, path, 1, reasons)
        return path

    if not substitutes_allowed():
        _record_absent(pattern, lane, _current_reasons(lane, sound_map.get(lane)), _own_file_count(lane, catalog, Path(root)))
        return None

    import fallback_samples

    if lane == "808_bass":
        path = fallback_samples.fallback_808(notes, bpm)
    elif lane == "hi_hats":
        path = fallback_samples.fallback_hat()
    else:
        from starter_kit import ensure_starter_kit

        path = ensure_starter_kit()["kick"]
    _record(pattern, lane, path, 2, [], user_files=_own_file_count(lane, catalog, Path(root)))
    return path


def _lane_is_export_clean(lane: str, path: Path, pattern: dict[str, Any], notes: list[dict], bpm: float) -> bool:
    from sample_match import rejection_reason_for_pattern

    if rejection_reason_for_pattern(path, lane, pattern) is not None:
        return False
    if lane != "808_bass" or not notes:
        return True
    from library_quality import bass_808_tail_rejection_reason
    from sample_pitch_passport import sample_pitch_passport

    try:
        passport = sample_pitch_passport(path)
    except Exception:  # noqa: BLE001
        return False
    if bass_808_tail_rejection_reason(path, notes, bpm, passport) is not None:
        return False
    return not (set(passport.get("weak_pitch_classes") or ()) & _needed_pitch_classes(notes))


def ensure_required_lane_samples(
    pattern: dict[str, Any],
    sound_map: dict[str, Path],
    catalog: dict[str, Any] | None,
    root: Path,
) -> None:
    """Final arbiter, run on the FINAL sound map (after every bake/transform).

    A required lane whose sample would fail export gets the ladder instead. MIDI
    route only: that is where the export contract exists.
    """
    if not pattern.get("plg_midi_melody"):
        return
    tracks = pattern.get("tracks") if isinstance(pattern.get("tracks"), dict) else {}
    bpm = float(pattern.get("bpm", 140) or 140)
    explicit = explicit_lanes(pattern)
    for lane in REQUIRED_LANES:
        notes = tracks.get(lane) or []
        if lane == "808_bass" and not notes:
            continue
        current = sound_map.get(lane)
        if lane in explicit and current is not None:
            _keep_explicit(lane, Path(current), pattern, notes, bpm)
            continue
        if current is not None and _lane_is_export_clean(lane, Path(current), pattern, notes, bpm):
            continue
        resolved = resolve_lane_sample(lane, pattern, sound_map, catalog, root)
        if resolved is None:
            sound_map.pop(lane, None)  # absent: the lane is left out of the .flp
        else:
            sound_map[lane] = resolved
