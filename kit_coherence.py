"""Kit Coherence Scorer: choose the drum kit as a WHOLE, not lane by lane.

Phase 3 of the engine-consistency plan, and the first phase that can change
what a beat sounds like.

Today ``kit_variety.pick_with_variety`` walks the lanes one at a time and each
lane takes its own best-scoring file. On a library whose files are named alike
(measured on a kit pack: every kick, snare, clap and snare layer scores the same
31) the "best" is a coin toss, so nothing makes the snare belong to the kick.

This module instead
  1. collects a shortlist per lane from ``sample_match.ranked_pool_for_track``
     (the unchanged name/folder/audio scoring),
  2. searches combinations with a beam, and
  3. scores each combination on MEASURED sample passports (sample_passport.py)
     and the Style Card, picks among the near-best with the generation's seed.

Three modes, one switch (``PLG_KIT_COHERENCE``):

  off     the old path; nothing in this module runs.
  shadow  the proposal is computed and stamped on the pattern as
          ``plg_kit_coherence`` next to the kit the old path chose, scored by the
          same scorer. The sounds do not change: byte-identical FLP.
  on      (default) the proposal replaces kick / snare / clap / hi_hats /
          snare_layer / 808_bass. ``melody_lead`` is never touched.

Design rules:
* Compatibility, not sameness. The terms penalise clashes and outliers; they do
  not minimise variance. A dark 808 under bright hats is fine.
* Brightness is judged as a PERCENTILE inside the lane's own pool: a "low"
  centroid for a hat is not one for a kick, and what counts as dark depends on
  the user's library.
* No term reads the genre lane. Style Card and passports only.
* Deterministic for a given seed; the only randomness is a ``random.Random``
  seeded from the generation seed.
* Every failure degrades to "no proposal" — the old kit stands.
"""
from __future__ import annotations

import logging
import math
import os
import random
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger("plg.kit_coherence")

MODE_ENV = "PLG_KIT_COHERENCE"
MODES = ("off", "shadow", "on")
#: Switched on after the listening review of 2026-10-04. Roll back with
#: ``PLG_KIT_COHERENCE=off`` (no code change), or set this back to "off".
DEFAULT_MODE = "on"
TELEMETRY_VERSION = 1

#: Lanes this module may choose. ``melody_lead`` is deliberately absent.
COHERENT_LANES = ("808_bass", "kick", "snare", "clap", "snare_layer", "hi_hats")
#: Search order: the anchors first, so later lanes are judged against them.
SEARCH_ORDER = COHERENT_LANES
#: Lanes whose channel fader is fixed (flp_writer._DEFAULT_CHANNEL_VOL) and whose
#: sample level is NOT compensated per file. 808 and melody are (flp_writer
#: ``_808_gain_correction_db`` / ``_melody_gain_correction_db``), so a level term
#: on them would double-count.
LEVEL_LANES = ("kick", "snare", "clap", "hi_hats", "snare_layer")
_BRIGHT_LANES = ("snare", "clap", "hi_hats", "snare_layer")
_PACK_LANES = ("808_bass", "kick", "snare", "clap", "hi_hats")
_DIRT_LANES = ("808_bass", "kick", "snare", "clap")

PER_LANE = 10          # shortlist size per lane
BAND = 35              # a candidate must be within this of the lane's best score ...
MIN_BAND = 8           # ... unless that leaves fewer than this many: then the best MIN_BAND by score
BEAM = 120             # partial kits kept per lane step
TOP_KITS = 10          # complete kits considered for the final draw
PICK_BAND = 8.0        # draw uniformly among kits within this many points of the best
MAX_POOL_FOR_STATS = 120
GATE_CHECK_LIMIT = 60  # how many 808 candidates may be run through the song gate

