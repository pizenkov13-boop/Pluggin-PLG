"""A deterministic synthetic drum library for tests (no real audio needed).

Shared by the golden test of pick_best_for_track and the Kit Coherence tests.
Files are named like a real kit ("Kick 03 Tight.wav"), live in lane folders, and
include a duplicate, a pack-style folder and unreadable files.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100


def _write(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(data.tobytes())


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(SR * seconds)) / SR


def _kick(rng, i):
    t = _t(0.35)
    f = 45 + 12 * (i % 5) + 90 * np.exp(-t * 40)
    return 0.9 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * (7 + i % 4))


def _snare(rng, i):
    t = _t(0.22)
    return (0.6 * rng.uniform(-1, 1, t.size) + 0.3 * np.sin(2 * np.pi * (180 + 15 * i) * t)) * np.exp(-t * (18 + i % 5))


def _clap(rng, i):
    t = _t(0.25)
    bursts = sum(np.exp(-np.clip(t - d, 0, None) * 60) * (t >= d) for d in (0.0, 0.012, 0.024))
    return 0.5 * rng.uniform(-1, 1, t.size) * bursts * np.exp(-t * (9 + i % 4))


def _hat(rng, i):
    t = _t(0.05 + 0.01 * (i % 3))
    # band-limited noise: a closed-hat gate rejects "metallic" (very high centroid and zcr) ticks
    noise = np.convolve(rng.uniform(-1, 1, t.size), np.ones(5 + i % 4) / (5 + i % 4), mode="same")
    return 0.6 * noise * np.exp(-t * (60 + 5 * i))


def _808(rng, i):
    t = _t(1.1 + 0.1 * (i % 4))
    return 0.9 * np.sin(2 * np.pi * (36 + 4 * (i % 6)) * t) * np.minimum(1.0, t / 0.004) * np.exp(-t * (1.6 + 0.2 * i % 3))


def _bell(rng, i):
    t = _t(0.8)
    return 0.5 * np.sin(2 * np.pi * (700 + 90 * i) * t) * np.exp(-t * 4)


RECIPES = {
    "Kick": ("Kicks", _kick, 10),
    "Snare": ("Snares", _snare, 10),
    "Clap": ("Claps", _clap, 9),
    "Hat": ("Hats", _hat, 8),
    "808": ("808s", _808, 9),
    "Bell": ("Melodies", _bell, 5),
}
TAGS = ("Dark", "Hard", "Crisp", "Snap", "Boom", "Tight", "Clean", "Dirty")




def build_library(root: Path) -> Path:
    for kind, (folder, make, count) in RECIPES.items():
        for i in range(count):
            rng = np.random.RandomState(1000 * len(kind) + i)
            _write(root / folder / f"{kind} {i:02d} {TAGS[i % len(TAGS)]}.wav", make(rng, i))
    # the same sound under a second folder name (duplicate filenames collapse in the variety band)
    _write(root / "Kicks" / "Pack B" / "Kick 00 Dark.wav", _kick(np.random.RandomState(7), 0))
    # a pack-style kit folder, and files that cannot be read
    for i in range(4):
        _write(root / "Drum Kit @prod" / f"Snare Main {i} @producerg.wav", _snare(np.random.RandomState(50 + i), i))
    (root / "Kicks" / "Kick broken.wav").write_bytes(b"RIFF nope")
    (root / "Claps" / "Clap empty.wav").write_bytes(b"")
    return root
