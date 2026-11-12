import wave
from pathlib import Path

import numpy as np

from library_catalog import scan_library


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _write_wav(path: Path, seconds: float, sr: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(sr * seconds)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)


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


def test_deep_custom_kit_sorted_by_keywords(tmp_path: Path):
    # A nested kit with NO top-level 808/hats folders (the real-world case).
    _touch(tmp_path / "MORE CHAOS" / "808s" / "dark_distorted.wav")
    _touch(tmp_path / "MORE CHAOS" / "Claps" / "hard_clap.wav")
    _touch(tmp_path / "ORGANIZED" / "Hi Hats" / "open_hat.wav")
    _touch(tmp_path / "ORGANIZED" / "Melody Loops" / "sad_piano.wav")

    audio = scan_library(tmp_path)["audio"]
    assert any("dark_distorted" in r for r in audio.get("808", []))
    assert any("hard_clap" in r for r in audio.get("kits", []))
    assert any("open_hat" in r for r in audio.get("hats", []))
    assert any("sad_piano" in r for r in audio.get("melodies", []))


def test_distortion_path_is_bass(tmp_path: Path):
    _touch(tmp_path / "Distortion Bass" / "x.wav")
    cat = scan_library(tmp_path)
    assert any(r.endswith("x.wav") for r in cat["audio"].get("808", []))
    assert cat["audio_total"] == 1


def test_nfo_and_images_ignored(tmp_path: Path):
    _touch(tmp_path / "kit" / "808" / "boom.wav")
    _touch(tmp_path / "kit" / "readme.nfo")
    _touch(tmp_path / "kit" / "art.jpg")
    cat = scan_library(tmp_path)
    assert cat["audio_total"] == 1


# --- Layer 1 of the two-layer one-shot fix (audit 2026-07) ----------------
# A real .mp3/.wav that carries no recognized keyword anywhere in its path
# used to default straight into "kits" — a 206-second loop sitting in an
# unrecognized "...BONUS LOOPS" folder landed there and got picked for the
# kick channel. Now the fallback checks duration: short files still land in
# "kits" (unchanged behavior), long ones route to "melodies" instead.


def test_unrecognized_short_file_still_defaults_to_kits(tmp_path: Path):
    _write_wav(tmp_path / "Random Pack" / "Bonus Stuff" / "xyz123.wav", 0.6)
    cat = scan_library(tmp_path)
    assert any("xyz123" in r for r in cat["audio"].get("kits", []))


def test_unrecognized_long_loop_routes_to_melodies_not_kits(tmp_path: Path):
    _write_wav(tmp_path / "MORE_CHAOS_KIT" / "BONUS LOOPS" / "A-Energy 159.wav", 12.0)
    cat = scan_library(tmp_path)
    assert any("A-Energy 159" in r for r in cat["audio"].get("melodies", []))
    assert not any("A-Energy 159" in r for r in cat["audio"].get("kits", []))


def test_unreadable_unrecognized_file_fails_open_into_kits(tmp_path: Path):
    # A file we can't measure (corrupt/unsupported) must not be silently
    # dropped or blocked — same "never break on a best-effort check" rule as
    # everywhere else, see library_paths.audio_duration_seconds.
    _touch(tmp_path / "Weird Pack" / "unknown_thing.wav")
    cat = scan_library(tmp_path)
    assert any("unknown_thing" in r for r in cat["audio"].get("kits", []))


def test_library_scan_creates_clean_808_view_without_deleting_raw_files(tmp_path: Path):
    clicky = "808/TRAP JUMP 808.wav"
    clean = "808/clean 808.wav"
    _write_808_wav(tmp_path / clicky, transient_only=True)
    _write_808_wav(tmp_path / clean, transient_only=False)

    cat = scan_library(tmp_path, quarantine=False)
    assert clicky in cat["audio"]["808"]
    assert clicky not in cat["filtered_audio"]["808"]
    assert cat["rejected_audio"][clicky] == "transient_without_808_body"
    assert clean in cat["filtered_audio"]["808"]


