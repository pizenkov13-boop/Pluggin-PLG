"""Random kit from the user's own library, pinned until the next Random or Auto.

``pick_random_kit`` draws one file per lane (808, kick, snare, clap, hats) from
the catalog of the user's library, so a built-in sound can never be drawn. A
lane with no usable file keeps its earlier pin (or stays automatic) and is
reported back. The pins live in ``pinned_kit.json`` in the user data folder;
``starter_kit.attach_sounds_to_pattern`` puts them in the sound map before
kit coherence runs, which then leaves those lanes alone. A pin drawn by Random
is a preference, not an override: an 808 that cannot serve the song's notes is
still swapped by the existing 808 gates, and the swap is reported.

A pin the user chose by hand in the sound browser is marked ``explicit`` in the
same file (``"explicit": [lane, ...]``, additive, still version 1): the gates
yield to it (see ``starter_kit`` and ``lane_fallback``).
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any

from library_paths import AUDIO_EXTENSIONS, relative_in_library
from plg_paths import user_data_dir

logger = logging.getLogger("plg.pinned_kit")

PIN_LANES = ("808_bass", "kick", "snare", "snare_layer", "clap", "hi_hats")
# The melody channel can be pinned by hand and ONLY by hand (2026-11): "Random from library" (``pick_random_kit``) walks
# PIN_LANES, so the dice never draw a melody sound; ``starter_kit`` reads the pin only while PLG_MELODY_ONESHOTS is on.
EXPLICIT_ONLY_LANES = ("melody_lead",)
HAND_PIN_LANES = PIN_LANES + EXPLICIT_ONLY_LANES
PIN_FILE_NAME = "pinned_kit.json"
# The matcher's own floor (pick_best_for_track): a file whose name says nothing
# about the lane scores below it. The export gate lets such a file through for
# snare and clap, but "Random" must not hand an "FX Roll" to the clap lane.
MIN_MATCH_SCORE = 5


def pin_file() -> Path:
    return user_data_dir() / PIN_FILE_NAME


def load_pins() -> dict[str, str]:
    try:
        data = json.loads(pin_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    picks = data.get("picks") if isinstance(data, dict) else None
    if not isinstance(picks, dict):
        return {}
    return {lane: str(path) for lane, path in picks.items() if lane in HAND_PIN_LANES and path}


def load_explicit() -> set[str]:
    """The lanes whose pin the user chose by hand (a subset of the pinned lanes)."""
    try:
        data = json.loads(pin_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    marked = data.get("explicit") if isinstance(data, dict) else None
    if not isinstance(marked, list):
        return set()
    return {str(lane) for lane in marked if lane in HAND_PIN_LANES} & set(load_pins())


def save_pins(picks: dict[str, str], explicit: set[str] | frozenset[str] | list[str] = ()) -> None:
    target = pin_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"version": 1, "picks": {lane: str(path) for lane, path in picks.items()}}
    marked = sorted(lane for lane in set(explicit) if lane in picks)
    if marked:
        payload["explicit"] = marked
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_pins() -> None:
    try:
        pin_file().unlink()
    except FileNotFoundError:
        pass


def valid_pins(root: Path | str) -> dict[str, Path]:
    """The pins that still point at an audio file inside the user's library."""
    root = Path(root)
    out: dict[str, Path] = {}
    for lane, raw in load_pins().items():
        path = Path(raw)
        if path.suffix.lower() not in AUDIO_EXTENSIONS or not path.is_file():
            continue
        try:
            relative_in_library(path, root)
        except ValueError:
            continue  # outside the library (a built-in sound, a moved folder): never used
        out[lane] = path.resolve()
    return out


def pinned_names(root: Path | str) -> dict[str, str]:
    return {lane: path.name for lane, path in valid_pins(root).items()}


def valid_explicit(root: Path | str) -> set[str]:
    """The explicit lanes whose pin still points at a file of the library."""
    return load_explicit() & set(valid_pins(root))


def pin_by_hand(lane: str, path: Path | str, root: Path | str) -> str | None:
    """Pin ``path`` to ``lane`` as the user's own choice; returns an error code or None.

    Only what the sound browser can know is checked: the lane exists, the file is an audio
    file inside the user's library, and a WAV has a header FL can read in a format PLG can decode.
    Whether the sound suits the lane or the song is NOT judged: the user chose it."""
    if lane not in HAND_PIN_LANES:
        return "unknown_lane"
    path = Path(path)
    if path.suffix.lower() not in AUDIO_EXTENSIONS or not path.is_file():
        return "not_found"
    try:
        relative_in_library(path, Path(root))
    except ValueError:
        return "outside_library"
    if path.suffix.lower() == ".wav":
        from wav_compat import inspect_wav
        from wav_decode import decodable

        if inspect_wav(path) is None:
            return "unreadable"
        if not decodable(path):
            return "unsupported_format"  # a WAV format PLG cannot decode would only play as noise
    pins = {lane_: str(p) for lane_, p in valid_pins(root).items()}
    explicit = valid_explicit(root)
    pins[lane] = str(path.resolve())
    explicit.add(lane)
    save_pins(pins, explicit)
    return None


def unpin(lane: str, root: Path | str) -> None:
    """Empty one cell: the lane goes back to automatic matching."""
    pins = {lane_: str(p) for lane_, p in valid_pins(root).items()}
    explicit = valid_explicit(root)
    pins.pop(lane, None)
    explicit.discard(lane)
    if pins:
        save_pins(pins, explicit)
    else:
        clear_pins()


def pick_random_kit(
    catalog: dict[str, Any],
    root: Path | str,
    *,
    previous: dict[str, Path | str] | None = None,
    rng: random.Random | None = None,
) -> tuple[dict[str, Path], list[str]]:
    """One random usable file per lane; returns ``(picks, lanes_without_a_file)``.

    "Usable" is the export gate's own verdict (``track_candidate_rejection_reasons``
    with no library root, so a drawn file is not refused later for its audio)
    plus a name that actually says it belongs to the lane (``MIN_MATCH_SCORE``).
    The same file is never drawn for two lanes, and a lane never redraws the
    file it already had unless that is the only usable one."""
    from sample_match import (
        iter_track_candidates_grouped,
        score_candidate,
        track_candidate_rejection_reasons,
    )

    rng = rng or random.SystemRandom()
    root = Path(root)
    before = {lane: str(Path(path).resolve()) for lane, path in (previous or {}).items()}
    taken: set[str] = set()
    picks: dict[str, Path] = {}
    without: list[str] = []
    for lane in PIN_LANES:
        pool = [((root / rel).resolve(), rel, group) for rel, group in iter_track_candidates_grouped(catalog, lane)]
        pool = [item for item in pool if item[0].is_file()]
        rng.shuffle(pool)
        chosen: Path | None = None
        same_again: Path | None = None
        for path, rel, group in pool:
            key = str(path)
            if key in taken:
                continue
            score = score_candidate(rel, lane, prompt_tokens=set(), style_tokens=set(), prompt_raw="", group=group)
            if score < MIN_MATCH_SCORE or track_candidate_rejection_reasons(path, lane):
                continue
            if key == before.get(lane):
                same_again = path
                continue
            chosen = path
            break
        chosen = chosen or same_again
        if chosen is None:
            without.append(lane)
            continue
        picks[lane] = chosen
        taken.add(str(chosen))
    return picks, without
