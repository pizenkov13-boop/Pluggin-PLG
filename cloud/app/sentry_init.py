"""Sentry — server crashes and performance."""

from __future__ import annotations

import logging

from cloud.app.config import APP_VERSION, PLG_ENV, SENTRY_DSN, SENTRY_TRACES_SAMPLE_RATE

logger = logging.getLogger("plg.sentry")


def init_sentry() -> None:
    if not SENTRY_DSN:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("sentry-sdk not installed — pip install sentry-sdk")
        return

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=PLG_ENV,
        release=f"plg-cloud@{APP_VERSION}",
        traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        send_default_pii=False,
    )
    logger.info("Sentry initialized env=%s", PLG_ENV)
