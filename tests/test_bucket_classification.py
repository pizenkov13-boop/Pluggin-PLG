"""Which bucket a file is filed in decides which audio gate can hide it.

A bucket verdict used to come from substrings of the WHOLE path, so a kit folder
("808 Heavy Drum Kit"), a word that merely contains a keyword ("Chat", "Whatever",
"Sharp") or an "808" in a drum name ("808 Snare") sent ordinary one-shots to the
808/hats buckets, where the 808-body / closed-hat gates judged them and hid them
from every lane. Now: the file name is judged first, whole words only for the
keywords that hide in ordinary words, a drum word beats "808" unless the file is
long, and a folder name counts only when the audio agrees with it.
"""

from __future__ import annotations

import json
import math
import wave
from pathlib import Path

import pytest

from library_catalog import _folder_category, scan_library

RATE = 44100
FIXTURE = Path(__file__).parent / "fixtures" / "real_kit_buckets.json"


def _tone(path: Path, seconds: float, freq: float = 60.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(RATE * seconds)
    frames = b"".join(
        int(20000 * math.exp(-i / RATE / max(0.03, seconds / 3)) * math.sin(2 * math.pi * freq * i / RATE)).to_bytes(2, "little", signed=True)
        for i in range(n)
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    return path


def _bucket(tmp_path: Path, rel: str, seconds: float) -> str:
    return _folder_category(_tone(tmp_path / rel, seconds), tmp_path)


@pytest.mark.skip(reason="requires third-party assets not included")
def test_a_real_kit_keeps_every_bucket_it_had():
    # 144 real relative paths captured before the rewrite (names only, so no
    # tie-break/agreement rule can fire): the classification must not move one.
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    root = Path("lib").resolve()
    changed = {rel: (bucket, _folder_category(root / rel, root)) for rel, bucket in expected.items()
               if _folder_category(root / rel, root) != bucket}
    assert changed == {}


@pytest.mark.parametrize(
    ("rel", "seconds", "bucket"),
    [
        # a drum word beats "808"/"distorted" unless the file is long
        ("808 Snare 01.wav", 0.15, "kits"),
        ("TR-808 Snare.wav", 0.15, "kits"),
        ("808 Clap 01.wav", 0.17, "kits"),
        ("808 Kick 01.wav", 0.14, "kits"),
        ("Kick Distorted.wav", 0.15, "kits"),
        ("Clap Reese.wav", 0.17, "kits"),
        ("808 Hat 01.wav", 0.06, "hats"),
        ("Kick 808.wav", 1.2, "808"),
        ("Distorted Kick 808.wav", 1.5, "808"),
        ("GHOUL 808.wav", 0.3, "808"),
        # words that only CONTAIN a keyword are not the keyword
        ("That Clap.wav", 0.15, "kits"),
        ("Chatter Clap.wav", 0.15, "kits"),
        ("Bride Kick.wav", 0.15, "kits"),
        ("Phat Snare.wav", 0.15, "kits"),
        ("Whatever Snare.wav", 0.15, "kits"),
        ("Override Snare.wav", 0.15, "kits"),
        ("Sharp Snare.wav", 0.15, "kits"),
        ("Isabelle Clap.wav", 0.15, "kits"),
        ("Atom Kick.wav", 0.15, "kits"),
        ("Leader Snare.wav", 0.15, "kits"),
        ("Monkeys Kick.wav", 0.15, "kits"),
        # the real words still work: plural, camelCase, hyphen, digits
        ("Hat01.wav", 0.06, "hats"),
        ("Hats 02.wav", 0.06, "hats"),
        ("ClosedHat.wav", 0.06, "hats"),
        ("Hi-Hat.wav", 0.06, "hats"),
        ("HiHat 03.wav", 0.06, "hats"),
        ("Open Hat.wav", 0.5, "hats"),
        ("Ride 01.wav", 0.06, "hats"),
        ("Crash Cymbal.wav", 0.06, "hats"),
        ("Bell 01.wav", 0.3, "melodies"),
        ("Lead 01.wav", 0.3, "melodies"),
        ("Arp 01.wav", 0.3, "melodies"),
        ("Keys 01.wav", 0.3, "melodies"),
        ("Snare FX.wav", 0.15, "fx"),
        ("SFX Hit.wav", 0.15, "fx"),
        ("Toms 01.wav", 0.15, "kits"),
        ("Rimshot.wav", 0.15, "kits"),
        ("Snap 01.wav", 0.15, "kits"),
    ],
)
def test_bucket_from_the_file_name(tmp_path, rel, seconds, bucket):
    assert _bucket(tmp_path, rel, seconds) == bucket


@pytest.mark.parametrize(
    ("rel", "seconds", "bucket"),
    [
        # a whole kit lives in the folder: an unlabelled short file is a drum
        ("808 Heavy Drum Kit/07.wav", 0.2, "kits"),
        ("808s & Drums/07.wav", 0.2, "kits"),
        ("Distorted Drums/07.wav", 0.2, "kits"),
        # a real 808 folder still files real 808s
        ("808s/07.wav", 1.5, "808"),
        ("Sub Bass Kit/07.wav", 1.5, "808"),
        ("808s/08.wav", 0.2, "kits"),  # too short to be an 808: the folder is not believed
        # hats: only a closed-hat-length file
        ("Hi Hats/07.wav", 0.06, "hats"),
        ("Hi Hats/08.wav", 0.3, "kits"),
        ("Ride Cymbals/07.wav", 0.06, "hats"),
        # substrings of ordinary words in a folder name
        ("Whatever Vol.1/07.wav", 0.2, "kits"),
        ("Chat Pack/07.wav", 0.2, "kits"),
        ("Sharp Kit/07.wav", 0.2, "kits"),
        # exact-name top folders no longer override the name / the audio
        ("808/Kick 01.wav", 0.14, "kits"),
        ("hats/Kick 01.wav", 0.14, "kits"),
        ("hats/Clap 01.wav", 0.17, "kits"),
        ("808/07.wav", 1.4, "808"),
        ("808/08.wav", 0.2, "kits"),
        ("hats/07.wav", 0.06, "hats"),
        ("hats/08.wav", 0.4, "kits"),
        # a drum kit with a flavour word is still a drum kit
        ("Lead Drums/07.wav", 0.2, "kits"),
        ("Impact Drums/07.wav", 0.2, "kits"),
        ("Ambient Drums/07.wav", 0.2, "kits"),
        # unchanged
        ("fx/07.wav", 0.2, "fx"),
        ("kits/07.wav", 0.2, "kits"),
        ("FX & Risers/07.wav", 0.2, "fx"),
        ("Vinyl Kit/07.wav", 0.2, "textures"),
    ],
)
def test_bucket_from_the_folder_only_when_the_audio_agrees(tmp_path, rel, seconds, bucket):
    assert _bucket(tmp_path, rel, seconds) == bucket


def test_the_collision_names_are_no_longer_hidden_or_misfiled(tmp_path):
    # Ordinary one-shots whose names collide with a keyword: none may be judged by
    # the wrong bucket's gate, so nothing lands in rejected_audio.
    names = [
        ("808 Kick 01.wav", 0.14, 60), ("808 Snare 01.wav", 0.15, 200), ("TR-808 Snare.wav", 0.15, 200),
        ("808 Clap 01.wav", 0.17, 1200), ("808 Hat 01.wav", 0.06, 3000), ("Kick Distorted.wav", 0.14, 60),
        ("That Clap.wav", 0.17, 1200), ("Chatter Clap.wav", 0.17, 1200), ("Bride Kick.wav", 0.14, 60),
    ]
    for name, seconds, freq in names:
        _tone(tmp_path / "kit" / name, seconds, freq)

    catalog = scan_library(tmp_path)

    assert catalog["rejected_audio"] == {}
    filed = {Path(r).name: b for b, rs in catalog["audio"].items() for r in rs}
    assert filed["808 Hat 01.wav"] == "hats"
    assert all(filed[n] == "kits" for n, _, _ in names if n != "808 Hat 01.wav")
