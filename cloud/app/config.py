"""Cloud API configuration."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

APP_VERSION = os.getenv("PLG_APP_VERSION", "1.0.0")
MIN_CLIENT_VERSION = os.getenv("PLG_MIN_CLIENT_VERSION", "1.0.0")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = (
    os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SECRET_KEY") or ""
).strip()
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "").strip()
# Public anon key — used only as the `apikey` header on the server-side
# GoTrue password-grant call in auth_login.py. Falls back to the service key
# (always present) when unset; both are accepted by GoTrue and neither
# weakens the password check itself.
SUPABASE_ANON_KEY = (
    os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY") or ""
).strip()
SUPABASE_JWKS_URL = (os.getenv("SUPABASE_JWKS_URL") or "").strip() or (
    f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json" if SUPABASE_URL else ""
)

MAX_PROMPT_CHARS = int(os.getenv("PLG_MAX_PROMPT_CHARS", "4000"))
MAX_DEVICES = int(os.getenv("PLG_MAX_DEVICES", "3"))
BEAT_LIMIT = int(os.getenv("PLG_BEAT_LIMIT", "30"))
PERIOD_DAYS = int(os.getenv("PLG_BEAT_PERIOD_DAYS", "30"))
# Kept only to satisfy the existing quota RPC signature. PLG has no daily cap.
DAILY_BEAT_LIMIT = 0

# Rough cost estimate per generation for kill-switch (USD)
EST_COST_BASE = float(os.getenv("PLG_EST_COST_BASE_USD", "0.006"))
EST_COST_PREMIUM = float(os.getenv("PLG_EST_COST_PREMIUM_USD", "0.01"))

DEV_BYPASS_AUTH = os.getenv("PLG_CLOUD_DEV_BYPASS", "").lower() in ("1", "true", "yes")
DEV_USER_ID = os.getenv("PLG_CLOUD_DEV_USER_ID", "00000000-0000-0000-0000-000000000001")

# Billing (Phase 2)
GRACE_DAYS = int(os.getenv("PLG_GRACE_DAYS", "3"))
TRIAL_BEATS = int(os.getenv("PLG_TRIAL_BEATS", "1"))
# The one-free-beat-per-device claim ages out after this many days: a device
# that used its trial long ago becomes eligible for a fresh one, so a genuine
# reinstall / hand-me-down PC self-heals without support. Set to 0 to make the
# claim permanent (the pre-2026-09 behaviour). A farmer would have to wait a
# full window per free beat, so this doesn't open a practical abuse path.
TRIAL_CLAIM_TTL_DAYS = int(os.getenv("PLG_TRIAL_CLAIM_TTL_DAYS", "180"))
PRICE_CIS_RUB = int(os.getenv("PLG_PRICE_CIS_RUB", "499"))
# Default for the `other` price-book key (see cloud/app/pricing.py). Every
# non-RU payer whose region isn't priced in its own currency lands here.
PRICE_INTL_USD_CENTS = int(os.getenv("PLG_PRICE_INTL_USD_CENTS", "1399"))
# Eurozone / EEA price-book key (`eu`). Fixed retail price — the ~10%
# Prodamus commission is priced in, not computed at runtime.
PRICE_EUR_CENTS = int(os.getenv("PLG_PRICE_EUR_CENTS", "1299"))
# Belarus price-book key (`by`). Prodamus confirmed USD/EUR/BYN as the only
# foreign currencies it can bill (plan §0) — BYN is the third. Minor unit is
# the kapeyka (BYN has 100, same shape as RUB kopecks). Fixed retail price,
# not a live FX conversion of PRICE_INTL_USD_CENTS — same reasoning as EUR:
# the rate moves, this doesn't, adjust the env var when it drifts too far.
PRICE_BYN_CENTS = int(os.getenv("PLG_PRICE_BYN_CENTS", "4300"))

# --- Launch promo: the first PROMO_MAX_PAID buyers pay PRICE_CIS_RUB_PROMO -----
# One-time discount on the domestic RUB rail. `ru_promo` (299 ₽) is offered in
# place of `ru` (499 ₽) while fewer than PROMO_MAX_PAID distinct users have
# paid it. Anything that is about to COMMIT a slot (freezing a checkout
# link's price, creating a real payment) additionally requires fewer than
# PROMO_LINK_BUDGET checkout links already frozen to it — that second bound
# caps overshoot from links frozen at 299 that aren't paid yet, and is
# deliberately NOT applied to a plain price lookup/display (see
# cloud/app/promo.py: promo_paid_available vs promo_tier_available). Set
# PLG_PROMO_ENABLED=false to end the promo early.
PROMO_ENABLED = os.getenv("PLG_PROMO_ENABLED", "true").lower() in ("1", "true", "yes")
PRICE_CIS_RUB_PROMO = int(os.getenv("PLG_PRICE_CIS_RUB_PROMO", "299"))
PROMO_MAX_PAID = int(os.getenv("PLG_PROMO_MAX_PAID", "100"))
PROMO_LINK_BUDGET = int(os.getenv("PLG_PROMO_LINK_BUDGET", "108"))

BILLING_RETURN_URL = os.getenv("PLG_BILLING_RETURN_URL", "https://example.com/billing/return.html")
# The desktop never opens a provider checkout directly. It creates a short-lived
# opaque link to this page, where the payer can review the offer and accept the
# payment terms before the provider checkout is issued.
BILLING_CHECKOUT_PAGE_URL = os.getenv("PLG_BILLING_CHECKOUT_PAGE_URL", "https://example.com/checkout.html")
BILLING_CHECKOUT_LINK_TTL_SECONDS = int(os.getenv("PLG_BILLING_CHECKOUT_LINK_TTL_SECONDS", "900"))

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")
# Receipt data for the "Чеки от ЮKassa" service. VAT code 1 means "Без НДС".
# Override it in production if the merchant's tax regime requires another rate.
YOOKASSA_VAT_CODE = int(os.getenv("YOOKASSA_VAT_CODE", "1"))
# YooKassa doesn't sign webhooks — it publishes fixed notification source IPs.
# The handler already re-fetches the payment from the API (forgery-proof); this
# allowlist stops randoms from spamming the endpoint (load on us + their API).
# Disable with PLG_YOOKASSA_VERIFY_IP=false if your edge mangles source IPs.
YOOKASSA_VERIFY_IP = os.getenv("PLG_YOOKASSA_VERIFY_IP", "true").lower() in ("1", "true", "yes")
YOOKASSA_WEBHOOK_IPS = [
    c.strip()
    for c in os.getenv(
        "PLG_YOOKASSA_WEBHOOK_IPS",
        "203.0.113.0/24",  # placeholder: set PLG_YOOKASSA_WEBHOOK_IPS to the provider's published ranges
    ).split(",")
    if c.strip()
]

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID_INTL = os.getenv("STRIPE_PRICE_ID_INTL", "")

# Prodamus — international cards (foreign-card acquiring ЮKassa can't do).
# Phase 1 ships the config slot only; the provider module + webhook land in
# Phase 2 once Prodamus enables the international method for the account
# (needs a separate sign-off from their профильный отдел) — see
# docs/PRODAMUS_INTL_BILLING_PLAN.md and docs/PRODAMUS_CALL_CHECKLIST_RU.md.
PRODAMUS_FORM_URL = os.getenv("PRODAMUS_FORM_URL", "")
PRODAMUS_SECRET_KEY = os.getenv("PRODAMUS_SECRET_KEY", "")

PADDLE_WEBHOOK_SECRET = os.getenv("PADDLE_WEBHOOK_SECRET", "")
PADDLE_API_KEY = os.getenv("PADDLE_API_KEY", "")

# Security (Phase 3)
GEN_COOLDOWN_SEC = int(os.getenv("PLG_GEN_COOLDOWN_SEC", "15"))
GEN_HOURLY_LIMIT = int(os.getenv("PLG_GEN_HOURLY_LIMIT", "20"))
IP_HOURLY_LIMIT = int(os.getenv("PLG_IP_HOURLY_LIMIT", "100"))
SIGNUP_IP_HOURLY_LIMIT = int(os.getenv("PLG_SIGNUP_IP_HOURLY_LIMIT", "5"))
# Password-login brute-force lockout (see cloud/app/auth_login.py). Login is
# proxied through the API so these can be enforced; N failed attempts for the
# same account (or from the same IP) inside the window lock further attempts
# for the rest of that window. A success clears the account counter.
LOGIN_MAX_FAILURES = int(os.getenv("PLG_LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCKOUT_SEC = int(os.getenv("PLG_LOGIN_LOCKOUT_SEC", "900"))
# CORS: desktop client uses Bearer tokens, not cookies — credentials off, lock
# origins. Comma-separated list, or "*" for non-credentialed any-origin.
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("PLG_ALLOWED_ORIGINS", "*").split(",") if o.strip()
] or ["*"]
# Hard cap on request body size — guards against memory/cost-exhaustion bodies
# while staying well above any legitimate sample catalog. 2 MB default.
MAX_BODY_BYTES = int(os.getenv("PLG_MAX_BODY_BYTES", str(2 * 1024 * 1024)))
IP_DAILY_GEN_ALERT = int(os.getenv("PLG_IP_DAILY_GEN_ALERT", "50"))
LLM_QUEUE_SLOTS = int(os.getenv("PLG_LLM_QUEUE_SLOTS", "8"))
LLM_QUEUE_TIMEOUT_SEC = int(os.getenv("PLG_LLM_QUEUE_TIMEOUT_SEC", "120"))
REQUIRE_DEVICE_BINDING = os.getenv("PLG_REQUIRE_DEVICE_BINDING", "true").lower() in (
    "1",
    "true",
    "yes",
)

# Default "none": Cloudflare Turnstile is excluded for 152-ФЗ reasons (data
# leaves RF) and Yandex SmartCaptcha proved unreachable for users whose
# network can't reach Yandex Cloud, silently blocking signup with no error
# shown (the project issue log (not included) §0aj). Rely on rate-limits + brute-force lockout
# instead (PLG_LOGIN_MAX_FAILURES/PLG_LOGIN_LOCKOUT_SEC).
CAPTCHA_PROVIDER = os.getenv("PLG_CAPTCHA_PROVIDER", "none").lower()
TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "")
HCAPTCHA_SITE_KEY = os.getenv("HCAPTCHA_SITE_KEY", "")
HCAPTCHA_SECRET_KEY = os.getenv("HCAPTCHA_SECRET_KEY", "")
YANDEX_CAPTCHA_SITE_KEY = os.getenv("YANDEX_CAPTCHA_SITE_KEY", "")
YANDEX_CAPTCHA_SECRET_KEY = os.getenv("YANDEX_CAPTCHA_SECRET_KEY", "")

ADMIN_SECRET = os.getenv("PLG_ADMIN_SECRET", "")
# Production terminates TLS on our own Caddy on the Timeweb RU VDS
# (cloud/infra/server-scripts/Caddyfile does `header_up X-Forwarded-For
# {remote_host}`), so the real client IP is XFF[0]. No Cloudflare in the path.
TRUSTED_PROXY = os.getenv("PLG_TRUSTED_PROXY", "nginx").lower()

# Offline IP -> country DB (DB-IP Lite, .mmdb). Used only for price/currency
# routing. The file is not shipped by default; drop it here and refresh it
# once a quarter (cloud/infra/ru-hosting-runbook.md). Absent -> lookups return
# None and callers fall back to the domestic tier.
GEOIP_DB_PATH = os.getenv(
    "PLG_GEOIP_DB", str(Path(__file__).resolve().parent / "data" / "dbip-country-lite.mmdb")
)

# Infra (Phase 4)
PLG_ENV = os.getenv("PLG_ENV", os.getenv("ENVIRONMENT", "development"))
REDIS_URL = os.getenv("REDIS_URL", "") or os.getenv("UPSTASH_REDIS_URL", "")

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))

WAITLIST_MODE = os.getenv("PLG_WAITLIST_MODE", "").lower() in ("1", "true", "yes")
INVITE_ONLY = os.getenv("PLG_INVITE_ONLY", "").lower() in ("1", "true", "yes")

LLM_REDIS_SLOT_KEY = os.getenv("PLG_LLM_REDIS_SLOT_KEY", "plg:llm:active")

# Legal (Phase 5)
LEGAL_TERMS_VERSION = os.getenv("PLG_LEGAL_TERMS_VERSION", "2.1")
LEGAL_PRIVACY_VERSION = os.getenv("PLG_LEGAL_PRIVACY_VERSION", "2.3")
LEGAL_AGE_MIN = int(os.getenv("PLG_LEGAL_MIN_AGE", "18"))
PROMPT_LOG_RETENTION_DAYS = int(os.getenv("PLG_PROMPT_LOG_RETENTION_DAYS", "90"))
# privacy.html §2: payment data is kept 5 years after account deletion.
PAYMENT_LOG_RETENTION_DAYS = int(os.getenv("PLG_PAYMENT_LOG_RETENTION_DAYS", "1825"))
DATA_REGION = os.getenv("PLG_DATA_REGION", "ru")
SUPPORT_EMAIL = os.getenv("PLG_SUPPORT_EMAIL", "help@example.com")

# Desktop bootstrap (GET /v1/region) — public anon keys only.
REGION_CIS_SUPABASE_URL = os.getenv("PLG_REGION_CIS_SUPABASE_URL", "").rstrip("/")
REGION_CIS_SUPABASE_ANON_KEY = os.getenv("PLG_REGION_CIS_SUPABASE_ANON_KEY", "").strip()
REGION_CIS_CLOUD_URL = os.getenv("PLG_REGION_CIS_CLOUD_URL", "").rstrip("/")
REGION_INTL_SUPABASE_URL = os.getenv("PLG_REGION_INTL_SUPABASE_URL", "").rstrip("/")
REGION_INTL_SUPABASE_ANON_KEY = os.getenv("PLG_REGION_INTL_SUPABASE_ANON_KEY", "").strip()
REGION_INTL_CLOUD_URL = os.getenv("PLG_REGION_INTL_CLOUD_URL", "").rstrip("/")

# Ops (Phase 6)
# No Telegram channel or status page exist yet — empty defaults so a fresh
# deploy that forgets to set these env vars serves nothing instead of the
# old example.com brand's dead links. Fill in real URLs once they exist.
SUPPORT_TELEGRAM = os.getenv("PLG_SUPPORT_TELEGRAM", "")
SUPPORT_SLA_HOURS = os.getenv("PLG_SUPPORT_SLA_HOURS", "24-48")
SUPPORT_UPDATES_URL = os.getenv("PLG_SUPPORT_UPDATES_URL", "")
STATUS_PAGE_URL = os.getenv("PLG_STATUS_PAGE_URL", "")

EMAIL_FROM = os.getenv("PLG_EMAIL_FROM", "Pluggin' <noreply@example.com>")
UNISENDER_API_KEY = os.getenv("UNISENDER_API_KEY", "").strip()
UNISENDER_VERIFICATION_TEMPLATE_ID = os.getenv(
    "UNISENDER_VERIFICATION_TEMPLATE_ID", ""
).strip()
UNISENDER_PASSWORD_RESET_TEMPLATE_ID = os.getenv(
    "UNISENDER_PASSWORD_RESET_TEMPLATE_ID", ""
).strip()
# Verification codes are short lived and their raw value is never persisted.
# The service key is a safe server-only fallback until PLG_EMAIL_VERIFICATION_SECRET
# is configured as a separate production secret.
EMAIL_VERIFICATION_SECRET = (
    os.getenv("PLG_EMAIL_VERIFICATION_SECRET", "").strip() or SUPABASE_SERVICE_KEY
)
EMAIL_VERIFICATION_TTL_MINUTES = int(os.getenv("PLG_EMAIL_VERIFICATION_TTL_MINUTES", "15"))
EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS = int(
    os.getenv(
        "PLG_EMAIL_VERIFICATION_COOLDOWN_SECONDS",
        os.getenv("PLG_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS", "60"),
    )
)
EMAIL_VERIFICATION_MAX_ATTEMPTS = int(os.getenv("PLG_EMAIL_VERIFICATION_MAX_ATTEMPTS", "5"))

POSTHOG_API_KEY = os.getenv("POSTHOG_API_KEY", "")
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://eu.i.posthog.com").rstrip("/")
