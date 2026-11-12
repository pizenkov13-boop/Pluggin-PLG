"""PLG's own melody one-shots: which one a MIDI melody gets, and how it is prepared (2026-11).

The sounds are ``assets/melody_oneshots/<type>/<type>_<N>_<dark|rage|soft>.wav``, played once on MIDI 60 (FL's
"C5", the Sampler's default root note) and made in Serum 2 from its FACTORY presets (``sources.txt`` there). The
types are piano, pluck, key, pad, strings, lead. ``_rejected/`` (a file Pavel dropped by ear) and anything that is
not a ``<type>/*.wav`` is never a candidate.

ON by default: ``sound_policy.melody_oneshots_enabled`` (``PLG_MELODY_ONESHOTS``, ``off`` switches it off). ``starter_kit`` is
the only caller. With the flag on no automatic pick from the user's folder sits on the melody channel; only a file the user
pinned BY HAND on the melody cell (``pinned_kit``, explicit) does, and it wins over the one-shot, which is asked only when
the channel has no such pin.

Selection is two steps and both are repeatable from the beat's variety seed:

1. ``choose_group``: the melody's own notes (``melody_features``: mean note length, share of time with 3+ notes
   sounding, median pitch, notes per bar) pick a GROUP of types. ESTIMATE, not measured on a corpus: every
   threshold below is a first guess to be checked by ear on Pavel's MIDI (``tools/show_flp_sounds.py`` prints the
   features and the group). `the project issue log (not included)` records it as such.
2. ``choose``: inside the group (or the nearest group that has files) the seed picks the type, then the file.

Pitch: a file whose pitch is an octave under MIDI 60 keeps its audio; ``root_offset`` (data in ``root_offsets.json``) tells
``flp_writer`` to write the channel's root note as 60 + offset, so MIDI 60 still sounds at the pitch the note says.

``prepare_samples`` is what makes a file safe to put in a project: DC removed (mean, then a 20 Hz high-pass),
fade-in 5 ms, fade-out 50 ms, peak -3 dB. The source in ``assets`` is never changed; the copy is new.
"""

from __future__ import annotations

import json
import logging
import random
import wave
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("plg.melody_oneshots")

TYPES = ("piano", "pluck", "key", "pad", "strings", "lead")
REJECTED_DIRNAME = "_rejected"  # a file moved here by hand is not a candidate and not in the exe
OFFSETS_FILE = "root_offsets.json"  # beside the wavs, made by ``tools/check_oneshots.py --write-offsets``
ROOT_MIDI = 60  # FL's "C5": the Sampler's default root note, where every file was played

# --- groups (an ESTIMATE, see the module docstring) --------------------------------------------------------
GROUP_SUSTAIN = "sustain"  # long notes, stacked, few per bar -> pad / strings
GROUP_PLUCKED = "lead_pluck"  # one voice, busy, high -> lead / pluck
GROUP_KEYS = "keys"  # chords of medium length -> piano / key
GROUP_DEFAULT = "default"  # nothing above fits -> piano
GROUP_TYPES: dict[str, tuple[str, ...]] = {
    GROUP_SUSTAIN: ("pad", "strings"),
    GROUP_PLUCKED: ("lead", "pluck"),
    GROUP_KEYS: ("piano", "key"),
    GROUP_DEFAULT: ("piano",),
}
# When the chosen group has no file, the next closest by the scheme: sustained and plucked melodies are the two
# ends, the keys sit between them.
GROUP_FALLBACK: dict[str, tuple[str, ...]] = {
    GROUP_SUSTAIN: (GROUP_KEYS, GROUP_PLUCKED),
    GROUP_PLUCKED: (GROUP_KEYS, GROUP_SUSTAIN),
    GROUP_KEYS: (GROUP_SUSTAIN, GROUP_PLUCKED),
    GROUP_DEFAULT: (GROUP_KEYS, GROUP_SUSTAIN, GROUP_PLUCKED),
}

BEATS_PER_BAR = 4.0  # the engine writes 4/4

# Thresholds. All of them are first guesses (ESTIMATE): nothing here was fitted to a corpus.
SUSTAIN_MIN_MEAN_LEN_BEATS = 2.0  # "long notes": held half a bar or more on average
SUSTAIN_MIN_POLYPHONY = 0.15  # share of the melody's span with 3+ notes sounding; below the 0.25 chord floor in melody_sample_bank
SUSTAIN_MAX_NOTES_PER_BAR = 4.0  # "few notes per bar"
PLUCKED_MAX_POLYPHONY = 0.08  # "almost one voice" (the same figure melody_sample_bank uses for a lead)
PLUCKED_MIN_NOTES_PER_BAR = 6.0  # "fast": busier than a plain run of eighths with rests
PLUCKED_MIN_MEDIAN_PITCH = 67  # "high": G in FL naming (C5 = 60), a fifth above the sample root
KEYS_MIN_MEAN_LEN_BEATS = 0.75  # "chords of medium length": a dotted eighth up to ...
KEYS_MAX_MEAN_LEN_BEATS = 2.0  # ... just under the sustain floor
KEYS_MIN_POLYPHONY = 0.10  # chords, not a single line

