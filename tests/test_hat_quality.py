"""Closed-hat duration + metallic gates — long/bright ticks = metal on asphalt."""

from __future__ import annotations

from pathlib import Path

from library_quality import audio_rejection_reason
from library_paths import audio_duration_seconds
from sample_match import pick_best_for_track, track_candidate_rejection_reason


def test_long_closed_hat_rejected_by_quality_gate():
    root = Path(r"c:/PLUG.FLP/PLG_Sounds_FL")
    long_hat = root / "hi_hats__XPOSED_Hat_#2_@producerb_pcm24.wav"
    if not long_hat.is_file():
        return
    assert audio_duration_seconds(long_hat) > 0.12
    assert audio_rejection_reason(long_hat, track="hi_hats") == "too_long_for_closed_hat_tick"
    assert track_candidate_rejection_reason(long_hat, "hi_hats", library_root=root)


def test_metallic_short_hat_rejected():
    root = Path(r"c:/PLUG.FLP/PLG_Sounds_FL")
    metallic = root / "hi_hats__DAREDEVIL_Hat_@producera_pcm24.wav"
    if not metallic.is_file():
        return
    reason = audio_rejection_reason(metallic, track="hi_hats")
    assert reason == "metallic_closed_hat_tick"


def test_pick_hi_hats_prefers_short_closed_tick():
    root = Path(r"c:/PLUG.FLP/PLG_Sounds_FL")
    files = [p.name for p in root.glob("hi_hats__*")]
    if len(files) < 5:
        return
    catalog = {"audio_total": len(files), "audio": {"hats": files}}
    path, _score = pick_best_for_track(catalog, "hi_hats", root, prompt="dark trap type beat")
    assert path is not None
    dur = audio_duration_seconds(path)
    assert dur is not None and dur <= 0.12
    assert "Op_Hat" not in path.name
    # Prefer darker closed ticks over tin (DAREDEVIL / INFERNO / MONEY_SPREAD).
    assert "DAREDEVIL" not in path.name
    assert "INFERNO" not in path.name
    assert "MONEY_SPREAD" not in path.name