def test_a_scan_never_moves_the_users_files_by_default(tmp_path: Path):
    # Used to move "rejected" audio to _PLG_UNUSE on import, app start and every
    # generation. The clean view already hides it from the matcher; the user's kit
    # is left exactly as it was.
    clicky = "808/TRAP JUMP 808.wav"
    _write_808_wav(tmp_path / clicky, transient_only=True)

    cat = scan_library(tmp_path)

    assert (tmp_path / clicky).is_file()
    assert not (tmp_path / "_PLG_UNUSE").exists()
    assert cat["quarantined_audio"] == {}
    assert cat["rejected_audio"][clicky] == "transient_without_808_body"
    assert clicky not in cat["filtered_audio"].get("808", [])  # still hidden from the matcher


def test_explicit_quarantine_opt_in_still_moves_with_a_recovery_manifest(tmp_path: Path):
    clicky = "808/TRAP JUMP 808.wav"
    _write_808_wav(tmp_path / clicky, transient_only=True)

    cat = scan_library(tmp_path, quarantine=True)
    destination = cat["quarantined_audio"][clicky]
    assert not (tmp_path / clicky).exists()
    assert (tmp_path / destination).is_file()
    manifest = tmp_path / "_PLG_UNUSE" / "manifest.json"
    rows = __import__("json").loads(manifest.read_text(encoding="utf-8"))
    assert rows[-1]["original_path"] == clicky
    assert rows[-1]["reason"] == "transient_without_808_body"


def test_a_normal_scan_puts_back_files_an_older_plg_moved(tmp_path: Path):
    from library_catalog import restore_quarantined_audio  # noqa: F401  (public API)

    clicky = "808/TRAP JUMP 808.wav"
    _write_808_wav(tmp_path / clicky, transient_only=True)
    scan_library(tmp_path, quarantine=True)  # what the old code did on import
    assert not (tmp_path / clicky).exists()

    cat = scan_library(tmp_path)  # what every scan does now

    assert (tmp_path / clicky).is_file()
    assert not (tmp_path / "_PLG_UNUSE").exists()  # emptied and removed
    assert cat["quarantined_audio"] == {}
    assert clicky not in cat["filtered_audio"].get("808", [])  # restored, yet hidden from the matcher


def test_restore_never_overwrites_a_file_that_took_the_place(tmp_path: Path):
    from library_catalog import restore_quarantined_audio

    clicky = "808/TRAP JUMP 808.wav"
    _write_808_wav(tmp_path / clicky, transient_only=True)
    scan_library(tmp_path, quarantine=True)
    _write_808_wav(tmp_path / clicky, transient_only=False)  # the user re-added a file there
    keeper = (tmp_path / clicky).read_bytes()

    restored = restore_quarantined_audio(tmp_path)

    assert (tmp_path / clicky).read_bytes() == keeper
    assert list(restored.values()) == ["808/TRAP JUMP 808__restored.wav"]
    assert (tmp_path / "808" / "TRAP JUMP 808__restored.wav").is_file()


def test_restore_ignores_a_manifest_row_whose_file_is_gone(tmp_path: Path):
    from library_catalog import restore_quarantined_audio

    clicky = "808/TRAP JUMP 808.wav"
    _write_808_wav(tmp_path / clicky, transient_only=True)
    cat = scan_library(tmp_path, quarantine=True)
    (tmp_path / cat["quarantined_audio"][clicky]).unlink()  # the user deleted it

    assert restore_quarantined_audio(tmp_path) == {}
    assert not (tmp_path / clicky).exists()


def test_tr808_and_sound_design_names_are_left_where_the_user_put_them(tmp_path: Path):
    # Ordinary short one-shots whose NAMES contain "808"/"distort" or a word that
    # looks like "hat" used to be judged by the wrong bucket's gate and moved.
    names = [
        "808 Kick 01.wav", "Kick 808.wav", "808 Snare 01.wav", "TR-808 Snare.wav",
        "808 Clap 01.wav", "808 Hat 01.wav", "Kick Distorted.wav", "That Clap.wav",
        "Chatter Clap.wav", "Bride Kick.wav",
    ]
    for name in names:
        _write_wav(tmp_path / "kit" / name, 0.15)

    scan_library(tmp_path)

    for name in names:
        assert (tmp_path / "kit" / name).is_file(), name
    assert not (tmp_path / "_PLG_UNUSE").exists()
