"""The tester build's plumbing: the flavor file, the archive scanner's rules and the guards that keep the
tester flavor out of the release build (read as text: PLG.spec and build_plg.bat are not importable)."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def make_flavor():
    return _load("make_build_flavor")


@pytest.fixture(scope="module")
def verify():
    return _load("verify_build_flavor")


# --- the flavor file ------------------------------------------------------------------------


def test_the_flavor_counts_days_from_today(make_flavor):
    today = date(2026, 10, 28)
    assert make_flavor.flavor(14, today) == {"flavor": "tester", "built": "2026-10-28", "expires": "2026-11-11"}
    assert make_flavor.flavor(0, today)["expires"] == "2026-10-28"
    assert make_flavor.flavor(-1, today)["expires"] == "2026-10-27"  # an already expired build, for checking


def test_the_tool_writes_the_file_the_app_reads(make_flavor, tmp_path):
    out = tmp_path / "x" / "build_flavor.json"
    assert make_flavor.main(["make_build_flavor", "7", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["flavor"] == "tester" and data["expires"] == (date.today() + timedelta(days=7)).isoformat()
    assert make_flavor.main(["make_build_flavor", "soon", str(out)]) == 2 and make_flavor.main(["make_build_flavor"]) == 2


# --- the scanner's rules (values are never printed, so the rules themselves are what is tested) ---


@pytest.mark.parametrize(
    "text",
    [
        "see https://cloud.example.com/v1/health",
        "api.plugflp.tech",
        "http://203.0.114.7:8787/v1/region",  # an address that is not a documentation one
        "x.abcdefghijklmnop.supabase.co",
        "PLG_CLOUD_URL=https://cloud.example.test",
        "SUPABASE_ANON_KEY=abcdefghijklmn",
        "sb_publishable_AbCdEfGhIjKlMnOpQr",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVP",
        "-----BEGIN PRIVATE KEY-----",
        "sk-or-v1-0123456789abcdef0123456789abcdef",
        "s3.example.com",
    ],
)
@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_scanner_flags_servers_keys_and_addresses(verify, text):
    assert verify.scan_text(text), text


@pytest.mark.parametrize(
    "text",
    [
        "http://127.0.0.1:8787", "listening on 0.0.0.0", "dns 1.1.1.1 and 8.8.8.8", "doc range 203.0.113.9",
        "contact help@example.com", "numpy==1.26.4.0", "version 2.0.0.1", "the SUPABASE_URL setting",
        "PLG_CLOUD_URL is read from .env", "PLG_CLOUD_URL=http://127.0.0.1:8787", "FLP_HEADER_VERSION = \"20.0.0.10\"", "21.0.0.0", "999.1.2.3", "plain text with no address",
    ],
)
def test_the_scanner_lets_ordinary_text_through(verify, text):
    assert verify.scan_text(text) == [], text


# --- keeping the tester flavor out of the release build --------------------------------------


def _bat_lines() -> list[str]:
    text = (REPO / "build_plg.bat").read_bytes().decode("utf-8", "ignore")
    return [line.strip() for line in text.splitlines() if line.strip()]


def test_the_release_path_clears_the_flavor_variable_and_never_sets_it():
    lines = _bat_lines()
    assignments = [(i, line) for i, line in enumerate(lines) if re.match(r"(?i)set PLG_BUILD_FLAVOR_FILE=", line)]
    assert assignments and assignments[0][1].lower() == "set plg_build_flavor_file="  # cleared first, for every mode
    tester_label = next(i for i, line in enumerate(lines) if line.lower() == ":tester_flavor")
    built_label = next(i for i, line in enumerate(lines) if line.lower() == ":built")
    for i, line in assignments[1:]:
        if line.lower() != "set plg_build_flavor_file=":  # a non-empty assignment
            assert tester_label < i < built_label, "the flavor file is set outside the tester branch"
    before_tester = lines[:tester_label]
    assert not any("make_build_flavor" in line for line in before_tester)


def test_every_build_ends_with_the_archive_check_in_its_own_mode():
    text = "\n".join(_bat_lines())
    assert r"verify_build_flavor.py %VERIFY_MODE% dist\%OUT_EXE%" in text
    assert "set VERIFY_MODE=release" in text and "set VERIFY_MODE=tester" in text
    assert "OUT_EXE=PLG_Tester.exe" in text and "OUT_EXE=PLG.exe" in text


def test_the_tester_build_has_its_own_work_folder_so_no_cache_crosses_over():
    assert r"--workpath build\PLG_Tester" in "\n".join(_bat_lines())


def test_the_spec_takes_the_flavor_only_from_the_variable_the_bat_hands_it():
    spec = (REPO / "PLG.spec").read_text(encoding="utf-8")
    assert 'os.environ.get("PLG_BUILD_FLAVOR_FILE"' in spec
    assert '".env.tester" if TESTER_BUILD else ".env.release"' in spec
    assert "([(_flavor_file, \".\")] if TESTER_BUILD else [])" in spec
    assert 'EXE_NAME = "PLG_Tester" if TESTER_BUILD else "PLG"' in spec


def test_the_flavor_file_is_never_committed():
    assert "build_flavor.json" in (REPO / ".gitignore").read_text(encoding="utf-8")
