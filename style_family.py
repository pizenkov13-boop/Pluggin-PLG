"""Style families — three separate worlds; what fits one must NOT bleed to another.

The user's rule from the reference breakdowns (FOUR worlds):
  - CARTI  (opium / plugg / dark_trap): tension & distorted dark. Semitone
    CLUSTER chords, dense mechanical hats, COWBELL, 808 on the root with a
    slide — no walk-ups, no pretty extensions.
  - KEN    (rage / twizzy / hyper_rage — Ken Carson / Destroy Lonely): FAST
    (150-160), textured but MELODIC — clean chord progressions, NOT clusters.
    One-step hats (every 16th slot filled), deliberately unquantized leads,
    LOUD distorted 808, structure via long↔short 808 switch-ups. No cowbell.
  - TRAVIS (trap): cinematic arc & scene. Ascending minor-2nd 808 walk-ups into
    drops, atmosphere/scene textures, verse-vs-hook register switch.
  - MELODIC (melodic_trap / pop_rap / rnb / lofi / boombap …): clean, singable
    melody first — none of the above aggression.
  - PHONK: NOT a trap-family derivative — a separate electronic tradition
    (Memphis rap roots, now its own scene). Dense, driving COWBELL ostinato
    (not Carti's sparse off-beat clank), distorted/boosted 808, chopped/
    screwed vocal samples (not yet built — see the vocal-chop backlog item).
    Architecturally independent: does not inherit Carti/Ken/Travis defaults.

Gate every stylistic move through these so a Carti move never lands on a Ken
or Travis beat and vice-versa — and so a Carti/Ken/Travis move never lands
on a Phonk beat either.
"""

from __future__ import annotations

CARTI_GENRES = frozenset({"opium", "plugg", "dark_trap"})
KEN_GENRES = frozenset({"rage", "twizzy", "hyper_rage"})
TRAVIS_GENRES = frozenset({"trap", "psychedelic", "cinematic"})
TOLIVER_GENRES = frozenset({"toliver"})
KANYE_GENRES = frozenset({"kanye", "soul_flip"})
PHONK_GENRES = frozenset({"phonk"})


def genre_of(data: dict) -> str:
    variety = data.get("plg_variety") if isinstance(data.get("plg_variety"), dict) else {}
    return str(variety.get("genre") or data.get("plg_genre") or "").strip().lower()


def is_carti(genre: str) -> bool:
    return str(genre or "").strip().lower() in CARTI_GENRES


def is_ken(genre: str) -> bool:
    return str(genre or "").strip().lower() in KEN_GENRES


def is_travis(genre: str) -> bool:
    return str(genre or "").strip().lower() in TRAVIS_GENRES


def is_toliver(genre: str) -> bool:
    return str(genre or "").strip().lower() in TOLIVER_GENRES


def is_kanye(genre: str) -> bool:
    return str(genre or "").strip().lower() in KANYE_GENRES


def is_phonk(genre: str) -> bool:
    return str(genre or "").strip().lower() in PHONK_GENRES
