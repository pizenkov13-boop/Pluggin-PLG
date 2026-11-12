"""PLG Cloud API — auth proxy, quota, billing, beat generation, security."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from supabase import Client

from cloud.app.abuse import record_generation_ip, list_alerts
from cloud.app.admin import ack_alert, admin_dashboard, ban_entity, require_admin, unban_entity
from cloud.app.auth import current_user, profile_row, service_client
from cloud.app.billing import (
    activate_subscription,
    billing_snapshot,
    claim_idempotency,
    expire_subscription,
    pick_checkout_provider,
    release_claim_on_failure,
    resolve_price_tier,
    resolve_web_offer_tier,
)
from cloud.app.pricing import normalize_tier, price_entry, price_label
from cloud.app.promo import clamp_promo_tier, promo_status
from cloud.app.checkout_links import (
    clean_referral_code,
    consume_checkout_link,
    create_checkout_link,
    lock_checkout_link_price,
    public_checkout_offer,
    read_checkout_link,
)
from cloud.app.config import BILLING_CHECKOUT_PAGE_URL
from cloud.app.config import (
    ALLOWED_ORIGINS,
    APP_VERSION,
    CAPTCHA_PROVIDER,
    HCAPTCHA_SITE_KEY,
    MAX_BODY_BYTES,
    MAX_PROMPT_CHARS,
    TURNSTILE_SITE_KEY,
    YANDEX_CAPTCHA_SITE_KEY,
    YOOKASSA_VERIFY_IP,
    YOOKASSA_WEBHOOK_IPS,
)
from cloud.app.captcha import captcha_required
from cloud.app.devices import register_device, touch_device
from cloud.app.kill_switch import check_kill_switch, record_spend
from cloud.app.llm_proxy import generate_beat_pattern
from cloud.app.prompt_guard import sanitize_prompt
from cloud.app.providers import paddle_provider, prodamus_provider, stripe_provider, yookassa_provider
from cloud.app.queue import llm_slot
from cloud.app.quota import (
    consume_beat,
    quota_snapshot,
    refund_beat,
    reserve_beat,
    roll_profile,
    save_profile,
)
from cloud.app.rate_limit import (
    check_generate_limits,
    check_ip_limit,
    check_signup_limit,
)
from cloud.app.security import (
    client_country,
    client_ip,
    enforce_not_banned,
    ip_in_allowlist,
    require_registered_device,
)
from cloud.app.signup import signup_user
from cloud.app.auth_login import login_user
from cloud.app.email_verification import issue_code, verify_code
from cloud.app.password_reset import complete_reset, issue_reset_code
from cloud.app.observability import health_payload
from cloud.app.feature_flags import flags_snapshot, flag_enabled, invalidate_cache
from cloud.app.waitlist import join_waitlist, generate_invite_codes
from cloud.app.legal import (
    delete_user_account,
    legal_snapshot,
    purge_generation_logs,
    purge_payment_events,
    require_payment_terms_acceptance,
)
from cloud.app.status_page import status_payload
from cloud.app.feedback import submit_feedback
from cloud.app.admin_ops import list_users, get_user_detail, patch_user_quota, ops_dashboard
from cloud.app.analytics_ops import admin_metrics, admin_referral_report, mark_first_beat, track_event
from cloud.app.beat_data import record_beat_event, upsert_beat
from cloud.app.privacy import exception_kind
from cloud.app.beat_ranker import build_global_reward_snapshot
from cloud.app.sentry_init import init_sentry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("plg.cloud")

# init_sentry() no-ops when SENTRY_DSN is unset — safe to always call.
init_sentry()

app = FastAPI(title="PLG Cloud API", version=APP_VERSION)

# Desktop client authenticates with Bearer tokens, not cookies — credentials off
# (and "*" + credentials is an invalid combo). Lock origins via PLG_ALLOWED_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@app.middleware("http")
async def hardening_middleware(request: Request, call_next):
    # Reject oversized bodies up front (defends the LLM endpoint from giant
    # catalog/user_profile payloads driving cost/memory).
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request body too large."})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length."})
    response = await call_next(request)
    for key, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    return response

_PUBLIC_PREFIXES = (
    "/health",
    "/v1/health",
    "/v1/billing/webhooks/",
    "/v1/auth/signup",
    "/v1/auth/verify-email",
    "/v1/auth/resend-verification",
    "/v1/auth/config",
    "/v1/flags",
    "/v1/waitlist/",
    "/v1/legal",
    "/v1/status",
    "/v1/release/",
)


_IP_EXEMPT_PREFIXES = _PUBLIC_PREFIXES + (
    "/v1/me",
    "/v1/billing/status",
    "/v1/flags/me",
)


@app.middleware("http")
async def ip_rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/v1/") and not any(path.startswith(p) for p in _IP_EXEMPT_PREFIXES):
        ip = client_ip(request)
        try:
            check_ip_limit(ip)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


class DeviceBody(BaseModel):
    device_id: str
    device_name: str | None = None


class GenerateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    catalog: dict[str, Any] | None = None
    user_profile: dict[str, Any] | None = None
    device_id: str | None = Field(default=None, max_length=200)
    locale: str | None = Field(default=None, max_length=16)
    smart_plan: bool | None = None


class GenreResolveBody(BaseModel):
    prompt: str = Field(default="", max_length=2000)


class CheckoutBody(BaseModel):
    price_tier: str | None = None
    accept_payment_terms: bool = False


class CheckoutLinkBody(BaseModel):
    accept_payment_terms: bool = False
    # Paid-placement referral attribution: the value of the first-party
    # `plg_ref` cookie set by example.com/r/<code>, read client-side by
    # checkout.js (cross-origin fetch to this API won't carry that cookie
    # automatically, so it's forwarded explicitly). Sanitized server-side
    # in clean_referral_code before it ever reaches YooKassa metadata.
    referral_code: str | None = Field(default=None, max_length=64)


class SignupBody(BaseModel):
    email: str
    password: str
    device_id: str | None = None
    captcha_token: str | None = None
    website: str | None = None  # honeypot — must stay empty
    accept_terms: bool = False
    confirm_age: bool = False
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    referrer: str | None = None


class LoginBody(BaseModel):
    email: str
    password: str


class VerifyEmailBody(BaseModel):
    user_id: str = Field(min_length=36, max_length=36)
    code: str = Field(min_length=6, max_length=12)


class ResendVerificationBody(BaseModel):
    user_id: str = Field(min_length=36, max_length=36)


class PasswordResetRequestBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class PasswordResetCompleteBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=6, max_length=12)
    new_password: str = Field(min_length=8, max_length=256)


class FeedbackBody(BaseModel):
    category: str = "general"
    message: str
    email: str | None = None
    attach_log: bool = False
    log_excerpt: str | None = None
    platform: str | None = None


class BeatSyncBody(BaseModel):
    beat_id: str = Field(min_length=1, max_length=80)
    parent_beat_id: str | None = Field(default=None, max_length=80)
    prompt_hash: str | None = Field(default=None, max_length=128)
    prompt_preview: str | None = Field(default=None, max_length=280)
    locale: str | None = Field(default=None, max_length=16)
    bpm: float | None = None
    style: str | None = Field(default=None, max_length=160)
    key: str | None = Field(default=None, max_length=16)
    scale: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=160)
    quality: dict[str, Any] | None = None
    pipeline: dict[str, Any] | None = None
    sample_picks: dict[str, Any] | None = None
    features: dict[str, Any] | None = None
    pattern: dict[str, Any] | None = None
    pattern_version: int | None = 1
    pattern_size_bytes: int | None = None
    source: str | None = Field(default="desktop", max_length=80)


class BeatEventBody(BaseModel):
    event_type: str = Field(min_length=1, max_length=32)
    source: str | None = Field(default="desktop", max_length=80)
    properties: dict[str, Any] | None = None


class QuotaPatchBody(BaseModel):
    beats_used: int | None = None
    status: str | None = None


class WaitlistBody(BaseModel):
    email: str
    ramp_tier: str | None = "wave_200"


class InviteGenBody(BaseModel):
    count: int = 10
    ramp_tier: str = "wave_200"
    max_uses: int | None = 1


class FlagPatchBody(BaseModel):
    key: str
    enabled: bool
    rollout_pct: int | None = None


class BanBody(BaseModel):
    ban_type: str
    ban_value: str
    reason: str | None = None
    expires_at: str | None = None


class ProdamusTestCheckoutBody(BaseModel):
    user_id: str
    price_tier: str = "ru"


class ManualRefundBody(BaseModel):
    user_id: str
    reason: str | None = None


class ManualActivateBody(BaseModel):
    user_id: str
    order_id: str
    price_tier: str
    referral_code: str | None = None
    reason: str | None = None


@app.api_route("/health", methods=["GET", "HEAD"])
def health() -> dict[str, Any]:
    # Uptime monitors (UptimeRobot et al.) probe with HEAD by default — GET-only
    # made every check a false "down" (405), not an actual outage.
    return health_payload()


@app.api_route("/v1/health", methods=["GET", "HEAD"])
def health_v1() -> dict[str, Any]:
    return health_payload()


@app.get("/v1/region")
def region_bootstrap(request: Request, region: str | None = None) -> dict[str, Any]:
    from cloud.app.region import region_payload

    return region_payload(request, explicit=region)


@app.get("/v1/auth/config")
def auth_config() -> dict[str, Any]:
    site_key = ""
    if CAPTCHA_PROVIDER == "turnstile":
        site_key = TURNSTILE_SITE_KEY
    elif CAPTCHA_PROVIDER == "hcaptcha":
        site_key = HCAPTCHA_SITE_KEY
    elif CAPTCHA_PROVIDER == "yandex":
        site_key = YANDEX_CAPTCHA_SITE_KEY
    return {
        "ok": True,
        "captcha_provider": CAPTCHA_PROVIDER,
        "captcha_site_key": site_key,
        "captcha_required": captcha_required(),
    }


@app.get("/v1/flags")
def get_flags() -> dict[str, Any]:
    return {"ok": True, "flags": flags_snapshot(service_client(), user_id=None)}


@app.get("/v1/flags/me")
def get_flags_me(user_id: str = Depends(current_user)) -> dict[str, Any]:
    client = service_client()
    return {"ok": True, "flags": flags_snapshot(client, user_id=user_id)}


@app.post("/v1/waitlist/join")
def waitlist_join(body: WaitlistBody) -> dict[str, Any]:
    return join_waitlist(service_client(), body.email, body.ramp_tier or "wave_200")


@app.post("/v1/auth/login")
def auth_login(body: LoginBody, request: Request) -> dict[str, Any]:
    # Proxied so PLG can rate-limit password guessing (auth_login.login_user).
    # Not added to _IP_EXEMPT_PREFIXES, so the generic 100/h/IP middleware
    # limit still applies on top of the per-account + per-IP lockout.
    return login_user(
        email=body.email,
        password=body.password,
        remote_ip=client_ip(request),
    )


@app.post("/v1/auth/signup")
async def auth_signup(
    body: SignupBody, request: Request, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    client = service_client()
    ip = client_ip(request)
    check_signup_limit(ip)
    return await signup_user(
        client,
        email=body.email,
        password=body.password,
        device_id=body.device_id,
        captcha_token=body.captcha_token,
        honeypot=body.website,
        remote_ip=ip,
        background_tasks=background_tasks,
        accept_terms=body.accept_terms,
        confirm_age=body.confirm_age,
        utm_source=body.utm_source,
        utm_medium=body.utm_medium,
        utm_campaign=body.utm_campaign,
        utm_content=body.utm_content,
        referrer=body.referrer,
    )


@app.post("/v1/auth/verify-email")
def auth_verify_email(body: VerifyEmailBody) -> dict[str, Any]:
    return verify_code(service_client(), user_id=body.user_id, code=body.code)


@app.post("/v1/auth/resend-verification")
def auth_resend_verification(body: ResendVerificationBody) -> dict[str, Any]:
    issue_code(service_client(), user_id=body.user_id)
    return {"ok": True, "message": "Verification code sent."}


@app.post("/v1/auth/request-password-reset")
def auth_request_password_reset(body: PasswordResetRequestBody, request: Request) -> dict[str, Any]:
    check_ip_limit(client_ip(request))
    issue_reset_code(service_client(), email=body.email)
    return {"ok": True, "message": "If an account exists, a reset code was sent."}


@app.post("/v1/auth/complete-password-reset")
def auth_complete_password_reset(body: PasswordResetCompleteBody, request: Request) -> dict[str, Any]:
    check_ip_limit(client_ip(request))
    return complete_reset(
        service_client(), email=body.email, code=body.code, new_password=body.new_password
    )


@app.get("/v1/status")
def public_status() -> dict[str, Any]:
    return status_payload(service_client())


@app.get("/v1/release/manifest")
def release_manifest() -> dict[str, Any]:
    import os

    return {
        "ok": True,
        "version": APP_VERSION,
        "url": os.getenv("PLG_RELEASE_DOWNLOAD_URL", ""),
        "notes": os.getenv("PLG_RELEASE_NOTES", "Bug fixes and improvements."),
        "sha256": os.getenv("PLG_RELEASE_SHA256", ""),
        # Ed25519 signature over "version\nsha256" from scripts/sign_release.py.
        # The desktop updater rejects the update if this doesn't verify against
        # the public key baked into the client, so a compromised backend can't
        # push a malicious binary.
        "signature": os.getenv("PLG_RELEASE_SIGNATURE", ""),
        "mandatory": os.getenv("PLG_RELEASE_MANDATORY", "").lower() in ("1", "true", "yes"),
    }


@app.get("/v1/legal")
def legal_info() -> dict[str, Any]:
    return legal_snapshot()


@app.delete("/v1/account")
def account_delete(user_id: str = Depends(current_user)) -> dict[str, Any]:
    return delete_user_account(service_client(), user_id)


@app.get("/v1/me")
def me(
    request: Request,
    user_id: str = Depends(current_user),
    x_plg_device: str | None = Header(default=None, alias="X-PLG-Device"),
) -> dict[str, Any]:
    client = service_client()
    ip = client_ip(request)
    enforce_not_banned(client, user_id=user_id, device_id=x_plg_device, ip=ip)
    row = roll_profile(profile_row(client, user_id))
    save_profile(client, user_id, row)
    if x_plg_device:
        touch_device(client, user_id, x_plg_device)
    offer_tier = resolve_web_offer_tier(client_country(request), client=client)
    return {
        "ok": True,
        "user_id": user_id,
        "quota": quota_snapshot(row),
        "billing": billing_snapshot(row, offer_tier=offer_tier),
    }


@app.post("/v1/devices/register")
def devices_register(
    body: DeviceBody,
    request: Request,
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    client = service_client()
    ip = client_ip(request)
    enforce_not_banned(client, user_id=user_id, device_id=body.device_id, ip=ip)
    return register_device(client, user_id, body.device_id, body.device_name)


@app.post("/v1/generate")
def generate(
    body: GenerateBody,
    request: Request,
    user_id: str = Depends(current_user),
    x_plg_device: str | None = Header(default=None, alias="X-PLG-Device"),
) -> dict[str, Any]:
    client = service_client()
    ip = client_ip(request)
    device_id = body.device_id or x_plg_device

    if flag_enabled(client, "maintenance_mode", user_id=user_id):
        raise HTTPException(503, "Maintenance in progress. Try again shortly.")

    enforce_not_banned(client, user_id=user_id, device_id=device_id, ip=ip)
    device_id = require_registered_device(client, user_id, device_id)

    row = roll_profile(profile_row(client, user_id))
    plan = str(row.get("plan") or "base")
    check_kill_switch(client, plan)

    # Rate-limit immediately before the atomic reserve + LLM: a user's cooldown/
    # hourly budget is only spent on requests that actually proceed to generate,
    # not ones rejected by ban/device/kill-switch. IP-level load-shedding already
    # runs in the middleware.
    check_generate_limits(user_id)

    # Reject a bad prompt BEFORE reserving: sanitize_prompt raises 400 on empty /
    # oversized / flagged prompts, and a credit reserved ahead of it was never
    # given back.
    prompt = sanitize_prompt(body.prompt)

    # Reserve one credit ATOMICALLY before the expensive LLM call so concurrent
    # requests on the same account can't over-generate / over-spend. The credit
    # is returned if ANYTHING fails before the response is built — the LLM call,
    # the LLM-queue timeout, or the post-generation bookkeeping. (Falls back to
    # legacy check-then-consume pre-migration.)
    reservation = reserve_beat(client, user_id, row)

    try:
        with llm_slot():
            pattern, meta = generate_beat_pattern(
                prompt,
                plan=plan,
                catalog=body.catalog,
                user_profile=body.user_profile,
                locale=body.locale,
                smart_plan=body.smart_plan,
            )

        if not reservation["atomic"]:
            consume_beat(client, user_id, row)

        quota = quota_snapshot(roll_profile(profile_row(client, user_id)))
        record_spend(client, plan, meta.get("cost_usd"))

        client.table("generation_logs").insert(
            {
                "user_id": user_id,
                "model": meta.get("model", "unknown"),
                "prompt_chars": meta.get("prompt_chars", len(prompt)),
                "cost_usd": meta.get("cost_usd"),
                "ip_address": ip,
                "device_id": device_id,
                # Which purchase funds the entitlement this generation was made
                # under — NULL for the free trial beat (no purchase yet) and for
                # any account that has never subscribed. Set once per purchase in
                # activate_subscription(); untouched by quota rollover.
                "payment_event_id": row.get("current_payment_event_id"),
            }
        ).execute()

        record_generation_ip(client, ip, user_id, device_id)
        mark_first_beat(client, user_id)
        track_event(client, "beat_generated", user_id=user_id, properties={"model": meta.get("model")})

        if device_id:
            touch_device(client, user_id, device_id)
    except Exception:
        if reservation["atomic"]:
            refund_beat(client, user_id, was_trial=reservation["was_trial"])
        raise

    return {
        "ok": True,
        "pattern": pattern,
        "quota": quota,
        "model": meta.get("model"),
    }


@app.post("/v1/genre/resolve")
def genre_resolve(
    body: GenreResolveBody,
    request: Request,
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    """Server-side genre brain — subscribers keep the flagship any-prompt
    routing even though the gateway key lives on the server. Cheap call
    (~220 max tokens), cached per prompt on the client, no beat quota."""
    client = service_client()
    if flag_enabled(client, "maintenance_mode", user_id=user_id):
        raise HTTPException(503, "Maintenance in progress. Try again shortly.")
    enforce_not_banned(client, user_id=user_id, device_id=None, ip=client_ip(request))

    prompt = sanitize_prompt(body.prompt)
    if not prompt.strip():
        return {"ok": True, "genre": None, "mood": [], "energy": None}

    from genre_brain import classify_prompt

    try:
        result = classify_prompt(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cloud genre brain failed type=%s", exception_kind(exc))
        raise HTTPException(503, "Genre brain busy. Try again in a minute.") from exc
    if not result:
        return {"ok": True, "genre": None, "mood": [], "energy": None}
    return {"ok": True, **result}


@app.post("/v1/feedback")
def feedback_submit(
    body: FeedbackBody,
    request: Request,
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    client = service_client()
    email = body.email
    if not email:
        try:
            from cloud.app.legal import _user_email
            email = _user_email(client, user_id)
        except Exception:  # noqa: BLE001
            email = None
    log_excerpt = body.log_excerpt if body.attach_log else None
    return submit_feedback(
        client,
        user_id=user_id,
        email=email,
        category=body.category,
        message=body.message,
        platform=body.platform or request.headers.get("User-Agent"),
        log_excerpt=log_excerpt,
    )


@app.post("/v1/beats")
def beat_sync(body: BeatSyncBody, user_id: str = Depends(current_user)) -> dict[str, Any]:
    client = service_client()
    payload = body.dict(exclude_none=True)
    out = upsert_beat(client, user_id, payload)
    track_event(
        client,
        "beat_synced",
        user_id=user_id,
        properties={
            "beat_id": body.beat_id,
            "style": body.style,
            "genre": (body.quality or {}).get("genre") if body.quality else None,
            "quality_score": (body.quality or {}).get("score") if body.quality else None,
        },
    )
    return out


@app.post("/v1/beats/{beat_id}/events")
def beat_event(
    beat_id: str,
    body: BeatEventBody,
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    client = service_client()
    out = record_beat_event(
        client,
        user_id,
        beat_id,
        body.event_type,
        source=body.source,
        properties=body.properties or {},
    )
    track_event(
        client,
        f"beat_{body.event_type}",
        user_id=user_id,
        properties={"beat_id": beat_id, **(body.properties or {})},
    )
    return out


@app.get("/v1/beats/reward-status")
def beat_reward_cloud_status(
    genre: str | None = None,
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    # User auth gates the endpoint; v1 returns the global aggregate because the
    # moat is a shared network effect, not just a per-device taste model.
    _ = user_id
    return build_global_reward_snapshot(service_client(), genre=genre)


@app.post("/v1/auth/logout")
def logout(user_id: str = Depends(current_user)) -> dict[str, bool]:
    logger.info("logout user=%s", user_id)
    return {"ok": True}


@app.get("/v1/billing/status")
def billing_status(
    request: Request,
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    client = service_client()
    enforce_not_banned(client, user_id=user_id, ip=client_ip(request))
    row = roll_profile(profile_row(client, user_id))
    save_profile(client, user_id, row)
    offer_tier = resolve_web_offer_tier(client_country(request), client=client)
    return {"ok": True, "billing": billing_snapshot(row, offer_tier=offer_tier)}


@app.get("/v1/billing/price")
def billing_price(request: Request, region: str | None = None) -> dict[str, Any]:
    """Public, unauthenticated: the price a visitor from this IP would pay.

    Drives the landing page before sign-up. The IP is resolved to a country
    entirely on this server (local DB-IP database, see cloud/app/geoip.py) —
    nothing is sent to any third party. While ``intl_billing`` is off this is
    499 ₽ for everyone. ``?region=`` overrides the geo (support).
    """
    client = service_client()
    tier = resolve_web_offer_tier(
        client_country(request), requested_region=region, client=client
    )
    entry = price_entry(tier)
    return JSONResponse(
        {
            "ok": True,
            "price_tier": tier,
            "price_label": price_label(tier),
            "currency": entry["currency"],
            "amount": entry["amount_minor"] / 100,
            "promo": promo_status(client),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/v1/billing/checkout")
def billing_checkout(
    body: CheckoutBody,
    request: Request,
    user_id: str = Depends(current_user),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict[str, Any]:
    client = service_client()
    enforce_not_banned(client, user_id=user_id, ip=client_ip(request))
    require_payment_terms_acceptance(body.accept_payment_terms)
    # Direct checkout (no frozen link) — a client-supplied `ru_promo` is only
    # honoured while the promo still has budget; otherwise it drops to `ru`.
    tier = clamp_promo_tier(resolve_price_tier(body.price_tier, accept_language), client)
    provider = pick_checkout_provider(tier, client=client)
    if provider == "yookassa":
        out = yookassa_provider.create_checkout(client, user_id, tier)
    elif provider == "prodamus":
        out = prodamus_provider.create_checkout(client, user_id, tier)
    elif provider == "stripe":
        out = stripe_provider.create_checkout(user_id, tier)
    else:
        raise HTTPException(503, f"Checkout provider {provider} not implemented.")
    return {"ok": True, **out}


@app.post("/v1/billing/checkout-link")
def billing_checkout_link(
    request: Request,
    user_id: str = Depends(current_user),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict[str, Any]:
    """Issue a one-use web checkout link; this endpoint does not create a payment."""
    from urllib.parse import urlencode

    client = service_client()
    enforce_not_banned(client, user_id=user_id, ip=client_ip(request))
    tier = resolve_price_tier("ru", accept_language)
    token = create_checkout_link(client, user_id=user_id, price_tier=tier)
    return {"ok": True, "checkout_url": f"{BILLING_CHECKOUT_PAGE_URL}?{urlencode({'token': token})}"}


@app.get("/v1/billing/checkout-link/{token}")
def billing_checkout_link_offer(
    token: str,
    request: Request,
    region: str | None = None,
) -> dict[str, Any]:
    """Public offer data for a valid opaque web checkout link.

    The first call freezes the price for this link from the caller's IP country
    (``?region=`` overrides it, for support); later calls return the frozen
    tier so the amount shown on checkout.html can't change before the pay click.
    """
    client = service_client()
    row = read_checkout_link(client, token)
    tier = lock_checkout_link_price(
        client, token, row, country=client_country(request), requested_region=region
    )
    return {"ok": True, **public_checkout_offer(row, tier=tier), "promo": promo_status(client)}


@app.post("/v1/billing/checkout-link/{token}")
def billing_checkout_link_pay(
    token: str,
    body: CheckoutLinkBody,
    request: Request,
    region: str | None = None,
) -> dict[str, Any]:
    """Consume a link after the website records payment-terms consent."""
    require_payment_terms_acceptance(body.accept_payment_terms)
    client = service_client()
    row = consume_checkout_link(client, token)
    user_id = str(row["user_id"])
    enforce_not_banned(client, user_id=user_id, ip=client_ip(request))
    referral_code = clean_referral_code(body.referral_code)
    # Normally the GET above already froze the tier onto the row. Fall back to a
    # fresh geo resolve only if this link was never viewed (direct API call).
    if row.get("price_locked"):
        tier = normalize_tier(row["price_tier"])
    else:
        # A real payment is about to be created below, same as the checkout
        # link's own freeze — so this also respects the link reservation
        # buffer, not just confirmed sales (see _domestic_offer_tier).
        tier = resolve_web_offer_tier(
            client_country(request), requested_region=region, client=client, enforce_link_budget=True
        )
    provider = pick_checkout_provider(tier, client=client)
    if provider == "yookassa":
        out = yookassa_provider.create_checkout(client, user_id, tier, referral_code=referral_code)
    elif provider == "prodamus":
        out = prodamus_provider.create_checkout(client, user_id, tier, referral_code=referral_code)
    elif provider == "stripe":
        out = stripe_provider.create_checkout(user_id, tier)
    else:
        raise HTTPException(503, f"Checkout provider {provider} not implemented.")
    return {"ok": True, **out}


@app.post("/v1/billing/webhooks/yookassa")
async def webhook_yookassa(request: Request) -> dict[str, str]:
    # YooKassa has no webhook signature — gate on its published source IPs so the
    # endpoint can't be spammed (the handler still re-fetches the payment from the
    # API, so this is anti-DoS, not the anti-forgery guard).
    if YOOKASSA_VERIFY_IP and YOOKASSA_WEBHOOK_IPS:
        ip = client_ip(request)
        if not ip_in_allowlist(ip, YOOKASSA_WEBHOOK_IPS):
            logger.warning("yookassa webhook rejected: source ip=%s not in allowlist", ip)
            raise HTTPException(403, "Forbidden.")
    client = service_client()
    body = await request.json()
    return yookassa_provider.handle_webhook(client, body)


@app.post("/v1/billing/webhooks/prodamus")
async def webhook_prodamus(request: Request) -> dict[str, str]:
    client = service_client()
    return await prodamus_provider.handle_webhook(client, request)


@app.post("/v1/billing/webhooks/stripe")
async def webhook_stripe(request: Request) -> dict[str, str]:
    client = service_client()
    return await stripe_provider.handle_webhook(client, request)


@app.post("/v1/billing/webhooks/paddle")
async def webhook_paddle(request: Request) -> dict[str, str]:
    client = service_client()
    return await paddle_provider.handle_webhook(client, request)


# --- Admin (manual ban / alerts) -------------------------------------------

@app.get("/v1/admin/dashboard", dependencies=[Depends(require_admin)])
def admin_dash() -> dict[str, Any]:
    client = service_client()
    base = admin_dashboard(client)
    ops = ops_dashboard(client)
    return {**base, **ops}


@app.get("/v1/admin/users", dependencies=[Depends(require_admin)])
def admin_users(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    return list_users(service_client(), limit=limit, offset=offset)


@app.get("/v1/admin/users/{user_id}", dependencies=[Depends(require_admin)])
def admin_user_detail(user_id: str) -> dict[str, Any]:
    return get_user_detail(service_client(), user_id)


@app.patch("/v1/admin/users/{user_id}/quota", dependencies=[Depends(require_admin)])
def admin_user_quota(user_id: str, body: QuotaPatchBody) -> dict[str, Any]:
    return patch_user_quota(
        service_client(),
        user_id,
        beats_used=body.beats_used,
        status=body.status,
    )


@app.get("/v1/admin/metrics", dependencies=[Depends(require_admin)])
def admin_metrics_route(days: int = 30) -> dict[str, Any]:
    return admin_metrics(service_client(), days=days)


@app.get("/v1/admin/referrals", dependencies=[Depends(require_admin)])
def admin_referrals_route(days: int = 90) -> dict[str, Any]:
    return admin_referral_report(service_client(), days=days)


@app.get("/v1/admin/alerts", dependencies=[Depends(require_admin)])
def admin_alerts(limit: int = 50) -> dict[str, Any]:
    return {"ok": True, "alerts": list_alerts(service_client(), limit=limit)}


@app.post("/v1/admin/ban", dependencies=[Depends(require_admin)])
def admin_ban(body: BanBody) -> dict[str, Any]:
    return ban_entity(
        service_client(),
        ban_type=body.ban_type,
        ban_value=body.ban_value,
        reason=body.reason,
        expires_at=body.expires_at,
    )


@app.post("/v1/admin/unban", dependencies=[Depends(require_admin)])
def admin_unban(body: BanBody) -> dict[str, Any]:
    return unban_entity(service_client(), ban_type=body.ban_type, ban_value=body.ban_value)


@app.post("/v1/admin/alerts/{alert_id}/ack", dependencies=[Depends(require_admin)])
def admin_ack_alert(alert_id: str) -> dict[str, Any]:
    return ack_alert(service_client(), alert_id)


@app.post("/v1/admin/invite-codes", dependencies=[Depends(require_admin)])
def admin_generate_invites(body: InviteGenBody) -> dict[str, Any]:
    codes = generate_invite_codes(
        service_client(),
        count=body.count,
        ramp_tier=body.ramp_tier,
        max_uses=body.max_uses,
    )
    return {"ok": True, "codes": codes, "count": len(codes)}


@app.patch("/v1/admin/flags", dependencies=[Depends(require_admin)])
def admin_patch_flag(body: FlagPatchBody) -> dict[str, Any]:
    from datetime import datetime, timezone

    patch: dict[str, Any] = {
        "enabled": body.enabled,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if body.rollout_pct is not None:
        patch["rollout_pct"] = body.rollout_pct
    service_client().table("feature_flags").update(patch).eq("key", body.key).execute()
    invalidate_cache()
    return {"ok": True, "key": body.key}


@app.post("/v1/admin/retention/purge", dependencies=[Depends(require_admin)])
def admin_purge_logs() -> dict[str, Any]:
    client = service_client()
    return {
        "ok": True,
        "generation_logs": purge_generation_logs(client),
        "payment_events": purge_payment_events(client),
    }


@app.post("/v1/admin/billing/prodamus-test-checkout", dependencies=[Depends(require_admin)])
def admin_prodamus_test_checkout(body: ProdamusTestCheckoutBody) -> dict[str, Any]:
    """One-off admin checkout link through Prodamus, bypassing the normal
    price-book provider guard (``allow_any_tier``). Originally added to prove
    the webhook/HMAC on a real payment before ``ru`` became a Prodamus-served
    tier — since 2026-09-29 ``pick_checkout_provider`` already prefers
    Prodamus for ``ru``/``ru_promo`` when configured, so this route is mostly
    useful now for a tier the price book doesn't map to Prodamus. See
    docs/PRODAMUS_INTL_BILLING_PLAN.md and the project issue log (not included) §0ai."""
    client = service_client()
    tier = normalize_tier(body.price_tier)
    logger.warning("admin prodamus test checkout requested user=%s tier=%s", body.user_id, tier)
    out = prodamus_provider.create_checkout(client, body.user_id, tier, allow_any_tier=True)
    return {"ok": True, **out}


@app.post("/v1/admin/billing/manual-refund", dependencies=[Depends(require_admin)])
def admin_manual_refund(body: ManualRefundBody) -> dict[str, Any]:
    """Close a subscriber's access after a refund processed by hand outside
    our webhook — Prodamus refunds are full-only and done manually in their
    merchant panel, not via a webhook event (the project issue log (not included) §0ai), so
    nothing here calls this automatically. Calls the real
    billing.expire_subscription() (status -> expired, grace_until cleared),
    not a raw profiles patch, so it stays consistent with every other refund
    path (yookassa_provider's refund.succeeded webhook does the same call).
    Does not write a payment_events row — a manual refund here won't appear
    in admin_metrics/admin_referral_report; reconcile against the provider's
    own panel if that's needed."""
    client = service_client()
    logger.warning(
        "admin manual refund requested user=%s reason=%s", body.user_id, body.reason or ""
    )
    expire_subscription(client, body.user_id)
    return {"ok": True, "user_id": body.user_id, "status": "expired"}


@app.post("/v1/admin/billing/manual-activate", dependencies=[Depends(require_admin)])
def admin_manual_activate(body: ManualActivateBody) -> dict[str, Any]:
    """Grant entitlement for a real payment whose webhook was lost/rejected
    by a bug on our side — added 2026-09-29 after a confirmed-genuine
    Prodamus payment (order 49304825, 299 RUB ru_promo via SBP) 400'd on
    handle_webhook's now-fixed `sum`/`currency_sum` field-name bug and never
    reached claim_idempotency, so it left no payment_events row and no
    activation. Prodamus's webhook-retry policy is unconfirmed (the project issue log (not included)
    §0ai), so this exists rather than gambling on an automatic retry.

    Reuses the exact same idempotency key scheme as the real webhook
    (``f"success:{order_id}"``) and the real price-book amount for the given
    tier (never a caller-supplied amount) — so this is safe to call even if
    Prodamus's webhook eventually does retry for the same order: whichever
    arrives second just finds the claim already made and is a no-op. Verify
    the payment is genuine (provider's merchant panel / Sentry-captured
    webhook body) before calling this — it grants access exactly like a real
    successful payment would."""
    client = service_client()
    tier = normalize_tier(body.price_tier)
    entry = price_entry(tier)
    idem_key = f"success:{body.order_id}"
    logger.warning(
        "admin manual activate requested user=%s order=%s tier=%s reason=%s",
        body.user_id,
        body.order_id,
        tier,
        body.reason or "",
    )
    if claim_idempotency(
        client,
        provider="prodamus",
        idempotency_key=idem_key,
        event_type="payment.succeeded",
        user_id=body.user_id,
        amount_cents=int(entry["amount_minor"]),
        currency=str(entry["currency"]).lower(),
        external_id=body.order_id,
        price_tier=tier,
        referral_code=body.referral_code,
        payload={"manual_admin_activation": True, "reason": body.reason or ""},
    ):
        return {"ok": True, "status": "duplicate"}

    with release_claim_on_failure(client, provider="prodamus", idempotency_key=idem_key):
        activate_subscription(
            client,
            body.user_id,
            provider="prodamus",
            external_id=body.order_id,
            price_tier=tier,
        )
        try:
            from cloud.app.analytics_ops import track_event
            from cloud.app.email import notify_payment_success
            from cloud.app.legal import _user_email

            track_event(client, "payment_completed", user_id=body.user_id)
            email = _user_email(client, body.user_id)
            if email:
                notify_payment_success(client, body.user_id, email)
        except Exception:  # noqa: BLE001
            logger.exception("post-payment notify failed (manual activation)")

    return {"ok": True, "user_id": body.user_id, "order_id": body.order_id, "status": "activated"}
