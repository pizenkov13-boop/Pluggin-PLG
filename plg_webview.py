"""PLG pywebview entry point — owns the OS window, exposes plg_api to JS.

This is a *thin* shell. It does two things and nothing else:
  1. opens a native window (WebView2 on Windows) sized like the old app;
  2. exposes an ``Api`` object whose methods are 1-line passthroughs to plg_api,
     reachable from the React side as ``window.pywebview.api.<name>(...)``.

No app logic lives here — that's all in plg_api, which stays importable and
testable without a window.

Modes
-----
dev  (``--dev`` or ``PLG_DEV=1``): load the Vite dev server (hot reload) at
     ``PLG_DEV_URL`` (default http://localhost:5173). pywebview still injects
     ``window.pywebview`` into that page, so the bridge works the same.
prod (default / frozen exe): serve the built ``web/dist`` over a tiny localhost
     static server and load that. We use http:// (not file://) on purpose —
     Chromium blocks ES-module loading over file://, and Vite's prod build is
     ES modules. The dist folder is located via plg_paths so it works both from
     the repo and from inside a PyInstaller bundle (_MEIPASS).

plg_app.py (tkinter) remains as a legacy fallback; PLG.pyw launches this pywebview UI.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import webview

import plg_api
from app_config import load_environment, seed_env_from_bundle
from plg_paths import resource_path
from plg_log import init_session_log
from starter_kit import ensure_starter_kit

logger = logging.getLogger("plg.webview")

WINDOW_TITLE = "PLG — Pluggin'"
# Fixed plugin-sized window — matches the old tk UI (no resize / no maximize).
# Keeps the layout tight instead of floating in a huge maximized black void.
WIN_W, WIN_H = 980, 680
DEV_URL = os.environ.get("PLG_DEV_URL", "http://localhost:5173")
BG_COLOR = "#0A0A0A"


def _notify_native_material_import(window: webview.Window, result: dict) -> None:
    """Tell React to refresh its material count after a Windows Explorer drop."""
    try:
        payload = json.dumps(result, ensure_ascii=False)
        window.evaluate_js(
            "window.dispatchEvent(new CustomEvent('plg-native-material-imported', "
            f"{{ detail: {payload} }}));"
        )
    except Exception:  # noqa: BLE001 - the import has already completed
        logger.debug("Could not notify UI about native material drop", exc_info=True)


# Mirrors HomeHero.tsx's MATERIAL_EXTENSIONS — used only to tell "this drop is
# a plain file the JS side already owns" from "this drop is genuinely nothing
# usable", never to decide whether to import anything (this handler only ever
# imports folders).
_MATERIAL_EXTENSIONS = {"mid", "midi", "wav", "mp3", "ogg", "flac", "aif", "aiff"}


def _handle_native_material_drop(window: webview.Window, event: dict) -> None:
    """Import folders dropped from Windows Explorer without a file dialog.

    The WebView2 browser API hides Explorer directory paths from JavaScript.
    pywebview's native DOM drop handler supplies ``pywebviewFullPath`` instead;
    the backend can then copy the folder directly into PLG_MIDI/PLG_Sounds.

    This native handler and the browser-native JS handler
    (``collectDroppedMaterials`` in HomeHero.tsx) both fire on the *same* drop
    event. JS already owns single-file drops correctly — it picked up the file,
    counted it, and shows its own feedback. Reporting a failure here whenever
    the drop contains no folder used to stamp a false "drop a folder" error
    over a drop that had just succeeded. Only report a failure when nothing
    dropped looks like material at all, so this handler never contradicts a
    drop the JS side already accepted.
    """
    files = event.get("dataTransfer", {}).get("files", []) if isinstance(event, dict) else []
    paths = [str(file.get("pywebviewFullPath", "")).strip() for file in files if isinstance(file, dict)]
    paths = [path for path in paths if path]
    folders = [path for path in paths if Path(path).is_dir()]
    if not folders:
        has_material_file = any(
            Path(path).suffix.lstrip(".").lower() in _MATERIAL_EXTENSIONS for path in paths
        )
        if paths and not has_material_file:
            _notify_native_material_import(window, {
                "ok": False,
                "error": "Drop a MIDI or drum-kit folder.",
                "error_type": "unsupported_file",
            })
        return

    results = [plg_api.import_kit_folder(folder) for folder in dict.fromkeys(folders)]
    failed = next((result for result in results if not result.get("ok")), None)
    if failed is not None:
        _notify_native_material_import(window, failed)
        return
    last = results[-1]
    last["imported"] = sum(int(result.get("imported", 0)) for result in results)
    _notify_native_material_import(window, last)


def _install_native_material_drop(window: webview.Window) -> None:
    """Bind pywebview's native Windows folder-drop bridge after the page loads."""
    try:
        from webview.dom import DOMEventHandler

        body = window.dom.get_element("body")
        if body is None:
            logger.warning("Could not bind native material drop: document body missing")
            return
        body.on(
            "drop",
            DOMEventHandler(
                lambda event: _handle_native_material_drop(window, event),
                prevent_default=True,
                stop_propagation=True,
            ),
        )
        logger.info("Native Windows folder drop is ready")
    except Exception:  # noqa: BLE001 - normal browser drag remains available
        logger.exception("Could not install native Windows folder drop")


