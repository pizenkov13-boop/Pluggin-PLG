"""Pick the best library audio per track from prompt + style (not first file).

Name/folder scoring is the fast first pass. Top candidates are re-ranked by:
  1) numpy spectral features (audio_features) when the prompt implies timbre
  2) CLAP text↔audio similarity when laion-clap is installed (PLG_USE_CLAP=1 or auto)
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path
from typing import Any, NamedTuple

from library_paths import (
    AUDIO_EXTENSIONS,
    DRUM_808_TIEBREAK_SECONDS,
    audio_duration_seconds,
    is_probably_one_shot,
    relative_in_library,
    strip_handles,
)
from library_quality import audio_rejection_reason

KIT_TRACK_ORDER = ("sub_808", "kick", "snare", "clap", "hi_hats", "melody_lead")
# 808_bass is deliberately NOT in KIT_TRACK_ORDER: that tuple is consumed
# unconditionally by starter_kit.resolve_track_samples for every generation
# regardless of genre (no profile/genre parameter reaches it), so adding an
# always-on-until-genre-opts-in track there would pick + log + surface a
# "Matched kit" sample for a channel that has no notes in every genre today
# (bass808_enabled is off everywhere — see genre_profiles.py), breaking the
# byte-identical-for-unchanged-genres guarantee. TRACK_PROFILES["808_bass"]
# below is ready to use; wiring an actual per-generation pick call is scoped
# to land alongside whichever genre first sets bass808_enabled=True.

TRACK_PROFILES: dict[str, dict[str, Any]] = {
    "kick": {
        "folders": ("kits", "808"),
        "strong": ("kick", "bd", "bassdrum", "bass_drum", "kik", "kck"),
        "avoid": ("snare", "clap", "hat", "hihat", "melody", "bell", "rim", "perc", "vocal", "tag", "808", "sub",
                  "crash", "cymbal", "ride", "tom", "shaker",
                  "sn", "sd", "clp", "cp", "hh", "ch", "oh"),
    },
    "snare": {
        "folders": ("kits", "splice"),
        "strong": ("snare", "snr", "sd", "sn", "rim", "rimshot", "shot"),
        "avoid": ("kick", "808", "hat", "clap", "melody", "bass", "sub", "vocal", "tag",
                  "crash", "cymbal", "ride", "tom",
                  "bd", "clp", "cp", "hh", "ch", "oh"),
    },
    "snare_layer": {
        "folders": ("kits", "splice"),
        "strong": ("rim", "rimshot", "click", "snap", "perc", "tick"),
        "avoid": ("kick", "808", "hat", "melody", "vocal", "sub", "bass", "bd", "hh", "ch", "oh"),
    },
    "clap": {
        "folders": ("kits", "splice"),
        "strong": ("clap", "clp", "cp", "snap", "slap", "perc"),
        "avoid": ("kick", "snare", "808", "hat", "melody", "bass", "sub", "vocal",
                  "crash", "cymbal", "ride", "tom",
                  "bd", "sn", "sd", "hh", "ch", "oh"),
    },
    "sub_808": {
        "folders": ("808", "kits"),
        "strong": ("808", "sub", "subbass", "sub_bass", "bass", "donk", "low", "distort", "reese", "deep", "boomy"),
        "avoid": (
            "kick",
            "snare",
            "clap",
            "hat",
            "hihat",
            "hi_hat",
            "openhat",
            "cymbal",
            "rim",
            "perc",
            "tom",
            "crash",
            "shaker",
            "vocal",
            "tag",
            "chant",
        ),
    },
    # Same tonal 808 family/library folder as sub_808 (no separate "808 bass"
    # library category exists), but NOT the same keyword list: 808_bass is the
    # short/punchy, kick-locked lane (bass808_gen.py) — "sub"/"subbass"/
    # "sub_bass" are moved to avoid (2026-07 audit: a real generation picked
    # "...808 Sub @producera.wav", a ~2.5s sustained file, for 808_bass purely
    # because "sub" scored a strong-keyword bonus here — exactly the long file
    # this lane must NOT reach for). See the duration scoring in
    # pick_best_for_track for the audio-side half of this fix.
    "808_bass": {
        "folders": ("808", "kits"),
        "strong": ("808", "bass", "donk", "low", "distort", "reese", "deep", "boomy"),
        "avoid": (
            "kick",
            "snare",
            "clap",
            "hat",
            "hihat",
            "hi_hat",
            "openhat",
            "cymbal",
            "rim",
            "perc",
            "tom",
            "crash",
            "shaker",
            "vocal",
            "tag",
            "chant",
            "sub",
            "subbass",
            "sub_bass",
        ),
    },
    "hi_hats": {
        # CLOSED hat is the 1/8 voice. Open hats / rides / crashes as the main
        # sample stack into a metallic sheet when retriggered every eighth.
        "folders": ("hats", "kits", "splice"),
        "strong": ("hat", "hihat", "hi_hat", "hi-hat", "hh", "ch", "closed", "clhat", "closedhat"),
        "avoid": (
            "808",
            "kick",
            "snare",
            "clap",
            "bass",
            "sub",
            "melody",
            "bell",
            "lead",
            "piano",
            "pluck",
            "bd",
            "sn",
            "sd",
            "clp",
            "cp",
            "oh",
            "open",
            "op_hat",
            "ophat",
            "openhat",
            "open_hat",
            "cymbal",
            "ride",
            "crash",
            "splash",
            "china",
        ),
    },
    "melody_lead": {
        "folders": ("melodies", "kits", "splice", "textures"),
        "strong": (
            "melody",
            "lead",
            "bell",
            "pluck",
            "key",
            "piano",
            "arp",
            "synth",
            "stab",
            "choir",
            "pad",
            "flute",
            "guitar",
            # kit naming reality: Carti-style kits label synth one-shots "Sound",
            # and cowbells/chimes ARE the lead voice in rage/phonk
            "sound",
            "cowbell",
            "chime",
            "kalimba",
            "mallet",
            "whistle",
            "brass",
            "epic",
            "weird",
            "unique",
        ),
        "avoid": (
            "808", "kick", "snare", "clap", "hat", "hihat", "rim", "perc", "tom", "crash",
            # not a beautiful melodic hook — keep FX/risers/textures off the lead
            "riser", "downer", "sweep", "whoosh", "impact", "drone", "noise", "sfx",
            "reverse", "foley", "atmos", "transition", "fx",
        ),
    },
    "chords": {
        # sustained pad/keys one-shot for the harmonic bed (PLG Chords channel);
        # falls back to the lead's instrument when nothing matches
        "folders": ("melodies", "kits", "textures", "splice"),
        "strong": ("pad", "key", "piano", "chord", "string", "choir", "organ",
                   "rhodes", "synth", "sound", "guitar", "brass", "epic", "weird", "unique"),
        "avoid": ("kick", "snare", "clap", "hat", "hihat", "808", "sub", "bass",
                  "rim", "perc", "tom", "crash", "vocal", "vox", "tag",
                  "riser", "sweep", "impact", "sfx", "fx", "snap", "click"),
    },
    "cowbell": {
        # Carti percussion signature — a cowbell/clank one-shot
        "folders": ("kits", "splice", "808"),
        "strong": ("cowbell", "bell", "clank", "clave", "block", "cow"),
        "avoid": ("kick", "snare", "clap", "hat", "hihat", "808", "sub", "bass",
                  "melody", "pluck", "lead", "vocal", "vox", "tag", "loop",
                  "riser", "sweep", "crash", "open", "snap"),
    },
    "texture": {
        # atmospheric bed (rain/vinyl/siren/drone) — the scene in the listener's
        # head. Triggered once per section, so loops are fine here.
        "folders": ("textures", "fx", "kits"),
        "strong": ("texture", "atmos", "ambient", "rain", "vinyl", "drone",
                   "noise", "siren", "wind", "crowd", "tape", "static",
                   "cricket", "crickets", "night", "nocturnal"),
        "avoid": ("kick", "snare", "clap", "hat", "hihat", "808", "sub", "bass",
                  "melody", "bell", "pluck", "lead", "vocal", "vox", "tag",
                  "riser", "impact", "crash", "snap", "perc"),
    },
    "fx": {
        # crash/impact one-shots for arrangement landings (PLG FX channel)
        "folders": ("fx", "kits", "splice"),
        "strong": ("crash", "splash", "impact", "cymbal", "china"),
        "avoid": ("kick", "snare", "clap", "808", "sub", "bass", "melody", "bell",
                  "pluck", "lead", "vocal", "vox", "tag", "loop", "riser", "sweep",
                  "downer", "texture", "atmos", "hat", "hihat", "shaker", "perc"),
    },
    "counter_melody": {
        "folders": ("melodies", "kits", "splice", "textures"),
        "strong": (
            "melody",
            "lead",
            "bell",
            "pluck",
            "key",
            "piano",
            "arp",
            "synth",
            "stab",
            "choir",
            "pad",
            "flute",
            "guitar",
        ),
        "avoid": (
            "808", "kick", "snare", "clap", "hat", "hihat", "rim", "perc", "tom", "crash",
            "riser", "downer", "sweep", "whoosh", "impact", "drone", "noise", "sfx",
            "reverse", "foley", "atmos", "transition", "fx",
        ),
    },
}


def _normalize(text: str) -> str:
    return strip_handles(text).lower().replace("-", "_").replace(" ", "_")


# Keywords that hide inside ordinary words match as WHOLE WORDS only: every short
# alphabetic keyword ("hat", "bd", "sd", "hh", "rim", "tom", "sub", "low", "arp",
# "pad", "vox", "sax", "tag", "fx") and a few longer ones ("ride", "lead", "bell",
# "keys"). "hat" is not in Chat/What/Phat/Whatever, "ride" not in Bride/Override,
# "tom" not in Atom, "sub" not in Subtle, "arp" not in Sharp. Words split on
# punctuation, digits and camelCase; a plural counts. Longer, distinctive keywords
# ("kick", "snare", "hihat", "hi_hat", "bass_drum") and anything with a digit
# ("808") stay substring matches.
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_EXTRA_WHOLE_WORD_KEYS = frozenset({
    "ride", "lead", "bell", "keys", "key", "horn", "harp", "tape", "wind", "rain",
    "epic", "roll", "slap", "snip", "stab", "tick", "snap", "pizz", "open",
})


def _is_whole_word_key(keyword: str) -> bool:
    return keyword.isalpha() and (len(keyword) <= 3 or keyword in _EXTRA_WHOLE_WORD_KEYS)


def _words_of(text: str) -> frozenset[str]:
    low = _CAMEL.sub(" ", strip_handles(text)).lower()
    tokens = re.findall(r"[a-zа-яё]+|\d+", low)
    words = set(tokens)
    words |= {t[:-1] for t in tokens if len(t) > 3 and t.endswith("s")}
    return frozenset(words)


def _kw_in(keyword: str, norm_text: str, words: frozenset[str]) -> bool:
    return (keyword in words) if _is_whole_word_key(keyword) else (keyword in norm_text)


def _tokens(*texts: str) -> set[str]:
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        norm = _normalize(text)
        for part in re.split(r"[_/\\.\s]+", norm):
            if len(part) >= 2:
                found.add(part)
        for word in re.findall(r"[a-z0-9]{3,}", strip_handles(text).lower()):
            found.add(word)
    return found


# Rule 16 — Perfect Pair: kick character follows the chosen 808.
_LONG_808_HINTS = ("long", "distort", "dist", "reese", "growl", "sub", "deep", "boom", "sustain")
_SHORT_808_HINTS = ("short", "punch", "tight", "click", "clean", "stab", "pluck")
_PUNCHY_KICK = ("click", "punch", "tight", "hard", "clean", "short", "knock")
_FAT_KICK = ("fat", "boom", "deep", "sub", "round", "long", "808", "thump")


def partner_kick_keywords(eight08_name: str) -> tuple[str, ...]:
    """A long/distorted sub wants a short punchy click kick, and vice versa."""
    name = _normalize(eight08_name)
    if any(h in name for h in _SHORT_808_HINTS):
        return _FAT_KICK
    if any(h in name for h in _LONG_808_HINTS):
        return _PUNCHY_KICK
    return _PUNCHY_KICK  # dark default: long distorted 808 → tight click kick


def pack_tokens(name: str) -> tuple[str, ...]:
    """The kit/pack identity — leading significant words of a filename, e.g.
    'BLAKK ROCKSTAR P1 Kick @producera' → ('blakk', 'rockstar'). Used to keep a
    beat's drums from ONE cohesive kit (sounds designed together = 'мясо') instead
    of mismatched 808/kick/snare from different packs."""
    stem = Path(name).stem
    words = [
        w for w in _normalize(stem).split("_")
        if len(w) >= 3 and not w.isdigit() and not w.startswith("@")
    ]
    return tuple(words[:2])


_MELODY_DRUM_MARKERS = (
    "808",
    "kick",
    "snare",
    "clap",
    "hat",
    "hihat",
    "hi_hat",
    "openhat",
    "subbass",
    "sub_bass",
    "bd",
    "bassdrum",
    "donk",
    "rim",
    "perc",
)

# Per-note-triggered channels must never get a LOOP — PLG fires the sample on
# every MIDI note, so a 139-bpm loop just retriggers its attack = stutter garbage
# (this is why the melody was "the same synth, only heard at the end"). Drum
# channels also reject MELODIC/vocal samples (a lead/bell/chant firing as a kick).
_OPEN_HAT_MARKERS = (
    "oh",
    "op_hat",
    "ophat",
    "openhat",
    "open_hat",
    "openhihat",
    "open_hihat",
    "open_hi_hat",
    "cymbal",
    "ride",
    "crash",
    "splash",
    "china",
)
_CLOSED_HAT_MARKERS = ("closed", "clhat", "closedhat", "closed_hat", "cl_hat")


def _is_open_hat_name(norm_name: str, words: frozenset[str] | None = None) -> bool:
    if words is None:
        words = _words_of(norm_name.replace("_", " "))
    if any(_kw_in(marker, norm_name, words) for marker in _OPEN_HAT_MARKERS):
        return True
    # Token-aware: "open" alone (open_rage.wav) without also being a closed label.
    parts = set(re.split(r"[_/\\.\s]+", norm_name))
    if "open" in parts and not any(m in norm_name for m in _CLOSED_HAT_MARKERS):
        return True
    return False


_ONE_SHOT_DRUM_TRACKS = ("kick", "snare", "snare_layer", "clap", "hi_hats", "sub_808", "808_bass", "fx", "cowbell")
_LOOP_REJECT_TRACKS = _ONE_SHOT_DRUM_TRACKS + ("melody_lead", "counter_melody", "chords")
# 2026-09 audit: name/path scoring alone cannot tell a real kick from a
# correctly-named but corrupt/unreadable one (zero-byte, garbled WAV) — a
# file can carry "kick" in its name and still be silent/broken audio.
# track_candidate_rejection_reason() already hard-rejects a handful of tracks
# on NAME mismatch, but nothing checks whether the content is even readable.
# pick_best_for_track routes an unreadable _ONE_SHOT_DRUM_TRACKS candidate
# into its own fallback tier (see ``unverified`` there) instead of scoring it
# down: this codebase's audio helpers (is_probably_one_shot,
# audio_duration_seconds, sample_pitch_passport) all fail OPEN on an
# unreadable file rather than blocking generation over a best-effort check,
# and the tier keeps that — it can still win a lane if it is genuinely the
# only candidate, it just never beats a competitor with real signal.
_LOOP_MARKERS = ("loop", "bpm")
_MELODY_LOOP_MARKERS = (
    "loop",
    "bpm",
    "bonus_loop",
    "bonus_loops",
    "mixtape",
    "effect",
    "texture",
    "atmos",
    "ambient",
    "bass",
)
_MELODY_NON_HOOK_MARKERS = (
    "fx", "roll", "shaker", "perc", "scream", "effect", "sfx", "impact",
    "whoosh", "sweep", "riser", "downer", "foley", "atmos", "transition",
    "vox", "vocal", "chant", "tag", "trap_jump", "bff",
    # percussive one-shots are not melody instruments (a repitched snap = clicks)
    "snap", "click", "snip", "stomp", "knock",
)
_MELODIC_MARKERS = (
    "melody", "lead", "bell", "pluck", "arp", "piano", "guitar", "flute", "chord",
    "choir", "chant", "vox", "vocal", "voice", "brass", "sax", "string", "horn",
    "organ", "harp", "violin", "pizz", "rhodes", "keys", "synth", "pad",
)


_MELODY_HOOK_PREFERRED = (
    "synth", "pad", "piano", "key", "pluck", "arp", "melody", "lead",
    "bell", "sound", "cowbell", "chime",
)

# Dirkey "Fix Your Sound Selection" — family pairing: a piano/EP-family lead
# tends toward strings/brass/orchestral highs for the NEXT layer, not another
# piano-family sound (avoids doubling the same register/timbre).
_PIANO_EP_FAMILY = ("piano", "rhodes", "keys", "grand", "wurli")
_ORCHESTRAL_PAIR = ("string", "strings", "violin", "cello", "brass", "horn", "orchestral", "choir")


def family_pairing_keywords(reference_name: str) -> tuple[str, ...]:
    """Bonus keywords for the NEXT melodic layer, paired to an already-chosen
    layer's instrument family. Only the piano/EP -> orchestral pair from the
    brief is implemented; returns () for any other/unknown family."""
    norm = _normalize(reference_name)
    if any(keyword in norm for keyword in _PIANO_EP_FAMILY):
        return _ORCHESTRAL_PAIR
    return ()


_KIT_SYMBIOSIS_TRACKS = frozenset(
    {
        "kick",
        "snare",
        "snare_layer",
        "clap",
        "hi_hats",
        "sub_808",
        "808_bass",
        "cowbell",
        "fx",
    }
)


def score_candidate(
    rel_path: str,
    track: str,
    *,
    prompt_tokens: set[str],
    style_tokens: set[str],
    prompt_raw: str,
    bonus_keywords: tuple[str, ...] = (),
    bury_keywords: tuple[str, ...] = (),
    group: str = "",
    duration: float | None = None,
    audio_vouches: bool = False,
) -> int:
    """``audio_vouches`` - the caller measured the AUDIO (``sound_type.vouches``) and it is unmistakably a sound of
    this lane: a foreign word in a FOLDER name (a pack called "808 Heavy", a "Perc & Vox" parent) then no longer
    vetoes the file; a foreign word in the FILE name still does.

    ``duration`` (seconds, optional) lets a drum-machine name through: "808 Snare"
    of 0.15 s is a snare, not an 808, so its "808" is not held against it.

    ``group`` is the CATALOG classification of this file (scan_library sorts
    every file into 808/hats/kits/melodies/… by content). When given, the folder
    bonus/gate uses it instead of the raw top-level directory name — user kits
    live in folders like "Cxdy_-_Space_Cadet_Drum_kit", and judging them by that
    name locked whole packs out of the pool."""
    profile = TRACK_PROFILES[track]
    rel_parts = rel_path.replace("\\", "/").split("/")
    if Path(rel_path).name.startswith("._") or any(part.lower() == "__macosx" for part in rel_parts):
        return -50_000
    norm_name = _normalize(Path(rel_path).name)
    norm_path = _normalize(rel_path)
    words_name = _words_of(Path(rel_path).name)
    words_path = _words_of(rel_path)
    # Main hi-hat lane is a closed 1/8 tick. Open hats / rides firing every
    # eighth = metallic sheet on asphalt (long resonant tails stacking).
    if track == "hi_hats" and _is_open_hat_name(norm_name, words_name):
        return -50_000
    if track in ("melody_lead", "counter_melody") and any(_kw_in(marker, norm_name, words_name) for marker in _MELODY_DRUM_MARKERS):
        return -50_000
    if track in ("fx", "texture") and Path(rel_path).suffix.lower() == ".mp3":
        return -50_000  # a full mp3 song is not ear candy / not an atmosphere
    if track in ("melody_lead", "counter_melody"):
        suffix = Path(rel_path).suffix.lower()
        # PLG triggers melodic lanes per MIDI note. Full mp3/loop/effect files
        # retrigger long tails and sound like a broken speaker in FL.
        if suffix == ".mp3" or any(_kw_in(marker, norm_path, words_path) for marker in _MELODY_LOOP_MARKERS):
            return -50_000
        for marker in _MELODY_NON_HOOK_MARKERS:
            if _kw_in(marker, norm_name, words_name):
                return -50_000
        if suffix == ".mp3":
            return -50_000
    if track in _LOOP_REJECT_TRACKS:
        if any(_kw_in(marker, norm_name, words_name) for marker in _LOOP_MARKERS):
            return -50_000  # loops break per-note triggering (drums AND melody)
    if track in _ONE_SHOT_DRUM_TRACKS:
        # Cross-type contamination is a HARD reject (prompt words like "808
        # distorted" used to drag an 808 onto the clap/kick): if the name has no
        # keyword for THIS channel but does carry a foreign drum / melodic marker,
        # it is the wrong kind of sound for the channel.
        has_own = any(_kw_in(k, norm_name, words_name) for k in profile["strong"])
        if not has_own and track in _STRICT_IDENTITY_TRACKS:
            has_own = _extra_identity(track, norm_name, rel_path)
        if not has_own:
            foreign = tuple(profile.get("avoid", ()))
            if track != "sub_808":
                foreign = foreign + _MELODIC_MARKERS
            # A filename can omit its actual type: e.g. ``DISMANTLED
            # Screamin.wav`` contains neither "perc" nor "vocal", while its
            # source folder is ``Perc & Vox``. The catalog deliberately groups
            # that folder under the broad ``kits`` bucket, so a name-only test
            # gives it the kick lane's +16 folder score. For a candidate with
            # no positive evidence for THIS drum role, the whole relative path
            # is authoritative enough to reject foreign vocal/percussion lanes.
            if audio_vouches:
                if any(_kw_in(marker, norm_name, words_name) for marker in foreign):
                    return -50_000
            elif any(_kw_in(marker, norm_path, words_path) for marker in foreign):
                return -50_000
    if track == "chords":
        # A harmonic bed needs a pad/keys/string-type instrument, not a
        # bass/drum one-shot — this used to be only a -45 soft penalty, so an
        # 808 sample could still win the chords lane outright when the
        # library had few real pad/piano/string candidates (audibly wrong: a
        # bass one-shot voiced as a chord, not a chord bed). Same hard-reject
        # shape as the drum lanes above, scoped to chords' own avoid list —
        # deliberately NOT unioned with _MELODIC_MARKERS, which is exactly
        # what a real chords sample's name is expected to carry.
        has_own = any(_kw_in(k, norm_name, words_name) for k in profile["strong"])
        if not has_own and any(_kw_in(marker, norm_name, words_name) for marker in profile.get("avoid", ())):
            return -50_000

    score = 0

    parts = rel_path.replace("\\", "/").split("/")
    folder = group.lower() if group else (parts[0].lower() if len(parts) > 1 else "")
    primary = profile["folders"][0]
    if folder == primary:
        score += 16
    elif folder in profile["folders"]:
        score += 8

    own_in_name = False
    for keyword in profile["strong"]:
        if _kw_in(keyword, norm_name, words_name):
            score += 15
            own_in_name = True
        elif _kw_in(keyword, norm_path, words_path):
            score += 8

    # "808 Snare" / "TR-808 Clap": a drum-machine kit puts 808 in every name. A
    # short file that also carries its OWN lane word is that drum, so the "808"
    # is not an avoid marker for it (a long "Kick 808" still is one).
    drum_808 = (
        own_in_name
        and duration is not None
        and duration < DRUM_808_TIEBREAK_SECONDS
        and track in ("kick", "snare", "snare_layer", "clap", "hi_hats")
    )
    for keyword in profile.get("avoid", ()):
        if _kw_in(keyword, norm_name, words_name):
            if drum_808 and keyword == "808":
                continue
            score -= 45

    for token in prompt_tokens | style_tokens:
        if len(token) < 3:
            continue
        if token in norm_name:
            score += 24
        elif token in norm_path:
            score += 14

    lower_prompt = prompt_raw.lower()
    for word in re.findall(r"[a-z0-9]{4,}", lower_prompt):
        if word in norm_name:
            score += 20

    for keyword in bonus_keywords:
        if keyword in norm_name:
            score += 34  # melody-energy / character bias — stronger than prompt tokens
        elif keyword in norm_path:
            score += 16

    for keyword in bury_keywords:
        if keyword in norm_name:
            score -= 30
        elif keyword in norm_path:
            score -= 14

    if track in ("melody_lead", "counter_melody"):
        for marker in _MELODY_HOOK_PREFERRED:
            if _kw_in(marker, norm_name, words_name):
                score += 18
                break  # one preferred hit is enough — no stacking

    if track == "hi_hats":
        if any(marker in norm_name for marker in _CLOSED_HAT_MARKERS):
            score += 28
        # Plain "...Hat..." without open markers — prefer over ambiguous names.
        elif "hat" in words_name and not _is_open_hat_name(norm_name, words_name):
            score += 12

    return score


_STRICT_IDENTITY_TRACKS = frozenset({"kick", "808_bass", "hi_hats"})

# The three anchor lanes must be able to say what they are, but a real kit
# rarely names files the way our keyword list does ("Sub 01", "eight-o-eight",
# "Бас", or "808s/Dark_01.wav"). Two more kinds of evidence count, both
# deliberately narrow so a wrongly-named file still cannot claim the lane:
#   1. an alias word in the FILE NAME (spelled-out / Cyrillic / "sub"), or
#   2. a DEDICATED parent folder ("808s", "Hats", "Kicks"): every word of the
#      folder name is a lane keyword or filler, so "808 Heavy Drum Kit" (a whole
#      kit) does NOT count.
# Neither applies when the path carries a foreign drum/melodic marker, and the
# audio gates (duration, body, pitch passport) still decide the rest.
_IDENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "808_bass": ("sub", "subbass", "sub_bass", "eight_o_eight", "бас", "басс", "саб"),
    "kick": ("кик", "бочка", "kck", "kk"),
    "hi_hats": ("хэт", "хет", "хат", "ch", "chh"),
}
_FOLDER_FILLER = (
    "shots", "shot", "hits", "hit", "sounds", "sound", "samples", "sample",
    "wavs", "wav", "packs", "pack", "the", "one", "hi", "volume", "vol",
)


def _strip_staging(rel_path: str) -> list[str]:
    """Path parts without PLG's own import staging (``imports/drop-<uuid>``)."""
    return [
        part for part in rel_path.replace("\\", "/").split("/")
        if part and part.lower() != "imports" and not part.lower().startswith("drop-")
    ]


