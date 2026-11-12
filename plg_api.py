"""PLG application API — UI-agnostic logic layer.

This module is the single seam between PLG's engine (backend_core, flp_writer,
fl_launch, starter_kit, beat_quota, …) and whatever front-end drives it. It does
NOT import tkinter, pywebview, or any UI toolkit, and it never touches the
engine modules' internals — it only *wraps* their existing public functions.

Design notes
------------
* Every public function returns a plain JSON-serializable ``dict`` (Path values
  are stringified). That lets a pywebview bridge expose these methods straight
  to JavaScript without translation.
* Errors are returned as ``{"ok": False, "error": <human text>, "error_type": …}``
  instead of raising, so the UI can render them. Truly unexpected exceptions are
  logged and also surface as ``ok: False``.
* Beat generation (``run_pipeline``) is a single 30–90 s blocking call inside the
  engine; we cannot see inside it without modifying backend_core, which is off
  limits. So generation reports *coarse phases* around the call, while the
  per-second "Generating · 42s" feedback is computed by the UI from the job's
  ``started_at`` (the same thing the old tk ticker did, moved client-side).
* Long-running work is exposed two ways:
    - a synchronous function (``create_beat``) — pure,
      testable, accepts an optional ``on_progress`` callback;
    - an async job (``start_beat`` → ``get_job`` polling) layered on top via a
      tiny thread-backed registry. pywebview UIs use the job/poll path so the
      window thread never blocks.

This file is the foundation for the React + pywebview front-end. plg_app.py
(tkinter) stays the shipping UI until the web UI is ready; nothing here changes
the engine, so both UIs can coexist.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import app_config
from app_config import (
    SettingsWriteError,
    app_version,
    get_auto_open_fl,
    get_samples_dir,
    has_api_key,
    is_release_build,
    settings_snapshot,
    write_env_file,
)
from backend_core import (
    count_track_notes,
    resolve_midi_dir,
    run_pipeline,
    run_pipeline_from_pattern,
)
from beat_quota import (
    BeatQuotaExceeded,
    consume_beat,
    ensure_can_consume_beat,
    format_quota_label,
    get_quota_snapshot,
)
from fl_launch import FLIntegrationError, find_fl_executable, open_beat_in_fl
from fl_detect import (
    configured_fl_executable,
    fl_version_label,
    is_valid_fl_executable,
    list_fl_installations,
    save_fl_executable,
)
from fl_setup import install_all, is_fl_bridge_ready
from library_catalog import save_catalog, scan_library as _scan_library
from library_paths import AUDIO_EXTENSIONS, MIDI_EXTENSIONS
from llm_client import format_llm_error, provider_label, safe_error_summary
from midi_ingest import has_available_midi_material
from plg_paths import app_dir, bundle_dir, projects_dir

try:
    import plg_cloud
except ImportError:  # pragma: no cover
    plg_cloud = None  # type: ignore[assignment]

logger = logging.getLogger("plg.api")

PROJECT_DIR = app_dir()
PATTERN_JSON = PROJECT_DIR / "output_pattern.json"
CATALOG_FILE = PROJECT_DIR / "sample_catalog.json"
# DOC_FILES (start_here / FL guides / legal) ship as read-only PyInstaller
# datas, not user-writable app state — they live in the bundle (_MEIPASS on
# a frozen exe, the repo root in dev), never next to the exe like .env does.
# Same PROJECT_DIR-vs-bundle_dir() mix-up that broke .env seeding.
DOCS_DIR = bundle_dir()

# Generation phases reported through on_progress (UI maps these to copy/spinner).
PHASE_PLANNING = "planning"
PHASE_GENERATING = "generating"
PHASE_FINALIZING = "finalizing"
PHASE_DONE = "done"

ProgressCb = Callable[[str, float, str], None]

# ---------------------------------------------------------------------------
# Module state (single desktop user; guarded by a lock).
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
_last_prompt: str = ""
_ui_locale: str = "en"
_session_validated = False
_library_synced_at_boot = False
# True while the boot rescan runs (it decodes every sound, seconds to minutes on a big kit): the status says so and the
# UI keeps asking until the counters are settled, instead of showing the stale ones for the rest of the session.
_library_syncing = False
_material_import_lock = threading.Lock()


@dataclass(frozen=True)
class _MaterialImportBatch:
    """One Home drag-and-drop batch: per-type staging roots, plus the flat,
    permanent library roots each staging root is consolidated into once the
    batch finishes."""

    sounds_staging: Path
    midi_staging: Path
    sounds_final: Path
    midi_final: Path


_material_import_roots: dict[str, _MaterialImportBatch] = {}


def set_ui_locale(locale: str) -> dict[str, Any]:
    global _ui_locale
    from prompt_locale import VALID_LOCALES

    _ui_locale = locale if locale in VALID_LOCALES else "en"
    return {"ok": True, "locale": _ui_locale}


def _set_last_prompt(prompt: str) -> None:
    global _last_prompt
    with _state_lock:
        _last_prompt = prompt


def _get_last_prompt() -> str:
    with _state_lock:
        return _last_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _err(message: str, error_type: str = "unknown", **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": message, "error_type": error_type}
    payload.update(extra)
    return payload


# Engine failures that reach create_beat's except-block are NOT gateway
# failures. Routing every exception through format_llm_error used to report a
# dropped 808 lane as "Invalid AI gateway API key" (the validator message
# contains the word "invalid"), which sent people to check their key while the
# real problem was an empty sample library. Classify the engine's own errors
# first; only genuinely unrecognised ones fall through to the LLM formatter.
_ENGINE_ERROR_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "midi export blocked",
        "midi_validation",
        "The generated beat did not pass the seven-lane export contract.\n\n{detail}\n\n"
        "This is usually a sample problem, not a note problem: a required lane "
        "(kick, 808_bass, hi_hats) had no usable sample, so it was dropped. "
        "Check that your PLG material library is reachable and contains a long "
        "enough 808.",
    ),
    (
        "add a midi file to start",
        "no_midi",
        "{detail}",
    ),
    (
        "midi melody is required",
        "no_midi",
        "Add MIDI and drum-kit material on Home, then try again.",
    ),
    (
        "could not be ingested",
        "midi_unreadable",
        "The selected MIDI file could not be read.\n\n{detail}\n\n"
        "Try another MIDI file from the material you added on Home.",
    ),
    (
        "no generation passed the final kick/808",
        "quality_gate",
        "Every attempt failed the final kick/808 symbiosis gate, so nothing was "
        "exported.\n\n{detail}",
    ),
    (
        # backend_core gives up after its attempts (e.g. a MIDI that holds only
        # drums on channel 10 or one repeated note leaves nothing to build a
        # beat from). Without this rule the message fell through to the
        # gateway text, which is meaningless in this product.
        "no generation succeeded",
        "midi_unusable",
        "PLG could not build a beat from this MIDI file.\n\n"
        "The file needs a melody (synth, keys, lead, pads). A MIDI that holds only "
        "drums, or a single repeated note, gives the generator nothing to build on.\n\n"
        "Try another MIDI file from the material you added on Home.",
    ),
    (
        "sample library",
        "library",
        "{detail}",
    ),
)


def _classify_engine_error(exc: Exception) -> tuple[str, str] | None:
    """Return ``(message, error_type)`` for a known engine failure, else None."""
    detail = str(exc).strip()
    if not detail:
        return None
    lowered = detail.lower()
    for needle, error_type, template in _ENGINE_ERROR_RULES:
        if needle in lowered:
            return template.format(detail=detail), error_type
    if isinstance(exc, FileNotFoundError):
        return f"A file the engine needed is missing:\n\n{detail}", "missing_file"
    return None


def _pinned_replaced_lanes(pattern: dict[str, Any]) -> list[str]:
    """Lanes where the user's random pick could not serve this track and another
    file of the library was used (never raises)."""
    record = pattern.get("plg_pinned_kit") if isinstance(pattern, dict) else None
    if not isinstance(record, dict):
        return []
    return [str(lane) for lane in record.get("replaced") or []]


def _sample_fallback_lanes(pattern: dict[str, Any]) -> list[str]:
    """Lanes whose built-in stand-in replaced the user's own files (never raises)."""
    try:
        from lane_fallback import fallback_lanes_for_user

        return fallback_lanes_for_user(pattern)
    except Exception as exc:  # noqa: BLE001 - a notice must never fail a finished beat
        logger.debug("fallback lane summary skipped: %s", exc)
        return []


def _pipeline_error(exc: Exception) -> dict[str, Any]:
    """Turn a generation exception into an honest, actionable UI error."""
    known = _classify_engine_error(exc)
    if known is not None:
        message, error_type = known
        logger.error("create_beat: engine failure (%s): %s", error_type, safe_error_summary(exc))
        return _err(message, error_type)
    return _err(format_llm_error(exc), "llm")


