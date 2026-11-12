"""Instant in-app beat preview — render the pattern to audio before FL.

Takes the generated pattern + the chosen one-shot samples (plg_sound_paths) and
bakes a short stereo .wav so the user can hit Play and *hear* the beat in the
app, no DAW needed. Drums are scheduled on the grid; 808/melody one-shots are
pitch-shifted per note; notes are panned using the producer brain's pan data;
the master gets a gentle soft-clip. Pure numpy + pydub (reads WAV without
ffmpeg) + stdlib wave for output — light and PyInstaller-friendly.

Anything missing or unreadable is skipped or replaced by a synth tone, so a
preview always renders — even on an empty library (bundled starter sounds).

``render_pattern(sr=..., max_seconds=None)`` also doubles as the full-length
render engine for offline export (see slowed_reverb_export.py) — same
bus-balanced mix, just uncapped and at export sample rate instead of the
20s/32kHz in-app preview.
"""

from __future__ import annotations

import base64
import io
import logging
import wave
from pathlib import Path
from typing import Any

import numpy as np

from pattern_utils import parse_note_name, track_notes

logger = logging.getLogger("plg.preview")

SR = 32000
MAX_SECONDS = 20.0
BASE_MIDI = 60  # C5 in PLG naming — reference pitch for pitched one-shots
FADE_MS = 4

DRUM_TRACKS = ("kick", "snare", "snare_layer", "clap", "hi_hats")
PITCHED_TRACKS = ("sub_808", "808_bass", "melody_lead", "counter_melody")
# Background/ear-candy one-shots (Dirkey bus-processing idea, same spirit as the
# FLP background bus): triggered per note like drums, but never a pitched
# instrument and never worth a synth fallback if the sample is missing — these
# lanes are already dropped upstream (starter_kit.py) when the library has no
# matching sample, so an unreadable file here just plays nothing for that note.
EXTRA_ONE_SHOT_TRACKS = ("chords", "texture", "fx", "cowbell")
# Relative level WITHIN a bus (cross-bus balance is handled by bus RMS staging).
TRACK_GAIN = {
    "kick": 1.0, "snare": 0.82, "snare_layer": 0.32, "clap": 0.6, "hi_hats": 0.42,
    "sub_808": 1.0, "808_bass": 1.0, "melody_lead": 1.0, "counter_melody": 0.55,
    "chords": 0.5, "texture": 0.4, "fx": 0.6, "cowbell": 0.5,
}

# Each track feeds one mix bus; buses are RMS-balanced so the melody always sits
# on top of the drums no matter how dense the beat or how loud the samples are.
_BUS_OF = {
    "kick": "drums", "snare": "drums", "snare_layer": "drums", "clap": "drums", "hi_hats": "drums",
    "sub_808": "bass", "808_bass": "bass", "melody_lead": "lead", "counter_melody": "lead",
    "chords": "candy", "texture": "candy", "fx": "candy", "cowbell": "candy",
}
# Target balance: everything clearly present — melody/808 a touch forward of the
# drums (the hook leads) without burying the beat. Ear candy stays background.
_BUS_LEVEL = {"drums": 0.90, "bass": 0.95, "lead": 0.98, "candy": 0.55}
_BUS_REF_RMS = 0.15  # each non-empty bus is normalized toward this RMS before _BUS_LEVEL
_BUS_GAIN_CLAMP = (0.25, 2.6)  # never over/under-drive a sparse or hot bus
_sample_cache: dict[tuple[str, int], np.ndarray] = {}


def _load_sample(path: str, sr: int = SR) -> np.ndarray | None:
    key = (path, sr)
    if key in _sample_cache:
        return _sample_cache[key]
    try:
        from pydub import AudioSegment

        seg = AudioSegment.from_file(path).set_channels(1).set_frame_rate(sr)
    except Exception as exc:
        logger.debug("preview load skip %s: %s", path, exc)
        return None
    data = np.asarray(seg.get_array_of_samples(), dtype=np.float64)
    if data.size == 0:
        return None
    data /= float(2 ** (8 * seg.sample_width - 1)) or 1.0
    _sample_cache[key] = data
    return data