def _extra_identity(track: str, norm_name: str, rel_path: str) -> bool:
    profile = TRACK_PROFILES[track]
    aliases = _IDENTITY_ALIASES.get(track, ())
    # The alias family is not "foreign" for its own lane: "sub" sits in the
    # 808_bass avoid list (it hurts ranking) but is also its alias.
    foreign = tuple(
        m for m in profile.get("avoid", ())
        if not any(m in alias or alias in m for alias in aliases)
    ) + _MELODIC_MARKERS
    parts = _strip_staging(rel_path)
    norm_path = _normalize("/".join(parts))
    words_path = _words_of("/".join(parts))
    if any(_kw_in(marker, norm_path, words_path) for marker in foreign):
        return False
    words_name = _words_of(Path(rel_path).name)
    if any(_kw_in(alias, norm_name, words_name) for alias in aliases):
        return True
    if len(parts) < 2:
        return False
    # A dedicated parent folder: after removing lane keywords and filler, nothing
    # is left ("808s", "Hats", "Kick Vol.2", "Bass Drums"; not "808 Heavy Drum
    # Kit", not "Whatever" whose "hat" leaves "wever").
    keywords = tuple(dict.fromkeys(tuple(profile["strong"]) + aliases))
    rest = _normalize(parts[-2])
    found = False
    for word in sorted(keywords + _FOLDER_FILLER, key=len, reverse=True):
        if word in rest:
            if word in keywords:
                found = True
            rest = rest.replace(word, "_")
    rest = re.sub(r"\d+", "", rest)
    leftover = [t for t in re.split(r"[^a-zа-яё]+", rest) if t and t not in ("s", "es")]
    return found and not leftover


