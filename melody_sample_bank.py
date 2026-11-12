"""Bundled melody-sample fallback (2026-07): a curated pool of hand-
picked one-shot/sustain WAVs (assets/starter/PLG_Samples — bells, plucks,
pads, vocal pads, keys, cowbell; any number of .wav files drop in and are
compared) that stands in for melody_lead when the user's own library has
nothing that scores well for it, or when a keyword library match is a poor
audio fit for a sustain-leaning MIDI profile — insurance so an uploaded
MIDI melody never plays back as a bare default/empty instrument.

Picking is driven by the ACTUAL audio content on both sides, never filenames:
the uploaded MIDI melody's own note-length/register profile (this module) is
compared against each candidate's audio_features (duration + sustain
character via analyze_sustain_cached, brightness via analyze) to find the
best musical fit. A file's name only ever contributes a small bonus, never a
veto or a decisive score — see _score_candidate. The one exception is the
root-pitch correction below, which reads a key tag from the filename on
purpose (see "Root-pitch correction").

Sustain demand is a graduated affinity in [0, 1], not a hard binary gate —
borderline MIDI (e.g. long_fraction≈0.16 / max≈2s) still leans toward
sustained samples instead of being treated as plucky.

Vibe/genre fit (2026-07): the sustain/coverage terms alone let one "most
sustained" file in the pool win for EVERY sustain-lean MIDI regardless of
genre (a dark aggressive trap beat and a dreamy ambient beat would both land
on the same vocal pad) — reported live as "выбралось говно" for a trap beat
that got an angelic/seagull vocal pad. _score_candidate now also folds in
audio_features.target_from_prompt/feature_match_score — the same
descriptor-direction scoring pick_best_for_track and kit_variety already use
for prompt-driven timbre matching (e.g. "dark" -> low centroid) — so a
bright/airy candidate loses points against a dark-toned prompt and a warm
one gains them, instead of sustain alone deciding.

Root-pitch correction (2026-07): several bank files carry an explicit key in
their filename (``MO_TC_vocal_pad_seagulls_Dmin.wav`` -> D minor,
``..._Fmaj.wav`` -> F major) — real musical/tonal content, not a one-shot
transient. flp_writer's Sampler channels always assume the loaded sample's
own natural pitch is C5 (MIDI note 60) and pitch-shift every note relative to
that fixed assumption (see flp_writer._channel_sampler_settings_events / the
"key (MIDI note, C5 = 60)" convention). A file whose real tonal center is
anything other than C therefore plays back systematically off-key by the gap
between its real key and C — inaudible on a short one-shot's transient, very
audible on a sustained pad holding a wrong chord tone against the rest of
the (correctly-rooted) arrangement. ``resolve_playback_path`` detects that
gap via ``midi_corpus.key_from_name`` (already used for the user's own MIDI
corpus) and bakes a small corrective pitch-shift — same resample-based
technique as sample_chop_engine._pitch_shift — into a cached copy before it
ever reaches the Sampler, so the file's audible root lands on C regardless
of what key it was authored in. Files with no key tag (bells, plucks,
cowbell, Cxdy set) are untouched — no filename key claim, no correction.

No sample looping (2026-07 investigation, shelved): a long MIDI note can
still outlast even the best-matching sample. FL's native Sampler DOES have a
usable loop-point UI (Channel plugin -> Playback -> "Use loop points" +
START/END, confirmed live in FL Studio 25.2.2 via computer-use), and looping
is safely note-independent there (a short note's note-off arrives before
playback ever reaches the loop region, so enabling it channel-wide doesn't
affect short notes) — but the .flp byte encoding for it could not be
cleanly isolated in reasonable time: diffing a real FL save with loop points
set against one without showed the change isn't a couple of extra
start/end integers but a much larger rewritten stretch of the Sampler's
internal parameters blob (confirmed NOT random save-to-save noise — two
resaves of the identical, unchanged project produced byte-identical files —
but entangled with what looks like other serialized UI/session state, e.g.
literal "Loop off" text appearing near the changed region, unrelated to the
sample's own loop points). Revisiting this needs a few more controlled saves
(isolate "enable" from "specific point values", and hold the surrounding UI
interaction sequence constant) before it's worth writing bytes blind.
Mitigation without it: the "coverage" term in _score_candidate already biases
long-note profiles toward samples whose OWN duration already covers the
MIDI's longest note, so an audible early cutoff is the rare case, not the
default — a sample simply plays through and stops naturally when it isn't.

Note-shape hard gate (2026-07): the sustain-affinity terms above are a
CONTINUOUS lean (long notes -> want sustain), which is right for "how long a
sample should ring" but blind to "what kind of instrument this musical line
actually is" — a dense, monophonic 1/16-grid electronic arpeggio jumping
octave to octave has a low median note length just like a pluck, but nothing
in the old scoring stopped a vocal-sounding pad from winning it purely on
vibe/coverage math, producing exactly the reported "пищащий бурундук" (a
vocal-ish sample pitch-shifted across a 2-octave arpeggio -> chipmunk).
classify_melody_shape() answers a narrower, purely geometric question first —
"does this MIDI track's own note grid (overlap, length, spacing, pitch-jump
pattern) look like a chord bed, a fast electronic lead, or a vocal-chop line"
— using ONLY note-level math (time_step/length/pitch), no audio, no filenames.
pick_melody_sample() then hard-filters the candidate pool to whichever
_sample_shape_category the classification allows before any audio-fit/vibe
scoring above ever runs.

Musical mutations / multi-export (2026-07): one "perfect" sample for a MIDI
line is a myth — the ear needs options. pick_melody_sample(mode=...) now
supports three strategies that the archived variant exporter wrote as Track_V1/V2/V3.flp:
safe (strict shape+audio math), contrast (opposite texture + heavy space FX
via mix macros), chaos (seeded random from the full bank, still
root-pitch-locked). See MUTATION_* constants.

Sample-side classification is likewise pure signal, no filenames, no hand-typed
per-file lists (2026-07 correction — an earlier revision hand-tagged the 13
current bank files by name, which promptly banned a file named
"...vocal_pad_angelic_vocals..." from the electronic-lead bucket purely
because of its name, even though its MEASURED waveform — 18.5s, loud
throughout — is exactly the dense/sustained texture a fast arpeggio can ring
beautifully under). _sample_shape_category() looks only at
analyze_sustain_cached's duration_s and tail_rms_ratio (the sample's own
ADSR-tail: does the waveform stay loud across most of its length, or does it
spike once and die): long-and-still-loud -> SAMPLE_SHAPE_SUSTAINED_DENSE
(Flutter_Piano, keys.wav, the Cxdy pack, and yes, the vocal-pad files all
measure this way — a bell that happens to have "vocal" in its filename would
NOT), short-and-fast-decaying -> SAMPLE_SHAPE_SHORT_TRANSIENT. A sustained/
dense file can ring under BOTH a held chord AND a fast atmospheric arpeggio
(retriggering a long evolving texture every 1/16 note is a legitimate, common
"wall of sound" plugg-lead technique) — only "chords" excludes the short,
fast-decaying bucket (a one-shot can't hold a bar-long chord) and only
"vocal_chop" excludes the sustained/dense bucket (a chop needs a hit that
actually stops between phrases, not a wash of pad). Ambiguous/real-world-messy
MIDI that doesn't cleanly match one of the three archetypes classifies as
"unknown" and competes across both sample categories, same as before. If the
shape-filtered pool would ever be empty, filtering is skipped entirely rather
than returning no sample at all — a mismatched pick beats a silent track.
"""

