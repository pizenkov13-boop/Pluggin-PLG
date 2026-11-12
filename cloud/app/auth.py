"""JWT validation + Supabase service client."""

from __future__ import annotations

from typing import Any

from yarl import URL

import jwt
from fastapi import Header, HTTPException
from supabase import Client, create_client

from cloud.app.config import (
    DEV_BYPASS_AUTH,
    DEV_USER_ID,
    MIN_CLIENT_VERSION,
    SUPABASE_JWT_SECRET,
    SUPABASE_JWKS_URL,
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
)


def _parse_version(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in (text or "0").split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts or (0,))


def require_client_version(header: str | None) -> None:
    if not header:
        raise HTTPException(400, "Missing X-PLG-Version header.")
    if _parse_version(header) < _parse_version(MIN_CLIENT_VERSION):
        raise HTTPException(
            426,
            f"App update required. Minimum version: {MIN_CLIENT_VERSION}",
        )


def service_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise HTTPException(503, "Cloud backend not configured.")
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    # Self-hosted deployments may expose PostgREST directly (usually the
    # ``supabase-rest`` container) rather than through Supabase Kong.  The
    # SDK assumes a public gateway and appends `/rest/v1`; direct PostgREST
    # expects requests at its root, so remove that prefix only in this mode.
    if "supabase-rest" in SUPABASE_URL:
        client.postgrest.base_url = URL(SUPABASE_URL)
    return client


def decode_user_token(authorization: str | None) -> str:
    if DEV_BYPASS_AUTH:
        return DEV_USER_ID

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing authorization token.")

    token = authorization.split(" ", 1)[1].strip()
    if not SUPABASE_JWT_SECRET and not SUPABASE_JWKS_URL:
        raise HTTPException(503, "JWT verification not configured.")

    try:
        if SUPABASE_JWT_SECRET:
            payload = jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
        else:
            from jwt import PyJWKClient

            signing_key = PyJWKClient(SUPABASE_JWKS_URL).get_signing_key_from_jwt(token)
            # Only asymmetric algorithms here. Allowing HS256 alongside a key
            # fetched from JWKS enables the classic alg-confusion attack (forge an
            # HS256 token using the public key as the HMAC secret). HS256 is only
            # ever valid via the shared-secret branch above.
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["ES256", "RS256"],
                audience="authenticated",
            )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid or expired token.") from exc

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(401, "Invalid token subject.")
    return str(sub)


def current_user(
    authorization: str | None = Header(default=None),
    x_plg_version: str | None = Header(default=None, alias="X-PLG-Version"),
) -> str:
    require_client_version(x_plg_version)
    return decode_user_token(authorization)


def profile_row(client: Client, user_id: str) -> dict[str, Any]:
    result = client.table("profiles").select("*").eq("id", user_id).maybe_single().execute()
    row = result.data
    if not row:
        client.table("profiles").insert({"id": user_id, "status": "trial"}).execute()
        result = client.table("profiles").select("*").eq("id", user_id).single().execute()
        row = result.data
    return row
