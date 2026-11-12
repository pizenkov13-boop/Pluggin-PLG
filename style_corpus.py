"""Style corpus — curated musical DNA per genre (progressions, pockets, recipes).

The "school kid vs. pro" gap is rarely the notes themselves: it's static harmony,
generic pockets and reinvented-from-scratch structure. This module gives every
generation a *proven foundation* mined from real production idioms:

- chord progressions as per-bar scale degrees (the 808 walks the changes),
- authentic drum-pocket cells per genre (used by the deterministic fallback),
- genre BPM ranges (a 92 BPM "drill" beat is wrong before the first note),
- short production recipes injected into the architect prompt.

Everything is deterministic per (genre, key, scale, seed) so the prompt addendum
and the post-LLM enforcement (``attach_progression``) always agree.

Env:
    PLG_USE_STYLE_CORPUS=1|0|auto   (auto = on)
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
from typing import Any

from music_theory import SCALES, key_label, name_for_midi
from pattern_utils import NOTE_INDEX, NOTE_NAMES

logger = logging.getLogger("plg.style_corpus")

_BARS_DEFAULT = 8
# Sub register used for naming concrete 808 roots in prompts (matches sub_808_gen).
_SUB_LO = 36


def _env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()


def corpus_enabled() -> bool:
    return _env_flag("PLG_USE_STYLE_CORPUS", "auto") not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# Progressions — per-bar scale degrees (0-based) over an 8-bar phrase.
# Diatonic by construction: a scale-locked hook stays consonant while the 808
# walks the roots. Bar 1 is always the tonic so the hook anchors.
# ---------------------------------------------------------------------------

_P = dict  # readability alias

PROGRESSIONS: dict[str, list[dict[str, Any]]] = {
    "kanye": [
        _P(name="i–IV–i–VII (soul vamp)", bar_degrees=(0, 0, 3, 3, 0, 0, 6, 6), weight=5, mood="gospel warmth"),
        _P(name="i–iv–VII–III", bar_degrees=(0, 0, 3, 3, 6, 6, 2, 2), weight=4, mood="church lift"),
        _P(name="i–VI–ii–v", bar_degrees=(0, 0, 5, 5, 1, 1, 4, 4), weight=3, mood="soul cadence"),
        _P(name="i–III–IV–i", bar_degrees=(0, 0, 2, 2, 3, 3, 0, 0), weight=3, mood="vintage flip"),
    ],
    "toliver": [
        _P(name="i–VI–iv–VII (float)", bar_degrees=(0, 0, 5, 5, 3, 3, 6, 6), weight=5, mood="airy drift"),
        _P(name="i–iv–VI–v", bar_degrees=(0, 0, 3, 3, 5, 5, 4, 4), weight=4, mood="warm suspension"),
        _P(name="i–VI–III–iv", bar_degrees=(0, 0, 5, 5, 2, 2, 3, 3), weight=3, mood="hovering lift"),
        _P(name="i–v–iv–VI", bar_degrees=(0, 0, 4, 4, 3, 3, 5, 5), weight=2, mood="slow exhale"),
    ],
    "trap": [
        _P(name="i–VI–III–VII", bar_degrees=(0, 0, 5, 5, 2, 2, 6, 6), weight=5, mood="anthemic dark"),
        _P(name="i–VI–VII–i", bar_degrees=(0, 0, 5, 5, 6, 6, 0, 0), weight=4, mood="hypnotic loop"),
        _P(name="i–iv–VI–VII", bar_degrees=(0, 0, 3, 3, 5, 5, 6, 6), weight=4, mood="moody lift"),
        _P(name="i–VII–VI–VII", bar_degrees=(0, 0, 6, 6, 5, 5, 6, 6), weight=3, mood="tension pedal"),
        _P(name="i–v–VI–iv", bar_degrees=(0, 0, 4, 4, 5, 5, 3, 3), weight=2, mood="melancholic"),
        _P(name="i–III–VII–VI", bar_degrees=(0, 0, 2, 2, 6, 6, 5, 5), weight=2, mood="epic descent"),
    ],
    "opium": [
        _P(name="i–bII (phrygian pedal)", bar_degrees=(0, 0, 1, 1, 0, 0, 1, 1), weight=5, mood="sinister rub"),
        _P(name="i–bII–i–v", bar_degrees=(0, 0, 1, 1, 0, 0, 4, 4), weight=4, mood="ritual tension"),
        _P(name="i–VI–bII–i", bar_degrees=(0, 0, 5, 5, 1, 1, 0, 0), weight=3, mood="dark swell"),
        _P(name="i–v–bII–i", bar_degrees=(0, 0, 4, 4, 1, 1, 0, 0), weight=3, mood="haunted cadence"),
        _P(name="i pedal (root drone)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=1, mood="drone hypnosis"),
    ],
    "rage": [
        _P(name="i–bII–i–bII", bar_degrees=(0, 0, 1, 1, 0, 0, 1, 1), weight=5, mood="aggressive rub"),
        _P(name="i–VI–VII–bII", bar_degrees=(0, 0, 5, 5, 6, 6, 1, 1), weight=3, mood="rising chaos"),
        _P(name="i–iv–bII–i", bar_degrees=(0, 0, 3, 3, 1, 1, 0, 0), weight=3, mood="stomp cycle"),
        _P(name="i–VII–i–VII", bar_degrees=(0, 0, 6, 6, 0, 0, 6, 6), weight=2, mood="bounce pedal"),
    ],
    "drill": [
        _P(name="i–VI–iv–v", bar_degrees=(0, 0, 5, 5, 3, 3, 4, 4), weight=5, mood="sliding menace"),
        _P(name="i–iv–i–v", bar_degrees=(0, 0, 3, 3, 0, 0, 4, 4), weight=4, mood="cold cycle"),
        _P(name="i–bII–VI–v", bar_degrees=(0, 0, 1, 1, 5, 5, 4, 4), weight=3, mood="uk shadow"),
        _P(name="i–VII–VI–v", bar_degrees=(0, 0, 6, 6, 5, 5, 4, 4), weight=3, mood="descending drive"),
    ],
    "underground": [
        _P(name="i–iv–v–i", bar_degrees=(0, 0, 3, 3, 4, 4, 0, 0), weight=5, mood="heavy-hitting cycle"),
        _P(name="i–VI–VII–i", bar_degrees=(0, 0, 5, 5, 6, 6, 0, 0), weight=3, mood="crunch build"),
        _P(name="i pedal (root drone)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=2, mood="808-first hypnosis"),
    ],
    "minimal_trap": [
        _P(name="i pedal (night drone)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=5, mood="sparse and still"),
        _P(name="i–iv–i–iv", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=3, mood="slow sway"),
    ],
    "pluggnb": [
        _P(name="i7–IV7 (dorian vamp)", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=5, mood="silky plugg vamp"),
        _P(name="i–VI–VII–i", bar_degrees=(0, 0, 5, 5, 6, 6, 0, 0), weight=4, mood="mellow lift"),
        _P(name="i–v–IV–i", bar_degrees=(0, 0, 4, 4, 3, 3, 0, 0), weight=3, mood="late-night sway"),
        _P(name="i pedal (dorian float)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=2, mood="hypnotic float"),
    ],
    "phonk": [
        _P(name="i–iv–i–v", bar_degrees=(0, 0, 3, 3, 0, 0, 4, 4), weight=4, mood="memphis loop"),
        _P(name="i–VI–i–VII", bar_degrees=(0, 0, 5, 5, 0, 0, 6, 6), weight=3, mood="dusty cycle"),
        _P(name="i–VII–VI–VII", bar_degrees=(0, 0, 6, 6, 5, 5, 6, 6), weight=3, mood="drift pedal"),
    ],
    "hyperpop": [
        _P(name="I–V–vi–IV", bar_degrees=(0, 0, 4, 4, 5, 5, 3, 3), weight=4, mood="sugar anthem"),
        _P(name="vi–IV–I–V", bar_degrees=(5, 5, 3, 3, 0, 0, 4, 4), weight=3, mood="emotional burst"),
        _P(name="I–iii–IV–V", bar_degrees=(0, 0, 2, 2, 3, 3, 4, 4), weight=2, mood="glitter climb"),
    ],
    "jersey": [
        _P(name="i–VI–III–VII", bar_degrees=(0, 0, 5, 5, 2, 2, 6, 6), weight=4, mood="club anthem"),
        _P(name="i–iv–VI–v", bar_degrees=(0, 0, 3, 3, 5, 5, 4, 4), weight=3, mood="bounce cycle"),
    ],
    "kpop": [
        _P(name="I–V–vi–IV", bar_degrees=(0, 0, 4, 4, 5, 5, 3, 3), weight=5, mood="idol anthem"),
        _P(name="IV–V–iii–vi", bar_degrees=(3, 3, 4, 4, 2, 2, 5, 5), weight=3, mood="royal road"),
        _P(name="vi–IV–I–V", bar_degrees=(5, 5, 3, 3, 0, 0, 4, 4), weight=3, mood="bittersweet"),
    ],
    "rnb": [
        _P(name="i7–IV7 (dorian vamp)", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=5, mood="silky vamp"),
        _P(name="i–iii–IV–i", bar_degrees=(0, 0, 2, 2, 3, 3, 0, 0), weight=3, mood="neo-soul float"),
        _P(name="i–v–IV–iii", bar_degrees=(0, 0, 4, 4, 3, 3, 2, 2), weight=2, mood="late night"),
    ],
    "pop": [
        _P(name="I–V–vi–IV", bar_degrees=(0, 0, 4, 4, 5, 5, 3, 3), weight=5, mood="radio anthem"),
        _P(name="vi–IV–I–V", bar_degrees=(5, 5, 3, 3, 0, 0, 4, 4), weight=4, mood="wistful drive"),
        _P(name="I–IV–vi–V", bar_degrees=(0, 0, 3, 3, 5, 5, 4, 4), weight=3, mood="open lift"),
    ],
    "dark_pop": [
        _P(name="i–VI–III–VII", bar_degrees=(0, 0, 5, 5, 2, 2, 6, 6), weight=4, mood="neon noir"),
        _P(name="i–iii–VII–i", bar_degrees=(0, 0, 2, 2, 6, 6, 0, 0), weight=3, mood="synth shadow"),
        _P(name="i–IV–i–VII", bar_degrees=(0, 0, 3, 3, 0, 0, 6, 6), weight=3, mood="dorian glow"),
    ],
    "disco_pop": [
        _P(name="i–IV–i–IV", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=4, mood="funk vamp"),
        _P(name="i–iii–IV–v", bar_degrees=(0, 0, 2, 2, 3, 3, 4, 4), weight=3, mood="groove climb"),
    ],
    "edm": [
        _P(name="i–VI–III–VII", bar_degrees=(0, 0, 5, 5, 2, 2, 6, 6), weight=5, mood="festival lift"),
        _P(name="i–VII–VI–VII", bar_degrees=(0, 0, 6, 6, 5, 5, 6, 6), weight=3, mood="main-stage pedal"),
        _P(name="vi–IV–I–V feel", bar_degrees=(5, 5, 3, 3, 0, 0, 4, 4), weight=2, mood="hands-up"),
    ],
    "grind": [
        _P(name="i pedal (locrian)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=4, mood="wall of noise"),
        _P(name="i–bII grind", bar_degrees=(0, 0, 1, 1, 0, 0, 1, 1), weight=3, mood="dissonant slam"),
    ],
    "pop_rap": [
        _P(name="i–VI–III–VII", bar_degrees=(0, 0, 5, 5, 2, 2, 6, 6), weight=5, mood="chart anthem"),
        _P(name="i–VI–VII–i", bar_degrees=(0, 0, 5, 5, 6, 6, 0, 0), weight=4, mood="club strut"),
        _P(name="i–iv–i–VII", bar_degrees=(0, 0, 3, 3, 0, 0, 6, 6), weight=3, mood="sassy cycle"),
    ],
    # --- Club / electronic ---
    "house": [
        _P(name="i7–IV7 (deep vamp)", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=5, mood="deep groove"),
        _P(name="i–VII–IV–i", bar_degrees=(0, 0, 6, 6, 3, 3, 0, 0), weight=3, mood="warm ride"),
        _P(name="i–iii–IV–VII", bar_degrees=(0, 0, 2, 2, 3, 3, 6, 6), weight=3, mood="uplift roll"),
    ],
    "techno": [
        _P(name="i pedal (hypnotic)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=5, mood="tunnel drive"),
        _P(name="i–VI pedal swap", bar_degrees=(0, 0, 0, 0, 5, 5, 5, 5), weight=3, mood="dark shift"),
        _P(name="i–VII–i–VII", bar_degrees=(0, 0, 6, 6, 0, 0, 6, 6), weight=2, mood="pump cycle"),
    ],
    "trance": [
        _P(name="i–VI–III–VII", bar_degrees=(0, 0, 5, 5, 2, 2, 6, 6), weight=5, mood="euphoric lift"),
        _P(name="i–VII–VI–VII", bar_degrees=(0, 0, 6, 6, 5, 5, 6, 6), weight=3, mood="rolling arps"),
        _P(name="i–iv–VI–VII", bar_degrees=(0, 0, 3, 3, 5, 5, 6, 6), weight=3, mood="emotional climb"),
    ],
    "hardstyle": [
        _P(name="i–bII rub", bar_degrees=(0, 0, 1, 1, 0, 0, 1, 1), weight=4, mood="raw tension"),
        _P(name="i–VI–VII–i", bar_degrees=(0, 0, 5, 5, 6, 6, 0, 0), weight=3, mood="anthem stomp"),
    ],
    "dnb": [
        _P(name="i–VI–VII–i", bar_degrees=(0, 0, 5, 5, 6, 6, 0, 0), weight=4, mood="liquid roll"),
        _P(name="i pedal (neuro)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=3, mood="reese tunnel"),
        _P(name="i–iv–VI–VII", bar_degrees=(0, 0, 3, 3, 5, 5, 6, 6), weight=3, mood="soulful drive"),
    ],
    "dubstep": [
        _P(name="i–bII wobble", bar_degrees=(0, 0, 1, 1, 0, 0, 1, 1), weight=4, mood="lurching menace"),
        _P(name="i–VI–i–VII", bar_degrees=(0, 0, 5, 5, 0, 0, 6, 6), weight=3, mood="half-time swell"),
    ],
    "garage": [
        _P(name="i7–IV7 shuffle", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=4, mood="2-step slink"),
        _P(name="i–iii–IV–i", bar_degrees=(0, 0, 2, 2, 3, 3, 0, 0), weight=3, mood="garage soul"),
    ],
    # --- Hip-hop lineage ---
    "lofi": [
        _P(name="i7–IV7 (dorian dust)", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=5, mood="mellow loop"),
        _P(name="i–iii–IV–iii", bar_degrees=(0, 0, 2, 2, 3, 3, 2, 2), weight=3, mood="jazzy sway"),
        _P(name="i–v–IV–i", bar_degrees=(0, 0, 4, 4, 3, 3, 0, 0), weight=3, mood="rainy cadence"),
    ],
    "boombap": [
        _P(name="i–iv loop", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=5, mood="dusty cycle"),
        _P(name="i–VI–i–VII", bar_degrees=(0, 0, 5, 5, 0, 0, 6, 6), weight=3, mood="soul chop"),
        _P(name="i–VII–VI–v", bar_degrees=(0, 0, 6, 6, 5, 5, 4, 4), weight=2, mood="minor stroll"),
    ],
    "west_coast": [
        _P(name="i7–IV7 (g-funk vamp)", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=5, mood="laid-back bounce"),
        _P(name="i–VII–IV–i", bar_degrees=(0, 0, 6, 6, 3, 3, 0, 0), weight=3, mood="westside cruise"),
        _P(name="i pedal (one-chord)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=2, mood="pocket lock"),
    ],
    # Jay Cactus + Lollipopbeatz (Detroit): one short repeating phrase carries
    # the whole track — heavily weighted toward the pedal, not a progression.
    "detroit": [
        _P(name="i pedal (repetitive foundation)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=6, mood="one-phrase loop"),
        _P(name="i–iv–i–iv", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=2, mood="simple sway"),
    ],
    # --- Global / rhythm-first ---
    "reggaeton": [
        _P(name="i–VI–III–VII", bar_degrees=(0, 0, 5, 5, 2, 2, 6, 6), weight=5, mood="anthem dembow"),
        _P(name="i–iv–VI–V feel", bar_degrees=(0, 0, 3, 3, 5, 5, 4, 4), weight=3, mood="latin pulse"),
        _P(name="i–VII–VI–VII", bar_degrees=(0, 0, 6, 6, 5, 5, 6, 6), weight=3, mood="perreo pedal"),
    ],
    "dancehall": [
        _P(name="i–VI–VII–i", bar_degrees=(0, 0, 5, 5, 6, 6, 0, 0), weight=4, mood="island bounce"),
        _P(name="i–iv–i–VII", bar_degrees=(0, 0, 3, 3, 0, 0, 6, 6), weight=3, mood="riddim cycle"),
    ],
    "afrobeats": [
        _P(name="i7–IV7 (afro vamp)", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=5, mood="sunny sway"),
        _P(name="i–VII–IV–i", bar_degrees=(0, 0, 6, 6, 3, 3, 0, 0), weight=3, mood="log-line lilt"),
        _P(name="i–iii–IV–VII", bar_degrees=(0, 0, 2, 2, 3, 3, 6, 6), weight=2, mood="afro lift"),
    ],
    "amapiano": [
        _P(name="i7–IV7 (piano vamp)", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=5, mood="rolling warmth"),
        _P(name="i–iii–VII–i", bar_degrees=(0, 0, 2, 2, 6, 6, 0, 0), weight=3, mood="deep yano"),
    ],
    # --- Organic / band ---
    "funk": [
        _P(name="i7 vamp (one-chord funk)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=4, mood="pocket lock"),
        _P(name="i7–IV7", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=4, mood="strut vamp"),
        _P(name="i–iii–IV–iii", bar_degrees=(0, 0, 2, 2, 3, 3, 2, 2), weight=2, mood="slick climb"),
    ],
    "soul": [
        _P(name="i7–IV7 (church vamp)", bar_degrees=(0, 0, 3, 3, 0, 0, 3, 3), weight=4, mood="warm sway"),
        _P(name="i–iii–IV–v", bar_degrees=(0, 0, 2, 2, 3, 3, 4, 4), weight=3, mood="gospel climb"),
        _P(name="i–v–IV–i", bar_degrees=(0, 0, 4, 4, 3, 3, 0, 0), weight=3, mood="slow burn"),
    ],
    "rock": [
        _P(name="i–VI–VII–i (power)", bar_degrees=(0, 0, 5, 5, 6, 6, 0, 0), weight=5, mood="fist anthem"),
        _P(name="i–iv–VI–VII", bar_degrees=(0, 0, 3, 3, 5, 5, 6, 6), weight=3, mood="driving verse"),
        _P(name="i–VII–iv–i", bar_degrees=(0, 0, 6, 6, 3, 3, 0, 0), weight=3, mood="grunge slouch"),
    ],
    "cinematic": [
        _P(name="i–VI–III–VII (epic)", bar_degrees=(0, 0, 5, 5, 2, 2, 6, 6), weight=5, mood="heroic swell"),
        _P(name="i–iv–i–v", bar_degrees=(0, 0, 3, 3, 0, 0, 4, 4), weight=3, mood="dark tension"),
        _P(name="i pedal (drone)", bar_degrees=(0, 0, 0, 0, 0, 0, 0, 0), weight=2, mood="brooding floor"),
    ],
}

# ---------------------------------------------------------------------------
# Genre BPM ranges (inclusive) — the pocket starts with the right tempo.
# ---------------------------------------------------------------------------

BPM_RANGES: dict[str, tuple[int, int]] = {
    "trap": (130, 155),
    "toliver": (130, 136),
    "kanye": (76, 88),
    # Dirkey "How To Make BEATS For LIL UZI VERT (Eternal Atake 2)" ran a
    # deliberately atypical 133 BPM for that album's experimental sound —
    # widened the floor from 140 so it's in the normal randomized pool,
    # not just Uzi's ceiling raised; the rest of the rage family (Yeat,
    # XXXTentacion, Ken Carson) still lands in range most of the time.
    "rage": (132, 165),
    "opium": (130, 152),
    # Praxi Plays (3rd Drift Phonk source): confirmed real tempo range 140-160
    # — widened from (120, 142) rather than shifting the floor, since other
    # phonk substyles this session's corpus already covers sit lower.
    "phonk": (120, 160),
    "drill": (138, 148),
    # Dirkey "How To Make Underground Beats" — 162 BPM was the video's own
    # tempo, at the fast end of the trap family. Floor widened to 148 so
    # Nettspend's 150 BPM ("Early Life Crisis") lands in the normal pool too
    # (the 70-90 half-time alternative is a different tempo bracket entirely —
    # not merged in here, would dilute the range for every other prompt).
    "underground": (148, 170),
    # Minimal dark trap — written literally at the practical ~110 BPM grid
    # (the "55 BPM" the video names is a half-time reading of the same tempo).
    "minimal_trap": (100, 116),
    "pluggnb": (136, 145),
    # Felix (100 gecs-style tutorial): 183 BPM confirmed for the genre — widened
    # from the old (150, 170) with headroom rather than a bare single-point fit.
    "hyperpop": (150, 190),
    "jersey": (130, 145),
    "kpop": (100, 128),
    "rnb": (85, 108),
    "pop": (95, 125),
    "dark_pop": (95, 118),
    "disco_pop": (105, 122),
    "edm": (124, 130),
    "grind": (160, 200),
    "pop_rap": (128, 148),
    "house": (120, 126),
    "techno": (126, 136),
    "trance": (132, 140),
    "hardstyle": (150, 160),
    "dnb": (170, 176),
    "dubstep": (138, 142),
    "garage": (130, 138),
    "lofi": (72, 92),
    "boombap": (85, 98),
    "west_coast": (90, 104),
    "detroit": (100, 105),
    "reggaeton": (88, 100),
    "dancehall": (95, 105),
    "afrobeats": (100, 115),
    "amapiano": (110, 118),
    "funk": (95, 112),
    "soul": (82, 100),
    "rock": (110, 145),
    "cinematic": (90, 130),
}

# ---------------------------------------------------------------------------
# Drum-pocket cells — 2 bars (8 beats), used by the deterministic fallback so a
# failed LLM stage still lands an authentic genre grid instead of a generic one.
# ---------------------------------------------------------------------------

DRUM_CELLS: dict[str, list[dict[str, Any]]] = {
    "kanye": [
        # live soul-flip drums: swung, dusty, backbeat 2 & 4 with pickups
        _P(name="soul backbeat", kick=(0.0, 2.5, 4.0, 6.0), snare=(2.0, 6.0)),
        _P(name="chop bounce", kick=(0.0, 1.5, 4.0, 5.5, 7.0), snare=(2.0, 6.0)),
    ],
    "toliver": [
        # drums are almost an apology — sparse, floating half-time
        _P(name="floating half-time", kick=(0.0, 4.0, 6.75), snare=(2.0, 6.0)),
        _P(name="lazy drift", kick=(0.0, 3.5, 4.0), snare=(2.0, 6.0)),
    ],
    "trap": [
        _P(name="classic half-time", kick=(0.0, 2.75, 4.0, 6.5), snare=(2.0, 6.0)),
        _P(name="rolling bounce", kick=(0.0, 1.5, 4.0, 5.5, 7.5), snare=(2.0, 6.0)),
        _P(name="sparse knock", kick=(0.0, 3.5, 4.0, 7.0), snare=(2.0, 6.0)),
        _P(name="double-tap", kick=(0.0, 0.75, 4.0, 4.75, 6.5), snare=(2.0, 6.0)),
    ],
    "opium": [
        _P(name="ritual space", kick=(0.0, 3.0, 4.0, 6.75), snare=(2.0, 6.0)),
        _P(name="pluggy stumble", kick=(0.0, 1.75, 4.5, 6.0), snare=(2.0, 6.0)),
    ],
    "rage": [
        _P(name="stomping eighths", kick=(0.0, 1.0, 3.5, 4.0, 5.0, 7.5), snare=(2.0, 6.0)),
        _P(name="offbeat rage", kick=(0.0, 1.75, 2.5, 4.0, 5.75, 6.5), snare=(2.0, 6.0)),
    ],
    "drill": [
        _P(name="uk slide", kick=(0.0, 1.75, 4.5, 5.25), snare=(3.0, 7.0)),
        _P(name="ny bounce", kick=(0.0, 2.5, 4.0, 6.75), snare=(3.0, 7.0)),
    ],
    "underground": [
        _P(name="heavy hitter", kick=(0.0, 1.0, 3.5, 4.0, 5.0, 7.5), snare=(2.0, 6.0)),
    ],
    "minimal_trap": [
        _P(name="bare minimum", kick=(0.0, 4.0), snare=(2.0, 6.0)),
    ],
    "pluggnb": [
        _P(name="subdued float", kick=(0.0, 3.5, 4.0), snare=(2.0, 6.0)),
        _P(name="mellow bounce", kick=(0.0, 1.75, 4.0, 5.75), snare=(2.0, 6.0)),
    ],
    "phonk": [
        _P(name="memphis knock", kick=(0.0, 1.0, 4.0, 5.0), snare=(2.0, 6.0)),
        _P(name="drift gallop", kick=(0.0, 1.5, 2.0, 4.0, 5.5, 6.0), snare=(2.0, 6.0)),
    ],
    "jersey": [
        _P(name="club 5-kick", kick=(0.0, 0.75, 1.5, 2.5, 3.25, 4.0, 4.75, 5.5, 6.5, 7.25), snare=(2.0, 6.0)),
    ],
    "edm": [
        _P(name="four-on-floor", kick=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "disco_pop": [
        _P(name="four-on-floor funk", kick=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "pop": [
        _P(name="pop backbeat", kick=(0.0, 2.5, 4.0, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "kpop": [
        _P(name="idol backbeat", kick=(0.0, 2.5, 4.0, 5.5, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "rnb": [
        _P(name="silk pocket", kick=(0.0, 2.75, 4.5, 6.75), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "dark_pop": [
        _P(name="noir backbeat", kick=(0.0, 3.5, 4.0, 7.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "hyperpop": [
        _P(name="burst grid", kick=(0.0, 1.0, 2.0, 3.5, 4.0, 5.0, 6.0, 7.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "grind": [
        _P(name="blast seed", kick=(0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5), snare=(0.25, 0.75, 1.25, 1.75, 2.25, 2.75, 3.25, 3.75, 4.25, 4.75, 5.25, 5.75, 6.25, 6.75, 7.25, 7.75)),
    ],
    "pop_rap": [
        _P(name="club knock", kick=(0.0, 1.75, 4.0, 5.75, 6.5), snare=(2.0, 6.0)),
        _P(name="bounce strut", kick=(0.0, 0.75, 2.5, 4.0, 4.75, 6.5), snare=(2.0, 6.0)),
    ],
    "house": [
        _P(name="four-on-floor deep", kick=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "techno": [
        _P(name="warehouse 4x4", kick=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "trance": [
        _P(name="uplift 4x4", kick=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "hardstyle": [
        _P(name="raw 4x4", kick=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "dnb": [
        _P(name="two-step break", kick=(0.0, 2.5, 4.0, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
        _P(name="rolling break", kick=(0.0, 2.5, 3.75, 4.0, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "dubstep": [
        _P(name="half-time lurch", kick=(0.0, 3.75, 4.0), snare=(2.0, 6.0)),
    ],
    "garage": [
        _P(name="2-step shuffle", kick=(0.0, 2.5, 4.0, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "lofi": [
        _P(name="dusty sway", kick=(0.0, 2.75, 4.0, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "boombap": [
        _P(name="mpc knock", kick=(0.0, 1.75, 4.0, 5.5, 6.25), snare=(1.0, 3.0, 5.0, 7.0)),
        _P(name="head-nod", kick=(0.0, 2.5, 4.0, 4.75, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "west_coast": [
        _P(name="westside bounce", kick=(0.0, 1.5, 2.5, 4.0, 5.5, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "detroit": [
        _P(name="detroit half-time", kick=(0.0, 3.5, 4.0), snare=(2.0, 6.0)),
    ],
    "reggaeton": [
        _P(name="dembow", kick=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0), snare=(0.75, 1.5, 2.75, 3.5, 4.75, 5.5, 6.75, 7.5)),
    ],
    "dancehall": [
        _P(name="riddim 3+3+2", kick=(0.0, 0.75, 1.5, 4.0, 4.75, 5.5), snare=(2.0, 6.0)),
    ],
    "afrobeats": [
        _P(name="afro tresillo", kick=(0.0, 1.5, 2.5, 4.0, 5.5, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "amapiano": [
        _P(name="yano pocket", kick=(0.0, 3.5, 4.0, 7.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "funk": [
        _P(name="one-and pocket", kick=(0.0, 0.75, 2.5, 4.0, 4.75, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "soul": [
        _P(name="motown sway", kick=(0.0, 2.5, 4.0, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "rock": [
        _P(name="backbeat drive", kick=(0.0, 2.5, 4.0, 6.5), snare=(1.0, 3.0, 5.0, 7.0)),
    ],
    "cinematic": [
        _P(name="epic half-time", kick=(0.0, 4.0), snare=(2.0, 6.0)),
    ],
}

# ---------------------------------------------------------------------------
# Production recipes — short idiom lines fed to the architect (no artist names).
# ---------------------------------------------------------------------------

RECIPES: dict[str, tuple[str, ...]] = {
    "kanye": (
        "Sample-flip soul: chopped warm vintage chords are the CORE, not a synth.",
        "Dusty gospel stack (7ths/9ths), tape echo, brass stabs answering the chops.",
        "Swung live-feel drums, gritty slow 808 on the roots; nothing digital-clean.",
    ),
    "toliver": (
        "Bass is the anchor: warm sustained roots FIRST, drums soft in the back.",
        "Glide pad rises into place and hovers — the floating feel is the hook.",
        "Bell/harp lead in call-and-response; reverb tails on every melodic voice.",
    ),
    "trap": (
        "808 walks the progression roots bar by bar; glide into bars 3/5/7.",
        "Hook = short dark motif that loops; leave beats 3.5–4 of even bars open for vocals.",
        "Hats live in the pocket: 1/8 base, 1/16 fills, 1/32 rolls only before transitions.",
    ),
    "opium": (
        "Bell/pluck hook with minor-2nd tension (phrygian rub) and long empty space.",
        "Half-time drums; the 808 is the melody's shadow — sustain until the next kick.",
        "Ear candy: reversed crash into bar 5, one octave-up echo of the hook per 8 bars.",
    ),
    "rage": (
        "Sawy pluck stabs in call-and-response; aggressive but repetitive hook.",
        "Kick doubles as pulse — off-beat pushes, no four-on-floor.",
        "Keep rolls sparse; energy comes from the bII rub and velocity waves.",
    ),
    "drill": (
        "Sliding 808s: glide between roots inside the bar, land clean on beat 1.",
        "Snare on beat 4 feel (3.0/7.0 in the 2-bar cell); ghost kicks between.",
        "Dark piano/strings motif in short phrases with rests (UK shadow).",
    ),
    "underground": (
        "Heavy-hitting 808 IS the record — clip-driven crunch on purpose, not a mixing accident.",
        "Same preset doubled across the melodic layers, varied by octave/pitch, not separate sounds.",
        "Fast (~160 BPM) but the drums stay simple — the crunch and the hook carry it.",
    ),
    "minimal_trap": (
        "Slow (~110 BPM felt as half-time), sparse, nocturnal — one idea, lots of empty space.",
        "One preset pool for every melodic layer; a quiet octave-up shimmer, not a loud double.",
        "A single occasional atmosphere hit (night-adjacent texture) sells the scene, not a wall of ear candy.",
    ),
    "pluggnb": (
        "Melody leads, 808 follows: quiet, sparse sub — let the hook take the wheel.",
        "Counter-melody is a bell-toned octave-up double of the hook, own preset, not a loud copy.",
        "One clap per 2-bar cell lands a quarter-beat late — the pocket's signature wobble.",
    ),
    "phonk": (
        "Dusty cowbell/lead loop over a memphis pocket; keep it hypnotic.",
        "808 distorted, rides the root; occasional octave stab.",
    ),
    "jersey": (
        "Signature 5-kick club cell drives everything; vocal chops as percussion.",
        "Keep harmony simple — the kick pattern is the hook.",
    ),
    "edm": (
        "Four-on-floor with off-beat bass; supersaw lead states the progression as chords.",
        "Snare riser + white-noise sweep into every drop.",
    ),
    "rnb": (
        "Dorian vamp, extended chords (add 7ths/9ths in voicings), lazy pocket.",
        "Melody sits behind the beat; keep drums soft and round.",
    ),
    "pop": (
        "Clear verse/chorus energy contrast; chorus melody peaks higher than verse.",
        "Four-chord anthem loop; keep the top-line singable (stepwise + one leap).",
    ),
    "kpop": (
        "Bright synth stack states the chords; percussion layers add energy per section.",
        "Chorus = biggest melodic hook, verse = rhythmic restraint.",
    ),
    "dark_pop": (
        "Analog synth pads state minor-9th colour; melody wide and slow.",
        "Drums are a heartbeat, not a show — space is the aesthetic.",
    ),
    "disco_pop": (
        "Syncopated funk bass walks the vamp; off-beat open hats.",
        "Keep chords bright (dorian IV), strings/stab accents on turnarounds.",
    ),
    "hyperpop": (
        "Detuned supersaw hook, octave jumps, pitch bends; loud and glittery.",
        "Contrast: fragile verse (few elements) vs. exploding chorus.",
    ),
    "grind": (
        "Blast pulse, no melody hierarchy — texture is the instrument.",
    ),
    "pop_rap": (
        "Bouncy club 808s walk the changes; leave clean pockets for chant hooks.",
        "Bright playful stabs (brass/pluck/piano) answer the vocal every 2 bars.",
        "Chorus doubles the energy: extra percussion layer + hook melody up an octave.",
    ),
    "house": (
        "Four-on-floor kick, open hats on the off-beats, bass ducks under the kick.",
        "Chords are the hook: warm 7th stabs on the vamp, one filtered break per 8 bars.",
    ),
    "techno": (
        "Hypnotic pedal harmony — movement comes from filter/percussion, not chords.",
        "Rumble kick + off-beat ride; keep melodic content minimal and dark.",
    ),
    "trance": (
        "Rolling 16th arpeggios state the progression; supersaw lead saves the anthem for the drop.",
        "Snare riser + sweep into every drop; breakdown strips to pads and arp.",
    ),
    "hardstyle": (
        "The kick IS the bass: pitched kick tail carries the root note.",
        "Screech lead in call-and-response; reverse crash before every drop.",
    ),
    "dnb": (
        "Two-step break at full tempo; ghost snares fill the pocket.",
        "Long reese/sub notes move with the progression — energy lives in the drums.",
    ),
    "dubstep": (
        "Half-time lurch: kick on 1, snare on 3, space in between for bass movement.",
        "Wobble/growl bass answers the lead — call and response every 2 bars.",
    ),
    "garage": (
        "Shuffled 2-step: swung 16th hats, clipped vocal chops as percussion.",
        "Warm organ/keys stabs on the off-beats; bassline slinks around the kick.",
    ),
    "lofi": (
        "Dusty lazy pocket — everything drags slightly behind the grid.",
        "Jazzy 7th chords, vinyl noise, mellow keys; keep the top-line understated.",
    ),
    "boombap": (
        "MPC swing on the kicks, cracking snare on 2 & 4, chopped sample loop feel.",
        "Bassline walks the loop roots; keep hats simple 8ths with velocity dips.",
    ),
    "west_coast": (
        "Laid-back bounce: syncopated kick answers a whiny dorian lead, minimal reverb keeps it gritty.",
        "808 walks the vamp under a piano/synth lead — G-funk sensibility, modern low end.",
    ),
    "detroit": (
        "One short repeating phrase carries the whole track — resist adding a second idea.",
        "808 tuned a bit higher (F/F#/G area) hits harder than the usual deep sub.",
        "3-4 layers max: melody, muted-top octave double, strings, bass — don't overcomplicate.",
    ),
    "reggaeton": (
        "Dembow snare (3+3+2) drives everything over a four-on-floor kick.",
        "Dark plucks/marimba state the hook; leave space for the vocal flow.",
    ),
    "dancehall": (
        "3+3+2 kick riddim, rim-shot accents, sparse dark keys.",
        "One melodic riff loops; percussion answers it every 2 bars.",
    ),
    "afrobeats": (
        "Tresillo kick + rim on 2 & 4; layered shakers and congas carry the groove.",
        "Warm log/pluck bass sings the vamp; melody is short call-and-response phrases.",
    ),
    "amapiano": (
        "Log drum bass IS the signature: syncopated staccato stabs walking the vamp.",
        "Soft piano chords + shaker grid; huge space, patient 8-bar development.",
    ),
    "funk": (
        "Syncopated bass locks with the kick 'one'; clav/wah guitar chops the off-beats.",
        "Horn-style stabs punctuate every 4 bars; groove over harmony.",
    ),
    "soul": (
        "Extended chords (7ths/9ths) on keys, lazy backbeat, warm round bass.",
        "Melody sits behind the beat with gospel-style runs at phrase ends.",
    ),
    "rock": (
        "Straight backbeat drums, power-chord roots follow the progression.",
        "Riff = the hook: short aggressive motif doubled an octave down.",
    ),
    "cinematic": (
        "Epic half-time hits, string ostinato states the progression, brass swells at climaxes.",
        "Build in layers every 8 bars: percussion → ostinato → full orchestra.",
    ),
}

_DEFAULT_GENRE = "trap"

# ---------------------------------------------------------------------------
# Bassline cells for non-808 genres — (beat_offset_in_bar, length, octave_shift).
# Used when the LLM left the bass lane empty: the progression + an idiomatic
# rhythm cell = a real bassline instead of silence.
# ---------------------------------------------------------------------------

BASSLINE_CELLS: dict[str, tuple[tuple[float, float, int], ...]] = {
    # off-beat 8ths — the classic pumping club bass
    "house": ((0.5, 0.45, 0), (1.5, 0.45, 0), (2.5, 0.45, 0), (3.5, 0.45, 0)),
    "techno": ((0.5, 0.4, 0), (1.5, 0.4, 0), (2.5, 0.4, 0), (3.5, 0.4, 0)),
    "trance": ((0.5, 0.45, 0), (1.5, 0.45, 0), (2.5, 0.45, 0), (3.5, 0.45, 0)),
    "hardstyle": ((0.5, 0.45, 0), (1.5, 0.45, 0), (2.5, 0.45, 0), (3.5, 0.45, 0)),
    "edm": ((0.5, 0.45, 0), (1.5, 0.45, 0), (2.5, 0.45, 0), (3.5, 0.45, 0)),
    "garage": ((0.0, 0.6, 0), (1.75, 0.35, 0), (2.5, 0.6, 0), (3.25, 0.3, 12)),
    "dnb": ((0.0, 1.9, 0), (2.0, 1.9, 0)),
    "dubstep": ((0.0, 1.9, 0), (2.5, 1.4, 0)),
    "amapiano": ((0.75, 0.3, 0), (1.5, 0.25, 0), (2.25, 0.35, 0), (3.0, 0.25, 0), (3.75, 0.2, 12)),
    "funk": ((0.0, 0.4, 0), (0.75, 0.2, 0), (1.5, 0.35, 0), (2.25, 0.2, 12), (3.0, 0.4, 0), (3.5, 0.3, 0)),
    "soul": ((0.0, 0.9, 0), (2.0, 0.9, 0), (3.5, 0.4, 0)),
    "disco_pop": ((0.0, 0.4, 0), (0.5, 0.4, 12), (1.0, 0.4, 0), (1.5, 0.4, 12), (2.0, 0.4, 0), (2.5, 0.4, 12), (3.0, 0.4, 0), (3.5, 0.4, 12)),
    "rock": tuple((i * 0.5, 0.45, 0) for i in range(8)),
    "cinematic": ((0.0, 3.9, 0),),
    "grind": (),
}


def synthesize_bassline(genre: str, roots_pc: list[int], bars: int) -> list[dict[str, Any]]:
    """Idiomatic bassline from the pinned progression for genres without an 808.

    Returns [] when the genre has no cell (caller keeps the lane empty).
    """
    cell = BASSLINE_CELLS.get(genre)
    if not cell or not roots_pc:
        return []
    notes: list[dict[str, Any]] = []
    for bar in range(max(1, bars)):
        pc = int(roots_pc[bar % len(roots_pc)]) % 12
        base_midi = _SUB_LO + ((pc - _SUB_LO) % 12)
        for offset, length, oct_shift in cell:
            notes.append(
                {
                    "time_step": round(bar * 4.0 + offset, 5),
                    "note": name_for_midi(base_midi + oct_shift),
                    "length": round(length, 5),
                    "velocity": 112 if offset == 0.0 else 102,
                    "bassline_gen": True,
                }
            )
    return notes


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def _rng(genre: str, seed: int | None) -> random.Random:
    mix = f"plg-style-corpus:{genre}:{seed if seed is not None else 0}"
    digest = hashlib.sha256(mix.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _root_pc_from_key(key: str) -> int:
    token = (key or "").strip().split()[0] if (key or "").strip() else "A"
    token = token[:1].upper() + token[1:2]  # "f#" -> "F#", "db" -> "Db"
    pc = NOTE_INDEX.get(token, NOTE_INDEX.get(token[:1], 9))
    return pc % 12


def normalize_scale(scale: str) -> str:
    name = (scale or "").strip().lower().replace(" ", "_")
    if name in SCALES:
        return name
    if name in ("minor", "aeolian", "natural"):
        return "natural_minor"
    if name in ("harmonic", "harmonicminor"):
        return "harmonic_minor" if "harmonic_minor" in SCALES else "natural_minor"
    return "natural_minor"


def bpm_range(genre: str) -> tuple[int, int]:
    return BPM_RANGES.get(genre, BPM_RANGES[_DEFAULT_GENRE])


def _weighted_choice(
    rng: random.Random,
    options: list[dict[str, Any]],
    *,
    boost: dict[str, float] | None = None,
) -> dict[str, Any]:
    weights = [
        max(1, int(opt.get("weight", 1))) * float((boost or {}).get(str(opt.get("name")), 1.0))
        for opt in options
    ]
    return rng.choices(options, weights=weights, k=1)[0]


_FLEET_MIN_VOTES = 5


def _fleet_progression_boost(genre: str) -> dict[str, float]:
    """Multipliers from the fleet's 👍/👎 per progression (0.75×–1.45×).

    The curated bank stays authoritative; the fleet only *tilts* the odds, so a
    few early votes can't collapse variety."""
    try:
        from beat_reward import fleet_reward_enabled, global_progression_stats

        if not fleet_reward_enabled():
            return {}
        stats = global_progression_stats(genre)
    except Exception:  # noqa: BLE001 — fleet must never break generation
        return {}
    boost: dict[str, float] = {}
    for name, row in stats.items():
        try:
            votes = int(row.get("votes", 0))
            win_rate = float(row.get("win_rate", 0.5))
        except (TypeError, ValueError):
            continue
        if votes >= _FLEET_MIN_VOTES:
            boost[name] = 0.75 + 0.7 * max(0.0, min(1.0, win_rate))
    return boost


