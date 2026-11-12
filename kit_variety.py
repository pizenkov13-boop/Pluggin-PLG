"""Kit Variety Guard — never reuse the same sample kit twice in a row."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from plg_paths import user_data_dir

logger = logging.getLogger("plg.kit_variety")

HISTORY_FILE = user_data_dir() / "kit_history.json"
MAX_HISTORY = 12


def recent_kit_sample_paths(*, recent: int = 5) -> set[str]:
    """All resolved sample paths used in the last N kits — hard block for variety."""
    history = _load_history()
    paths: set[str] = set()
    for entry in history[-max(1, recent) :]:
        if isinstance(entry, dict):
            paths.update(str(v) for v in entry.values() if v)
    return paths


def _load_history() -> list[dict[str, str]]:
    if not HISTORY_FILE.is_file():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        entries = data.get("kits", [])
        return entries if isinstance(entries, list) else []
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def last_kit_paths(*, recent: int = 3) -> set[str]:
    history = _load_history()
    if not history:
        return set()
    paths: set[str] = set()
    for entry in history[-max(1, recent) :]:
        if not isinstance(entry, dict):
            continue
        paths.update(str(v) for v in entry.values() if v)
    return paths


def save_kit_pick(picks: dict[str, Path]) -> None:
    """Persist kit variety opportunistically, never as an export dependency."""
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {track: str(path.resolve()) for track, path in picks.items()}
        history = _load_history()
        history.append(entry)
        history = history[-MAX_HISTORY:]
        HISTORY_FILE.write_text(json.dumps({"kits": history}, indent=2), encoding="utf-8")
        logger.info("Kit variety: saved kit (%s tracks)", len(entry))
    except OSError as exc:
        logger.warning("Kit history unavailable; continuing without kit memory (%s)", exc)


def apply_variety_penalty(
    score: int,
    path: Path,
    *,
    blocked: set[str],
    last_paths: set[str],
) -> int:
    resolved = str(path.resolve())
    if resolved in blocked:
        return -10_000
    if resolved in last_paths:
        return score - 200
    return score


def pick_with_variety(
    catalog: dict[str, Any],
    library_root: Path,
    *,
    prompt: str = "",
    style: str = "",
    variety_seed: int | None = None,
    blocked_names: set[str] | None = None,
    melody_energy: dict[str, Any] | None = None,
) -> dict[str, tuple[Any, int]]:
    """Wrapper around pick_full_kit that penalizes recent kits and bad ratings."""
    import random

    from sample_match import (
        KIT_TRACK_ORDER,
        pack_tokens,
        partner_kick_keywords,
        pick_best_for_track,
    )
    from sound_descriptors import descriptor_hints

    # Harder block — "same edgy kit every generation" was surviving a 5-deep window.
    last_paths = last_kit_paths(recent=10) | recent_kit_sample_paths(recent=10)
    used: set[str] = set()
    kit: dict[str, tuple[Any, int]] = {}
    kit_pack: tuple[str, ...] = ()
    variety_rng = random.Random(variety_seed if variety_seed is not None else time.time_ns())
    blocked_names = set(blocked_names or set())
    # Duplicate kit folders give the SAME sound under a different path — block
    # recent picks by filename too, or "variety" rotates between two copies of
    # one 808.
    blocked_names |= {Path(p).name for p in last_paths}

    from audio_features import target_from_prompt

    for track in KIT_TRACK_ORDER:
        # Timbre targeting: pull samples whose names match the *character* asked
        # for (dark / hard / glassy / vintage…), routed to the right channel.
        bonus: tuple[str, ...] = descriptor_hints(prompt, style, track=track)
        # V2 "hearing": match by the actual sound, even on garbage filenames.
        audio_target = target_from_prompt(prompt, style, track)
        # Kit cohesion: pull the rest of the drums toward the 808's pack so the
        # whole kit was designed together (not mismatched packs).
        if track in ("kick", "snare", "clap", "hi_hats") and kit_pack:
            bonus = tuple(dict.fromkeys(kit_pack + bonus))
        # Rule 16 — Perfect Pair: long fat 808 → short click kick (and vice versa).
        # Prefer 808_bass (hip-hop primary) when present, else sub_808.
        if track == "kick":
            partner_src = None
            if "808_bass" in kit:
                partner_src = Path(kit["808_bass"][0]).name
            elif "sub_808" in kit:
                partner_src = Path(kit["sub_808"][0]).name
            if partner_src:
                bonus = tuple(dict.fromkeys(partner_kick_keywords(partner_src) + bonus))
        # First pass: strict block on last kit
        path, score = pick_best_for_track(
            catalog,
            track,
            library_root,
            prompt=prompt,
            style=style,
            exclude=used | last_paths,
            bonus_keywords=bonus,
            audio_target=audio_target,
            variety_rng=variety_rng,
            melody_energy=melody_energy,
        )
        # Second pass: allow last kit only if nothing else fits
        if path is None:
            path, score = pick_best_for_track(
                catalog,
                track,
                library_root,
                prompt=prompt,
                style=style,
                exclude=used,
                bonus_keywords=bonus,
                audio_target=audio_target,
                variety_rng=variety_rng,
                melody_energy=melody_energy,
            )
            if path is not None and str(path.resolve()) in last_paths:
                score -= 40

        if path is not None and path.name in blocked_names:
            fallback_path, fallback_score = path, score
            used.add(str(path.resolve()))
            path, score = pick_best_for_track(
                catalog,
                track,
                library_root,
                prompt=prompt,
                style=style,
                exclude=used | last_paths,
                bonus_keywords=bonus,
                audio_target=audio_target,
                variety_rng=variety_rng,
                melody_energy=melody_energy,
            )
            if path is None:
                # Small pools (melodic one-shots) exhaust under the history
                # block — a repeated GOOD sound beats the bundled fallback.
                path, score = fallback_path, fallback_score
                used.discard(str(fallback_path.resolve()))

        if path is None:
            continue

        # Role split — the backbeat's second half. KIT_TRACK_ORDER picks the
        # snare BEFORE the clap, so the clap can be kept out of the snare's own
        # band and attack: two lanes holding one role sum into a single thick
        # slap instead of body-plus-click. The test is on the measured sound,
        # because a combo one-shot ("SnClap") is not detectable from its name.
        #
        # Deliberately a POST-check on the settled pick, not a filter inside the
        # scorer. Vetoing candidates up front changed the first pass's winner,
        # and the blocked-name retry below does not re-check its own result — so
        # an early veto moved the final clap on files where nothing collided at
        # all. Re-picking only on a real collision keeps every other beat
        # byte-identical.
        if track == "clap" and "snare" in kit:
            snare_path = Path(kit["snare"][0])
            try:
                import audio_features

                collides = audio_features.roles_collide(path, snare_path)
            except Exception:  # noqa: BLE001 — audio analysis is advisory
                collides = False
            if collides:
                alt, alt_score = pick_best_for_track(
                    catalog,
                    track,
                    library_root,
                    prompt=prompt,
                    style=style,
                    exclude=used | last_paths | {str(path.resolve())},
                    bonus_keywords=bonus,
                    audio_target=audio_target,
                    variety_rng=variety_rng,
                    melody_energy=melody_energy,
                    role_reference=snare_path,
                )
                if alt is not None:
                    logger.info(
                        "Role split: %s shared the snare's role, took %s instead",
                        path.name,
                        alt.name,
                    )
                    path, score = alt, alt_score

        kit[track] = (path, score)
        used.add(str(path.resolve()))
        if track == "sub_808":
            kit_pack = pack_tokens(Path(path).name)  # anchor the kit on the 808

    return kit