def _genre_gate_enabled() -> bool:
    """Escape hatch for internal QA generation of not-yet-detailed genres —
    real users always get the honest stub (see create_beat)."""
    import os

    return os.environ.get("PLG_GENRE_GATE", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _emit(on_progress: ProgressCb | None, phase: str, fraction: float, message: str) -> None:
    """Call a progress callback without ever letting it break the worker."""
    if on_progress is None:
        return
    try:
        on_progress(phase, float(fraction), message)
    except Exception:  # noqa: BLE001 - progress is best-effort, never fatal
        logger.debug("progress callback raised", exc_info=True)


def _provider_label_safe() -> str:
    try:
        return provider_label()
    except Exception:  # noqa: BLE001 - unknown provider in .env shouldn't crash status
        return "—"


def _cached_material_counts() -> tuple[int, int, int]:
    """Material counts for the status UI, including the dedicated MIDI root.

    The catalog belongs to the audio-library root, while PLG_MIDI is a sibling
    material entry point.  Include both so an existing MIDI unlocks generation
    without moving it into the sound library.
    """
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        # No catalog yet (first run, the boot scan has not finished) or an unreadable one: the files in PLG_MIDI are
        # still there and are counted live, so Home does not say "add files" over a folder that already holds them.
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        return _material_counts_from_catalog(data)
    except (OSError, ValueError, TypeError):
        return 0, 0, 0


def _material_counts_from_catalog(data: dict[str, Any]) -> tuple[int, int, int]:
    """Merge catalog MIDI with files in ``resolve_midi_dir`` without double-counting."""
    audio_total = int(data.get("audio_total", 0))
    catalog_midi = data.get("midi", [])
    if not isinstance(catalog_midi, list):
        catalog_midi = []

    known_midi: set[Path] = set()
    root_value = data.get("root")
    catalog_root = Path(root_value) if isinstance(root_value, str) and root_value else None
    if catalog_root is not None:
        for rel_path in catalog_midi:
            if isinstance(rel_path, str):
                known_midi.add((catalog_root / rel_path).resolve())

    midi_root = resolve_midi_dir()
    if midi_root.is_dir():
        for extension in MIDI_EXTENSIONS:
            known_midi.update(path.resolve() for path in midi_root.rglob(f"*{extension}") if path.is_file())

    # Only the user's own material counts: never PLG's own output, and a copy of a file is the same file.
    from library_paths import distinct_midi_files, is_service_midi

    midi_total = len(distinct_midi_files(p for p in known_midi if not is_service_midi(p)))
    catalog_total = int(data.get("total", audio_total + len(catalog_midi)))
    material_total = catalog_total + max(0, midi_total - len(catalog_midi))
    return audio_total, midi_total, material_total


def _cloud_enabled() -> bool:
    return bool(plg_cloud and plg_cloud.is_cloud_mode())


_last_reachability: dict[str, Any] | None = None


def _beat_job_running() -> bool:
    with _jobs_lock:
        return _live_job_locked("beat") is not None


def _cloud_reachability(*, force: bool = False) -> dict[str, Any]:
    """Cloud reachability for the status/offline banner.

    While a beat is being generated the answer is the last known one instead
    of a fresh probe: the run is CPU-heavy in this same process, a starved
    3-second probe would read as "no connection" mid-generation, and the
    generation request itself reports a real network failure. ``force`` (the
    banner's explicit retry) always probes.
    """
    global _last_reachability
    if not _cloud_enabled():
        return {"online": True}
    if not plg_cloud:
        return {"online": False, "error": "Cloud client unavailable.", "error_type": "config"}
    if not force and _beat_job_running():
        return dict(_last_reachability) if _last_reachability else {"online": True, "url": plg_cloud.cloud_api_url()}
    ping = plg_cloud.ping_cloud()
    _last_reachability = {
        "online": bool(ping.get("ok")),
        # A failed probe that ping_cloud still forgives (it needs 3 in a row before it says
        # "offline"): not an answer yet, so the status calls it "checking", not "online".
        "degraded": bool(ping.get("ok") and ping.get("last_probe_error")),
        "error": ping.get("error"),
        "error_type": ping.get("error_type"),
        "url": plg_cloud.cloud_api_url(),
    }
    return dict(_last_reachability)


def _network_online() -> bool:
    return bool(_cloud_reachability().get("online"))


def _sync_generated_beat_async(pattern: dict[str, Any], prompt: str, *, model: str | None = None) -> None:
    def worker() -> None:
        try:
            from plg_data_sync import sync_generated_beat

            sync_generated_beat(pattern, prompt=prompt, model=model)
        except Exception as exc:  # noqa: BLE001
            logger.debug("generated beat sync skipped: %s", exc)

    threading.Thread(target=worker, name="plg-data-sync", daemon=True).start()


def _sync_beat_event_async(pattern: dict[str, Any], event_type: str, *, extra: dict[str, Any] | None = None) -> None:
    def worker() -> None:
        try:
            from plg_data_sync import sync_beat_event

            sync_beat_event(pattern, event_type, extra=extra)
        except Exception as exc:  # noqa: BLE001
            logger.debug("beat event sync skipped: %s", exc)

    threading.Thread(target=worker, name="plg-data-event-sync", daemon=True).start()


def ping_cloud_connection() -> dict[str, Any]:
    """Explicit connectivity probe for the offline banner retry button."""
    reach = _cloud_reachability(force=True)
    with _net_lock:
        global _net_result
        _net_result = None  # the next status asks again instead of repeating an old answer
    return {
        "ok": True,
        "online": reach.get("online", False),
        "cloud_url": reach.get("url"),
        "error": reach.get("error"),
        "error_type": reach.get("error_type"),
    }


def _format_cloud_quota_label(snap: dict[str, Any]) -> str:
    days = snap.get("days_until_reset", 0)
    daily = snap.get("daily_remaining", snap.get("daily_limit", 10))
    return (
        f"{snap.get('remaining', 0)}/{snap.get('limit', 30)} beats · "
        f"{daily}/day left · resets in {days}d"
    )


# ---------------------------------------------------------------------------
# Cloud auth (Phase 1)
# ---------------------------------------------------------------------------
def get_auth_status() -> dict[str, Any]:
    if not _cloud_enabled():
        return {"ok": True, "cloud_mode": False, "signed_in": True}
    try:
        ensured = plg_cloud.ensure_session(validate=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_session failed in get_auth_status: %s", safe_error_summary(exc))
        ensured = {"ok": True, "signed_in": bool(plg_cloud.session_snapshot().get("signed_in"))}
    snap = plg_cloud.session_snapshot()
    snap["signed_in"] = bool(ensured.get("signed_in"))
    cfg = plg_cloud.fetch_auth_config()
    flags = plg_cloud.fetch_feature_flags()
    return {
        "ok": True,
        "cloud_mode": True,
        "signed_in": snap.get("signed_in", False),
        "email": snap.get("email"),
        "captcha_provider": cfg.get("captcha_provider"),
        "captcha_site_key": cfg.get("captcha_site_key"),
        "captcha_required": cfg.get("captcha_required", False),
        "feature_flags": flags.get("flags") if flags.get("ok") else {},
    }


def cloud_signup(
    email: str,
    password: str,
    captcha_token: str | None = None,
    accept_terms: bool = False,
    confirm_age: bool = False,
) -> dict[str, Any]:
    global _session_validated
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    result = plg_cloud.signup(email, password, captcha_token, accept_terms, confirm_age)
    if result.get("ok") and plg_cloud.is_signed_in():
        _session_validated = True
    return result


def cloud_delete_account() -> dict[str, Any]:
    global _session_validated
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    if not plg_cloud.is_signed_in():
        return _err("Sign in first.", "auth")
    result = plg_cloud.delete_account()
    if result.get("ok"):
        _session_validated = False
    return result


def cloud_login(email: str, password: str) -> dict[str, Any]:
    global _session_validated
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    result = plg_cloud.login(email, password)
    if result.get("ok"):
        _session_validated = True
    return result


def cloud_logout() -> dict[str, Any]:
    global _session_validated
    if not _cloud_enabled():
        return {"ok": True}
    _session_validated = False
    return plg_cloud.logout()


def cloud_reset_password(email: str) -> dict[str, Any]:
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    return plg_cloud.request_password_reset(email)


def cloud_complete_password_reset(
    email: str, code: str, new_password: str
) -> dict[str, Any]:
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    return plg_cloud.complete_password_reset(email, code, new_password)


def cloud_verify_email(user_id: str, code: str) -> dict[str, Any]:
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    return plg_cloud.verify_email(user_id, code)


def cloud_resend_verification(user_id: str) -> dict[str, Any]:
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    return plg_cloud.resend_verification(user_id)


def cloud_billing_status() -> dict[str, Any]:
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    if not plg_cloud.is_signed_in():
        return _err("Sign in first.", "auth")
    return plg_cloud.billing_status()


def cloud_billing_checkout(
    price_tier: str | None = None, accept_payment_terms: bool = False
) -> dict[str, Any]:
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    if not plg_cloud.is_signed_in():
        return _err("Sign in first.", "auth")
    result = plg_cloud.billing_checkout(
        price_tier, accept_payment_terms=accept_payment_terms
    )
    if not result.get("ok"):
        return result
    url = str(result.get("confirmation_url") or "").strip()
    if url:
        opened = open_external_url(url)
        if not opened.get("ok"):
            return {**result, "opened": False, "warning": opened.get("error")}
        return {**result, "opened": True}
    return result


def cloud_billing_checkout_link() -> dict[str, Any]:
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    if not plg_cloud.is_signed_in():
        return _err("Sign in first.", "auth")
    return plg_cloud.billing_checkout_link()


def cloud_fetch_status() -> dict[str, Any]:
    if not _cloud_enabled():
        return {"ok": False, "error": "Cloud mode is disabled.", "error_type": "config"}
    return plg_cloud.fetch_status()


def cloud_submit_feedback(
    category: str,
    message: str,
    *,
    attach_log: bool = False,
) -> dict[str, Any]:
    if not _cloud_enabled():
        return _err("Cloud mode is disabled.", "config")
    if not plg_cloud.is_signed_in():
        return _err("Sign in first.", "auth")
    log_excerpt = None
    if attach_log:
        try:
            from plg_log import read_log_tail

            log_excerpt = read_log_tail()
        except Exception:  # noqa: BLE001
            log_excerpt = None
    return plg_cloud.submit_feedback(
        category=category,
        message=message,
        attach_log=attach_log,
        log_excerpt=log_excerpt,
    )


def _tester_expired_error() -> dict[str, Any] | None:
    """The refusal a tester build gives once its date has passed (None while it is still valid)."""
    if not app_config.tester_expired():
        return None
    expiry = app_config.tester_expiry()
    return _err(
        f"This tester build stopped creating beats on {expiry.isoformat() if expiry else 'its end date'}. "
        "Ask the developer for a new one.",
        "tester_expired",
        expires=expiry.isoformat() if expiry else None,
    )


def open_external_url(url: str) -> dict[str, Any]:
    """Open HTTPS checkout or help link in the default browser."""
    if app_config.tester_build():
        return _err("Opening links is turned off in the tester build.", "local_only")
    target = (url or "").strip()
    if not target.startswith(("https://", "http://")):
        return _err("Invalid URL.", "validation")
    try:
        import webbrowser

        webbrowser.open(target, new=2)
        return {"ok": True, "url": target}
    except OSError as exc:
        return _err(str(exc), "os_error")


# ---------------------------------------------------------------------------
# Settings  (wraps app_config)
# ---------------------------------------------------------------------------
def get_settings() -> dict[str, Any]:
    """Current settings for the form. Keys are returned raw (local-only app).

    ``has_*_key`` booleans are provided so the UI can show "configured" state
    without echoing secrets if it prefers to mask them.
    """
    app_config.load_environment()
    snap = settings_snapshot()
    return {
        "ok": True,
        "provider": snap["provider"],
        "openrouter_key": snap.get("openrouter_key", ""),
        "openrouter_base_url": snap.get("openrouter_base_url", ""),
        "openrouter_model": snap.get("openrouter_model", ""),
        "gemini_key": snap["gemini_key"],
        "anthropic_key": snap["anthropic_key"],
        "has_openrouter_key": bool(snap.get("openrouter_key", "")),
        "has_gemini_key": bool(snap["gemini_key"]),
        "has_anthropic_key": bool(snap["anthropic_key"]),
        "samples_dir": snap["samples_dir"],
        "gemini_model": snap["gemini_model"],
        "claude_model": snap["claude_model"],
        "auto_open_fl": snap["auto_open_fl"] == "true",
        "cloud_mode": snap["cloud_mode"] == "true",
        "cloud_url": snap["cloud_url"],
        "release_build": is_release_build(),
        # The user's manual override, verbatim, even if it's currently
        # broken — never a different auto-detected install silently swapped
        # in. Falls back to the auto-detected newest path only when no
        # override is saved at all (see fl_detect.resolve_fl_executable).
        "fl_executable": configured_fl_executable() or str(find_fl_executable() or ""),
        "fl_installations": list_fl_installations(),
    }


def _pinned_kit_names() -> dict[str, str]:
    """Lane -> file name of the kit the user pinned with Random (never raises)."""
    try:
        from pinned_kit import pinned_names

        return pinned_names(Path(get_samples_dir()))
    except Exception as exc:  # noqa: BLE001 - a status field must never fail the status
        logger.debug("pinned kit names skipped: %s", exc)
        return {}


def _busy_with_job_or_import() -> bool:
    with _jobs_lock:
        busy = any(job.status == "running" for job in _jobs.values())
    with _material_import_lock:
        return busy or bool(_material_import_roots)


def _pin_state(root: Path) -> dict[str, Any]:
    from pinned_kit import pinned_names, valid_explicit

    return {"pinned": pinned_names(root), "explicit": sorted(valid_explicit(root))}


def list_sounds() -> dict[str, Any]:
    """Every sound of the user's library for the sound browser: ``[{rel, name}]`` by file name.

    Read from the catalog (no audio is opened, so 144 or 4000 files cost the same few
    milliseconds). Files the quality filter set aside are listed too: it is the user's
    folder, and an explicit pick is theirs to make."""
    try:
        catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        catalog = {}
    root = Path(catalog.get("root") or get_samples_dir())
    seen: set[str] = set()
    rels: list[str] = []
    for key in ("audio", "rejected_audio"):
        groups = catalog.get(key)
        if not isinstance(groups, dict):
            continue
        for files in groups.values():
            for entry in files if isinstance(files, list) else []:
                rel = entry if isinstance(entry, str) else (entry.get("path") if isinstance(entry, dict) else None)
                if isinstance(rel, str) and rel not in seen and Path(rel).suffix.lower() in AUDIO_EXTENSIONS:
                    seen.add(rel)
                    rels.append(rel)
    rels.sort(key=lambda rel: (Path(rel).name.casefold(), rel.casefold()))
    from wav_decode import decodable

    # "usable": PLG can read the file. One it cannot (an unknown WAV format) is shown but cannot be played or picked.
    sounds = [{"rel": rel, "name": Path(rel).name, "usable": decodable(root / rel)} for rel in rels]
    import sound_preview

    return {"ok": True, "sounds": sounds, "preview_base": sound_preview.base_url(), **_pin_state(root)}


def pin_sound(lane: str, rel: str) -> dict[str, Any]:
    """Put one library file in one cell (Kick, 808, Snare, Snare Layer, Clap, Hi-Hat).

    The pick is the user's own: the gates yield to it when the beat is made. Refused only
    for a file that is not there, outside the library, or that FL could not read."""
    from pinned_kit import pin_by_hand

    if _busy_with_job_or_import():
        return _err("Wait for the running job or import to finish first.", "busy")
    root = _library_root_for_pins()
    messages = {
        "unknown_lane": "That cell does not exist.",
        "not_found": "That sound is no longer in your library.",
        "outside_library": "That file is not in your sound library.",
        "unreadable": "PLG cannot read that file.",
        "unsupported_format": "PLG cannot use that file: its audio format is not supported.",
    }
    code = pin_by_hand(lane, (root / rel) if rel else Path(""), root)
    if code:
        return _err(messages.get(code, code), code)
    return {"ok": True, **_pin_state(root)}


def unpin_sound(lane: str) -> dict[str, Any]:
    """Empty one cell: that lane is matched automatically again."""
    from pinned_kit import unpin

    if _busy_with_job_or_import():
        return _err("Wait for the running job or import to finish first.", "busy")
    root = _library_root_for_pins()
    try:
        unpin(lane, root)
    except OSError as exc:
        return _err(str(exc), "settings_save_failed")
    return {"ok": True, **_pin_state(root)}


def library_root_now() -> Path:
    """The library folder the catalog was scanned from (what the sound browser lists and plays)."""
    return _library_root_for_pins()


def _library_root_for_pins() -> Path:
    try:
        catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        catalog = {}
    return Path(catalog.get("root") or get_samples_dir())


def _pinned_explicit_lanes() -> list[str]:
    """Lanes whose pin the user chose by hand in the sound browser (never raises)."""
    try:
        from pinned_kit import valid_explicit

        return sorted(valid_explicit(Path(get_samples_dir())))
    except Exception as exc:  # noqa: BLE001 - a status field must never fail the status
        logger.debug("pinned explicit lanes skipped: %s", exc)
        return []


def random_kit() -> dict[str, Any]:
    """"Random from library": pin a random usable file of the user's own library
    for each of 808, kick, snare, snare layer, clap and hats.

    A lane with no usable file keeps its earlier pin and comes back in ``kept``.
    The pins last until the next Random or ``clear_pinned_kit``; the files are
    only read. Refused while a job or an import runs."""
    from pinned_kit import pick_random_kit, pinned_names, save_pins, valid_explicit, valid_pins

    if _busy_with_job_or_import():
        return _err("Wait for the running job or import to finish first.", "busy")
    try:
        catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        catalog = {}
    if not int(catalog.get("audio_total", 0) or 0):
        return _err("Add sounds to your library first.", "no_library")
    root = Path(catalog.get("root") or get_samples_dir())
    previous = valid_pins(root)
    picks, kept = pick_random_kit(catalog, root, previous=previous)
    pins = {lane: str(path) for lane, path in previous.items()}
    pins.update({lane: str(path) for lane, path in picks.items()})
    # A lane Random could not redraw keeps its pin, and keeps being the user's own choice if it was.
    explicit = {lane for lane in valid_explicit(root) if lane not in picks}
    try:
        save_pins(pins, explicit)
    except OSError as exc:
        return _err(str(exc), "settings_save_failed")
    return {
        "ok": True,
        "picked": {lane: path.name for lane, path in picks.items()},
        "kept": kept,
        "pinned": pinned_names(root),
        "explicit": sorted(valid_explicit(root)),
    }


def clear_pinned_kit() -> dict[str, Any]:
    """"Auto": back to the automatic sound matching."""
    from pinned_kit import clear_pins

    try:
        clear_pins()
    except OSError as exc:
        return _err(str(exc), "settings_save_failed")
    return {"ok": True}


def archive_material(targets: list[str] | None = None) -> dict[str, Any]:
    """Move the user's MIDI and/or sounds into ``PLG_Archive/<date-time>/``.

    ``targets`` is any of ``"midi"`` (PLG_MIDI) and ``"library"`` (PLG_Library
    and PLG_Sounds, whichever exist). Files are renamed into the archive, never
    deleted, and every one keeps its bytes; if a move fails they all go back
    (``material_archive``). Refused while a job or an import is running, and
    for a folder that is not one of the default PLG_* folders, so a library
    pointed at the user's own directory is never relocated. The catalog is
    rescanned afterwards so the counters match the folders."""
    from library_paths import DEFAULT_MIDI_DIR, DEFAULT_LIBRARY_DIR, LEGACY_LIBRARY_DIR, library_roots
    from material_archive import ArchiveError, archive_folders

    wanted = {str(target).strip().lower() for target in (targets or [])}
    if not wanted or not wanted <= {"midi", "library"}:
        return _err("Choose what to clear: midi, library or both.", "validation")
    with _jobs_lock:
        busy = any(job.status == "running" for job in _jobs.values())
    with _material_import_lock:
        busy = busy or bool(_material_import_roots)
    if busy:
        return _err("Wait for the running job or import to finish first.", "busy")

    library_primary = Path(get_samples_dir()).resolve()
    midi_dir = resolve_midi_dir().resolve()
    if "library" in wanted and library_primary not in {DEFAULT_LIBRARY_DIR.resolve(), LEGACY_LIBRARY_DIR.resolve()}:
        return _err(f"Your sounds are in {library_primary}, not in PLG_Library or PLG_Sounds. Nothing was moved.", "custom_folder")
    if "midi" in wanted and midi_dir != DEFAULT_MIDI_DIR.resolve():
        return _err(f"Your MIDI folder is {midi_dir}, not PLG_MIDI. Nothing was moved.", "custom_folder")

    library_folders = library_roots(library_primary)
    sources: list[tuple[str, Path, str]] = []
    if "midi" in wanted:
        sources.append(("midi", midi_dir, "MIDI"))
        if "library" not in wanted:
            # MIDI kept inside a library folder (the older layout) counts as MIDI too.
            sources.extend(("midi", root / "midi", f"MIDI/{root.name}_midi") for root in library_folders)
    if "library" in wanted:
        sources.extend(("library", root, f"Library/{root.name}") for root in library_folders)
    try:
        moved = archive_folders(sources)
    except ArchiveError as exc:
        return _err(str(exc), exc.error_type, left_in_archive=list(exc.left_in_archive))
    try:
        save_catalog(_scan_library(get_samples_dir()), CATALOG_FILE)
    except (OSError, FileNotFoundError) as exc:
        logger.warning("catalog rescan after archive failed: %s", safe_error_summary(exc))
    audio_total, midi_total, material_total = _cached_material_counts()
    return {
        "ok": True,
        **moved,
        "counts": {"audio": audio_total, "midi": midi_total, "material": material_total},
    }


def check_fl_executable(path: str) -> dict[str, Any]:
    """Real on-disk existence check for Settings' live FL-path status.

    Deliberately its own tiny call rather than folded into get_settings()/
    save_settings(): the UI calls this on every debounced keystroke, and it
    must stay a cheap, side-effect-free filesystem check — no profile write,
    no installation scan.
    """
    return {"ok": True, "exists": is_valid_fl_executable(path)}


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Persist settings to .env. Accepts a partial dict; merges with current.

    Mirrors the validation in the old SettingsDialog: the *active* provider must
    have a matching API key after the merge when running in local mode.
    """
    global _session_validated
    current = get_settings()
    provider = str(updates.get("provider", current["provider"]) or "openrouter").strip().lower()
    openrouter_key = str(updates.get("openrouter_key", current.get("openrouter_key", ""))).strip()
    openrouter_base_url = str(updates.get("openrouter_base_url", current.get("openrouter_base_url", ""))).strip()
    openrouter_model = str(updates.get("openrouter_model", current.get("openrouter_model", ""))).strip()
    gemini_key = str(updates.get("gemini_key", current["gemini_key"])).strip()
    anthropic_key = str(updates.get("anthropic_key", current["anthropic_key"])).strip()
    samples_dir = str(updates.get("samples_dir", current["samples_dir"])).strip()

    auto_open = updates.get("auto_open_fl", current["auto_open_fl"])
    auto_open_str = "true" if (auto_open if isinstance(auto_open, bool) else str(auto_open).lower() == "true") else "false"

    target_cloud = current.get("cloud_mode", _cloud_enabled())
    if "cloud_mode" in updates:
        target_cloud = bool(updates["cloud_mode"])
    cloud_url = str(updates.get("cloud_url", current.get("cloud_url", ""))).strip()
    was_cloud = _cloud_enabled()

    env_common: dict[str, str] = {
        "PLG_SAMPLES_DIR": samples_dir,
        "PLG_AUTO_OPEN_FL": auto_open_str,
        "PLG_CLOUD_MODE": "true" if target_cloud else "false",
    }
    if cloud_url or "cloud_url" in updates:
        env_common["PLG_CLOUD_URL"] = cloud_url or (plg_cloud.cloud_api_url() if plg_cloud else "http://127.0.0.1:8787")

    if target_cloud:
        try:
            write_env_file(env_common)
        except SettingsWriteError as exc:
            return _err(str(exc), "settings_save_failed")
        if "fl_executable" in updates:
            raw_fl = str(updates.get("fl_executable") or "").strip()
            try:
                save_fl_executable(raw_fl or None)
            except (OSError, FileNotFoundError) as exc:
                return _err(str(exc), "validation")
        if target_cloud != was_cloud:
            _session_validated = False
        return {
            "ok": True,
            "settings": get_settings(),
            "cloud_mode_changed": target_cloud != was_cloud,
        }

    if provider in ("openrouter", "or") and not openrouter_key:
        return _err("Add an OpenRouter/aitunnel API key to continue in local mode.", "validation")
    if provider in ("anthropic", "claude") and not anthropic_key:
        return _err("Add an Anthropic API key or switch the provider to openrouter.", "validation")
    if provider in ("gemini", "google") and not gemini_key:
        return _err("Add a Gemini API key or switch the provider to openrouter.", "validation")

    env_updates: dict[str, str] = {
        **env_common,
        "PLG_LLM_PROVIDER": provider,
        "OPENROUTER_API_KEY": openrouter_key,
        "OPENROUTER_BASE_URL": openrouter_base_url,
        "OPENROUTER_MODEL": openrouter_model,
        "GEMINI_API_KEY": gemini_key,
        "ANTHROPIC_API_KEY": anthropic_key,
    }
    if "gemini_model" in updates:
        env_updates["GEMINI_MODEL"] = str(updates["gemini_model"]).strip()
    if "claude_model" in updates:
        env_updates["PLG_CLAUDE_MODEL"] = str(updates["claude_model"]).strip()

    if "fl_executable" in updates:
        raw_fl = str(updates.get("fl_executable") or "").strip()
        try:
            save_fl_executable(raw_fl or None)
        except OSError as exc:
            return _err(str(exc), "validation")

    try:
        write_env_file(env_updates)
    except SettingsWriteError as exc:
        return _err(str(exc), "settings_save_failed")
    if was_cloud and not target_cloud:
        _session_validated = False
    return {
        "ok": True,
        "settings": get_settings(),
        "cloud_mode_changed": was_cloud != target_cloud,
    }


# ---------------------------------------------------------------------------
# Status & quota
# ---------------------------------------------------------------------------
def get_quota() -> dict[str, Any]:
    """Beat quota snapshot plus the short label the status bar shows."""
    if _cloud_enabled() and plg_cloud.is_signed_in():
        try:
            me = plg_cloud.fetch_me()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cloud quota fetch failed, falling back to local snapshot: %s",
                safe_error_summary(exc),
            )
            me = {"ok": False}
        if me.get("ok") and isinstance(me.get("quota"), dict):
            snap = dict(me["quota"])
            snap["label"] = _format_cloud_quota_label(snap)
            snap["skipped"] = False
            return snap
        # Signed into a cloud account, but the real quota couldn't be
        # fetched (network error, 5xx, whatever _cloud_get failed on). This
        # used to fall through to get_quota_snapshot() below — the LOCAL,
        # no-cloud-account quota model (a flat 30 beats / 30 days, no
        # concept of trial/grace/status). For a signed-in cloud user that
        # isn't "unavailable", it's actively wrong: a brand-new trial
        # account with 1 free beat would show "30/30" as if it were a paid
        # subscriber on day one. Report it as unavailable instead — the UI
        # already has a real "quota unavailable, retry" state for this.
        logger.warning(
            "cloud quota unavailable, not substituting the local quota model: %s",
            me.get("error"),
        )
        return {
            "used": 0,
            "limit": 0,
            "remaining": 0,
            "days_until_reset": 0,
            "skipped": True,
            "label": "",
        }
    snap = get_quota_snapshot()
    snap["label"] = format_quota_label(snap)
    return snap


def _read_pattern_field(key: str, default: Any = None) -> Any:
    try:
        data = json.loads(PATTERN_JSON.read_text(encoding="utf-8"))
        return data.get(key, default)
    except (OSError, ValueError, TypeError):
        return default


def _read_sample_picks() -> dict[str, str]:
    picks = _read_pattern_field("plg_sample_picks")
    return picks if isinstance(picks, dict) else {}


def _read_export_meta() -> dict[str, Any]:
    stem_files = _read_pattern_field("plg_stem_files", [])
    if not isinstance(stem_files, list):
        stem_files = []
    chop = _read_pattern_field("plg_sample_chop")
    quality = _read_pattern_field("plg_quality")
    if not isinstance(quality, dict):
        quality = {}
    reward_meta: dict[str, Any] = {}
    try:
        from beat_reward import model_status

        reward_meta = model_status()
    except ImportError:
        reward_meta = {}
    meta = {
        "bpm": _read_pattern_field("bpm"),
        "style": _read_pattern_field("style"),
        "stem_session": _read_pattern_field("plg_stem_session"),
        "stem_files": [Path(str(p)).name for p in stem_files],
        "sample_chop": chop if isinstance(chop, dict) else None,
        "clipper_mode": bool(
            _read_pattern_field("plg_clipper_mode", False)
            or _read_pattern_field("plg_filth_mode", False)
        ),
        "beat_id": _read_pattern_field("plg_beat_id"),
        "beat_rating": _read_pattern_field("plg_rating"),
        "quality_score": quality.get("score"),
        # Rating UI is always on when a beat exists; sklearn only gates local ML.
        "reward_learning": True,
        "reward_ml_enabled": bool(reward_meta.get("enabled")),
        "reward_model_ready": bool(reward_meta.get("model_ready")),
        "reward_ratings": reward_meta.get("ratings"),
        "reward_thumbs_up": reward_meta.get("thumbs_up"),
        "reward_thumbs_down": reward_meta.get("thumbs_down"),
        "reward_min_samples": reward_meta.get("min_samples"),
        "reward_progress": reward_meta.get("progress"),
        "reward_favorite_genre": reward_meta.get("favorite_genre"),
    }
    return meta


def _set_library_syncing() -> None:
    """Mark the boot scan as running before its thread starts, so the very first status already says so."""
    global _library_syncing
    _library_syncing = True


def _sync_library_catalog_now() -> None:
    """Full rescan + save, run off the request thread — see ``get_status``.

    ``scan_library`` runs the audio-health gate (``audio_rejection_reason``)
    per file, which decodes each one to read its duration. On a real kit
    (hundreds of one-shots) that's seconds-to-minutes of work — fine for an
    explicit "scan library"/import action, but it must never sit in the path
    of a status call the UI is awaiting to paint the home screen.
    """
    global _library_syncing
    _library_syncing = True
    try:
        save_catalog(_scan_library(get_samples_dir()), CATALOG_FILE)
    except OSError as exc:
        logger.warning("boot library sync failed: %s", safe_error_summary(exc))
    finally:
        _library_syncing = False


# --- the network half of the status never makes the app wait --------------------
# get_status used to run, one after the other, the token refresh (30 s), /v1/me (12 s),
# the reachability ping and a second /v1/me for the quota, each with a DoH retry: with
# the server down Home sat blank for 1-2 minutes and showed no buttons. Now those calls
# run once, in ONE background thread (a second status call never starts a second one),
# and get_status waits for it at most ``_NET_BUDGET_S``. If it is not done the status
# is built from what is known locally and says ``network_checking`` (the UI asks again
# in a few seconds); when it is done the next status carries the real answer.
_NET_BUDGET_S = 1.5
_net_lock = threading.Lock()
_net_thread: threading.Thread | None = None
_net_result: dict[str, Any] | None = None


def _network_probe() -> None:
    """The slow, server-bound part of the status. Runs in the probe thread only."""
    global _net_result, _session_validated
    # The cheap question first: when the server does not answer, the token refresh and the
    # two /v1/me calls would each wait out their own timeout (and a DoH retry) for nothing.
    reach = _cloud_reachability()
    unavailable = {"used": 0, "limit": 0, "remaining": 0, "days_until_reset": 0, "skipped": True, "label": ""}
    auth = plg_cloud.session_snapshot()
    signed_in = bool(auth.get("signed_in"))
    quota = unavailable
    if reach.get("online") and not reach.get("degraded"):
        try:
            validate = not _session_validated
            ensured = plg_cloud.ensure_session(validate=validate)
            if validate:
                _session_validated = True
            signed_in = bool(ensured.get("signed_in"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ensure_session failed: %s", safe_error_summary(exc))
        auth = plg_cloud.session_snapshot()
        quota = get_quota()
    auth["signed_in"] = signed_in
    with _net_lock:
        _net_result = {"signed_in": signed_in, "auth": auth, "reach": reach, "quota": quota}


def _network_status() -> tuple[dict[str, Any] | None, bool]:
    """``(result, checking)``: the probe's answer, or ``(None-or-stale, True)`` when it
    is still running after the budget."""
    global _net_thread
    with _net_lock:
        thread = _net_thread
        if thread is None or not thread.is_alive():
            thread = threading.Thread(target=_network_probe, name="plg-status-probe", daemon=True)
            _net_thread = thread
            thread.start()
    thread.join(_NET_BUDGET_S)
    with _net_lock:
        done = not thread.is_alive()
        return (dict(_net_result) if _net_result else None), (not done)


def get_status() -> dict[str, Any]:
    """Cheap app status for the header/status bar (no filesystem walk, after boot).

    ``material_total`` normally comes from the cached catalog, refreshed only
    by an explicit scan/import. If files land in the library between app runs
    (a kit dropped in earlier, moved by hand, whatever), that cache can be
    stale the moment the app opens. So the first call each process kicks off
    one real resync in the background — this call still returns immediately
    on whatever's cached, but the badge self-corrects within moments instead
    of staying wrong for the rest of the session.
    """
    global _session_validated, _library_synced_at_boot
    app_config.load_environment()
    if not _library_synced_at_boot:
        _library_synced_at_boot = True
        _set_library_syncing()
        threading.Thread(target=_sync_library_catalog_now, daemon=True).start()
    fl_exe = find_fl_executable()
    export_meta = _read_export_meta() if PATTERN_JSON.is_file() else {}
    cloud = _cloud_enabled()
    signed_in = True
    auth: dict[str, Any] = {}
    checking = False
    quota: dict[str, Any] | None = None
    if cloud:
        net, checking = _network_status()
        if net is not None and not checking:
            signed_in, auth, reach, quota = net["signed_in"], net["auth"], net["reach"], net["quota"]
        elif net is not None:  # a probe is running again: the last answer, flagged as stale
            signed_in, auth, reach, quota = net["signed_in"], net["auth"], net["reach"], net["quota"]
        else:  # first call, the server is slow: what is known locally, no network verdict yet
            auth = plg_cloud.session_snapshot()
            signed_in = bool(auth.get("signed_in"))
            reach = {"online": None, "url": plg_cloud.cloud_api_url()}
            quota = {"used": 0, "limit": 0, "remaining": 0, "days_until_reset": 0, "skipped": True, "label": ""}
    else:
        reach = _cloud_reachability()
    audio_total, midi_total, material_total = _cached_material_counts()
    return {
        "ok": True,
        "cloud_mode": cloud,
        "cloud_url": reach.get("url") if cloud else None,
        "release_build": is_release_build(),
        "signed_in": signed_in,
        "auth_email": auth.get("email"),
        "app_version": app_version(),
        # None while the first probe is still running: the UI treats it as "not offline (yet)".
        "network_online": None if reach.get("online") is None else bool(reach.get("online")),
        "network_checking": bool(checking or (cloud and reach.get("degraded"))),
        "cloud_error": reach.get("error") if cloud and reach.get("online") is False else None,
        "cloud_error_type": reach.get("error_type") if cloud and reach.get("online") is False else None,
        "local_mode_available": has_api_key() if cloud else False,
        "provider": _provider_label_safe() if not cloud else "PLG Cloud",
        "has_api_key": signed_in if cloud else (has_api_key() or app_config.offline_dev()),
        # Development only (PLG_OFFLINE_DEV): the UI shows a badge so nobody mistakes it for a real session.
        "offline_dev": app_config.offline_dev(),
        # Tester build (a flag stamped into that exe alone): the UI shows a badge and, after the date, a message.
        "tester_build": app_config.tester_build(),
        "tester_expires": (app_config.tester_expiry().isoformat() if app_config.tester_build() and app_config.tester_expiry() else None),
        "tester_days_left": app_config.tester_days_left(),
        "tester_expired": app_config.tester_expired(),
        "fl_bridge_ready": is_fl_bridge_ready(PROJECT_DIR),
        "fl_installed": fl_exe is not None,
        "fl_version": fl_version_label(fl_exe),
        "fl_executable": str(fl_exe) if fl_exe else None,
        "beat_ready": PATTERN_JSON.is_file(),
        "auto_open_fl": get_auto_open_fl(),
        "library_audio_total": audio_total,
        "library_midi_total": midi_total,
        "material_total": material_total,
        "library_syncing": _library_syncing,
        "last_prompt": _get_last_prompt(),
        "pinned_kit": _pinned_kit_names(),
        "pinned_explicit": _pinned_explicit_lanes(),
        "sample_picks": _read_sample_picks(),
        "quota": quota if quota is not None else get_quota(),
        **export_meta,
    }


def reveal_path(path: str) -> dict[str, Any]:
    """Open a folder in the OS shell (Windows Explorer, Finder, xdg-open)."""
    target = Path((path or "").strip())
    if not target.exists():
        return _err(f"Path not found: {path}", "not_found")
    try:
        import os
        import sys

        reveal = target if target.is_dir() else target.parent
        if sys.platform == "win32":
            os.startfile(reveal)  # noqa: S606 — intentional desktop shell open
        elif sys.platform == "darwin":
            import subprocess

            subprocess.run(["open", str(reveal)], check=False)
        else:
            import subprocess

            subprocess.run(["xdg-open", str(reveal)], check=False)
        return {"ok": True, "path": str(reveal)}
    except OSError as exc:
        return _err(str(exc), "os_error")


def open_path(path: str) -> dict[str, Any]:
    """Open a file with the OS default app (e.g. Notepad / browser for .md)."""
    target = Path((path or "").strip())
    if not target.is_file():
        return _err(f"File not found: {path}", "not_found")
    try:
        import os
        import sys

        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess

            subprocess.run(["open", str(target)], check=False)
        else:
            import subprocess

            subprocess.run(["xdg-open", str(target)], check=False)
        return {"ok": True, "path": str(target)}
    except OSError as exc:
        return _err(str(exc), "os_error")


# ---------------------------------------------------------------------------
# Beat rating (local reward model) — ARCHIVED 2026-08-11
#
# Product decision: PLG must be right on the first generation, not learn from
# accumulated 👍/👎. The rating UI was archived (archive/rating-2026-08-11/) and
# these entry points were removed from plg_webview.Api and web/src/api.ts.
# `beat_reward.py` itself stays: beat_quality/generation_variety/style_corpus
# import it defensively, and it is inert without ratings (reward_enabled() is
# False when scikit-learn is absent). Do not re-expose these.
# ---------------------------------------------------------------------------
def _archived_record_beat_rating(rating: int) -> dict[str, Any]:
    """Store 👍/👎 for the current beat and retrain the reward ranker when possible."""
    import uuid

    from beat_reward import model_status, record_rating

    if not PATTERN_JSON.is_file():
        return _err("No beat to rate yet.", "no_beat")

    try:
        pattern = json.loads(PATTERN_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return _err(f"Could not read beat: {exc}", "io")

    if not isinstance(pattern, dict):
        return _err("Invalid beat file.", "invalid")

    if not pattern.get("plg_beat_id"):
        pattern["plg_beat_id"] = uuid.uuid4().hex[:16]

    try:
        result = record_rating(pattern, int(rating))
    except Exception as exc:  # noqa: BLE001
        logger.error("record_beat_rating failed type=%s", type(exc).__name__)
        return _err(str(exc), "reward")

    pattern["plg_rating"] = result["rating"]
    try:
        PATTERN_JSON.write_text(
            json.dumps(pattern, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        return _err(f"Could not save rating: {exc}", "io")

    _sync_beat_event_async(
        pattern,
        "liked" if result["rating"] > 0 else "disliked",
        extra={"local_total_ratings": result["total_ratings"]},
    )
    status = model_status()
    return {
        "ok": True,
        "rating": result["rating"],
        "total_ratings": result["total_ratings"],
        "model_trained": result["model_trained"],
        "model_ready": status.get("model_ready"),
        "favorite_genre": status.get("favorite_genre"),
        "progress": status.get("progress"),
        "min_samples": status.get("min_samples"),
        "beat_id": pattern.get("plg_beat_id"),
    }


def _archived_beat_reward_status() -> dict[str, Any]:
    from beat_reward import model_status

    payload = {"ok": True, **model_status()}
    if plg_cloud and plg_cloud.is_signed_in():
        try:
            cloud = plg_cloud.cloud_reward_status()
            if cloud.get("ok"):
                payload["cloud"] = cloud
        except Exception as exc:  # noqa: BLE001
            logger.debug("cloud reward status skipped: %s", exc)
    return payload


# ---------------------------------------------------------------------------
# Producer console (chaos roll / flip beat / clipper mode / producer blueprint)
# — REMOVED 2026-08-11.
#
# These were post-generation mutations of an already-finished beat: the Re-bake
# family under a different name. The product contract is one generation run
# producing a finished FLP (internal notes (not included)). Their UI lives in
# `archive/unmounted-ui-2026-08-11/`. Their backend module (`pattern_tools.py`)
# and its tests lived in `archive/rebake/`, deleted 2026-09-23 as an IP-hygiene
# cleanup — history is in
# Git. Do not restore any of it.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------
def scan_library() -> dict[str, Any]:
    """Fresh scan of PLG's internal material folder; persists its catalog.

    Heavier than get_status (walks the tree), so the UI calls it explicitly —
    after a material import — not on every poll.
    """
    samples_dir = get_samples_dir()
    try:
        catalog = _scan_library(samples_dir)
    except FileNotFoundError:
        return _err(f"Library folder not found: {samples_dir}", "not_found", samples_dir=str(samples_dir))
    save_catalog(catalog, CATALOG_FILE)
    audio_total, midi_total, material_total = _material_counts_from_catalog(catalog)
    return {
        "ok": True,
        "root": catalog["root"],
        "total": catalog["total"],
        "audio_total": audio_total,
        "midi_total": midi_total,
        "material_total": material_total,
        "filtered_audio_total": catalog.get("filtered_audio_total", catalog.get("audio_total", 0)),
        "rejected_audio": catalog.get("rejected_audio", {}),
        "quarantined_audio": catalog.get("quarantined_audio", {}),
        "audio": catalog.get("audio", {}),
        "midi": catalog.get("midi", []),
        "presets": catalog.get("presets", []),
        "projects": catalog.get("projects", []),
        "banks": catalog.get("banks", []),
        "plugins": catalog.get("plugins", []),
    }


def preview_kit(prompt: str) -> dict[str, Any]:
    """Match kick/snare/clap/808/hats/melody from the library without generating a beat."""
    prompt = (prompt or "").strip()
    if not prompt:
        return _err("Describe your beat first.", "validation")

    app_config.load_environment()
    samples_dir = get_samples_dir()
    try:
        catalog = _scan_library(samples_dir)
    except FileNotFoundError:
        return _err(f"Library folder not found: {samples_dir}", "not_found", samples_dir=str(samples_dir))

    from starter_kit import ensure_starter_kit, resolve_track_samples

    ensure_starter_kit()
    picks = resolve_track_samples(catalog, library_root=samples_dir, prompt=prompt)
    return {
        "ok": True,
        "audio_total": catalog.get("audio_total", 0),
        "picks": {track: {"name": path.name, "path": str(path)} for track, path in picks.items()},
    }


def detect_midi_genre(midi_path: str) -> dict[str, Any]:
    """Infer the genre lane from an uploaded MIDI file's own musical content
    (density, interval shape, scale, tempo — see midi_genre_detect.py),
    WITHOUT running any generation. Fast/synchronous — meant to be called
    right after the file is attached, so the UI can show the detected genre
    and let the user confirm or override it BEFORE hitting "Create beat"
    (2026-07: MIDI-driven genre selection can fully override the text
    prompt, so it must never be a silent decision).

    Returns {"ok": True, "genre": str | None, "confidence": float | None}.
    ``genre`` is None when the signal is too thin to trust (too few notes,
    ambiguous fit) — the UI should then fall back to manual genre selection,
    never a forced guess. Never errors on a bad/unreadable file — just
    reports ``genre: None``.
    """
    path_str = (midi_path or "").strip()
    if not path_str:
        return _err("No MIDI file given.", "validation")

    app_config.load_environment()
    path = Path(path_str)
    if not path.is_file():
        return _err("MIDI file not found.", "not_found")

    try:
        from midi_genre_detect import DETAILED_GENRES, detect_genre_from_midi, read_tempo_bpm
        from midi_ingest import read_midi_notes

        notes = read_midi_notes(path)
        tempo_bpm = read_tempo_bpm(path)
        hit = detect_genre_from_midi(notes, tempo_bpm)
    except Exception as exc:  # noqa: BLE001 — detection must never error the UI
        logger.info("detect_midi_genre failed for %s: %s", path.name, exc)
        return {"ok": True, "genre": None, "confidence": None, "available_genres": []}

    if hit is None:
        return {"ok": True, "genre": None, "confidence": None, "available_genres": sorted(DETAILED_GENRES)}
    genre, confidence = hit
    return {"ok": True, "genre": genre, "confidence": confidence, "available_genres": sorted(DETAILED_GENRES)}


# ---------------------------------------------------------------------------
# Beat generation  (wraps backend_core.run_pipeline + beat_quota)
# ---------------------------------------------------------------------------
def create_beat(
    prompt: str,
    on_progress: ProgressCb | None = None,
    *,
    locale: str | None = None,
    smart_plan: bool | None = None,
    regenerate: bool = False,
    midi_path: str | None = None,
    reference_audio_path: str | None = None,
    respect_uploaded_scale: bool = False,
    genre_override: str | None = None,
) -> dict[str, Any]:
    """Generate a beat from MIDI material. Synchronous (blocks 30–90 s).

    Checks the quota first, runs the engine pipeline, consumes one beat on
    success — the same order the old tk UI used. Does NOT auto-open FL; the
    returned ``auto_open_fl`` flag lets the caller decide.

    ``midi_path`` — path to a user-uploaded .mid/.midi file whose melody
    becomes the core motif instead of the LLM's own (see midi_ingest.
    ingest_uploaded_midi). ``reference_audio_path`` — the audio equivalent: a
    user-uploaded WAV/MP3/etc. transcribed via basic-pitch (see
    reference_reverse.ingest_uploaded_reference_audio). Only one is used per
    call — midi_path wins if both are given. Neither is supported in cloud
    mode yet. The response's ``midi_upload`` / ``reference_upload`` fields
    carry the rights warning / any failure reason the UI should show — this
    function never silently drops it.

    ``genre_override`` — a genre lane the UI already showed and the user
    confirmed/picked (see ``detect_midi_genre``), bypassing automatic genre
    resolution (keyword/artist/MIDI/LLM) entirely when set.
    """
    # Public compatibility parameter only.  Musical content comes from the
    # MIDI folder/upload and the sound library, never from free-form prose.
    prompt = (prompt or "").strip()

    expired = _tester_expired_error()
    if expired is not None:
        return expired

    app_config.load_environment()
    # Cloud generation runs through PLG Cloud (/v1/generate, prompt + catalog).
    # It can't consume an explicitly attached MIDI/audio file yet, so reject that
    # upfront rather than silently dropping the user's upload. A normal cloud run
    # (no attachment) flows into the `_cloud_enabled()` branch further down.
    if _cloud_enabled() and (midi_path or reference_audio_path):
        return _err(
            "MIDI/audio upload isn't supported in cloud mode yet — remove the "
            "attached file, or switch off cloud mode to use it.",
            "not_supported",
        )
    # Only the local route needs an on-box LLM key; the cloud release ships
    # without one by design.
    if not _cloud_enabled() and not has_api_key() and not app_config.offline_dev():
        return _err("Add an API key in Settings to generate beats.", "no_api_key")

    if not has_available_midi_material(
        get_samples_dir(),
        midi_root=resolve_midi_dir(),
        midi_path=midi_path,
    ):
        return _err(
            "Add a MIDI file to start — drag your MIDI and drum kit into PLG.",
            "no_midi",
        )

    if not _cloud_enabled():
        try:
            ensure_can_consume_beat()
        except BeatQuotaExceeded as exc:
            return _err(str(exc), "quota", quota=get_quota())

    if _genre_gate_enabled():
        try:
            from genre_profiles import DETAILED_GENRES, is_genre_detailed

            if genre_override:
                # Already a UI-confirmed pick from detect_midi_genre's own
                # DETAILED_GENRES-only menu — no need to re-run detection.
                resolved_genre = genre_override
            else:
                from genre_brain import resolve_generation_genre

                resolved_genre = resolve_generation_genre(prompt, midi_path=midi_path).get("genre")
        except Exception:  # noqa: BLE001 — the gate itself must never block a beat
            resolved_genre = None
        if resolved_genre and not is_genre_detailed(resolved_genre):
            return _err(
                "Упс, этот жанр пока не доступен",
                "genre_not_ready",
                genre=resolved_genre,
                available_genres=sorted(DETAILED_GENRES),
            )

    loc = locale or _ui_locale
    _emit(on_progress, PHASE_GENERATING, 0.12, f"Generating · {_provider_label_safe()}")
    try:
        logger.info("create_beat start: locale=%s", loc)
        sync_model: str | None = None
        if _cloud_enabled():
            samples_dir = Path(get_samples_dir())
            try:
                catalog = _scan_library(samples_dir, quarantine=False)
            except FileNotFoundError:
                catalog = {"root": str(samples_dir), "total": 0, "audio_total": 0, "audio": {}}
            cloud_out = plg_cloud.cloud_generate(
                prompt, catalog=catalog, locale=loc, smart_plan=smart_plan
            )
            if not cloud_out.get("ok"):
                return _err(
                    str(cloud_out.get("error") or "Cloud generation failed."),
                    str(cloud_out.get("error_type") or "cloud"),
                    quota=cloud_out.get("quota"),
                )
            pattern = run_pipeline_from_pattern(cloud_out["pattern"], prompt, locale=loc)
            cloud_quota = cloud_out.get("quota")
            sync_model = str(cloud_out.get("model") or "") or None
        else:
            pattern = run_pipeline(
                prompt,
                locale=loc,
                smart_plan=smart_plan,
                regenerate=regenerate,
                midi_path=midi_path,
                reference_audio_path=reference_audio_path,
                respect_uploaded_scale=respect_uploaded_scale,
                genre_override=genre_override,
            )
            cloud_quota = None
            sync_model = _provider_label_safe()
    except Exception as exc:  # noqa: BLE001 - surface any engine/LLM error to the UI
        logger.warning("create_beat: pipeline failed: %s", safe_error_summary(exc))
        return _pipeline_error(exc)

    _emit(on_progress, PHASE_FINALIZING, 0.9, "Baking session · FL")

    # One generation writes one canonical FLP from the pattern that just
    # passed validation.  Do not re-bake or create mutated variants here.
    # Named after the source MIDI (PLG_<word>__vNN.flp) so repeat generations
    # from the same upload get their own file instead of overwriting the
    # last one — the version tag also gets persisted into the pattern so
    # open_in_fl() reopens this exact file rather than minting a new one.
    baked: dict[str, Any] = {}
    try:
        from flp_writer import flp_base_word, next_versioned_flp_path, write_flp_session

        session_target = next_versioned_flp_path(projects_dir(), flp_base_word(pattern))
        session_path = write_flp_session(pattern, session_target, bundle_sounds=True)
        pattern["plg_flp_path"] = str(session_path)
        try:
            PATTERN_JSON.write_text(json.dumps(pattern, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not persist plg_flp_path after bake: %s", exc)
        baked = {
            "flp": str(session_path),
            "flps": [str(session_path)],
            "message": "Session ready — open in FL Studio",
        }
    except Exception as exc:  # noqa: BLE001 - a beat without its .flp is a failure, never a quiet "ok"
        logger.error("Canonical FLP write after create_beat failed: %s", safe_error_summary(exc))
        return _err(
            "The beat was built but its .flp project file could not be written to the Projects folder.",
            "flp_write_failed",
        )

    # Local quota is charged only once the beat actually exists on disk — a
    # failed pipeline or a failed FLP write never costs the user a beat. (Cloud
    # mode is charged, and refunded on failure, server-side in /v1/generate.)
    if not _cloud_enabled() and baked.get("flp"):
        try:
            consume_beat()
        except BeatQuotaExceeded:
            pass

    _set_last_prompt(prompt)
    logger.info("create_beat ok: bpm=%s style=%s", pattern.get("bpm"), pattern.get("style"))
    _session_note(_lane_summary(pattern))
    _sync_generated_beat_async(pattern, prompt, model=sync_model)
    result = {
        "ok": True,
        "bpm": pattern.get("bpm"),
        "style": pattern.get("style", "unknown"),
        "note_count": count_track_notes(pattern),
        "sample_count": len(pattern.get("samples") or []),
        "provider": _provider_label_safe(),
        "auto_open_fl": get_auto_open_fl(),
        "sample_picks": pattern.get("plg_sample_picks") or {},
        # Lanes where the user's own files could not be used and a built-in
        # sound stands in — the UI says so instead of leaving them guessing.
        "sample_fallback_lanes": _sample_fallback_lanes(pattern),
        "pinned_replaced_lanes": _pinned_replaced_lanes(pattern),
        "stem_session": baked.get("stem_session") or pattern.get("plg_stem_session"),
        "stem_files": baked.get("stem_files")
        or [Path(p).name for p in (pattern.get("plg_stem_files") or [])],
        "flp": baked.get("flp"),
        "flps": baked.get("flps") or ([baked["flp"]] if baked.get("flp") else []),
        "export_variants": baked.get("export_variants") or [],
        "clipper_mode": bool(
            baked.get("clipper_mode")
            or pattern.get("plg_clipper_mode")
            or pattern.get("plg_filth_mode")
        ),
        "quota": cloud_quota if cloud_quota else get_quota(),
        "beat_id": pattern.get("plg_beat_id"),
        "beat_rating": pattern.get("plg_rating"),
        "message": baked.get("message") or "Session baked — open in FL Studio",
        "midi_upload": pattern.get("plg_midi_upload_result"),
        "reference_upload": pattern.get("plg_reference_upload_result"),
    }
    _emit(on_progress, PHASE_DONE, 1.0, "Open in FL Studio")
    return result


def _archived_regenerate(prompt: str | None = None, on_progress: ProgressCb | None = None) -> dict[str, Any]:
    """Re-run generation. Uses the last prompt when none is given.

    Quota is enforced inside create_beat; any "this uses 1 beat" confirmation is
    a UI concern and should happen before calling this.
    """
    text = (prompt or "").strip() or _get_last_prompt()
    if not text:
        return _err("Describe your beat first.", "validation")
    return create_beat(text, on_progress=on_progress, locale=_ui_locale, regenerate=True)


# ---------------------------------------------------------------------------
# Open in FL  (wraps fl_launch.open_beat_in_fl)
# ---------------------------------------------------------------------------
def open_in_fl() -> dict[str, Any]:
    """Write the .flp session, install scripts, and launch FL Studio."""
    try:
        result = open_beat_in_fl(PROJECT_DIR)
    except FLIntegrationError as exc:
        return _err(str(exc), "fl_bridge_setup")
    except FileNotFoundError as exc:
        msg = str(exc)
        if "FL Studio not found" in msg:
            # open_beat_in_fl() already resolved and persisted the real
            # filename (plg_flp_path) before raising this — surface it so
            # the UI can name the actual file instead of a generic ".flp".
            flp_name = ""
            try:
                saved = json.loads(PATTERN_JSON.read_text(encoding="utf-8"))
                flp_name = Path(str(saved.get("plg_flp_path") or "")).name
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            return _err(msg, "fl_not_found", flp_name=flp_name)
        return _err(msg, "not_found")
    except ValueError as exc:
        return _err(str(exc), "validation")
    except OSError as exc:
        return _err(str(exc), "io")

    method = str(result.get("import_method", ""))
    if result.get("imported") and method == "flp_session":
        message = "FL Studio · 3 channels ready — load your sounds"
    elif result.get("imported"):
        message = "FL Studio · 3 tracks imported"
    else:
        message = "FL Studio opened"

    try:
        if PATTERN_JSON.is_file():
            pattern = json.loads(PATTERN_JSON.read_text(encoding="utf-8"))
            if isinstance(pattern, dict):
                _sync_beat_event_async(
                    pattern,
                    "exported",
                    extra={"import_method": method, "imported": bool(result.get("imported"))},
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("export event sync skipped: %s", exc)

    return {
        "ok": True,
        "message": message,
        "imported": bool(result.get("imported")),
        "import_method": method,
        "flp": str(result.get("flp", "")),
        "midi": str(result.get("midi", "")),
        "fl_exe": str(result.get("fl_exe", "")),
    }


# ---------------------------------------------------------------------------
# FL setup  (wraps fl_setup.install_all)
# ---------------------------------------------------------------------------
def install_fl_scripts() -> dict[str, Any]:
    """Install the importer + V2 piano-roll script pack into FL's user folder."""
    try:
        installed = install_all(PROJECT_DIR)
    except OSError as exc:
        return _err(str(exc), "fl_bridge_setup")
    pack = installed.get("script_pack") or []
    return {
        "ok": True,
        "plugin_script": str(installed.get("plugin_script", "")),
        "script_pack_count": len(pack),
        "fl_bridge_ready": is_fl_bridge_ready(PROJECT_DIR),
    }


# ---------------------------------------------------------------------------
# Stem splitting and reference-audio reversal were ARCHIVED 2026-08-11.
#
# Both were complete backend features with a job runner and no UI entry
# point anywhere in web/src. See archive/stem-split-2026-08-11/ and
# archive/reference-audio-2026-08-11/. Do not re-add them without a
# product decision.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Async job registry — start_* + get_job polling for non-blocking UIs
# ---------------------------------------------------------------------------
@dataclass
class _Job:
    id: str
    kind: str
    status: str = "running"  # running | done | error
    phase: str = ""
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def snapshot(self) -> dict[str, Any]:
        now = self.finished_at if self.finished_at is not None else time.time()
        return {
            "ok": True,
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "phase": self.phase,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "error_type": self.error_type,
            "started_at": self.started_at,
            "elapsed": round(now - self.started_at, 1),
        }


_jobs: dict[str, _Job] = {}
_jobs_lock = threading.Lock()

JobTarget = Callable[[ProgressCb], dict[str, Any]]


def _update_job(job: _Job, phase: str, fraction: float, message: str) -> None:
    with _jobs_lock:
        job.phase = phase
        job.progress = float(fraction)
        job.message = message


def _finish_job(job: _Job, result: dict[str, Any]) -> None:
    with _jobs_lock:
        job.finished_at = time.time()
        if result.get("ok"):
            job.status = "done"
            job.result = result
            job.progress = 1.0
        else:
            job.status = "error"
            job.error = result.get("error")
            job.error_type = result.get("error_type")
            job.result = result


# A beat costs a credit (reserved server-side in cloud mode, charged locally
# otherwise), so only one may run at a time: a second start attaches to the
# running job instead of starting another. A job older than this is presumed
# dead (the whole pipeline takes 30-90 s) and must not lock generation forever.
_SINGLE_FLIGHT_KINDS = frozenset({"beat"})
_JOB_STALE_S = 15 * 60


def _live_job_locked(kind: str) -> _Job | None:
    """The running, not-presumed-dead job of ``kind``. Caller holds ``_jobs_lock``."""
    now = time.time()
    for job in _jobs.values():
        if job.kind == kind and job.status == "running" and now - job.started_at < _JOB_STALE_S:
            return job
    return None


def _session_note(message: str) -> None:
    """One line into plg_session.log (the file a tester sends back); never raises."""
    try:
        from plg_log import log_event

        log_event(message)
    except Exception:  # noqa: BLE001 - a log line must never break a job
        logger.debug("session log line skipped", exc_info=True)


def _lane_summary(pattern: dict[str, Any]) -> str:
    """One line for plg_session.log: which file plays each drum lane, or why a lane has none (never raises)."""
    try:
        origins = pattern.get("plg_sample_origins") if isinstance(pattern.get("plg_sample_origins"), dict) else {}
        records = pattern.get("plg_sample_fallback") if isinstance(pattern.get("plg_sample_fallback"), dict) else {}
        parts = []
        for lane in ("kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer"):
            origin = origins.get(lane) or {}
            if origin.get("origin") == "absent":
                record = records.get(lane) if isinstance(records.get(lane), dict) else {}
                why = ",".join(record.get("reasons") or []) or "no usable file"
                parts.append(f"{lane}=ABSENT({why}; own files turned down: {record.get('user_files', 0)})")
            else:
                parts.append(f"{lane}={origin.get('file') or '?'} [{origin.get('origin') or '?'}]")
        return "lanes: " + "; ".join(parts)
    except Exception:  # noqa: BLE001 - a log line must never fail a beat
        return "lanes: (unavailable)"


def _run_job(kind: str, target: JobTarget) -> dict[str, Any]:
    """Run ``target(on_progress)`` in a daemon thread; return a job handle.

    For single-flight kinds an already-running job is returned instead
    (``reused: True``); the check and the registration are one atomic step, so
    two simultaneous starts cannot both pass it.
    """
    job = _Job(id=uuid4().hex, kind=kind)
    with _jobs_lock:
        if kind in _SINGLE_FLIGHT_KINDS:
            live = _live_job_locked(kind)
            if live is not None:
                logger.info("job %s already running (%s); attaching instead of starting another", kind, live.id)
                return {"ok": True, "job_id": live.id, "kind": kind, "status": "running", "reused": True}
        _jobs[job.id] = job

    def worker() -> None:
        _session_note(f"{kind} job {job.id[:8]} started")
        try:
            result = target(lambda phase, frac, msg: _update_job(job, phase, frac, msg))
        except Exception as exc:  # noqa: BLE001 - never let a job thread die silently
            logger.error("job %s crashed: %s", kind, safe_error_summary(exc))
            _session_note(f"{kind} job {job.id[:8]} CRASHED: {safe_error_summary(exc)}")
            result = _err("Operation failed unexpectedly. Please try again.", "unknown")
        else:
            if result.get("ok"):
                _session_note(f"{kind} job {job.id[:8]} done: {Path(str(result.get('flp') or '')).name or 'no file'}")
            else:
                first = (str(result.get("error") or "").strip().splitlines() or [""])[0][:200]
                _session_note(f"{kind} job {job.id[:8]} FAILED type={result.get('error_type')}: {first}")
        _finish_job(job, result)

    threading.Thread(target=worker, daemon=True, name=f"plg-{kind}").start()
    return {"ok": True, "job_id": job.id, "kind": kind, "status": "running"}


def get_job(job_id: str) -> dict[str, Any]:
    """Poll a job's state. Returns status=unknown for stale/unknown ids."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return {"ok": False, "status": "unknown", "error": "Unknown job id.", "job_id": job_id}
    return job.snapshot()


def start_beat(
    prompt: str,
    locale: str | None = None,
    smart_plan: bool | None = None,
    midi_path: str | None = None,
    reference_audio_path: str | None = None,
    respect_uploaded_scale: bool = False,
    genre_override: str | None = None,
) -> dict[str, Any]:
    """Async create_beat. UI polls get_job(job_id) for phase/elapsed/result.

    ``midi_path``/``reference_audio_path`` — an explicit file the Library UI's
    Attach flow picked (see midi_ingest.ingest_uploaded_midi / reference_reverse.
    ingest_uploaded_reference_audio). Both optional and default to unset —
    every existing caller (no attachment) behaves exactly as before.

    ``genre_override`` — a genre lane the UI already showed (via
    ``detect_midi_genre``) and the user confirmed/picked, forwarded straight
    to create_beat.
    """
    prompt = (prompt or "").strip()
    expired = _tester_expired_error()
    if expired is not None:
        return expired
    if locale:
        set_ui_locale(locale)
    loc = locale or _ui_locale
    plan = smart_plan
    return _run_job(
        "beat",
        lambda prog: create_beat(
            prompt,
            on_progress=prog,
            locale=loc,
            smart_plan=plan,
            midi_path=midi_path,
            reference_audio_path=reference_audio_path,
            respect_uploaded_scale=respect_uploaded_scale,
            genre_override=genre_override,
        ),
    )


def prompt_detail_hint(prompt: str) -> dict[str, Any]:
    """Expose detail meter + auto smart-plan recommendation to the web UI."""
    from beat_planner import smart_plan_hint

    return {"ok": True, **smart_plan_hint(prompt or "")}


def start_open_in_fl() -> dict[str, Any]:
    """Async open_in_fl — keeps the UI thread free while scanning/launching."""
    return _run_job("open_fl", lambda _prog: open_in_fl())


def clear_finished_jobs() -> dict[str, Any]:
    """Drop done/error jobs from the registry (housekeeping)."""
    with _jobs_lock:
        stale = [jid for jid, job in _jobs.items() if job.status in ("done", "error")]
        for jid in stale:
            del _jobs[jid]
    return {"ok": True, "cleared": len(stale)}


APP_VERSION = app_version()


# ---------------------------------------------------------------------------
# The signed auto-updater was ARCHIVED 2026-08-11 — see
# archive/updater-2026-08-11/. check_for_updates / download_update /
# apply_downloaded_update had no UI entry point. SECURITY.md's release
# signing procedure depends on this vertical: restore it together with
# scripts/sign_release.py before shipping updates.
# ---------------------------------------------------------------------------


DOC_FILES = {
    "start_here": "START_HERE.md",
    "fl_bridge": "FL_BRIDGE.md",
    "fl_scripts": "FL_SCRIPTS.md",
    "fl_workflows": "FL_WORKFLOWS.md",
    "fl_versions": "FL_VERSIONS.md",
    "terms": "legal/TERMS_RU.md",
    "privacy": "legal/PRIVACY_RU.md",
    "payment": "legal/PAYMENT_TERMS_RU.md",
    "refund": "legal/PAYMENT_TERMS_RU.md",
    "business": "docs/internal/BUSINESS_RU.md",
}


def import_kit_folder(source: str, *, organize: bool = False) -> dict[str, Any]:
    """Import a folder into PLG's active sound and MIDI material roots.

    This is the native-folder-picker fallback for WebView2 folder drops.  It
    follows the same split as chunked drag-and-drop: .mid/.midi → PLG_MIDI,
    audio → PLG_Sounds.
    """
    from organize_kit import copy_kit_as_is, organize_library

    src = Path((source or "").strip())
    if not src.is_dir():
        return _err(f"Folder not found: {source}", "not_found")

    samples_dir = Path(get_samples_dir())
    midi_dir = resolve_midi_dir()
    try:
        if organize:
            counts = organize_library(
                src.resolve(),
                samples_dir.resolve(),
                midi_root=midi_dir.resolve(),
            )
            imported = sum(counts.values())
            mode = "organize"
            destination = None
            message = f"Imported {imported} files — sorted into library folders."
        else:
            copied = copy_kit_as_is(
                src.resolve(), samples_dir.resolve(), midi_root=midi_dir.resolve()
            )
            imported = int(copied.get("imported", 0))
            if imported == 0:
                # Own error_type (not the generic "not_found" used elsewhere
                # in this file for unrelated missing-path cases) so the UI
                # can give this one a specific, localized message instead of
                # showing this raw English fallback.
                return _err("No supported files found (wav, mid, midi, mp3).", "no_supported_files")
            counts = {"imports": imported}
            mode = "as_is"
            destination = copied.get("destination")
            message = f"Imported kit into {destination} — folder kept as-is."
        catalog = _scan_library(samples_dir)
        save_catalog(catalog, CATALOG_FILE)
        audio_total, midi_total, material_total = _material_counts_from_catalog(catalog)
    except OSError as exc:
        return _err(str(exc), "io")

    return {
        "ok": True,
        "imported": imported,
        "counts": counts,
        "mode": mode,
        "destination": destination,
        "root": catalog["root"],
        "total": catalog["total"],
        "audio_total": audio_total,
        "midi_total": midi_total,
        "material_total": material_total,
        "message": message,
    }


_MATERIAL_EXTENSIONS = AUDIO_EXTENSIONS | MIDI_EXTENSIONS


def _material_relative_path(value: str) -> Path | None:
    """Return a safe relative import path for a dropped MIDI/audio file."""
    normalized = str(value or "").replace("\\", "/").lstrip("/")
    candidate = Path(normalized)
    if not normalized or candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    if candidate.suffix.lower() not in _MATERIAL_EXTENSIONS:
        return None
    return candidate


def begin_material_import() -> dict[str, Any]:
    """Create isolated per-type staging destinations for one Home drop.

    Home is the only material intake: audio from a dropped kit belongs under
    PLG_Sounds while a MIDI melody belongs under PLG_MIDI, which is the active
    MIDI-first pipeline's control surface. Chunks land in a batch-private
    staging folder first — a file is written incrementally, so a concurrent
    library scan must never see it half-written — and finish_material_import()
    moves everything into the flat, permanent root once the batch completes.
    """
    samples_dir = Path(get_samples_dir()).resolve()
    midi_dir = resolve_midi_dir().resolve()
    batch_id = uuid4().hex
    sounds_staging = samples_dir / "imports" / f"drop-{batch_id}"
    midi_staging = midi_dir / "imports" / f"drop-{batch_id}"
    try:
        sounds_staging.mkdir(parents=True, exist_ok=False)
        midi_staging.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return _err(str(exc), "io")
    with _material_import_lock:
        _material_import_roots[batch_id] = _MaterialImportBatch(
            sounds_staging=sounds_staging,
            midi_staging=midi_staging,
            sounds_final=samples_dir,
            midi_final=midi_dir,
        )
    return {"ok": True, "import_id": batch_id}


def import_material_chunk(import_id: str, relative_path: str, offset: int, data_base64: str) -> dict[str, Any]:
    """Append one bounded browser-upload chunk to the active material batch."""
    with _material_import_lock:
        batch = _material_import_roots.get(str(import_id or ""))
    relative = _material_relative_path(relative_path)
    if batch is None:
        return _err("Import session expired. Drop the files again.", "import_expired")
    if relative is None:
        raw_path = str(relative_path or "").replace("\\", "/")
        if raw_path.startswith("/") or any(part == ".." for part in raw_path.split("/")):
            return _err("Invalid import path.", "invalid_path")
        return _err("Only MIDI and audio files can be imported.", "unsupported_file")
    try:
        batch_root = batch.midi_staging if relative.suffix.lower() in MIDI_EXTENSIONS else batch.sounds_staging
        destination = (batch_root / relative).resolve()
        destination.relative_to(batch_root.resolve())
        chunk = base64.b64decode(str(data_base64 or ""), validate=True)
        if not chunk:
            return _err("Received an empty file chunk.", "empty_chunk")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb" if int(offset) == 0 else "ab") as output:
            output.write(chunk)
    except (OSError, ValueError) as exc:
        return _err(str(exc), "io")
    except Exception:  # noqa: BLE001 - malformed data must not crash the bridge
        return _err("The dropped file could not be read.", "invalid_file")
    return {"ok": True}


def _unique_destination(folder: Path, filename: str) -> Path:
    """Find a free path for ``filename`` in ``folder``.

    Renames like Windows Explorer's own copy conflict resolution
    (``kick.wav`` -> ``kick (1).wav`` -> ``kick (2).wav``) — an existing file
    with the same name is never overwritten.
    """
    target = folder / filename
    if not target.exists():
        return target
    stem, suffix = Path(filename).stem, Path(filename).suffix
    index = 1
    while True:
        candidate = folder / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _consolidate_batch_dir(staging_root: Path, final_root: Path) -> None:
    """Move every file out of one completed batch's staging folder into its
    flat, permanent destination, then remove the now-empty staging folder."""
    if not staging_root.is_dir():
        return
    for file_path in sorted(p for p in staging_root.rglob("*") if p.is_file()):
        relative = file_path.relative_to(staging_root)
        target_dir = final_root / relative.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = _unique_destination(target_dir, relative.name)
        shutil.move(str(file_path), str(destination))
    shutil.rmtree(staging_root, ignore_errors=True)
    try:
        staging_root.parent.rmdir()  # the batch-less "imports" folder, if now empty
    except OSError:
        pass


def finish_material_import(import_id: str) -> dict[str, Any]:
    """Consolidate a completed drop into PLG_MIDI/PLG_Sounds and rescan.

    Files move out of the batch's staging folder into the flat library root —
    no imports/drop-<uuid>/ subfolder is left behind — then Home's material
    counts are refreshed without an app restart.
    """
    with _material_import_lock:
        batch = _material_import_roots.pop(str(import_id or ""), None)
    if batch is None:
        return _err("Import session expired. Drop the files again.", "import_expired")
    try:
        _consolidate_batch_dir(batch.sounds_staging, batch.sounds_final)
        _consolidate_batch_dir(batch.midi_staging, batch.midi_final)
        samples_dir = Path(get_samples_dir())
        catalog = _scan_library(samples_dir)
        save_catalog(catalog, CATALOG_FILE)
        audio_total, midi_total, material_total = _material_counts_from_catalog(catalog)
    except OSError as exc:
        return _err(str(exc), "io")
    return {
        "ok": True,
        "destination": str(batch.sounds_final),
        "midi_destination": str(batch.midi_final),
        "root": catalog["root"],
        "audio_total": audio_total,
        "midi_total": midi_total,
        "material_total": material_total,
        "message": "Materials added to PLG.",
    }


def cancel_material_import(import_id: str) -> dict[str, Any]:
    """Delete an interrupted upload's partial files. Nothing is kept."""
    with _material_import_lock:
        batch = _material_import_roots.pop(str(import_id or ""), None)
    if batch is not None:
        for staging_root in (batch.sounds_staging, batch.midi_staging):
            shutil.rmtree(staging_root, ignore_errors=True)
            try:
                staging_root.parent.rmdir()
            except OSError:
                pass
    return {"ok": True}


def _available_docs() -> dict[str, str]:
    return {
        key: str(DOCS_DIR / name)
        for key, name in DOC_FILES.items()
        if (DOCS_DIR / name).is_file()
    }


def get_app_info() -> dict[str, Any]:
    """Version, paths, starter pack, FL — for Settings / Help."""
    from starter_kit import starter_kit_info

    fl_exe = find_fl_executable()
    starter = starter_kit_info()
    return {
        "ok": True,
        "version": APP_VERSION,
        "project_dir": str(PROJECT_DIR),
        "fl_executable": str(fl_exe) if fl_exe else None,
        "fl_version": fl_version_label(fl_exe),
        "fl_installations": list_fl_installations(),
        "fl_bridge_ready": is_fl_bridge_ready(PROJECT_DIR),
        "starter": starter,
        "docs": _available_docs(),
        "quota": get_quota(),
    }


def get_docs() -> dict[str, Any]:
    """Which shipped docs actually exist — for Help's legal-docs card.

    Deliberately doesn't call get_app_info(): that also does FL executable/
    installation detection, starter-kit info, and a get_quota() that hits
    the cloud over the network when signed in — none of which Help's docs
    card needs. Cuts a real network round-trip out of the render path.
    """
    return {"ok": True, "docs": _available_docs()}


def open_document(doc_id: str) -> dict[str, Any]:
    """Open a shipped markdown doc in the user's default viewer — not the whole folder."""
    name = DOC_FILES.get(doc_id)
    if not name:
        return _err(f"Unknown document: {doc_id}", "validation")
    path = DOCS_DIR / name
    if not path.is_file():
        return _err(f"Document missing: {name}", "not_found")
    return open_path(str(path))




__all__ = [
    # settings
    "get_settings",
    "save_settings",
    # status / quota / library
    "get_status",
    "get_quota",
    "scan_library",
    # actions (sync)
    "create_beat",
    "open_in_fl",
    "install_fl_scripts",
    "reveal_path",
    "import_kit_folder",
    "get_app_info",
    "open_document",
    "get_auth_status",
    "cloud_signup",
    "cloud_delete_account",
    "cloud_login",
    "cloud_logout",
    "cloud_reset_password",
    "cloud_complete_password_reset",
    "cloud_verify_email",
    "cloud_resend_verification",
    "cloud_billing_status",
    "cloud_billing_checkout",
    "cloud_billing_checkout_link",
    "cloud_fetch_status",
    "ping_cloud_connection",
    "cloud_submit_feedback",
    "set_ui_locale",
    "open_external_url",
    # actions (async job)
    "start_beat",
    "start_open_in_fl",
    "get_job",
    "clear_finished_jobs",
]


if __name__ == "__main__":
    # Quick smoke check without any UI: prints status as JSON.
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(get_status(), ensure_ascii=False, indent=2))