def _triad_pcs(root_pc: int, degree: int, intervals: tuple[int, ...]) -> list[int]:
    size = len(intervals)
    pcs: list[int] = []
    for stack in (0, 2, 4):  # stacked thirds within the scale
        step = (degree + stack) % size
        octave_lift = (degree + stack) // size
        pcs.append((root_pc + intervals[step] + 12 * octave_lift) % 12)
    return pcs


def style_dna(
    genre: str,
    *,
    key: str = "A",
    scale: str = "natural_minor",
    seed: int | None = None,
    bars: int = _BARS_DEFAULT,
) -> dict[str, Any]:
    """Deterministic musical foundation for one generation."""
    genre = genre if genre in PROGRESSIONS else _DEFAULT_GENRE
    scale_name = normalize_scale(scale)
    intervals = SCALES.get(scale_name, SCALES["natural_minor"])
    root_pc = _root_pc_from_key(key)
    rng = _rng(genre, seed)

    prog = _weighted_choice(rng, PROGRESSIONS[genre], boost=_fleet_progression_boost(genre))
    base_degrees = list(prog["bar_degrees"])
    bar_degrees = [base_degrees[i % len(base_degrees)] for i in range(max(1, bars))]
    size = len(intervals)
    roots_pc = [(root_pc + intervals[d % size]) % 12 for d in bar_degrees]
    chord_tones_pc = [_triad_pcs(root_pc, d % size, intervals) for d in bar_degrees]

    cells = DRUM_CELLS.get(genre) or DRUM_CELLS[_DEFAULT_GENRE]
    cell = _weighted_choice(rng, cells)

    return {
        "genre": genre,
        "key": NOTE_NAMES[root_pc],
        "scale": scale_name,
        "root_pc": root_pc,
        "label": key_label(root_pc, scale_name),
        "progression": {
            "name": str(prog["name"]),
            "mood": str(prog.get("mood", "")),
            "bar_degrees": bar_degrees,
            "roots_pc": roots_pc,
            "chord_tones_pc": chord_tones_pc,
        },
        "drum_cell": {
            "name": str(cell["name"]),
            "kick": list(cell["kick"]),
            "snare": list(cell["snare"]),
            "bar_len": 2,
        },
        "recipe": list(RECIPES.get(genre, ())),
        "seed": seed,
    }


