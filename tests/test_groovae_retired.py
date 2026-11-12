"""The GrooVAE / Magenta route stays retired.

``groovae_humanize`` shipped an optional drum-humanization step built on
Magenta. It was off by default in every build (``use_groovae()`` returned
False even for ``"auto"``), its dependencies were never packaged, and the
~40 MB checkpoint it would have fetched had no version pin, no checksum and
no recorded licence for the weights. It was removed on 2026-08-27.

These tests pin the removal: the module is gone, the pipeline no longer
reaches for it, and the only network call the desktop code ever made is no
longer in the source tree.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

RETIRED_TOKENS = (
    "groovae",
    "GrooVAE",
    "PLG_USE_GROOVAE",
    "magenta",
    "note_seq",
    "note-seq",
    "groovae_2bar_humanize",
    "storage.googleapis.com",
)

# Files that legitimately still name the route as history rather than code.
HISTORY_ALLOWED = {
    "the project issue log (not included)",
    "PROJECT_MAP.md",
    "the asset inventory (not included)",
    "docs/DEPENDENCY_LICENSE_INVENTORY.md",
    "tests/test_groovae_retired.py",
    "tests/test_symbolic_composer.py",
    "symbolic_composer.py",
    "validator.py",
}


def test_module_is_gone():
    assert not (REPO / "groovae_humanize.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("groovae_humanize")


def test_groovae_only_tests_are_gone():
    assert not (REPO / "tests" / "test_groovae_humanize.py").exists()


def test_no_python_source_imports_the_retired_module():
    hits = []
    for path in REPO.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "import groovae_humanize" in text or "from groovae_humanize" in text:
            hits.append(path.name)
    assert hits == []


def test_checkpoint_url_is_absent_from_the_source_tree():
    """The only urlopen the desktop code ever had went to this host."""
    hits = []
    for path in REPO.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "storage.googleapis.com" in text or "groovae_2bar_humanize" in text:
            hits.append(path.name)
    assert hits == []


def test_no_desktop_module_performs_checkpoint_network_io():
    """No root-level module reaches for urllib.request any more."""
    hits = []
    for path in REPO.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "urllib.request" in text:
            hits.append(path.name)
    assert hits == []


def test_optional_requirements_no_longer_advertise_the_route():
    text = (REPO / "requirements-optional.txt").read_text(encoding="utf-8")
    for token in ("GrooVAE", "groovae", "magenta", "note-seq"):
        assert token not in text, token


@pytest.mark.parametrize("env_value", ["1", "true", "on", "auto", "0"])
def test_setting_the_retired_env_flag_changes_nothing(monkeypatch, env_value):
    """An old shell/profile that still exports PLG_USE_GROOVAE is inert."""
    import symbolic_composer as sc

    pattern = {
        "bpm": 140,
        "style": "rage",
        "tracks": {
            "kick": [{"note": "C1", "time_step": i * 4.0, "velocity": 110, "length": 1.0} for i in range(8)],
            "hi_hats": [{"note": "F#1", "time_step": i * 1.0, "velocity": 90, "length": 0.5} for i in range(16)],
        },
    }

    def canon(obj):
        return hashlib.sha256(
            json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    monkeypatch.delenv("PLG_USE_GROOVAE", raising=False)
    baseline = canon(sc.apply_drum_symbolic_pipeline(dict(pattern)))

    monkeypatch.setenv("PLG_USE_GROOVAE", env_value)
    assert canon(sc.apply_drum_symbolic_pipeline(dict(pattern))) == baseline


def test_pipeline_still_runs_without_the_retired_step():
    import symbolic_composer as sc

    out = sc.apply_drum_symbolic_pipeline(
        {"bpm": 140, "style": "rage", "tracks": {"kick": [
            {"note": "C1", "time_step": i * 4.0, "velocity": 110, "length": 1.0} for i in range(8)
        ]}}
    )
    assert "tracks" in out
    assert out.get("metadata", {}).get("validator_ok") is not None
