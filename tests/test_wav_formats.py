"""Compressed WAV formats are decoded properly or refused, never read as noise.

A-law, mu-law, IMA / MS ADPCM and Ogg Vorbis stored in a WAV (format tag 0x674F) used to be read as if
they were PCM, which turned them into noise in the preview, the analysis and the exported project
(a real library file, ``200 KASH Snare #2``, was one of them). ``wav_decode`` now decodes them through
libsndfile; a WAV it cannot decode at all (``wav_decode.decodable``) is refused by the shared gate, by a
hand pick and by the preview.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import pinned_kit
import sample_match
import sound_preview
import wav_compat
import wav_decode

RATE = 44100


def _tone(seconds: float = 0.3) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return 0.6 * np.sin(2 * np.pi * 440 * t) * np.exp(-t / 0.15)


def _compressed(path: Path, subtype: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), _tone(), RATE, subtype=subtype, format="WAV")
    return path


def _ogg_in_wav(path: Path, payload: bytes | None = None) -> Path:
    if payload is None:
        buffer = io.BytesIO()
        sf.write(buffer, _tone(), RATE, format="OGG", subtype="VORBIS")
        payload = buffer.getvalue()
    fmt = struct.pack("<HHIIHHH", 0x674F, 1, RATE, 5512, 1, 16, 0) + bytes(6)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(payload)) + payload
    if len(payload) % 2:
        body += b"\x00"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def _tagged(path: Path, tag: int) -> Path:
    data = bytes(range(256)) * 8
    fmt = struct.pack("<HHIIHH", tag, 1, RATE, RATE * 2, 2, 16)
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def _similar(decoded: np.ndarray, reference: np.ndarray) -> float:
    n = min(len(decoded), len(reference))
    return float(np.corrcoef(decoded[:n], reference[:n])[0, 1])


FORMATS = [("ULAW", wav_decode._MULAW), ("ALAW", wav_decode._ALAW), ("IMA_ADPCM", wav_decode._IMA_ADPCM), ("MS_ADPCM", wav_decode._MS_ADPCM)]


@pytest.mark.parametrize("subtype, tag", FORMATS)
def test_a_compressed_wav_is_decoded_to_the_sound_it_holds(tmp_path, subtype, tag):
    path = _compressed(tmp_path / f"{subtype}.wav", subtype)

    assert wav_decode.format_tag(path) == tag and wav_decode.decodable(path)
    mono, rate, seconds = wav_decode.read_wav_mono(path)
    assert rate == RATE and 0.29 < seconds < 0.4  # ADPCM pads the last block
    assert _similar(mono, _tone()) > 0.95  # the old PCM reading of these bytes correlates with nothing
    assert abs(wav_decode.duration_seconds(path) - seconds) < 1e-9


@pytest.mark.parametrize("subtype, tag", FORMATS)
def test_the_export_copy_of_a_compressed_wav_is_the_same_sound_in_pcm24(tmp_path, subtype, tag):
    path = _compressed(tmp_path / f"{subtype}.wav", subtype)
    out = tmp_path / "copy.wav"

    assert wav_compat.needs_fl_conversion(path) is True
    assert wav_compat.convert_to_pcm24(path, out) is True

    assert wav_compat.inspect_wav(out)[0] == 1 and wav_compat.inspect_wav(out)[3] == 24
    assert _similar(wav_decode.read_wav_mono(out)[0], _tone()) > 0.95


def test_ogg_vorbis_inside_a_wav_is_decoded_not_played_as_noise(tmp_path):
    path = _ogg_in_wav(tmp_path / "ogg.wav")
    out = tmp_path / "copy.wav"

    assert wav_decode.format_tag(path) == 0x674F and wav_decode.decodable(path)
    assert _similar(wav_decode.read_wav_mono(path)[0], _tone()) > 0.9
    assert wav_compat.convert_to_pcm24(path, out) is True
    assert _similar(wav_decode.read_wav_mono(out)[0], _tone()) > 0.9


def test_reading_such_a_tag_as_pcm_is_no_longer_possible():
    with pytest.raises(ValueError):
        wav_compat._decode_to_float(wav_decode._MULAW, 8, b"\x00" * 100)


# --- what cannot be decoded is refused everywhere ---------------------------------------------


@pytest.fixture
def undecodable(tmp_path):
    """Three WAVs PLG cannot read: a stream that is not Ogg, an MP3-in-WAV (tag 0x55), a made-up tag."""
    root = tmp_path / "PLG_Sounds"
    return root, [
        _ogg_in_wav(root / "kits" / "bad_ogg.wav", payload=b"this is not an ogg stream" * 40),
        _tagged(root / "kits" / "mp3_in_wav.wav", 0x55),
        _tagged(root / "kits" / "made_up.wav", 0x1234),
    ]


def test_a_wav_that_cannot_be_decoded_is_not_decodable_and_no_lane_accepts_it(undecodable):
    _root, files = undecodable
    for path in files:
        assert wav_decode.decodable(path) is False
        for lane in ("kick", "snare", "hi_hats", "808_bass"):
            assert sample_match.track_candidate_rejection_reasons(path, lane) == ["undecodable_wav"], (path.name, lane)
        assert wav_compat.convert_to_pcm24(path, path.with_name("x.wav")) is False


def test_a_hand_pick_of_such_a_file_is_refused(undecodable, tmp_path, monkeypatch):
    root, files = undecodable
    monkeypatch.setattr(pinned_kit, "pin_file", lambda: tmp_path / "state" / "pinned_kit.json")
    for path in files:
        assert pinned_kit.pin_by_hand("kick", path, root) == "unsupported_format"
    assert pinned_kit.load_pins() == {}


def test_the_preview_refuses_it_and_the_list_marks_it_unusable(undecodable, tmp_path, monkeypatch):
    import json

    import plg_api

    root, files = undecodable
    good = _compressed(root / "kits" / "good_ulaw.wav", "ULAW")
    for path in files:
        assert sound_preview.playable(path) is None
    assert sound_preview.playable(good) is not None  # decoded to a PCM24 copy
    monkeypatch.setattr(sound_preview, "_cache_dir", lambda: tmp_path / "cache")
    assert wav_decode.decodable(sound_preview.playable(good)) and sound_preview.playable(good).parent == tmp_path / "cache"

    catalog = tmp_path / "catalog.json"
    rels = [f"kits/{p.name}" for p in files] + ["kits/good_ulaw.wav"]
    catalog.write_text(json.dumps({"root": str(root), "audio": {"kits": rels}}), encoding="utf-8")
    monkeypatch.setattr(plg_api, "CATALOG_FILE", catalog)
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: root)

    usable = {s["name"]: s["usable"] for s in plg_api.list_sounds()["sounds"]}

    assert usable == {"bad_ogg.wav": False, "mp3_in_wav.wav": False, "made_up.wav": False, "good_ulaw.wav": True}


def test_pcm_and_float_wavs_are_untouched_by_the_check(tmp_path):
    pcm = tmp_path / "pcm.wav"
    sf.write(str(pcm), _tone(), RATE, subtype="PCM_16")
    flt = tmp_path / "float.wav"
    sf.write(str(flt), _tone(), RATE, subtype="FLOAT")
    assert wav_decode.decodable(pcm) and wav_decode.decodable(flt)
    assert wav_decode.decodable(tmp_path / "missing.wav")  # not judged here: other checks own it


# --- the real file that started this -----------------------------------------------------------

REAL = Path(r"C:\PLG_Sounds\Snare & Rim\200 KASH Snare #2 @producera.wav")


@pytest.mark.skipif(not REAL.is_file(), reason="the developer's library is not on this machine")
def test_the_real_ogg_in_wav_file_decodes_to_the_ogg_stream_it_carries(tmp_path):
    raw = REAL.read_bytes()
    reference, _rate = sf.read(io.BytesIO(raw[raw.index(b"OggS"):raw.index(b"OggS") + 7124]), dtype="float64", always_2d=True)
    out = tmp_path / "copy.wav"

    assert wav_decode.format_tag(REAL) == 0x674F and wav_decode.decodable(REAL)
    assert sample_match.track_candidate_rejection_reasons(REAL, "snare") == []
    assert wav_compat.convert_to_pcm24(REAL, out)
    assert _similar(wav_decode.read_wav_mono(out)[0], reference[:, 0]) > 0.999