def _sub_note_name(pc: int) -> str:
    return name_for_midi(_SUB_LO + ((pc - _SUB_LO) % 12))


def dna_addendum(dna: dict[str, Any]) -> str:
    """Prompt block for the architect / single-stage LLM."""
    prog = dna.get("progression") or {}
    roots_pc = list(prog.get("roots_pc") or [])
    root_names = [_sub_note_name(int(pc)) for pc in roots_pc]
    cell = dna.get("drum_cell") or {}
    lines = [
        "[PLG STYLE DNA — proven foundation. Realize it with taste; do NOT reinvent it:]",
        f"- Lane: {str(dna.get('genre', '')).replace('_', ' ')} — this genre's pocket and palette, no drifting.",
        f"- Harmony: {dna.get('label')} — progression {prog.get('name')} ({prog.get('mood')}).",
        f"- 808 roots bar-by-bar: {', '.join(root_names)}. The hook loops over the tonic; the 808 walks these changes.",
        f"- Melody: emphasize chord tones on bar starts, stay in {dna.get('scale')}; contour repeats with ONE variation per 4 bars.",
        f"- Drum pocket reference: '{cell.get('name')}' feel (2-bar cell), keep the backbeat honest.",
    ]
    for tip in (dna.get("recipe") or [])[:3]:
        lines.append(f"- {tip}")
    return "\n".join(lines)


