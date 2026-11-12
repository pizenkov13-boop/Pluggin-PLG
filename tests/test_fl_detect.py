import json
from pathlib import Path

from fl_detect import (
    configured_fl_executable,
    discover_fl_installations,
    flp_header_version,
    fl_profile_version,
    fl_version_label,
    parse_fl_major,
    resolve_fl_executable,
    save_fl_executable,
)


def test_parse_fl_major_year_and_legacy_names():
    assert parse_fl_major("FL Studio 2025") == 25
    assert parse_fl_major("FL Studio 2026") == 26
    assert parse_fl_major("FL Studio 24") == 24
    assert parse_fl_major("FL Studio 21") == 21
    assert parse_fl_major("FL Studio 20") == 20
    assert parse_fl_major("Other") is None


def test_version_sort_prefers_2025_over_21(tmp_path: Path, monkeypatch):
    roots = tmp_path / "Image-Line"
    for name in ("FL Studio 21", "FL Studio 2025", "FL Studio 24"):
        folder = roots / name
        folder.mkdir(parents=True)
        (folder / "FL64.exe").write_bytes(b"")

    monkeypatch.setattr("fl_detect._IMAGE_LINE_ROOTS", (roots,))
    monkeypatch.setattr("fl_detect._registry_exes", lambda: [])
    installs = discover_fl_installations()
    assert [exe.parent.name for exe in installs] == [
        "FL Studio 2025",
        "FL Studio 24",
        "FL Studio 21",
    ]


def test_user_override_wins(tmp_path: Path, monkeypatch):
    custom = tmp_path / "FL Studio 23"
    custom.mkdir()
    custom_exe = custom / "FL64.exe"
    custom_exe.write_bytes(b"")

    newer = tmp_path / "Image-Line" / "FL Studio 2025" / "FL64.exe"
    newer.parent.mkdir(parents=True)
    newer.write_bytes(b"")

    profile = tmp_path / "user_profile.json"
    monkeypatch.setattr("fl_detect.USER_PROFILE_FILE", profile)
    monkeypatch.setattr("fl_detect._IMAGE_LINE_ROOTS", (tmp_path / "Image-Line",))
    monkeypatch.setattr("fl_detect._registry_exes", lambda: [])

    save_fl_executable(str(custom_exe))
    assert resolve_fl_executable() == custom_exe.resolve()


def test_manual_override_broken_does_not_silently_fall_back(tmp_path: Path, monkeypatch):
    """Manual QA #5 regression: a user-picked FL64.exe that later disappears
    must not be silently swapped for a different installed FL Studio, and the
    saved override must not be rewritten to that other install either.

    Scenario: manual FL path saved -> executable disappears -> another FL
    gets installed -> saved path stays unchanged + no silent fallback.
    """
    custom = tmp_path / "FL Studio 23"
    custom.mkdir()
    custom_exe = custom / "FL64.exe"
    custom_exe.write_bytes(b"")

    profile = tmp_path / "user_profile.json"
    monkeypatch.setattr("fl_detect.USER_PROFILE_FILE", profile)
    monkeypatch.setattr("fl_detect._IMAGE_LINE_ROOTS", (tmp_path / "Image-Line",))
    monkeypatch.setattr("fl_detect._registry_exes", lambda: [])

    # User explicitly picks FL64.exe in Settings.
    save_fl_executable(str(custom_exe))
    assert resolve_fl_executable() == custom_exe.resolve()

    # The picked executable disappears (moved/uninstalled/unmounted drive).
    custom_exe.unlink()

    # A different FL Studio gets installed and is perfectly discoverable.
    newer = tmp_path / "Image-Line" / "FL Studio 2025" / "FL64.exe"
    newer.parent.mkdir(parents=True)
    newer.write_bytes(b"")
    assert discover_fl_installations() == [newer.resolve()]

    # No silent fallback: resolve_fl_executable() must NOT return the newly
    # discovered install — callers treat None as "FL Studio not found" and
    # route the user to Settings instead of guessing.
    assert resolve_fl_executable() is None

    # The user's saved choice must survive untouched — not rewritten to the
    # auto-detected install, and still shown as-is (broken) in Settings.
    assert configured_fl_executable() == str(custom_exe)
    saved_raw = json.loads(profile.read_text(encoding="utf-8"))
    assert saved_raw["fl_executable"] == str(custom_exe)


def test_fl_labels_and_flp_header():
    exe = Path(r"C:\Program Files\Image-Line\FL Studio 2025\FL64.exe")
    assert fl_version_label(exe) == "FL 2025"
    assert fl_profile_version(exe) == "2025"
    assert flp_header_version(exe) == "21.0.0.0"
