"""Smoke tests for plg_api helpers added for full UI."""

from __future__ import annotations

import json
from pathlib import Path
import base64

import plg_api


def test_get_app_info_has_version_and_docs(tmp_path, monkeypatch):
    # DOC_FILES resolve against DOCS_DIR (bundle_dir()) now, not PROJECT_DIR —
    # docs ship as read-only PyInstaller data, not next to the user's .env.
    monkeypatch.setattr(plg_api, "DOCS_DIR", tmp_path)
    (tmp_path / "START_HERE.md").write_text("# start", encoding="utf-8")

    info = plg_api.get_app_info()

    assert info["ok"] is True
    assert info["version"] == plg_api.APP_VERSION
    assert "start_here" in info["docs"]
    assert "quota" in info


def test_get_docs_matches_get_app_info_without_the_extra_work(tmp_path, monkeypatch):
    monkeypatch.setattr(plg_api, "DOCS_DIR", tmp_path)
    (tmp_path / "START_HERE.md").write_text("# start", encoding="utf-8")
    (tmp_path / "legal").mkdir()
    (tmp_path / "legal" / "TERMS_RU.md").write_text("# terms", encoding="utf-8")

    def fail_if_called(*_a, **_k):
        raise AssertionError("get_docs() must not touch FL detection / quota")

    monkeypatch.setattr(plg_api, "find_fl_executable", fail_if_called)
    monkeypatch.setattr(plg_api, "list_fl_installations", fail_if_called)
    monkeypatch.setattr(plg_api, "get_quota", fail_if_called)

    result = plg_api.get_docs()

    assert result == {"ok": True, "docs": {"start_here": str(tmp_path / "START_HERE.md"), "terms": str(tmp_path / "legal" / "TERMS_RU.md")}}


def test_open_document_opens_file(tmp_path, monkeypatch):
    monkeypatch.setattr(plg_api, "DOCS_DIR", tmp_path)
    doc = tmp_path / "FL_BRIDGE.md"
    doc.write_text("# bridge", encoding="utf-8")

    called: list[str] = []

    def fake_open(path: str):
        called.append(path)
        return {"ok": True}

    monkeypatch.setattr(plg_api, "open_path", fake_open)
    result = plg_api.open_document("fl_bridge")

    assert result["ok"] is True
    assert called == [str(doc)]


def test_import_kit_folder_copies_and_scans(tmp_path, monkeypatch):
    src = tmp_path / "kit"
    src.mkdir()
    (src / "subs").mkdir()
    (src / "kick_hard.wav").write_bytes(b"wav")
    (src / "subs" / "boom808.wav").write_bytes(b"wav")
    (src / "melody.mid").write_bytes(b"MThd")
    out = tmp_path / "library"
    midi_root = tmp_path / "PLG_MIDI"
    out.mkdir()

    monkeypatch.setattr(plg_api, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: str(out))
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)

    result = plg_api.import_kit_folder(str(src))

    assert result["ok"] is True
    assert result["mode"] == "as_is"
    assert (out / "imports" / "kit" / "kick_hard.wav").is_file()
    assert (out / "imports" / "kit" / "subs" / "boom808.wav").is_file()
    assert (midi_root / "imports" / "kit" / "melody.mid").is_file()
    assert (tmp_path / "catalog.json").is_file()
    catalog = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["audio_total"] >= 2


def test_import_kit_folder_missing_dir():
    result = plg_api.import_kit_folder(str(Path("/nonexistent/kit_folder_xyz")))
    assert result["ok"] is False


def test_chunked_material_import_routes_midi_and_audio_to_active_roots(tmp_path, monkeypatch):
    sounds = tmp_path / "PLG_Sounds"
    midi_root = tmp_path / "PLG_MIDI"
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: sounds)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)
    monkeypatch.setattr(plg_api, "CATALOG_FILE", tmp_path / "sample_catalog.json")

    started = plg_api.begin_material_import()
    assert started["ok"] is True
    import_id = started["import_id"]
    assert plg_api.import_material_chunk(
        import_id,
        "My kit/melody.mid",
        0,
        base64.b64encode(b"midi bytes").decode("ascii"),
    )["ok"] is True
    assert plg_api.import_material_chunk(
        import_id,
        "My kit/Kick.wav",
        0,
        base64.b64encode(b"wav bytes").decode("ascii"),
    )["ok"] is True

    completed = plg_api.finish_material_import(import_id)
    assert completed["ok"] is True
    assert completed["midi_total"] == 1
    assert completed["audio_total"] == 1
    # Files land flat in PLG_MIDI/PLG_Sounds (subpath from the drop preserved) —
    # no imports/drop-<uuid>/ staging folder survives a completed import.
    assert (midi_root / "My kit" / "melody.mid").is_file()
    assert (sounds / "My kit" / "Kick.wav").is_file()
    assert not (midi_root / "imports" / f"drop-{import_id}").exists()
    assert not (sounds / "imports" / f"drop-{import_id}").exists()
    assert plg_api.has_available_midi_material(sounds, midi_root=midi_root) is True


def test_finish_material_import_renames_on_name_collision_never_overwrites(tmp_path, monkeypatch):
    sounds = tmp_path / "PLG_Sounds"
    midi_root = tmp_path / "PLG_MIDI"
    sounds.mkdir()
    (sounds / "kick.wav").write_bytes(b"existing")
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: sounds)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)
    monkeypatch.setattr(plg_api, "CATALOG_FILE", tmp_path / "sample_catalog.json")

    started = plg_api.begin_material_import()
    import_id = started["import_id"]
    assert plg_api.import_material_chunk(
        import_id,
        "kick.wav",
        0,
        base64.b64encode(b"new drop").decode("ascii"),
    )["ok"] is True

    completed = plg_api.finish_material_import(import_id)

    assert completed["ok"] is True
    assert (sounds / "kick.wav").read_bytes() == b"existing"
    assert (sounds / "kick (1).wav").read_bytes() == b"new drop"


def test_cancel_material_import_deletes_partial_files(tmp_path, monkeypatch):
    sounds = tmp_path / "PLG_Sounds"
    midi_root = tmp_path / "PLG_MIDI"
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: sounds)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)

    started = plg_api.begin_material_import()
    import_id = started["import_id"]
    assert plg_api.import_material_chunk(
        import_id,
        "kick.wav",
        0,
        base64.b64encode(b"partial upload").decode("ascii"),
    )["ok"] is True
    staging = sounds / "imports" / f"drop-{import_id}"
    assert (staging / "kick.wav").is_file()

    result = plg_api.cancel_material_import(import_id)

    assert result["ok"] is True
    assert not staging.exists()
    assert not (sounds / "kick.wav").exists()


def test_chunked_material_import_rejects_paths_outside_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: tmp_path / "PLG_Library")
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: tmp_path / "PLG_MIDI")
    started = plg_api.begin_material_import()
    result = plg_api.import_material_chunk(
        started["import_id"],
        "../outside.mid",
        0,
        base64.b64encode(b"x").decode("ascii"),
    )
    assert result["ok"] is False
    assert result["error_type"] == "invalid_path"
