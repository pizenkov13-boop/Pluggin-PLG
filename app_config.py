"""Read and write PLG settings (.env) from the desktop app."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import date
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from library_paths import DEFAULT_LIBRARY_DIR, LEGACY_LIBRARY_DIR
from plg_log import log_event
from plg_paths import app_dir, bundle_dir, is_frozen

logger = logging.getLogger("plg.settings")

PROJECT_DIR = app_dir()
ENV_FILE = PROJECT_DIR / ".env"
ENV_LOCAL = PROJECT_DIR / ".env.local"
ENV_EXAMPLE = PROJECT_DIR / ".env.example"
RELEASE_ENV = bundle_dir() / ".env.release"
TESTER_ENV = bundle_dir() / ".env.tester"
FLAVOR_FILE = "build_flavor.json"


class SettingsWriteError(OSError):
    """Writing .env failed (e.g. no write permission to the app folder).

    Subclasses OSError so it's still caught by any generic `except OSError`
    handler, but callers that want to react specifically to a settings-save
    failure (see plg_api.save_settings) can catch this one by name instead.
    """


def app_version() -> str:
    load_environment()
    return os.getenv("PLG_APP_VERSION", "1.0.0")


def is_release_build() -> bool:
    load_environment()
    return os.getenv("PLG_RELEASE_BUILD", "").strip().lower() in ("1", "true", "yes")


@lru_cache(maxsize=1)
def build_flavor() -> dict:
    """What this exe was built as: ``{"flavor": "tester", "built": ..., "expires": ...}`` or ``{}``.

    The ONLY source is ``build_flavor.json`` inside the frozen exe, written by ``build_plg.bat tester``
    and added to the bundle by ``PLG.spec`` for that build alone. No environment variable, ``.env`` or
    ``.env.local`` can set it, a release exe does not contain the file, and a source run is never a
    tester build."""
    if not is_frozen():
        return {}
    try:
        data = json.loads((bundle_dir() / FLAVOR_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def tester_build() -> bool:
    """True in the tester exe (local only: no server, sign-in, quota or network; expires on a date)."""
    return build_flavor().get("flavor") == "tester"


def tester_expiry() -> date | None:
    """The date after which the tester build stops creating beats; None when it cannot be read."""
    try:
        return date.fromisoformat(str(build_flavor().get("expires", ""))[:10])
    except ValueError:
        return None


def _tester_today() -> date:
    """Today, but never earlier than the latest day this build has seen: setting the clock back does
    not give a tester build more days. The last seen day is kept in the user's data folder."""
    from plg_paths import user_data_dir

    today = date.today()
    path = user_data_dir() / "tester_clock.json"
    try:
        seen = date.fromisoformat(str(json.loads(path.read_text(encoding="utf-8")).get("last_seen", ""))[:10])
    except (OSError, ValueError, AttributeError):
        seen = None
    if seen is not None and seen > today:
        return seen
    if seen is None or seen < today:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"last_seen": today.isoformat()}), encoding="utf-8")
        except OSError:
            pass
    return today


def tester_days_left() -> int | None:
    """Whole days left (0 = last day); negative once expired; None for a non-tester build."""
    if not tester_build():
        return None
    expiry = tester_expiry()
    if expiry is None:
        return -1  # an unreadable date fails closed
    return (expiry - _tester_today()).days


def tester_expired() -> bool:
    left = tester_days_left()
    return left is not None and left < 0


_offline_dev_warned = False