from __future__ import annotations

import logging
import random
import statistics
from pathlib import Path
from typing import Any

from audio_features import analyze, analyze_cached, analyze_sustain_cached, feature_match_score, target_from_prompt
from pattern_utils import parse_note_name, track_notes
from plg_paths import melody_sample_bank_dir, user_data_dir

logger = logging.getLogger("plg.melody_sample_bank")

LONG_NOTE_S = 1.5  # a note held this long or more reads as "sustained"

PITCH_CACHE_DIR = user_data_dir() / "melody_bank_pitch"

# Soft floor: MIDI sustain affinity at/above this triggers bank-vs-library
# compete and blocks length-based auto-chop. Calibrated so the real borderline
# profile (~0.159 long_fraction / ~2.03s max) clears it (~0.27–0.30 affinity).
SUSTAIN_AFFINITY_SOFT_FLOOR = 0.15

# Below this duration, analyze_sustain's tail_rms_ratio is unreliable — its
# head/tail (~1s each) windows overlap on a short file, so the ratio
# trivially saturates near 1.0 regardless of the sample's real character
# (confirmed empirically against every PLG_Samples file: cowbell_ignorant.wav,
# 0.5s, reports tail_rms_ratio=1.0 despite being a plain short one-shot).
SUSTAINED_MIN_DURATION_S = 4.0
SUSTAINED_MIN_TAIL_RATIO = 0.3


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _midi_melody_profile(notes: list[dict[str, Any]], bpm: float) -> dict[str, float] | None:
    """Symbolic profile of an ingested MIDI melody's note-length/register
    character — pure note-data math, no audio involved."""
    if not notes:
        return None
    beat_s = 60.0 / max(1.0, float(bpm))
    lengths_s: list[float] = []
    pitches: list[int] = []
    for entry in notes:
        try:
            lengths_s.append(float(entry.get("length", 0.0)) * beat_s)
        except (TypeError, ValueError):
            continue
        try:
            pitches.append(parse_note_name(str(entry.get("note", "C4"))))
        except (ValueError, IndexError):
            continue
    if not lengths_s:
        return None
    long_count = sum(1 for length in lengths_s if length >= LONG_NOTE_S)
    return {
        "max_length_s": max(lengths_s),
        "median_length_s": statistics.median(lengths_s),
        "long_fraction": long_count / len(lengths_s),
        "median_pitch": statistics.median(pitches) if pitches else 60.0,
    }


