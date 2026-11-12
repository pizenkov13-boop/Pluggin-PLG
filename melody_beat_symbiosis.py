"""Melody → full-kit beat symbiosis.

Melody MIDI is the host organism. Kick / snare / clap /
hats / 808 / snare_layer / cowbell / fx answer their energy and attack —
both as programming bias and as sample-name bias.
"""

from __future__ import annotations

import bisect
import hashlib
import logging
from typing import Any

from pattern_utils import track_notes

logger = logging.getLogger(__name__)

BEATS_PER_BAR = 4.0
_DENSITY_HOT = 1.15
_DENSITY_COLD = 0.85


def _rng01(seed: int, *parts: Any) -> float:
    raw = hashlib.md5(f"{seed}|{'|'.join(map(str, parts))}".encode("utf-8")).hexdigest()
    return int(raw[:8], 16) / 0xFFFFFFFF


# Producer law: hi-hats never sit below C4 — lower reads as a heavy/unnatural
# "hat", not a hat. (This codebase's note convention has C5 = MIDI 60, so
# C4 = 48 — see bass808_gen.BASS_MIDI_CENTER for the same convention.)
HAT_PITCH_FLOOR_MIDI = 48


def _clamp_hat_pitch_floor(note_name: str) -> str:
    try:
        from music_theory import name_for_midi, parse_note_name

        midi = parse_note_name(note_name)
    except (KeyError, TypeError, ValueError):
        return note_name
    if midi >= HAT_PITCH_FLOOR_MIDI:
        return note_name
    while midi < HAT_PITCH_FLOOR_MIDI:
        midi += 12
    return name_for_midi(midi)

_DRUM_LANES = (
    "kick",
    "snare",
    "clap",
    "hi_hats",
    "sub_808",
    "808_bass",
    "snare_layer",
    "cowbell",
    "fx",
)

# Filename bias for the whole kit (not just hats/808).
_SHARP_BOOST = (
    "dry",
    "tight",
    "snap",
    "hard",
    "short",
    "punch",
    "crack",
    "crisp",
    "sharp",
    "knock",
)
_SHARP_BURY = (
    "lofi",
    "washed",
    "long",
    "soft",
    "warm",
    "dusty",
    "vinyl",
    "tape",
)
_LUSH_BOOST = (
    "dark",
    "deep",
    "soft",
    "warm",
    "long",
    "round",
    "boom",
    "subby",
    "muted",
)
_LUSH_BURY = (
    "crisp",
    "metallic",
    "bright",
    "thin",
    "clicky",
    "digital",
)

# Role-specific kit character — stronger than generic sharp/lush.
_ROLE_KIT_BIAS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "pluck": (
        ("dry", "tight", "snap", "crisp", "tick", "closed", "short", "perc", "digital", "hard"),
        ("boom", "dusty", "long", "washed", "lofi", "warm", "soft"),
    ),
    "bell": (
        ("crisp", "bright", "tick", "snap", "thin", "short"),
        ("boom", "subby", "long", "mud", "dusty"),
    ),
    "synth": (
        ("digital", "hard", "punch", "tight", "distort", "crush"),
        ("acoustic", "dusty", "vinyl", "soft"),
    ),
    "thin_ep": (
        ("dark", "deep", "round", "boom", "warm", "soft", "muted"),
        ("click", "digital", "thin", "metallic", "chip", "crisp"),
    ),
    "key": (
        ("dark", "deep", "punch", "tight", "hard"),
        ("washed", "lofi", "vinyl", "sparkle"),
    ),
    "piano": (
        ("acoustic", "dry", "punch", "tight", "natural"),
        ("distort", "crush", "digital", "chip"),
    ),
    "thick_pad": (
        ("deep", "long", "soft", "warm", "subby", "round"),
        ("snap", "click", "crisp", "thin", "short"),
    ),
    "strings": (
        ("deep", "warm", "soft", "long", "round"),
        ("snap", "digital", "crush", "thin"),
    ),
}

_HOT_SECTION_NAMES = frozenset({"chorus", "drop", "hook"})
_COLD_SECTION_NAMES = frozenset({"verse", "outro", "intro", "prehook"})


def _mutation_seed(pattern: dict[str, Any]) -> tuple[int | None, str | None]:
    mutation = pattern.get("plg_melody_mutation")
    if not isinstance(mutation, dict):
        mutation = {}
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    seed = mutation.get("seed")
    if seed is None:
        seed = variety.get("seed")
    try:
        seed_i = int(seed) if seed is not None else None
    except (TypeError, ValueError):
        seed_i = None
    mode = mutation.get("mode")
    return seed_i, str(mode) if mode else None


def ensure_melody_energy(pattern: dict[str, Any]) -> dict[str, Any]:
    """Stamp the melody energy card the whole kit answers.

    Until 2026-08-28 this also picked a FLEX pack/preset before drums ran. That
    pick reached the drum RNG seed, so the pocket depended on a third-party
    preset catalogue and on a rotating pick. The melody energy card is derived
    from the MIDI itself and is all the drums ever needed.
    """
    return build_melody_energy_card(pattern)


# Historical name kept as a thin alias: callers outside this module still use it.
ensure_early_flex_and_energy = ensure_melody_energy


def build_melody_energy_card(pattern: dict[str, Any]) -> dict[str, Any]:
    """Compact melody signal for drum programming and kit matching."""
    notes = track_notes(pattern, "melody_lead")
    onset_rate = 0.0
    short_frac = 0.0
    syncopation = 0.0
    poly = 0.0
    coverage = 0.0
    median_len = 1.0

    if len(notes) >= 2:
        try:
            from melody_character import _extract_sieve_features

            feat = _extract_sieve_features(notes)
            if feat is not None:
                onset_rate = float(feat.onset_rate)
                short_frac = float(feat.short_frac)
                syncopation = float(feat.syncopation)
                poly = float(feat.poly)
                coverage = float(feat.coverage)
                median_len = float(feat.median_len)
        except Exception:  # noqa: BLE001
            pass

    character = pattern.get("plg_melody_character")
    if not isinstance(character, dict):
        character = {}
        try:
            from melody_character import resolve_melody_character

            bpm = float(pattern.get("bpm") or 140)
            ch = resolve_melody_character(notes, bpm=bpm, pattern=pattern)
            character = {
                "tempo_band": ch.tempo_band,
                "attack": ch.attack,
                "vel_mode": ch.vel_mode,
                "pocket_muted": ch.pocket_muted,
                "dissonant": ch.dissonant,
                "min_onset_gap": ch.min_onset_gap,
            }
            pattern["plg_melody_character"] = character
        except Exception:  # noqa: BLE001
            character = {
                "tempo_band": "mid",
                "attack": "neutral",
                "vel_mode": "neutral",
                "pocket_muted": False,
                "dissonant": False,
                "min_onset_gap": 0.5,
            }

    attack = str(character.get("attack") or "neutral")
    role = str(pattern.get("plg_melody_role") or "pluck")
    vibe = str(pattern.get("plg_melody_vibe") or "neutral")
    section_density = _section_density_ratios(pattern)
    bpm = float(pattern.get("bpm") or 140)
    min_gap = float(character.get("min_onset_gap") or 0.5)

    # Pace the DRUMS must match — half-time trap on a racing MIDI feels broken.
    pace = _melody_pace(
        bpm=bpm,
        onset_rate=onset_rate,
        short_frac=short_frac,
        median_len=median_len,
        min_onset_gap=min_gap,
    )
    if pace == "racing" and attack == "lush":
        attack = "sharp"
        character = dict(character)
        character["attack"] = "sharp"
        pattern["plg_melody_character"] = character

    # 0..1 "разъеб" score the kit answers.
    busyness = max(
        0.0,
        min(
            1.0,
            0.35 * min(1.0, onset_rate / 4.0)
            + 0.25 * short_frac
            + 0.20 * syncopation
            + 0.10 * (1.0 if attack == "sharp" else 0.0)
            + 0.10 * (1.0 if role in ("pluck", "bell", "synth") else 0.0),
        ),
    )
    lushness = max(
        0.0,
        min(
            1.0,
            0.35 * coverage
            + 0.25 * poly
            + 0.20 * (1.0 if median_len >= 1.2 else median_len / 1.2)
            + 0.10 * (1.0 if attack == "lush" else 0.0)
            + 0.10 * (1.0 if role in ("thin_ep", "thick_pad", "strings", "piano") else 0.0),
        ),
    )
    if pace == "racing":
        busyness = max(busyness, 0.72)
        lushness *= 0.35
    elif pace == "driving":
        busyness = max(busyness, 0.55)
        lushness *= 0.55

    card = {
        "onset_rate": round(onset_rate, 4),
        "short_frac": round(short_frac, 4),
        "syncopation": round(syncopation, 4),
        "poly": round(poly, 4),
        "coverage": round(coverage, 4),
        "median_len": round(median_len, 4),
        "attack": attack,
        "min_onset_gap": min_gap,
        "role": role,
        "vibe": vibe,
        "pace": pace,
        "bpm": round(bpm, 2),
        "busyness": round(busyness, 4),
        "lushness": round(lushness, 4),
        "section_density": section_density,
    }

    pattern["plg_melody_energy"] = card
    return card


