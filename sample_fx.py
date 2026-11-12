"""Melody FX baked into the sample — the Yeat/Carti/Travis sound, plugin-free.

Mixer-insert plugin chains corrupted live FL projects (quarantined behind
PLG_INSERT_SPACE_FX), so character FX go where saturation already goes: into
the wav itself. The Sampler loads a pre-processed one-shot and every note
carries the sound. All DSP is numpy, deterministic, cached per (genre, file).

Effects (offline, on a <=4s one-shot):
- hard_clip      Fruity WaveShaper move: drive into a hard ceiling -> square-ish
                 metal ("Still Countin" lead).
- bitcrush       sample-hold downsampling + bit quantize -> digital dirt that
                 "sands" the top end (rage).
- ring_mod       multiply by a low sine -> the Metamorphosis buzz / alien metal.
- flanger        LFO-modulated short delay -> liquid, spinning psychedelia.
- lpf_sweep      slowly breathing one-pole lowpass -> underwater auto-wah.
- reverse_tail   the one-shot's own reverse swelling underneath -> HITR ghost.
- gate_tail      expander after distortion: silence between notes stays silent.
- interval_stack layer a pitch-shifted copy under the dry signal (Dirkey's
                 "pitch a chord down 5 and stack it" trick -> bigger voicing).
- low_cut        one-pole highpass -> the thin low end a phone mic captures
                 recording speakers across a room (part of Dirkey's re-amp trick).

Recipes are per-genre lane; melody_lead gets the full chain, counter_melody a
lighter version, chords only a whisper (the bed must glue, not fight). Two
genre-agnostic bonus layers ride on top of every recipe: _SAMPLE_REALISM_LAYER
(wow/echo/low-cut - approximates re-amping through speakers and re-recording
off a phone, Dirkey's "sounds sampled" trick) and _CHORD_INTERVAL_LAYER
(interval_stack on the chords lane only). The chords lane also gets an
asymmetric stereo bake (apply_stereo_split / process_file_stereo): Dirkey's
"pan two copies, distort only one side" trick baked into a single stereo
one-shot for the SAME sampler channel — no second mixer track, no insert chain.
Kill switches: PLG_MELODY_FX=off (all), PLG_SAMPLE_REALISM=off (re-amp bonus),
PLG_CHORD_INTERVAL_LAYER=off (chord-doubling bonus),
PLG_STEREO_ASYMMETRY=off (asymmetric L/R bake).

A separate, unrelated concern lives at the bottom of this file: sub_808-only
character (Dirkey "How To Make 808s From Scratch" — PLG picks a sample rather
than synthesizing from an oscillator, so only the 2 techniques that still
apply to a sample were ported): pitch_punch (a short downward pitch sweep
baked into the onset, the "kick punch" transient) and mono_below (mid/side
split, highpass the side channel below the crossover so the sub stays
phase-safe centered while any stereo info above it survives). Wired via
sub_808_character_sound_map, independent of the melody recipe system above.
Kill switches: PLG_808_PUNCH=off, PLG_808_MONO_BELOW=off.

RAW SAMPLE MODE: PLG_RAW_SAMPLES=1 is the one master switch for users whose
library is already professionally mixed — it forces every per-sample DSP
bake in this file (melody FX, realism/re-amp, chord-interval doubling,
stereo asymmetry, delay layer, reverse chop, 808 punch/mono-below) AND the
tanh saturation warmth + Haas stereo-widen in starter_kit.py off at once,
overriding every individual PLG_*=auto default above. It does NOT touch
sample SELECTION, note velocity/timing/panning, or mixer gain staging —
raw mode changes which bytes get written into the one-shot's wav, nothing
about how the notes that trigger it are arranged or mixed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from sample_saturate import _read_wav, _write_wav16

logger = logging.getLogger("plg.sample_fx")

_PEAK_CEILING = 0.95
_BLOCK = 256  # block size for the time-varying filters (fast + smooth enough)


def raw_sample_mode_enabled() -> bool:
    """Master override for a library that's already professionally mixed —
    off by default (opt-IN, unlike the individual PLG_*=auto switches below,
    which default on). When true, every per-sample DSP gate in this module
    (and starter_kit.py's saturation warmth) short-circuits to disabled."""
    return os.environ.get("PLG_RAW_SAMPLES", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def melody_fx_enabled() -> bool:
    if raw_sample_mode_enabled():
        return False
    return os.environ.get("PLG_MELODY_FX", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


def sample_realism_enabled() -> bool:
    if raw_sample_mode_enabled():
        return False
    return os.environ.get("PLG_SAMPLE_REALISM", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


def chord_interval_layer_enabled() -> bool:
    if raw_sample_mode_enabled():
        return False
    return os.environ.get("PLG_CHORD_INTERVAL_LAYER", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


def stereo_asymmetry_enabled() -> bool:
    if raw_sample_mode_enabled():
        return False
    return os.environ.get("PLG_STEREO_ASYMMETRY", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


def melody_delay_layer_enabled() -> bool:
    if raw_sample_mode_enabled():
        return False
    return os.environ.get("PLG_MELODY_DELAY_LAYER", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


def reverse_chop_enabled() -> bool:
    if raw_sample_mode_enabled():
        return False
    return os.environ.get("PLG_REVERSE_CHOP", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


# ---------------------------------------------------------------------------
# Effects — each takes/returns a mono float64 array in [-1, 1]. Pure, testable.
# ---------------------------------------------------------------------------


def _normalize_peak(out: Any, reference: Any) -> Any:
    import numpy as np

    peak = float(np.max(np.abs(out))) if out.size else 0.0
    ref = min(_PEAK_CEILING, max(0.1, float(np.max(np.abs(reference))) if reference.size else 1.0))
    if peak > 1e-9:
        out = out * (ref / peak)
    return out


def hard_clip(x: Any, *, drive: float = 3.0, mix: float = 0.8) -> Any:
    """WaveShaper wall: drive the wave into a hard ceiling (controlled metal)."""
    import numpy as np

    clipped = np.clip(x * max(1.0, drive), -1.0, 1.0)
    out = (1.0 - mix) * x + mix * clipped
    return _normalize_peak(out, x)


def bitcrush(x: Any, sr: int, *, factor: int = 5, bits: int = 10, mix: float = 0.35) -> Any:
    """Sample-rate reduction (hold every Nth sample) + bit-depth quantize."""
    import numpy as np

    factor = max(1, int(factor))
    held = np.repeat(x[::factor], factor)[: x.shape[0]]
    if held.shape[0] < x.shape[0]:
        held = np.pad(held, (0, x.shape[0] - held.shape[0]), mode="edge")
    levels = float(2 ** (max(4, int(bits)) - 1))
    quant = np.round(held * levels) / levels
    out = (1.0 - mix) * x + mix * quant
    return _normalize_peak(out, x)


def ring_mod(x: Any, sr: int, *, freq: float = 40.0, mix: float = 0.25) -> Any:
    """Ring modulation — the low-frequency 'жужание' buzz under the note."""
    import numpy as np

    t = np.arange(x.shape[0], dtype=np.float64) / float(sr)
    carrier = np.sin(2.0 * np.pi * freq * t)
    out = (1.0 - mix) * x + mix * (x * carrier)
    return _normalize_peak(out, x)


def flanger(x: Any, sr: int, *, rate_hz: float = 0.7, depth_ms: float = 4.0, mix: float = 0.3) -> Any:
    """LFO-modulated short delay mixed under the dry — liquid spinning motion."""
    import numpy as np

    n = x.shape[0]
    t = np.arange(n, dtype=np.float64)
    base = max(1.0, depth_ms * sr / 1000.0)
    delay = base * (1.0 + np.sin(2.0 * np.pi * rate_hz * t / sr)) * 0.5 + 1.0
    src = t - delay
    src = np.clip(src, 0, n - 1)
    lo = np.floor(src).astype(np.int64)
    hi = np.minimum(lo + 1, n - 1)
    frac = src - lo
    delayed = x[lo] * (1.0 - frac) + x[hi] * frac
    out = (1.0 - mix) * x + mix * 0.5 * (x + delayed)
    return _normalize_peak(out, x)


def lpf_sweep(
    x: Any, sr: int, *, base_hz: float = 900.0, range_hz: float = 2600.0,
    rate_hz: float = 0.5, mix: float = 1.0,
) -> Any:
    """Breathing one-pole lowpass (auto-wah): cutoff rides a slow LFO."""
    import numpy as np

    n = x.shape[0]
    out = np.empty_like(x)
    state = 0.0
    two_pi = 2.0 * np.pi
    for start in range(0, n, _BLOCK):
        stop = min(n, start + _BLOCK)
        mid = (start + stop) * 0.5
        cutoff = base_hz + range_hz * 0.5 * (1.0 + np.sin(two_pi * rate_hz * mid / sr))
        alpha = 1.0 - np.exp(-two_pi * cutoff / sr)
        seg = x[start:stop]
        acc = np.empty_like(seg)
        for i in range(seg.shape[0]):  # tiny inner loop, one block at a time
            state += alpha * (seg[i] - state)
            acc[i] = state
        out[start:stop] = acc
    result = (1.0 - mix) * x + mix * out
    return _normalize_peak(result, x)


def reverse_tail(x: Any, *, mix: float = 0.22) -> Any:
    """Overlay the one-shot's own reverse, swelling toward the end (HITR ghost)."""
    import numpy as np

    n = x.shape[0]
    swell = np.linspace(0.0, 1.0, n, dtype=np.float64) ** 2
    out = x + mix * x[::-1] * swell
    return _normalize_peak(out, x)


def pitch_glide(x: Any, sr: int, *, semitones: float = 1.2, rise: float = 0.55) -> Any:
    """Bake a rising pitch envelope into the one-shot (the Toliver glide pad).

    The sample starts ``semitones`` below, glides smoothly up over the first
    ``rise`` fraction of its length, then hovers in tune — every triggered note
    'floats into place'. Implemented as variable-rate resampling (numpy interp)."""
    import numpy as np

    n = x.shape[0]
    if n < 8:
        return x
    rise_n = max(1, int(n * min(0.95, max(0.05, rise))))
    # smooth S-curve from -semitones to 0, then flat
    t = np.arange(n, dtype=np.float64)
    curve = np.minimum(t / rise_n, 1.0)
    curve = curve * curve * (3.0 - 2.0 * curve)  # smoothstep
    cents = -abs(semitones) * 100.0 * (1.0 - curve)
    rate = 2.0 ** (cents / 1200.0)
    positions = np.concatenate(([0.0], np.cumsum(rate)[:-1]))
    positions = np.clip(positions, 0, n - 1)
    out = np.interp(positions, t, x)
    return _normalize_peak(out, x)


def vinyl_wow(x: Any, sr: int, *, depth_cents: float = 8.0, rate_hz: float = 1.3) -> Any:
    """Slow pitch wobble (wow & flutter) — the Kanye 'sampled off a record' feel.

    A gentle sine pitch drift (variable-rate resample) plus a hair of second-
    harmonic flutter, so the flip breathes like worn vinyl instead of a clean
    synth."""
    import numpy as np

    n = x.shape[0]
    if n < 16:
        return x
    t = np.arange(n, dtype=np.float64)
    cents = depth_cents * (
        np.sin(2.0 * np.pi * rate_hz * t / sr)
        + 0.35 * np.sin(2.0 * np.pi * rate_hz * 2.7 * t / sr)
    )
    rate = 2.0 ** (cents / 1200.0)
    positions = np.clip(np.concatenate(([0.0], np.cumsum(rate)[:-1])), 0, n - 1)
    out = np.interp(positions, t, x)
    return _normalize_peak(out, x)


def tape_echo(x: Any, sr: int, *, delay_ms: float = 190.0, feedback: float = 0.34,
              mix: float = 0.26, tone: float = 0.6) -> Any:
    """Analog feedback delay (EchoBoy-style) — warm repeats that darken each pass.

    Each echo is lowpassed a touch more than the last (``tone``), so the tail
    dissolves the way tape does instead of a sterile digital repeat."""
    import numpy as np

    n = x.shape[0]
    d = max(1, int(delay_ms * sr / 1000.0))
    out = x.astype(np.float64).copy()
    tap = x.astype(np.float64).copy()
    gain = feedback
    for _ in range(4):  # four decaying taps is plenty for the tail
        shifted = np.zeros(n, dtype=np.float64)
        if d < n:
            shifted[d:] = tap[:-d]
        # progressive one-pole lowpass = tape tone loss
        alpha = 1.0 - np.exp(-2.0 * np.pi * (1200.0 * tone) / sr)
        filtered = np.empty(n, dtype=np.float64)
        state = 0.0
        for i in range(n):
            state += alpha * (shifted[i] - state)
            filtered[i] = state
        out += mix * gain * filtered
        tap = filtered
        gain *= feedback
        if gain < 0.02:
            break
    return _normalize_peak(out, x)


def gate_tail(x: Any, sr: int, *, thresh_db: float = -46.0) -> Any:
    """Expander after distortion: kill the smeared noise floor between notes."""
    import numpy as np

    n = x.shape[0]
    thresh = 10.0 ** (thresh_db / 20.0)
    gains = np.ones(n, dtype=np.float64)
    env = 0.0
    release = np.exp(-1.0 / (0.012 * sr))  # ~12ms release per sample
    for start in range(0, n, _BLOCK):
        stop = min(n, start + _BLOCK)
        peak = float(np.max(np.abs(x[start:stop])))
        env = max(peak, env * (release ** (stop - start)))
        gains[start:stop] = 1.0 if env >= thresh else 0.0
    # smooth gate edges (~3ms) so it clicks like a gate, not like a glitch
    kernel = max(1, int(0.003 * sr))
    gains = np.convolve(gains, np.ones(kernel) / kernel, mode="same")
    return x * gains


def interval_stack(x: Any, sr: int, *, semitones: float = -5.0, mix: float = 0.3) -> Any:
    """Layer a pitch-shifted copy underneath the dry signal (Dirkey's "pitch a
    chord down 5 and stack it" trick — a doubled interval reads as a bigger,
    more considered voicing than the same chord alone)."""
    import numpy as np

    n = x.shape[0]
    if n < 8:
        return x
    rate = 2.0 ** (semitones / 12.0)
    t = np.arange(n, dtype=np.float64)
    positions = np.clip(t * rate, 0, n - 1)
    shifted = np.interp(positions, t, x)
    out = x + mix * shifted
    return _normalize_peak(out, x)


def unison_octave_stack(x: Any, sr: int, *, mix_up: float = 0.35, mix_down: float = 0.35) -> Any:
    """Copy the melody an octave up AND down, both computed from the SAME dry
    signal and summed in (not chained through interval_stack, which would
    cascade one shift on top of the other) — Jay Cactus's "West Coast piano":
    basic notes doubled ±octave, layered, as the foundation of the melody
    rather than a quiet background blend."""
    import numpy as np

    n = x.shape[0]
    if n < 8:
        return x
    t = np.arange(n, dtype=np.float64)

    def _shift(semitones: float) -> Any:
        rate = 2.0 ** (semitones / 12.0)
        positions = np.clip(t * rate, 0, n - 1)
        return np.interp(positions, t, x)

    out = x + mix_up * _shift(12.0) + mix_down * _shift(-12.0)
    return _normalize_peak(out, x)


def _match_length(y: Any, n: int) -> Any:
    """Truncate or zero-pad ``y`` to length ``n`` along the time axis (no
    resample/tile — avoids scrambling a second layer's own content)."""
    import numpy as np

    if y.shape[0] >= n:
        return y[:n]
    pad_shape = (n - y.shape[0],) + y.shape[1:]
    return np.concatenate([y, np.zeros(pad_shape, dtype=y.dtype)], axis=0)


def _stable_seed(path: Path) -> int:
    """Deterministic seed from a file path — same chop order every re-render."""
    import hashlib

    return int(hashlib.sha256(str(path).encode()).hexdigest()[:8], 16)


def reverse_chop(
    x: Any, sr: int, *, layer: Any | None = None, layer_mix: float = 0.4,
    chop_count: int = 8, seed: int = 0,
) -> Any:
    """Dirkey Destroy Lonely: "heard a lot of reversed melodies" on the album —
    mix a second layer in (if given), reverse the WHOLE combined buffer, then
    chop it into pieces and deterministically reshuffle their order. Works on
    already-selected one-shots (no pattern-timeline rendering); the new
    bassline underneath is a separate, ordinary arrangement concern."""
    import random

    import numpy as np

    n = x.shape[0]
    if n < 8:
        return x
    combined = x.astype(np.float64).copy()
    if layer is not None and getattr(layer, "size", 0):
        layer = np.asarray(layer, dtype=np.float64)
        if layer.ndim == combined.ndim:
            matched = _match_length(layer, n)
            combined = (1.0 - layer_mix) * combined + layer_mix * matched
    reversed_buf = combined[::-1]

    count = max(1, min(chop_count, n))
    bounds = np.linspace(0, n, count + 1).astype(int)
    segments = [reversed_buf[bounds[i]:bounds[i + 1]] for i in range(count)]
    segments = [s for s in segments if s.shape[0] > 0]
    if not segments:
        return _normalize_peak(reversed_buf, x)

    order = list(range(len(segments)))
    random.Random(seed).shuffle(order)
    out = np.concatenate([segments[i] for i in order], axis=0)
    return _normalize_peak(out, x)


def delay_tail(
    x: Any, sr: int, *,
    delay_ms: float = 220.0, feedback: float = 0.45, repeats: int = 4, wet_mix: float = 0.55,
) -> Any:
    """Mike Dean: a genuine decaying delay tail baked ONCE into a one-shot —
    repeats echoes at delay_ms spacing, each `feedback` quieter than the
    last (exponential decay), summed with the dry signal. Not a channel
    effect: this extends the buffer itself, the same "bake it into the WAV"
    idea as reverse_chop, just additive instead of reversed/chopped. Works
    on an already-selected one-shot (no pattern-timeline rendering)."""
    import numpy as np

    n = x.shape[0]
    if n == 0:
        return x
    delay_samples = max(1, int(sr * delay_ms / 1000.0))
    out = np.zeros((n + delay_samples * repeats,) + x.shape[1:], dtype=np.float64)
    out[:n] += x.astype(np.float64)
    gain = wet_mix
    for i in range(1, repeats + 1):
        gain *= feedback
        start = i * delay_samples
        out[start:start + n] += x.astype(np.float64) * gain
    return _normalize_peak(out, x)


def low_cut(x: Any, sr: int, *, hz: float = 100.0, mix: float = 1.0) -> Any:
    """One-pole highpass — the low end a phone mic loses recording speakers
    from across a room (part of Dirkey's re-amp trick: the thinned-out low end
    is part of what reads as captured, not triggered from a sampler).

    Deliberately skips ``_normalize_peak``: that helper restores output peak to
    match the input, which would silently undo the intended attenuation for a
    signal that's mostly low-frequency content."""
    import numpy as np

    n = x.shape[0]
    alpha = 1.0 - np.exp(-2.0 * np.pi * hz / sr)
    lp = np.empty(n, dtype=np.float64)
    state = 0.0
    for i in range(n):
        state += alpha * (x[i] - state)
        lp[i] = state
    hp = x - lp
    out = (1.0 - mix) * x + mix * hp
    return np.clip(out, -1.0, 1.0)


# ---------------------------------------------------------------------------
# Genre recipes — ordered chains of (effect_name, params).
# ---------------------------------------------------------------------------

Chain = list[tuple[str, dict[str, Any]]]

# Yeat metal: wall-clip + digital sand, gated tight.
_RAGE: Chain = [
    ("hard_clip", {"drive": 3.2, "mix": 0.8}),
    ("bitcrush", {"factor": 5, "bits": 10, "mix": 0.3}),
    ("gate_tail", {"thresh_db": -46.0}),
    # Blood-style rage: a slow, phaser-like sweep on top of the distortion — the
    # "melting" glide-pad character in the background (chords gets it quieter
    # than melody_lead via the existing per-lane _LANE_AMOUNT scaling, same as
    # every other genre's chain — no new lane-specific plumbing needed).
    ("flanger", {"rate_hz": 0.35, "depth_ms": 5.0, "mix": 0.25}),
]
# Carti buzz: clip + low ring-mod «жужание», a breath of motion.
_OPIUM: Chain = [
    ("hard_clip", {"drive": 2.4, "mix": 0.7}),
    ("ring_mod", {"freq": 38.0, "mix": 0.22}),
    ("flanger", {"rate_hz": 0.5, "depth_ms": 3.0, "mix": 0.2}),
]
# Travis space: liquid motion + underwater breathing + reverse ghost.
_PSYCHEDELIC: Chain = [
    ("flanger", {"rate_hz": 0.8, "depth_ms": 5.0, "mix": 0.3}),
    ("lpf_sweep", {"base_hz": 1100.0, "range_hz": 2800.0, "rate_hz": 0.4, "mix": 0.55}),
    ("reverse_tail", {"mix": 0.2}),
    ("hard_clip", {"drive": 1.6, "mix": 0.35}),
]
# Phonk: crushed and mean.
_PHONK: Chain = [
    ("bitcrush", {"factor": 6, "bits": 9, "mix": 0.45}),
    ("hard_clip", {"drive": 2.6, "mix": 0.65}),
]
# Default trap: character without wrecking melodic beats.
_TRAP: Chain = [
    ("hard_clip", {"drive": 1.8, "mix": 0.45}),
    ("flanger", {"rate_hz": 0.6, "depth_ms": 3.5, "mix": 0.22}),
]
# Don Toliver: the glide pad — pitch floats up into place, liquid motion,
# NO distortion (this world is airy, not aggressive).
_TOLIVER: Chain = [
    ("pitch_glide", {"semitones": 1.2, "rise": 0.55}),
    ("flanger", {"rate_hz": 0.4, "depth_ms": 6.0, "mix": 0.28}),
]
# Kanye: the sample-flip signature — worn-vinyl wobble + warm tape saturation +
# analog tape echo, tops rolled off so it reads as a chopped dusty record, NOT
# a clean synth. No hard clip, no metal.
_KANYE: Chain = [
    ("vinyl_wow", {"depth_cents": 8.0, "rate_hz": 1.3}),
    ("hard_clip", {"drive": 1.4, "mix": 0.3}),   # gentle tape-style saturation
    ("lpf_sweep", {"base_hz": 2600.0, "range_hz": 400.0, "rate_hz": 0.15, "mix": 0.7}),
    ("tape_echo", {"delay_ms": 190.0, "feedback": 0.32, "mix": 0.24}),
]
# West Coast (Jay Cactus): piano unison-doubled an octave up/down first (the
# "basic notes copied on octave up and down, layered" foundation), THEN a
# slow, wide LFO delay in Juno6-chorus territory (depth_ms here is 5-6x
# _TRAP's flanger depth — chorus, not a flanger sweep). No distortion — the
# gritty read comes from minimal reverb (see MIX_RECIPES["west_coast"]'s low
# wet in style_corpus.py), not a clipped melody.
_WEST_COAST: Chain = [
    ("unison_octave_stack", {"mix_up": 0.35, "mix_down": 0.35}),
    ("flanger", {"rate_hz": 0.3, "depth_ms": 18.0, "mix": 0.3}),
]
# Dirkey "How To Make Underground Beats": ring modulation is the signature
# lead character, not a subtle background hint like opium's (freq 38, mix
# 0.22) — pushed louder and lower for a rawer, more prominent buzz, plus a
# hard clip to match the deliberately crunchy 808 aesthetic.
_UNDERGROUND: Chain = [
    ("ring_mod", {"freq": 55.0, "mix": 0.4}),
    ("hard_clip", {"drive": 2.0, "mix": 0.5}),
]

MELODY_FX_RECIPES: dict[str, Chain] = {
    "rage": _RAGE,
    "twizzy": _RAGE,
    "hyper_rage": _RAGE,
    "opium": _OPIUM,
    "plugg": _OPIUM,
    "dark_trap": _OPIUM,
    "trap": _PSYCHEDELIC,
    "psychedelic": _PSYCHEDELIC,
    "melodic_trap": _TRAP,
    "phonk": _PHONK,
    "drill": _TRAP,
    "jersey": _TRAP,
    "pop_rap": _TRAP,
    "toliver": _TOLIVER,
    "kanye": _KANYE,
    "soul": _KANYE,
    "west_coast": _WEST_COAST,
    "underground": _UNDERGROUND,
}

# Which lanes get the recipe, and how hard (mix scaling).
_LANE_AMOUNT = {"melody_lead": 1.0, "counter_melody": 0.6, "chords": 0.3}

_EFFECTS = {
    "hard_clip": lambda x, sr, p: hard_clip(x, **p),
    "bitcrush": lambda x, sr, p: bitcrush(x, sr, **p),
    "ring_mod": lambda x, sr, p: ring_mod(x, sr, **p),
    "flanger": lambda x, sr, p: flanger(x, sr, **p),
    "lpf_sweep": lambda x, sr, p: lpf_sweep(x, sr, **p),
    "reverse_tail": lambda x, sr, p: reverse_tail(x, **p),
    "pitch_glide": lambda x, sr, p: pitch_glide(x, sr, **p),
    "vinyl_wow": lambda x, sr, p: vinyl_wow(x, sr, **p),
    "tape_echo": lambda x, sr, p: tape_echo(x, sr, **p),
    "gate_tail": lambda x, sr, p: gate_tail(x, sr, **p),
    "interval_stack": lambda x, sr, p: interval_stack(x, sr, **p),
    "low_cut": lambda x, sr, p: low_cut(x, sr, **p),
    "unison_octave_stack": lambda x, sr, p: unison_octave_stack(x, sr, **p),
}

# Dirkey "record the playback through speakers on a phone" trick, approximated
# offline: a hair of pitch wobble (room air/movement) + a couple of short
# decaying reflections (tape_echo reused at room-bounce timing, not delay-pedal
# timing) + a thinned low end (distance loses low frequencies). Subtle by
# design — a bonus riding on top of the genre's own chain, never a replacement.
_SAMPLE_REALISM_LAYER: Chain = [
    ("vinyl_wow", {"depth_cents": 2.5, "rate_hz": 0.6}),
    ("tape_echo", {"delay_ms": 18.0, "feedback": 0.16, "mix": 0.14, "tone": 0.45}),
    ("low_cut", {"hz": 80.0, "mix": 0.35}),
]

# Dirkey "pitch it down 5 and stack" — appended to the chords lane only.
_CHORD_INTERVAL_LAYER: Chain = [("interval_stack", {"semitones": -5.0, "mix": 0.28})]

# Dirkey "We Don't Trust You" double piano layer: the upper/lead notes get a
# short tail delay, the root/bed notes don't (phase-safe — the root stays
# anchored while the top line breathes). Root = the chords lane (sustained
# bed); upper = melody_lead (the hook) — so this bonus rides ONLY on
# melody_lead, mirroring _CHORD_INTERVAL_LAYER's "chords only" shape inverted.
_MELODY_DELAY_LAYER: Chain = [("tape_echo", {"delay_ms": 170.0, "feedback": 0.2, "mix": 0.22, "tone": 0.55})]

# Dirkey "pan two copies, distort only one side" — extra grit baked ONLY into
# the right channel of the chords lane's stereo asymmetry bake; left stays the
# lane's normal (undistorted) chain.
_STEREO_ASYMMETRY_RIGHT_EXTRA: Chain = [("hard_clip", {"drive": 1.6, "mix": 0.28})]


def apply_stereo_split(x: Any, sr: int, left_chain: Chain, right_chain: Chain) -> Any:
    """Run different chains on left vs right — Dirkey's "pan two copies, distort
    only one side" trick, baked into a single stereo one-shot (same sampler
    channel, no new mixer track). A mono source is duplicated to both sides
    before the chains diverge; an already-stereo source uses its left channel
    as the shared dry base for both sides."""
    import numpy as np

    mono = np.asarray(x, dtype=np.float64)
    if mono.ndim > 1:
        mono = mono[:, 0]
    left = apply_chain(mono, sr, left_chain)
    right = apply_chain(mono, sr, right_chain)
    n = min(left.shape[0], right.shape[0])
    return np.stack([left[:n], right[:n]], axis=1)


def _scale_chain(chain: Chain, amount: float) -> Chain:
    if amount >= 0.999:
        return chain
    scaled: Chain = []
    for name, params in chain:
        p = dict(params)
        if "mix" in p:
            p["mix"] = round(float(p["mix"]) * amount, 4)
            if p["mix"] < 0.05:
                continue
        scaled.append((name, p))
    return scaled


def apply_chain(x: Any, sr: int, chain: Chain) -> Any:
    import numpy as np

    out = np.asarray(x, dtype=np.float64)
    for name, params in chain:
        fx = _EFFECTS.get(name)
        if fx is None:
            continue
        out = fx(out, sr, params)
    return out


def process_file(src: Path, dst: Path, chain: Chain) -> bool:
    """Bake the chain into ``dst``. Returns True when a processed file landed."""
    if not chain:
        return False
    try:
        import numpy as np

        audio, sr, channels = _read_wav(src)
        if channels > 1:
            processed = np.stack(
                [apply_chain(audio[:, c], sr, chain) for c in range(audio.shape[1])],
                axis=1,
            )
        else:
            processed = apply_chain(audio, sr, chain)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _write_wav16(dst, processed, sr)
        return True
    except Exception as exc:  # noqa: BLE001 — any wav we can't process passes through
        logger.info("Melody FX skipped for %s (%s)", Path(src).name, exc)
        return False


def process_file_stereo(src: Path, dst: Path, left_chain: Chain, right_chain: Chain) -> bool:
    """Bake an asymmetric L/R chain pair into a stereo ``dst``. Returns True on success."""
    if not left_chain and not right_chain:
        return False
    try:
        audio, sr, _ = _read_wav(src)
        stereo = apply_stereo_split(audio, sr, left_chain, right_chain)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _write_wav16(dst, stereo, sr)
        return True
    except Exception as exc:  # noqa: BLE001 — any wav we can't process passes through
        logger.info("Stereo asymmetry skipped for %s (%s)", Path(src).name, exc)
        return False


def process_file_reverse_chop(
    src: Path,
    dst: Path,
    *,
    chain: Chain | None = None,
    layer_src: Path | None = None,
    layer_mix: float = 0.4,
    chop_count: int = 8,
    seed: int = 0,
) -> bool:
    """Bake reverse_chop into ``dst`` — the genre chain (if any) applies FIRST,
    in memory, then the combined-and-reversed chop, all in one write."""
    try:
        audio, sr, _ = _read_wav(src)
        if chain:
            audio = apply_chain(audio, sr, chain)
        layer_audio = None
        if layer_src is not None and Path(layer_src).is_file():
            try:
                layer_audio, _lsr, _lch = _read_wav(layer_src)
            except Exception:  # noqa: BLE001 — a bad/missing layer just skips the mix-in
                layer_audio = None
        processed = reverse_chop(
            audio, sr, layer=layer_audio, layer_mix=layer_mix, chop_count=chop_count, seed=seed
        )
        dst.parent.mkdir(parents=True, exist_ok=True)
        _write_wav16(dst, processed, sr)
        return True
    except Exception as exc:  # noqa: BLE001 — any wav we can't process passes through
        logger.info("Reverse-chop skipped for %s (%s)", Path(src).name, exc)
        return False


def process_file_delay_accent(
    src: Path,
    dst: Path,
    *,
    chain: Chain | None = None,
    delay_ms: float = 220.0,
    feedback: float = 0.45,
    repeats: int = 4,
    wet_mix: float = 0.55,
) -> bool:
    """Bake delay_tail into ``dst`` — same read/apply-chain/write/
    except-log-False contract as process_file_reverse_chop."""
    try:
        audio, sr, _ = _read_wav(src)
        if chain:
            audio = apply_chain(audio, sr, chain)
        processed = delay_tail(
            audio, sr, delay_ms=delay_ms, feedback=feedback, repeats=repeats, wet_mix=wet_mix
        )
        dst.parent.mkdir(parents=True, exist_ok=True)
        _write_wav16(dst, processed, sr)
        return True
    except Exception as exc:  # noqa: BLE001 — any wav we can't process passes through
        logger.info("Delay-accent bake skipped for %s (%s)", Path(src).name, exc)
        return False


def melody_fx_sound_map(
    sound_map: dict[str, Path],
    cache_dir: Path,
    *,
    genre: str = "trap",
    chords_reverse_chop: bool = False,
) -> dict[str, Path]:
    """Return a new track->path map with FX-baked melodic one-shots.

    ``chords_reverse_chop`` is the caller's GenreProfile.chords_reverse_chop
    value (opt-in per genre, e.g. opium) — this function only owns the env
    kill switch, not genre routing, matching how the other per-lane bonus
    layers here are gated on/off but genre-agnostic.
    """
    if not melody_fx_enabled():
        return dict(sound_map)
    chain = MELODY_FX_RECIPES.get(str(genre or "").strip().lower())
    if not chain:
        return dict(sound_map)

    realism_on = sample_realism_enabled()
    chord_layer_on = chord_interval_layer_enabled()
    stereo_on = stereo_asymmetry_enabled()
    delay_layer_on = melody_delay_layer_enabled()
    reverse_chop_on = bool(chords_reverse_chop) and reverse_chop_enabled()
    cache_dir = Path(cache_dir)
    tag = str(genre).strip().lower()
    flags = "".join(
        f for f, on in (
            ("r", realism_on), ("i", chord_layer_on), ("s", stereo_on),
            ("d", delay_layer_on), ("c", reverse_chop_on),
        ) if on
    )
    if flags:
        tag = f"{tag}-{flags}"
    result: dict[str, Path] = {}
    for track, path in sound_map.items():
        path = Path(path)
        amount = _LANE_AMOUNT.get(track, 0.0)
        if amount > 0.0 and path.suffix.lower() == ".wav":
            lane_chain = _scale_chain(chain, amount)
            if realism_on:
                lane_chain = lane_chain + _scale_chain(_SAMPLE_REALISM_LAYER, amount)
            if track == "chords" and chord_layer_on:
                lane_chain = lane_chain + _scale_chain(_CHORD_INTERVAL_LAYER, amount)
            if track == "melody_lead" and delay_layer_on:
                lane_chain = lane_chain + _scale_chain(_MELODY_DELAY_LAYER, amount)
            dst = cache_dir / f"{track}__fx_{tag}_{path.stem}.wav"
            if track == "chords" and reverse_chop_on:
                # More specific than the generic stereo-asymmetry bonus below —
                # takes priority when both happen to be enabled for chords.
                layer_src = sound_map.get("melody_lead")
                rc_dst = cache_dir / f"chords__reversechop_{tag}_{path.stem}.wav"
                if rc_dst.is_file() or process_file_reverse_chop(
                    path, rc_dst, chain=lane_chain,
                    layer_src=Path(layer_src) if layer_src else None,
                    seed=_stable_seed(path),
                ):
                    result[track] = rc_dst
                    logger.info("Melody FX (%s): %s <- %s (reverse-chop)", tag, track, path.name)
                    continue
            elif track == "chords" and stereo_on:
                right_chain = lane_chain + _scale_chain(_STEREO_ASYMMETRY_RIGHT_EXTRA, amount)
                if dst.is_file() or process_file_stereo(path, dst, lane_chain, right_chain):
                    result[track] = dst
                    logger.info("Melody FX (%s): %s <- %s (stereo asym)", tag, track, path.name)
                    continue
            elif dst.is_file() or process_file(path, dst, lane_chain):
                result[track] = dst
                logger.info("Melody FX (%s): %s <- %s", tag, track, path.name)
                continue
        result[track] = path
    return result


# ---------------------------------------------------------------------------
# sub_808 character — unrelated to the melody recipe system above (Dirkey "How
# To Make 808s From Scratch"). Only the 2 techniques that survive translation
# from oscillator synthesis to sample-based 808s: an onset pitch punch and a
# mono-below-crossover safety net.
# ---------------------------------------------------------------------------

_808_PUNCH_SEMITONES = 8.0
_808_PUNCH_MS = 25.0
_808_MONO_BELOW_HZ = 300.0


def sub_808_punch_enabled() -> bool:
    if raw_sample_mode_enabled():
        return False
    # Default OFF — pitch-punch bake made the FL 808 a different sound than
    # the folder wav. Opt in with PLG_808_PUNCH=1.
    return os.environ.get("PLG_808_PUNCH", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def sub_808_mono_below_enabled() -> bool:
    if raw_sample_mode_enabled():
        return False
    return os.environ.get("PLG_808_MONO_BELOW", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def pitch_punch(
    x: Any, sr: int, *, semitones: float = _808_PUNCH_SEMITONES, duration_ms: float = _808_PUNCH_MS
) -> Any:
    """Bake a short downward pitch sweep into the one-shot's onset — starts
    ``semitones`` above the written pitch and settles within ``duration_ms``
    (the classic 808 "kick punch" transient, emulated on a sample instead of
    an oscillator's pitch envelope). Same variable-rate-resample technique as
    ``pitch_glide``, mirrored: starts high instead of low, over a fixed short
    duration instead of a fraction of the whole sample."""
    import numpy as np

    n = x.shape[0]
    if n < 16:
        return x
    punch_n = min(n - 1, max(1, int(sr * duration_ms / 1000.0)))
    t = np.arange(n, dtype=np.float64)
    curve = np.minimum(t / punch_n, 1.0)
    curve = curve * curve * (3.0 - 2.0 * curve)  # smoothstep
    cents = abs(semitones) * 100.0 * (1.0 - curve)  # starts high, decays to 0
    rate = 2.0 ** (cents / 1200.0)
    positions = np.concatenate(([0.0], np.cumsum(rate)[:-1]))
    positions = np.clip(positions, 0, n - 1)
    out = np.interp(positions, t, x)
    return _normalize_peak(out, x)


def mono_below(x: Any, sr: int, *, crossover_hz: float = _808_MONO_BELOW_HZ) -> Any:
    """Mid/side split — highpass the side channel at ``crossover_hz`` so stereo
    width collapses to mono below it (phase-safe, centered sub) while any
    stereo content above the crossover survives. Mono input passes through
    unchanged — there is no width to remove."""
    import numpy as np

    if x.ndim < 2 or x.shape[1] < 2:
        return x
    left, right = x[:, 0], x[:, 1]
    mid = (left + right) * 0.5
    side = (left - right) * 0.5
    side_hp = low_cut(side, sr, hz=crossover_hz, mix=1.0)
    return np.stack([mid + side_hp, mid - side_hp], axis=1)


def process_file_808_character(
    src: Path, dst: Path, *, punch: bool, mono_below_hz: float | None
) -> bool:
    """Bake the punch + mono-below onto ``dst``. Returns True on success."""
    if not punch and mono_below_hz is None:
        return False
    try:
        import numpy as np

        audio, sr, channels = _read_wav(src)
        if punch:
            if channels > 1:
                audio = np.stack(
                    [pitch_punch(audio[:, c], sr) for c in range(audio.shape[1])], axis=1
                )
            else:
                audio = pitch_punch(audio, sr)
        if mono_below_hz is not None and audio.ndim > 1 and audio.shape[1] > 1:
            audio = mono_below(audio, sr, crossover_hz=mono_below_hz)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _write_wav16(dst, audio, sr)
        return True
    except Exception as exc:  # noqa: BLE001 — any wav we can't process passes through
        logger.info("808 character skipped for %s (%s)", Path(src).name, exc)
        return False


def sub_808_character_sound_map(
    sound_map: dict[str, Path],
    cache_dir: Path,
) -> dict[str, Path]:
    """Return a new track->path map with the sub_808 one-shot punch/mono-below
    baked in (no-op for every other track — this never touches drums/melody)."""
    punch_on = sub_808_punch_enabled()
    mono_on = sub_808_mono_below_enabled()
    result = dict(sound_map)
    if not punch_on and not mono_on:
        return result
    path = sound_map.get("sub_808")
    if path is None:
        return result
    path = Path(path)
    if path.suffix.lower() != ".wav":
        return result

    cache_dir = Path(cache_dir)
    tag = "".join(f for f, on in (("p", punch_on), ("m", mono_on)) if on)
    dst = cache_dir / f"sub_808__808char_{tag}_{path.stem}.wav"
    if dst.is_file() or process_file_808_character(
        path, dst, punch=punch_on, mono_below_hz=_808_MONO_BELOW_HZ if mono_on else None
    ):
        result["sub_808"] = dst
        logger.info("808 character (%s): sub_808 <- %s", tag, path.name)
    return result
