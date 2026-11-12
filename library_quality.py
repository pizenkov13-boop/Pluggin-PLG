"""Non-destructive audio-health gates for the user sample library.

The original PLG_Sounds tree is never modified.  A scan records any rejected
asset in a virtual filtered catalog, with the reason, and the matcher consumes
that catalog rather than repeatedly rediscovering the same bad candidate.
"""

from __future__ import annotations

from pathlib import Path

from library_paths import audio_duration_seconds

_808_TRACKS = frozenset({"sub_808", "808_bass"})
_MIN_808_SECONDS = 0.35
_MAX_808_TRANSIENT_BODY_RATIO = 3.0
# Closed hi-hat on a 1/8 grid must die before the next tick. Longer one-shots
# (mislabeled opens, rides, trashy 0.3–0.8s "Hat" files) stack into a metallic
# sheet when retriggered every eighth. Bright/trashy closed ticks (centroid
# screaming above ~10 kHz) do the same even when short — tin on asphalt.
_MAX_CLOSED_HAT_SECONDS = 0.12
_MIN_CLOSED_HAT_SECONDS = 0.012
_MAX_HAT_CENTROID_HZ = 10000.0
_MAX_HAT_ZCR = 0.39


def bass_808_tail_rejection_reason(
    path: Path,
    notes: list[dict],
    bpm: float,
    passport: dict | None = None,
) -> str | None:
    """Reject an 808 when transposed playback exhausts its WAV tail.

    FL's sampler pitch changes playback speed. A C3 note with a C2 root
    consumes the source twice as fast, so a WAV that looks long enough in a
    file browser can still end in the middle of a sustained MIDI note.
    """
    duration = audio_duration_seconds(path)
    if duration is None or duration <= 0 or not notes or bpm <= 0:
        return None
    if passport is None:
        try:
            from sample_pitch_passport import sample_pitch_passport

            passport = sample_pitch_passport(path)
        except Exception:  # noqa: BLE001
            return "808_tail_unreadable"
    try:
        max_source_seconds = bass_808_max_source_seconds(notes, bpm, passport)
    except (TypeError, ValueError):
        return "808_tail_unreadable"
    return "808_tail_too_short_for_midi" if max_source_seconds > float(duration) else None


def _source_seconds_per_beat(note: dict, bpm: float, passport: dict) -> float:
    """Seconds of SAMPLE a note consumes per beat of its length.

    FL's sampler pitches by playback speed, so a note above the sample's root
    eats the source faster. One owner of this formula: the tail gate and
    ``fit_808_notes_to_duration`` must agree on it exactly.
    """
    from music_theory import parse_note_name

    root = int(passport.get("fl_root_note", 24))
    pitch_map = passport.get("pitch_class_map") or {}
    source_midi = parse_note_name(str(note.get("note") or "C2"))
    target_midi = int(pitch_map.get(str(source_midi % 12), source_midi))
    return (60.0 / float(bpm)) * 2.0 ** ((target_midi - root) / 12.0)


def bass_808_max_source_seconds(notes: list[dict], bpm: float, passport: dict) -> float:
    """Longest stretch of sample any (non-slide) note needs."""
    longest = 0.0
    for note in notes:
        if not isinstance(note, dict) or note.get("slide"):
            continue
        beats = max(0.0, float(note.get("length", 0.0) or 0.0))
        longest = max(longest, beats * _source_seconds_per_beat(note, bpm, passport))
    return longest


def fit_808_notes_to_duration(
    notes: list[dict],
    bpm: float,
    passport: dict,
    duration: float,
    *,
    min_length: float,
    safety: float = 0.97,
) -> bool:
    """Shorten 808 notes so none outruns a sample of ``duration`` seconds.

    Never lengthens a note and never goes below ``min_length`` beats (a shorter
    808 is a click, and the export's low-end contract forbids it). Slide notes
    bend an already-sounding voice and are left alone, as the tail gate does.
    All-or-nothing: returns False and touches NOTHING when any note cannot fit,
    so the caller can move on to a longer sample.
    """
    if duration <= 0 or bpm <= 0:
        return False
    plan: list[tuple[dict, float]] = []
    for note in notes:
        if not isinstance(note, dict) or note.get("slide"):
            continue
        beats = max(0.0, float(note.get("length", 0.0) or 0.0))
        per_beat = _source_seconds_per_beat(note, bpm, passport)
        if per_beat <= 0:
            return False
        allowed = duration * safety / per_beat
        if allowed >= beats:
            continue
        if allowed < min_length:
            return False
        plan.append((note, round(allowed, 4)))
    for note, length in plan:
        note["length"] = length
    return True


def audio_rejection_reason(
    path: Path,
    *,
    category: str | None = None,
    track: str | None = None,
) -> str | None:
    """Return a deterministic safety reason, or ``None`` when usable.

    This is intentionally narrow: an 808 sample is both a category and a
    note-triggered voice, so a click without a sustained body is objectively
    wrong. Closed hats have a hard duration ceiling for the same reason —
    long tails on an 1/8 grid are objectively wrong. Other sound families
    stay fail-open until they have an equally reliable signal-based rule.
    """
    if track == "hi_hats" or category == "hats":
        duration = audio_duration_seconds(path)
        if duration is not None and duration > _MAX_CLOSED_HAT_SECONDS:
            return "too_long_for_closed_hat_tick"
        if duration is not None and duration < _MIN_CLOSED_HAT_SECONDS:
            return "too_short_for_hat_tick"
        import audio_features

        features = audio_features.analyze_cached(path)
        if features:
            centroid = float(features.get("centroid", 0.0) or 0.0)
            zcr = float(features.get("zcr", 0.0) or 0.0)
            if centroid > _MAX_HAT_CENTROID_HZ and zcr > 0.30:
                return "metallic_closed_hat_tick"
            if zcr > _MAX_HAT_ZCR and centroid > 9000.0:
                return "metallic_closed_hat_tick"

    is_808 = category == "808" or track in _808_TRACKS
    if not is_808:
        return None

    duration = audio_duration_seconds(path)
    if duration is not None and duration < _MIN_808_SECONDS:
        return "too_short_for_808_body"

    # Import lazily: catalog scans of non-808-only libraries should remain as
    # cheap as before, while analyzed candidates are cached by audio_features.
    import audio_features

    features = audio_features.analyze_cached(path)
    if features and float(features.get("transient_body_ratio", 0.0)) > _MAX_808_TRANSIENT_BODY_RATIO:
        return "transient_without_808_body"
    if track == "808_bass" and features:
        # ``808_bass`` is the kick-locked, audible low-end lane — not the
        # separate pure-sub lane. A near-sine with a very slow attack has
        # enough RMS to win a name/loudness rank but disappears under a kick
        # and gives the exact “it exists but isn't there” result in FL.
        centroid = float(features.get("centroid", 0.0) or 0.0)
        attack = float(features.get("attack", 0.0) or 0.0)
        if centroid < 70.0 and attack > 100.0:
            return "sub_only_slow_808_bass"
        # Opposite failure: a pre-destroyed distorted file leaves no room for
        # kick attack or the project's own clipper/saturation.
        if centroid > 3200.0:
            return "overdriven_808_bass"
        # A very bright source remains a whistle/lead after transposition even
        # when it has enough duration to pass the generic body check.
        if centroid > 500.0:
            return "too_bright_for_808_bass"
    return None


__all__ = [
    "bass_808_tail_rejection_reason",
    "bass_808_max_source_seconds",
    "fit_808_notes_to_duration",
    "audio_rejection_reason",
]
