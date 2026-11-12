"""FL Studio mixing blueprint — READ_ME_IMBA.txt for every generated beat."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mix_blueprint_i18n import blueprint_text
from pattern_utils import CHANNEL_NAMES, SAMPLE_TRACK_KEYS
from prompt_locale import VALID_LOCALES

PROJECT_DIR = Path(__file__).resolve().parent
BLUEPRINT_FILE = PROJECT_DIR / "READ_ME_IMBA.txt"


def _master_clip_baked(data: dict[str, Any]) -> bool:
    """Mirrors flp_writer._master_soft_clip_enabled: template export always
    ships a fixed Master Soft Clipper regardless of genre — the guide should
    say so instead of asking the user to add one."""
    return data.get("plg_use_template", True) is not False


def _saturation_baked() -> bool:
    """Mirrors starter_kit.py's PLG_SATURATE gate (default ON) — 808/kick/
    snare/melody one-shots already get tanh warmth baked in (sample_saturate.py)."""
    import os

    return os.environ.get("PLG_SATURATE", "1").strip().lower() not in ("0", "off", "false", "no")


def _kick_808_duck_baked(data: dict[str, Any]) -> bool:
    """True only when duck_808_under_kick actually retriggered a note this run
    (beat_humanize.py records the outcome — see plg_producer_meta.kick_808_duck_applied)."""
    meta = data.get("plg_producer_meta")
    return bool(isinstance(meta, dict) and meta.get("kick_808_duck_applied"))


def _drum_eq_baked() -> bool:
    """Mirrors mix_gain_staging.drum_eq_enabled() — kick/hats low-shelf cuts
    baked via the mixer's built-in EQ (same mechanism as melody_low_cut_params)."""
    import os

    return os.environ.get("PLG_DRUM_EQ", "auto").strip().lower() not in ("0", "off", "false", "no")


def _style_bucket(data: dict[str, Any]) -> str:
    tags = data.get("plg_style_tags")
    if isinstance(tags, list):
        joined = " ".join(str(t) for t in tags).lower()
        if "rage" in joined or "dark trap" in joined:
            return "rage"
        if "phonk" in joined:
            return "phonk"
        if "atmospheric" in joined:
            return "atmospheric"
    text = f"{data.get('style', '')} {data.get('user_prompt', '')}".lower()
    if any(k in text for k in ("rage", "dark trap", "underground", "plugg", "jerk")):
        return "rage"
    if any(k in text for k in ("phonk", "drift", "memphis", "cowbell")):
        return "phonk"
    if any(k in text for k in ("atmospheric", "ambient trap", "cinematic")):
        return "atmospheric"
    if any(k in text for k in ("country", "кантри")):
        return "country"
    return "trap"


def _bpm_line(bpm: float, bucket: str, locale: str) -> str:
    if bucket == "rage":
        return blueprint_text(locale, "bpm_opium", bpm=f"{bpm:.0f}")
    if bucket == "phonk":
        return blueprint_text(locale, "bpm_phonk", bpm=f"{bpm:.0f}")
    return blueprint_text(locale, "bpm_trap", bpm=f"{bpm:.0f}")


