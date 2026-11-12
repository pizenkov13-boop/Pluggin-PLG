import wave
from pathlib import Path

import numpy as np
import pytest

from audio_features import (
    analyze,
    feature_match_score,
    gap_fill_target,
    rms_dbfs,
    roles_collide,
    target_from_prompt,
)
from sample_match import pick_best_for_track


def _sine_wav(path: Path, freq: float, ms: int = 500, sr: int = 44100, amp: float = 0.5) -> None:
    t = np.arange(int(sr * ms / 1000)) / sr
    x = (amp * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


def test_analyze_brightness_orders_correctly(tmp_path: Path):
    bright = tmp_path / "bright.wav"
    dark = tmp_path / "dark.wav"
    _sine_wav(bright, 6000)
    _sine_wav(dark, 150)
    fb = analyze(bright)
    fd = analyze(dark)
    assert fb and fd
    assert fb["centroid"] > fd["centroid"]


def test_analyze_reports_a_large_transient_without_808_body(tmp_path: Path):
    path = tmp_path / "transient.wav"
    sr = 44100
    t = np.arange(sr) / sr
    # 60 ms loud click followed by a nearly silent sine tail.
    amp = np.where(t < 0.060, 0.95, 0.04)
    x = (amp * np.sin(2 * np.pi * 70 * t) * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())

    features = analyze(path)
    assert features is not None
    assert features["transient_body_ratio"] > 3.0


def test_analyze_unreadable_returns_none(tmp_path: Path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav")
    assert analyze(bad) is None


def test_rms_dbfs_orders_loud_above_quiet(tmp_path: Path):
    loud = tmp_path / "loud.wav"
    quiet = tmp_path / "quiet.wav"
    _sine_wav(loud, 440, ms=3000, amp=0.9)
    _sine_wav(quiet, 440, ms=3000, amp=0.05)
    loud_db = rms_dbfs(loud)
    quiet_db = rms_dbfs(quiet)
    assert loud_db is not None and quiet_db is not None
    assert loud_db > quiet_db
    # sine RMS = amplitude/sqrt(2); sanity-check against the closed form.
    import math

    assert loud_db == pytest.approx(20 * math.log10(0.9 / math.sqrt(2)), abs=0.1)


def test_rms_dbfs_unreadable_returns_none(tmp_path: Path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav")
    assert rms_dbfs(bad) is None


def test_target_from_prompt_directions():
    assert target_from_prompt("dark deep 808", "", "sub_808").get("centroid") == "low"
    assert target_from_prompt("glassy bright bell", "", "melody_lead").get("centroid") == "high"


def test_feature_match_rewards_and_penalizes():
    bright = {"centroid": 6000.0}
    dark = {"centroid": 200.0}
    assert feature_match_score(bright, {"centroid": "high"}) > 0
    assert feature_match_score(dark, {"centroid": "high"}) < 0


def test_gap_fill_target_bright_reference_wants_low(tmp_path: Path):
    bright = tmp_path / "bright_ref.wav"
    _sine_wav(bright, 6000)
    assert gap_fill_target([bright]) == {"centroid": "low"}


def test_gap_fill_target_dark_reference_wants_high(tmp_path: Path):
    dark = tmp_path / "dark_ref.wav"
    _sine_wav(dark, 150)
    assert gap_fill_target([dark]) == {"centroid": "high"}


def test_gap_fill_target_unreadable_reference_is_no_op(tmp_path: Path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav")
    assert gap_fill_target([bad]) == {}


def test_gap_fill_target_averages_multiple_references(tmp_path: Path):
    # Two bright references average well above the "high" threshold.
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _sine_wav(a, 6000)
    _sine_wav(b, 7000)
    assert gap_fill_target([a, b]) == {"centroid": "low"}


def test_pick_hears_vibe_on_neutral_names(tmp_path: Path):
    (tmp_path / "melodies").mkdir()
    # aaa = dark (first in order), bbb = bright. Names carry zero vibe info.
    _sine_wav(tmp_path / "melodies" / "aaa.wav", 150)
    _sine_wav(tmp_path / "melodies" / "bbb.wav", 6000)
    catalog = {"audio": {"melodies": ["melodies/aaa.wav", "melodies/bbb.wav"]}}

    target = target_from_prompt("glassy bright bell", "", "melody_lead")
    path, _score = pick_best_for_track(
        catalog, "melody_lead", tmp_path, prompt="glassy bright bell", audio_target=target
    )
    # Name scores are equal and aaa comes first; only "hearing" flips it to bbb.
    assert path is not None and path.name == "bbb.wav"


def _hit_wav(path: Path, freq: float, attack_ms: float, ms: int = 150,
             sr: int = 44100, amp: float = 0.8) -> None:
    """A percussive one-shot with a controllable band and attack time."""
    n = int(sr * ms / 1000)
    t = np.arange(n) / sr
    rise = max(1, int(sr * attack_ms / 1000))
    env = np.concatenate([
        np.linspace(0.0, 1.0, rise),
        np.exp(-np.linspace(0.0, 6.0, max(1, n - rise))),
    ])[:n]
    x = (amp * env * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


def test_roles_collide_flags_two_lanes_holding_one_role(tmp_path: Path):
    # Same band, same attack — this is what sums into a single thick slap.
    snare = tmp_path / "snare.wav"
    clap = tmp_path / "clap.wav"
    _hit_wav(snare, 5000, attack_ms=1.0)
    _hit_wav(clap, 5050, attack_ms=1.5)
    assert roles_collide(clap, snare) is True


def test_roles_collide_allows_a_spread_clap_beside_a_snare(tmp_path: Path):
    # The BatonRouge case: the two share a band, but the clap opens over tens
    # of milliseconds instead of instantly, so the roles stay separate.
    snare = tmp_path / "snare.wav"
    clap = tmp_path / "clap.wav"
    _hit_wav(snare, 5000, attack_ms=1.0)
    _hit_wav(clap, 5050, attack_ms=35.0)
    assert roles_collide(clap, snare) is False


def test_roles_collide_allows_a_different_band(tmp_path: Path):
    snare = tmp_path / "snare.wav"
    clap = tmp_path / "clap.wav"
    _hit_wav(snare, 2000, attack_ms=1.0)
    _hit_wav(clap, 6000, attack_ms=1.0)
    assert roles_collide(clap, snare) is False


def test_roles_collide_is_silent_on_unreadable_audio(tmp_path: Path):
    good = tmp_path / "good.wav"
    bad = tmp_path / "bad.wav"
    _hit_wav(good, 5000, attack_ms=1.0)
    bad.write_bytes(b"not a wav")
    assert roles_collide(bad, good) is False


def _clap_catalog(tmp_path: Path, entries: dict[str, tuple[float, float]]) -> dict:
    (tmp_path / "kits").mkdir(exist_ok=True)
    for name, (freq, attack) in entries.items():
        _hit_wav(tmp_path / "kits" / name, freq, attack_ms=attack)
    return {"audio": {"kits": [f"kits/{n}" for n in entries]}}


def test_role_reference_keeps_a_colliding_clap_out_of_the_lane(tmp_path: Path):
    snare = tmp_path / "the snare.wav"
    _hit_wav(snare, 5000, attack_ms=1.0)
    # Equal names score alike, so the stable sort would take AAA without the
    # veto; AAA is the one that collapses into the snare's role.
    catalog = _clap_catalog(tmp_path, {
        "AAA Clap.wav": (5040, 1.2),
        "BBB Clap.wav": (5040, 40.0),
    })

    plain, _ = pick_best_for_track(catalog, "clap", tmp_path)
    assert plain is not None and plain.name == "AAA Clap.wav"

    split, _ = pick_best_for_track(catalog, "clap", tmp_path, role_reference=snare)
    assert split is not None and split.name == "BBB Clap.wav"


def test_role_reference_still_fills_the_lane_when_the_whole_pool_collides(tmp_path: Path):
    snare = tmp_path / "the snare.wav"
    _hit_wav(snare, 5000, attack_ms=1.0)
    catalog = _clap_catalog(tmp_path, {
        "AAA Clap.wav": (5040, 1.2),
        "BBB Clap.wav": (5010, 2.0),
    })

    picked, _ = pick_best_for_track(catalog, "clap", tmp_path, role_reference=snare)
    assert picked is not None, "the lane must never be left empty by the veto"
    assert picked.name == "AAA Clap.wav"