#: Tunable, in "points" on the scale of sample_match scores (a band is 35).
WEIGHTS: dict[str, float] = {
    "base": 0.5,              # x (score - lane best score), always <= 0
    "recent": 200.0,          # same as kit_variety's last-kit penalty
    "unmeasured": 5.0,        # no passport: neither rewarded nor trusted
    "pack": 20.0,             # share of lane pairs from one pack folder
    "bright_target": 10.0,    # distance from the Style Card kit_target
    "bright_spread": 8.0,     # spread of the top-of-kit lanes' brightness
    "tail_hat": 10.0,         # hat tail longer than a 16th
    "tail_pair": 6.0,         # snare / clap tails of very different length
    "tail_kick": 6.0,         # kick still ringing 3/4 of a beat later
    "level": 8.0,             # outlier level inside the fixed faders
    "collide_backbeat": 25.0,  # snare and clap are one role (roles_collide)
    "collide_layer": 10.0,    # snare layer collapses into the snare
    "two_subs": 15.0,         # kick and 808 both pure sub
    "dirt": 6.0,              # one lane very clean/dirty against the others
    "root_scale": 6.0,        # 808 root outside the song's scale (small on purpose)
    "root_tonic": 2.0,        # 808 root on the tonic
}

_TARGET_PERCENTILE = {"low": 0.25, "mid": 0.5, "high": 0.75}

_GENERIC_FOLDER_WORDS = frozenset(
    {
        "kick", "kicks", "snare", "snares", "rim", "rims", "clap", "claps", "snclap", "hat", "hats", "hihat",
        "hihats", "open", "closed", "808", "808s", "bass", "bassline", "sub", "perc", "percs", "percussion",
        "drum", "drums", "drumkit", "drumkits", "kit", "kits", "sample", "samples", "sound", "sounds", "one",
        "shot", "shots", "oneshot", "oneshots", "import", "imports", "fx", "melody", "melodies", "loop",
        "loops", "organized", "deconstructed", "stuff", "misc", "other", "all", "the", "and", "pack", "packs",
    }
)


def coherence_mode() -> str:
    """The active mode: ``DEFAULT_MODE`` when ``PLG_KIT_COHERENCE`` is unset or empty,
    otherwise ``off`` / ``shadow`` / ``on`` (``1`` / ``true`` / ``yes`` = on).
    An unrecognised value means ``off`` — a typo must never switch the feature ON."""
    raw = os.environ.get(MODE_ENV, "").strip().lower()
    if not raw:
        return DEFAULT_MODE
    if raw in {"1", "true", "yes"}:
        return "on"
    return raw if raw in MODES else "off"


# --- sample identity -------------------------------------------------------


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def pack_identity(path: Path, library_root: Path | None) -> tuple[str, str, str]:
    """(outer pack folder, inner set folder, producer tag) of a sample; any may be ''.

    Folders that are only a lane name ("Kick", "Snare & Rim", "Drumkits") say
    nothing about where a sound came from and are skipped. Of the rest, the
    OUTERMOST is the pack (a downloaded kit's own folder) and the INNERMOST is
    the set inside it (a song's recorded drums, "12 BACC"): sounds from one set
    were made together, which is the strongest "belongs together" there is. The
    producer tag is the ``@name`` suffix many packs put on file names.
    ``sample_match.pack_tokens`` reads leading filename words instead, which in a
    kit organised by folder are only the instrument ("Kick", "Clap")."""
    from library_paths import relative_in_library

    try:
        parts = list(relative_in_library(path, library_root).parts[:-1]) if library_root else list(path.parts[-4:-1])
    except ValueError:
        parts = list(path.parts[-4:-1])
    named: list[str] = []
    for part in parts:
        words = _words(part)
        if words and not all(w in _GENERIC_FOLDER_WORDS or w.isdigit() for w in words):
            named.append("_".join(words))
    match = re.search(r"@([A-Za-z0-9_.-]+)", path.stem)
    return (
        named[0] if named else "",
        named[-1] if named else "",
        match.group(1).lower() if match else "",
    )


# --- candidates ------------------------------------------------------------


