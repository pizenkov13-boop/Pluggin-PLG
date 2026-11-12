from pathlib import Path

from sample_chop_engine import _wants_chop_by_text, build_chop_arrangement, should_chop_source


def test_should_chop_on_keyword(monkeypatch, tmp_path: Path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr("sample_chop_engine._ffmpeg_available", lambda: True)

    class _FakeAudio:
        def __len__(self) -> int:
            return 2000

    monkeypatch.setattr("sample_chop_engine._load_audio", lambda _path: _FakeAudio())
    assert should_chop_source(wav, prompt="vintage japanese pop sample flip")


def test_should_chop_skips_without_ffmpeg(monkeypatch):
    monkeypatch.setattr("sample_chop_engine._ffmpeg_available", lambda: False)
    assert not should_chop_source(Path("x.wav"), prompt="vintage japanese pop sample flip")


def test_chop_arrangement_scatters(tmp_path: Path):
    paths = [tmp_path / f"c{i}.wav" for i in range(4)]
    for p in paths:
        p.write_bytes(b"RIFF")
    arr = build_chop_arrangement(paths, bpm=140, bars=4)
    assert len(arr) >= 2
    assert all(a.get("chop") for a in arr)


# --- audit 2026-07: "pop" substring false-triggered on "hyperpop" ----------
# One of PLG's 10 core detailed genres — every hyperpop generation was
# silently getting its melody chopped/pitch/tempo-shifted. Whole-word match
# only now.


def test_hyperpop_no_longer_false_triggers_the_chop_keyword():
    assert _wants_chop_by_text("hyperpop beat, glitchy digital textures") is False


def test_standalone_pop_still_triggers():
    assert _wants_chop_by_text("upbeat pop song sample") is True


def test_populated_does_not_false_trigger():
    assert _wants_chop_by_text("a populated street sample flip") is True  # "sample"/"flip" still trigger
    assert _wants_chop_by_text("a populated street scene") is False  # but "pop" inside "populated" must not


def test_other_keywords_still_match_as_whole_words():
    assert _wants_chop_by_text("old vintage chop") is True
    assert _wants_chop_by_text("dark trap beat 150 bpm") is False


def test_cyrillic_keywords_still_match():
    assert _wants_chop_by_text("старый винтажный сэмпл") is True
    assert _wants_chop_by_text("агрессивный тёмный трэп") is False


# --- MIDI sustain affinity skips length-based auto-chop (2026-07) ----------


def _sustain_lean_pattern() -> dict:
    return {
        "bpm": 90,
        "style": "trap",
        "tracks": {
            "melody_lead": [
                {"time_step": i * 4.0, "note": "C4", "length": 4.0, "velocity": 100}
                for i in range(4)
            ]
        },
    }


def test_sustain_affinity_blocks_length_based_auto_chop(monkeypatch, tmp_path: Path):
    wav = tmp_path / "long_pad.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr("sample_chop_engine._ffmpeg_available", lambda: True)

    class _FakeAudio:
        def __len__(self) -> int:
            return 8000  # well above MIN_CHOP_SOURCE_MS

    monkeypatch.setattr("sample_chop_engine._load_audio", lambda _path: _FakeAudio())
    assert should_chop_source(wav, prompt="", pattern=_sustain_lean_pattern()) is False


def test_chop_keyword_still_wins_over_sustain_affinity(monkeypatch, tmp_path: Path):
    wav = tmp_path / "long_pad.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr("sample_chop_engine._ffmpeg_available", lambda: True)

    class _FakeAudio:
        def __len__(self) -> int:
            return 8000

    monkeypatch.setattr("sample_chop_engine._load_audio", lambda _path: _FakeAudio())
    assert should_chop_source(
        wav, prompt="vintage chop sample flip", pattern=_sustain_lean_pattern()
    ) is True


def test_long_file_still_auto_chops_without_sustain_pattern(monkeypatch, tmp_path: Path):
    wav = tmp_path / "long_loop.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr("sample_chop_engine._ffmpeg_available", lambda: True)

    class _FakeAudio:
        def __len__(self) -> int:
            return 8000

    monkeypatch.setattr("sample_chop_engine._load_audio", lambda _path: _FakeAudio())
    assert should_chop_source(wav, prompt="") is True