def _channel_block(data: dict[str, Any], bucket: str, locale: str) -> list[str]:
    picks = data.get("plg_sample_picks") if isinstance(data.get("plg_sample_picks"), dict) else {}
    lines: list[str] = []

    kick_name = picks.get("kick", "kick")
    kick_eq_line = blueprint_text(locale, "kick_line_done" if _drum_eq_baked() else "kick_line")
    lines.extend([
        f"KICK ({kick_name})",
        f"  • {kick_eq_line}",
        f"  • {blueprint_text(locale, 'kick_bus')}",
        "",
    ])

    snare_name = picks.get("snare", "snare")
    lines.extend([
        f"SNARE ({snare_name})",
        f"  • {blueprint_text(locale, 'snare_line')}",
        f"  • {blueprint_text(locale, 'snare_layer')}",
        "",
    ])

    hat_name = picks.get("hi_hats", "hats")
    rolls = data.get("plg_hat_rolls", 0)
    hats_eq_line = blueprint_text(locale, "hats_eq_done" if _drum_eq_baked() else "hats_eq")
    lines.extend([
        f"HI-HATS ({hat_name})",
        f"  • {blueprint_text(locale, 'hats_line', rolls=rolls or 'auto')}",
        f"  • {blueprint_text(locale, 'hats_pan_done')}",
        f"  • {hats_eq_line}",
        "",
    ])

    bass_name = picks.get("sub_808", "808")
    sidechain_line = (
        blueprint_text(locale, "already_baked_kick_808_duck")
        if _kick_808_duck_baked(data)
        else blueprint_text(locale, "manual_sidechain_fallback")
    )
    bass_fx = [f"808 / SUB ({bass_name})"]
    warmth_line = (
        blueprint_text(locale, "already_baked_808_warmth")
        if _saturation_baked()
        else blueprint_text(locale, "bass_trap")  # saturation kill-switched: fall back to the manual recipe
    )
    bass_fx.append(f"  • {warmth_line}")
    if bucket == "rage":
        bass_fx.extend([
            f"  • {blueprint_text(locale, 'already_baked_808_mono_legato')}",
            f"  • {blueprint_text(locale, 'manual_808_extra_opium')}",
        ])
    elif bucket == "phonk":
        bass_fx.append(f"  • {blueprint_text(locale, 'bass_phonk_cowbell_only')}")
    else:
        bass_fx.append(f"  • {blueprint_text(locale, 'bass_trap_lowcut_glide_only')}")
    bass_fx.append(f"  • {sidechain_line}")
    bass_fx.append(f"  • {blueprint_text(locale, 'fx_recipe_kick')}")
    lines.extend(bass_fx)
    lines.append("")

    melody_name = picks.get("melody_lead", "lead")
    lines.extend([
        f"MELODY ({melody_name})",
        f"  • {blueprint_text(locale, 'melody_line')}",
        "",
    ])

    if bucket == "rage":
        lines.extend([
            "SPACE MIX (stadium psychedelic depth)",
            "  • PLG MIDI insert: Fruity Reeverb 2 — Wet 35%, Decay 2.5 s",
            "  • PLG MIDI insert: Fruity Delay 3 — dotted 1/8 psychedelic throws",
            "  • PLG MIDI EQ: built-in insert shelf cut @ 5 kHz (dark / underwater)",
            "  • PLG Counter Melody shares the Melody bus — same space chain",
            "",
        ])

    if _master_clip_baked(data):
        lines.extend(["MASTER", f"  • {blueprint_text(locale, 'already_baked_master_clip')}"])
        if bucket in ("rage", "phonk"):
            lines.append(f"  • {blueprint_text(locale, 'manual_master_extra_opium')}")
        lines.append("")
    elif bucket in ("rage", "phonk"):
        lines.extend(["MASTER", f"  • {blueprint_text(locale, 'master_opium')}", ""])
    else:
        lines.extend(["MASTER", f"  • {blueprint_text(locale, 'master_trap')}", ""])

    return lines


def _blueprint_locale(data: dict[str, Any], locale: str | None = None) -> str:
    loc = (locale or data.get("plg_locale") or "en").lower()
    return loc if loc in VALID_LOCALES else "en"


