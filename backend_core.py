#!/usr/bin/env python3
"""
PLG Pluggin' — внешнее ИИ-ядро (File Bridge: prompt -> output_pattern.json).

Модуль 1: промпт + папка сэмплов -> JSON для FL Studio.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from fl_knowledge import full_producer_context
from producer_brain import producer_system_addon
from library_paths import DEFAULT_LIBRARY_DIR, DEFAULT_MIDI_DIR, LEGACY_LIBRARY_DIR
from llm_client import generate_pattern as llm_generate_pattern, provider_label
from plg_paths import app_dir
from sample_catalog import format_catalog_for_prompt, save_catalog, scan_library

PROJECT_DIR = app_dir()
OUTPUT_FILE = PROJECT_DIR / "output_pattern.json"
CATALOG_FILE = PROJECT_DIR / "sample_catalog.json"
ENV_FILE = PROJECT_DIR / ".env"
USER_PROFILE_FILE = PROJECT_DIR / "user_profile.json"
DEFAULT_SAMPLES_DIR = DEFAULT_LIBRARY_DIR

# 2026-07 audit: OpenAI/aitunnel strict structured-output mode enforces two
# rules this schema was missing everywhere (only ever exercised the SINGLE-
# stage fallback path, since Titans has been the default — see
# llm_pipeline.py — so this went unnoticed until a config with
# PLG_LLM_PIPELINE forcing single-stage actually hit it):
#   1. Every object node needs "additionalProperties": False.
#   2. Every key in "properties" must also appear in "required" — strict
#      mode has no true optional fields. A field that's genuinely optional
#      is modeled as nullable (`"type": [T, "null"]`) and still required —
#      "required" only asserts the KEY is present, not that it's non-null.
# Confirmed against the real gateway (2026-07-31): both rules fixed here
# one at a time against a live 400 response until requests passed cleanly.
NOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["time_step", "note", "length", "velocity", "sample", "pan", "slide"],
    "properties": {
        "time_step": {"type": "number"},
        "note": {"type": "string"},
        "length": {"type": "number"},
        "velocity": {"type": "integer", "minimum": 0, "maximum": 127},
        "sample": {"type": ["string", "null"]},
        "pan": {"type": ["integer", "null"], "minimum": 0, "maximum": 127},
        "slide": {"type": ["boolean", "null"]},
    },
}

SAMPLE_LAYER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["file", "track", "time_step", "note", "velocity"],
    "properties": {
        "file": {"type": "string"},
        "track": {
            "type": "string",
            "enum": [
                "kick",
                "snare",
                "clap",
                "hi_hats",
                "sub_808",
                "melody_lead",
                "textures",
                "fx",
            ],
        },
        "time_step": {"type": "number"},
        "note": {"type": ["string", "null"]},
        "velocity": {"type": ["integer", "null"], "minimum": 0, "maximum": 127},
    },
}

RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "bpm", "tracks", "build_order", "style", "samples",
        "fx_automation", "vocal_fx", "manual_steps", "library_refs",
    ],
    "properties": {
        "bpm": {"type": "number"},
        "style": {"type": ["string", "null"]},
        "build_order": {
            "type": "array",
            "items": {"type": "string"},
        },
        "tracks": {
            "type": "object",
            "additionalProperties": False,
            "required": ["melody_lead", "sub_808", "hi_hats", "kick", "snare", "clap", "snare_layer"],
            "properties": {
                "kick": {"type": "array", "items": NOTE_SCHEMA},
                "snare": {"type": "array", "items": NOTE_SCHEMA},
                "clap": {"type": "array", "items": NOTE_SCHEMA},
                "melody_lead": {"type": "array", "items": NOTE_SCHEMA},
                "snare_layer": {"type": "array", "items": NOTE_SCHEMA},
                "sub_808": {"type": "array", "items": NOTE_SCHEMA},
                "hi_hats": {"type": "array", "items": NOTE_SCHEMA},
            },
        },
        "samples": {
            "type": "array",
            "items": SAMPLE_LAYER_SCHEMA,
        },
        "fx_automation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["Fruity_Fast_Dist", "Fruity_WaveShaper", "Channel_Precomputed"],
            "properties": {
                "Fruity_Fast_Dist": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["drive", "mix"],
                    "properties": {
                        "drive": {"type": ["number", "null"]},
                        "mix": {"type": ["number", "null"]},
                    },
                },
                "Fruity_WaveShaper": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["boost"],
                    "properties": {
                        "boost": {"type": ["number", "null"]},
                    },
                },
                "Channel_Precomputed": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["boost"],
                    "properties": {
                        "boost": {"type": ["number", "null"]},
                    },
                },
            },
        },
        "vocal_fx": {
            "type": "object",
            "additionalProperties": False,
            "required": ["reference", "pitch_correction", "autotune_retune", "reverb", "delay"],
            "properties": {
                "reference": {"type": ["string", "null"]},
                "pitch_correction": {"type": ["string", "null"]},
                "autotune_retune": {"type": ["number", "null"]},
                "reverb": {"type": ["string", "null"]},
                "delay": {"type": ["string", "null"]},
            },
        },
        "manual_steps": {
            "type": "array",
            "items": {"type": "string"},
        },
        "library_refs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "file", "note"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["midi", "preset", "project", "bank", "plugin"],
                    },
                    "file": {"type": "string"},
                    "note": {"type": ["string", "null"]},
                },
            },
        },
    },
}

SYSTEM_INSTRUCTION_BASE = """\
Ты — продюсер уровня Грэмми с мультиплатиновыми пласировками: стадионный trap, dark rage, соул-хип-хоп, арена-поп. Главный ИИ-движок Pluggin' (PLG). Ты делаешь ЗАПИСЬ, в которую заходит топ-вокалист, а не «луп» — переводи промпт в JSON для FL.