def _pitch_shift(samples: np.ndarray, semitones: int) -> np.ndarray:
    if semitones == 0 or samples.size == 0:
        return samples
    factor = 2.0 ** (semitones / 12.0)
    new_len = max(1, int(samples.size / factor))
    idx = np.linspace(0, samples.size - 1, new_len)
    return np.interp(idx, np.arange(samples.size), samples)


def _synth_tone(midi: int, length_samples: int, sr: int = SR) -> np.ndarray:
    """Fallback saw tone with a quick ADSR when a track has no sample."""
    if length_samples <= 0:
        return np.zeros(0)
    freq = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    t = np.arange(length_samples) / sr
    phase = (freq * t) % 1.0
    wave_out = 2.0 * phase - 1.0  # saw
    env = np.ones(length_samples)
    a = min(length_samples, int(0.005 * sr))
    r = min(length_samples, int(0.08 * sr))
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] *= np.linspace(1, 0, r)
    return wave_out * env * 0.5


_drum_synth_cache: dict[tuple[str, int], np.ndarray] = {}


def _synth_drum(key: str, sr: int = SR) -> np.ndarray:
    """Percussive synth fallback so the BEAT is always audible — even when a drum
    sample is missing or unreadable. Without this, drum lanes were silently
    dropped and the preview played only the melody/808."""
    cache_key = (key, sr)
    if cache_key in _drum_synth_cache:
        return _drum_synth_cache[cache_key]

    if key == "kick":
        dur = 0.20
        t = np.arange(int(dur * sr)) / sr
        freq = 120.0 * np.exp(-t * 32.0) + 46.0  # punchy pitch drop
        body = np.sin(2.0 * np.pi * np.cumsum(freq) / sr)
        buf = np.tanh(body * np.exp(-t * 17.0) * 1.5)
    elif key in ("snare", "snare_layer"):
        t = np.arange(int(0.17 * sr)) / sr
        noise = np.random.default_rng(909).standard_normal(t.size)
        tone = np.sin(2.0 * np.pi * 185.0 * t)
        buf = np.tanh((noise * 0.7 + tone * 0.4) * np.exp(-t * 24.0))
    elif key == "clap":
        t = np.arange(int(0.15 * sr)) / sr
        noise = np.random.default_rng(707).standard_normal(t.size)
        buf = np.tanh(noise * np.exp(-t * 30.0) * 0.95)
    else:  # hi_hats and any other percussion
        t = np.arange(int(0.05 * sr)) / sr
        noise = np.random.default_rng(808).standard_normal(t.size)
        buf = np.tanh(noise * np.exp(-t * 95.0) * 0.85)

    _drum_synth_cache[cache_key] = buf
    return buf


def _fade(buf: np.ndarray, sr: int = SR) -> np.ndarray:
    n = min(len(buf), int(FADE_MS / 1000.0 * sr))
    if n > 1:
        buf = buf.copy()
        buf[:n] *= np.linspace(0, 1, n)
        buf[-n:] *= np.linspace(1, 0, n)
    return buf


def _pan_gains(pan: float) -> tuple[float, float]:
    theta = max(0.0, min(127.0, pan)) / 127.0 * (np.pi / 2.0)
    return float(np.cos(theta)), float(np.sin(theta))