@dataclass
class Cand:
    lane: str
    path: Path
    base: int
    key: str = ""
    name: str = ""
    measured: bool = False
    bright: float = 0.5          # percentile inside the lane's pool, 0 dark .. 1 bright
    level_delta: float = 0.0     # rms dB against the lane's median
    crest_delta: float = 0.0
    t20: float = 0.0
    t40: float = 0.0
    sub_share: float = 0.0
    root_pc: int | None = None
    root_trusted: bool = False
    pack: str = ""          # outermost pack folder
    pack_set: str = ""      # innermost set folder
    producer: str = ""
    recent: bool = False

    def __post_init__(self) -> None:
        self.key = self.key or str(self.path.resolve())
        self.name = self.name or self.path.name


@dataclass
class LanePool:
    lane: str
    ranked: list[tuple[int, Path]]
    best: int
    bright_values: list[float] = field(default_factory=list)   # sorted
    level_median: float = 0.0
    crest_median: float = 0.0
    scores: dict[str, int] = field(default_factory=dict)


def _percentile(sorted_values: list[float], x: float) -> float:
    if not sorted_values:
        return 0.5
    below = sum(1 for v in sorted_values if v < x)
    equal = sum(1 for v in sorted_values if v == x)
    return (below + 0.5 * equal) / len(sorted_values)


def _brightness_raw(passport: dict[str, Any]) -> float | None:
    feats = passport.get("features") or {}
    char = passport.get("character") or {}
    centroid, rolloff = feats.get("centroid"), char.get("rolloff85_hz")
    if not centroid or not rolloff:
        return None
    return 0.5 * math.log(max(float(centroid), 20.0)) + 0.5 * math.log(max(float(rolloff), 20.0))