def sustain_affinity(profile: dict[str, float]) -> float:
    """Graduated [0, 1] how much this MIDI profile wants a sustained sample.

    Soft ramps (not hard gates): long_fraction from 0.05→0.45, max_length_s
    from 1.2→4.0s, median_length_s from 0.8→3.0s (half weight). Borderline
    real-world profiles (~0.16 / ~2s) land around 0.27–0.30.
    """
    frac_w = _clamp01((profile.get("long_fraction", 0.0) - 0.05) / 0.40)
    max_w = _clamp01((profile.get("max_length_s", 0.0) - 1.2) / 2.8)
    median_w = _clamp01((profile.get("median_length_s", 0.0) - 0.8) / 2.2)
    return max(frac_w, max_w, 0.5 * median_w)


def midi_sustain_affinity(pattern: dict[str, Any]) -> float:
    """Public helper: sustain affinity of pattern['tracks']['melody_lead']."""
    notes = track_notes(pattern, "melody_lead")
    bpm = float(pattern.get("bpm", 140) or 140)
    profile = _midi_melody_profile(notes, bpm)
    if profile is None:
        return 0.0
    return sustain_affinity(profile)


def midi_melody_profile_for_pattern(pattern: dict[str, Any]) -> dict[str, float] | None:
    """Expose the MIDI profile for callers that also need to score a library pick."""
    notes = track_notes(pattern, "melody_lead")
    bpm = float(pattern.get("bpm", 140) or 140)
    return _midi_melody_profile(notes, bpm)


# --- Note-shape classification (2026-07) -------------------------------------
# Pure note-grid geometry (time_step/length/pitch in BEATS, tempo-independent —
# "1/16 grid" and "1-2 bars" are metric descriptions, not durations) that
# decides which SAMPLE CATEGORY (see _sample_shape_category) is even allowed
# to compete for a MIDI melody, before any audio-fit/vibe scoring runs. See
# module docstring, "Note-shape hard gate".

CHORDS_MIN_POLYPHONY = 0.25  # fraction of the track's span with >=3 notes ringing
CHORDS_MIN_MEDIAN_LEN_BEATS = 3.0  # notes held close to/through a bar

LEAD_MAX_POLYPHONY = 0.08  # strictly monophonic (no meaningful stacking)
LEAD_MAX_MEDIAN_LEN_BEATS = 0.6  # 1/16-1/8 grid: very short notes
LEAD_MIN_BIG_JUMP_FRACTION = 0.30  # frequent >=7-semitone jumps ("comb" pattern)

VOCAL_MAX_POLYPHONY = 0.12  # monophonic
VOCAL_MIN_MEDIAN_LEN_BEATS = 0.65  # medium notes: longer than a fast arpeggio...
VOCAL_MAX_MEDIAN_LEN_BEATS = 3.0  # ...shorter than a held chord
VOCAL_MIN_REST_FRACTION = 0.15  # regular "breathing" gaps between phrases
VOCAL_MAX_BIG_JUMP_FRACTION = 0.30  # doesn't leap wildly across octaves

BIG_JUMP_SEMITONES = 7  # roughly a fifth or more between consecutive notes

MELODY_SHAPE_CHORDS = "chords"
MELODY_SHAPE_ELECTRONIC_LEAD = "electronic_lead"
MELODY_SHAPE_VOCAL_CHOP = "vocal_chop"
MELODY_SHAPE_UNKNOWN = "unknown"

# Two physical sample buckets, derived purely from the WAV's own measured
# waveform (see _sample_shape_category) — no filenames, no per-file lists.
SAMPLE_SHAPE_SUSTAINED_DENSE = "sustained_dense"
SAMPLE_SHAPE_SHORT_TRANSIENT = "short_transient"

# Musical mutation modes for multi-export (2026-07): one "perfect" pick is a
# myth — bake emits three FLPs (Track_V1/V2/V3) each with a different sample
# strategy. See pick_melody_sample(mode=...).
MUTATION_SAFE = "safe"  # V1: current strict shape+audio-fit math
MUTATION_CONTRAST = "contrast"  # V2: opposite texture (short pluck vs dense pad)
MUTATION_CHAOS = "chaos"  # V3: seeded random from the full bank, root-locked
MUTATION_MODES = (MUTATION_SAFE, MUTATION_CONTRAST, MUTATION_CHAOS)