def _localized_sample_steps(data: dict[str, Any], loc: str) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    paths = data.get("plg_sound_paths") if isinstance(data.get("plg_sound_paths"), dict) else {}
    picks = data.get("plg_sample_picks") if isinstance(data.get("plg_sample_picks"), dict) else {}
    starter = bool(data.get("starter_mode"))
    for track in SAMPLE_TRACK_KEYS:
        path = paths.get(track) or picks.get(track)
        if not path:
            continue
        file = Path(str(path)).name
        channel = CHANNEL_NAMES.get(track, track)
        if starter:
            text = blueprint_text(loc, "step_starter_sound", file=file, channel=channel)
        else:
            text = blueprint_text(loc, "step_drag_sample", file=file, channel=channel)
        steps.append({"id": f"sample-{track}", "text": text})
    if starter:
        steps.append({"id": "starter-ready", "text": blueprint_text(loc, "step_starter_ready")})
    return steps


def _localized_meta_steps(data: dict[str, Any], loc: str) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    meta = data.get("plg_producer_meta") if isinstance(data.get("plg_producer_meta"), dict) else {}

    if data.get("plg_drum_blueprint"):
        steps.append({"id": "drum-blueprint", "text": blueprint_text(loc, "step_drum_blueprint")})
        steps.append({"id": "808-engine", "text": blueprint_text(loc, "step_808_engine")})

    if meta.get("markov_hats") or data.get("plg_drum_blueprint"):
        steps.append({"id": "hat-grid", "text": blueprint_text(loc, "step_hat_grid")})

    lag = meta.get("melody_lag_ms")
    if isinstance(lag, (int, float)) and lag > 0:
        steps.append({
            "id": "groove-lag",
            "text": blueprint_text(loc, "step_groove_lag", ms=int(lag)),
        })

    if meta.get("voicing_spread"):
        steps.append({"id": "voicing", "text": blueprint_text(loc, "step_voicing")})

    if meta.get("kick_syncopation") and not data.get("plg_drum_blueprint"):
        steps.append({"id": "kick-sync", "text": blueprint_text(loc, "step_kick_sync")})

    if meta.get("snare_riser"):
        steps.append({"id": "snare-riser", "text": blueprint_text(loc, "step_snare_riser")})

    if meta.get("mono_stereo_drop"):
        steps.append({"id": "mono-stereo", "text": blueprint_text(loc, "step_mono_stereo")})

    # NB: was `data.get("plg_space_mix") is not False` — permissive-by-default,
    # so it claimed the Reverb2/Delay3/Blood Overdrive insert chain was baked
    # in on every beat when it's actually opt-in-only (space_mix_enabled ==
    # plg_space_mix is True) and quarantined off by default (see
    # mix_insert_fx.insert_chains_enabled — a live-FL reliability incident).
    from mix_insert_fx import space_mix_enabled

    if space_mix_enabled(data):
        steps.append({
            "id": "space-mix",
            "text": blueprint_text(loc, "step_space_mix"),
        })

    plg_key = data.get("plg_key") or meta.get("key")
    if plg_key:
        steps.append({
            "id": "key-lock",
            "text": blueprint_text(loc, "step_key_lock", key=str(plg_key)),
        })

    genre = data.get("plg_genre") or meta.get("genre")
    if genre:
        extra = blueprint_text(loc, "step_genre_max_drive") if meta.get("max_drive") else ""
        steps.append({
            "id": "genre-profile",
            "text": blueprint_text(loc, "step_genre_profile", genre=str(genre), extra=extra),
        })

    chop = data.get("plg_sample_chop")
    if isinstance(chop, dict) and chop.get("count"):
        steps.append({
            "id": "sample-chop",
            "text": blueprint_text(
                loc,
                "step_sample_chop",
                count=int(chop.get("count", 0)),
                source=Path(str(chop.get("source", "sample"))).name,
                pitch=float(chop.get("pitch", 0)),
                tempo=float(chop.get("tempo", 1)),
            ),
        })

    midi = data.get("plg_midi_ingest")
    if isinstance(midi, dict) and midi.get("name"):
        steps.append({
            "id": "midi-ingest",
            "text": blueprint_text(
                loc,
                "step_midi_ingest",
                name=str(midi.get("name", "MIDI")),
                notes=int(midi.get("notes", 0)),
            ),
        })

    return steps


