"""Build bundled trap one-shots into assets/starter/bundled_sounds/."""

from __future__ import annotations

import json
import logging
import math
import struct
import wave
from pathlib import Path

from plg_paths import starter_bundle_dir

logger = logging.getLogger("plg.bundled_sounds")

SAMPLE_RATE = 44100


def bundled_sounds_dir() -> Path:
    path = starter_bundle_dir() / "bundled_sounds"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_wav_mono(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        for value in samples:
            clamped = max(-1.0, min(1.0, value))
            handle.writeframes(struct.pack("<h", int(clamped * 32767)))


def _soft_clip(value: float, drive: float = 2.2) -> float:
    return math.tanh(value * drive) / math.tanh(drive)


def _synth_808_variant(
    *,
    start_hz: float = 62,
    end_hz: float = 28,
    duration: float = 0.62,
    drive: float = 2.8,
    click: float = 0.35,
) -> list[float]:
    total = int(SAMPLE_RATE * duration)
    out: list[float] = []
    for i in range(total):
        t = i / SAMPLE_RATE
        progress = min(1.0, t / duration)
        freq = start_hz * (1.0 - 0.62 * progress) + end_hz
        body = math.sin(2 * math.pi * freq * t)
        click_wave = math.sin(2 * math.pi * 180 * t) * math.exp(-90 * t) * click
        env = math.exp(-2.4 * t) * (1.0 - progress * 0.12)
        out.append(_soft_clip((body * 0.95 + click_wave) * env, drive=drive))
    return out


def _synth_kick_variant(*, body_hz: float = 55, click_mix: float = 0.55, drive: float = 2.4) -> list[float]:
    duration = 0.18
    total = int(SAMPLE_RATE * duration)
    out: list[float] = []
    for i in range(total):
        t = i / SAMPLE_RATE
        click = math.sin(2 * math.pi * 120 * t) * math.exp(-55 * t)
        body = math.sin(2 * math.pi * body_hz * t) * math.exp(-18 * t)
        out.append(_soft_clip((click * click_mix + body * 0.85) * 0.9, drive=drive))
    return out


def _synth_hat_variant(*, tone_hz: float = 9200, noise: float = 0.75, seed: int = 808) -> list[float]:
    import random

    random.seed(seed)
    duration = 0.055
    total = int(SAMPLE_RATE * duration)
    out: list[float] = []
    for i in range(total):
        t = i / SAMPLE_RATE
        n = random.uniform(-1, 1)
        tone = math.sin(2 * math.pi * tone_hz * t) * 0.15
        env = math.exp(-70 * t)
        out.append(_soft_clip((n * noise + tone) * env * 0.55, drive=1.8))
    return out


def _synth_snare_variant(*, tone_hz: float = 180, noise_mix: float = 0.7) -> list[float]:
    import random

    random.seed(909)
    duration = 0.22
    total = int(SAMPLE_RATE * duration)
    out: list[float] = []
    for i in range(total):
        t = i / SAMPLE_RATE
        n = random.uniform(-1, 1)
        tone = math.sin(2 * math.pi * tone_hz * t) * math.exp(-35 * t)
        env = math.exp(-16 * t)
        out.append(_soft_clip((n * noise_mix + tone * 0.35) * env * 0.75, drive=2.0))
    return out


def _synth_clap() -> list[float]:
    import random

    random.seed(707)
    duration = 0.16
    total = int(SAMPLE_RATE * duration)
    out: list[float] = []
    for i in range(total):
        t = i / SAMPLE_RATE
        burst = random.uniform(-1, 1) if (i % 120) < 40 else random.uniform(-0.4, 0.4)
        env = math.exp(-28 * t)
        out.append(_soft_clip(burst * env * 0.55, drive=1.7))
    return out


def _synth_melody_variant(*, freqs: tuple[float, ...], decay: float = 7.5) -> list[float]:
    duration = 0.45
    total = int(SAMPLE_RATE * duration)
    weights = (0.55, 0.3, 0.15)[: len(freqs)]
    out: list[float] = []
    for i in range(total):
        t = i / SAMPLE_RATE
        env = math.exp(-decay * t)
        value = sum(math.sin(2 * math.pi * f * t) * w for f, w in zip(freqs, weights))
        out.append(_soft_clip(value * env * 0.42, drive=1.6))
    return out


BUNDLED_SPECS: list[tuple[str, list[float]]] = [
    ("808_opium_heavy.wav", _synth_808_variant(start_hz=58, drive=3.2, click=0.42)),
    ("808_rage_distort.wav", _synth_808_variant(start_hz=65, drive=3.6, click=0.5, duration=0.55)),
    ("808_starboy_clean.wav", _synth_808_variant(start_hz=52, drive=2.2, click=0.28, duration=0.72)),
    ("808_phonk_sub.wav", _synth_808_variant(start_hz=48, end_hz=22, drive=3.0, duration=0.8)),
    ("808_glide_stab.wav", _synth_808_variant(start_hz=72, end_hz=35, drive=2.6, duration=0.4)),
    ("kick_punch_trap.wav", _synth_kick_variant(body_hz=52, click_mix=0.6, drive=2.6)),
    ("kick_opium_click.wav", _synth_kick_variant(body_hz=62, click_mix=0.72, drive=2.8)),
    ("kick_boom_808.wav", _synth_kick_variant(body_hz=45, click_mix=0.4, drive=2.0)),
    ("hat_tight_machine.wav", _synth_hat_variant(tone_hz=10500, noise=0.82, seed=101)),
    ("hat_opium_airy.wav", _synth_hat_variant(tone_hz=8800, noise=0.68, seed=202)),
    ("hat_open_roll.wav", _synth_hat_variant(tone_hz=7200, noise=0.55, seed=303)),
    ("snare_trap_crack.wav", _synth_snare_variant(tone_hz=190, noise_mix=0.75)),
    ("snare_rim_layer.wav", _synth_snare_variant(tone_hz=240, noise_mix=0.55)),
    ("clap_stack.wav", _synth_clap()),
    ("melody_dark_pluck.wav", _synth_melody_variant(freqs=(415.0, 622.0, 830.0))),
    ("melody_bell_hook.wav", _synth_melody_variant(freqs=(523.0, 784.0, 1046.0), decay=5.5)),
]

TRACK_PREFIXES = {
    "sub_808": ("808_",),
    "kick": ("kick_",),
    "hi_hats": ("hat_",),
    "snare": ("snare_",),
    "snare_layer": ("snare_rim", "snare_"),
    "clap": ("clap_",),
    "melody_lead": ("melody_",),
}


def ensure_bundled_sounds(*, force: bool = False) -> Path:
    """Write bundled one-shots if missing (or when force=True)."""
    out_dir = bundled_sounds_dir()
    manifest = out_dir / "manifest.json"
    if not force and manifest.is_file() and len(list(out_dir.glob("*.wav"))) >= len(BUNDLED_SPECS):
        return out_dir

    written: list[str] = []
    for filename, samples in BUNDLED_SPECS:
        path = out_dir / filename
        _write_wav_mono(path, samples)
        written.append(filename)
        logger.info("Bundled sound: %s", filename)

    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "count": len(written),
                "files": written,
                "style": "plg_bundled_trap_procedural",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out_dir


def pick_bundled_for_track(
    track: str,
    prompt: str = "",
    style: str = "",
) -> Path | None:
    """Score bundled pool by prompt keywords."""
    out_dir = ensure_bundled_sounds()
    prefixes = TRACK_PREFIXES.get(track)
    if not prefixes:
        return None

    candidates = [
        p for p in sorted(out_dir.glob("*.wav"))
        if any(p.name.startswith(pref) for pref in prefixes)
    ]
    if track == "hi_hats":
        # The main hat lane is a closed tick on a 1/8 grid: an open/roll hat
        # (hat_open_roll.wav) stacks into a metallic sheet there.
        candidates = [p for p in candidates if "open" not in p.stem.lower() and "roll" not in p.stem.lower()]
    if not candidates:
        return None

    text = f"{prompt} {style}".lower()
    keywords = {
        "opium": ("opium", "rage", "dark", "distort"),
        "heavy": ("heavy", "distort", "rage", "phonk"),
        "clean": ("clean", "star", "melodic", "ambient"),
        "click": ("click", "punch", "trap"),
        "rim": ("rim", "layer"),
        "bell": ("bell", "hook", "melody"),
        "machine": ("machine", "roll", "hat"),
    }

    def score(path: Path) -> int:
        name = path.stem.lower()
        total = 0
        for tag, words in keywords.items():
            if tag in name and any(w in text for w in words):
                total += 20
        if track == "snare_layer" and "rim" in name:
            total += 15
        if track == "sub_808" and "opium" in name and any(w in text for w in keywords["opium"]):
            total += 25
        return total

    return max(candidates, key=score)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    path = ensure_bundled_sounds(force=True)
    print(f"Wrote {len(BUNDLED_SPECS)} sounds -> {path}")