# Sample-shape -> which MIDI shapes are allowed to pick from it (see module
# docstring, "Sample-side classification is likewise pure signal"). A
# sustained/dense file can ring under a held chord OR a fast atmospheric
# arpeggio; only "chords" needs it exclusively (a one-shot can't hold a bar)
# and only "vocal_chop" needs a real stop-between-phrases hit exclusively.
_SHAPE_ALLOWED_SAMPLE_CATEGORIES: dict[str, set[str]] = {
    MELODY_SHAPE_CHORDS: {SAMPLE_SHAPE_SUSTAINED_DENSE},
    MELODY_SHAPE_ELECTRONIC_LEAD: {SAMPLE_SHAPE_SUSTAINED_DENSE, SAMPLE_SHAPE_SHORT_TRANSIENT},
    MELODY_SHAPE_VOCAL_CHOP: {SAMPLE_SHAPE_SHORT_TRANSIENT},
    MELODY_SHAPE_UNKNOWN: {SAMPLE_SHAPE_SUSTAINED_DENSE, SAMPLE_SHAPE_SHORT_TRANSIENT},
}


def _note_intervals_beats(notes: list[dict[str, Any]]) -> list[tuple[float, float, int]]:
    """(start_beat, end_beat, midi_pitch) per note, sorted by onset — the raw
    geometry classify_melody_shape reasons about. Beats, not seconds: grid
    density/bar-length are tempo-independent metric properties."""
    out: list[tuple[float, float, int]] = []
    for entry in notes:
        try:
            start = float(entry.get("time_step", 0.0))
            length = float(entry.get("length", 0.0))
        except (TypeError, ValueError):
            continue
        if length <= 0:
            continue
        try:
            pitch = parse_note_name(str(entry.get("note", "C4")))
        except (ValueError, IndexError):
            continue
        out.append((start, start + length, pitch))
    out.sort(key=lambda item: item[0])
    return out


def _polyphony_fraction(intervals: list[tuple[float, float, int]], threshold: int = 3) -> float:
    """Fraction of the notes' total time span where >= ``threshold`` notes
    overlap simultaneously — a sweep-line over note-on/note-off events."""
    if not intervals:
        return 0.0
    events: list[tuple[float, int]] = []
    for start, end, _pitch in intervals:
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda item: item[0])
    span = max(1e-6, max(end for _s, end, _p in intervals) - intervals[0][0])
    active = 0
    last_t = events[0][0]
    high_poly_time = 0.0
    for t, delta in events:
        if t > last_t and active >= threshold:
            high_poly_time += t - last_t
        active += delta
        last_t = t
    return _clamp01(high_poly_time / span)


def _rest_fraction(intervals: list[tuple[float, float, int]]) -> float:
    """Fraction of the (monophonic) span that is silence between notes —
    the "breathing room" a vocal phrase leaves and a dense arpeggio doesn't."""
    if len(intervals) < 2:
        return 0.0
    span = max(1e-6, max(end for _s, end, _p in intervals) - intervals[0][0])
    gap_total = 0.0
    prev_end = intervals[0][1]
    for start, end, _pitch in intervals[1:]:
        if start > prev_end:
            gap_total += start - prev_end
        prev_end = max(prev_end, end)
    return _clamp01(gap_total / span)


def _octave_jump_stats(intervals: list[tuple[float, float, int]]) -> tuple[float, float]:
    """(mean absolute semitone jump, fraction of jumps >= BIG_JUMP_SEMITONES)
    between consecutive note onsets — the "comb"/arpeggio signature."""
    if len(intervals) < 2:
        return 0.0, 0.0
    deltas = [abs(intervals[i + 1][2] - intervals[i][2]) for i in range(len(intervals) - 1)]
    if not deltas:
        return 0.0, 0.0
    mean_jump = sum(deltas) / len(deltas)
    big_fraction = sum(1 for d in deltas if d >= BIG_JUMP_SEMITONES) / len(deltas)
    return mean_jump, big_fraction