def _audio_vouches(path: Path, track: str) -> bool:
    """``sound_type.vouches`` for the one-shot drum lanes; never raises (no reading = no vouching)."""
    if track not in _ONE_SHOT_DRUM_TRACKS:
        return False
    try:
        import sound_type

        return sound_type.vouches(path, track)
    except Exception:  # noqa: BLE001 - an advisory measurement must not fail a pick
        return False


def _identity_path(path: Path, library_root: Path | None = None) -> str:
    """The part of a path that describes the SAMPLE, not where it lives.

    Scoring a full absolute path let unrelated ancestors veto a good file: an
    808 under ``D:/Kits/Snare & Clap Pack/808s/sub.wav`` (or anything below
    ``C:/Users/clapton``) scored as a clap conflict and was thrown away.
    """
    if library_root is not None:
        try:
            return relative_in_library(path, Path(library_root)).as_posix()
        except (OSError, ValueError):
            pass
    return f"{path.parent.name}/{path.name}" if path.parent.name else path.name


def _collect_rejections(
    path: Path, track: str, library_root: Path | None, *, first_only: bool
) -> list[str]:
    """Every reason a concrete file cannot serve a track, in gate order.

    One owner of the rules: ``track_candidate_rejection_reason`` (the first
    reason — what selection and export have always used) and
    ``track_candidate_rejection_reasons`` (all of them — what the last-resort
    ladder needs to tell a soft failure from a hard one) both read this.
    """
    path = Path(path)
    if track not in TRACK_PROFILES:
        return ["unknown_track"]
    if not path.is_file():
        return ["file_not_found"]
    if path.suffix.lower() == ".wav":
        # A WAV in a format PLG cannot decode (an unknown tag, a damaged compressed stream) would be read
        # as noise: it can serve no lane, and no analysis of it means anything.
        from wav_decode import decodable

        if not decodable(path):
            return ["undecodable_wav"]
    reasons: list[str] = []

    def add(reason: str) -> bool:
        reasons.append(reason)
        return first_only

    # An 808 may deliberately have a long body/tail, so the generic short
    # one-shot heuristic is invalid for it.  Its dedicated audio passport
    # below validates body and attack instead.
    if track in _ONE_SHOT_DRUM_TRACKS and track not in {"808_bass", "sub_808"} and not is_probably_one_shot(path):
        if add("not_a_one_shot"):
            return reasons
    quality_reason = audio_rejection_reason(path, track=track)
    if quality_reason and add(quality_reason):
        return reasons

    if track == "808_bass":
        # A large pitch-drop can have a measurable tail but no trustworthy
        # attack root. Do not let that tail become FL's root key.
        try:
            from sample_pitch_passport import sample_pitch_passport

            passport = sample_pitch_passport(path)
        except Exception:  # noqa: BLE001
            passport = None
            if add("808_root_unreadable"):
                return reasons
        # ``root_trusted`` = strict "ok" OR a clear in-tune body under a big
        # attack pitch-drop (see sample_pitch_passport). Passports cached from
        # an older run lack the key: fall back to the strict status.
        if passport is not None and not passport.get(
            "root_trusted", passport.get("status") == "ok"
        ):
            if add("808_root_unverified"):
                return reasons

    identity = _identity_path(path, library_root)
    score = score_candidate(
        identity,
        track,
        prompt_tokens=set(),
        style_tokens=set(),
        prompt_raw="",
    )
    if score <= -50_000 and _audio_vouches(path, track):
        # a folder name alone must not throw out a file whose sound is unmistakably this lane's
        score = score_candidate(
            identity,
            track,
            prompt_tokens=set(),
            style_tokens=set(),
            prompt_raw="",
            audio_vouches=True,
        )
    if score <= -50_000 and add("name_or_path_conflicts_with_track"):
        return reasons

    # A file whose NAME says nothing about the lane (``sample_01.wav``, ``07.wav``)
    # is judged by its sound (sound_type): a kick-like file no longer plays the
    # hats, and a clearly kick/808-like or bright-hat file is no clap. A labelled
    # file is never overruled by its sound.
    import sound_type

    norm_name = _normalize(path.name)
    words_name = _words_of(path.name)
    strong = TRACK_PROFILES[track]["strong"]
    named = any(_kw_in(marker, norm_name, words_name) for marker in strong) or (
        track in _STRICT_IDENTITY_TRACKS and _extra_identity(track, norm_name, identity)
    )
    if not named and track in _ONE_SHOT_DRUM_TRACKS and sound_type.conflicts(path, track):
        if add("sound_conflicts_with_track"):
            return reasons

    if track in _STRICT_IDENTITY_TRACKS and not named and not sound_type.identifies(path, track):
        add("missing_track_identity")
    return reasons