def offline_dev() -> bool:
    """``PLG_OFFLINE_DEV=1``: run from source with no server (development only).

    Behaves like local mode: no cloud calls, no sign-in screen, no beat quota and
    no API-key requirement (the MIDI-first route calls no LLM), so a developer
    can press Create and listen with the server down. The single owner of the
    decision: ``get_cloud_mode``, ``plg_cloud.is_cloud_mode``, ``beat_quota`` and
    ``plg_api`` all ask it. The environment flag is ALWAYS ignored in a frozen exe
    and in a release build (``PLG_RELEASE_BUILD``), whatever the environment says.

    The one exception is not the environment: the tester exe (``tester_build()``, a
    file stamped into that build alone) is local-only by construction."""
    global _offline_dev_warned
    if tester_build():
        return True
    if is_frozen() or is_release_build():
        return False
    if os.environ.get("PLG_OFFLINE_DEV", "").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    if not _offline_dev_warned:
        _offline_dev_warned = True
        logger.warning(
            "PLG_OFFLINE_DEV is ON: cloud, sign-in and the beat quota are bypassed (development only)"
        )
    return True


def _safe_log_event(message: str) -> None:
    """log_event() opens plg_session.log next to the exe on first use — in
    the exact same "app folder isn't writable" case this module is guarding
    against, that open can itself raise. Never let logging a failure become
    a second, unrelated crash."""
    try:
        log_event(message)
    except OSError:
        logger.debug("could not write to plg_session.log", exc_info=True)


def seed_env_from_bundle() -> None:
    """First run of frozen exe — copy the bundled .env.release next to the exe.

    Only RELEASE_ENV is trusted as a seed source. .env.example used to be a
    fallback here, but it ships with PLG_CLOUD_MODE commented out — silently
    falling back to it turned "this build is missing .env.release" (a
    packaging mistake) into "the app quietly has no cloud features at all"
    (a customer-visible bug) instead of something loud and diagnosable. A
    missing RELEASE_ENV is now logged as exactly what it is.

    Also runs on every launch, not just the first: a .env from an older
    build, or one seeded before this function existed, can be missing
    PLG_CLOUD_MODE entirely — the old "return if ENV_FILE.exists()" trusted
    that file forever with no way to self-correct.
    """
    if tester_build():
        # The tester exe seeds its own, cloud-free template and never heals a cloud mode back in.
        if not ENV_FILE.exists() and TESTER_ENV.is_file():
            try:
                shutil.copy(TESTER_ENV, ENV_FILE)
            except OSError:
                logger.error("seed_env_from_bundle: could not write %s (tester build)", ENV_FILE, exc_info=True)
        load_dotenv(ENV_FILE if ENV_FILE.exists() else TESTER_ENV, override=True)
        return
    if ENV_FILE.exists():
        _heal_missing_cloud_mode()
        return
    if not RELEASE_ENV.is_file():
        msg = f"seed_env_from_bundle: {RELEASE_ENV} missing from the bundle — incomplete build."
        logger.error(msg)
        _safe_log_event(msg)
        return
    try:
        shutil.copy(RELEASE_ENV, ENV_FILE)
    except OSError as exc:
        msg = f"seed_env_from_bundle: could not write {ENV_FILE} ({exc}); continuing with in-memory defaults."
        logger.error(msg, exc_info=True)
        _safe_log_event(msg)
        # The seed couldn't persist to disk, but the app should still open
        # with a working window — load the template straight into this
        # process's environment instead of leaving PLG_CLOUD_MODE and
        # everything else unset for the whole session.
        load_dotenv(RELEASE_ENV, override=True)
        return
    load_dotenv(ENV_FILE, override=True)


def _heal_missing_cloud_mode() -> None:
    """Patch PLG_CLOUD_MODE back into an existing .env that predates it (or
    fell back to .env.example before that fallback was removed), instead of
    leaving the account permanently stuck in local mode with no cloud
    features. Only touches this one key — not a wholesale overwrite of
    whatever else is already in the user's .env."""
    if get_cloud_mode():
        return  # already set correctly, nothing to heal
    if not RELEASE_ENV.is_file():
        return
    release_value = read_env_file(RELEASE_ENV).get("PLG_CLOUD_MODE", "").strip().lower()
    if release_value not in ("1", "true", "yes"):
        return  # the template itself doesn't call for cloud mode either
    try:
        write_env_file({"PLG_CLOUD_MODE": release_value})
    except SettingsWriteError:
        msg = f"seed_env_from_bundle: could not heal PLG_CLOUD_MODE in {ENV_FILE}"
        logger.error(msg, exc_info=True)
        _safe_log_event(msg)


