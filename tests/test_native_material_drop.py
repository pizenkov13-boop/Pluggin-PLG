"""_handle_native_material_drop must never contradict a drop the browser-native
JS handler (HomeHero.tsx collectDroppedMaterials) already accepted.

Both handlers fire on the same Explorer drop event. This one exists only to
unlock folder imports (WebView2 hides folder paths from JS) — it must not
report failure just because a drop wasn't a folder, since a single material
file is legitimately owned by the JS side."""

from __future__ import annotations

import json

import plg_webview as pw


class _FakeWindow:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def evaluate_js(self, script: str) -> None:
        self.calls.append(script)


def _last_payload(window: _FakeWindow) -> dict:
    script = window.calls[-1]
    prefix = "window.dispatchEvent(new CustomEvent('plg-native-material-imported', { detail: "
    suffix = " }));"
    assert script.startswith(prefix) and script.endswith(suffix), script
    return json.loads(script[len(prefix):-len(suffix)])


def test_single_material_file_drop_is_silent(tmp_path, monkeypatch):
    """The real bug: dropping one .mid file used to stamp a false 'drop a
    folder' error over a drop the JS side had just accepted correctly."""
    midi = tmp_path / "melody.mid"
    midi.write_bytes(b"")
    window = _FakeWindow()
    event = {"dataTransfer": {"files": [{"pywebviewFullPath": str(midi)}]}}
    monkeypatch.setattr(
        pw.plg_api,
        "import_kit_folder",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not treat a file as a folder")),
    )

    pw._handle_native_material_drop(window, event)

    assert window.calls == []


def test_single_unsupported_file_drop_still_reports_error(tmp_path):
    junk = tmp_path / "notes.txt"
    junk.write_bytes(b"")
    window = _FakeWindow()
    event = {"dataTransfer": {"files": [{"pywebviewFullPath": str(junk)}]}}

    pw._handle_native_material_drop(window, event)

    payload = _last_payload(window)
    assert payload["ok"] is False
    assert payload["error_type"] == "unsupported_file"


def test_folder_drop_still_imports(tmp_path, monkeypatch):
    kit = tmp_path / "MyKit"
    kit.mkdir()
    window = _FakeWindow()
    event = {"dataTransfer": {"files": [{"pywebviewFullPath": str(kit)}]}}
    seen: dict = {}

    def _fake_import(folder):
        seen["folder"] = folder
        return {"ok": True, "imported": 3}

    monkeypatch.setattr(pw.plg_api, "import_kit_folder", _fake_import)

    pw._handle_native_material_drop(window, event)

    assert seen["folder"] == str(kit)
    payload = _last_payload(window)
    assert payload["ok"] is True
    assert payload["imported"] == 3


def test_empty_drop_is_silent():
    window = _FakeWindow()
    pw._handle_native_material_drop(window, {"dataTransfer": {"files": []}})
    assert window.calls == []