def track_candidate_rejection_reason(
    path: Path, track: str, *, library_root: Path | None = None
) -> str | None:
    """Return why a concrete file cannot safely serve a track.

    The matcher score is intentionally flexible for creative lanes, but the
    three MIDI-mode anchors are not creative guesses: a kick must identify as
    a kick, an 808 must identify as an 808/bass and have a real body, and hats
    must identify as hats. This helper is shared by matching and final export
    validation so stale JSON cannot bypass the same rules.
    """
    reasons = _collect_rejections(path, track, library_root, first_only=True)
    return reasons[0] if reasons else None


def track_candidate_rejection_reasons(
    path: Path, track: str, *, library_root: Path | None = None
) -> list[str]:
    """All reasons (gate order) — ``[]`` when the file is fully usable."""
    return _collect_rejections(path, track, library_root, first_only=False)


def rejection_reason_for_pattern(path: Path, track: str, pattern: dict[str, Any]) -> str | None:
    """The export-time verdict: the strict gate, minus what selection recorded.

    When no clean sample existed for a lane, ``lane_fallback`` records the file
    it settled on and the SOFT reasons it accepted in
    ``pattern["plg_sample_fallback"][lane]``. Export must not re-litigate that
    decision (two owners disagreeing about one lane is how a fallback used to be
    picked and then vetoed), but it still blocks anything that is not exactly
    the recorded, soft set — a hard reason is never exempt.
    """
    reasons = track_candidate_rejection_reasons(path, track)
    if not reasons:
        return None
    recorded = (pattern.get("plg_sample_fallback") or {}).get(track) if isinstance(pattern, dict) else None
    if isinstance(recorded, dict) and recorded.get("tier") == 1:
        accepted = set(recorded.get("reasons") or ())
        remaining = [reason for reason in reasons if reason not in accepted]
        return remaining[0] if remaining else None
    return reasons[0]