def classify_melody_shape(notes: list[dict[str, Any]]) -> str:
    """Pure note-geometry classification into one of the three archetypes the
    product spec calls out, or MELODY_SHAPE_UNKNOWN when the track doesn't
    cleanly match any of them (see module docstring for the full rationale):

    - chords: notes stack 3-4 deep vertically AND run long (~1+ bar) ->
      Pad / choir / piano territory, short one-shots and vocal are banned.
    - electronic_lead: strictly monophonic, very short (1/16-1/8 grid) notes
      that jump octaves frequently ("comb" pattern) -> dense synth/pluck only,
      vocal is strictly banned (a human voice can't move like this).
    - vocal_chop: monophonic, medium-length notes with frequent regular gaps
      (phrase "breathing") and no wild octave leaping -> the one shape allowed
      to pick a vocal sample.
    """
    intervals = _note_intervals_beats(notes)
    if len(intervals) < 2:
        return MELODY_SHAPE_UNKNOWN

    polyphony = _polyphony_fraction(intervals, threshold=3)
    median_len = statistics.median(end - start for start, end, _pitch in intervals)
    _mean_jump, big_jump_fraction = _octave_jump_stats(intervals)
    rest_fraction = _rest_fraction(intervals)

    if polyphony >= CHORDS_MIN_POLYPHONY and median_len >= CHORDS_MIN_MEDIAN_LEN_BEATS:
        return MELODY_SHAPE_CHORDS

    if (
        polyphony <= LEAD_MAX_POLYPHONY
        and median_len <= LEAD_MAX_MEDIAN_LEN_BEATS
        and big_jump_fraction >= LEAD_MIN_BIG_JUMP_FRACTION
    ):
        return MELODY_SHAPE_ELECTRONIC_LEAD

    if (
        polyphony <= VOCAL_MAX_POLYPHONY
        and VOCAL_MIN_MEDIAN_LEN_BEATS <= median_len <= VOCAL_MAX_MEDIAN_LEN_BEATS
        and rest_fraction >= VOCAL_MIN_REST_FRACTION
        and big_jump_fraction <= VOCAL_MAX_BIG_JUMP_FRACTION
    ):
        return MELODY_SHAPE_VOCAL_CHOP

    return MELODY_SHAPE_UNKNOWN


def midi_melody_shape_for_pattern(pattern: dict[str, Any]) -> str:
    """Public helper: note-shape classification of pattern['tracks']['melody_lead']."""
    notes = track_notes(pattern, "melody_lead")
    return classify_melody_shape(notes)


# A file is SAMPLE_SHAPE_SUSTAINED_DENSE if it's long enough on its own
# (whatever its tail does), OR if it's a bit shorter but its tail is still
# genuinely loud (a dense pad that just happens to run 3s, not 4s). Purely a
# waveform-duration/tail-loudness read — see analyze_sustain's tail_rms_ratio
# (RMS of the file's final window vs its overall RMS): a bell/pluck spikes
# once and decays away (low ratio), a pad/texture stays present (high ratio).
SUSTAINED_DENSE_MIN_DURATION_S = 4.0
SUSTAINED_DENSE_SOFT_MIN_DURATION_S = 2.5
SUSTAINED_DENSE_SOFT_MIN_TAIL_RATIO = 0.5


def _sample_shape_category(path: Path) -> str:
    """Which of the two physical sample buckets ``path`` belongs to — purely
    from its own measured waveform (analyze_sustain_cached's duration_s /
    tail_rms_ratio), never its filename. An unreadable file defaults to
    SAMPLE_SHAPE_SHORT_TRANSIENT (the more restrictive bucket — it will only
    ever compete for short/monophonic MIDI shapes, never win a chord bed it
    can't actually sustain)."""
    sustain_feats = analyze_sustain_cached(path)
    if not sustain_feats:
        return SAMPLE_SHAPE_SHORT_TRANSIENT

    duration = float(sustain_feats.get("duration_s") or 0.0)
    tail_ratio = float(sustain_feats.get("tail_rms_ratio") or 0.0)
    is_sustained_dense = duration >= SUSTAINED_DENSE_MIN_DURATION_S or (
        duration >= SUSTAINED_DENSE_SOFT_MIN_DURATION_S and tail_ratio >= SUSTAINED_DENSE_SOFT_MIN_TAIL_RATIO
    )
    return SAMPLE_SHAPE_SUSTAINED_DENSE if is_sustained_dense else SAMPLE_SHAPE_SHORT_TRANSIENT


def _filter_candidates_by_shape(candidates: list[Path], melody_shape: str) -> list[Path]:
    """Hard-gate ``candidates`` to the sample categories ``melody_shape``
    allows (see _SHAPE_ALLOWED_SAMPLE_CATEGORIES). Falls back to the full,
    unfiltered pool if the gate would leave nothing to pick from — a
    mismatched sample beats no sample at all."""
    allowed = _SHAPE_ALLOWED_SAMPLE_CATEGORIES.get(
        melody_shape, {SAMPLE_SHAPE_SUSTAINED_DENSE, SAMPLE_SHAPE_SHORT_TRANSIENT}
    )
    filtered = [path for path in candidates if _sample_shape_category(path) in allowed]
    return filtered if filtered else candidates