class Api:
    """Every public method here becomes ``window.pywebview.api.<name>`` in JS.

    Keep them as pure passthroughs. Args and return values are JSON only
    (plg_api already returns plain dicts), so pywebview marshals them as-is.
    """

    # --- status / settings / quota / library (cheap reads) -----------------
    def get_status(self) -> dict:
        return plg_api.get_status()

    def get_settings(self) -> dict:
        return plg_api.get_settings()

    def save_settings(self, updates: dict | None = None) -> dict:
        return plg_api.save_settings(updates or {})

    def random_kit(self) -> dict:
        return plg_api.random_kit()

    def clear_pinned_kit(self) -> dict:
        return plg_api.clear_pinned_kit()

    def list_sounds(self) -> dict:
        return plg_api.list_sounds()

    def pin_sound(self, lane: str, rel: str) -> dict:
        return plg_api.pin_sound(lane, rel)

    def unpin_sound(self, lane: str) -> dict:
        return plg_api.unpin_sound(lane)

    def archive_material(self, targets: list | None = None) -> dict:
        return plg_api.archive_material(targets or [])

    def check_fl_executable(self, path: str) -> dict:
        return plg_api.check_fl_executable(path)

    def get_quota(self) -> dict:
        return plg_api.get_quota()

    def scan_library(self) -> dict:
        return plg_api.scan_library()

    # --- long actions: start a job, then poll get_job(job_id) --------------
    def start_beat(
        self,
        prompt: str,
        locale: str | None = None,
        smart_plan: bool | None = None,
        midi_path: str | None = None,
        reference_audio_path: str | None = None,
        respect_uploaded_scale: bool = False,
        genre_override: str | None = None,
    ) -> dict:
        return plg_api.start_beat(
            prompt, locale, smart_plan, midi_path, reference_audio_path, respect_uploaded_scale, genre_override
        )

    def set_ui_locale(self, locale: str) -> dict:
        return plg_api.set_ui_locale(locale)

    def start_open_in_fl(self) -> dict:
        return plg_api.start_open_in_fl()

    def get_job(self, job_id: str) -> dict:
        return plg_api.get_job(job_id)

    def clear_finished_jobs(self) -> dict:
        return plg_api.clear_finished_jobs()

    # --- small/fast sync actions (no job needed) ---------------------------
    def open_in_fl(self) -> dict:
        return plg_api.open_in_fl()

    def install_fl_scripts(self) -> dict:
        return plg_api.install_fl_scripts()

    def reveal_path(self, path: str) -> dict:
        return plg_api.reveal_path(path)

    # Re-bake / producer-console actions (chaos_roll, flip_beat, clipper mode,
    # producer blueprint) are deliberately NOT exposed. The product contract is
    # one generation run producing a finished FLP; see internal notes (not included). Their UI was
    # archived on 2026-08-11; the rebake archive folder itself was deleted
    # 2026-09-23 (IP-hygiene cleanup) — history is in Git.

    def get_auth_status(self) -> dict:
        return plg_api.get_auth_status()

    def cloud_signup(
        self,
        email: str,
        password: str,
        captcha_token: str | None = None,
        accept_terms: bool = False,
        confirm_age: bool = False,
    ) -> dict:
        return plg_api.cloud_signup(email, password, captcha_token, accept_terms, confirm_age)

    def cloud_delete_account(self) -> dict:
        return plg_api.cloud_delete_account()

    def cloud_login(self, email: str, password: str) -> dict:
        return plg_api.cloud_login(email, password)

    def cloud_logout(self) -> dict:
        return plg_api.cloud_logout()

    def cloud_reset_password(self, email: str) -> dict:
        return plg_api.cloud_reset_password(email)

    def cloud_complete_password_reset(self, email: str, code: str, new_password: str) -> dict:
        return plg_api.cloud_complete_password_reset(email, code, new_password)

    def cloud_verify_email(self, user_id: str, code: str) -> dict:
        return plg_api.cloud_verify_email(user_id, code)

    def cloud_resend_verification(self, user_id: str) -> dict:
        return plg_api.cloud_resend_verification(user_id)

    def cloud_billing_status(self) -> dict:
        return plg_api.cloud_billing_status()

    def cloud_billing_checkout(
        self, price_tier: str | None = None, accept_payment_terms: bool = False
    ) -> dict:
        return plg_api.cloud_billing_checkout(price_tier, accept_payment_terms)

    def cloud_billing_checkout_link(self) -> dict:
        return plg_api.cloud_billing_checkout_link()

    def cloud_fetch_status(self) -> dict:
        return plg_api.cloud_fetch_status()

    def ping_cloud_connection(self) -> dict:
        return plg_api.ping_cloud_connection()

    def cloud_submit_feedback(
        self,
        category: str,
        message: str,
        attach_log: bool = False,
    ) -> dict:
        return plg_api.cloud_submit_feedback(category, message, attach_log=attach_log)

    # Beat rating (👍/👎) is deliberately NOT exposed. PLG's contract is to be
    # good on the first run, not to improve from accumulated user ratings.
    # Archived 2026-08-11 — see archive/rating-2026-08-11/README.md.

    def open_external_url(self, url: str) -> dict:
        return plg_api.open_external_url(url)

    def import_kit_folder(self, source: str, organize: bool = False) -> dict:
        return plg_api.import_kit_folder(source, organize=organize)

    def begin_material_import(self) -> dict:
        return plg_api.begin_material_import()

    def import_material_chunk(self, import_id: str, relative_path: str, offset: int, data_base64: str) -> dict:
        return plg_api.import_material_chunk(import_id, relative_path, offset, data_base64)

    def finish_material_import(self, import_id: str) -> dict:
        return plg_api.finish_material_import(import_id)

    def cancel_material_import(self, import_id: str) -> dict:
        return plg_api.cancel_material_import(import_id)

    def get_app_info(self) -> dict:
        return plg_api.get_app_info()

    def get_docs(self) -> dict:
        return plg_api.get_docs()

    def open_document(self, doc_id: str) -> dict:
        return plg_api.open_document(doc_id)

    def pick_folder(self) -> dict:
        try:
            if not webview.windows:
                return {"ok": False, "error": "Window not ready.", "error_type": "ui"}
            paths = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if paths and len(paths) > 0:
                return {"ok": True, "path": paths[0]}
            return {"ok": False, "cancelled": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "error_type": "ui"}

    def pick_fl_executable(self) -> dict:
        try:
            if not webview.windows:
                return {"ok": False, "error": "Window not ready.", "error_type": "ui"}
            paths = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                # pywebview only accepts wildcard patterns here (util.parse_file_type
                # requires every entry to start with "*."), so a literal-filename
                # filter like "(FL64.exe;FL.exe)" is rejected outright — it never
                # opens a dialog, it raises ValueError("... is not a valid file
                # filter"), which surfaced as a raw crash message in Settings.
                file_types=(
                    "FL Studio (*.exe)",
                    "All files (*.*)",
                ),
            )
            if paths and len(paths) > 0:
                return {"ok": True, "path": paths[0]}
            return {"ok": False, "cancelled": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "error_type": "ui"}