def iter_track_candidates_grouped(catalog: dict[str, Any], track: str) -> list[tuple[str, str]]:
    """Candidates as (rel_path, catalog_group). The group is scan_library's
    content classification — the truth about what a file IS, regardless of the
    user's kit folder naming."""
    # A fresh library scan supplies ``filtered_audio``.  Keep the raw-catalog
    # fallback for old saved catalogs and focused unit tests.
    audio = catalog.get("filtered_audio") or catalog.get("audio") or {}
    profile = TRACK_PROFILES[track]
    seen: set[str] = set()
    ordered: list[tuple[str, str]] = []

    for folder in profile["folders"]:
        for rel in audio.get(folder, []):
            if rel not in seen:
                seen.add(rel)
                ordered.append((rel, folder))

    for folder, files in audio.items():
        if folder in profile["folders"]:
            continue
        for rel in files:
            if rel not in seen:
                seen.add(rel)
                ordered.append((rel, folder))

    return ordered


def iter_track_candidates(catalog: dict[str, Any], track: str) -> list[str]:
    return [rel for rel, _group in iter_track_candidates_grouped(catalog, track)]


class _TrackPool(NamedTuple):
    """The per-candidate scoring pass for one lane, before any tier fallback."""

    scored: list[tuple[int, Path]]
    # Candidates that would collapse into the partner lane's role. Kept aside
    # rather than dropped, so a pool that shares the reference's role entirely
    # still yields a pick instead of nothing.
    role_vetoed: list[tuple[int, Path]]
    # Candidates whose audio content could not be read at all.
    unverified: list[tuple[int, Path]]


