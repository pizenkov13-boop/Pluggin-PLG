"""Prompt → timbre descriptor matching for sample selection.

Turns the *character* a user asks for — dark, hard, distorted, glassy, deep,
vintage, punchy… in English or Russian — into filename keyword hints, so the
picker grabs the 808 / kick / melody that actually sounds like the request
instead of a random file from the dump folder. Hints ride the existing
``bonus_keywords`` channel in sample_match, so they stack with Rule 16 pairing.

Each family is track-aware: "glassy" only nudges the melody, "knock" only the
drums, while broad vibes (dark, rage) apply everywhere. Pass ``track`` to
``descriptor_hints`` to get only the families relevant to that channel.
"""

from __future__ import annotations

# family -> (prompt triggers EN+RU, filename hint keywords, tracks | () = all)
DESCRIPTOR_FAMILIES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "distorted": (
        ("distort", "dist", "overdrive", "grimy", "gritty", "fuzz", "saturat", "screw",
         "crunch", "dirty", "дист", "искаж", "грязн", "перегруз", "хрип"),
        ("dist", "distort", "drive", "od", "sat", "grime", "grimy", "fuzz", "crush",
         "dirty", "screw", "crunch"),
        ("sub_808", "kick", "melody_lead"),
    ),
    "dark": (
        ("dark", "evil", "sinister", "gloom", "grim", "doom", "haunt", "creepy",
         "тёмн", "темн", "злой", "мрачн", "жутк"),
        ("dark", "evil", "night", "gloom", "shadow", "grim", "doom", "haunt",
         "creep", "blakk", "black"),
        (),
    ),
    "hard": (
        ("hard", "aggress", "heavy", "banger", "brutal", "violent",
         "жёстк", "жестк", "агресс", "тяжёл", "тяжел", "мощн"),
        ("hard", "heavy", "aggress", "bang", "war", "brutal", "slam"),
        ("kick", "snare", "clap", "sub_808"),
    ),
    "clean": (
        ("clean", "pure", "smooth", "polished", "чист", "гладк"),
        ("clean", "pure", "dry", "smooth"),
        (),
    ),
    "deep": (
        ("deep", "subby", "low end", "boom", "thick", "rumble", "fat",
         "глубок", "низк", "саб", "жирн", "бубн"),
        ("deep", "sub", "low", "boom", "thick", "rumble", "fat"),
        ("sub_808", "kick"),
    ),
    "punchy": (
        ("punch", "tight", "snappy", "knock", "click", "snap",
         "панч", "чётк", "четк", "хлёст", "хлест"),
        ("punch", "tight", "knock", "snap", "click", "tick", "pop"),
        ("kick", "snare", "snare_layer", "clap"),
    ),
    "vintage": (
        ("vintage", "lofi", "lo-fi", "retro", "vinyl", "dusty", "tape", "analog",
         "винтаж", "ретро", "пыльн", "плёнк", "пленк", "старый"),
        ("vintage", "lofi", "lo_fi", "retro", "vinyl", "dust", "tape", "analog", "warm"),
        ("melody_lead",),
    ),
    "bright": (
        ("bright", "glassy", "glass", "crystal", "shiny", "shine", "bell", "airy",
         "светл", "ярк", "стекл", "звонк", "колокол"),
        ("bright", "glass", "crystal", "shine", "bell", "air", "glassy"),
        ("melody_lead",),
    ),
    "metallic": (
        ("metal", "metallic", "steel", "industrial", "металл", "сталь"),
        ("metal", "steel", "metallic", "industr"),
        ("melody_lead", "hi_hats"),
    ),
    "rage": (
        ("rage", "dark trap", "underground", "pluggnb", "plugg", "jerk", "sigma"),
        ("rage", "dark", "distort", "plugg", "aggressive"),
        (),
    ),
    "phonk": (
        ("phonk", "drift", "memphis", "cowbell", "фонк", "дрифт"),
        ("phonk", "drift", "memphis", "cowbell"),
        (),
    ),
    "drill": (
        ("drill", "дрилл", "uk drill", "ny drill"),
        ("drill", "slide", "uk", "ny"),
        ("sub_808", "hi_hats"),
    ),
    "jersey": (
        ("jersey", "club", "джерси", "bed squeak", "bounce"),
        ("jersey", "club", "bounce", "squeak"),
        ("kick", "sub_808"),
    ),
    "hyperpop": (
        ("hyperpop", "hyper pop", "гиперпоп", "glitch", "detune", "supersaw"),
        ("hyper", "glitch", "detune", "supersaw", "saw", "pitch"),
        ("melody_lead",),
    ),
    "pop_rap": (
        ("pop rap", "pop-rap", "поп рэп", "поп-рэп", "club rap", "female rap"),
        ("bounce", "club", "brass", "pluck", "piano", "catchy", "stab"),
        (),
    ),
    "house": (
        ("house", "хаус"),
        ("house", "deep", "groove", "organ", "piano", "stab", "4x4"),
        (),
    ),
    "techno": (
        ("techno", "техно"),
        ("techno", "berlin", "rumble", "analog", "industrial", "acid"),
        (),
    ),
    "trance": (
        ("trance", "транс", "psytrance"),
        ("trance", "supersaw", "pluck", "uplift", "acid", "gate"),
        ("melody_lead", "hi_hats"),
    ),
    "dnb": (
        ("drum and bass", "dnb", "днб", "jungle", "neurofunk", "драм-н-бейс"),
        ("dnb", "jungle", "break", "amen", "reese", "neuro", "liquid"),
        (),
    ),
    "dubstep": (
        ("dubstep", "дабстеп", "wobble", "riddim", "brostep"),
        ("dubstep", "wobble", "growl", "riddim", "sub", "bro"),
        ("sub_808", "melody_lead", "kick", "snare"),
    ),
    "garage": (
        ("garage", "ukg", "2-step", "гараж"),
        ("garage", "ukg", "2step", "shuffle", "organ", "chop"),
        (),
    ),
    "lofi": (
        ("lofi", "lo-fi", "chillhop", "чилхоп", "лоуфай", "study beat"),
        ("lofi", "dust", "vinyl", "tape", "warm", "rhodes", "jazz", "mellow"),
        (),
    ),
    "boombap": (
        ("boom bap", "boombap", "бум бэп", "old school hip hop", "90s hip hop", "golden era"),
        ("boom", "bap", "vinyl", "break", "dusty", "90s", "mpc", "chop"),
        (),
    ),
    "reggaeton": (
        ("reggaeton", "реггетон", "dembow", "perreo", "latin"),
        ("reggaeton", "dembow", "latin", "perc", "conga", "bongo", "pluck"),
        (),
    ),
    "dancehall": (
        ("dancehall", "дансхолл"),
        ("dancehall", "riddim", "island", "steel", "rim"),
        (),
    ),
    "afrobeats": (
        ("afrobeat", "afroswing", "afropop", "афробит", "afro house"),
        ("afro", "perc", "shaker", "conga", "kalimba", "marimba", "log"),
        (),
    ),
    "amapiano": (
        ("amapiano", "амапиано", "log drum"),
        ("amapiano", "log", "piano", "shaker", "yano", "perc"),
        (),
    ),
    "funk": (
        ("funk", "фанк", "funky"),
        ("funk", "slap", "wah", "clav", "brass", "horn", "stab"),
        (),
    ),
    "soul": (
        ("soul", "соул", "motown", "gospel", "госпел"),
        ("soul", "rhodes", "keys", "organ", "motown", "gospel", "choir", "warm"),
        ("melody_lead",),
    ),
    "rock": (
        ("rock", "рок", "metal", "метал", "punk", "панк", "grunge", "guitar", "гитар"),
        ("rock", "guitar", "live", "room", "crash", "punk", "metal", "amp"),
        (),
    ),
    "hardstyle": (
        ("hardstyle", "хардстайл", "gabber", "rawstyle"),
        ("hardstyle", "gabber", "hard", "raw", "screech", "distort"),
        ("kick", "sub_808", "melody_lead"),
    ),
    "cinematic": (
        ("cinematic", "orchestral", "оркестр", "кинематограф", "trailer", "soundtrack", "саундтрек"),
        ("orchestral", "strings", "brass", "choir", "hit", "impact", "taiko", "epic", "ensemble"),
        (),
    ),
}


def descriptor_hints(prompt: str = "", style: str = "", track: str | None = None) -> tuple[str, ...]:
    """Filename keyword hints for the descriptor families the text triggers.

    If ``track`` is given, only families relevant to that channel contribute.
    """
    text = f"{prompt} {style}".lower()
    hints: list[str] = []
    seen: set[str] = set()
    for triggers, keywords, tracks in DESCRIPTOR_FAMILIES.values():
        if track is not None and tracks and track not in tracks:
            continue
        if any(trig in text for trig in triggers):
            for keyword in keywords:
                if keyword not in seen:
                    seen.add(keyword)
                    hints.append(keyword)
    return tuple(hints)
