"""FROZEN copy of sample_match.pick_best_for_track as of commit a3b880c (before
the Phase 3 extraction of its scoring loop).

It exists only so tests/test_pick_best_for_track_golden.py can prove, on many
seeds, that the refactored function returns the same file AND consumes the same
random numbers. Delete it once Phase 3 has been accepted. Do not import it from
production code and do not "fix" it: its whole value is that it never changes.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

from library_paths import audio_duration_seconds
from sample_match import (
    TRACK_PROFILES,
    _KIT_SYMBIOSIS_TRACKS,
    _ONE_SHOT_DRUM_TRACKS,
    _tokens,
    iter_track_candidates_grouped,
    score_candidate,
    track_candidate_rejection_reason,
)


def legacy_pick_best_for_track(
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
