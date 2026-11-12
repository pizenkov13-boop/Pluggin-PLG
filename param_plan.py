"""Param Plan: the data contract between a future LLM "decider" and beat_humanize.

Nothing in this module calls an LLM, touches the RNG, reads the environment or
is called from the generation route. It only defines

* the INPUT the decider would see (``build_plan_input``): a compact numeric
  description of the situation, never raw notes, audio, paths or artist names;
* the OUTPUT it may answer with (a *Param Plan*) and a validator that turns any
  answer - garbage included - into a safe plan (``validate_plan``);
* the mapping from a validated plan to the keyword arguments the humanize
  functions accept (``humanizer_kwargs``).

Seven knobs are offered to the model: hat swing, hat velocity, roll probability,
roll depth, melody lag, kick density multiplier and double-kick share. Snare and clap velocity are implemented end to end but
switched off (``OFFER_DRUM_VELOCITY``). The rest of the schema
(``RESERVED``) is accepted on the wire so the contract does not break later, but
is dropped by the validator and never reaches ``humanizer_kwargs``.

``kick_syncopation`` is reserved because it is NOT connected: every profile of
the live lanes has bass808_enabled, which gates the only reader of
GenreProfile.kick_syncopation off (the kicks come from midi_phrase_composer).
A plan value for it could not change a note. See the project issue log (not included).

Every missing, malformed or out-of-contract field falls back to the BASELINE,
the value the genre profile gives today. A plan made only of baseline values
therefore maps to exactly the defaults of the functions it feeds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1
NOTES_MAX_CHARS = 200

#: documented in the order they are wired; only these reach humanizer_kwargs
#: Offered to the model and accepted from it. snare_velocity / clap_velocity are
#: implemented end to end (validator, kwargs, humanize) but switched off here: on the
#: three test tracks the model never moved them, and their prompt text cost tokens.
#: Set to True (and bump param_plan_client.PROMPT_VERSION) to offer them again.
OFFER_DRUM_VELOCITY = False
DRUM_VELOCITY_KNOBS = ("snare_velocity", "clap_velocity")
ACTIVE_KNOBS = (
    "hat_swing", "hat_velocity", "roll", "melody_lag_ms", "kick_density_multiplier", "double_kick_share",
)

#: (low, high) bounds per scalar leaf; the validator clamps into them
LIMITS: dict[str, tuple[float, float]] = {
    "hat_swing": (0.0, 1.28),  # 1.28 = what trap/drill profiles ask for; see APPLIED_SWING_CAP
    "hat_velocity.peak": (60.0, 110.0),
    "hat_velocity.vel_min": (30.0, 70.0),
    "hat_velocity.vel_max": (90.0, 127.0),
    "roll.prob.hot": (0.0, 0.60),
    "roll.prob.default": (0.0, 0.50),
    "roll.prob.quiet": (0.0, 0.30),
    "roll.depth.hot": (0.20, 0.50),
    "roll.depth.default": (0.15, 0.50),
    "snare_velocity.lo": (80.0, 115.0),
    "snare_velocity.hi": (105.0, 127.0),
    "clap_velocity.lo": (75.0, 110.0),
    "clap_velocity.hi": (100.0, 127.0),
    "melody_lag_ms": (0.0, 25.0),
    # Same bounds drum_density.GENRE_MULTIPLIER_MIN/MAX clamp to (a test keeps them equal).
    "kick_density_multiplier": (0.75, 1.25),
    # Share of low-end onsets that may become a deliberate double kick (baseline 0.10; the bar-share
    # bound of 1/3 still applies on top).
    "double_kick_share": (0.0, 0.30),
}
#: a snare/clap velocity range narrower than this is a flat hit, not a performance
MIN_DRUM_VELOCITY_SPAN = 8
#: What beat_humanize applies today (HAT_SWING_CAP; a test keeps the two equal).
#: The baseline is this, not the raw profile value, so a baseline-only plan is a
#: no-op. A plan may ask for more (up to LIMITS) but nothing applies more than
#: this until the cap is made plan-dependent AND drum_density's swing reach is
#: re-derived for it.
APPLIED_SWING_CAP = 1.15
#: vel_max must stay at least this far above vel_min, else the velocity wave collapses
MIN_VELOCITY_SPAN = 20

#: section names the decider may use -> the three buckets hihat_rolls knows
SECTION_BUCKET = {
    "hot": "hot", "hook": "hot", "chorus": "hot", "drop": "hot",
    "default": "default", "bridge": "default", "build": "default",
    "quiet": "quiet", "verse": "quiet", "intro": "quiet", "outro": "quiet",
}

#: accepted but inert in v1 (future knobs). Listed so they are dropped
#: deliberately and reported, not mistaken for typos.
RESERVED = frozenset({"swing_range_ticks", "kick_syncopation"})
_TOP_LEVEL = {"schema", "notes", *ACTIVE_KNOBS, *DRUM_VELOCITY_KNOBS, "roll", *RESERVED}


@dataclass(frozen=True)
class ValidatedPlan:
    """The only thing downstream code may read."""

    values: dict[str, Any]  # always complete: every active knob present
    ok: bool  # False when the whole answer was rejected and the baseline used
    notes: str = ""
    clamped: tuple[str, ...] = ()
    fell_back: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    reserved: tuple[str, ...] = ()

    def telemetry(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION, "ok": self.ok, "values": self.values, "notes": self.notes,
            "clamped": list(self.clamped), "fell_back": list(self.fell_back),
            "dropped": list(self.dropped), "reserved": list(self.reserved),
        }


# --- baseline -------------------------------------------------------------------


def baseline_plan(profile: Any) -> dict[str, Any]:
    """The plan that reproduces today's behaviour for ``profile``.

    Scalars come from the profile, the rest from the constants the humanize
    functions default to (read from them, never copied, so they cannot drift).
    """
    import beat_humanize
    import drum_density
    import hihat_rolls
    import midi_phrase_drums

    return {
        "hat_swing": _clamp(
            min(float(getattr(profile, "hat_swing", 1.0)), APPLIED_SWING_CAP), *LIMITS["hat_swing"]
        )[0],
        "hat_velocity": {
            "peak": 92,
            "vel_min": hihat_rolls.HAT_VEL_MIN,
            "vel_max": hihat_rolls.HAT_VEL_MAX,
        },
        "roll": {
            "prob": {
                "hot": hihat_rolls.ROLL_PROB_HOT,
                "default": hihat_rolls.ROLL_PROB,
                "quiet": hihat_rolls.ROLL_PROB_QUIET,
            },
            "depth": {"hot": hihat_rolls.ROLL_DEPTH_HOT, "default": hihat_rolls.ROLL_DEPTH},
        },
        "snare_velocity": {"lo": beat_humanize.SNARE_VEL_RANGE[0], "hi": beat_humanize.SNARE_VEL_RANGE[1]},
        "clap_velocity": {"lo": beat_humanize.CLAP_VEL_RANGE[0], "hi": beat_humanize.CLAP_VEL_RANGE[1]},
        "melody_lag_ms": _clamp(float(getattr(profile, "melody_lag_ms", 0.0)), *LIMITS["melody_lag_ms"])[0],
        # Not rounded: a baseline-only plan must hand drum_density exactly the profile's own multiplier.
        "kick_density_multiplier": drum_density._profile_multiplier(profile),
        "double_kick_share": midi_phrase_drums._DOUBLE_MAX_ONSET_SHARE,
    }


# --- validator ---------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> tuple[float, bool]:
    clipped = min(high, max(low, value))
    return clipped, clipped != value


def _leaf(raw: Any, path: str, base: float, clamped: list[str], fell_back: list[str], *, integer: bool = False) -> float:
    if raw is None:
        return base
    number = _number(raw)
    if number is None:
        fell_back.append(path)
        return base
    low, high = LIMITS[path]
    value, was_clamped = _clamp(number, low, high)
    if was_clamped:
        clamped.append(path)
    return int(round(value)) if integer else round(value, 4)


def _as_dict(raw: Any, path: str, fell_back: list[str]) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    fell_back.append(path)
    return {}


def validate_plan(raw: Any, baseline: dict[str, Any]) -> ValidatedPlan:
    """Turn any answer into a complete, in-range plan. Never raises.

    * not a dict / wrong schema version -> the whole baseline, ``ok=False``;
    * unknown keys dropped, reserved keys dropped and listed;
    * non-numeric (incl. bool, NaN, inf) values fall back to the baseline;
    * out-of-range values are clamped;
    * a missing field is simply the baseline.
    """
    base = _deepcopy(baseline)
    if not isinstance(raw, dict):
        return ValidatedPlan(values=base, ok=False, fell_back=("<plan>",))
    if raw.get("schema") != SCHEMA_VERSION:
        return ValidatedPlan(values=base, ok=False, fell_back=("schema",))

    clamped: list[str] = []
    fell_back: list[str] = []
    dropped: list[str] = []
    reserved: list[str] = []
    for key in raw:
        if key in RESERVED:
            reserved.append(str(key))
        elif key not in _TOP_LEVEL:
            dropped.append(str(key))

    out = _deepcopy(base)
    out["hat_swing"] = _leaf(raw.get("hat_swing"), "hat_swing", base["hat_swing"], clamped, fell_back)
    out["melody_lag_ms"] = _leaf(raw.get("melody_lag_ms"), "melody_lag_ms", base["melody_lag_ms"], clamped, fell_back)
    for knob in ("kick_density_multiplier", "double_kick_share"):
        out[knob] = _leaf(raw.get(knob), knob, base[knob], clamped, fell_back)

    velocity = _as_dict(raw.get("hat_velocity"), "hat_velocity", fell_back)
    vel = out["hat_velocity"]
    for name in ("peak", "vel_min", "vel_max"):
        vel[name] = _leaf(velocity.get(name), f"hat_velocity.{name}", base["hat_velocity"][name], clamped, fell_back, integer=True)
    for extra in set(velocity) - {"peak", "vel_min", "vel_max"}:
        dropped.append(f"hat_velocity.{extra}")
    if vel["vel_max"] - vel["vel_min"] < MIN_VELOCITY_SPAN:
        # a collapsed wave is a defect, not a style: restore the baseline pair
        fell_back.append("hat_velocity.span")
        vel["vel_min"] = base["hat_velocity"]["vel_min"]
        vel["vel_max"] = base["hat_velocity"]["vel_max"]

    for knob in DRUM_VELOCITY_KNOBS:
        if not OFFER_DRUM_VELOCITY:
            if knob in raw:
                reserved.append(knob)  # not offered: whatever the model sent is ignored
            continue
        given = _as_dict(raw.get(knob), knob, fell_back)
        pair = out[knob]
        for name in ("lo", "hi"):
            pair[name] = _leaf(given.get(name), f"{knob}.{name}", base[knob][name], clamped, fell_back, integer=True)
        for extra in set(given) - {"lo", "hi"}:
            dropped.append(f"{knob}.{extra}")
        if pair["hi"] - pair["lo"] < MIN_DRUM_VELOCITY_SPAN:
            fell_back.append(f"{knob}.span")
            pair["lo"], pair["hi"] = base[knob]["lo"], base[knob]["hi"]

    roll = _as_dict(raw.get("roll"), "roll", fell_back)
    for extra in set(roll) - {"prob", "depth"}:
        dropped.append(f"roll.{extra}")
    depth = _as_dict(roll.get("depth"), "roll.depth", fell_back)
    for name in ("hot", "default"):
        out["roll"]["depth"][name] = _leaf(depth.get(name), f"roll.depth.{name}", base["roll"]["depth"][name], clamped, fell_back)
    for extra in set(depth) - {"hot", "default"}:
        dropped.append(f"roll.depth.{extra}")
    prob = _as_dict(roll.get("prob"), "roll.prob", fell_back)
    for section, value in prob.items():
        bucket = SECTION_BUCKET.get(str(section).strip().lower())
        if bucket is None:
            dropped.append(f"roll.prob.{section}")
            continue
        out["roll"]["prob"][bucket] = _leaf(
            value, f"roll.prob.{bucket}", out["roll"]["prob"][bucket], clamped, fell_back
        )

    notes = raw.get("notes")
    notes = notes.strip()[:NOTES_MAX_CHARS] if isinstance(notes, str) else ""
    return ValidatedPlan(
        values=out, ok=True, notes=notes,
        clamped=tuple(clamped), fell_back=tuple(fell_back),
        dropped=tuple(dropped), reserved=tuple(sorted(set(reserved))),
    )


def _deepcopy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _deepcopy(item) for key, item in value.items()}
    return value


# --- plan -> function arguments (pure mapping; nothing calls this yet) ------------


def humanizer_kwargs(plan: ValidatedPlan) -> dict[str, dict[str, Any]]:
    """Keyword arguments per humanize function, from a validated plan.

    A baseline plan yields exactly the defaults of those functions.
    """
    v = plan.values
    prob = v["roll"]["prob"]
    vel = v["hat_velocity"]
    return {
        "apply_hi_hat_swing": {"intensity": v["hat_swing"]},
        "apply_trap_hi_hats": {
            "roll_prob": prob["default"],
            "roll_prob_hot": prob["hot"],
            "roll_prob_quiet": prob["quiet"],
            "roll_depth": v["roll"]["depth"]["default"],
            "roll_depth_hot": v["roll"]["depth"]["hot"],
            "peak": vel["peak"],
            "vel_min": vel["vel_min"],
            "vel_max": vel["vel_max"],
        },
        "apply_markov_hat_dynamics": {
            "peak": vel["peak"], "vel_min": vel["vel_min"], "vel_max": vel["vel_max"],
        },
        "melody_lag_ms": v["melody_lag_ms"],
        "build_drum_density_card": {"genre_multiplier": v["kick_density_multiplier"]},
        "build_phrase_kick_808_pocket": {"double_max_onset_share": v["double_kick_share"]},
        "snare_velocity": (v["snare_velocity"]["lo"], v["snare_velocity"]["hi"]),
        "clap_velocity": (v["clap_velocity"]["lo"], v["clap_velocity"]["hi"]),
    }


# --- decider input ---------------------------------------------------------------------


_PC_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _pitch_class_name(value: Any) -> str | None:
    number = _number(value)
    return _PC_NAMES[int(number) % 12] if number is not None else None


def _round(value: Any, digits: int = 2) -> Any:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _sections(pattern: dict[str, Any]) -> list[dict[str, Any]]:
    energy = pattern.get("plg_melody_energy") if isinstance(pattern.get("plg_melody_energy"), dict) else {}
    densities = [d for d in energy.get("section_density") or [] if isinstance(d, dict)]
    if not densities:
        # The energy card is stamped before arrange_song, so it has no per-section
        # density yet; the same pure helper gives it once the arrangement exists.
        try:
            from melody_beat_symbiosis import _section_density_ratios

            densities = [d for d in _section_density_ratios(pattern) if isinstance(d, dict)]
        except Exception:  # noqa: BLE001 - a missing density only makes the input less detailed
            densities = []
    rows: list[dict[str, Any]] = []
    for section in pattern.get("plg_arrangement") or []:
        if not isinstance(section, dict):
            continue
        start = (_number(section.get("start_bar")) or 0.0) * 4.0
        end = start + (_number(section.get("bars")) or 0.0) * 4.0
        covered = [
            _number(d.get("ratio")) for d in densities
            if (_number(d.get("start")) or 0.0) < end and (_number(d.get("end")) or 0.0) > start
        ]
        covered = [c for c in covered if c is not None]
        rows.append({
            "name": str(section.get("name", "")),
            "bars": int(_number(section.get("bars")) or 0),
            "energy": _round(section.get("energy")),
            "melody_density": _round(sum(covered) / len(covered)) if covered else None,
            "rolls_allowed": bool(section.get("rolls")),
            "hat_feel": str(section.get("hat_feel", "")),
        })
    return rows


def build_plan_input(
    pattern: dict[str, Any],
    profile: Any,
    *,
    kit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact, deterministic description of one beat for the decider.

    Reads only values the pipeline already computed after MIDI ingest. No raw
    notes, no file paths, no names. ``kit`` is an optional summary supplied by
    the caller (the kit is chosen after humanize, so it is usually unknown at
    this point); it is passed through only for its known numeric/short fields.
    """
    passport = pattern.get("plg_midi_passport") if isinstance(pattern.get("plg_midi_passport"), dict) else {}
    tempo = passport.get("tempo") if isinstance(passport.get("tempo"), dict) else {}
    key = passport.get("key") if isinstance(passport.get("key"), dict) else {}
    progression = pattern.get("plg_progression") if isinstance(pattern.get("plg_progression"), dict) else {}
    energy = pattern.get("plg_melody_energy") if isinstance(pattern.get("plg_melody_energy"), dict) else {}
    structure = pattern.get("plg_midi_structure") if isinstance(pattern.get("plg_midi_structure"), dict) else {}
    span = structure.get("span") if isinstance(structure.get("span"), dict) else {}
    baseline = baseline_plan(profile)
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}

    document: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "genre": str(variety.get("genre") or getattr(profile, "name", "")),
        "tempo": {"bpm": _round(pattern.get("bpm"), 1), "reliable": tempo.get("source") not in (None, "default")},
        "key": {
            "name": progression.get("key") or _pitch_class_name(key.get("root_pc")),
            "scale": progression.get("scale") or key.get("scale"),
        },
        "melody": {
            name: (_round(energy.get(name)) if isinstance(energy.get(name), (int, float)) else energy.get(name))
            for name in ("busyness", "lushness", "pace", "onset_rate", "syncopation", "attack", "role", "vibe")
            if energy.get(name) is not None
        },
        "structure": {
            "bars": int(_number(span.get("bars")) or 0),
            "phrases": len(structure.get("phrases") or []),
            "pauses": len(structure.get("pauses") or []),
            "sections": _sections(pattern),
        },
        "baseline": {k: v for k, v in baseline.items() if OFFER_DRUM_VELOCITY or k not in DRUM_VELOCITY_KNOBS},
        "limits": {
            name: list(bounds) for name, bounds in LIMITS.items()
            if OFFER_DRUM_VELOCITY or name.split(".")[0] not in DRUM_VELOCITY_KNOBS
        },
        "min_velocity_span": MIN_VELOCITY_SPAN,
        **({"min_drum_velocity_span": MIN_DRUM_VELOCITY_SPAN} if OFFER_DRUM_VELOCITY else {}),
        "section_buckets": dict(SECTION_BUCKET),
        "active_knobs": list(ACTIVE_KNOBS) + (list(DRUM_VELOCITY_KNOBS) if OFFER_DRUM_VELOCITY else []),
    }
    if kit:
        document["kit"] = {
            name: kit[name] for name in ("hat_tail", "hat_brightness", "kick_weight") if name in kit
        }
    return document


def response_template() -> dict[str, Any]:
    """The answer shape a decider is asked for (all fields optional)."""
    return {
        "schema": SCHEMA_VERSION,
        "hat_swing": "float",
        "hat_velocity": {"peak": "int", "vel_min": "int", "vel_max": "int"},
        "roll": {
            "prob": {"hot": "float", "default": "float", "quiet": "float"},
            "depth": {"hot": "float", "default": "float"},
        },
        **({"snare_velocity": {"lo": "int", "hi": "int"}, "clap_velocity": {"lo": "int", "hi": "int"}}
           if OFFER_DRUM_VELOCITY else {}),
        "melody_lag_ms": "float",
        "kick_density_multiplier": "float",
        "double_kick_share": "float",
        "notes": f"short text, <= {NOTES_MAX_CHARS} chars",
    }


__all__ = [
    "ACTIVE_KNOBS", "LIMITS", "RESERVED", "SCHEMA_VERSION", "ValidatedPlan",
    "baseline_plan", "build_plan_input", "humanizer_kwargs", "response_template", "validate_plan",
]