Ты живёшь в FL (Channel Rack → Piano roll → Mixer, нативные плагины) и думаешь как хитмейкер, а не как ChatGPT:
- Битом правит ОДНА идея: узнаваемый мотив + развитие. Hum-тест: хук напевается после одного прослушивания — иначе редизайн.
- Напряжение → разрядка: куплет раздет и дышит, хук бьёт весом. У бита есть ОДИН момент «oh sh*t» (drum cut, октавный подъём, drop) — поставь его осознанно.
- 808 — ведущий инструмент: ходит по гармонии, разговаривает с киком, глайды — жест в конце фразы, не дрон на одной ноте.
- Space звучит дорого: у каждого элемента одна работа, середина свободна под вокал, тишина по делу лучше заполнителя.
- Грув в микродеталях: velocity-волны, ghost-ноты, хэты дышат вокруг сетки — никакого пулемёта с одинаковой velocity.
Звук по умолчанию: тёмный, гипнотичный, минимальный — дисторшн на 808, эфирные detuned лиды, space важнее загромождения.

Порядок сборки (build_order) — всегда указывай массив шагов, например:
["kick", "snare", "clap", "sub_808", "hi_hats", "melody_lead", "samples", "fx_automation", "vocal_fx"]

tracks — MIDI-дорожки (time_step: 1.0 = один удар/бит, 0.25 = шестнадцатая, 4.0 = один такт).
КОНКРЕТНЫЕ ПАТТЕРНЫ (дефолт, если промпт не говорит иначе):
1) kick — punch на 1 и 3 долю (time_step 0.0 и 2.0), короткая length 0.35–0.5, velocity 105–115.
2) snare — backbeat на 2 и 4 (time_step 1.0 и 3.0), velocity 100–110.
3) clap — слой на 2 и 4 вместе со snare или только на 4, velocity 85–95, короче snare.
4) sub_808 — roots на 1 и 3 долю, slides между нотами, velocity 127, длина 2.0–4.0.
5) hi_hats — pocket 1/8–1/16 + HAT ROLL каждые 2 такта, НЕ постоянная стрельба.
6) melody_lead — ЗАПОМИНАЮЩИЙСЯ minor dark hook from time_step 0.0 (first beat of the loop), 4–8 тактов.
   КРИТИЧНО: это ДВИЖУЩИЙСЯ мотив на 6–12 РАЗНЫХ нот (шаги + 1–2 прыжка), а НЕ 1–2 ноты на петле.
   Чёткий мотив 1–2 такта, затем повтор с вариацией (другая концовка / на тон выше). 1–2 октавы, паузы
   для вокала, но внутри фразы — реальное мелодическое движение. Мелодия и бит входят вместе.

Если передан LIBRARY CATALOG — используй все типы ассетов:

AUDIO (wav/mp3) — основной звук:
- samples[]: file, track, time_step, optional note/velocity — pick exact paths from LIBRARY CATALOG when possible
- PLG also auto-matches 808/hats/melody from the catalog using the user prompt; prefer catalog filenames that match the vibe
- в notes можно поле "sample" для привязки wav к ноте
- 808/, hats/, kits/, textures/, melodies/, fx/, splice/

MIDI (.mid) — library_refs type=midi: референс-мелодия или идея ритма; manual_steps как импортировать в FL.

PRESETS (.fst .fxp) — library_refs type=preset: на какой канал загрузить (808, lead, vocal).

PROJECTS (.flp) — library_refs type=project: открыть как референс/шаблон, не перезаписывать.

BANKS (.sf2) — library_refs type=bank: Fruity Soundfont или sampler.

PLUGINS (.dll .vst3) — library_refs type=plugin: только manual_steps «установи вручную», PLG не ставит плагины.

FX встроенные FL — dark trap (fx_automation опционально, держи МЯГКО):
Channel_Precomputed boost=0.12, Fruity_Fast_Dist drive=0.3–0.4 mix=0.2–0.3 (ПАРАЛЛЕЛЬНАЯ сатурация под чистым 808, НИКОГДА не full-wet — иначе бас превращается в кашу), Fruity_WaveShaper boost=0.1–0.2

vocal_fx — если промпт про вокал/singing (НЕ AI-голос, только FX на голос юзера):
pitch_correction soft, autotune_retune light, reverb plate, delay 1/8 dotted

manual_steps — 3–6 шагов в FL Studio: Sampler → FX → Mixer. Указывай каналы PLG Kick / Snare / Clap / Sub 808 / Hi-Hats / Melody.