# --- preparing a file --------------------------------------------------------------------------------------
HIGHPASS_HZ = 20.0
FADE_IN_S = 0.005
FADE_OUT_S = 0.050
TARGET_PEAK_DB = -3.0
PREP_VERSION = 1  # part of the prepared file's name: change the recipe, bump it, old copies are not reused


# --- the library ---------------------------------------------------------------------------------------------
def library(root: Path | None = None) -> dict[str, list[Path]]:
    """``{type: [wav, ...]}`` sorted by name; only ``<root>/<type>/*.wav``. A type with no file is absent
    (it is never chosen), and ``_rejected/`` is never looked into."""
    if root is None:
        from plg_paths import melody_oneshots_dir

        root = melody_oneshots_dir()
    found: dict[str, list[Path]] = {}
    for kind in TYPES:
        folder = root / kind
        if not folder.is_dir():
            continue
        files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".wav" and not p.name.startswith("_"))
        if files:
            found[kind] = files
    return found


def root_offset(path: Path | str) -> int:
    """The channel root offset, in semitones, of one library file: 0 for a file whose pitch is on MIDI 60, -12 for one
    an octave below it. Read from ``root_offsets.json`` beside the type folders (data, not code); 0 when the file or
    the key is missing or unreadable. ``flp_writer`` writes the channel root as ``ROOT_MIDI + offset``; the wav is not
    changed."""
    path = Path(path)
    try:
        offsets = json.loads((path.parent.parent / OFFSETS_FILE).read_text(encoding="utf-8")).get("offsets", {})
        return int(offsets.get(f"{path.parent.name}/{path.name}", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


# --- what the melody is like ---------------------------------------------------------------------------------
def melody_features(pattern: dict[str, Any]) -> dict[str, float] | None:
    """Mean note length (beats), share of the span with 3+ notes sounding, median pitch (FL naming, C5 = 60) and
    notes per bar, read from the melody's own notes (the lead and, when ``midi_roles`` lanes are on, the chord and
    counter lanes: they are the same melody). None when there are no notes."""
    from melody_sample_bank import _note_intervals_beats, _polyphony_fraction
    from pattern_utils import track_notes

    notes: list[dict[str, Any]] = []
    for lane in ("melody_lead", "counter_melody", "chords"):
        notes.extend(track_notes(pattern, lane))
    intervals = _note_intervals_beats(notes)
    if not intervals:
        return None
    lengths = [end - start for start, end, _pitch in intervals]
    pitches = sorted(pitch for _s, _e, pitch in intervals)
    mid = len(pitches) // 2
    median_pitch = float(pitches[mid]) if len(pitches) % 2 else (pitches[mid - 1] + pitches[mid]) / 2.0
    span = max(end for _s, end, _p in intervals) - intervals[0][0]
    bars = max(1.0, span / BEATS_PER_BAR)
    return {
        "notes": float(len(intervals)),
        "mean_len_beats": sum(lengths) / len(lengths),
        "polyphony": _polyphony_fraction(intervals, threshold=3),
        "median_pitch": median_pitch,
        "notes_per_bar": len(intervals) / bars,
    }


def choose_group(features: dict[str, float]) -> str:
    """The group of types a melody with these features gets (ESTIMATE, thresholds above)."""
    mean_len = features["mean_len_beats"]
    poly = features["polyphony"]
    density = features["notes_per_bar"]
    if mean_len >= SUSTAIN_MIN_MEAN_LEN_BEATS and poly >= SUSTAIN_MIN_POLYPHONY and density <= SUSTAIN_MAX_NOTES_PER_BAR:
        return GROUP_SUSTAIN
    if poly <= PLUCKED_MAX_POLYPHONY and density >= PLUCKED_MIN_NOTES_PER_BAR and features["median_pitch"] >= PLUCKED_MIN_MEDIAN_PITCH:
        return GROUP_PLUCKED
    if KEYS_MIN_MEAN_LEN_BEATS <= mean_len < KEYS_MAX_MEAN_LEN_BEATS and poly >= KEYS_MIN_POLYPHONY:
        return GROUP_KEYS
    return GROUP_DEFAULT


def seed_of(pattern: dict[str, Any]) -> int:
    """The beat's variety seed (``plg_variety``); 0 when there is none, so the same melody still picks the same file."""
    variety = pattern.get("plg_variety")
    seed = variety.get("seed") if isinstance(variety, dict) else None
    return int(seed) if isinstance(seed, int) and not isinstance(seed, bool) else 0


def choose(pattern: dict[str, Any], *, root: Path | None = None, seed: int | None = None) -> dict[str, Any] | None:
    """``{"seed", "group", "group_used", "type", "file", "path", "root_offset", "features"}`` or None (no melody notes, or no file
    in the library). The same notes, seed and library always give the same answer."""
    features = melody_features(pattern)
    if features is None:
        return None
    files = library(root)
    if not files:
        return None
    seed = seed_of(pattern) if seed is None else int(seed)
    group = choose_group(features)
    used = None
    for candidate in (group, *GROUP_FALLBACK[group]):
        kinds = [kind for kind in GROUP_TYPES[candidate] if kind in files]
        if kinds:
            used = candidate
            break
    if used is None:
        return None
    rng = random.Random(f"melody_oneshot:{seed}")  # a str seed is hashed by value: the same on every run
    kind = kinds[rng.randrange(len(kinds))]
    path = files[kind][rng.randrange(len(files[kind]))]
    return {
        "seed": seed,
        "group": group,
        "group_used": used,
        "type": kind,
        "file": path.name,
        "path": str(path),
        "root_offset": root_offset(path),
        "features": {key: round(value, 3) for key, value in features.items()},
    }


# --- preparing the audio -------------------------------------------------------------------------------------
def _highpass_zero_phase(samples: np.ndarray, rate: int, cutoff_hz: float) -> np.ndarray:
    """A 2nd-order Butterworth high-pass magnitude applied in the frequency domain (zero phase), per channel.
    numpy only: ``scipy`` is not in the exe."""
    frames = samples.shape[0]
    size = 1 << int(np.ceil(np.log2(frames + 2 * rate)))  # zero padding: the filter's own tail must not wrap round
    spectrum = np.fft.rfft(samples, n=size, axis=0)
    ratio4 = (np.fft.rfftfreq(size, 1.0 / rate) / cutoff_hz) ** 4
    gain = np.sqrt(ratio4 / (1.0 + ratio4))
    return np.fft.irfft(spectrum * gain[:, None], n=size, axis=0)[:frames]


def prepare_samples(samples: np.ndarray, rate: int) -> np.ndarray:
    """``(frames, channels)`` float -> the same shape: DC removed (mean, then the 20 Hz high-pass), fade-in 5 ms,
    fade-out 50 ms, peak -3 dB. A silent input is returned unchanged."""
    out = np.asarray(samples, dtype=np.float64)
    if out.ndim == 1:
        out = out[:, None]
    if out.shape[0] == 0 or not np.any(out):
        return out
    out = out - out.mean(axis=0, keepdims=True)
    out = _highpass_zero_phase(out, rate, HIGHPASS_HZ)
    fade_in = min(out.shape[0], max(1, int(round(rate * FADE_IN_S))))
    fade_out = min(out.shape[0], max(1, int(round(rate * FADE_OUT_S))))
    out[:fade_in] *= np.linspace(0.0, 1.0, fade_in, endpoint=False)[:, None]
    out[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)[:, None]
    peak = float(np.abs(out).max())
    if peak > 0.0:
        out *= 10.0 ** (TARGET_PEAK_DB / 20.0) / peak
    return out


def write_wav24(path: Path, samples: np.ndarray, rate: int) -> None:
    """A 24-bit PCM wav, the format every other sound of the project is published in."""
    data = np.clip(np.round(samples * 8388607.0), -8388608, 8388607).astype("<i4")
    raw = np.frombuffer(data.tobytes(), dtype=np.uint8).reshape(-1, 4)[:, :3].tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with wave.open(str(partial), "wb") as out:
        out.setnchannels(samples.shape[1])
        out.setsampwidth(3)
        out.setframerate(rate)
        out.writeframes(raw)
    partial.replace(path)


def prepared_copy(source: Path, cache_dir: Path) -> Path:
    """The prepared copy of ``source`` in ``cache_dir`` (made once; the source is never touched)."""
    dest = cache_dir / f"{source.stem}__prep{PREP_VERSION}.wav"
    if dest.is_file() and dest.stat().st_mtime >= source.stat().st_mtime:
        return dest
    from wav_decode import read_wav

    audio = read_wav(source)
    samples = audio.samples if audio.samples.ndim > 1 else audio.samples[:, None]
    write_wav24(dest, prepare_samples(samples, audio.rate), audio.rate)
    return dest


def pick_for_pattern(pattern: dict[str, Any], cache_dir: Path, *, root: Path | None = None) -> Path | None:
    """Choose, prepare and RECORD (``pattern["plg_melody_oneshot"]``) the melody one-shot of this beat; the prepared
    copy's path, or None. Fails open: a broken file or folder leaves the melody channel without a sound, as before."""
    try:
        choice = choose(pattern, root=root)
        if choice is None:
            return None
        prepared = prepared_copy(Path(choice["path"]), cache_dir)
    except Exception as exc:  # noqa: BLE001 - a bonus sound must never fail a beat
        logger.warning("Melody one-shot skipped (%s)", exc)
        return None
    choice["prepared"] = str(prepared)
    pattern["plg_melody_oneshot"] = choice
    logger.info(
        "Melody one-shot: %s (group %s, seed %s, root offset %+d)",
        choice["file"], choice["group_used"], choice["seed"], choice["root_offset"],
    )
    return prepared


__all__ = [
    "TYPES",
    "choose",
    "choose_group",
    "library",
    "melody_features",
    "pick_for_pattern",
    "prepare_samples",
    "prepared_copy",
    "root_offset",
    "seed_of",
    "write_wav24",
]