MANAGED_KEYS = (
    "PLG_LLM_PROVIDER",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_MODEL",
    "PLG_LLM_PIPELINE",
    "PLG_TITAN_ARCHITECT_MODEL",
    "PLG_TITAN_COMPOSER_MODEL",
    "PLG_TITAN_QA_MODEL",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "PLG_SAMPLES_DIR",
    "GEMINI_MODEL",
    "PLG_CLAUDE_MODEL",
    "PLG_AUTO_OPEN_FL",
)


def load_environment() -> None:
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=True)
    else:
        load_dotenv()
    if ENV_LOCAL.exists():
        load_dotenv(ENV_LOCAL, override=True)


def read_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_FILE
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env_file(updates: dict[str, str]) -> None:
    current = read_env_file()
    current.update({key: value for key, value in updates.items() if value is not None})

    lines: list[str] = []
    written_keys: set[str] = set()

    if ENV_EXAMPLE.exists():
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
                continue
            if "=" not in stripped:
                lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in current:
                lines.append(f"{key}={current[key]}")
                written_keys.add(key)
            else:
                lines.append(line)

    for key, value in current.items():
        if key not in written_keys:
            lines.append(f"{key}={value}")

    try:
        ENV_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        raise SettingsWriteError(str(exc)) from exc
    load_dotenv(ENV_FILE, override=True)


def get_samples_dir() -> Path:
    load_environment()
    raw = os.environ.get("PLG_SAMPLES_DIR") or os.environ.get("PLG_LIBRARY_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    if DEFAULT_LIBRARY_DIR.is_dir():
        return DEFAULT_LIBRARY_DIR.resolve()
    if LEGACY_LIBRARY_DIR.is_dir():
        return LEGACY_LIBRARY_DIR.resolve()
    return DEFAULT_LIBRARY_DIR.resolve()


def has_api_key() -> bool:
    load_environment()
    provider = (os.environ.get("PLG_LLM_PROVIDER") or "openrouter").strip().lower()
    if provider in ("openrouter", "or"):
        return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    if provider in ("anthropic", "claude"):
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    return bool(
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )


def get_auto_open_fl() -> bool:
    load_environment()
    raw = (os.environ.get("PLG_AUTO_OPEN_FL") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_cloud_mode() -> bool:
    load_environment()
    if offline_dev():
        return False
    raw = (os.environ.get("PLG_CLOUD_MODE") or "").strip().lower()
    return raw in ("1", "true", "yes")


def get_cloud_url() -> str:
    load_environment()
    return (os.environ.get("PLG_CLOUD_URL") or "http://127.0.0.1:8787").rstrip("/")


def settings_snapshot() -> dict[str, str]:
    load_environment()
    return {
        "provider": os.environ.get("PLG_LLM_PROVIDER", "openrouter"),
        "openrouter_key": os.environ.get("OPENROUTER_API_KEY", ""),
        "openrouter_base_url": os.environ.get("OPENROUTER_BASE_URL", ""),
        "openrouter_model": os.environ.get("OPENROUTER_MODEL", "gpt-5.6-luna"),
        "gemini_key": os.environ.get("GEMINI_API_KEY", ""),
        "anthropic_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "samples_dir": str(get_samples_dir()),
        "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        "claude_model": os.environ.get("PLG_CLAUDE_MODEL", "claude-sonnet-4-6"),
        "auto_open_fl": "true" if get_auto_open_fl() else "false",
        "cloud_mode": "true" if get_cloud_mode() else "false",
        "cloud_url": get_cloud_url(),
    }
