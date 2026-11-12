import os
import random
import wave
from pathlib import Path

import numpy as np

from sample_match import (
    family_pairing_keywords,
    partner_kick_keywords,
    pick_best_for_track,
    pick_full_kit,
    score_candidate,
    track_candidate_rejection_reason,
)


def _write_wav(path: Path, seconds: float, sr: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(sr * seconds)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)


def _write_tone_wav(path: Path, seconds: float, *, freq: float = 200.0, sr: int = 44100) -> None:
    """A real, analyzable one-shot (audible sine) — unlike ``_write_wav``'s
    silence, this passes audio_features.analyze() so a test can tell a
    genuinely verifiable candidate apart from an unreadable one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(sr * seconds)) / sr
    x = (0.6 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


def _write_808_wav(path: Path, *, transient_only: bool, sr: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(sr) / sr
    amp = np.where(t < 0.060, 0.95, 0.04 if transient_only else 0.55)
    x = (amp * np.sin(2 * np.pi * 70 * t) * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


def test_ancestor_folder_name_does_not_veto_a_good_sample(tmp_path: Path):
    """An 808 stays an 808 even when a parent folder is named after another lane.

    Real libraries nest kits ("Snare & Clap Pack/808s/…"), and a user home
    folder can contain any word at all — only the sample's own path inside the
    library may decide its identity.
    """
    rel = "Snare & Clap Pack/808/deep sub 808.wav"
    _write_808_wav(tmp_path / rel, transient_only=False)
    assert track_candidate_rejection_reason(tmp_path / rel, "sub_808", library_root=tmp_path) is None

    catalog = {"audio_total": 1, "audio": {"808": [rel]}}
    path, _score = pick_best_for_track(catalog, "sub_808", tmp_path, prompt="dark 808")
    assert path is not None and path.name == "deep sub 808.wav"


def test_variety_rng_picks_different_top_sounds(tmp_path: Path):
    (tmp_path / "melodies").mkdir()
    files = [f"melodies/dark bell {n}.wav" for n in ("one", "two", "three", "four")]
    for rel in files:
        (tmp_path / rel).write_bytes(b"x")
    catalog = {"audio_total": len(files), "audio": {"melodies": files}}

    # No RNG → deterministic (always the same best pick).
    deterministic = {
        pick_best_for_track(catalog, "melody_lead", tmp_path, prompt="dark bell")[0].name
        for _ in range(8)
    }
    assert len(deterministic) == 1

    # With RNG → varies across the top-scoring (all good) candidates.
    varied = set()
    for seed in range(24):
        path, _ = pick_best_for_track(
            catalog, "melody_lead", tmp_path, prompt="dark bell", variety_rng=random.Random(seed)
        )
        varied.add(path.name)
    assert len(varied) > 1


def test_partner_kick_long_808_wants_punchy():
    kws = partner_kick_keywords("Reese_long_distorted_808.wav")
    assert "click" in kws or "punch" in kws


def test_partner_kick_short_808_wants_fat():
    kws = partner_kick_keywords("short_punch_808_tight.wav")
    assert "fat" in kws or "boom" in kws


def test_family_pairing_piano_wants_orchestral():
    kws = family_pairing_keywords("Grand_Piano_C_Melody.wav")
    assert "strings" in kws and "brass" in kws


def test_family_pairing_rhodes_wants_orchestral():
    kws = family_pairing_keywords("Rhodes EP Chord.wav")
    assert "orchestral" in kws


def test_family_pairing_unknown_family_returns_nothing():
    assert family_pairing_keywords("808_Distorted_Sub.wav") == ()


def _score(name, track, prompt=""):
    return score_candidate(name, track, prompt_tokens=set(), style_tokens=set(), prompt_raw=prompt)


def test_drum_channels_reject_loops_and_leads():
    # The "uncle Petya" bugs: a lead loop on the kick, a song loop on the snare layer.
    assert _score("kits/(Bonus) BLAKK ROCKSTAR Lead (139 bpm).wav", "kick") < 0
    assert _score("kits/Qexotic - brand new (150 bpm).mp3", "snare_layer") < 0
    assert _score("melodies/dark bell loop 140 bpm.wav", "clap") < 0
    # Real one-shots still win.
    assert _score("kits/BLAKK ROCKSTAR P2 Kick.wav", "kick") > 0
    assert _score("kits/BLAKK ROCKSTAR P1 Clap.wav", "clap") > 0


def test_kick_rejects_dismantled_screamin_from_perc_and_vox_folder(tmp_path: Path):
    """A real production regression: the filename has no literal ``vocal``
    or ``perc`` token, but its parent folder proves it is not a kick."""
    bad_rel = "kits/Perc & Vox/DISMANTLED Screamin @producera.wav"
    good_rel = "kits/Kicks/TRAP JUMP Kick @producerb.wav"
    _write_wav(tmp_path / bad_rel, 0.35)
    _write_wav(tmp_path / good_rel, 0.35)
    catalog = {"audio_total": 2, "audio": {"kits": [bad_rel, good_rel]}}

    assert _score(bad_rel, "kick") <= -50_000
    selected, _score_value = pick_best_for_track(catalog, "kick", tmp_path)
    assert selected is not None
    assert selected.name == "TRAP JUMP Kick @producerb.wav"


def test_prompt_words_cannot_drag_808_onto_clap():
    # Prompt mentioning "808 distorted" must NOT pull an 808 sample onto the clap.
    score = _score("808/K-HOLE 808 Distorted.wav", "clap", prompt="dark rage distorted 808")
    assert score < 0
    # ...but the 808 sample is great for the sub_808 channel.
    assert _score("808/K-HOLE 808 Distorted.wav", "sub_808", prompt="distorted 808") > 0


def test_melody_loops_are_rejected():
    # PLG triggers the melody sample per MIDI note, so a loop just retriggers its
    # attack (stutter garbage). One-shots only on the melody channel.
    assert _score("melodies/dark arp 140 bpm.wav", "melody_lead") < 0
    assert _score("melodies/dark bell stab.wav", "melody_lead") > 0


def test_bonus_keywords_boost_score():
    base = score_candidate(
        "kits/kick_clean.wav", "kick", prompt_tokens=set(), style_tokens=set(), prompt_raw=""
    )
    boosted = score_candidate(
        "kits/kick_clean.wav",
        "kick",
        prompt_tokens=set(),
        style_tokens=set(),
        prompt_raw="",
        bonus_keywords=("clean",),
    )
    assert boosted > base


def test_chords_hard_rejects_808_and_bass_named_samples():
    # An 808/bass one-shot must never win the chords lane outright — it used
    # to be only a -45 soft penalty, so a bass sample could still beat a weak
    # library and get voiced as a "chord" (audibly wrong: bass, not a pad).
    assert _score("kits/ROOT OF ALL EVIL 808 Distorted @producerb.wav", "chords") <= -50_000
    assert _score("808/K-HOLE 808 Distorted.wav", "chords") <= -50_000
    assert _score("kits/deep_bass_sub.wav", "chords") <= -50_000


def test_chords_still_accepts_real_pad_and_piano_samples():
    assert _score("melodies/Grand_Piano_C_Chord.wav", "chords") > 0
    assert _score("melodies/warm_analog_pad.wav", "chords") > 0


def test_melody_lead_rejects_808_named_samples():
    score = score_candidate(
        "kits/K-HOLE 808 Distorted @producerb.wav",
        "melody_lead",
        prompt_tokens={"distorted", "aggressive"},
        style_tokens={"opium", "rage"},
        prompt_raw="hard opium rage distorted lead",
    )
    assert score < 0


def test_melody_lead_rejects_trap_jump_bell_and_mp3():
    assert _score("melodies/TRAP JUMP Bell @producerb.wav", "melody_lead") < 0
    assert _score("melodies/BFF 140 producerb producerb2.mp3", "melody_lead") < 0
    assert _score("melodies/dark synth pluck.wav", "melody_lead") > 0


def test_808_prefers_prompt_match_over_alphabetical(tmp_path):
    (tmp_path / "808").mkdir()
    (tmp_path / "kits").mkdir()
    files = {
        "808/AAA_generic.wav": b"",
        "808/KEN_CARSON_chaos_808.wav": b"",
        "808/ZZZ_dark_sub.wav": b"",
        "kits/random_snare.wav": b"",
    }
    for rel in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    catalog = {
        "audio_total": 4,
        "audio": {
            "808": [
                "808/AAA_generic.wav",
                "808/KEN_CARSON_chaos_808.wav",
                "808/ZZZ_dark_sub.wav",
            ],
            "kits": ["kits/random_snare.wav"],
        },
    }

    kit = pick_full_kit(catalog, tmp_path, prompt="ken carson chaos rage 808", style="opium trap")
    assert kit["sub_808"][0].name == "KEN_CARSON_chaos_808.wav"
    assert kit["sub_808"][1] > score_candidate(
        "808/AAA_generic.wav",
        "sub_808",
        prompt_tokens=set(),
        style_tokens=set(),
        prompt_raw="",
    )


def test_808_avoids_snare_in_kits_folder():
    rel_scores = {
        rel: score_candidate(rel, "sub_808", prompt_tokens={"chaos"}, style_tokens=set(), prompt_raw="chaos 808")
        for rel in ("kits/chaos_snare_hard.wav", "kits/chaos_808_sub.wav", "808/BLAKK_ROCKSTAR.wav")
    }
    assert rel_scores["kits/chaos_snare_hard.wav"] < rel_scores["808/BLAKK_ROCKSTAR.wav"]


def test_full_kit_no_duplicate_files(tmp_path):
  for folder in ("808", "kits", "hats", "melodies"):
      (tmp_path / folder).mkdir()
  samples = {
      "808/low_808.wav": b"",
      "kits/kick_hard.wav": b"",
      "kits/snare_crack.wav": b"",
      "kits/clap_snap.wav": b"",
      "hats/closed_hat_tick.wav": b"",
      "hats/open_rage.wav": b"",
      "melodies/dark_bell.wav": b"",
  }
  for rel in samples:
      path = tmp_path / rel
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_bytes(b"")

  catalog = {
      "audio_total": 7,
      "audio": {
          "808": ["808/low_808.wav"],
          "kits": ["kits/kick_hard.wav", "kits/snare_crack.wav", "kits/clap_snap.wav"],
          "hats": ["hats/closed_hat_tick.wav", "hats/open_rage.wav"],
          "melodies": ["melodies/dark_bell.wav"],
      },
  }
  kit = pick_full_kit(catalog, tmp_path, prompt="rage kick snare clap 808 hat melody")
  paths = [str(path.resolve()) for path, _ in kit.values()]
  assert len(paths) == len(set(paths))
  assert kit["kick"][0].name == "kick_hard.wav"
  assert kit["snare"][0].name == "snare_crack.wav"
  assert kit["sub_808"][0].name == "low_808.wav"
  assert kit["hi_hats"][0].name == "closed_hat_tick.wav"



# --- Layer 2 of the two-layer one-shot fix (audit 2026-07) ----------------
# Hard duration backstop for the whole one-shot drum family, regardless of
# which catalog bucket a candidate landed in — a mislabeled/miscategorized
# loop must never win a drum lane. This is the definitive fix: even a file
# whose NAME happens to carry a strong keyword (extreme edge case) still
# gets rejected on length alone if it's not actually a one-shot.


def test_long_loop_never_wins_the_kick_lane_even_with_zero_keyword_evidence(tmp_path: Path):
    _write_wav(tmp_path / "kits" / "A-Energy 159 PRODUCERB PRODUCERB2_3.wav", 12.0)
    _write_wav(tmp_path / "kits" / "kick_hard.wav", 0.35)
    catalog = {
        "audio_total": 2,
        "audio": {"kits": ["kits/A-Energy 159 PRODUCERB PRODUCERB2_3.wav", "kits/kick_hard.wav"]},
    }
    for seed in range(20):
        path, _ = pick_best_for_track(
            catalog, "kick", tmp_path, prompt="", variety_rng=random.Random(seed)
        )
        assert path is not None
        assert path.name == "kick_hard.wav"


def test_long_file_rejected_for_kick_even_if_its_name_says_kick(tmp_path: Path):
    """Duration wins over a keyword match — a mislabeled long file with
    "kick" in its own name must still not win a one-shot drum lane."""
    _write_wav(tmp_path / "kits" / "kick_pack_full_demo.wav", 15.0)
    _write_wav(tmp_path / "kits" / "kick_real.wav", 0.4)
    catalog = {
        "audio_total": 2,
        "audio": {"kits": ["kits/kick_pack_full_demo.wav", "kits/kick_real.wav"]},
    }
    path, _ = pick_best_for_track(catalog, "kick", tmp_path, prompt="")
    assert path is not None
    assert path.name == "kick_real.wav"


def test_no_valid_short_candidate_returns_none_rather_than_a_loop(tmp_path: Path):
    _write_wav(tmp_path / "kits" / "only_a_loop.wav", 20.0)
    catalog = {"audio_total": 1, "audio": {"kits": ["kits/only_a_loop.wav"]}}
    path, score = pick_best_for_track(catalog, "kick", tmp_path, prompt="")
    assert path is None


def test_duration_gate_does_not_apply_to_non_drum_tracks(tmp_path: Path):
    # melody_lead is not in the one-shot drum family -- a long .wav there is
    # still subject to its OWN existing guards, just not this duration gate.
    _write_wav(tmp_path / "melodies" / "long_dark_bell.wav", 12.0)
    catalog = {"audio_total": 1, "audio": {"melodies": ["melodies/long_dark_bell.wav"]}}
    path, score = pick_best_for_track(catalog, "melody_lead", tmp_path, prompt="dark bell")
    assert path is not None
    assert path.name == "long_dark_bell.wav"


# --- 808_bass profile (Part B, 2026-07) ---------------------------------------


def test_808_bass_profile_scores_808_named_files_over_unrelated_ones():
    assert _score("808/BLAKK_ROCKSTAR_808.wav", "808_bass") > _score("hats/random_hat.wav", "808_bass")


def test_hi_hats_hard_rejects_open_hat_and_cymbal_names():
    """Open hats / rides as the main 1/8 sample = metallic sheet on asphalt."""
    assert _score("hats/BLAKK_ROCKSTAR_P2_Op_Hat.wav", "hi_hats") <= -50_000
    assert _score("hats/open_rage.wav", "hi_hats") <= -50_000
    assert _score("hats/crash_cymbal.wav", "hi_hats") <= -50_000
    assert _score("hats/LIVELEAK_P1_Hat.wav", "hi_hats") > _score(
        "hats/LIVELEAK_P2_Op_Hat.wav", "hi_hats"
    )


def test_808_bass_hard_rejects_kick_and_snare_named_files():
    """808_bass is in _ONE_SHOT_DRUM_TRACKS: a file with none of its own
    strong keywords but a foreign drum keyword must be hard-rejected, same
    protection sub_808/kick already have against each other."""
    assert _score("kits/plain_kick.wav", "808_bass") <= -50_000
    assert _score("kits/plain_snare.wav", "808_bass") <= -50_000


def test_808_bass_rejects_long_loops(tmp_path: Path):
    _write_wav(tmp_path / "808" / "long_808_loop.wav", 20.0)
    catalog = {"audio_total": 1, "audio": {"808": ["808/long_808_loop.wav"]}}
    path, _score_val = pick_best_for_track(catalog, "808_bass", tmp_path, prompt="808")
    assert path is None


def test_808_bass_rejects_transient_only_sample_and_keeps_real_808(tmp_path: Path):
    """A short transient labelled 808 must not become a note-gated 808 Bass
    lane, where it would only ever sound like a click."""
    transient = "808/808_click.wav"
    bass = "808/808_full_body.wav"
    _write_wav(tmp_path / transient, 0.12)
    _write_wav(tmp_path / bass, 0.8)
    catalog = {"audio_total": 2, "audio": {"808": [transient, bass]}}

    selected, _score_value = pick_best_for_track(catalog, "808_bass", tmp_path, prompt="808")
    assert selected is not None
    assert selected.name == "808_full_body.wav"


def test_808_bass_rejects_a_clicky_808_with_no_sustained_body(tmp_path: Path):
    clicky = "808/TRAP JUMP 808.wav"
    clean = "808/clean 808.wav"
    _write_808_wav(tmp_path / clicky, transient_only=True)
    _write_808_wav(tmp_path / clean, transient_only=False)
    catalog = {"audio_total": 2, "audio": {"808": [clicky, clean]}}

    selected, _score_value = pick_best_for_track(catalog, "808_bass", tmp_path, prompt="808")
    assert selected is not None
    assert selected.name == "clean 808.wav"


def test_808_bass_and_sub_808_can_pick_different_files_from_the_same_pool(tmp_path: Path):
    (tmp_path / "808").mkdir()
    for name in ("808/first_808.wav", "808/second_808.wav"):
        (tmp_path / name).write_bytes(b"x")
    catalog = {"audio_total": 2, "audio": {"808": ["808/first_808.wav", "808/second_808.wav"]}}
    sub_path, _ = pick_best_for_track(catalog, "sub_808", tmp_path, prompt="808")
    assert sub_path is not None
    bass_path, _ = pick_best_for_track(
        catalog, "808_bass", tmp_path, prompt="808", exclude={str(sub_path.resolve())}
    )
    assert bass_path is not None
    assert bass_path != sub_path


# --- Unverified-audio penalty (audit 2026-09) ------------------------------
# audio_duration_seconds()/audio_features.analyze() both fail open (return
# None) on an unreadable file, by design — but a candidate that literally
# cannot be analyzed must not silently win a one-shot drum lane over a real
# one just because its name says "kick".


def test_unreadable_but_correctly_named_kick_loses_to_a_real_kick(tmp_path: Path):
    corrupt = tmp_path / "kits" / "kick_corrupt.wav"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(os.urandom(400))
    _write_tone_wav(tmp_path / "kits" / "kick_real.wav", 0.3)
    catalog = {
        "audio_total": 2,
        "audio": {"kits": ["kits/kick_corrupt.wav", "kits/kick_real.wav"]},
    }
    for seed in range(20):
        path, _ = pick_best_for_track(
            catalog, "kick", tmp_path, prompt="", variety_rng=random.Random(seed)
        )
        assert path is not None
        assert path.name == "kick_real.wav"


def test_unreadable_kick_still_wins_as_last_resort_when_alone(tmp_path: Path):
    """Fail-open is preserved: an unreadable file with no competitor is still
    picked rather than leaving the lane empty."""
    corrupt = tmp_path / "kits" / "kick_corrupt.wav"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(os.urandom(400))
    catalog = {"audio_total": 1, "audio": {"kits": ["kits/kick_corrupt.wav"]}}

    path, score = pick_best_for_track(catalog, "kick", tmp_path, prompt="")
    assert path is not None
    assert path.name == "kick_corrupt.wav"
    assert score >= 5


def test_unreadable_never_beats_a_verified_candidate_even_with_worst_case_jitter(tmp_path: Path):
    """A flat score penalty could still lose to the ±8 variety jitter and win
    anyway; the tiered fallback makes this exact instead of probabilistic —
    run enough seeds to make a jitter-driven flip near-certain if one exists."""
    corrupt = tmp_path / "kits" / "clap_corrupt.wav"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(os.urandom(400))
    _write_tone_wav(tmp_path / "kits" / "clap_real.wav", 0.2)
    catalog = {
        "audio_total": 2,
        "audio": {"kits": ["kits/clap_corrupt.wav", "kits/clap_real.wav"]},
    }
    for seed in range(50):
        path, _ = pick_best_for_track(
            catalog, "clap", tmp_path, prompt="", variety_rng=random.Random(seed)
        )
        assert path is not None
        assert path.name == "clap_real.wav"


def test_unreadable_files_are_still_ranked_among_themselves_as_last_resort(tmp_path: Path):
    """When every candidate is unreadable, the fallback pool still prefers
    the better name/folder match instead of picking arbitrarily."""
    strong = tmp_path / "kits" / "clap_hard.wav"
    weak = tmp_path / "kits" / "zzz999.wav"
    for f in (strong, weak):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(os.urandom(400))
    catalog = {
        "audio_total": 2,
        "audio": {"kits": ["kits/clap_hard.wav", "kits/zzz999.wav"]},
    }
    path, score = pick_best_for_track(catalog, "clap", tmp_path, prompt="")
    assert path is not None
    assert path.name == "clap_hard.wav"
    assert score >= 5