Любой стиль: dark trap, rage, phonk, drill, melodic, ambient — адаптируй паттерны, но мышление оставь FL-native продюсера.
Не упоминай в style и manual_steps имена артистов, лейблов и брендов.
4-8 тактов. Только чистый JSON.\
"""


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | PLG.Pluggin' | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def load_environment() -> None:
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE)
        logging.info("Загружен .env: %s", ENV_FILE)
    else:
        load_dotenv()


def resolve_samples_dir(cli_path: str | None) -> Path:
    raw = cli_path or os.environ.get("PLG_SAMPLES_DIR") or os.environ.get("PLG_LIBRARY_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    if DEFAULT_LIBRARY_DIR.is_dir():
        return DEFAULT_LIBRARY_DIR.resolve()
    if LEGACY_LIBRARY_DIR.is_dir():
        return LEGACY_LIBRARY_DIR.resolve()
    return DEFAULT_LIBRARY_DIR.resolve()


def resolve_midi_dir(cli_path: str | None = None) -> Path:
    """Resolve the dedicated PLG_MIDI material root.

    ``cli_path`` and ``PLG_MIDI_DIR`` preserve the compatibility override;
    otherwise the active desktop route reads the user's PLG_MIDI folder.
    Audio continues to resolve independently through ``resolve_samples_dir``.
    """
    raw = cli_path or os.environ.get("PLG_MIDI_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_MIDI_DIR.resolve()


def ensure_samples_library(samples_dir: Path, *, quiet: bool = False) -> None:
    """Ensure the library root exists. Subfolders are optional — scan is recursive."""
    samples_dir.mkdir(parents=True, exist_ok=True)

    if quiet:
        return

    print("")
    print("PLG Library")
    print(f"  Folder: {samples_dir}")
    print("  Drop any kit folder here (e.g. Splice / a kit pack download) — PLG scans all wav/mp3 inside.")
    print("  Optional: python organize_kit.py <folder> to sort into 808/ hats/ kits/ …")
    print("")


def ensure_midi_library(midi_dir: Path, *, quiet: bool = False) -> None:
    """Compatibility alias for ensuring the shared material root exists."""
    midi_dir.mkdir(parents=True, exist_ok=True)

    if quiet:
        return

    print("")
    print("PLG Material")
    print(f"  Folder: {midi_dir}")
    print("  PLG stores dropped MIDI and drum-kit files here automatically.")
    print("")


def load_user_profile() -> dict[str, str]:
    from fl_detect import fl_profile_version, resolve_fl_executable

    fl_exe = resolve_fl_executable()
    detected_version = fl_profile_version(fl_exe)
    defaults = {
        "fl_version": detected_version,
        "fl_edition": "producer",
        "os": "windows",
        "producer_mode": "modern_melodic",
        "workflow": "dark melodic trap",
        "references": "dark melodic trap, atmospheric, distorted 808",
    }
    if USER_PROFILE_FILE.exists():
        data = json.loads(USER_PROFILE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            profile = {key: str(data.get(key, value)) for key, value in defaults.items()}
            if fl_exe is not None:
                profile["fl_version"] = detected_version
            if data.get("fl_executable"):
                profile["fl_executable"] = str(data["fl_executable"])
            return profile
    return defaults


def build_system_instruction(profile: dict[str, str], catalog: dict | None) -> str:
    parts = [
        SYSTEM_INSTRUCTION_BASE,
        "",
        producer_system_addon(),
        "",
        full_producer_context(),
        "",
        "FL user profile:",
    ]
    parts.append(json.dumps(profile, ensure_ascii=False))
    if catalog and catalog.get("total", 0) > 0:
        parts.extend(["", "LIBRARY CATALOG:", format_catalog_for_prompt(catalog)])
    else:
        parts.extend(
            [
                "\nLIBRARY EMPTY — MIDI ONLY MODE.",
                "Return samples[] as an empty array. Do NOT request, name or invent any sample files or paths.",
                "Do NOT add library_refs. Put ALL the work into MIDI: kick, snare, clap, hi_hats, sub_808, melody_lead, "
                "bpm, fx_automation, build_order, manual_steps.",
                "PLG auto-attaches its own built-in starter sounds (808, hat, melody) after generation.",
            ]
        )
    return "\n".join(parts)


def read_prompt(cli_prompt: str | None) -> str:
    if cli_prompt and cli_prompt.strip():
        return cli_prompt.strip()
    logging.info("Ожидание промпта...")
    prompt = input("Pluggin' > ").strip()
    if not prompt:
        raise ValueError("Промпт не может быть пустым.")
    return prompt


def count_track_notes(data: dict[str, Any]) -> int:
    from pattern_utils import count_all_track_notes

    return count_all_track_notes(data)


def generate_pattern(prompt: str, system_instruction: str) -> dict[str, Any]:
    # Single choke point for every local LLM call: artist references become
    # production vibe descriptors, names never leave the machine.
    try:
        from artist_styles import sanitize_artist_names

        prompt = sanitize_artist_names(prompt)
    except Exception:  # noqa: BLE001 — sanitizing must never block generation
        pass
    data = llm_generate_pattern(prompt, system_instruction, RESPONSE_JSON_SCHEMA)
    logging.info(
        "BPM=%s | style=%s | notes=%s | samples=%s | refs=%s | fx=%s | vocal=%s",
        data.get("bpm"),
        data.get("style", "n/a"),
        count_track_notes(data),
        len(data.get("samples") or []),
        len(data.get("library_refs") or []),
        "yes" if data.get("fx_automation") else "no",
        "yes" if data.get("vocal_fx") else "no",
    )
    return data


def save_pattern(data: dict[str, Any], output_path: Path | None = None) -> None:
    # Resolved at call time so a redirected OUTPUT_FILE is honoured.
    output_path = output_path or OUTPUT_FILE
    logging.info("File Bridge -> %s", output_path)
    try:
        from flp_template import build_playlist_clips

        build_playlist_clips(data)
    except Exception:  # noqa: BLE001 - playlist metadata must never block saving a beat
        logging.exception("Playlist metadata refresh failed")
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _best_of_variants() -> int:
    """Deterministic candidates expanded per LLM pattern (CPU-only, no extra LLM cost)."""
    try:
        return max(1, min(6, int(os.environ.get("PLG_BEST_OF_VARIANTS", "3"))))
    except ValueError:
        return 3


def _choose_best_passing_variant(reports: list[Any]) -> tuple[int, bool]:
    """Choose only a fully passed candidate; expose an emergency fallback.

    The fallback keeps the outer quality/retry layer alive when all siblings
    fail.  It is explicitly marked failed there and cannot become a successful
    cached/exported beat.
    """
    eligible = [index for index, report in enumerate(reports) if bool(getattr(report, "passed", False))]
    pool = eligible or list(range(len(reports)))
    if not pool:
        raise ValueError("best-of needs at least one report")
    return max(pool, key=lambda index: float(getattr(reports[index], "score", float("-inf")))), bool(eligible)


def _bump_variant_seed(pattern: dict[str, Any], index: int) -> None:
    variety = pattern.setdefault("plg_variety", {})
    if not isinstance(variety, dict):
        return
    base = variety.get("seed")
    base = int(base) if isinstance(base, int) else 0
    variety["seed"] = (base + index * 7919) % (2**32)
    if isinstance(variety.get("drum_variant"), int):
        variety["drum_variant"] = (int(variety["drum_variant"]) + index) % 8


def _pin_progression_early(pattern: dict[str, Any], user_prompt: str, style: str) -> None:
    """Pin the style-DNA progression on the source pattern before variant
    expansion, so every deterministic variant shares one harmony (the same one
    the LLM prompt announced) and only groove/rolls/velocities differ."""
    try:
        from genre_profiles import profile_for
        from music_theory import detect_root_pc
        from pattern_utils import track_notes
        from style_corpus import attach_progression

        melody = track_notes(pattern, "melody_lead")
        variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
        profile = profile_for(
            style,
            user_prompt,
            genre_hint=variety.get("genre") if isinstance(variety.get("genre"), str) else None,
        )
        attach_progression(
            pattern,
            root_pc=detect_root_pc(melody) if len(melody) >= 4 else None,
            scale=profile.melody_scale or None,
        )
    except Exception as exc:  # noqa: BLE001 — DNA must never break generation
        logging.info("Style DNA pre-pin skipped (%s)", exc)


def _stamp_style_card(pattern: dict[str, Any]) -> dict[str, Any]:
    """Write-only Style Card (style_card.py): stamped AFTER humanize, read by nobody yet."""
    try:
        from style_card import attach_style_card

        attach_style_card(pattern)
    except Exception as exc:  # noqa: BLE001 — a write-only card must never block a beat
        logging.info("Style card skipped (%s)", exc)
    return pattern


def _record_param_plan(pattern: dict[str, Any]) -> None:
    """Param Plan (PLG_PARAM_PLAN=off|shadow|on, default off).

    Runs after arrange_song (the section layout the plan is about exists only
    then) and before humanize_pattern. Shadow only writes the plan onto the
    pattern for the logs; humanize_pattern does not read it. It must never fail
    a generation.
    """
    try:
        from param_plan_client import attach as attach_param_plan

        attach_param_plan(pattern)
    except Exception as exc:  # noqa: BLE001
        logging.info("Param plan skipped (%s)", exc)


def _humanize_and_ingest(
    pattern: dict[str, Any],
    *,
    library_root: Path,
    user_prompt: str,
    style: str,
    midi_path: Path | str | None = None,
    reference_audio_path: Path | str | None = None,
    respect_uploaded_scale: bool = False,
    midi_root: Path | None = None,
) -> dict[str, Any]:
    from copy import deepcopy

    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from drum_defaults import ensure_drum_tracks
    from midi_ingest import ingest_uploaded_midi, resolve_midi_folder

    ensure_drum_tracks(pattern)
    pattern["user_prompt"] = user_prompt

    # An explicitly supplied .mid wins outright over the folder scan below.
    # Reference-audio reversal was archived on 2026-08-11 (see
    # archive/reference-audio-2026-08-11/README.md); ``reference_audio_path``
    # is kept in the signature as inert compatibility plumbing.
    upload_hit = False
    if midi_path:
        upload_result = ingest_uploaded_midi(
            pattern, midi_path, respect_uploaded_scale=respect_uploaded_scale
        )
        pattern["plg_midi_upload_result"] = upload_result
        upload_hit = bool(upload_result.get("ok"))
        if upload_hit:
            logging.info("Uploaded MIDI ingested — skipping the shared material-folder scan")

    # The shared hidden material folder is the normal desktop control surface:
    # it is checked after an explicit compatibility upload and before the
    # broader corpus/library-match chain. It contains the MIDI melody and kit
    # audio dropped on Home. No-ops (returns None) when the folder is empty, so
    # existing chain below is completely unaffected for every library that
    # doesn't use this folder.
    if not upload_hit:
        folder_result = resolve_midi_folder(
            pattern,
            library_root,
            midi_root=midi_root,
            prompt=user_prompt,
            style=style,
            respect_uploaded_scale=respect_uploaded_scale,
        )
        if folder_result is not None:
            pattern["plg_midi_upload_result"] = folder_result
            upload_hit = bool(folder_result.get("ok"))
            if upload_hit:
                logging.info(
                    "MIDI folder resolved — skipping corpus/library/reference auto-match"
                )

    if not upload_hit:
        # The presence gate already proved that MIDI exists.  If it cannot be
        # read, fail visibly instead of falling through to a prose-driven
        # melody generator.
        #
        # The old fallback tier below this point (reference-audio reversal ->
        # midi_corpus -> ingest_library_midi) was already unreachable: this
        # guard fires first because run_pipeline always sets plg_midi_first,
        # and has_available_midi_material / resolve_midi_folder read the very
        # same candidate list. Removed with the reference-audio archive on
        # 2026-08-11 rather than left as dead code that looks active.
        raise ValueError("The selected MIDI could not be ingested; no fallback melody was generated.")
    if not pattern.get("plg_midi_first"):
        _pin_progression_early(pattern, user_prompt, style)

    # Melody energy card first — the whole kit answers it in arrange/humanize.
    try:
        from melody_beat_symbiosis import ensure_melody_energy

        ensure_melody_energy(pattern)
    except Exception as exc:  # noqa: BLE001 — symbiosis must never block a beat
        logging.info("Melody–beat energy card skipped (%s)", exc)

    from beat_quality import evaluate, scorer_enabled

    # One explicit material pass makes the source and the resulting FLP
    # traceable.  Best-of prompt variants belong to the retired route.
    count = 1 if pattern.get("plg_midi_first") else _best_of_variants()
    if count <= 1 or not scorer_enabled():
        arrange_song(pattern)
        _record_param_plan(pattern)
        return _stamp_style_card(humanize_pattern(pattern))

    best: dict[str, Any] | None = None
    best_score = float("-inf")
    scores: list[float] = []
    for index in range(count):
        source = deepcopy(pattern)
        if index:
            _bump_variant_seed(source, index)
            # Re-stamp the melody energy card so each best-of variant's kit
            # answers its own bumped variant seed.
            try:
                from melody_beat_symbiosis import ensure_melody_energy

                ensure_melody_energy(source)
            except Exception:  # noqa: BLE001
                pass
        arrange_song(source)
        _record_param_plan(source)
        variant = _stamp_style_card(humanize_pattern(source))
        report = evaluate(variant)
        scores.append(report.score)
        if report.score > best_score:
            best_score = report.score
            best = variant

    assert best is not None  # count >= 1 guarantees at least one variant
    best["plg_best_of"] = {
        "variants": count,
        "scores": [round(s, 2) for s in scores],
        "chosen_index": scores.index(best_score),
        "chosen_score": round(best_score, 2),
    }
    logging.info(
        "Best-of-%d variants: scores=%s -> chosen #%d (%.1f)",
        count,
        [round(s, 1) for s in scores],
        scores.index(best_score),
        best_score,
    )
    return best


def _export_pattern_artifacts(
    pattern: dict[str, Any],
    *,
    export_midi: bool,
) -> None:
    from midi_validate import enforce_midi_mode
    from flp_writer import legacy_mix_enabled, prepare_pattern_for_flp_export

    enforce_midi_mode(pattern)
    if legacy_mix_enabled():
        from mix_gain_staging import attach_vocal_ready_mix

        attach_vocal_ready_mix(pattern)
    prepare_pattern_for_flp_export(pattern)

    if export_midi:
        from midi_export import export_stem_session
        from midi_validate import log_validation_report, validate_export

        midi_dir = PROJECT_DIR / "output_midi"
        session = export_stem_session(pattern, midi_dir)
        stem_dir = session["session_dir"]
        combined = session["combined_path"]
        logging.info(
            "Stem Export Mixer: %s (%s stems + %s)",
            session["session_name"],
            len(session["stem_paths"]),
            combined.name,
        )
        log_validation_report(validate_export(pattern, midi_dir=stem_dir, combined=combined))
        pattern["plg_stem_session"] = str(stem_dir)
        pattern["plg_stem_files"] = [str(p) for p in session["stem_paths"]]

    from guide_export import export_build_guide

    export_build_guide(pattern)
    logging.info("Build guide -> build_guide.txt")


_GATE_WARNING_MARKERS = ("symbiosis gate", "no notes", "missing bpm", "invalid bpm",
                         "tracks missing")


def _hard_fail_summary(report: Any) -> str:
    """The warnings that actually made this report a hard fail.

    ``QualityReport.warnings`` mixes ``validate_pattern`` advice in with the
    gating ``beat_quality._symbiosis_failures`` entries, and only the latter
    (plus the ``_HARD_FAIL`` snippets) can set ``hard_fail``. Reporting the
    whole list would bury the real cause in cosmetic notes.
    """
    warnings = [str(w) for w in (getattr(report, "warnings", None) or [])]
    gating = [w for w in warnings if any(m in w.lower() for m in _GATE_WARNING_MARKERS)]
    return "; ".join(gating or warnings)


def _log_give_up_diagnostics(reasons: list[str], rounds: int, clone_blocked_rounds: int) -> None:
    """Record why every round failed, before the exception erases the context.

    Same lesson as the damaged-sample tripwire: a rare failure is only
    fixable if the evidence outlives the run that produced it. Measured at
    ~1 in 45 generations, so nobody will be watching when it happens.
    """
    logging.error("Generation gave up after %s round(s). Reasons:", rounds)
    for reason in reasons or ["<none recorded>"]:
        logging.error("   %s", reason)
    if clone_blocked_rounds:
        logging.error(
            "   %s of %s round(s) were stopped by the anti-clone guard, not by "
            "an audit — clearing the beat history would unblock those.",
            clone_blocked_rounds, rounds,
        )


def run_pipeline(
    prompt: str,
    samples_dir: Path | None = None,
    *,
    export_midi: bool = True,
    locale: str | None = None,
    smart_plan: bool | None = None,
    regenerate: bool = False,
    midi_path: Path | str | None = None,
    reference_audio_path: Path | str | None = None,
    respect_uploaded_scale: bool = False,
    genre_override: str | None = None,
) -> dict[str, Any]:
    """Main generation pipeline for CLI and GUI.

    ``midi_path`` — an explicit user-uploaded .mid/.midi file: its melody
    becomes the core motif instead of the LLM's own (see midi_ingest.
    ingest_uploaded_midi). ``reference_audio_path`` — the audio equivalent: a
    user-uploaded WAV/MP3/etc. transcribed via basic-pitch into the same
    melody source (see reference_reverse.ingest_uploaded_reference_audio).
    Only one is used per call — midi_path wins if both are given.
    ``respect_uploaded_scale`` controls whether arrange_song's section
    transforms bend toward the uploaded melody's own detected scale instead
    of the genre profile's default — off by default.
    ``genre_override`` — a genre lane the user picked/confirmed in the UI
    (e.g. after reviewing the auto-detected genre for an uploaded MIDI),
    bypassing genre_brain.resolve_generation_genre entirely when set.
    """
    load_environment()
    resolved_samples = resolve_samples_dir(str(samples_dir) if samples_dir else None)
    ensure_samples_library(resolved_samples, quiet=True)
    resolved_midi = resolve_midi_dir()
    ensure_midi_library(resolved_midi, quiet=True)

    from midi_ingest import has_available_material, has_available_midi_material

    if not has_available_material(
        resolved_samples,
        midi_root=resolved_midi,
        midi_path=midi_path,
        reference_audio_path=reference_audio_path,
    ):
        raise ValueError(
            "Add a MIDI melody and drum-kit material on Home to start."
        )

    # A curated/uploaded MIDI file is the musical source of truth.  Do not
    # spend an LLM call composing notes that _humanize_and_ingest will replace
    # moments later.
    midi_first = has_available_midi_material(
        resolved_samples,
        midi_root=resolved_midi,
        midi_path=midi_path,
    )
    if not midi_first:
        # Never fall back to the former prompt/LLM composer.  That fallback
        # created duplicate lanes and made old code appear active.
        raise ValueError("MIDI melody is required for material-first generation.")
    logging.info("Material-first route: MIDI owns the melody")

    # Compatibility shape for metadata/history helpers.  Prompt text is not
    # musical input on the active route.
    prepared = {"user_prompt": "", "llm_prompt": ""}
    llm_prompt = ""

    scan_started = time.perf_counter()
    logging.info("Library scan start: %s", resolved_samples)
    catalog = scan_library(resolved_samples)
    save_catalog(catalog, CATALOG_FILE)
    logging.info(
        "Library: %s assets (%s audio) in %.2fs",
        catalog["total"],
        catalog.get("audio_total", 0),
        time.perf_counter() - scan_started,
    )

    if catalog.get("audio_total", 0) == 0:
        logging.info("No user audio in library — using PLG starter sound pack")

    # The LLM note-composer instruction is intentionally not constructed on
    # the active route.

    reference_hints = None
    director_brief = None

    beat_brief: dict[str, Any] | None = None

    from generation_variety import (
        anti_clone_retry_suffix,
        append_beat_history,
        attach_meta,
        build_context,
        is_too_similar,
        llm_addendum,
        prime_pattern_variety,
        set_active_context,
    )

    from style_corpus import style_text_for_matching

    # The selected MIDI lane is the style source.  Prompt text must not select
    # a different musical route behind the user's back.
    genre_hint: str | None = genre_override or "trap"
    genre_mood: list[str] = []
    if genre_override:
        genre_hint = genre_override
        logging.info("Genre override (user-confirmed): lane=%s", genre_hint)

    variety_ctx = build_context(
        prepared["user_prompt"],
        regenerate=regenerate,
        genre_hint=genre_hint,
        mood=genre_mood,
    )
    set_active_context(variety_ctx)
    try:
        llm_prompt = llm_prompt + "\n\n" + llm_addendum(variety_ctx)
        logging.info(
            "Generation variety: seed=%s key=%s bpm=%s regenerate=%s",
            variety_ctx.get("seed"),
            variety_ctx.get("key"),
            variety_ctx.get("bpm"),
            regenerate,
        )

        from beat_quality import (
            attach_quality_meta,
            evaluate,
            max_retries,
            min_score,
            retry_prompt_suffix,
            scorer_enabled,
        )
        from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

        retries = 0 if midi_first else max_retries()
        # The MIDI route used to get clone_rounds=1 AND retries=0, i.e. no
        # second chance at all: best_pattern is only assigned when a candidate
        # is NOT a hard fail, so one hard fail dropped straight through to the
        # RuntimeError below and the user got "press Generate, get an error".
        # Measured 2026-08-15 at ~1 in 45 generations on the shipping corpus.
        #
        # Raising clone_rounds (not `retries`) is what actually helps here:
        # the inner loop reuses this round's variety_ctx, so another attempt
        # would rebuild a near-identical candidate, while each OUTER round
        # builds a fresh context (bump=clone_attempt) that re-rolls the
        # per-run material. The hard-fail path reaches it — accepted stays
        # None, clone_blocked is False, best_pattern is None, so the round
        # simply ends and the next one starts. A first-round success still
        # breaks immediately, so normal generations are unaffected.
        clone_rounds = 3 if midi_first else 5
        best_pattern: dict[str, Any] | None = None
        best_score = -1.0
        accepted: dict[str, Any] | None = None
        last_attempt = 0
        # Why the last round gave up — the RuntimeError used to name the
        # kick/808 gate unconditionally, which is wrong for two of the three
        # paths that reach it (a full-kit audit failure, and the anti-clone
        # guard, which breaks before any audit is consulted).
        give_up_reasons: list[str] = []
        clone_block_rounds = 0

        for clone_attempt in range(clone_rounds):
            variety_ctx = build_context(
                prepared["user_prompt"],
                regenerate=regenerate or clone_attempt > 0,
                bump=clone_attempt,
                genre_hint=genre_hint,
                mood=genre_mood,
            )
            set_active_context(variety_ctx)
            if clone_attempt == 0:
                base_prompt = llm_prompt
            else:
                base_prompt = (
                    prepared["llm_prompt"]
                    + "\n\n"
                    + llm_addendum(variety_ctx)
                    + anti_clone_retry_suffix(0.55, clone_attempt)
                )

            clone_blocked = False
            for attempt in range(retries + 1):
                last_attempt = attempt
                llm_input = base_prompt if attempt == 0 else base_prompt + retry_prompt_suffix(attempt)
                if attempt > 0:
                    logging.info("Beat quality retry %s/%s", attempt, retries)

                if midi_first:
                    # Deliberately empty: MIDI ingest becomes the one owner of
                    # melody and drum notes.
                    candidate = {
                        "bpm": int(variety_ctx.get("bpm") or 140),
                        "style": genre_hint or "trap",
                        "tracks": {},
                        "plg_midi_first": True,
                    }
                else:
                    candidate = generate_pattern(llm_input, system_instruction)
                prime_pattern_variety(candidate, variety_ctx)
                candidate["sample_library"] = catalog["root"]
                candidate = _humanize_and_ingest(
                    candidate,
                    library_root=resolved_samples,
                    user_prompt=prepared["user_prompt"],
                    style=style_text_for_matching(candidate),
                    midi_path=midi_path,
                    reference_audio_path=reference_audio_path,
                    respect_uploaded_scale=respect_uploaded_scale,
                    midi_root=resolved_midi,
                )
                too_close, sim = is_too_similar(candidate)
                if too_close:
                    logging.warning(
                        "Beat clone guard: %.0f%% similar to recent history (round %s)",
                        sim * 100,
                        clone_attempt,
                    )
                    clone_blocked = True
                    clone_block_rounds += 1
                    give_up_reasons.append(
                        f"round {clone_attempt}: anti-clone guard "
                        f"({sim * 100:.0f}% similar to recent history)"
                    )
                    break

                report = evaluate(candidate)
                # A low score may still be the best usable musical variant;
                # a broken kick/808 contract is not usable at all. Do not let
                # the old "best available" escape hatch export it.
                if report.hard_fail:
                    give_up_reasons.append(
                        f"round {clone_attempt}: hard fail (score {report.score:.1f}) — "
                        + (_hard_fail_summary(report) or "no reason recorded")
                    )
                elif report.score > best_score:
                    best_score = report.score
                    best_pattern = candidate
                if report.passed or not scorer_enabled():
                    accepted = candidate
                    break
                if attempt < retries:
                    logging.warning(
                        "Beat below quality bar (score=%.1f < %.1f%s); regenerating…",
                        report.score,
                        min_score(),
                        ", hard fail" if report.hard_fail else "",
                    )

            if accepted is not None:
                break
            if clone_blocked:
                continue
            if best_pattern is not None and not is_too_similar(best_pattern)[0]:
                accepted = best_pattern
                break

        pattern = accepted or best_pattern
        if pattern is None:
            _log_give_up_diagnostics(give_up_reasons, clone_rounds, clone_block_rounds)
            detail = "; ".join(give_up_reasons) if give_up_reasons else "no reason recorded"
            raise RuntimeError(
                f"No generation succeeded after {clone_rounds} attempts; "
                f"nothing was exported. Reasons — {detail}"
            )
        if reference_hints:
            pattern["plg_reference_hints"] = reference_hints
        if director_brief:
            pattern["plg_director_brief"] = director_brief
        final_report = evaluate(pattern)
        attach_quality_meta(pattern, final_report, attempt=last_attempt, retries_used=last_attempt)
        if scorer_enabled():
            logging.info(
                "Beat quality: score=%.1f passed=%s (attempt %s)",
                final_report.score,
                final_report.passed,
                last_attempt,
            )

        ensure_starter_kit()
        attach_sounds_to_pattern(
            pattern, catalog, library_root=resolved_samples, prompt=prepared["user_prompt"]
        )
        attach_meta(pattern, variety_ctx)
        append_beat_history(pattern)

        # Per-genre finishing: section-aware FX plan + genre pump when the
        # architect (or single-stage path) provided none.
        try:
            from fx_layer import apply_fx_intent
            from style_corpus import attach_default_fx_intent, mix_recipe_for_pattern

            if attach_default_fx_intent(pattern):
                apply_fx_intent(pattern)
            meta = pattern.get("plg_producer_meta")
            has_curve = isinstance(meta, dict) and meta.get("sidechain_curve")
            from flp_writer import legacy_mix_enabled

            if not has_curve and legacy_mix_enabled():
                recipe = mix_recipe_for_pattern(pattern) or {}
                if recipe.get("sidechain") is not None:
                    from sidechain_curve import attach_sidechain_curve

                    depth = float(recipe["sidechain"])
                    from genre_profiles import PROFILES
                    from style_family import genre_of

                    profile = PROFILES.get(genre_of(pattern))
                    if getattr(profile, "sidechain_depth_bass_type_aware", False):
                        from beat_humanize import bass_type_aware_sidechain_depth
                        from pattern_utils import track_notes

                        depth = bass_type_aware_sidechain_depth(
                            track_notes(pattern, "sub_808"), depth,
                            pre_gate_kind=pattern.get("plg_bass_type"),
                        )
                    attach_sidechain_curve(pattern, depth=depth)
        except Exception as exc:  # noqa: BLE001 — finishing must never break generation
            logging.info("Genre finishing skipped (%s)", exc)

        _export_pattern_artifacts(pattern, export_midi=export_midi)
        save_pattern(pattern)
        return pattern
    finally:
        set_active_context(None)


def _archived_rebake_session(
    pattern: dict[str, Any],
    *,
    prompt: str = "",
    samples_dir: Path | None = None,
    export_midi: bool = True,
) -> dict[str, Any]:
    """Arrange, humanize, attach samples, export — no LLM (reference reverse / rebake)."""
    load_environment()
    resolved_samples = resolve_samples_dir(str(samples_dir) if samples_dir else None)
    ensure_samples_library(resolved_samples, quiet=True)
    ensure_midi_library(resolve_midi_dir(), quiet=True)

    catalog = scan_library(resolved_samples)
    save_catalog(catalog, CATALOG_FILE)
    pattern = dict(pattern)
    pattern["sample_library"] = catalog["root"]
    if prompt:
        pattern["user_prompt"] = prompt

    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from drum_defaults import ensure_drum_tracks
    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    ensure_drum_tracks(pattern)
    try:
        from melody_beat_symbiosis import ensure_melody_energy

        ensure_melody_energy(pattern)
    except Exception as exc:  # noqa: BLE001
        logging.info("Melody–beat energy card skipped (%s)", exc)
    arrange_song(pattern)
    pattern = humanize_pattern(pattern)

    from beat_quality import attach_quality_meta, evaluate, scorer_enabled

    if scorer_enabled():
        report = evaluate(pattern)
        attach_quality_meta(pattern, report, attempt=0, retries_used=0)

    ensure_starter_kit()
    attach_sounds_to_pattern(
        pattern,
        catalog,
        library_root=resolved_samples,
        prompt=str(pattern.get("user_prompt") or prompt or ""),
    )
    _export_pattern_artifacts(pattern, export_midi=export_midi)
    save_pattern(pattern)
    return pattern


def run_pipeline_from_pattern(
    pattern: dict[str, Any],
    prompt: str,
    samples_dir: Path | None = None,
    *,
    export_midi: bool = True,
    locale: str | None = None,
) -> dict[str, Any]:
    """Finalize a cloud-generated pattern locally (no LLM call)."""
    load_environment()
    resolved_samples = resolve_samples_dir(str(samples_dir) if samples_dir else None)
    ensure_samples_library(resolved_samples, quiet=True)
    resolved_midi = resolve_midi_dir()
    ensure_midi_library(resolved_midi, quiet=True)

    catalog = scan_library(resolved_samples)
    save_catalog(catalog, CATALOG_FILE)
    from prompt_locale import prepare_prompt_for_llm

    prepared = prepare_prompt_for_llm(prompt, locale or pattern.get("plg_locale"))
    pattern = dict(pattern)
    pattern.setdefault("plg_locale", prepared["plg_locale"])
    if prepared.get("plg_style_tags"):
        pattern.setdefault("plg_style_tags", prepared["plg_style_tags"])
    pattern["sample_library"] = catalog["root"]

    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    pattern = _humanize_and_ingest(
        pattern,
        library_root=resolved_samples,
        user_prompt=prepared["user_prompt"],
        style=str(pattern.get("style", "")),
        midi_root=resolved_midi,
    )

    from beat_quality import attach_quality_meta, evaluate, scorer_enabled

    if scorer_enabled():
        report = evaluate(pattern)
        attach_quality_meta(pattern, report, attempt=0, retries_used=0)
        logging.info("Beat quality: score=%.1f passed=%s", report.score, report.passed)

    ensure_starter_kit()
    attach_sounds_to_pattern(pattern, catalog, library_root=resolved_samples, prompt=prepared["user_prompt"])

    _export_pattern_artifacts(pattern, export_midi=export_midi)
    save_pattern(pattern)
    return pattern


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PLG Pluggin' backend core")
    parser.add_argument("-p", "--prompt", type=str, help="Music prompt")
    parser.add_argument(
        "-s",
        "--samples-dir",
        type=str,
        help="Folder with 808/hats/textures (a kit pack downloads, etc.)",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Only scan sample folder and write sample_catalog.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging()
    logging.info("Pluggin' backend core")

    try:
        load_environment()
        samples_dir = resolve_samples_dir(args.samples_dir)
        ensure_samples_library(samples_dir)
        ensure_midi_library(resolve_midi_dir())

        catalog = scan_library(samples_dir)
        save_catalog(catalog, CATALOG_FILE)
        logging.info("Library: %s assets -> %s", catalog["total"], CATALOG_FILE)

        if args.scan_only:
            return 0

        prompt = read_prompt(args.prompt)
        run_pipeline(prompt, samples_dir)
        logging.info("Done: %s", OUTPUT_FILE)
        logging.info("FL: Piano roll -> Scripts -> PLG Pluggin'")
        return 0

    except EnvironmentError as exc:
        logging.error("Config: %s", exc)
        return 1
    except ValueError as exc:
        logging.error("Input: %s", exc)
        return 1
    except RuntimeError as exc:
        logging.error("API: %s", exc)
        return 1
    except Exception as exc:
        logging.exception("Error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
