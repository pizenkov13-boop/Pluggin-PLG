"""Sentry for desktop .exe — optional crash reporting."""

from __future__ import annotations

import logging
import os
import sys

from app_config import app_version

logger = logging.getLogger("plg.sentry")

_HOOKED = False


def init_desktop_sentry() -> None:
    global _HOOKED
    dsn = (os.getenv("PLG_SENTRY_DSN") or os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        return

    try:
        import sentry_sdk
    except ImportError:
        logger.debug("sentry-sdk not installed — pip install sentry-sdk")
        return

    environment = os.getenv("PLG_ENV", "desktop")
    release = app_version()

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=f"plg-desktop@{release}",
        traces_sample_rate=float(os.getenv("PLG_SENTRY_TRACES_SAMPLE_RATE", "0.05")),
        send_default_pii=False,
    )

    if not _HOOKED:
        _install_exception_hooks(sentry_sdk)
        _HOOKED = True

    def _flush_on_exit(*_args: object) -> None:
        sentry_sdk.flush(timeout=2.0)

    import atexit

    atexit.register(_flush_on_exit)
    logger.info("Desktop Sentry enabled env=%s", environment)


def _install_exception_hooks(sentry_sdk: object) -> None:
    import threading

    original_excepthook = sys.excepthook

    def _hook(exc_type, exc, tb) -> None:
        try:
            sentry_sdk.capture_exception((exc_type, exc, tb))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        original_excepthook(exc_type, exc, tb)

    sys.excepthook = _hook

    if hasattr(threading, "excepthook"):
        original_thread_hook = threading.excepthook

        def _thread_hook(args) -> None:  # type: ignore[no-untyped-def]
            try:
                sentry_sdk.capture_exception(args.exc_value)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            original_thread_hook(args)

        threading.excepthook = _thread_hook


def capture_exception(exc: BaseException) -> None:
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001
        logger.debug("sentry capture failed", exc_info=True)