def _localized_done_lines(data: dict[str, Any], loc: str, *, limit: int = 8) -> list[str]:
    """Localized bullet lines for READ_ME «PLG ALREADY DID» section."""
    steps = _localized_sample_steps(data, loc) + _localized_meta_steps(data, loc)
    return [s["text"] for s in steps[:limit]]


def _mastering_math_block(data: dict[str, Any], bpm: float, locale: str) -> list[str]:
    """Exact commercial mix/master targets, with reverb times derived from BPM
    (ms = 60000 / BPM). Peaks/mono/sidechain now reflect what's actually baked
    (see _DEFAULT_CHANNEL_VOL in flp_writer.py, mono_below in sample_fx.py,
    duck_808_under_kick in beat_humanize.py) — EQ/reverb/loudness-ceiling are
    still a spec for FL, nothing bakes those yet."""
    beat_ms = 60_000.0 / max(1.0, bpm)
    predelay_ms = beat_ms / 4.0       # 1/16-note pre-delay
    bar_ms = beat_ms * 4.0            # one bar = reverb decay target
    header = blueprint_text(locale, "master_math_header")
    sidechain_line = (
        "  Sidechain: kick -> 808 duck already baked (real note retriggers, ~26 velocity dip, 0.6-beat recovery)."
        if _kick_808_duck_baked(data)
        else "  Sidechain: 808 → Fruity Limiter (COMP) → input = Kick · Threshold ~1/2 down · Ratio up (~50 ms duck)"
    )
    return [
        f"─── {header} ───",
        "  Peaks (baked):  Kick -3 dB · 808 -4.5 dB · Clap/Snare -5 dB · Melody -12 dB · Hi-Hats -9 dB",
        "  EQ (apply in FL):  Kick HPF <30 Hz · 808 LPF >250 Hz (vocal space) · Clap +1.5 kHz snap",
        "          Hats HPF <500 Hz · Melody HPF <150 Hz + dip 200-500 Hz & 2-4 kHz",
        sidechain_line,
        "  Mono:   808 already mono <300 Hz (mid/side crossover baked in) · other lanes below 120 Hz still manual",
        f"  Reverb @ {bpm:.0f} BPM:  beat = {beat_ms:.1f} ms · pre-delay 1/16 = {predelay_ms:.1f} ms · decay 1 bar = {bar_ms:.0f} ms",
        "  Master: Fruity Soft Clipper already baked · Ceiling -1.0 dB (true-peak) · Dithering + noise-shaping still apply in FL by ear",
        "          Target loudness: -7..-5 LUFS short-term (commercial trap density) — still apply in FL, no loudness metering baked in",
        "",
    ]