def _score_track_pool(
    catalog: dict[str, Any],
    track: str,
    library_root: Path,
    *,
    prompt: str = "",
    style: str = "",
    exclude: set[str] | None = None,
    bonus_keywords: tuple[str, ...] = (),
    bury_keywords: tuple[str, ...] = (),
    variety_rng: random.Random | None = None,
    melody_energy: dict[str, Any] | None = None,
    role_reference: Path | str | None = None,
) -> _TrackPool:
    """Score every candidate of ``track`` — the loop ``pick_best_for_track`` has
    always run, moved here unchanged so the Kit Coherence Scorer can read the
    whole ranked pool instead of only its winner.

    ``variety_rng`` is consumed once per surviving candidate, in catalog order
    (the +-8 rank jitter). That draw order is part of the contract:
    ``pick_best_for_track`` must keep producing the same file for the same seed.
    """
    prompt_tokens = _tokens(prompt)
    style_tokens = _tokens(style)
    blocked = exclude or set()
    profile = TRACK_PROFILES[track]

    energy_bonus: tuple[str, ...] = ()
    energy_bury: tuple[str, ...] = ()
    if track in _KIT_SYMBIOSIS_TRACKS and isinstance(melody_energy, dict):
        try:
            from melody_beat_symbiosis import kit_bias_for_energy

            energy_bonus, energy_bury = kit_bias_for_energy(melody_energy)
        except Exception:  # noqa: BLE001
            energy_bonus, energy_bury = (), ()
    all_bonus = tuple(dict.fromkeys(tuple(bonus_keywords) + energy_bonus))
    all_bury = tuple(dict.fromkeys(tuple(bury_keywords) + energy_bury))

    scored: list[tuple[int, Path]] = []
    # Candidates that would collapse into the partner lane's role. Kept aside
    # rather than dropped, so a pool that shares the reference's role entirely
    # still yields a pick instead of nothing.
    role_vetoed: list[tuple[int, Path]] = []
    # Candidates whose audio content could not be read at all (corrupt/zero
    # -byte file, unsupported codec) — audio_duration_seconds()/analyze() both
    # fail OPEN and return None rather than raising, so name/folder scoring
    # alone can't tell "this IS a kick" apart from "this claims to be a kick
    # but we have zero evidence its content is even valid audio". Kept aside
    # exactly like role_vetoed above: never lets an unverified file beat a
    # verified one, but still fills the lane rather than leaving it empty when
    # nothing else is available.
    unverified: list[tuple[int, Path]] = []
    for rel, group in iter_track_candidates_grouped(catalog, track):
        path = (library_root / rel).resolve()
        if not path.is_file():
            continue
        if str(path) in blocked:
            continue
        # Layer 2 of the two-layer one-shot fix (Layer 1 is library_catalog's
        # _folder_category): a hard duration backstop for the whole one-shot
        # drum family, regardless of which catalog bucket a file landed in —
        # a mislabeled/miscategorized loop must never win a drum lane, not
        # just the "kits" fallback case that first exposed this.
        if track_candidate_rejection_reason(path, track, library_root=library_root):
            continue

        score = score_candidate(
            rel,
            track,
            prompt_tokens=prompt_tokens,
            style_tokens=style_tokens,
            prompt_raw=prompt,
            bonus_keywords=all_bonus,
            bury_keywords=all_bury,
            group=group,
            duration=audio_duration_seconds(path) if track in _ONE_SHOT_DRUM_TRACKS else None,
            audio_vouches=_audio_vouches(path, track),
        )
        unreadable = False
        if track in _ONE_SHOT_DRUM_TRACKS:
            unreadable = audio_duration_seconds(path) is None
            if not unreadable:
                try:
                    import audio_features

                    unreadable = audio_features.analyze_cached(path) is None
                except Exception:  # noqa: BLE001 — verification is advisory, never blocks a pick
                    unreadable = True
        if track == "hi_hats":
            # Prefer snappy closed ticks; long "Hat" one-shots still feel metallic
            # even under the hard duration gate. Prefer darker (lower centroid)
            # over tinny trap hats that read as metal sheet on a 1/8 grid.
            dur = audio_duration_seconds(path)
            if dur is not None:
                if 0.03 <= dur <= 0.09:
                    score += 22
                elif dur <= 0.12:
                    score += 10
            try:
                import audio_features

                feat = audio_features.analyze_cached(path) or {}
                centroid = float(feat.get("centroid", 0.0) or 0.0)
                if 0.0 < centroid <= 8800.0:
                    score += 24
                elif centroid <= 9500.0:
                    score += 10
                elif centroid > 10000.0:
                    score -= 20
            except Exception:  # noqa: BLE001 — scoring must never fail on analysis
                pass
        if track in ("sub_808", "808_bass"):
            # Prefer fat/loud bodies — quiet folder 808s read as distant burps.
            try:
                from audio_features import rms_dbfs

                rms = rms_dbfs(path)
                if rms is not None:
                    if rms >= -7.0:
                        score += 28
                    elif rms >= -10.0:
                        score += 14
                    elif rms < -13.0:
                        score -= 18
            except Exception:  # noqa: BLE001
                pass
            dur = audio_duration_seconds(path)
            # Opposite duration preference per role (2026-07 audit — see
            # TRACK_PROFILES["808_bass"] comment): sub_808 walks the harmony,
            # legato, wants a real sustained body. 808_bass is the short,
            # punchy, kick-locked hit (bass808_gen.py) — a long "808 Sub" file
            # winning this lane was the actual "sounds like a fart" bug.
            if track == "sub_808":
                if dur is not None and dur >= 1.5:
                    score += 12
            elif dur is not None:
                if dur <= 0.6:
                    score += 20
                elif dur <= 1.0:
                    score += 8
                elif dur >= 1.8:
                    score -= 25
                elif dur >= 1.2:
                    score -= 10
            if track == "808_bass":
                # Readability contest for the kick-locked 808.  Names and
                # RMS alone selected slow, almost-pure sine files; reward a
                # tight attack plus a modest harmonic body instead.
                try:
                    import audio_features

                    feat = audio_features.analyze_cached(path) or {}
                    centroid = float(feat.get("centroid", 0.0) or 0.0)
                    attack = float(feat.get("attack", 0.0) or 0.0)
                    body = float(feat.get("transient_body_ratio", 0.0) or 0.0)
                    if 120.0 <= centroid <= 1400.0:
                        score += 34
                    elif 80.0 <= centroid < 120.0:
                        score += 12
                    elif centroid > 1800.0:
                        score -= 25
                    if 2.0 <= attack <= 70.0:
                        score += 20
                    elif attack > 100.0:
                        score -= 28
                    if 0.45 <= body <= 2.4:
                        score += 12
                except Exception:  # noqa: BLE001 — audio analysis is advisory
                    pass
        # Gate off-type files by the catalog GROUP (content classification), not
        # the raw kit folder name — "OUNDWAV_-_UTOPIA_DRUM_KIT/…/808 G.wav" is a
        # first-class 808 candidate even though its top folder isn't "808".
        if group not in profile["folders"] and score < 18:
            continue
        if variety_rng is not None:
            # Per-generation rank shuffle (±8 vs 15-25 point quality tiers):
            # with a fixed prompt the same files topped the list every beat —
            # "те же кики и 808 каждый раз". The seed re-deals the deck.
            score += variety_rng.randint(-8, 8)
        if unreadable:
            # Structurally excluded from the main pool (not merely
            # penalized): a flat score penalty could still lose to the ±8
            # variety jitter above and win anyway. Tiered fallback, same
            # shape as role_vetoed below, makes "never beats a verified
            # candidate" exact regardless of jitter.
            unverified.append((score, path))
            continue
        if role_reference is not None:
            try:
                import audio_features

                if audio_features.roles_collide(path, role_reference):
                    role_vetoed.append((score, path))
                    continue
            except Exception:  # noqa: BLE001 — audio analysis is advisory
                pass
        scored.append((score, path))

    return _TrackPool(scored, role_vetoed, unverified)