def dna_for_context(ctx: dict[str, Any], prompt: str) -> dict[str, Any]:
    """DNA for the generation context (same derivation as attach_progression)."""
    genre = ctx.get("genre")
    if not isinstance(genre, str) or genre not in PROGRESSIONS:
        from genre_profiles import detect_genre

        genre = detect_genre("", prompt)
    return style_dna(
        genre,
        key=str(ctx.get("key") or "A"),
        scale=str(ctx.get("scale") or "natural_minor"),
        seed=ctx.get("seed") if isinstance(ctx.get("seed"), int) else None,
    )


def attach_progression(
    pattern: dict[str, Any],
    *,
    root_pc: int | None = None,
    scale: str | None = None,
) -> dict[str, Any] | None:
    """Compute the same DNA from pattern metadata and pin it as ``plg_progression``.

    The 808 engines (sub_808_gen / compose_kick_locked_808) read this to walk
    the changes instead of droning on the melody's single root. Pass ``root_pc``
    (e.g. detected from the final melody) so the walk anchors to the music that
    actually got composed, not just the requested key.
    """
    if not corpus_enabled():
        return None
    if isinstance(pattern.get("plg_progression"), dict):
        return pattern["plg_progression"]

    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    seed = variety.get("seed") if isinstance(variety.get("seed"), int) else None
    key = str(variety.get("key") or pattern.get("key") or "A")
    if root_pc is not None:
        key = NOTE_NAMES[int(root_pc) % 12]
    scale_name = str(scale or variety.get("scale") or pattern.get("scale") or "natural_minor")
    dna = style_dna(
        _genre_for_pattern(pattern),
        key=key,
        scale=scale_name,
        seed=seed,
    )
    prog = dict(dna["progression"])
    prog["genre"] = dna["genre"]
    prog["key"] = dna["key"]
    prog["scale"] = dna["scale"]
    prog["label"] = dna["label"]
    prog["seed"] = seed
    pattern["plg_progression"] = prog
    logger.info(
        "Style DNA: %s %s — %s (roots %s)",
        dna["label"],
        dna["genre"],
        prog["name"],
        ",".join(NOTE_NAMES[pc] for pc in prog["roots_pc"]),
    )
    return prog