@dataclass
class Context:
    """Everything a kit is judged against. Built once per generation."""

    bpm: float
    library_root: Path | None
    pools: dict[str, LanePool]
    last_paths: set[str]
    recent_names: set[str] = field(default_factory=set)
    kit_target: str | None = None          # "low" | "mid" | "high"
    key_root_pc: int | None = None
    key_scale_pcs: frozenset[int] | None = None
    passports: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    _collide_cache: dict[tuple[str, str], bool] = field(default_factory=dict)

    # --- building candidates ------------------------------------------------

    def passport(self, path: Path) -> dict[str, Any] | None:
        key = str(path.resolve())
        if key not in self.passports:
            from sample_passport import passport_for

            got = passport_for(path, persist=False)
            self.passports[key] = got if got and got.get("status") == "ok" else None
        return self.passports[key]

    def make_cand(self, lane: str, path: Path, base: int) -> Cand:
        pool = self.pools.get(lane)
        cand = Cand(lane=lane, path=path, base=base)
        cand.pack, cand.pack_set, cand.producer = pack_identity(path, self.library_root)
        # a duplicate copy of a recent sample (same file name) is as recent as the original
        cand.recent = cand.key in self.last_paths or cand.name in self.recent_names
        passport = self.passport(path)
        if passport is None or pool is None:
            return cand
        cand.measured = True
        char = passport.get("character") or {}
        raw = _brightness_raw(passport)
        if raw is not None:
            cand.bright = _percentile(pool.bright_values, raw)
        if char.get("rms_db") is not None:
            cand.level_delta = float(char["rms_db"]) - pool.level_median
        if char.get("crest_db") is not None:
            cand.crest_delta = float(char["crest_db"]) - pool.crest_median
        window_ms = float(char.get("analyzed_s") or 2.0) * 1000.0
        cand.t20 = float(char.get("t20_ms") if char.get("t20_ms") is not None else window_ms)
        cand.t40 = float(char.get("t40_ms") if char.get("t40_ms") is not None else window_ms)
        cand.sub_share = float((char.get("bands") or {}).get("sub") or 0.0)
        root = passport.get("root")
        if isinstance(root, dict) and root.get("root_pc") is not None:
            cand.root_pc = int(root["root_pc"]) % 12
            cand.root_trusted = bool(root.get("root_trusted"))
        return cand

    # --- scoring --------------------------------------------------------------

    def _collides(self, a: Cand, b: Cand) -> bool:
        key = tuple(sorted((a.key, b.key)))
        if key not in self._collide_cache:
            try:
                import audio_features

                self._collide_cache[key] = bool(audio_features.roles_collide(a.path, b.path))
            except Exception:  # noqa: BLE001 — advisory
                self._collide_cache[key] = False
        return self._collide_cache[key]

    def score(self, kit: dict[str, Cand]) -> tuple[float, dict[str, float]]:
        """Total and per-term points of a (possibly partial) kit. Higher is better."""
        w = WEIGHTS
        terms: dict[str, float] = {}

        def put(name: str, value: float) -> None:
            if value:
                terms[name] = terms.get(name, 0.0) + value

        for lane, cand in kit.items():
            pool = self.pools.get(lane)
            if pool is not None:
                put("base", w["base"] * (cand.base - pool.best))
            if cand.recent:
                put("recent", -w["recent"])
            if not cand.measured:
                put("unmeasured", -w["unmeasured"])

        # pack cohesion: how many lane pairs come from one set / pack / producer
        pack_cands = [kit[l] for l in _PACK_LANES if l in kit]
        if len(pack_cands) >= 2:
            pairs = same = 0.0
            for i, a in enumerate(pack_cands):
                for b in pack_cands[i + 1 :]:
                    pairs += 1
                    if a.pack_set and a.pack_set == b.pack_set:
                        same += 1.0          # recorded/made together
                    elif (a.pack and a.pack == b.pack) or (a.producer and a.producer == b.producer):
                        same += 0.5          # same pack or same producer
            put("pack", w["pack"] * same / pairs)

        # brightness: near the Style Card target, and the top of the kit not scattered
        bright = [kit[l].bright for l in _BRIGHT_LANES if l in kit and kit[l].measured]
        if bright and self.kit_target in _TARGET_PERCENTILE:
            target = _TARGET_PERCENTILE[self.kit_target]
            put("bright_target", -w["bright_target"] * 2.0 * statistics.fmean(abs(b - target) for b in bright))
        if len(bright) >= 3:
            put("bright_spread", -w["bright_spread"] * 2.0 * statistics.pstdev(bright))

        # tails against the tempo
        sixteenth = 15000.0 / self.bpm
        beat = 60000.0 / self.bpm
        if "hi_hats" in kit and kit["hi_hats"].measured:
            over = max(0.0, (kit["hi_hats"].t20 - sixteenth) / sixteenth)
            put("tail_hat", -w["tail_hat"] * min(over, 2.0) / 2.0)
        if "snare" in kit and "clap" in kit and kit["snare"].measured and kit["clap"].measured:
            ratio = abs(math.log(max(kit["snare"].t20, 10.0) / max(kit["clap"].t20, 10.0)))
            put("tail_pair", -w["tail_pair"] * min(ratio, 1.5) / 1.5)
        if "kick" in kit and kit["kick"].measured:
            over = max(0.0, (kit["kick"].t40 - 0.75 * beat) / beat)
            put("tail_kick", -w["tail_kick"] * min(over, 1.0))

        # levels, only on lanes whose fader is fixed
        deltas = [abs(kit[l].level_delta) for l in LEVEL_LANES if l in kit and kit[l].measured]
        if deltas:
            put("level", -w["level"] * sum(min(max((d - 3.0) / 6.0, 0.0), 1.0) for d in deltas) / len(deltas))

        # role clashes
        if "snare" in kit and "clap" in kit and self._collides(kit["snare"], kit["clap"]):
            put("collide_backbeat", -w["collide_backbeat"])
        if "snare" in kit and "snare_layer" in kit and self._collides(kit["snare"], kit["snare_layer"]):
            put("collide_layer", -w["collide_layer"])
        if "kick" in kit and "808_bass" in kit and kit["kick"].measured and kit["808_bass"].measured:
            if kit["kick"].sub_share >= 0.75 and kit["808_bass"].sub_share >= 0.85:
                put("two_subs", -w["two_subs"])

        # clean against dirty
        crest = [kit[l].crest_delta for l in _DIRT_LANES if l in kit and kit[l].measured]
        if len(crest) >= 3:
            put("dirt", -min(w["dirt"], statistics.pstdev(crest)))

        # 808 root against the song's key (small on purpose: the writer retunes per note)
        bass = kit.get("808_bass")
        if bass is not None and bass.root_trusted and bass.root_pc is not None and self.key_scale_pcs:
            if bass.root_pc not in self.key_scale_pcs:
                put("root_scale", -w["root_scale"])
            elif bass.root_pc == self.key_root_pc:
                put("root_tonic", w["root_tonic"])

        return sum(terms.values()), terms

    def evaluate(self, picks: dict[str, Path]) -> dict[str, Any]:
        """Score a kit someone else chose (the old path's) with the same terms."""
        kit: dict[str, Cand] = {}
        for lane, path in picks.items():
            if lane not in COHERENT_LANES:
                continue
            pool = self.pools.get(lane)
            base = pool.scores.get(str(Path(path).resolve()), pool.best - 10) if pool else 0
            kit[lane] = self.make_cand(lane, Path(path), base)
        total, terms = self.score(kit)
        return {"total": round(total, 2), "terms": {k: round(v, 2) for k, v in sorted(terms.items())}}


