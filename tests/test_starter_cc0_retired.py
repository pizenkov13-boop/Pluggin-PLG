"""The retired CC0 starter-install path stays retired.

PLG used to let a user drop sample-pack zips into ``assets/starter/incoming/``;
``ensure_starter_kit()`` would extract them and copy WAVs out of the archive
over the shipped starter files. That path was removed on 2026-08-27, and these
tests pin the three things that removal has to guarantee:

* a clean install still gets the exact first-party audio in ``GENERATED.json``;
* a zip in ``incoming/`` -- valid or corrupt -- can no longer influence anything;
* an install that already went through the old path keeps its bytes untouched,
  because those bytes are the sound its owner currently hears.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import wave
import zipfile
from pathlib import Path

import pytest

import starter_kit

REPO = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((REPO / "assets" / "starter" / "GENERATED.json").read_text(encoding="utf-8"))["files"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def starter_dir(tmp_path, monkeypatch):
    """A clean install: an empty runtime dir beside the read-only shipped bundle.

    On a frozen install these are two different places -- the bundle lives
    inside the exe (``starter_bundle_dir``) and the runtime folder under
    ``%LOCALAPPDATA%``. Collapsing them into one would let ``_seed_from_bundle``
    find nothing to copy and fall through to the bundled-pool picker, which is
    not what a real first run does.
    """
    bundle = tmp_path / "bundle"
    (bundle / "bundled_sounds").mkdir(parents=True)
    shipped = REPO / "assets" / "starter"
    for name in set(starter_kit.TRACK_STARTER_FILES.values()):
        shutil.copy2(shipped / name, bundle / name)
    for src in (shipped / "bundled_sounds").glob("*.wav"):
        shutil.copy2(src, bundle / "bundled_sounds" / src.name)

    runtime = tmp_path / "starter"
    runtime.mkdir()

    monkeypatch.setattr(starter_kit, "_starter_dir", lambda: runtime)
    monkeypatch.setattr(starter_kit, "starter_bundle_dir", lambda: bundle)
    monkeypatch.setattr(starter_kit, "bundled_sounds_dir", lambda: bundle / "bundled_sounds")
    return runtime


def write_zip(path: Path, member: str) -> None:
    """A structurally valid zip carrying one real WAV, named to win scoring."""
    frames = struct.pack("<h", 1000) * 44100
    wav = path.parent / "_payload.wav"
    with wave.open(str(wav), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(frames)
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(wav, member)
    wav.unlink()


# --------------------------------------------------------------------------
# 1. clean install
# --------------------------------------------------------------------------

def test_clean_install_produces_the_contracted_first_party_audio(starter_dir):
    paths = starter_kit.ensure_starter_kit()

    assert set(paths) == set(starter_kit.TRACK_STARTER_FILES)
    for track, path in paths.items():
        name = starter_kit.TRACK_STARTER_FILES[track]
        assert path.is_file(), name
        assert sha256(path) == CONTRACT[name]["sha256"], name


def test_clean_install_is_idempotent(starter_dir):
    first = {k: sha256(v) for k, v in starter_kit.ensure_starter_kit().items()}
    second = {k: sha256(v) for k, v in starter_kit.ensure_starter_kit().items()}
    assert first == second


# --------------------------------------------------------------------------
# 2-3. incoming/*.zip is inert
# --------------------------------------------------------------------------

def test_valid_zip_in_incoming_cannot_change_the_starter_audio(starter_dir):
    starter_kit.ensure_starter_kit()
    before = {p.name: sha256(p) for p in starter_dir.glob("*.wav")}

    incoming = starter_dir / "incoming"
    incoming.mkdir(exist_ok=True)
    write_zip(incoming / "trap_vault.zip", "hat_loop.wav")

    starter_kit.ensure_starter_kit()

    assert {p.name: sha256(p) for p in starter_dir.glob("*.wav")} == before
    assert not (starter_dir / "_cache").exists()
    assert not (starter_dir / "LICENSE_CC0.txt").exists()


def test_corrupt_zip_in_incoming_cannot_break_generation(starter_dir):
    incoming = starter_dir / "incoming"
    incoming.mkdir(exist_ok=True)
    # Before retirement this raised zipfile.BadZipFile straight through
    # ensure_starter_kit() into beat generation, which never guarded it.
    (incoming / "kick_drums.zip").write_bytes(b"not a zip at all")

    paths = starter_kit.ensure_starter_kit()

    for track, path in paths.items():
        assert sha256(path) == CONTRACT[starter_kit.TRACK_STARTER_FILES[track]]["sha256"]


# --------------------------------------------------------------------------
# 4. legacy installs are left alone
# --------------------------------------------------------------------------

def test_legacy_cc0_starter_wavs_are_never_rewritten(starter_dir):
    """The core product rule: an old install's sound must not change."""
    starter_kit.ensure_starter_kit()

    # Stand in for third-party audio a retired installer once copied in.
    substituted = {}
    for name in ("PLG_starter_hat.wav", "PLG_starter_808.wav", "PLG_starter_melody.wav"):
        target = starter_dir / name
        with wave.open(str(target), "w") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(44100)
            handle.writeframes(struct.pack("<h", 4321) * 22050)
        substituted[name] = sha256(target)

    (starter_dir / "starter_manifest.json").write_text(
        json.dumps({"source": starter_kit.LEGACY_STARTER_SOURCE, "bundle_version": 1}),
        encoding="utf-8",
    )

    before = {p.name: sha256(p) for p in starter_dir.glob("*.wav")}
    starter_kit.ensure_starter_kit()
    after = {p.name: sha256(p) for p in starter_dir.glob("*.wav")}

    assert after == before
    for name, digest in substituted.items():
        assert after[name] == digest, "%s was rewritten" % name
        assert after[name] != CONTRACT[name]["sha256"]


def test_legacy_source_is_still_recognised_but_not_installable(starter_dir):
    starter_kit.ensure_starter_kit()
    (starter_dir / "starter_manifest.json").write_text(
        json.dumps({"source": starter_kit.LEGACY_STARTER_SOURCE, "bundle_version": 1}),
        encoding="utf-8",
    )

    info = starter_kit.starter_kit_info()
    assert info["source"] == starter_kit.LEGACY_STARTER_SOURCE
    assert info["bundled"] is True
    assert info["legacy_starter"] is True

    # Nothing can produce that state any more.
    assert not hasattr(starter_kit, "install_from_incoming_zips")
    assert not hasattr(starter_kit, "_extract_incoming_zips")


# --------------------------------------------------------------------------
# retired surface is gone
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "symbol",
    ["CC0_SOURCES", "_cache_dir", "_license_file", "_write_cc0_license",
     "_extract_incoming_zips", "install_from_incoming_zips"],
)
def test_retired_symbol_is_absent(symbol):
    assert not hasattr(starter_kit, symbol)


def test_retired_tooling_files_are_gone():
    assert not (REPO / "download_starter_pack.py").exists()
    assert not (REPO / "install_starter_sounds.bat").exists()


def test_incoming_dir_survives_for_the_api_contract(starter_dir):
    # web/src/types.ts still types starter.incoming_dir; keep it addressable.
    assert "incoming_dir" in starter_kit.starter_kit_info()
    assert starter_kit._incoming_dir().is_dir()


def test_shipped_docs_no_longer_advertise_the_retired_installer():
    for name in ("assets/starter/README.md",):
        text = (REPO / name).read_text(encoding="utf-8")
        assert "install_starter_sounds" not in text, name
        assert "Signature Sounds" not in text, name