def _genre_for_pattern(pattern: dict[str, Any]) -> str:
    # The brain's lane (pinned into plg_variety by the generation context) wins;
    # prompt-only keyword detection is the offline fallback — either way the
    # prompt addendum and post-LLM enforcement agree.
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    genre = variety.get("genre")
    if isinstance(genre, str) and genre in PROGRESSIONS:
        return genre
    from genre_profiles import detect_genre

    return detect_genre("", str(pattern.get("user_prompt", "")))


# ---------------------------------------------------------------------------
# Mix DNA — per-genre insert-FX character (native FL plugins, plugin-free math).
# sub_boost_db drives Fruity Blood Overdrive on the 808 bus (the rage/opium
# "дисторшн"); wet/decay shape Fruity Reeverb 2 on the melody bus; lpf_hz keeps
# lofi dark and rage bright; sidechain is the pump depth when the architect
# stays silent. Flags choose which chains actually load, so clean genres stay
# clean (the old all-off default remains for unknown lanes).
# ---------------------------------------------------------------------------

MIX_RECIPES: dict[str, dict[str, Any]] = {
    # --- aggressive: hot 808 drive, tight dry space ---
    "rage": _P(sidechain=0.5, sub_drive=True, sub_boost_db=7.0, melody_space=True, wet=22.0, decay=1.8, lpf_hz=7500.0),
    "opium": _P(sidechain=0.55, sub_drive=True, sub_boost_db=6.0, melody_space=True, wet=45.0, decay=3.4, lpf_hz=6000.0),
    # trap = the default lane: 808 drive ON (no tail, pure punch) but melody
    # reverb/delay OFF — long tails on the default caused muted-playback
    # complaints (see mix_insert_fx.space_mix_enabled). Space lanes opt in below.
    # Tecca/Travis iron sidechain: depth 0.3 = 808 sits down 30% under the kick.
    "trap": _P(sidechain=0.3, sub_drive=True, sub_boost_db=4.0, melody_space=False, wet=35.0, decay=2.5, lpf_hz=5000.0),
    "pop_rap": _P(sidechain=0.3, sub_drive=True, sub_boost_db=3.5, melody_space=True, wet=28.0, decay=1.9, lpf_hz=6500.0),
    # Don Toliver: floating — big wet tails on every melodic voice, 808 standard
    "toliver": _P(sidechain=0.4, sub_drive=True, sub_boost_db=3.0, melody_space=True, wet=48.0, decay=3.2, lpf_hz=5200.0),
    # Kanye: warm & dusty — gentle glue, dark tops (sampled record), soft 808
    "kanye": _P(sidechain=0.25, sub_drive=True, sub_boost_db=2.5, melody_space=True, wet=30.0, decay=2.0, lpf_hz=3800.0),
    "drill": _P(sidechain=0.45, sub_drive=True, sub_boost_db=4.5, melody_space=True, wet=30.0, decay=2.2, lpf_hz=5500.0),
    # Dirkey "Underground Beats": the crunchy 808 is the point — highest
    # sub_boost_db in the whole table, on purpose.
    "underground": _P(sidechain=0.5, sub_drive=True, sub_boost_db=7.5, melody_space=True, wet=20.0, decay=1.6, lpf_hz=6500.0),
    # Minimal dark trap — sparse, nocturnal: low sidechain pump (nothing to
    # duck against in an empty arrangement), dark/muffled top end, long decay.
    "minimal_trap": _P(sidechain=0.2, sub_drive=False, sub_boost_db=1.5, melody_space=True, wet=38.0, decay=2.8, lpf_hz=3200.0),
    "pluggnb": _P(sidechain=0.25, sub_drive=False, sub_boost_db=1.0, melody_space=True, wet=36.0, decay=2.4, lpf_hz=5500.0),
    "phonk": _P(sidechain=0.5, sub_drive=True, sub_boost_db=6.5, melody_space=True, wet=26.0, decay=1.6, lpf_hz=4200.0),
    "hyperpop": _P(sidechain=0.65, sub_drive=True, sub_boost_db=5.0, melody_space=True, wet=30.0, decay=1.8, lpf_hz=9000.0),
    "dubstep": _P(sidechain=0.6, sub_drive=True, sub_boost_db=5.5, melody_space=True, wet=28.0, decay=2.0, lpf_hz=7000.0),
    "hardstyle": _P(sidechain=0.7, sub_drive=True, sub_boost_db=6.5, melody_space=False, wet=25.0, decay=1.5, lpf_hz=8000.0),
    "grind": _P(sidechain=0.3, sub_drive=True, sub_boost_db=7.0, melody_space=False, wet=20.0, decay=1.2, lpf_hz=8000.0),
    "rock": _P(sidechain=0.25, sub_drive=True, sub_boost_db=5.0, melody_space=False, wet=22.0, decay=1.4, lpf_hz=7500.0),
    # --- pump: the kick breathes through everything ---
    "house": _P(sidechain=0.9, sub_drive=False, sub_boost_db=1.5, melody_space=True, wet=30.0, decay=2.0, lpf_hz=6000.0),
    "techno": _P(sidechain=0.85, sub_drive=True, sub_boost_db=3.0, melody_space=True, wet=32.0, decay=2.6, lpf_hz=5500.0),
    "trance": _P(sidechain=0.9, sub_drive=False, sub_boost_db=1.0, melody_space=True, wet=38.0, decay=2.8, lpf_hz=8500.0),
    "edm": _P(sidechain=0.85, sub_drive=False, sub_boost_db=1.5, melody_space=True, wet=35.0, decay=2.4, lpf_hz=8500.0),
    "disco_pop": _P(sidechain=0.8, sub_drive=False, sub_boost_db=1.0, melody_space=True, wet=26.0, decay=1.6, lpf_hz=7500.0),
    "jersey": _P(sidechain=0.7, sub_drive=True, sub_boost_db=3.0, melody_space=False, wet=24.0, decay=1.4, lpf_hz=6500.0),
    "garage": _P(sidechain=0.65, sub_drive=False, sub_boost_db=1.5, melody_space=True, wet=28.0, decay=1.8, lpf_hz=6000.0),
    "dnb": _P(sidechain=0.5, sub_drive=True, sub_boost_db=4.0, melody_space=True, wet=26.0, decay=1.6, lpf_hz=7000.0),
    # --- warm / organic: gentle glue, darker tops ---
    "lofi": _P(sidechain=0.35, sub_drive=False, sub_boost_db=1.0, melody_space=True, wet=40.0, decay=1.6, lpf_hz=3200.0),
    "boombap": _P(sidechain=0.25, sub_drive=True, sub_boost_db=2.5, melody_space=True, wet=20.0, decay=1.2, lpf_hz=4200.0),
    # Deon/Infinite: minimal reverb keeps the gritty West Coast character —
    # deliberately lower wet than every other hip-hop-lineage genre here.
    "west_coast": _P(sidechain=0.3, sub_drive=True, sub_boost_db=2.0, melody_space=True, wet=16.0, decay=1.1, lpf_hz=5500.0),
    "detroit": _P(sidechain=0.3, sub_drive=True, sub_boost_db=2.5, melody_space=True, wet=18.0, decay=1.3, lpf_hz=5000.0),
    "rnb": _P(sidechain=0.35, sub_drive=False, sub_boost_db=1.0, melody_space=True, wet=34.0, decay=2.2, lpf_hz=5000.0),
    "soul": _P(sidechain=0.2, sub_drive=False, sub_boost_db=0.5, melody_space=True, wet=30.0, decay=1.8, lpf_hz=5200.0),
    "funk": _P(sidechain=0.3, sub_drive=False, sub_boost_db=1.5, melody_space=False, wet=22.0, decay=1.3, lpf_hz=6500.0),
    "reggaeton": _P(sidechain=0.55, sub_drive=True, sub_boost_db=3.0, melody_space=True, wet=26.0, decay=1.7, lpf_hz=6500.0),
    "dancehall": _P(sidechain=0.5, sub_drive=True, sub_boost_db=3.0, melody_space=True, wet=26.0, decay=1.7, lpf_hz=6500.0),
    "afrobeats": _P(sidechain=0.45, sub_drive=False, sub_boost_db=1.5, melody_space=True, wet=28.0, decay=1.8, lpf_hz=6500.0),
    "amapiano": _P(sidechain=0.5, sub_drive=False, sub_boost_db=2.0, melody_space=True, wet=34.0, decay=2.4, lpf_hz=6000.0),
    # --- wide pop / cinematic space ---
    "pop": _P(sidechain=0.6, sub_drive=False, sub_boost_db=1.0, melody_space=True, wet=30.0, decay=2.0, lpf_hz=7500.0),
    "kpop": _P(sidechain=0.6, sub_drive=False, sub_boost_db=1.5, melody_space=True, wet=30.0, decay=2.0, lpf_hz=8000.0),
    "dark_pop": _P(sidechain=0.6, sub_drive=True, sub_boost_db=2.5, melody_space=True, wet=42.0, decay=3.0, lpf_hz=5500.0),
    "cinematic": _P(sidechain=0.3, sub_drive=False, sub_boost_db=1.0, melody_space=True, wet=55.0, decay=4.5, lpf_hz=9000.0),
}


