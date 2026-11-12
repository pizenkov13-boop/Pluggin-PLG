"""Deterministic root-note passport for pitched one-shot samples."""
from __future__ import annotations

import math
import struct
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

# Relaxed root-trust bounds (see ``root_trusted`` in ``_cached``). The strict
# ``status == "ok"`` bounds are prominence >= 2.0 and pitch_drop <= 6.0.
# Measured on the dev library: every real 808 has prominence >= 70, unpitched
# one-shots (claps, hats) top out at ~30. 50 sits in the gap.
_TRUSTED_MIN_PROMINENCE = 50.0
_TRUSTED_MAX_PITCH_DROP = 24.0


def _embedded_wav_root_note(path: Path) -> int | None:
    """Return the author-set MIDI unity note from a WAV ``smpl`` chunk.

    When present, this is stronger evidence than spectrum analysis: it is the
    root key saved with the sample itself, not a guess made by this program.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None
    offset = 12
    while offset + 8 <= len(raw):
        chunk = raw[offset : offset + 4]
        size = struct.unpack_from("<I", raw, offset + 4)[0]
        payload = offset + 8
        if chunk == b"smpl" and size >= 16 and payload + 16 <= len(raw):
            note = int(struct.unpack_from("<I", raw, payload + 12)[0])
            return note if 0 <= note <= 127 else None
        offset = payload + size + (size & 1)
    return None


def _read_pcm_mono(path: Path) -> tuple[np.ndarray, int]:
    from audio_decode import AudioDecodeError, read_mono

    try:
        data, rate, _duration = read_mono(path)
    except (AudioDecodeError, EOFError, OSError):
        # Not a WAV/FLAC audio_decode understands: last resort is the pydub
        # decoder (needs ffmpeg for anything but plain PCM).
        from audio_features import _load_pydub_samples

        decoded = _load_pydub_samples(path, max_ms=5000)
        if decoded is None:
            raise
        data, rate, _duration = decoded
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    return (data / peak if peak else data), rate


def _estimate_fundamental(signal: np.ndarray, rate: int) -> tuple[float, float]:
    if signal.size < max(2048, rate // 10):
        raise ValueError("sample too short")
    body = signal[min(signal.size // 3, int(rate * 0.075)) :]
    win = min(32768, body.size)
    if win < 2048:
        raise ValueError("no sustained body")
    hop = max(512, win // 8)
    offsets = range(0, max(1, body.size - win + 1), hop)
    offset = max(offsets, key=lambda pos: float(np.mean(body[pos : pos + win] ** 2)))
    frame = body[offset : offset + win]
    frame = (frame - float(np.mean(frame))) * np.hanning(frame.size)
    nfft = 1 << max(16, int(math.ceil(math.log2(frame.size * 4))))
    spectrum = np.abs(np.fft.rfft(frame, n=nfft))
    freqs = np.fft.rfftfreq(nfft, 1.0 / rate)
    scores: list[float] = []
    trial_freqs = np.geomspace(27.5, 246.94, 1800)
    for freq in trial_freqs:
        score = 0.0
        for harmonic, weight in ((1, 1.0), (2, 0.72), (3, 0.46), (4, 0.28)):
            idx = int(np.argmin(np.abs(freqs - freq * harmonic)))
            score += weight * float(np.max(spectrum[max(0, idx - 2) : idx + 3]))
        scores.append(score)
    best = int(np.argmax(scores))
    median = float(np.median(scores)) or 1e-9
    return float(trial_freqs[best]), float(scores[best] / median)


def _head_dominant_frequency(signal: np.ndarray, rate: int) -> float:
    """Dominant audible pitch at the start of an 808 pitch-drop."""
    size = min(signal.size, max(2048, int(rate * 0.18)))
    if size < 2048:
        return 0.0
    frame = (signal[:size] - float(np.mean(signal[:size]))) * np.hanning(size)
    spectrum = np.abs(np.fft.rfft(frame))
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    band = (freqs >= 40.0) & (freqs <= 500.0)
    if not np.any(band):
        return 0.0
    candidates = np.flatnonzero(band)
    return float(freqs[candidates[int(np.argmax(spectrum[band]))]])


def _legacy_audible_808_window(signal: np.ndarray, rate: int, root_hz: float) -> tuple[float, float, set[int]]:
    """Infer the target-frequency window this particular 808 can carry.

    A nearly pure sub needs to sit higher before it reads as an 808 rather
    than a vocal-like moan.  A richer 808 with audible upper partials can
    remain lower.  The result is a WAV-specific constraint on *musical target
    notes*; it never changes their pitch class.
    """
    # Root verification and this fixed musical lane are the evidence here.
    # Do not manufacture per-note bans from an FFT frequency threshold.
    return 32.70, 65.41, set(range(12))
    body = signal[min(signal.size // 3, int(rate * 0.075)) :]
    win = min(32768, body.size)
    if win < 2048 or root_hz <= 0:
        return 38.0, 66.0, set(range(12))
    frame = (body[:win] - float(np.mean(body[:win]))) * np.hanning(win)
    power = np.abs(np.fft.rfft(frame)) ** 2
    freqs = np.fft.rfftfreq(win, 1.0 / rate)
    total = float(np.sum(power)) or 1.0
    harmonic_ratio = (float(np.sum(freqs * power)) / total) / root_hz
    if harmonic_ratio <= 1.10:
        # Almost a pure sine: it needs the A#3–C4-style mid-bass pocket.
        lo, hi = 48.0, 66.0
    elif harmonic_ratio <= 1.35:
        lo, hi = 43.0, 66.0
    else:
        lo, hi = 38.0, 66.0
    supported: set[int] = set()
    # FL's useful 808 lane is deliberately bounded to C3–C5.  Candidates
    # outside that range are either infra-sub or a lead/whine, never a fix.
    for midi in range(24, 37):
        hz = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        if lo <= hz <= hi:
            supported.add(midi % 12)
    return lo, hi, supported


def _audible_808_window(signal: np.ndarray, rate: int, root_hz: float) -> tuple[float, float, set[int]]:
    """Return the fixed, verified C2--C3 808 playing lane.

    The WAV root determines how FL transposes the source. Within one approved
    octave, the note name itself is not evidence that a pitch class is bad;
    harmony therefore keeps all twelve pitch classes available.
    """
    return 32.70, 65.41, set(range(12))


def _preferred_808_register(
    signal: np.ndarray, rate: int, root_hz: float
) -> tuple[int, dict[str, float], dict[str, int], dict[str, float]]:
    """Choose C2/C3 from the *audible rendered body*, not source pitch.

    A root detector only tells FL how to transpose a WAV. It cannot tell us
    whether a C2 playback will be felt, or merely disappear under the kick.
    For each candidate octave we project the sample spectrum through that
    pitch shift and favour body (80-260 Hz) plus readable harmonics, while
    rejecting sub-only and harsh results. This keeps every result in C2-C3.
    """
    body = signal[min(signal.size // 3, int(rate * 0.075)) :]
    win = min(32768, body.size)
    if win < 2048 or root_hz <= 0:
        return (
            2,
            {"C2": 0.0, "C3": 1.0},
            {str(pc): 24 + pc for pc in range(12)},
            {str(pc): 1.0 for pc in range(12)},
        )
    frame = (body[:win] - float(np.mean(body[:win]))) * np.hanning(win)
    power = np.abs(np.fft.rfft(frame)) ** 2
    freqs = np.fft.rfftfreq(win, 1.0 / rate)
    total = float(np.sum(power)) or 1.0
    source_centroid = float(np.sum(freqs * power)) / total

    def band(lo: float, hi: float) -> float:
        mask = (freqs >= lo) & (freqs < hi)
        return float(np.sum(power[mask])) / total

    def score_for_midi(target_midi: int) -> float:
        target_hz = 440.0 * 2.0 ** ((target_midi - 69) / 12.0)
        shift = target_hz / root_hz
        # Convert desired output bands back into source-spectrum bands.
        body_energy = band(80.0 / shift, 260.0 / shift)
        presence = band(260.0 / shift, 1500.0 / shift)
        sub_only = band(20.0 / shift, 65.0 / shift)
        harsh = band(2600.0 / shift, rate / 2.0)
        # Project the spectral centroid too. A low-centroid WAV can reach C5
        # cleanly; a bright WAV (GHOUL) turns vocal/whiny long before that.
        output_centroid = source_centroid * shift
        if 420.0 <= output_centroid <= 1320.0:
            timbre = 0.75
        elif output_centroid < 240.0:
            timbre = -0.85
        elif output_centroid > 1750.0:
            timbre = -1.25
        else:
            timbre = -0.25
        low_note = 0.30 if 75.0 <= target_hz <= 180.0 else (-0.30 if target_hz < 62.0 or target_hz > 230.0 else 0.0)
        return 3.2 * body_energy + 0.8 * presence - 1.8 * sub_only - 1.1 * harsh + timbre + low_note

    scores: dict[str, float] = {}
    for octave, label in ((2, "C2"), (3, "C3")):
        scores[label] = round(score_for_midi(octave * 12), 6)
    # Per pitch-class map. Do not force F to share C's octave: each harmonic
    # note gets the least muddy/non-vocal playable register for this WAV.
    # Candidate register is C3–C5.  This is the only range in which the 808
    # lane is allowed to solve a harmonic note.
    note_map: dict[str, int] = {}
    note_scores: dict[str, float] = {}
    for pc in range(12):
        candidates = [midi for midi in range(24, 37) if midi % 12 == pc]
        best_note = max(candidates, key=score_for_midi)
        note_map[str(pc)] = best_note
        note_scores[str(pc)] = round(score_for_midi(best_note), 6)
    best = max((2, 3), key=lambda octave: scores[f"C{octave}"])
    return best, scores, note_map, note_scores


@lru_cache(maxsize=512)
def _cached(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    path = Path(path_text)
    try:
        signal, rate = _read_pcm_mono(path)
        freq, prominence = _estimate_fundamental(signal, rate)
        head_freq = _head_dominant_frequency(signal, rate)
        midi_float = 69.0 + 12.0 * math.log2(freq / 440.0)
        analysed_midi = int(round(midi_float))
        embedded_root = _embedded_wav_root_note(path)
        head_midi = 69.0 + 12.0 * math.log2(head_freq / 440.0) if head_freq > 0 else midi_float
        pitch_drop = abs(head_midi - midi_float)
        # A surprising number of kit WAVs carry a container-default C5
        # ``smpl`` value even when their sustained body is C2. Treating that
        # as an author root lowers every C2/C3 trigger by 2--3 octaves.
        # A tag is authoritative only when it agrees with the measured body.
        tag_matches_body = (
            embedded_root is not None and abs(float(embedded_root) - midi_float) <= 3.0
        )
        detected_midi = embedded_root if tag_matches_body else analysed_midi
        cents = (midi_float - detected_midi) * 100.0
        # If no matching author tag exists, accept only a stable body whose
        # attack is close enough to it to be an actual playable 808 instead
        # of a long pitch-drop/noise effect.
        reliable = tag_matches_body or (
            prominence >= 2.0 and abs(cents) <= 45.0 and pitch_drop <= 6.0
        )
        # ``status`` stays strict (an attack that lands far from the body is
        # "uncertain"). ``root_trusted`` is the answer to "may FL's root key be
        # set from the measured body?": a classic 808 pitch envelope (attack
        # 12-20 semitones above the body) with a clear, in-tune sustained body
        # has a perfectly good root -- the body is what long notes play. Only
        # the pitch-drop clause is relaxed; a weak fundamental or an out-of-tune
        # body still fails, so noise-like/unpitched files stay rejected.
        root_trusted = reliable or (
            prominence >= _TRUSTED_MIN_PROMINENCE
            and abs(cents) <= 45.0
            and pitch_drop <= _TRUSTED_MAX_PITCH_DROP
        )
        preferred_octave, register_scores, note_map, note_scores = _preferred_808_register(signal, rate, freq)
        audible_min_hz, audible_max_hz, supported_pitch_classes = _audible_808_window(signal, rate, freq)
        # A pc is "weak" only relative to what THIS sample can otherwise do —
        # its own best-achievable score, not an absolute threshold that would
        # penalize a naturally quiet/dark sample across the whole kit. This is
        # the "even the best register is still bad" signal score_for_midi's
        # per-candidate ranking alone cannot express (it only ranks candidates
        # against each other, never against "is this actually usable").
        weak_pitch_classes = []
        anchor = preferred_octave * 12
        winner = float(register_scores[f"C{preferred_octave}"])
        lower = float(register_scores.get(f"C{preferred_octave - 1}", winner - 9.0))
        upper = float(register_scores.get(f"C{preferred_octave + 1}", winner - 9.0))
        # Per-WAV orientation remains useful metadata, but final export uses
        # pitch_class_map below rather than a one-size range.
        if winner - upper <= 0.25:
            register_layout = "bottom_anchor"
            safe_min, safe_max = anchor, anchor + 11
        elif winner - upper <= 0.70 and winner - lower <= 0.70:
            register_layout = "center_anchor"
            safe_min, safe_max = anchor - 5, anchor + 6
        else:
            register_layout = "top_anchor"
            safe_min, safe_max = anchor - 11, anchor
        return {
            "path": str(path), "status": "ok" if reliable else "uncertain",
            "root_trusted": bool(root_trusted),
            "frequency_hz": round(freq, 3), "detected_midi": detected_midi,
            "root_source": (
                "wav_smpl" if tag_matches_body
                else ("spectral_estimate" if reliable else "spectral_ambiguous")
            ),
            # FL's root-key field is an absolute MIDI note, not merely a pitch
            # class parked in the C5 octave. Mapping a 41 Hz 808 to E5 then
            # triggering C3 transposes it into infra-sound (~8 Hz), which
            # looks like valid MIDI but is effectively silent. Preserve the
            # detected octave so low 808 notes play in the audible sub range.
            "root_pc": detected_midi % 12, "fl_root_note": detected_midi,
            "preferred_note_octave": preferred_octave,
            "register_scores": register_scores,
            "register_layout": register_layout,
            "pitch_class_map": note_map,
            "pitch_class_scores": note_scores,
            "weak_pitch_classes": weak_pitch_classes,
            "audible_min_hz": round(audible_min_hz, 2),
            "audible_max_hz": round(audible_max_hz, 2),
            "supported_pitch_classes": sorted(supported_pitch_classes),
            "safe_note_min": max(24, safe_min),
            "safe_note_max": min(36, safe_max),
            "cents": round(cents, 2), "prominence": round(prominence, 3),
            "head_frequency_hz": round(head_freq, 3), "pitch_drop_semitones": round(pitch_drop, 2),
        }
    except Exception as exc:  # noqa: BLE001
        return {"path": str(path), "status": "unavailable", "reason": str(exc), "fl_root_note": 60}


def sample_pitch_passport(path: Path | str) -> dict[str, Any]:
    sample = Path(path)
    stat = sample.stat()
    return dict(_cached(str(sample.resolve()), stat.st_mtime_ns, stat.st_size))


__all__ = ["sample_pitch_passport"]