def render_pattern(
    pattern: dict[str, Any],
    *,
    sr: int = SR,
    max_seconds: float | None = MAX_SECONDS,
) -> np.ndarray:
    """Render the pattern to a stereo float buffer in [-1, 1].

    ``max_seconds=None`` renders the FULL pattern length uncapped (the export
    path); the default (20s) preserves the instant in-app preview's original
    behaviour exactly.
    """
    bpm = float(pattern.get("bpm", 140) or 140)
    spb = 60.0 / max(1.0, bpm)
    sound_paths = pattern.get("plg_sound_paths") or {}
    all_tracks = DRUM_TRACKS + PITCHED_TRACKS + EXTRA_ONE_SHOT_TRACKS

    total_beats = 0.0
    for key in all_tracks:
        for note in track_notes(pattern, key):
            total_beats = max(total_beats, float(note.get("time_step", 0)) + float(note.get("length", 0.25)))
    total_sec = total_beats * spb + 0.6
    if max_seconds is not None:
        total_sec = min(max_seconds, total_sec)
    n = max(1, int(total_sec * sr))

    # Accumulate into separate mix buses so we can balance them independently.
    buses: dict[str, list[np.ndarray]] = {
        name: [np.zeros(n), np.zeros(n)] for name in ("drums", "bass", "lead", "candy")
    }

    for key in all_tracks:
        notes = track_notes(pattern, key)
        if not notes:
            continue
        base = _load_sample(str(sound_paths.get(key, "")), sr) if sound_paths.get(key) else None
        pitched = key in PITCHED_TRACKS
        extra_one_shot = key in EXTRA_ONE_SHOT_TRACKS
        track_gain = TRACK_GAIN.get(key, 0.7)
        bus_l, bus_r = buses[_BUS_OF.get(key, "lead")]

        for note in notes:
            start = int(float(note.get("time_step", 0)) * spb * sr)
            if start >= n:
                continue
            try:
                midi = parse_note_name(str(note.get("note", "C5")))
            except ValueError:
                midi = BASE_MIDI

            if base is not None:
                clip = _pitch_shift(base, midi - BASE_MIDI) if pitched else base
            elif pitched:
                length_samples = int(max(0.1, float(note.get("length", 0.5))) * spb * sr)
                clip = _synth_tone(midi, length_samples, sr)
            elif extra_one_shot:
                continue  # no sample, no synth fallback — starter_kit already drops these lanes when unmatched
            else:
                clip = _synth_drum(key, sr)  # drum lane with no sample → synth hit (never silent)

            clip = _fade(clip, sr)
            gain = track_gain * (int(note.get("velocity", 100)) / 127.0)
            lg, rg = _pan_gains(float(note.get("pan", 64)))
            end = min(n, start + clip.size)
            seg = clip[: end - start]
            bus_l[start:end] += seg * gain * lg
            bus_r[start:end] += seg * gain * rg

    # --- Bus gain staging: RMS-normalize each bus, then set the musical balance.
    # This keeps the lead/melody clearly on top of the drums regardless of how
    # dense the beat is or how hot the samples are (no more "only drums" wall).
    left = np.zeros(n)
    right = np.zeros(n)
    lo, hi = _BUS_GAIN_CLAMP
    for name, (bus_l, bus_r) in buses.items():
        bus_rms = float(np.sqrt(np.mean(bus_l ** 2 + bus_r ** 2) / 2.0))
        if bus_rms <= 1e-6:
            continue
        factor = min(hi, max(lo, _BUS_REF_RMS / bus_rms)) * _BUS_LEVEL[name]
        left += bus_l * factor
        right += bus_r * factor

    stereo = np.stack([left, right], axis=1)
    # Clean limiter: leave headroom, then a touch of soft-clip glue on the peaks
    # only — never saturate the whole signal (which used to crush the melody).
    peak = float(np.max(np.abs(stereo)))
    if peak > 1e-6:
        stereo *= 0.85 / peak
    stereo = np.tanh(stereo * 1.05) / float(np.tanh(1.05))
    peak = float(np.max(np.abs(stereo)))
    if peak > 0.985:
        stereo *= 0.985 / peak
    return stereo


def _to_wav_bytes(stereo: np.ndarray) -> bytes:
    pcm = (np.clip(stereo, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def render_preview(pattern: dict[str, Any], out_path: Path | None = None) -> dict[str, Any]:
    """Render the pattern and return {ok, audio (data URI), seconds, path}."""
    stereo = render_pattern(pattern)
    seconds = round(stereo.shape[0] / SR, 2)
    wav_bytes = _to_wav_bytes(stereo)

    path_str = ""
    if out_path is not None:
        try:
            out_path.write_bytes(wav_bytes)
            path_str = str(out_path)
        except OSError as exc:
            logger.warning("preview write failed: %s", exc)

    b64 = base64.b64encode(wav_bytes).decode("ascii")
    return {
        "ok": True,
        "audio": f"data:audio/wav;base64,{b64}",
        "seconds": seconds,
        "path": path_str,
    }