def mix_recipe(genre: str | None) -> dict[str, Any] | None:
    """Mix DNA for a lane; None for unknown lanes (legacy all-off behaviour)."""
    if not corpus_enabled() or not genre:
        return None
    recipe = MIX_RECIPES.get(genre)
    return dict(recipe) if recipe else None


def mix_recipe_for_pattern(pattern: dict[str, Any]) -> dict[str, Any] | None:
    return mix_recipe(_genre_for_pattern(pattern))


# ---------------------------------------------------------------------------
# FX auto-plan — section-aware ear candy when the architect gave no fx_intent.
# ---------------------------------------------------------------------------

# per-genre flavor: which event types this lane uses
_FX_FLAVOR: dict[str, tuple[str, ...]] = {
    "opium": ("texture", "crash", "riser"),
    "rage": ("texture", "crash", "riser"),
    "phonk": ("texture", "crash", "riser"),
    "trap": ("crash", "riser"),
    "toliver": ("texture", "crash"),
    "pop_rap": ("crash", "riser", "sweep"),
    "drill": ("crash", "riser"),
    "underground": ("crash", "riser", "impact"),
    "minimal_trap": ("texture",),
    "pluggnb": ("texture", "crash"),
    "edm": ("crash", "riser", "sweep", "impact"),
    "trance": ("crash", "riser", "sweep", "impact"),
    "house": ("crash", "sweep"),
    "techno": ("sweep",),
    "hardstyle": ("crash", "riser", "impact"),
    "dubstep": ("crash", "riser", "impact"),
    "dnb": ("crash", "riser"),
    "garage": ("crash", "sweep"),
    "jersey": ("crash", "riser"),
    "pop": ("crash", "riser", "sweep"),
    "kpop": ("crash", "riser", "sweep", "impact"),
    "hyperpop": ("crash", "riser", "impact"),
    "disco_pop": ("crash", "sweep"),
    "dark_pop": ("texture", "crash", "riser"),
    "cinematic": ("impact", "crash", "texture"),
    "lofi": ("texture",),
    "boombap": ("crash",),
    "west_coast": ("crash",),
    # fx lane reserved for the alternating "spins" bass (alternating_bass_sections)
    # — no generic crash/riser here, they'd claim the lane first and starve it.
    "detroit": ("texture",),
    "rnb": ("crash", "texture"),
    "soul": ("crash",),
    "funk": ("crash",),
    "reggaeton": ("crash", "riser"),
    "dancehall": ("crash", "riser"),
    "afrobeats": ("crash", "sweep"),
    "amapiano": ("sweep", "texture"),
    "rock": ("crash", "impact"),
    "grind": ("impact",),
}
_MAX_AUTO_FX = 12