def _settle_pool(pool: _TrackPool, track: str, role_reference: Path | str | None) -> list[tuple[int, Path]]:
    """Apply the two fallback tiers: role-vetoed, then unverified, then nothing."""
    scored, role_vetoed, unverified = pool.scored, pool.role_vetoed, pool.unverified
    if not scored and role_vetoed:
        # Every candidate shares the partner's role: a doubled role beats no
        # sample at all, and the lane must always be filled.
        logging.info(
            "Role split: whole %s pool collides with %s, keeping the best anyway",
            track,
            Path(str(role_reference)).name,
        )
        scored = role_vetoed
    if not scored and unverified:
        # No verified candidate at all: an unreadable file is still better
        # than an empty lane, same reasoning as the role_vetoed fallback.
        logging.info(
            "No verified candidate for %s — falling back to an unreadable/unverified file",
            track,
        )
        scored = unverified
    return scored


_MIN_PICK_SCORE = 5  # pick_best_for_track returns None below this


def ranked_pool_for_track(
    catalog: dict[str, Any],
    track: str,
    library_root: Path,
    *,
    prompt: str = "",
    style: str = "",
    exclude: set[str] | None = None,
    bonus_keywords: tuple[str, ...] = (),
    bury_keywords: tuple[str, ...] = (),
    melody_energy: dict[str, Any] | None = None,
    role_reference: Path | str | None = None,
) -> list[tuple[int, Path]]:
    """Every pickable candidate of ``track`` as ``(score, path)``, best first.

    The same scoring ``pick_best_for_track`` uses, with no RNG: no rank jitter,
    no variety band. Ties keep catalog order (stable sort). Candidates under the
    pick floor are left out, so ``result[0]`` is what the argmax pick would be.
    Stage-2 re-ranking by ``audio_target`` / CLAP is not applied: both need a
    prompt, and the MIDI route has none.
    """
    pool = _score_track_pool(
        catalog,
        track,
        library_root,
        prompt=prompt,
        style=style,
        exclude=exclude,
        bonus_keywords=bonus_keywords,
        bury_keywords=bury_keywords,
        variety_rng=None,
        melody_energy=melody_energy,
        role_reference=role_reference,
    )
    scored = _settle_pool(pool, track, role_reference)
    ordered = sorted(scored, key=lambda item: item[0], reverse=True)
    return [(score, path) for score, path in ordered if score >= _MIN_PICK_SCORE]