# --- the plan ----------------------------------------------------------------


@dataclass
class KitPlan:
    picks: dict[str, Path]
    total: float
    terms: dict[str, float]
    alternatives: list[dict[str, Any]]
    candidates: dict[str, int]
    seconds: float
    context: Context
    seed: int
    applied: bool = False

    def telemetry(self, *, mode: str, legacy: dict[str, Path] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": TELEMETRY_VERSION,
            "mode": mode,
            "applied": self.applied,
            "seed": self.seed,
            "seconds": round(self.seconds, 3),
            "candidates": dict(self.candidates),
            "proposal": {
                "picks": {lane: str(path) for lane, path in self.picks.items()},
                "names": {lane: path.name for lane, path in self.picks.items()},
                "total": round(self.total, 2),
                "terms": {k: round(v, 2) for k, v in sorted(self.terms.items())},
            },
            "alternatives": self.alternatives,
        }
        if legacy is not None:
            out["legacy"] = {
                "picks": {lane: str(path) for lane, path in legacy.items() if lane in COHERENT_LANES},
                "names": {lane: Path(path).name for lane, path in legacy.items() if lane in COHERENT_LANES},
                **self.context.evaluate(legacy),
            }
        return out


def _scale_pcs(card: dict[str, Any]) -> tuple[int | None, frozenset[int] | None]:
    key = card.get("key") if isinstance(card, dict) else None
    if not isinstance(key, dict) or key.get("root_pc") is None or not key.get("scale"):
        return None, None
    from music_theory import SCALES

    intervals = SCALES.get(str(key["scale"]))
    if intervals is None:
        return None, None
    root = int(key["root_pc"]) % 12
    return root, frozenset((root + i) % 12 for i in intervals)


def _build_pool(lane: str, ranked: list[tuple[int, Path]], ctx_passport: Callable[[Path], Any]) -> LanePool:
    pool = LanePool(lane=lane, ranked=ranked, best=ranked[0][0] if ranked else 0)
    pool.scores = {str(p.resolve()): s for s, p in ranked}
    brights: list[float] = []
    levels: list[float] = []
    crests: list[float] = []
    for _score, path in ranked[:MAX_POOL_FOR_STATS]:
        passport = ctx_passport(path)
        if not passport:
            continue
        raw = _brightness_raw(passport)
        if raw is not None:
            brights.append(raw)
        char = passport.get("character") or {}
        if char.get("rms_db") is not None:
            levels.append(float(char["rms_db"]))
        if char.get("crest_db") is not None:
            crests.append(float(char["crest_db"]))
    pool.bright_values = sorted(brights)
    pool.level_median = statistics.median(levels) if levels else 0.0
    pool.crest_median = statistics.median(crests) if crests else 0.0
    return pool