def _sample_key_pc(path: Path) -> int | None:
    """Pitch class (0=C..11=B) parsed from a bank filename's key tag, if any."""
    try:
        from midi_corpus import key_from_name
    except Exception:  # noqa: BLE001 — key tagging is a bonus, never block a pick
        return None
    hint = key_from_name(path.stem)
    return hint[0] if hint else None


def _root_correction_semitones(key_pc: int) -> int:
    """Minimal signed shift so ``key_pc`` lands on C (0), e.g. B(11) -> +1,
    not -11 — smaller shifts mean less resample artifact on the correction."""
    return ((-key_pc + 6) % 12) - 6


def _pitch_shift_wav(path: Path, dest: Path, semitones: int) -> bool:
    """Resample-based semitone shift — same technique as
    sample_chop_engine._pitch_shift, duplicated here to avoid a cross-module
    import cycle (chop engine already imports from pattern land, not audio)."""
    try:
        from pydub import AudioSegment
        from pydub_windows_compat import audio_segment_from_file

        seg = audio_segment_from_file(path)
        factor = 2.0 ** (semitones / 12.0)
        shifted = seg._spawn(seg.raw_data, overrides={"frame_rate": int(seg.frame_rate * factor)})
        shifted = shifted.set_frame_rate(seg.frame_rate)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shifted.export(dest, format="wav")
        return True
    except Exception as exc:  # noqa: BLE001 — correction is best-effort, never block a pick
        logger.info("Root-pitch correction skipped for %s (%s)", path.name, exc)
        return False


def resolve_playback_path(path: Path) -> Path:
    """Bank candidate -> the path that should actually be loaded/played.

    Unchanged unless the filename declares a non-C key (see module docstring,
    "Root-pitch correction") — then a cached, pitch-corrected copy is
    returned so the Sampler's fixed C5-root assumption lands on the right
    pitch. Cheap no-op for the common case (no key tag / already rooted C)."""
    key_pc = _sample_key_pc(path)
    if key_pc is None or key_pc == 0:
        return path
    shift = _root_correction_semitones(key_pc)
    if shift == 0:
        return path
    try:
        st = path.stat()
    except OSError:
        return path
    dest = PITCH_CACHE_DIR / f"{path.stem}__root_c{shift:+d}_{int(st.st_mtime)}.wav"
    if dest.is_file():
        return dest
    if _pitch_shift_wav(path, dest, shift):
        logger.info("Root-pitch correction: %s (key pc=%d) -> shift %+d st", path.name, key_pc, shift)
        return dest
    return path


def _sample_is_sustained(sustain_feats: dict[str, float] | None) -> bool:
    if not sustain_feats:
        return False
    duration = sustain_feats.get("duration_s", 0.0)
    tail = sustain_feats.get("tail_rms_ratio", 0.0)
    return duration >= SUSTAINED_MIN_DURATION_S and tail >= SUSTAINED_MIN_TAIL_RATIO


def score_melody_candidate(
    path: Path,
    profile: dict[str, float],
    *,
    prompt: str = "",
    style: str = "",
) -> float:
    """Audio-fit score of ``path`` against a MIDI note-length/register profile.

    Public so starter_kit can score a library pick with the same rubric as the
    bundled bank (compete when sustain affinity clears the soft floor).
    ``prompt``/``style`` add the same vibe/descriptor scoring bank candidates
    get — a fair fight, not audio-fit-only vs audio-fit-plus-vibe.
    """
    return _score_candidate(path, profile, prompt=prompt, style=style)