class _QuietHandler(SimpleHTTPRequestHandler):
    """Static handler that doesn't spam the log with every asset request."""

    def log_message(self, *_args) -> None:  # noqa: D401 - silence access log
        pass


def _serve_dist(dist_dir: Path) -> str:
    """Serve ``dist_dir`` on a background localhost server; return its URL."""
    handler = partial(_QuietHandler, directory=str(dist_dir))
    httpd = HTTPServer(("127.0.0.1", 0), handler)  # port 0 → OS picks a free port
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True, name="plg-static").start()
    logger.info("Serving built UI from %s on 127.0.0.1:%s", dist_dir, port)
    return f"http://127.0.0.1:{port}/index.html"


def _resolve_url(dev: bool) -> str:
    if dev:
        logger.info("DEV mode → %s", DEV_URL)
        return DEV_URL
    dist_dir = resource_path("web", "dist")
    index = dist_dir / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            f"Built UI not found at {index}.\n"
            "Build it first:  cd web && npm install && npm run build\n"
            "Or run dev mode:  python plg_webview.py --dev"
        )
    return _serve_dist(dist_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="PLG pywebview UI")
    parser.add_argument("--dev", action="store_true", help="Load the Vite dev server (hot reload)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | PLG webview | %(levelname)s | %(message)s")
    from app_config import tester_build

    if tester_build():
        # The tester exe never touches the network: from here on only loopback connections work.
        from local_only import apply_webview_isolation, install_socket_guard

        install_socket_guard()
        apply_webview_isolation()  # the window's own engine too: it may only reach loopback
    load_environment()
    seed_env_from_bundle()
    load_environment()
    init_session_log()
    ensure_starter_kit()

    dev = args.dev or os.environ.get("PLG_DEV") == "1"
    url = _resolve_url(dev)
    try:
        import sound_preview

        sound_preview.serve(plg_api.library_root_now)  # the sound browser's <audio> source
    except Exception:  # noqa: BLE001 - a preview is a convenience, never a reason not to start
        logger.exception("Sound preview server did not start")

    window = webview.create_window(
        WINDOW_TITLE,
        url=url,
        js_api=Api(),
        width=WIN_W,
        height=WIN_H,
        resizable=False,
        fullscreen=False,
        maximized=False,
        background_color=BG_COLOR,
    )
    window.events.loaded += lambda: _install_native_material_drop(window)
    # debug=True opens devtools and is handy while building the UI.
    webview.start(debug=dev)


if __name__ == "__main__":
    main()