def attach_default_fx_intent(pattern: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a section-aware fx_intent from the arrangement when none exists.

    Crash on every chorus/hook landing, riser leading into each chorus, impact
    at the final chorus, plus genre flavor (texture bed for opium/lofi, sweeps
    for dance lanes). Bars are 1-indexed like the architect's schema.
    """
    if not corpus_enabled():
        return []
    if pattern.get("fx_intent") or pattern.get("fx_plan"):
        return []
    arrangement = pattern.get("plg_arrangement")
    if not isinstance(arrangement, list) or not arrangement:
        return []

    genre = _genre_for_pattern(pattern)
    flavor = set(_FX_FLAVOR.get(genre, ("crash",)))
    events: list[dict[str, Any]] = []

    if "texture" in flavor:
        events.append({"bar": 1, "type": "texture", "length": 16.0})

    for section in arrangement:
        if not isinstance(section, dict):
            continue
        name = str(section.get("name", ""))
        start_bar = int(section.get("start_bar", 0)) + 1  # 1-indexed
        if name in ("chorus", "hook", "drop"):
            if "crash" in flavor:
                events.append({"bar": start_bar, "type": "crash", "length": 4.0})
            if "riser" in flavor and start_bar > 2:
                events.append({"bar": start_bar - 2, "type": "riser", "length": 8.0})
            if "sweep" in flavor and start_bar > 1:
                events.append({"bar": start_bar - 1, "type": "sweep", "length": 4.0})
            if "impact" in flavor and section.get("final_chorus"):
                events.append({"bar": start_bar, "type": "impact", "length": 2.0})

    events = events[:_MAX_AUTO_FX]
    if events:
        pattern["fx_intent"] = events
        pattern["plg_fx_auto"] = True
        logger.info("FX auto-plan: %d events for %s", len(events), genre)
    return events


def style_text_for_matching(pattern: dict[str, Any]) -> str:
    """Style string for sample matching, enriched with the brain's lane + mood.

    Free-form prompts carry no genre words, so descriptor families would never
    trigger; the resolved lane ("dark pop") and mood words ("moody", "dreamy")
    bring the user's library search back on target.
    """
    parts = [str(pattern.get("style") or "")]
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    genre = variety.get("genre")
    if isinstance(genre, str) and genre:
        parts.append(genre.replace("_", " "))
    moods = variety.get("mood")
    if isinstance(moods, list):
        parts.extend(str(m) for m in moods[:4])
    tags = pattern.get("plg_style_tags")
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags[:6])
    return " ".join(p for p in parts if p).strip()


def progression_roots_pc(pattern: dict[str, Any]) -> list[int] | None:
    prog = pattern.get("plg_progression")
    if not isinstance(prog, dict):
        return None
    roots = prog.get("roots_pc")
    if not isinstance(roots, list) or not roots:
        return None
    try:
        return [int(pc) % 12 for pc in roots]
    except (TypeError, ValueError):
        return None