def pick_best_for_track(
    catalog: dict[str, Any],
    track: str,
    library_root: Path,
    *,
    prompt: str = "",
    style: str = "",
    exclude: set[str] | None = None,
    bonus_keywords: tuple[str, ...] = (),
    bury_keywords: tuple[str, ...] = (),
    audio_target: dict[str, str] | None = None,
    audio_top_k: int = 6,
    use_clap: bool | None = None,
    clap_top_k: int = 8,
    variety_rng: random.Random | None = None,
    melody_energy: dict[str, Any] | None = None,
    role_reference: Path | str | None = None,
) -> tuple[Path | None, int]:
    pool = _score_track_pool(
        catalog,
        track,
        library_root,
        prompt=prompt,
        style=style,
        exclude=exclude,
        bonus_keywords=bonus_keywords,
        bury_keywords=bury_keywords,
        variety_rng=variety_rng,
        melody_energy=melody_energy,
        role_reference=role_reference,
    )
    scored = _settle_pool(pool, track, role_reference)
    if not scored:
        return None, -10_000

    # Stable sort keeps the original candidate order on ties (legacy behaviour).
    order = sorted(range(len(scored)), key=lambda i: scored[i][0], reverse=True)

    rerank_k = max(audio_top_k, clap_top_k) if use_clap is not False else audio_top_k
    shortlist = [scored[i] for i in order[:rerank_k]]

    clap_on = use_clap
    if clap_on is None:
        try:
            import clap_match

            clap_on = clap_match.use_clap() and bool((prompt or style).strip())
        except ImportError:
            clap_on = False

    clap_bonuses: dict[str, int] = {}
    if clap_on and shortlist:
        import clap_match

        text_emb = clap_match.get_text_embedding(prompt, style, track)
        if text_emb is not None:
            clap_bonuses = clap_match.score_paths([p for _, p in shortlist], text_emb)

    if audio_target or clap_bonuses:
        import audio_features

        combined: list[tuple[int, Path]] = []
        for name_score, path in shortlist:
            total = name_score
            if audio_target:
                total += audio_features.feature_match_score(
                    audio_features.analyze_cached(path),
                    audio_target,
                )
            total += clap_bonuses.get(str(path.resolve()), 0)
            combined.append((total, path))
    else:
        combined = [scored[i] for i in order[:rerank_k]]

    combined.sort(key=lambda sp: sp[0], reverse=True)
    best_score, best_path = combined[0]

    # Variety: pick among the TOP good candidates so each generation sounds
    # different but still gorgeous (garbage is already filtered out). Generous band
    # of clearly-positive near-top matches, gently weighted toward the best. Without
    # an RNG, return the argmax.
    if variety_rng is not None and len(combined) > 1:
        # Floor of 20 = needs a real type match (own keyword / strong prompt hit),
        # not just a folder bonus — keeps weak off-type sounds (brass/pizz on a
        # kick) out of the variety pool while still rotating genuine matches.
        # Melodic lanes get a WIDER band: one lucky prompt-token outlier used to
        # shrink the band to a single file — every beat played the same timbre.
        span = 50 if track in ("melody_lead", "counter_melody") else 35
        cutoff = max(20, best_score - span)
        top: list[tuple[int, Path]] = []
        seen_names: set[str] = set()
        for s, p in combined:
            if s < cutoff:
                continue
            # Kits often exist twice (re-download / copy with mangled folder
            # name): same filename = the same sound — one slot in the pool.
            name_key = p.name.lower()
            if name_key in seen_names:
                continue
            seen_names.add(name_key)
            top.append((s, p))
            if len(top) >= (12 if track in ("melody_lead", "counter_melody") else 10):
                break
        if len(top) > 1:
            weights = [1] * len(top)
            best_score, best_path = variety_rng.choices(top, weights=weights, k=1)[0]

    if best_path is None or best_score < 5:
        return None, best_score
    return best_path, best_score


def pick_full_kit(
    catalog: dict[str, Any],
    library_root: Path,
    *,
    prompt: str = "",
    style: str = "",
    melody_energy: dict[str, Any] | None = None,
) -> dict[str, tuple[Path, int]]:
    """Pick one unique wav per drum/melody role — no file reused across channels."""
    used: set[str] = set()
    kit: dict[str, tuple[Path, int]] = {}

    for track in KIT_TRACK_ORDER:
        path, score = pick_best_for_track(
            catalog,
            track,
            library_root,
            prompt=prompt,
            style=style,
            exclude=used,
            melody_energy=melody_energy,
        )
        if path is None:
            continue
        kit[track] = (path, score)
        used.add(str(path.resolve()))

    return kit


def resolve_path_from_library(file_ref: str, library_root: Path) -> Path | None:
    raw = file_ref.strip()
    if not raw:
        return None

    direct = Path(raw)
    if direct.is_file():
        resolved = direct.resolve()
        try:
            relative_in_library(resolved, library_root)
        except ValueError:
            return None
        if resolved.name.startswith("._") or any(part.lower() == "__macosx" for part in resolved.parts):
            return None
        return resolved

    relative = library_root / raw
    if relative.is_file():
        resolved = relative.resolve()
        try:
            relative_in_library(resolved, library_root)
        except ValueError:
            return None
        if resolved.name.startswith("._") or any(part.lower() == "__macosx" for part in resolved.parts):
            return None
        return resolved

    target = Path(raw).name.lower()
    if not target:
        return None

    for path in library_root.rglob("*"):
        if path.name.startswith("._") or any(part.lower() == "__macosx" for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS and path.name.lower() == target:
            return path.resolve()
    return None


def merge_llm_sample_picks(
    sound_map: dict[str, Path],
    samples_layer: list[dict[str, Any]],
    library_root: Path,
    *,
    prompt: str = "",
    style: str = "",
) -> dict[str, Path]:
    """Apply the LLM's samples[] picks over the matcher — but VALIDATE them first.
    The LLM picks from the catalog by name and often chooses wrong (a 139-bpm loop
    on the melody, an 808 on the kick); those used to silently override the clean
    matcher pick. Now a pick that scores as the wrong type for its channel is
    ignored, keeping the matcher's correct sound."""
    merged = dict(sound_map)
    p_tokens = _tokens(prompt)
    s_tokens = _tokens(style)
    for entry in samples_layer:
        if not isinstance(entry, dict):
            continue
        track = entry.get("track")
        file_ref = entry.get("file")
        if track not in merged or not file_ref:
            continue
        resolved = resolve_path_from_library(str(file_ref), library_root)
        if resolved is None:
            continue
        if audio_rejection_reason(resolved, track=str(track)):
            continue
        if track in TRACK_PROFILES:
            try:
                rel = relative_in_library(resolved, library_root).as_posix()
            except ValueError:
                rel = resolved.name
            score = score_candidate(
                rel, str(track), prompt_tokens=p_tokens, style_tokens=s_tokens, prompt_raw=prompt
            )
            if score < 0:
                continue  # wrong type for this channel — keep the matcher's pick
        merged[str(track)] = resolved
    return merged
