"""The sound browser's preview: only the user's own audio, only with this launch's token (sound browser, step 3)."""

from __future__ import annotations

import struct
import urllib.error
import urllib.request
import wave
from pathlib import Path
from urllib.parse import quote

import pytest

import sound_preview


def _wav(path: Path, frames: int = 2000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(b"".join(struct.pack("<h", (i * 37) % 2000 - 1000) for i in range(frames)))
    return path


def _float_wav(path: Path) -> Path:
    """A 32-bit IEEE-float WAV (format tag 3): FL's sampler and the preview both want PCM instead."""
    samples = [((i % 50) - 25) / 100.0 for i in range(2000)]
    data = b"".join(struct.pack("<f", s) for s in samples)
    fmt = struct.pack("<HHIIHH", 3, 1, 44100, 44100 * 4, 4, 32)
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


@pytest.fixture
def server(tmp_path, monkeypatch):
    root = tmp_path / "PLG_Sounds"
    _wav(root / "kits" / "Kick 01.wav")
    (root / "kits" / "notes.txt").write_text("not audio")
    _wav(tmp_path / "elsewhere" / "secret.wav")
    monkeypatch.setattr(sound_preview, "_base_url", "")
    monkeypatch.setattr(sound_preview, "_cache_dir", lambda: tmp_path / "cache")
    base = sound_preview.serve(lambda: root)
    return root, base


def _get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), b""


def test_resolve_accepts_only_audio_inside_the_library_with_the_right_token(tmp_path):
    root = tmp_path / "PLG_Sounds"
    good = _wav(root / "kits" / "a.wav")
    _wav(tmp_path / "outside.wav")
    (root / "kits" / "b.txt").write_text("x")

    assert sound_preview.resolve(sound_preview.TOKEN, "kits/a.wav", root) == good.resolve()
    assert sound_preview.resolve("wrong", "kits/a.wav", root) is None
    assert sound_preview.resolve(sound_preview.TOKEN, "kits/b.txt", root) is None
    assert sound_preview.resolve(sound_preview.TOKEN, "kits/missing.wav", root) is None
    assert sound_preview.resolve(sound_preview.TOKEN, "../outside.wav", root) is None
    assert sound_preview.resolve(sound_preview.TOKEN, "", root) is None


def test_the_server_plays_a_library_file_whole_and_by_range(server):
    root, base = server
    url = base + quote("kits/Kick 01.wav")
    original = (root / "kits" / "Kick 01.wav").read_bytes()

    status, headers, body = _get(url)
    assert status == 200 and body == original and headers["Content-Type"] == "audio/wav"
    assert headers["Accept-Ranges"] == "bytes"

    status, headers, body = _get(url, {"Range": "bytes=10-19"})
    assert status == 206 and body == original[10:20]
    assert headers["Content-Range"] == f"bytes 10-19/{len(original)}"
    status, _h, body = _get(url, {"Range": "bytes=-8"})
    assert status == 206 and body == original[-8:]


@pytest.mark.parametrize(
    "tail",
    ["kits/notes.txt", "kits/nope.wav", "../elsewhere/secret.wav", "%2e%2e/elsewhere/secret.wav", "..%2Felsewhere%2Fsecret.wav"],
)
def test_nothing_but_library_audio_is_ever_served(server, tail):
    _root, base = server
    assert _get(base + tail)[0] == 404


def test_a_wrong_token_or_a_bare_path_is_refused(server):
    _root, base = server
    host = base.split("/")[0] + "//" + base.split("/")[2]
    assert _get(f"{host}/not-the-token/kits/Kick%2001.wav")[0] == 404
    assert _get(f"{host}/kits/Kick%2001.wav")[0] == 404
    assert _get(host + "/")[0] == 404


def test_a_float_wav_is_served_as_a_pcm24_copy(server, tmp_path):
    root, base = server
    float_file = _float_wav(root / "kits" / "float.wav")

    status, _h, body = _get(base + "kits/float.wav")

    assert status == 200 and body != float_file.read_bytes()
    from wav_compat import inspect_wav

    copy = tmp_path / "served.wav"
    copy.write_bytes(body)
    tag, _ch, _sr, bits, _data = inspect_wav(copy)
    assert (tag, bits) == (1, 24)


def test_the_list_hands_the_ui_the_preview_address(server, tmp_path, monkeypatch):
    import json

    import plg_api

    root, base = server
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"root": str(root), "audio": {"kits": ["kits/Kick 01.wav"]}}), encoding="utf-8")
    monkeypatch.setattr(plg_api, "CATALOG_FILE", catalog)
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: root)

    listed = plg_api.list_sounds()

    assert listed["preview_base"] == base and base.endswith(sound_preview.TOKEN + "/")


# --- the preview must never play a broken copy (a click bug found on the real library) --------------


def _tagged_wav(path: Path, tag: int, bits: int = 16) -> Path:
    """A WAV whose fmt tag is not PCM / float (Ogg-in-WAV is 0x674F, MS ADPCM is 2)."""
    data = bytes(range(256)) * 8
    fmt = struct.pack("<HHIIHH", tag, 1, 44100, 44100 * 2, 2, bits)
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


@pytest.mark.parametrize("tag", [0x674F, 2])
def test_a_wav_format_the_converter_cannot_decode_is_refused_not_played_as_noise(server, tag):
    root, base = server
    odd = _tagged_wav(root / "kits" / "odd.wav", tag)

    assert sound_preview.playable(odd) is None
    assert _get(base + "kits/odd.wav")[0] == 415


def test_an_upper_case_extension_is_served(server):
    root, base = server
    _wav(root / "kits" / "LOUD.WAV")

    status, headers, _body = _get(base + "kits/LOUD.WAV")

    assert status == 200 and headers["Content-Type"] == "audio/wav"


def test_simultaneous_clicks_on_a_slow_to_convert_file_all_get_the_whole_copy(server, monkeypatch):
    """Before the fix the second request found the half-written copy and served it."""
    import threading
    import time

    import wav_compat

    root, base = server
    _float_wav(root / "kits" / "slow.wav")
    real = wav_compat.convert_to_pcm24

    def slow_convert(src, dst):
        scratch = dst.with_name("scratch.tmp")
        assert real(src, scratch)
        whole = scratch.read_bytes()
        scratch.unlink()
        with open(dst, "wb") as handle:  # a slow disk: half, a pause, the rest
            handle.write(whole[: len(whole) // 2])
            handle.flush()
            time.sleep(0.4)
            handle.write(whole[len(whole) // 2 :])
        return True

    monkeypatch.setattr(wav_compat, "convert_to_pcm24", slow_convert)
    bodies: list[bytes] = []

    def click():
        bodies.append(_get(base + "kits/slow.wav")[2])

    threads = [threading.Thread(target=click) for _ in range(4)]
    for thread in threads:
        thread.start()
        time.sleep(0.05)  # the later clicks arrive while the first copy is being written
    for thread in threads:
        thread.join()

    assert len(bodies) == 4 and len({len(b) for b in bodies}) == 1 and len(bodies[0]) > 4000
    assert len({b for b in bodies}) == 1
