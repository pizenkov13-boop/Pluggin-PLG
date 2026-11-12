"""audio_features: atomic / batched cache writes and the additive character numbers.

The cache file is ~8 MB on a real library. It used to be rewritten in full,
non-atomically, on every miss; these tests lock the replacement down and prove
analyze() itself was not touched.
"""
from __future__ import annotations

import json
import threading
import wave
from pathlib import Path

import numpy as np
import pytest

import audio_features

SR = 44100


def _write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> Path:
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(data.tobytes())
    return path


def _sine(freq: float, seconds: float = 1.0, amp: float = 0.8) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


@pytest.fixture()
def cache_file(tmp_path, monkeypatch):
    target = tmp_path / "audio_features.json"
    monkeypatch.setattr(audio_features, "CACHE_FILE", target)
    monkeypatch.setattr(audio_features, "_cache", None)
    monkeypatch.setattr(audio_features, "_batch_depth", 0)
    monkeypatch.setattr(audio_features, "_dirty", False)
    return target


# --- atomic write ----------------------------------------------------------


def test_save_cache_is_valid_json_and_leaves_no_temp_file(cache_file, tmp_path):
    wav = _write_wav(tmp_path / "a.wav", _sine(100))
    assert audio_features.analyze_cached(wav) is not None
    assert json.loads(cache_file.read_text(encoding="utf-8"))
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_failed_replace_keeps_the_previous_cache_intact(cache_file, monkeypatch):
    cache_file.write_text('{"keep": 1}', encoding="utf-8")

    def refuse(*_a, **_k):
        raise PermissionError("held open by another process")

    monkeypatch.setattr(audio_features.os, "replace", refuse)
    monkeypatch.setattr(audio_features.time, "sleep", lambda _s: None)
    assert audio_features.atomic_write_text(cache_file, '{"new": 2}') is False
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"keep": 1}
    assert [p for p in cache_file.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_atomic_write_never_raises_on_an_unwritable_target(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert audio_features.atomic_write_text(blocker / "child.json", "{}") is False


# --- batching --------------------------------------------------------------


def _count_writes(monkeypatch):
    calls = []
    real = audio_features.atomic_write_text

    def counting(path, text):
        calls.append(path)
        return real(path, text)

    monkeypatch.setattr(audio_features, "atomic_write_text", counting)
    return calls


def _wavs(tmp_path, n):
    return [_write_wav(tmp_path / f"s{i}.wav", _sine(80 + 40 * i)) for i in range(n)]


def test_outside_a_batch_every_miss_still_persists_as_before(cache_file, tmp_path, monkeypatch):
    calls = _count_writes(monkeypatch)
    for wav in _wavs(tmp_path, 3):
        audio_features.analyze_cached(wav)
    assert len(calls) == 3


def test_batch_coalesces_many_misses_into_one_write(cache_file, tmp_path, monkeypatch):
    calls = _count_writes(monkeypatch)
    wavs = _wavs(tmp_path, 6)
    with audio_features.cache_batch():
        for wav in wavs:
            audio_features.analyze_cached(wav)
        assert calls == []
        # lookups inside the batch already see the new entries
        assert audio_features.analyze_cached(wavs[0]) is not None
    assert len(calls) == 1
    assert len(json.loads(cache_file.read_text(encoding="utf-8"))) == 6


def test_nested_batches_write_once_at_the_outermost_exit(cache_file, tmp_path, monkeypatch):
    calls = _count_writes(monkeypatch)
    a, b = _wavs(tmp_path, 2)
    with audio_features.cache_batch():
        with audio_features.cache_batch():
            audio_features.analyze_cached(a)
        assert calls == []
        audio_features.analyze_cached(b)
    assert len(calls) == 1


def test_batch_flushes_even_when_the_block_raises(cache_file, tmp_path, monkeypatch):
    calls = _count_writes(monkeypatch)
    wav = _wavs(tmp_path, 1)[0]
    with pytest.raises(RuntimeError):
        with audio_features.cache_batch():
            audio_features.analyze_cached(wav)
            raise RuntimeError("boom")
    assert len(calls) == 1


def test_an_empty_batch_writes_nothing(cache_file, monkeypatch):
    calls = _count_writes(monkeypatch)
    with audio_features.cache_batch():
        pass
    assert calls == []


def test_concurrent_misses_do_not_corrupt_or_lose_entries(cache_file, tmp_path):
    wavs = _wavs(tmp_path, 12)
    errors: list[BaseException] = []

    def work(chunk):
        try:
            for wav in chunk:
                audio_features.analyze_cached(wav)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(wavs[i::4],)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(json.loads(cache_file.read_text(encoding="utf-8"))) == 12


# --- analyze() is untouched -------------------------------------------------------


def test_analyze_keeps_exactly_its_original_fields(tmp_path):
    feats = audio_features.analyze(_write_wav(tmp_path / "k.wav", _sine(60)))
    assert set(feats) == {
        "duration_s", "rms", "centroid", "zcr", "attack", "decay", "pitch", "transient_body_ratio",
    }


def test_analyze_with_character_returns_the_same_base_features(tmp_path):
    wav = _write_wav(tmp_path / "k.wav", _sine(60))
    base, character = audio_features.analyze_with_character(wav)
    assert base == audio_features.analyze(wav)
    assert "centroid" not in character and "attack" not in character  # nothing re-derived


# --- character numbers --------------------------------------------------------------


def _character(samples):
    return audio_features._character_from_array(np.asarray(samples, dtype=float), SR)


def test_low_sine_is_all_sub_and_low_and_pure():
    c = _character(_sine(100))
    assert c["bands"]["sub"] + c["bands"]["low"] > 0.95
    assert c["flatness"] < 0.01
    assert c["rolloff85_hz"] < 200


def test_high_sine_lands_in_the_presence_band_and_rolls_off_high():
    c = _character(_sine(4000))
    assert c["bands"]["presence"] > 0.9
    assert c["rolloff85_hz"] > 3000


def test_noise_is_flat_and_bright_compared_with_a_tone():
    noise = np.random.RandomState(0).uniform(-0.5, 0.5, SR)
    n, tone = _character(noise), _character(_sine(100))
    assert n["flatness"] > 0.3 > tone["flatness"]
    assert n["bands"]["air"] > tone["bands"]["air"]


def test_band_shares_sum_to_one():
    c = _character(np.random.RandomState(1).uniform(-0.5, 0.5, SR))
    assert sum(c["bands"].values()) == pytest.approx(1.0, abs=0.01)


def test_decay_times_follow_an_exponential_envelope():
    tau = 0.050
    t = np.arange(int(SR * 1.5)) / SR
    c = _character(np.random.RandomState(2).uniform(-1, 1, t.size) * np.exp(-t / tau))
    # -20 dB at tau*ln(10), -40 dB at twice that
    assert c["t20_ms"] == pytest.approx(tau * np.log(10) * 1000, abs=20)
    assert c["t40_ms"] == pytest.approx(2 * tau * np.log(10) * 1000, abs=30)
    assert c["decay_truncated"] is False


def test_a_sustained_tone_reports_a_truncated_decay():
    assert _character(_sine(100, seconds=1.0))["decay_truncated"] is True


def test_crest_factor_separates_a_sine_from_a_square():
    square = np.sign(_sine(100)) * 0.9
    assert _character(_sine(100, amp=0.9))["crest_db"] == pytest.approx(3.0, abs=0.3)
    assert _character(square)["crest_db"] < 0.5


def test_clipping_is_counted():
    assert _character(np.clip(_sine(100, amp=3.0), -1, 1))["clip_frac"] > 0.3
    assert _character(_sine(100, amp=0.5))["clip_frac"] == 0.0


def test_silence_and_empty_input_yield_none():
    assert _character(np.zeros(SR)) is None
    assert _character(np.zeros(0)) is None
    assert audio_features._character_from_array(np.ones(10), 0) is None


def test_character_is_deterministic_and_json_safe():
    samples = np.random.RandomState(3).uniform(-0.5, 0.5, SR)
    first, second = _character(samples), _character(samples)
    assert first == second
    assert json.loads(json.dumps(first)) == first
