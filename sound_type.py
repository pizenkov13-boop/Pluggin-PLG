"""What a one-shot SOUNDS like: the single owner of "is this audio a kick / a hat / ...".

``sample_match`` decides by NAME. A file called ``sample_01.wav`` or ``01.wav`` has
no name evidence at all, so until now it could be handed to any lane, and a kick
ended up playing the hi-hats. This module reads the audio features that
``audio_features.analyze_cached`` already keeps (duration, spectral centroid,
zero-crossing rate), so it costs no extra decode, and answers three narrow
questions:

* ``identifies(path, lane)`` - the audio clearly says "this is a <lane> sound";
* ``conflicts(path, lane)``  - the audio clearly says it is ANOTHER kind of drum;
* ``fit(path, lane)``        - how typical the sound is for the lane (ranking only).

It is evidence for files whose NAME says nothing about the lane. A file the user
labelled (``Kick 01``, a ``Hats/`` folder) is never overruled by its sound, and an
unreadable file gets no opinion either way. ``PLG_SOUND_TYPE=off`` removes every
effect of this module.

Thresholds were measured on a real 144-file one-shot library (names as ground
truth, 2026-10): hats sit at centroid 7.5-10 kHz with zcr >= 0.22, claps and
snares at 2.8-7.7 kHz, kicks and 808s under 1 kHz with zcr ~ 0, kicks up to
0.14 s and 808s from 0.72 s. The ambiguous band (7.0-7.7 kHz) is left undecided on
purpose: "no opinion" costs nothing, a wrong verdict hides a good file.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

#: Below this a sound is a low (kick / 808) sound: centroid in Hz, zcr as a ratio.
LOW_CENTROID_HZ = 1200.0
LOW_ZCR = 0.06
#: A low sound shorter than this is a kick, longer is an 808 (the matcher's own
#: DRUM_808_TIEBREAK_SECONDS).
KICK_MAX_SECONDS = 0.6
#: A hat: bright, noisy and short.
HAT_CENTROID_HZ = 7000.0
HAT_ZCR = 0.18
HAT_MAX_SECONDS = 0.3
#: Clearly a hat and not a snare/clap/rim: above everything measured on snares.
CLEAR_HAT_CENTROID_HZ = 8200.0
CLEAR_HAT_MAX_SECONDS = 0.2
#: Not low and not bright: where claps, snares and rims live.
BACKBEAT_CENTROID_HZ = (2000.0, 7000.0)

_BACKBEAT_LANES = frozenset({"snare", "clap", "snare_layer"})
_DRUM_LANES = frozenset({"kick", "snare", "clap", "snare_layer", "hi_hats", "808_bass"})

# Prototype (duration s, centroid Hz) per lane, for ``fit`` only.
_PROTOTYPE = {
    "kick": (0.22, 180.0),
    "808_bass": (1.6, 150.0),
    "hi_hats": (0.07, 8600.0),
    "snare": (0.18, 5200.0),
    "clap": (0.15, 4900.0),
    "snare_layer": (0.15, 5000.0),
}

_cache: dict[tuple[str, int, int], dict[str, float] | None] = {}


def enabled() -> bool:
    return os.environ.get("PLG_SOUND_TYPE", "").strip().lower() not in ("0", "off", "false", "no")


def _features(path: Path | str) -> dict[str, float] | None:
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), int(st.st_mtime_ns), int(st.st_size))
    if key not in _cache:
        try:
            import audio_features

            feats = audio_features.analyze_cached(p)
        except Exception:  # noqa: BLE001 - no reading means no opinion, never an error
            feats = None
        _cache[key] = feats if isinstance(feats, dict) else None
    return _cache[key]


def _numbers(feats: dict[str, float] | None) -> tuple[float, float, float] | None:
    if not feats:
        return None
    try:
        return float(feats["duration_s"]), float(feats["centroid"]), float(feats["zcr"])
    except (KeyError, TypeError, ValueError):
        return None


def _low(centroid: float, zcr: float) -> bool:
    return centroid <= LOW_CENTROID_HZ and zcr <= LOW_ZCR


def _hat(duration: float, centroid: float, zcr: float) -> bool:
    return centroid >= HAT_CENTROID_HZ and zcr >= HAT_ZCR and duration <= HAT_MAX_SECONDS


def identifies(path: Path | str, lane: str) -> bool:
    """The audio clearly is a ``lane`` sound (kick, 808_bass or hi_hats only)."""
    if not enabled():
        return False
    nums = _numbers(_features(path))
    if nums is None:
        return False
    duration, centroid, zcr = nums
    if lane == "kick":
        return _low(centroid, zcr) and 0.03 <= duration < KICK_MAX_SECONDS
    if lane == "808_bass":
        return _low(centroid, zcr) and duration >= KICK_MAX_SECONDS
    if lane == "hi_hats":
        return _hat(duration, centroid, zcr)
    return False


#: Where claps and snares live (centroid, Hz) and the longest they run: a sound inside both is a backbeat sound.
BACKBEAT_VOUCH_HZ = (2800.0, 7000.0)
BACKBEAT_VOUCH_MAX_SECONDS = 0.6


def vouches(path: Path | str, lane: str) -> bool:
    """The audio on its own is unmistakably a ``lane`` sound - enough to overrule a misleading FOLDER name.

    kick / 808 / hats: ``identifies``. snare / clap / layer: a one-shot in the backbeat band, neither low nor a
    bright hat. Deliberately narrow (the ambiguous 7.0-7.7 kHz band says nothing); never a gate by itself."""
    if lane in ("kick", "808_bass", "hi_hats"):
        return identifies(path, lane)
    if lane not in _BACKBEAT_LANES or not enabled() or conflicts(path, lane):
        return False
    nums = _numbers(_features(path))
    if nums is None:
        return False
    duration, centroid, zcr = nums
    return (
        BACKBEAT_VOUCH_HZ[0] <= centroid <= BACKBEAT_VOUCH_HZ[1]
        and 0.03 <= duration <= BACKBEAT_VOUCH_MAX_SECONDS
        and not _low(centroid, zcr)
    )


def conflicts(path: Path | str, lane: str) -> bool:
    """The audio clearly is ANOTHER kind of drum than ``lane``.

    Deliberately one-sided: only verdicts the measurements above separate with
    room to spare. Anything in between is "no conflict"."""
    if not enabled() or lane not in _DRUM_LANES:
        return False
    nums = _numbers(_features(path))
    if nums is None:
        return False
    duration, centroid, zcr = nums
    low = _low(centroid, zcr)
    if lane == "kick":
        return centroid >= BACKBEAT_CENTROID_HZ[0] or (low and duration >= 1.0)
    if lane == "hi_hats":
        return low or centroid <= 4500.0
    if lane in _BACKBEAT_LANES:
        return low or (centroid >= CLEAR_HAT_CENTROID_HZ and duration <= CLEAR_HAT_MAX_SECONDS)
    return False  # 808_bass: the audio passport gates in library_quality already judge it


def fit(path: Path | str, lane: str) -> float:
    """0..1, how typical the sound is for ``lane`` (1 = right on the prototype).

    For ranking among files that all pass the gates; never a gate itself. An
    unreadable file scores 0.5, the same as an unknown lane."""
    proto = _PROTOTYPE.get(lane)
    nums = _numbers(_features(path)) if enabled() else None
    if proto is None or nums is None:
        return 0.5
    duration, centroid, _zcr = nums
    if duration <= 0 or centroid <= 0:
        return 0.5
    distance = abs(math.log(centroid / proto[1])) + 0.6 * abs(math.log(duration / proto[0]))
    return 1.0 / (1.0 + distance)


def describe(path: Path | str) -> dict[str, Any]:
    """Diagnostics: which lanes the audio identifies / conflicts with."""
    nums = _numbers(_features(path))
    return {
        "numbers": nums,
        "identifies": [lane for lane in ("kick", "808_bass", "hi_hats") if identifies(path, lane)],
        "conflicts": sorted(lane for lane in _DRUM_LANES if conflicts(path, lane)),
    }


__all__ = ["conflicts", "describe", "enabled", "fit", "identifies", "vouches"]
