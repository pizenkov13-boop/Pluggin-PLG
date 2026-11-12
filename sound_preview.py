"""Preview of a library sound in the sound browser: a tiny localhost server for ``<audio>``.

The UI plays a file with an ordinary ``<audio src>``; the bytes never cross the pywebview
bridge (a long 808 would block it). The server

* listens on 127.0.0.1 only, on a free port, in its own daemon thread;
* answers ``GET /<token>/<url-quoted path relative to the library>``; the token is random per
  launch and is only handed to the UI by ``plg_api.list_sounds``, so no other page can read the files;
* serves only an audio file that sits inside the user's library (``relative_in_library``);
* turns a WAV that FL (and so Chromium) cannot decode as is (float, 8-bit, ...) into a PCM24
  copy under the user-data folder first (``wav_compat``; float, 8-bit, A-law / mu-law / ADPCM and
  Ogg-in-WAV are decoded properly), written aside and renamed so a second request never reads a
  half-written copy; a WAV PLG cannot decode at all (``wav_decode.decodable``) is refused with 415
  instead of being played as noise;
* answers a single ``Range`` request, which is what a media element sends.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from library_paths import AUDIO_EXTENSIONS, relative_in_library

logger = logging.getLogger("plg.sound_preview")

TOKEN = secrets.token_urlsafe(12)
_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
}
_base_url = ""
_convert_lock = threading.Lock()


def base_url() -> str:
    """``http://127.0.0.1:<port>/<token>/`` once the server runs, else ``""`` (no preview)."""
    return f"{_base_url}/{TOKEN}/" if _base_url else ""


def resolve(token: str, rel: str, root: Path) -> Path | None:
    """The audio file ``rel`` of the library, or None (wrong token, outside the library, not audio)."""
    if not hmac.compare_digest(str(token), TOKEN) or not rel:
        return None
    path = Path(root) / rel
    if path.suffix.lower() not in AUDIO_EXTENSIONS or not path.is_file():
        return None
    try:
        relative_in_library(path, Path(root))
    except ValueError:
        return None
    return path.resolve()


def _cache_dir() -> Path:
    from plg_paths import user_data_dir

    return user_data_dir() / "preview_cache"


def playable(path: Path) -> Path | None:
    """``path`` itself, a PCM24 copy of it when a browser could not decode the WAV as is, or None
    when the WAV's format cannot be decoded correctly (it would play as noise)."""
    if path.suffix.lower() != ".wav":
        return path
    try:
        from wav_compat import convert_to_pcm24, needs_fl_conversion
        from wav_decode import decodable

        if not decodable(path):
            return None
        if not needs_fl_conversion(path):
            return path
        stat = path.stat()
        key = hashlib.sha1(f"{path}|{stat.st_mtime_ns}|{stat.st_size}".encode("utf-8")).hexdigest()[:16]
        target = _cache_dir() / f"{key}.wav"
        with _convert_lock:  # two clicks on one file must not both write the copy
            if not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_suffix(".part")
                if not convert_to_pcm24(path, part):
                    part.unlink(missing_ok=True)
                    return path
                os.replace(part, target)  # whole file or nothing: never a half-written copy
        return target
    except Exception as exc:  # noqa: BLE001 - a preview is a convenience: play the original
        logger.info("Preview conversion skipped for %s (%s)", path.name, exc)
        return path


_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def _byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    match = _RANGE.match((header or "").strip())
    if not match or size <= 0:
        return None
    first, last = match.groups()
    if first == "" and last == "":
        return None
    if first == "":  # the last N bytes
        start, end = max(0, size - int(last)), size - 1
    else:
        start, end = int(first), int(last) if last else size - 1
    end = min(end, size - 1)
    return (start, end) if 0 <= start <= end else None


def _make_handler(root_provider: Callable[[], Path]):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:  # noqa: D401 - no access log
            pass

        def _refuse(self, code: int) -> None:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            try:
                parts = urlsplit(self.path).path.lstrip("/").split("/", 1)
                if len(parts) != 2:
                    return self._refuse(404)
                path = resolve(parts[0], unquote(parts[1]), root_provider())
                if path is None:
                    return self._refuse(404)
                path = playable(path)
                if path is None:
                    return self._refuse(415)  # a format that would only play as noise
                size = path.stat().st_size
                span = _byte_range(self.headers.get("Range"), size)
                start, end = span if span else (0, size - 1)
                self.send_response(206 if span else 200)
                self.send_header("Content-Type", _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                if span:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(max(0, end - start + 1)))
                self.end_headers()
                with path.open("rb") as handle:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = handle.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the media element gave up (a new click): nothing to report
            except Exception:  # noqa: BLE001 - never take the app down for a preview
                logger.exception("Sound preview failed")
                try:
                    self._refuse(500)
                except OSError:
                    pass

    return Handler


def serve(root_provider: Callable[[], Path]) -> str:
    """Start the preview server (once per process); returns its ``base_url()``."""
    global _base_url
    if _base_url:
        return base_url()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root_provider))
    httpd.daemon_threads = True
    _base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True, name="plg-sound-preview").start()
    logger.info("Sound preview on %s", _base_url)
    return base_url()