def _score_candidate(
    path: Path,
    profile: dict[str, float],
    *,
    prompt: str = "",
    style: str = "",
) -> float:
    sustain_feats = analyze_sustain_cached(path)
    if sustain_feats is None:
        return -1000.0  # unreadable — never pick a file we can't measure

    duration = sustain_feats.get("duration_s", 0.0)
    is_sustained = _sample_is_sustained(sustain_feats)
    # Remap raw affinity so soft-floor clearances (~0.15–0.30) already lean
    # sustain for scoring; raw 0 stays full pluck, raw ≥0.35 saturates to 1.
    raw_affinity = sustain_affinity(profile)
    affinity = _clamp01(raw_affinity / 0.35) if raw_affinity > 0.0 else 0.0
    pluck_lean = 1.0 - affinity

    score = 0.0
    # Continuous blend of the old binary sustain/pluck scoring poles.
    score += affinity * (20.0 if is_sustained else -12.0)
    score += pluck_lean * (15.0 if not is_sustained else -6.0)
    coverage = min(1.0, duration / max(0.25, profile["max_length_s"]))
    score += affinity * 15.0 * coverage
    if duration <= 4.0:
        score += pluck_lean * 5.0  # short one-shot repitches best per note

    # Mild, second-order register nudge: don't let a very bright one-shot
    # (bell) stand in for a low melody or vice versa. Deliberately loose —
    # audio_features.pitch is tuned for 808/bass detection (40-500Hz band),
    # not melodic register, so brightness (centroid) is used as a rough
    # proxy instead of exact Hz/semitone distance.
    one_shot_feats = analyze(path)
    if one_shot_feats and one_shot_feats.get("centroid"):
        centroid = float(one_shot_feats["centroid"])
        median_pitch = profile.get("median_pitch", 60.0)
        register_lean = max(0.0, min(1.0, (median_pitch - 48.0) / 36.0))
        expected_centroid = 800.0 + register_lean * 3200.0
        distance = abs(centroid - expected_centroid)
        score += max(-6.0, 6.0 - distance / 800.0)

    # Filename: a light hint only (product decision) — never decisive next
    # to the audio-driven terms above. Soft-weighted by affinity.
    name = path.stem.lower()
    if any(tag in name for tag in ("pad", "vocal", "cxdy")):
        score += affinity * 2.0
    if any(tag in name for tag in ("bell", "pluck", "key")):
        score += pluck_lean * 2.0

    # Vibe/genre fit: same descriptor-direction scoring pick_best_for_track
    # and kit_variety already use for prompt-driven timbre matching (e.g.
    # "dark" -> low centroid). Without this, sustain alone routes every
    # sustain-lean MIDI of every genre onto the same one "most sustained"
    # file in the pool, regardless of whether it actually fits the beat.
    if prompt or style:
        audio_target = target_from_prompt(prompt, style, track="melody_lead")
        if audio_target:
            score += feature_match_score(analyze_cached(path), audio_target)

    return score