def build_mix_blueprint(data: dict[str, Any], *, stem_folder: str | None = None) -> str:
    """Producer-style mixing cheat sheet for the generated beat."""
    bpm = float(data.get("bpm", 140))
    style = str(data.get("style", "unknown"))
    bucket = _style_bucket(data)
    prompt = str(data.get("user_prompt", ""))[:120]
    locale = str(data.get("plg_locale") or "en")

    lines = [
        "═══════════════════════════════════════════════════════════",
        f"  {blueprint_text(locale, 'title')}",
        "═══════════════════════════════════════════════════════════",
        "",
        f"{blueprint_text(locale, 'track')}: {style}",
        _bpm_line(bpm, bucket, locale),
        f"{blueprint_text(locale, 'prompt')}: {prompt}" if prompt else "",
        "",
        f"─── {blueprint_text(locale, 'stems_header')} ───",
    ]

    if stem_folder:
        lines.extend([
            f"{blueprint_text(locale, 'stems_folder')}: {stem_folder}",
            blueprint_text(locale, "stems_drag"),
            blueprint_text(locale, "stems_map"),
            blueprint_text(locale, "stems_or_one"),
            "",
        ])
    else:
        lines.extend([
            blueprint_text(locale, "stems_fallback"),
            "",
        ])

    lines.append(f"─── {blueprint_text(locale, 'channels_header')} ───")
    lines.append("")
    lines.extend(_channel_block(data, bucket, locale))

    # NB: the old "HARD TRAP FX RECIPE" block (fx_recipe_808/fx_recipe_master)
    # duplicated — and contradicted — the already-baked lines _channel_block
    # now prints (808 warmth, master clip). Dropped rather than shown twice.
    # fx_recipe_kick survives as an informational bullet in _channel_block.

    lines.extend(_mastering_math_block(data, bpm, locale))

    manual = _localized_done_lines(data, locale)
    if manual:
        lines.extend([f"─── {blueprint_text(locale, 'plg_done')} ───", ""])
        lines.extend(f"  • {step}" for step in manual)
        lines.append("")

    fx = data.get("fx_automation")
    if fx:
        lines.extend([f"─── {blueprint_text(locale, 'fx_header')} ───", json.dumps(fx, ensure_ascii=False, indent=2), ""])

    lines.extend([
        f"─── {blueprint_text(locale, 'scripts_header')} ───",
        f"  {blueprint_text(locale, 'scripts_hint')}",
        "",
        blueprint_text(locale, "footer"),
        "═══════════════════════════════════════════════════════════",
    ])
    return "\n".join(line for line in lines if line is not None)


def export_mix_blueprint(
    data: dict[str, Any],
    output_path: Path = BLUEPRINT_FILE,
    *,
    stem_folder: str | None = None,
) -> Path:
    text = build_mix_blueprint(data, stem_folder=stem_folder)
    output_path.write_text(text + "\n", encoding="utf-8")
    return output_path


def session_slug(data: dict[str, Any]) -> str:
    bpm = int(float(data.get("bpm", 140)))
    raw = re.sub(r"[^\w\s-]", "", str(data.get("style", "beat")).lower())
    slug = re.sub(r"\s+", "_", raw.strip())[:28] or "beat"
    return f"PLG_Stems_{bpm}bpm_{slug}"


def list_blueprint_steps(data: dict[str, Any], locale: str | None = None) -> list[dict[str, str]]:
    """Interactive producer checklist for the web UI."""
    loc = _blueprint_locale(data, locale)
    steps: list[dict[str, str]] = []

    steps.append({
        "id": "clipper-master",
        "text": blueprint_text(
            loc, "already_baked_master_clip" if _master_clip_baked(data) else "step_clipper_master"
        ),
    })
    steps.append({
        "id": "clipper-808",
        "text": blueprint_text(
            loc, "already_baked_808_warmth" if _saturation_baked() else "step_clipper_808"
        ),
    })

    steps.append({
        "id": "pre-snare",
        "text": blueprint_text(loc, "step_pre_snare"),
    })
    steps.append({
        "id": "hat-6db",
        "text": blueprint_text(loc, "step_hat_6db"),
    })
    steps.append({
        "id": "hat-choke",
        "text": blueprint_text(loc, "step_hat_choke"),
    })
    steps.append({
        "id": "808-attack",
        "text": blueprint_text(loc, "step_808_attack"),
    })
    steps.append({
        "id": "sidechain",
        "text": blueprint_text(
            loc, "already_baked_kick_808_duck" if _kick_808_duck_baked(data) else "manual_sidechain_fallback"
        ),
    })
    steps.append({
        "id": "stems",
        "text": blueprint_text(loc, "step_stems"),
    })

    steps.extend(_localized_sample_steps(data, loc))
    steps.extend(_localized_meta_steps(data, loc))

    if data.get("pitch_bend_automation"):
        steps.append({
            "id": "pitch-bend",
            "text": blueprint_text(loc, "step_pitch_bend"),
        })

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for step in steps:
        key = step["text"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(step)
    return unique[:24]