def _section_density_ratios(pattern: dict[str, Any]) -> list[dict[str, float]]:
    arrangement = pattern.get("plg_arrangement")
    melody = track_notes(pattern, "melody_lead")
    if not isinstance(arrangement, list) or len(melody) < 2:
        return []

    sections: list[tuple[float, float, int]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        try:
            start_bar = int(section.get("start_bar", 0))
            bars = int(section.get("bars", 0))
        except (TypeError, ValueError):
            continue
        if bars > 0:
            sections.append(
                (start_bar * BEATS_PER_BAR, (start_bar + bars) * BEATS_PER_BAR, bars)
            )
    if len(sections) < 2:
        return []

    def _density(start: float, end: float, bars: int) -> float:
        count = sum(
            1
            for n in melody
            if isinstance(n, dict) and start <= float(n.get("time_step", 0)) < end
        )
        return count / max(1, bars)

    densities = [_density(s, e, b) for s, e, b in sections]
    avg = sum(densities) / len(densities)
    if avg <= 0:
        return []
    out: list[dict[str, float]] = []
    for (s, e, _b), d in zip(sections, densities):
        out.append(
            {
                "start": float(s),
                "end": float(e),
                "ratio": round(d / avg, 4),
            }
        )
    return out


def _melody_pace(
    *,
    bpm: float,
    onset_rate: float,
    short_frac: float,
    median_len: float,
    min_onset_gap: float,
) -> str:
    """racing | driving | pocket — how fast the drum grid must feel."""
    # High onset OR compressed gaps = melody is moving; BPM alone is not enough
    # (150 BPM half-time still feels slow under a 16th hook).
    if (
        onset_rate >= 3.2
        or min_onset_gap <= 0.35
        or (short_frac >= 0.40 and onset_rate >= 2.0)
        or (bpm >= 140 and onset_rate >= 2.6 and median_len <= 1.1)
    ):
        return "racing"
    if (
        onset_rate >= 1.8
        or min_onset_gap <= 0.55
        or short_frac >= 0.28
        or (bpm >= 145 and onset_rate >= 1.4)
    ):
        return "driving"
    return "pocket"


def kit_bias_for_energy(card: dict[str, Any] | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(boost_keywords, bury_keywords) for sample_match across the full kit."""
    if not isinstance(card, dict):
        return (), ()
    busyness = float(card.get("busyness") or 0.0)
    lushness = float(card.get("lushness") or 0.0)
    attack = str(card.get("attack") or "neutral")
    role = str(card.get("role") or "")
    vibe = str(card.get("vibe") or "")
    pace = str(card.get("pace") or "pocket")

    boost: list[str] = []
    bury: list[str] = []

    role_boost, role_bury = _ROLE_KIT_BIAS.get(role, ((), ()))
    boost.extend(role_boost)
    bury.extend(role_bury)

    if pace == "racing":
        # Never the slow/edgy boom kit under a fast melody.
        boost.extend(("tight", "snap", "short", "crisp", "dry", "punch", "hard", "closed", "tick"))
        bury.extend(("slow", "boom", "long", "deep", "subby", "washed", "distort", "crush", "dirt", "lofi"))
    elif pace == "driving":
        boost.extend(("tight", "punch", "dry", "snap", "hard"))
        bury.extend(("slow", "washed", "long", "lofi"))

    sharpish = attack == "sharp" or busyness >= 0.55 or role in ("pluck", "bell")
    lushish = attack == "lush" or lushness >= 0.55 or role in ("thin_ep", "thick_pad", "strings")

    if sharpish and not lushish:
        boost.extend(_SHARP_BOOST)
        bury.extend(_SHARP_BURY)
    elif lushish and not sharpish:
        boost.extend(_LUSH_BOOST)
        bury.extend(_LUSH_BURY)
    elif sharpish and lushish:
        boost.extend(("dry", "tight", "deep", "dark"))
        bury.extend(("washed", "metallic", "vinyl"))

    if vibe == "rage":
        boost.extend(("hard", "distort", "crush", "anger", "punch"))
        bury.extend(("soft", "lullaby", "bell", "warm"))
    elif vibe == "sweet_dark":
        boost.extend(("dark", "deep", "warm", "night", "muted"))
        bury.extend(("bright", "sparkle", "chip", "thin"))

    def _uniq(items: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return tuple(out)

    return _uniq(boost), _uniq(bury)


def melody_modulated_section_style(
    name: str,
    base_energy: float,
    base_hat_density: float,
    base_kick_drive: float,
    card: dict[str, Any] | None,
) -> tuple[float, float, float]:
    """Amplify verse↔chorus contrast from the melody energy card.

    Used by arranger so section development answers the host melody, not only
    the static name table.
    """
    energy = base_energy
    hat_density = base_hat_density
    kick_drive = base_kick_drive
    if not isinstance(card, dict):
        return energy, hat_density, kick_drive

    busyness = float(card.get("busyness") or 0.0)
    lushness = float(card.get("lushness") or 0.0)
    attack = str(card.get("attack") or "neutral")
    name_l = (name or "").lower()

    pace = str(card.get("pace") or "pocket")
    if pace == "racing":
        # Fast melody → every section stays mobile; verse is not a half-time nap.
        energy = min(1.12, max(energy, 0.88) + 0.08 * busyness)
        hat_density = min(1.9, max(hat_density, 1.15) * (1.2 + 0.25 * busyness))
        kick_drive = min(0.9, max(kick_drive, 0.35) + 0.2 * busyness)
        if name_l in _COLD_SECTION_NAMES:
            hat_density = max(0.85, hat_density * 0.9)
        return energy, hat_density, kick_drive

    if name_l in _HOT_SECTION_NAMES:
        boost = 0.12 + 0.18 * busyness + (0.08 if attack == "sharp" else 0.0)
        energy = min(1.12, energy + boost)
        hat_density = min(1.8, hat_density * (1.15 + 0.35 * busyness))
        kick_drive = min(0.85, kick_drive + 0.15 + 0.25 * busyness)
    elif name_l in _COLD_SECTION_NAMES:
        cut = 0.08 + 0.14 * lushness + (0.06 if attack == "lush" else 0.0)
        energy = max(0.45, energy - cut)
        hat_density = max(0.25, hat_density * (0.75 - 0.2 * lushness))
        kick_drive = max(0.0, kick_drive * (0.55 - 0.2 * lushness))

    return energy, hat_density, kick_drive


# What a bar's position is worth against what the melody actually plays there.
#
# The musical half of the score below — phrase gap, velocity, note length —
# spans roughly 1.4 to 4.4 in practice. A positional bonus of the same size
# therefore does not TILT the choice, it MAKES it, and the melody stops being
# consulted at all.
#
# Measured 2026-08-19 on the five-file listening corpus, with the half-bar
# bonus at its old 2.0: `plg_melody_accents` came out as exactly two accents
# per bar, at 0.0 and 2.0, in EVERY bar of EVERY file — 2 distinct positions,
# 100 % on beats one and three. Every bar of every file has candidates off
# those beats (76/76, 60/60, 72/72 bars), and their best musical score is
# level with the best on-beat one (3.62 vs 3.62 on Sahara, 3.51 vs 3.49 on
# Shuttle, 4.38 vs 4.31 on deyflo). The material was tied; position decided
# every single time.
#
# That is the root of the metronomic kick the user reported on all five files
# including the one they judged good. `midi_lowend_director._local_score`
# weights accent coincidence highest (2.0), so the pocket was being rewarded
# for landing on a CONSTANT grid — the scorer was working correctly on an
# input that carried no information.
#
# The downbeat bonus stays at 3.0: a bar needs an anchor, and it holds in
# 100 % of bars at every setting tried. The half-bar bonus becomes a
# tie-break — it still prefers beat three when the material is genuinely
# level, and no longer outranks a real musical event elsewhere.
#
# Calibrated across all five files (distinct kick-bar shapes / share of the
# most common one), not on a single example:
#
#     bonus   Sahara      Shuttle     deyflo      Graveyard   Detroit
#     2.0     1 / 100 %   1 / 100 %   2 /  96 %   1 / 100 %   1 / 100 %
#     1.0     1 / 100 %   3 /  82 %   3 /  71 %   1 / 100 %   1 / 100 %
#     0.5     3 /  92 %   6 /  62 %   3 /  46 %   7 /  63 %   7 /  58 %
#     0.25    3 /  83 %   7 /  48 %   4 /  50 %   7 /  43 %   7 /  36 %
#     0.0     3 /  66 %   7 /  32 %   4 /  62 %   7 /  27 %   7 /  19 %
#
# The table above came from a REIMPLEMENTATION of the selection loop and its
# tie-break ran the other way round, which matters here because the scores
# genuinely tie. Re-measured on the real pipeline, 0.5 and 0.25 move only
# Shuttle and deyflo and leave Detroit at 80 % one shape:
#
#     bonus   Sahara      Graveyard   Detroit     Shuttle     deyflo
#     2.0     60 % / 94   69 % / 88   80 % / 91   98 % / 100  89 % / 94
#     0.5     60 % / 94   69 % / 88   80 % / 91   83 % / 100  58 % / 84
#     0.0     54 % / 88   63 % / 77   23 % / 88   31 % /  94  58 % / 84
#              (share of the most common kick bar-shape / % of bars with a kick on beat one)
#
# Hence 0.0. The half-bar keeps no bonus at all: beat three earns an accent
# when the material puts one there, like any other position. The bar's
# anchor is the DOWNBEAT bonus above, which is untouched.
#
# The beat-one share falling (Graveyard 88 -> 77 %) is variation, not damage:
# the kicks move onto beat two and the eighths, and the one position that
# actually sounds wrong — the sixteenth just after a beat, the project issue log (not included) 0p —
# does not grow anywhere (Graveyard 2 % either way, Detroit 4 % -> 0 %).
# Watch that number, not the beat-one share, when retuning this.
_ACCENT_DOWNBEAT_BONUS = 3.0
_ACCENT_HALF_BAR_BONUS = 0.0


def _crowds_the_backbeat(t: float) -> int:
    """1 when this instant would speak in FRONT of the bar's backbeat.

    A pure tie-break, never a bonus. §0q retired the +2.0 half-bar bonus for
    the right reason — over a musical range of about 1.4 to 4.4 a bonus that
    size does not tilt the choice, it makes it — and that decision stands.
    This term sits AFTER the score in the sort key, so on any bar whose
    material actually separates its candidates it is never consulted at all.

    It exists because on flat material the material does NOT separate them.
    A pad holding one chord onset on every beat with notes three beats long
    pins both musical terms at their ceiling (the gap term saturates past 0.9
    beats, the length term past 1.0), leaving velocity as the only live input
    — and on the 2026-08-25 corpus that produced an EXACT tie in 80 % of
    Tragic's bars and 100 % of Sahara's, against 0 % on Baton Rouge and
    Graveyard. The old key broke those ties on `t` ascending, so the earliest
    candidate won every time, which on a four-on-the-floor pad is always the
    beat before the backbeat: 56 of Tragic's 152 accents and 67 of Sahara's
    landed there, against 0 of Baton Rouge's 160.

    That is what the kick then answered — 27 of Tragic's 34 pre-backbeat
    kicks sat on one of these accents — and it is the reading that separates
    the two files judged best (2 % and 8 % of their off-downbeat kicks speak
    before the backbeat) from the three that were not (39-70 %).

    The downbeat is never "crowding": it is the bar's own anchor and carries
    its own bonus above.

    Only the stretch in FRONT of the backbeat is demoted; landing on it and
    answering after it rank the same, and the existing "earlier wins" order
    then settles between them. Ranking "after" above "on" was tried and is a
    closed decision against: it moved Tragic's whole accent map to the
    downbeat plus beat four, the last paired bar came out with no low end at
    all, and `incomplete_kick_coverage` rejected the generation three rounds
    running — a hard error for the user, where the shipped code merely sounds
    wrong. Sahara is the file that pays for this ordering; see the project issue log (not included).
    """
    # The single owner of where a backbeat lands (midi_phrase_drums keeps the
    # anchor fixed by design). Never re-derive it as "half a bar".
    from midi_phrase_drums import _BACKBEAT_BAR_OFFSET

    beat_in_bar = t % BEATS_PER_BAR
    return int(0.12 <= beat_in_bar < _BACKBEAT_BAR_OFFSET - 0.12)


def _accent_candidates(melody: list[dict[str, Any]]) -> dict[int, list[tuple[float, float]]]:
    """Score every accent candidate, per bar. The single owner of that score.

    Split out 2026-08-25 so `melody_accent_firmness` can read the same numbers
    `extract_melody_accents` ranks on. Two functions scoring the melody with
    two private copies of one formula is the defect class this project has paid
    for most often; there is one copy and both callers use it.
    """
    if not melody:
        return {}
    ordered = sorted(
        (n for n in melody if isinstance(n, dict)),
        key=lambda n: float(n.get("time_step", 0) or 0),
    )
    candidates_by_bar: dict[int, list[tuple[float, float]]] = {}
    prev_t = -999.0
    for note in ordered:
        t = round(float(note.get("time_step", 0) or 0), 5)
        vel = int(note.get("velocity", 100) or 100)
        length = max(0.0, float(note.get("length", 0.0) or 0.0))
        gap = t - prev_t
        beat_in_bar = t % BEATS_PER_BAR
        on_bar_downbeat = beat_in_bar < 0.12
        on_half_bar = abs(beat_in_bar - 2.0) < 0.12
        phrase_entry = gap >= 0.45
        strong = phrase_entry or vel >= 105 or on_bar_downbeat or on_half_bar
        if strong:
            score = (
                (_ACCENT_DOWNBEAT_BONUS if on_bar_downbeat else 0.0)
                + (_ACCENT_HALF_BAR_BONUS if on_half_bar else 0.0)
                + min(2.0, max(0.0, gap) / 0.45)
                + min(1.5, max(0, vel - 92) / 13.0)
                + min(1.0, length / 1.0)
            )
            candidates_by_bar.setdefault(int(t // BEATS_PER_BAR), []).append((score, t))
        prev_t = t
    return candidates_by_bar


# One MIDI velocity unit, in the score's own units — the velocity term is
# `(vel - 92) / 13`, so a single unit of loudness is worth this much. A bar
# decided by less than that was not decided by the material.
ACCENT_FIRMNESS_FLOOR = 1.0 / 13.0


def melody_accent_firmness(melody: list[dict[str, Any]]) -> dict[str, Any]:
    """How firmly the melody itself chooses where the kit's accent goes.

    The kit has two failure modes and they are opposite, which is why one
    global setting cannot serve both (the project issue log (not included) §0x):

    * where the melody states a rhythm, pressing for variation OVERRIDES it —
      measured on Baton Rouge, the file judged best, where §0u's section
      step-down took the kick off the "1" in 23 of 48 verse bars, every second
      bar of a two-bar loop;
    * where the melody states nothing, the kit's position is decided by
      defaults and reads as arbitrary — Tragic and Sahara.

    Both files were treated identically because nothing measured the
    difference. This does. For each bar it takes the margin between the
    winning off-downbeat candidate and the best candidate at a different
    position; a margin of zero means the score was tied and the sort order,
    not the music, chose. Measured over the listening corpus, the share of
    bars decided by at least one velocity unit is:

        Baton Rouge 100 %   Graveyard 52 %   deyflo 40 %   Tragic 20 %   Sahara 0 %

    which is the same order as the ear, and no other reading in this codebase
    separates them (`tension` and the section label measured degenerate in
    §0v.1, and the whole-bar melody onset count is flat on most material).

    Returns per-bar margins plus `firm_bars`, and `share` over the bars that
    had a contest at all. A file with no contested bar reports share 0.0 and
    every consumer then behaves exactly as it did before this existed.
    """
    candidates = _accent_candidates(melody)
    margins: dict[int, float] = {}
    for bar, rows in candidates.items():
        off = [(s, t) for s, t in rows if (t % BEATS_PER_BAR) >= 0.12]
        if not off:
            continue
        best_at: dict[float, tuple[float, float]] = {}
        for s, t in off:
            key = round(t, 2)
            if key not in best_at or s > best_at[key][0]:
                best_at[key] = (s, t)
        ranked = sorted(best_at.values(), key=lambda r: (-r[0], r[1]))
        winner = ranked[0]
        rival = next((r for r in ranked[1:] if abs(r[1] - winner[1]) >= 0.35), None)
        if rival is None:
            # Only one position was ever on offer: the material left no choice
            # to make, which is not the same as a coin-flip and must not be
            # counted as one.
            continue
        margins[bar] = round(winner[0] - rival[0], 6)
    firm = {b for b, m in margins.items() if m >= ACCENT_FIRMNESS_FLOOR}
    return {
        "margins": margins,
        "firm_bars": sorted(firm),
        "contested": len(margins),
        "share": round(len(firm) / len(margins), 4) if margins else 0.0,
    }


def extract_melody_accents(melody: list[dict[str, Any]]) -> list[float]:
    """A small set of phrase anchors the drums may answer.

    Dense MIDI often uses one flat, high velocity for every note.  Treating
    velocity alone as an accent made essentially the whole melody an accent
    map, so rank candidates in musical context and keep at most two per bar.
    """
    candidates_by_bar = _accent_candidates(melody)
    accents: list[float] = []
    for bar in sorted(candidates_by_bar):
        chosen: list[float] = []
        for _score, _crowds, t in sorted(
            (
                (score, _crowds_the_backbeat(t), t)
                for score, t in candidates_by_bar[bar]
            ),
            key=lambda item: (-item[0], item[1], item[2]),
        ):
            if all(abs(t - prior) >= 0.35 for prior in chosen):
                chosen.append(t)
            if len(chosen) >= 2:
                break
        accents.extend(chosen)
    return sorted(accents)


_MICRO_PAUSE_ABS_FLOOR = 0.75
_MICRO_PAUSE_MEDIAN_MULT = 3.0
_MICRO_PAUSE_PAD = 0.05


def find_melody_micro_pauses(melody: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Local breaths inside the melody itself — independent of arrangement
    sections. A gap is flagged relative to THIS melody's own median onset
    gap (racing melodies have a tiny median, ~0.25-0.35 beat, so a ~1-beat
    breath there already reads as a real pause without needing a fixed
    absolute threshold tuned for one tempo).

    Used to give ``pace == "racing"`` melodies real cold-window thinning —
    they used to get none at all (see apply_melody_beat_symbiosis), which
    meant the busiest/most rage-typical melodies never let the kit breathe.
    """
    ordered = sorted(
        (n for n in melody if isinstance(n, dict)),
        key=lambda n: float(n.get("time_step", 0) or 0),
    )
    if len(ordered) < 3:
        return []

    onsets = [float(n.get("time_step", 0) or 0) for n in ordered]
    gaps = [b - a for a, b in zip(onsets, onsets[1:]) if b > a]
    if not gaps:
        return []
    sorted_gaps = sorted(gaps)
    mid = len(sorted_gaps) // 2
    median_gap = (
        sorted_gaps[mid]
        if len(sorted_gaps) % 2
        else (sorted_gaps[mid - 1] + sorted_gaps[mid]) / 2.0
    )
    threshold = max(_MICRO_PAUSE_ABS_FLOOR, _MICRO_PAUSE_MEDIAN_MULT * median_gap)

    windows: list[tuple[float, float]] = []
    for cur, nxt in zip(ordered, ordered[1:]):
        cur_t = float(cur.get("time_step", 0) or 0)
        nxt_t = float(nxt.get("time_step", 0) or 0)
        # Flag on inter-ONSET spacing (consistent with median_gap above) —
        # a long note that rings right up to the next attack has no audible
        # silence even if its onset gap is technically wide, so the emitted
        # window below still only spans the literal silent stretch.
        if nxt_t - cur_t < threshold:
            continue
        cur_end = cur_t + float(cur.get("length", 0) or 0)
        start = cur_end + _MICRO_PAUSE_PAD
        end = nxt_t - _MICRO_PAUSE_PAD
        if end > start:
            windows.append((start, end))
    return windows


def _hot_cold_windows(
    pattern: dict[str, Any],
    sections: list[dict[str, float]],
    card: dict[str, Any],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Hot/cold windows from melody density, or synthesized from section names."""
    hot = [(s["start"], s["end"]) for s in sections if float(s.get("ratio", 1.0)) >= _DENSITY_HOT]
    cold = [(s["start"], s["end"]) for s in sections if float(s.get("ratio", 1.0)) <= _DENSITY_COLD]
    if hot or cold:
        return hot, cold

    # Looped MIDI → flat density ratios. Fall back to arrangement names × card.
    arrangement = pattern.get("plg_arrangement")
    if not isinstance(arrangement, list):
        return [], []
    busyness = float(card.get("busyness") or 0.0)
    lushness = float(card.get("lushness") or 0.0)
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        try:
            start = int(section.get("start_bar", 0)) * BEATS_PER_BAR
            end = start + int(section.get("bars", 0)) * BEATS_PER_BAR
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        name = str(section.get("name") or "").lower()
        if name in _HOT_SECTION_NAMES and busyness >= 0.25:
            hot.append((start, end))
        elif name in _COLD_SECTION_NAMES and (lushness >= 0.25 or busyness <= 0.55):
            cold.append((start, end))
    return hot, cold


# How close an existing hi-hat has to be for an accent to count as already
# answered. The check used to be `round(accent, 3) in existing_h` — an EXACT
# key on three decimal places of beats, which at 150 BPM is 0.4 ms.
#
# It cannot work where it sits. This pass runs at beat_humanize:6768, long
# after apply_hi_hat_swing / apply_open_hat_choke / apply_hat_forward_lag
# (:6147-6194) have moved every existing hat off the grid, so the hat an
# accent means to reuse is typically 0.002-0.006 beats away — a different key.
# Measured 2026-08-21 across the five listening files: of the hats this pass
# adds, 30 of 108 on Sahara and 21 of 108 on Tragic landed within 15 ms of a
# hat that was already there, the closest pair 0.7 ms apart. Two copies of the
# same 60 ms broadband transient a fraction of a millisecond apart comb-filter
# into a metallic hiss — the "horrible noise on Sahara's hats" report.
#
# 0.08 beats — the SAME tolerance the velocity-boost branch of this very
# function has always used to decide whether a hat is on an accent. One
# function answering "is this the same instant?" two different ways is the
# defect; the fix is to answer it once. The tightest LEGITIMATE spacing in the
# generated lane is about 0.142 beats (a 32nd roll is 0.125), so this cannot
# eat a real roll. 0.06 was tried first and left 14 pairs on Sahara and 7 on
# Tragic at 25-30 ms, just past its own edge — two distinct ticks rather than
# the metallic comb, but still not something either hat was placed to do.
_ACCENT_HAT_MIN_GAP = 0.08


def _hat_within(times: list[float], point: float, gap: float) -> bool:
    """Is there already a hat within ``gap`` beats of ``point``?"""
    i = bisect.bisect_left(times, point - gap)
    return i < len(times) and times[i] <= point + gap


def lock_drums_to_melody_accents(
    tracks: dict[str, Any],
    accents: list[float],
    *,
    midi_hats: bool,
    attack: str,
    move_kick_timing: bool = True,
    hat_budget_per_bar: int | None = None,
) -> dict[str, int]:
    """Nudge/boost kick·snare·clap·hats so they sit on melody accents.

    ``move_kick_timing=False`` (bass808_enabled family) keeps the velocity
    boost but never touches kick's time_step. That family's kick is
    phase-locked to 808_bass at birth (bass808_gen, via
    beat_humanize._apply_808_bass_lane) — moving it afterward silently
    drifted the two apart (measured: 808 ended up before the kick instead
    of after) until a compensating re-align pass caught it every time.
    Simpler not to move it in the first place, matching how this same
    function already treats snare/clap/hats (velocity/extra-note only,
    never a timing move)."""
    stats = {"kick_nudged": 0, "kick_added": 0, "snare_boosted": 0, "hat_boosted": 0}
    if not accents:
        return stats

    kick = tracks.get("kick")
    if isinstance(kick, list) and kick:
        kick_out = [dict(n) if isinstance(n, dict) else n for n in kick]
        existing = {
            round(float(n.get("time_step", 0) or 0), 3)
            for n in kick_out
            if isinstance(n, dict)
        }
        for accent in accents:
            beat_in_bar = accent % BEATS_PER_BAR
            # Kick answers downbeat / and-of accents, not every melody tick.
            if beat_in_bar > 0.35 and abs(beat_in_bar - 2.0) > 0.35 and beat_in_bar < 3.4:
                if attack != "sharp":
                    continue
            nearest_i = None
            nearest_d = 0.12
            for i, note in enumerate(kick_out):
                if not isinstance(note, dict):
                    continue
                d = abs(float(note.get("time_step", 0) or 0) - accent)
                if d < nearest_d:
                    nearest_d = d
                    nearest_i = i
            if nearest_i is not None:
                note = kick_out[nearest_i]
                old_t = float(note.get("time_step", 0) or 0)
                # Nudge only — never invent extra kicks (that made slow edgy
                # grids denser in the wrong places and broke groove tests).
                if move_kick_timing and abs(old_t - accent) > 0.01:
                    note["time_step"] = accent
                    stats["kick_nudged"] += 1
                note["melody_accent_lock"] = True
                vel = int(note.get("velocity", 110) or 110)
                note["velocity"] = max(vel, min(127, vel + 8))
        tracks["kick"] = sorted(
            kick_out, key=lambda n: float(n.get("time_step", 0) or 0) if isinstance(n, dict) else 0
        )

    for lane, key in (("snare", "snare_boosted"), ("clap", "snare_boosted")):
        notes = tracks.get(lane)
        if not isinstance(notes, list):
            continue
        out = []
        for note in notes:
            if not isinstance(note, dict):
                out.append(note)
                continue
            n = dict(note)
            t = float(n.get("time_step", 0) or 0)
            if any(abs(t - a) <= 0.15 for a in accents):
                n["velocity"] = min(127, int(n.get("velocity", 100) or 100) + 10)
                n["melody_accent_lock"] = True
                stats[key] += 1
            out.append(n)
        tracks[lane] = out

    if not midi_hats:
        hats = tracks.get("hi_hats")
        if isinstance(hats, list) and hats:
            out = [dict(n) if isinstance(n, dict) else n for n in hats]
            # Occupancy as TIMES, compared with a tolerance — not as rounded
            # keys. See _ACCENT_HAT_MIN_GAP for what the exact-key version
            # cost. Kept sorted so the check below is a bisect, not a scan
            # over the whole lane for every accent.
            existing_h = sorted(
                float(n.get("time_step", 0) or 0) for n in out if isinstance(n, dict)
            )
            for note in out:
                if not isinstance(note, dict):
                    continue
                t = float(note.get("time_step", 0) or 0)
                if any(abs(t - a) <= 0.08 for a in accents):
                    note["velocity"] = min(127, int(note.get("velocity", 80) or 80) + 12)
                    note["melody_accent_lock"] = True
                    stats["hat_boosted"] += 1
            # Sharp melodies get a short hat tick on accents that had no hat.
            #
            # This is the single largest writer of the hat lane (+118..+152
            # notes, measured 2026-08-19 across the corpus) and it used to be
            # unbounded: one hat per melody accent, placed exactly ON the
            # melody's own attack, gated only by attack == "sharp". On a
            # sparse melody that reads as locking in; on a dense one it doubles
            # every note the melody plays and is the loudest single source of
            # the masking. It now stops at the bar's transient budget
            # (drum_density.hat_floor_per_bar) — full strength where the
            # material leaves room, nothing where it does not.
            per_bar_h: dict[int, int] = {}
            for note in out:
                if isinstance(note, dict):
                    b = int(float(note.get("time_step", 0) or 0) // BEATS_PER_BAR)
                    per_bar_h[b] = per_bar_h.get(b, 0) + 1
            if attack == "sharp":
                # An accent hat joins the lane at the lane's OWN level, because
                # these notes are appended AFTER apply_six_db_hat_rule has run
                # and nothing downstream ever levels them against the backbeat.
                # The rule this pass established in the project issue log (not included) 0v.2 now lives
                # in drum_density.late_hat_velocity, where the three OTHER
                # passes that append after the leveller read it too (0v.8) —
                # this call is that same computation, not a second copy of it.
                from drum_density import late_hat_velocity

                accent_vel = late_hat_velocity(out, 88)
                for accent in accents:
                    if _hat_within(existing_h, accent, _ACCENT_HAT_MIN_GAP):
                        continue
                    if (accent % 0.5) > 0.12:  # stay near 8th grid
                        continue
                    bar_h = int(float(accent) // BEATS_PER_BAR)
                    if hat_budget_per_bar is not None and per_bar_h.get(bar_h, 0) >= hat_budget_per_bar:
                        continue
                    per_bar_h[bar_h] = per_bar_h.get(bar_h, 0) + 1
                    out.append(
                        {
                            "time_step": accent,
                            "note": _clamp_hat_pitch_floor("F#3"),
                            "length": 0.12,
                            "velocity": accent_vel,
                            "melody_accent_lock": True,
                        }
                    )
                    bisect.insort(existing_h, float(accent))
                    stats["hat_boosted"] += 1
            tracks["hi_hats"] = sorted(
                out, key=lambda n: float(n.get("time_step", 0) or 0) if isinstance(n, dict) else 0
            )

    return stats


def retune_808_to_melody(
    sub_notes: list[dict[str, Any]],
    melody: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Walk 808 pitches onto the melody's low root at each hit time."""
    if not sub_notes or not melody:
        return sub_notes, 0
    try:
        from music_theory import detect_root_pc, name_for_midi, parse_note_name
        from sub_808_gen import SUB_HI, SUB_LO, _clamp_sub_midi
    except Exception:  # noqa: BLE001
        return sub_notes, 0

    mel_sorted = sorted(
        (n for n in melody if isinstance(n, dict)),
        key=lambda n: float(n.get("time_step", 0) or 0),
    )
    fallback_pc = detect_root_pc(melody) % 12 if melody else 4
    changed = 0
    out: list[dict[str, Any]] = []

    for note in sub_notes:
        if not isinstance(note, dict):
            out.append(note)
            continue
        n = dict(note)
        t = float(n.get("time_step", 0) or 0)
        bar_start = (t // BEATS_PER_BAR) * BEATS_PER_BAR
        bar_end = bar_start + BEATS_PER_BAR
        in_bar = [
            m
            for m in mel_sorted
            if bar_start <= float(m.get("time_step", 0) or 0) < bar_end
        ]
        # Prefer melody notes near the 808 attack; else lowest in the bar.
        near = [
            m
            for m in in_bar
            if abs(float(m.get("time_step", 0) or 0) - t) <= 0.75
        ]
        pool = near or in_bar
        if pool:
            try:
                root_midi = min(parse_note_name(str(m["note"])) for m in pool)
            except (ValueError, TypeError, KeyError):
                root_midi = SUB_LO + fallback_pc
        else:
            root_midi = SUB_LO + fallback_pc
        root_midi = _clamp_sub_midi(root_midi)
        # Keep in sub register (C1–B2-ish).
        while root_midi > SUB_HI:
            root_midi -= 12
        while root_midi < SUB_LO:
            root_midi += 12
        new_name = name_for_midi(root_midi)
        if str(n.get("note")) != new_name:
            n["note"] = new_name
            n["melody_808_lock"] = True
            changed += 1
        out.append(n)
    return out, changed


_PHRASE_BEATS = 16.0  # matches bass808_gen.PHRASE_BEATS — the grid 808_bass already breathes on
_PHRASE_ROOT_MIN_NOTES = 3
_PHRASE_ROOT_MIN_SHARE = 0.45
_PHRASE_ROOT_FOLLOW_PROB = 0.5


def detect_phrase_root_overrides(
    melody: list[dict[str, Any]],
    *,
    start: float,
    bars: int,
    global_root_pc: int,
    seed: int,
) -> dict[int, int]:
    """Phrase-level (4-bar) melody root vs. the song's pinned progression.

    Minimal, rare-by-design harmony reaction for 808_bass (see
    bass808_gen.generate_808_from_kicks' ``phrase_root_overrides`` param) —
    NOT the same as retune_808_to_melody's note-by-note walk above (that's
    sub_808's job). Only proposes an override when a phrase's melody has a
    dominant, well-supported pitch class that genuinely differs from the
    song root — then a seeded 50/50 coin flip decides whether the bass
    actually follows it that phrase. Absence of signal (most phrases, most
    songs) means zero behavior change from the canned progression walk.
    """
    if not melody or bars <= 0:
        return {}
    try:
        from music_theory import _safe_midi
    except Exception:  # noqa: BLE001
        return {}

    end = start + bars * BEATS_PER_BAR
    n_phrases = max(1, int((end - start + _PHRASE_BEATS - 1e-6) // _PHRASE_BEATS))
    global_pc = global_root_pc % 12

    overrides: dict[int, int] = {}
    for phrase_idx in range(n_phrases):
        p_start = start + phrase_idx * _PHRASE_BEATS
        p_end = p_start + _PHRASE_BEATS
        weights: dict[int, float] = {}
        total = 0.0
        count = 0
        for note in melody:
            if not isinstance(note, dict):
                continue
            t = float(note.get("time_step", 0) or 0)
            if not (p_start <= t < p_end):
                continue
            midi = _safe_midi(str(note.get("note", "")))
            if midi is None:
                continue
            weight = max(1.0, float(note.get("length", 0.25) or 0.25)) * max(
                1, int(note.get("velocity", 100) or 100)
            )
            pc = midi % 12
            weights[pc] = weights.get(pc, 0.0) + weight
            total += weight
            count += 1
        if count < _PHRASE_ROOT_MIN_NOTES or total <= 0:
            continue
        local_pc, local_weight = max(weights.items(), key=lambda kv: (kv[1], -kv[0]))
        if local_pc == global_pc or local_weight / total < _PHRASE_ROOT_MIN_SHARE:
            continue
        if _rng01(seed, "harmony_follow", phrase_idx) < _PHRASE_ROOT_FOLLOW_PROB:
            overrides[phrase_idx] = local_pc
    return overrides


def densify_hats_in_windows(
    hats: list[dict[str, Any]],
    windows: list[tuple[float, float]],
) -> tuple[list[dict[str, Any]], int]:
    """Add missing 16th hats inside hot windows (chorus разъеб)."""
    if not hats or not windows:
        return hats, 0
    out = [dict(n) if isinstance(n, dict) else n for n in hats]
    existing = {
        round(float(n.get("time_step", 0) or 0), 3)
        for n in out
        if isinstance(n, dict)
    }
    added = 0
    for start, end in windows:
        t = start
        while t < end - 1e-6:
            key = round(t, 3)
            if key not in existing:
                # Fill only off-grid 16ths relative to existing 8ths sparingly.
                if abs((t % 0.5) - 0.25) < 0.02:
                    out.append(
                        {
                            "time_step": round(t, 5),
                            "note": _clamp_hat_pitch_floor("F#3"),
                            "length": 0.12,
                            "velocity": 72,
                            "melody_section_densify": True,
                        }
                    )
                    existing.add(key)
                    added += 1
            t += 0.25
    out.sort(key=lambda n: float(n.get("time_step", 0) or 0) if isinstance(n, dict) else 0)
    return out, added


def apply_melody_beat_symbiosis(
    pattern: dict[str, Any], *, move_kick_timing: bool = True
) -> dict[str, Any]:
    """Reshape the full drum kit so it answers the melody energy card.

    Call after drum blueprint / hats / 808 are materialized. Skips MIDI-owned
    hat lanes. Returns a small stats dict for producer meta.

    ``move_kick_timing=False`` — see lock_drums_to_melody_accents' own
    docstring; the caller passes False for bass808_enabled profiles, whose
    kick is phase-locked to 808_bass at birth.
    """
    card = pattern.get("plg_melody_energy")
    if not isinstance(card, dict):
        card = build_melody_energy_card(pattern)
    else:
        sections_now = _section_density_ratios(pattern)
        if sections_now:
            card = dict(card)
            card["section_density"] = sections_now
            pattern["plg_melody_energy"] = card

    tracks = pattern.get("tracks")
    if not isinstance(tracks, dict):
        return {"applied": False}

    melody = track_notes(pattern, "melody_lead")
    accents = extract_melody_accents(melody)

    stats = {
        "applied": True,
        "busyness": card.get("busyness"),
        "lushness": card.get("lushness"),
        "role": card.get("role"),
        "attack": card.get("attack"),
        "velocity_scaled": 0,
        "hats_thinned": 0,
        "hats_densified": 0,
        "kick_extras_thinned": 0,
        "808_lengths_tuned": 0,
        "808_retuned": 0,
        "perc_thinned": 0,
        "accents": len(accents),
        "kick_nudged": 0,
        "kick_added": 0,
        "snare_boosted": 0,
        "hat_boosted": 0,
    }

    busyness = float(card.get("busyness") or 0.0)
    lushness = float(card.get("lushness") or 0.0)
    attack = str(card.get("attack") or "neutral")
    vibe = str(card.get("vibe") or "neutral")
    sections = card.get("section_density") if isinstance(card.get("section_density"), list) else []

    # 1) Accent lock — drums sit on the melody's phrase hits.
    from drum_density import density_card, hat_floor_per_bar

    lock_stats = lock_drums_to_melody_accents(
        tracks,
        accents,
        midi_hats=bool(pattern.get("plg_midi_hats")),
        attack=attack,
        move_kick_timing=move_kick_timing,
        hat_budget_per_bar=hat_floor_per_bar(density_card(pattern)),
    )
    stats.update(lock_stats)

    # 2) sub_808 pitches follow melody roots. 808_bass stays flat on C5
    # (FL Sampler root + Cut Itself law) — never walk pitch stairs on that lane.
    notes = tracks.get("sub_808")
    if isinstance(notes, list) and notes:
        retuned, n = retune_808_to_melody(notes, melody)
        tracks["sub_808"] = retuned
        stats["808_retuned"] += n

    # Global velocity ceiling for the whole kit.
    global_vel = 1.0
    if vibe == "rage" or busyness >= 0.65:
        global_vel = 1.08
    elif lushness >= 0.65 and busyness <= 0.35:
        global_vel = 0.90
    elif attack == "sharp":
        global_vel = 1.04
    elif attack == "lush":
        global_vel = 0.94

    for lane in _DRUM_LANES:
        if lane == "hi_hats" and pattern.get("plg_midi_hats"):
            continue
        notes = tracks.get(lane)
        if not isinstance(notes, list) or not notes:
            continue
        scaled = _scale_lane_velocity(notes, global_vel, sections)
        tracks[lane] = scaled
        stats["velocity_scaled"] += len(scaled)

    pace = str(card.get("pace") or "pocket")
    # Length-tune only the Sub lane. 808_bass keeps its long C5 humming tails.
    notes = tracks.get("sub_808")
    if isinstance(notes, list) and notes:
        if pace == "racing":
            tracks["sub_808"] = _tune_808_lengths(notes, attack="sharp", lushness=0.0, busyness=0.9)
        else:
            tracks["sub_808"] = _tune_808_lengths(
                notes, attack=attack, lushness=lushness, busyness=busyness
            )
        stats["808_lengths_tuned"] += len(tracks["sub_808"])

    hot, cold = _hot_cold_windows(pattern, sections, card)
    if pace == "racing":
        # Keep the whole SECTION mobile — don't thin verse into a nap — but
        # still let the kit breathe in the melody's own local pauses, even
        # inside a hot section (a breath is a breath regardless of section).
        cold = find_melody_micro_pauses(melody)
        if not hot:
            arrangement = pattern.get("plg_arrangement")
            if isinstance(arrangement, list):
                hot = []
                for section in arrangement:
                    if not isinstance(section, dict):
                        continue
                    try:
                        start = int(section.get("start_bar", 0)) * BEATS_PER_BAR
                        end = start + int(section.get("bars", 0)) * BEATS_PER_BAR
                    except (TypeError, ValueError):
                        continue
                    if end > start:
                        hot.append((start, end))

    if hot:
        hot_boost = 1.12 if busyness >= 0.5 else 1.08
        for lane in ("kick", "snare", "clap", "hi_hats", "snare_layer", "cowbell"):
            if lane == "hi_hats" and pattern.get("plg_midi_hats"):
                continue
            notes = tracks.get(lane)
            if isinstance(notes, list) and notes:
                tracks[lane] = _boost_section_velocity(notes, hot, hot_boost)
        if not pattern.get("plg_midi_hats"):
            hats = tracks.get("hi_hats")
            if isinstance(hats, list):
                # Read the density card before making a hat-density decision
                # (2026-08-15). Densifying material whose card sits below the
                # trim threshold refills exactly what drum_density's trim
                # owner just removed — the measured non-monotonicity: quiet
                # files ended up with MORE hats than dense ones. Symmetric
                # with thin_generated_hats: a busy card (>= 0.95) may
                # densify, a quieter card may not. No card -> old behaviour.
                _card = pattern.get("plg_drum_density")
                _card = _card if isinstance(_card, dict) else {}
                try:
                    _hat_mult = float(_card.get("hat_multiplier") or 1.0)
                except (TypeError, ValueError):
                    _hat_mult = 1.0
                if _hat_mult >= 0.95:
                    densified, n = densify_hats_in_windows(hats, hot)
                    tracks["hi_hats"] = densified
                    stats["hats_densified"] += n
        for lane in ("sub_808", "808_bass"):
            notes = tracks.get(lane)
            if isinstance(notes, list) and notes:
                tracks[lane] = _stretch_808_in_windows(notes, hot, factor=0.85)

    if cold:
        kick = tracks.get("kick")
        if isinstance(kick, list):
            kept, n = _thin_tagged(kick, cold, tags=("kick_sync", "ghost", "fill", "section_drive"))
            tracks["kick"] = kept
            stats["kick_extras_thinned"] += n

        if not pattern.get("plg_midi_hats"):
            hats = tracks.get("hi_hats")
            if isinstance(hats, list):
                kept, n = _thin_quiet_hats(hats, cold)
                tracks["hi_hats"] = kept
                stats["hats_thinned"] += n

        for lane in ("snare_layer", "cowbell", "fx"):
            notes = tracks.get(lane)
            if isinstance(notes, list) and notes:
                kept, n = _thin_in_windows(notes, cold, keep_frac=0.35)
                tracks[lane] = kept
                stats["perc_thinned"] += n

        for lane in ("sub_808", "808_bass"):
            notes = tracks.get(lane)
            if isinstance(notes, list) and notes:
                tracks[lane] = _stretch_808_in_windows(notes, cold, factor=1.2)

        # Cold sections: soften snare/clap so chorus hits harder by contrast.
        for lane in ("snare", "clap"):
            notes = tracks.get(lane)
            if isinstance(notes, list) and notes:
                tracks[lane] = _boost_section_velocity(notes, cold, 0.88)

    pattern["plg_melody_beat_symbiosis"] = stats
    logger.info(
        "Melody–beat symbiosis v2: role=%s attack=%s busy=%.2f lush=%.2f "
        "accents=%d kick_nudge=%d 808_retune=%d hat_dense=%d hat_thin=%d",
        card.get("role"),
        card.get("attack"),
        busyness,
        lushness,
        stats["accents"],
        stats["kick_nudged"] + stats["kick_added"],
        stats["808_retuned"],
        stats["hats_densified"],
        stats["hats_thinned"],
    )
    return stats


def _in_windows(t: float, windows: list[tuple[float, float]]) -> bool:
    return any(s <= t < e for s, e in windows)


def _window_index(t: float, windows: list[tuple[float, float]]) -> int | None:
    """Which window (by list position) contains ``t``, or None."""
    for i, (s, e) in enumerate(windows):
        if s <= t < e:
            return i
    return None


def _scale_lane_velocity(
    notes: list[dict[str, Any]],
    factor: float,
    sections: list[dict[str, float]],
) -> list[dict[str, Any]]:
    if abs(factor - 1.0) < 0.01 and not sections:
        return notes
    out: list[dict[str, Any]] = []
    for note in notes:
        if not isinstance(note, dict):
            out.append(note)
            continue
        n = dict(note)
        vel = int(n.get("velocity", 100) or 100)
        local = factor
        t = float(n.get("time_step", 0) or 0)
        for sec in sections:
            if float(sec.get("start", 0)) <= t < float(sec.get("end", 0)):
                ratio = float(sec.get("ratio", 1.0))
                if ratio >= _DENSITY_HOT:
                    local *= 1.05
                elif ratio <= _DENSITY_COLD:
                    local *= 0.94
                break
        n["velocity"] = max(1, min(127, int(round(vel * local))))
        out.append(n)
    return out


def _boost_section_velocity(
    notes: list[dict[str, Any]],
    windows: list[tuple[float, float]],
    factor: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for note in notes:
        if not isinstance(note, dict):
            out.append(note)
            continue
        n = dict(note)
        t = float(n.get("time_step", 0) or 0)
        if _in_windows(t, windows):
            vel = int(n.get("velocity", 100) or 100)
            n["velocity"] = max(1, min(127, int(round(vel * factor))))
        out.append(n)
    return out


def _tune_808_lengths(
    notes: list[dict[str, Any]],
    *,
    attack: str,
    lushness: float,
    busyness: float,
) -> list[dict[str, Any]]:
    if attack == "sharp" or busyness >= 0.6:
        factor = 0.95
    elif attack == "lush" or lushness >= 0.6:
        factor = 1.18
    else:
        return notes
    out: list[dict[str, Any]] = []
    for note in notes:
        if not isinstance(note, dict):
            out.append(note)
            continue
        n = dict(note)
        length = float(n.get("length", 1.0) or 1.0)
        n["length"] = max(0.5, min(16.0, length * factor))
        out.append(n)
    return out


def _stretch_808_in_windows(
    notes: list[dict[str, Any]],
    windows: list[tuple[float, float]],
    factor: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for note in notes:
        if not isinstance(note, dict):
            out.append(note)
            continue
        n = dict(note)
        t = float(n.get("time_step", 0) or 0)
        if _in_windows(t, windows):
            length = float(n.get("length", 1.0) or 1.0)
            n["length"] = max(0.25, min(16.0, length * factor))
        out.append(n)
    return out


def _thin_tagged(
    notes: list[dict[str, Any]],
    windows: list[tuple[float, float]],
    *,
    tags: tuple[str, ...],
) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    removed = 0
    for note in notes:
        if isinstance(note, dict) and any(note.get(tag) for tag in tags):
            t = float(note.get("time_step", 0) or 0)
            if _in_windows(t, windows):
                removed += 1
                continue
        kept.append(note)
    return kept, removed


def _thin_quiet_hats(
    notes: list[dict[str, Any]],
    windows: list[tuple[float, float]],
) -> tuple[list[dict[str, Any]], int]:
    """Drop soft / roll / open extras in cold windows; keep the 1/8 skeleton."""
    kept: list[dict[str, Any]] = []
    removed = 0
    for note in notes:
        if not isinstance(note, dict):
            kept.append(note)
            continue
        t = float(note.get("time_step", 0) or 0)
        if not _in_windows(t, windows):
            kept.append(note)
            continue
        vel = int(note.get("velocity", 100) or 100)
        is_extra = bool(
            note.get("hat_roll")
            or note.get("roll")
            or note.get("open")
            or note.get("ghost")
            or vel < 70
        )
        # Also thin off-eighth chatter (odd 16ths) when tagged or very soft.
        step = t % 1.0
        off_eighth = abs(step - 0.0) > 0.05 and abs(step - 0.5) > 0.05
        if is_extra or (off_eighth and vel < 90):
            removed += 1
            continue
        kept.append(note)
    return kept, removed


def _thin_in_windows(
    notes: list[dict[str, Any]],
    windows: list[tuple[float, float]],
    *,
    keep_frac: float,
) -> tuple[list[dict[str, Any]], int]:
    """Deterministically keep ~keep_frac of the notes that fall inside any
    of ``windows``, leaving everything outside them untouched.

    2026-08 fix: this used to run ONE running counter (``idx_in_cold``)
    across ALL windows combined, keyed purely on ``idx % 100 < keep_frac*100``
    — so a window's kept/dropped outcome depended on how many notes every
    EARLIER window in the list had already consumed from that shared
    counter. A real trap generation (72 bars) had ``_COLD_SECTION_NAMES``
    classify the final "verse" + "outro" as one continuous cold stretch
    (bar 60-72); by the time the counter reached it, its phase sat entirely
    in the removed 65% for that window's whole note count (14/14 dropped),
    silently deleting snare_layer from the last third of the song — the
    real cause behind the historical "Snare Layer '145 bars'"-class bug
    (see _seal_song_note_bounds' docstring, which only guards the opposite
    direction: a note placed PAST the song end, not a lane emptied out
    before it). Keying the counter PER WINDOW instead (reset to 0 at each
    window's first note) means a window's own outcome only depends on its
    own note count/order, never on how many notes came before it in
    earlier windows — so a short trailing window can no longer be silently
    zeroed out by accumulated phase from unrelated earlier windows.
    """
    kept: list[dict[str, Any]] = []
    removed = 0
    idx_per_window: dict[int, int] = {}
    for note in notes:
        if not isinstance(note, dict):
            kept.append(note)
            continue
        t = float(note.get("time_step", 0) or 0)
        window_i = _window_index(t, windows)
        if window_i is not None:
            idx = idx_per_window.get(window_i, 0)
            # Keep a deterministic fraction so rerolls stay stable.
            keep = (idx % 100) < int(keep_frac * 100)
            idx_per_window[window_i] = idx + 1
            if not keep:
                removed += 1
                continue
        kept.append(note)
    return kept, removed


def merge_flex_producer_meta(pattern: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Preserve early-FLEX melody_* producer flags after humanize overwrite."""
    prev = pattern.get("plg_producer_meta")
    if isinstance(prev, dict):
        for key, value in prev.items():
            if str(key).startswith("melody_") and key not in meta:
                meta[key] = value
    character = pattern.get("plg_melody_character")
    if isinstance(character, dict):
        if character.get("pocket_muted"):
            meta.setdefault("melody_pocket_muted", True)
        attack = character.get("attack")
        if attack == "sharp":
            meta.setdefault("melody_prefer_sharp_attack", True)
        elif attack == "lush":
            meta.setdefault("melody_prefer_lush_release", True)
    sym = pattern.get("plg_melody_beat_symbiosis")
    if isinstance(sym, dict):
        meta["melody_beat_symbiosis"] = sym
    energy = pattern.get("plg_melody_energy")
    if isinstance(energy, dict):
        meta["melody_energy_busyness"] = energy.get("busyness")
        meta["melody_energy_lushness"] = energy.get("lushness")
    return meta