def _rank_candidates(
    candidates: list[Path],
    profile: dict[str, float],
    *,
    prompt: str = "",
    style: str = "",
) -> list[tuple[Path, float]]:
    """Score every candidate and return (path, score) sorted best→worst."""
    ranked: list[tuple[Path, float]] = []
    for path in candidates:
        ranked.append((path, _score_candidate(path, profile, prompt=prompt, style=style)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _contrast_pool(candidates: list[Path], melody_shape: str) -> list[Path]:
    """Opposite-texture pool for MUTATION_CONTRAST.

    Fast / chord-leaning MIDI normally wants dense sustained material — contrast
    forces the short/sharp bucket instead (artificial tail comes from heavy
    reverb/delay in the V2 mix macros). Vocal-chop MIDI wants short hits —
    contrast flips to the dense/sustained bucket. Unknown falls back to
    whichever bucket the safe ranking did NOT put first.
    """
    by_shape: dict[str, list[Path]] = {
        SAMPLE_SHAPE_SUSTAINED_DENSE: [],
        SAMPLE_SHAPE_SHORT_TRANSIENT: [],
    }
    for path in candidates:
        by_shape[_sample_shape_category(path)].append(path)

    if melody_shape in (MELODY_SHAPE_CHORDS, MELODY_SHAPE_ELECTRONIC_LEAD):
        opposite = SAMPLE_SHAPE_SHORT_TRANSIENT
    elif melody_shape == MELODY_SHAPE_VOCAL_CHOP:
        opposite = SAMPLE_SHAPE_SUSTAINED_DENSE
    else:
        # Prefer short if both exist (contrast = sharper than "safe"), else dense.
        opposite = (
            SAMPLE_SHAPE_SHORT_TRANSIENT
            if by_shape[SAMPLE_SHAPE_SHORT_TRANSIENT]
            else SAMPLE_SHAPE_SUSTAINED_DENSE
        )
    pool = by_shape[opposite]
    return pool if pool else candidates


def pick_melody_sample(
    pattern: dict[str, Any],
    *,
    prompt: str = "",
    style: str = "",
    mode: str = MUTATION_SAFE,
    seed: int | None = None,
    exclude: set[str] | None = None,
) -> tuple[Path | None, float]:
    """Best-matching bundled melody sample for pattern["tracks"]["melody_lead"].

    ``mode`` selects the musical-mutation strategy used by multi-export:
    - ``safe`` (V1): shape hard-gate + audio-fit/vibe argmax (legacy behaviour).
    - ``contrast`` (V2): opposite physical texture from the MIDI's natural lean
      (fast MIDI → ultra-short pluck; chop MIDI → dense pad). Mix macros pile
      ~50% reverb/delay on top so the short hit still has a tail.
    - ``chaos`` (V3): seeded random pick from the FULL bank (ignores shape
      gates — Opium/experimental), but always root-pitch-corrects so tonality
      still holds.

    ``exclude`` is a set of resolved path strings already used by sibling
    variants (so V2/V3 don't silently re-pick V1's file when alternatives
    exist). ``seed`` drives chaos / contrast tie-breaks.

    Returns (None, 0.0) if the bank is missing/empty or the melody has no
    notes to build a profile from. The returned path may be a cached,
    root-pitch-corrected copy (see ``resolve_playback_path``)."""
    bank_dir = melody_sample_bank_dir()
    if not bank_dir.is_dir():
        return None, 0.0
    candidates = sorted(
        p for p in bank_dir.iterdir() if p.is_file() and p.suffix.lower() == ".wav"
    )
    if not candidates:
        return None, 0.0

    notes = track_notes(pattern, "melody_lead")
    profile = midi_melody_profile_for_pattern(pattern)
    if profile is None:
        return None, 0.0

    melody_shape = classify_melody_shape(notes)
    prompt_text = prompt or str(pattern.get("user_prompt") or "")
    style_text = style or str(pattern.get("style") or "")
    mutation = mode if mode in MUTATION_MODES else MUTATION_SAFE
    excluded = exclude or set()

    def _not_excluded(path: Path) -> bool:
        try:
            return str(path.resolve()) not in excluded and str(path) not in excluded
        except OSError:
            return str(path) not in excluded

    if mutation == MUTATION_CHAOS:
        # Full bank, ignore shape gates — root correction still applied below.
        pool = [p for p in candidates if _not_excluded(p)] or candidates
        rng = random.Random(seed if seed is not None else 0)
        best_path = rng.choice(pool)
        best_score = _score_candidate(best_path, profile, prompt=prompt_text, style=style_text)
    elif mutation == MUTATION_CONTRAST:
        pool = [p for p in _contrast_pool(candidates, melody_shape) if _not_excluded(p)]
        if not pool:
            pool = _contrast_pool(candidates, melody_shape)
        ranked = _rank_candidates(pool, profile, prompt=prompt_text, style=style_text)
        # Prefer the *worst* safe-fit among the opposite pool (true contrast),
        # but never an unreadable file (score ≈ -1000). Fall back to best of
        # pool if everything is unreadable.
        readable = [(p, s) for p, s in ranked if s > -500.0]
        if readable:
            best_path, best_score = readable[-1]
        else:
            best_path, best_score = ranked[0]
    else:
        # MUTATION_SAFE — shape-gated argmax (legacy).
        shape_candidates = _filter_candidates_by_shape(candidates, melody_shape)
        pool = [p for p in shape_candidates if _not_excluded(p)] or shape_candidates
        ranked = _rank_candidates(pool, profile, prompt=prompt_text, style=style_text)
        best_path, best_score = ranked[0]

    if best_path is None:
        return None, 0.0
    affinity = sustain_affinity(profile)
    playback_path = resolve_playback_path(best_path)
    logger.info(
        "Melody sample bank [%s]: %s (shape=%s, score %.1f, affinity=%.2f "
        "long_fraction=%.2f max_len=%.2fs)%s",
        mutation,
        best_path.name,
        melody_shape,
        best_score,
        affinity,
        profile["long_fraction"],
        profile["max_length_s"],
        "" if playback_path == best_path else f" -> root-corrected {playback_path.name}",
    )
    return playback_path, best_score


def apply_melody_mutation(
    pattern: dict[str, Any],
    *,
    mode: str = MUTATION_SAFE,
    seed: int | None = None,
    exclude: set[str] | None = None,
    prompt: str = "",
    style: str = "",
) -> Path | None:
    """Re-pick ``melody_lead`` on ``pattern`` under ``mode`` and rewrite
    ``plg_sound_paths`` / ``plg_sample_picks`` in place. Returns the playback
    path (or None if the bank had nothing). Used by multi-export bake so V1/V2/V3
    share drums/arrangement but diverge on the melodic one-shot."""
    prompt_text = prompt or str(pattern.get("user_prompt") or "")
    style_text = style or str(pattern.get("style") or pattern.get("plg_genre") or "")
    path, score = pick_melody_sample(
        pattern,
        prompt=prompt_text,
        style=style_text,
        mode=mode,
        seed=seed,
        exclude=exclude,
    )
    if path is None:
        return None
    sound_paths = pattern.setdefault("plg_sound_paths", {})
    if not isinstance(sound_paths, dict):
        sound_paths = {}
        pattern["plg_sound_paths"] = sound_paths
    sound_paths["melody_lead"] = str(path)
    picks = pattern.setdefault("plg_sample_picks", {})
    if isinstance(picks, dict):
        picks["melody_lead"] = path.name
    pattern["plg_melody_mutation"] = {
        "mode": mode,
        "sample": path.name,
        "score": float(score),
        "seed": seed,
    }
    return path