def _stratified(band: list[tuple[int, Path]], ctx: Context, size: int) -> list[tuple[int, Path]]:
    """Shortlist ``size`` candidates that SPAN the band's brightness range.

    On a library whose files score alike, ordering the band by name score and
    cutting it picks whichever files the seeded tie-break happened to put first
    — the search would then only ever see a random corner of the lane. Spreading
    the shortlist over the measured brightness keeps dark, middle and bright
    options in play, so a compatible partner exists when the beam needs one. The
    best-scoring candidate is always kept; unmeasurable files come last."""
    if len(band) <= size:
        return band
    measured: list[tuple[float, int, int, Path]] = []
    rest: list[tuple[int, Path]] = []
    for order, (score, path) in enumerate(band):
        passport = ctx.passport(path)
        raw = _brightness_raw(passport) if passport else None
        if raw is None:
            rest.append((score, path))
        else:
            measured.append((raw, order, score, path))
    measured.sort(key=lambda item: (item[0], item[1]))
    count = len(measured)
    if count <= size:
        spread = list(range(count))
    else:
        spread = sorted({round(i * (count - 1) / max(1, size - 1)) for i in range(size)})
        for extra in range(count):          # rounding can merge neighbours; top up to ``size``
            if len(spread) >= size:
                break
            if extra not in spread:
                spread.append(extra)
        spread.sort()
    picked: dict[int, tuple[int, Path]] = {measured[i][1]: (measured[i][2], measured[i][3]) for i in spread}
    if 0 not in picked:
        # keep the top-scoring candidate: make room by dropping a middle one, never an extreme
        by_brightness = [measured[i][1] for i in spread]
        if len(by_brightness) >= 3:
            del picked[by_brightness[len(by_brightness) // 2]]
        picked[0] = band[0]
    for order, item in enumerate(band):
        if len(picked) >= size:
            break
        if item in rest:
            picked.setdefault(order, item)
    return [picked[k] for k in sorted(picked)][:size]


def plan_kit(
    catalog: dict[str, Any],
    library_root: Path,
    *,
    pattern: dict[str, Any],
    melody_energy: dict[str, Any] | None,
    seed: int | None,
    last_paths: Iterable[str] = (),
    blocked_names: Iterable[str] = (),
    reserved_paths: Iterable[str] = (),
    gate_808: Callable[[Path], bool] | None = None,
    lanes: Iterable[str] = COHERENT_LANES,
) -> KitPlan | None:
    """Search whole kits and return the chosen one, or None when there is nothing to improve.

    ``gate_808`` is the song-dependent acceptance test for an 808 (rejection
    reason, tail against tempo, pitch classes it goes dead on); it runs on the
    shortlist, never on the whole pool, because the pitch passport costs ~0.3 s.
    """
    started = time.perf_counter()
    from sample_match import ranked_pool_for_track
    from sample_passport import fill_root, flush_store

    rng_seed = (seed if seed is not None else time.time_ns()) ^ 0xC0FFEE
    rng = random.Random(rng_seed)
    blocked = {str(n) for n in blocked_names}
    reserved = {str(p) for p in reserved_paths}
    card = pattern.get("plg_style_card") if isinstance(pattern.get("plg_style_card"), dict) else {}
    key_root, scale_pcs = _scale_pcs(card)
    kit_target = (card.get("kit_target") or {}).get("centroid") if isinstance(card.get("kit_target"), dict) else None
    try:
        bpm = max(60.0, min(220.0, float(pattern.get("bpm") or 140.0)))
    except (TypeError, ValueError):
        bpm = 140.0

    ctx = Context(
        bpm=bpm,
        library_root=Path(library_root),
        pools={},
        last_paths={str(p) for p in last_paths},
        recent_names={Path(str(p)).name for p in last_paths},
        kit_target=kit_target if kit_target in _TARGET_PERCENTILE else None,
        key_root_pc=key_root,
        key_scale_pcs=scale_pcs,
    )

    shortlist: dict[str, list[Cand]] = {}
    for lane in lanes:
        ranked = [
            (s, p)
            for s, p in ranked_pool_for_track(
                catalog, lane, library_root, melody_energy=melody_energy, exclude=reserved
            )
            if p.name not in blocked
        ]
        if not ranked:
            continue
        ctx.pools[lane] = _build_pool(lane, ranked, ctx.passport)
        tie_break = {str(p.resolve()): rng.random() for _s, p in ranked}
        band = [(s, p) for s, p in ranked if s >= ranked[0][0] - BAND]
        if len(band) < MIN_BAND:
            # A strong keyword bias (a racing melody boosts "tight"/"snap") can lift one file
            # more than BAND above the rest. Two lanes can then share the same single
            # candidate and the search has nothing to trade; the ``base`` term still prices
            # the score deficit of the extra candidates.
            band = list(ranked[:MIN_BAND])
        band.sort(key=lambda sp: (-sp[0], tie_break[str(sp[1].resolve())]))
        if lane != "808_bass":
            band = _stratified(band, ctx, PER_LANE)
        chosen: list[tuple[int, Path]] = []
        checked = 0
        for score, path in band:
            if lane == "808_bass" and gate_808 is not None:
                if checked >= GATE_CHECK_LIMIT:
                    break
                checked += 1
                try:
                    if not gate_808(path):
                        continue
                except Exception:  # noqa: BLE001 — a failing gate must not admit a candidate
                    continue
            chosen.append((score, path))
            if len(chosen) >= PER_LANE:
                break
        if lane == "808_bass":
            for _score, path in chosen:
                fill_root(path)            # persisted; ~0.3 s the first time per sample
                ctx.passports.pop(str(path.resolve()), None)
        shortlist[lane] = [ctx.make_cand(lane, path, score) for score, path in chosen]

    flush_store()
    if sum(1 for cands in shortlist.values() if cands) < 3:
        return None

    beam: list[tuple[float, dict[str, Cand]]] = [(0.0, {})]
    for lane in SEARCH_ORDER:
        cands = shortlist.get(lane)
        if not cands:
            continue
        grown: list[tuple[float, dict[str, Cand]]] = []
        for _total, kit in beam:
            used = {c.key for c in kit.values()}
            for cand in cands:
                if cand.key in used:
                    continue
                trial = {**kit, lane: cand}
                grown.append((ctx.score(trial)[0], trial))
        if not grown:
            continue           # every candidate of this lane is already used: leave the lane out
        grown.sort(key=lambda item: (-item[0], tuple(c.key for c in item[1].values())))
        beam = grown[:BEAM]

    finals = beam[:TOP_KITS]
    if any(shortlist.get(lane) and lane not in finals[0][1] for lane in SEARCH_ORDER):
        return None            # a lane with candidates could not be filled: keep the old kit whole
    best_total = finals[0][0]
    near = [item for item in finals if item[0] >= best_total - PICK_BAND]
    total, kit = near[rng.randrange(len(near))]
    _t, terms = ctx.score(kit)

    alternatives = [
        {"total": round(t, 2), "names": {lane: c.name for lane, c in k.items()}}
        for t, k in finals[:3]
    ]
    return KitPlan(
        picks={lane: cand.path for lane, cand in kit.items()},
        total=total,
        terms=terms,
        alternatives=alternatives,
        candidates={lane: len(cands) for lane, cands in shortlist.items()},
        seconds=time.perf_counter() - started,
        context=ctx,
        seed=int(rng_seed),
    )


__all__ = [
    "COHERENT_LANES",
    "KitPlan",
    "coherence_mode",
    "pack_identity",
    "plan_kit",
]
