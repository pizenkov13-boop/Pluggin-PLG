"""Desktop cloud-auth behavior that must stay stable for the account flow."""

from __future__ import annotations

from types import SimpleNamespace

import plg_cloud


class _Response:
    def __init__(self, status_code: int, payload: dict[str, object], text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self) -> dict[str, object]:
        return self._payload


class _Client:
    def __init__(self, response: _Response, calls: list[tuple[str, str, dict[str, object] | None]]) -> None:
        self.response = response
        self.calls = calls

    def __enter__(self) -> "_Client":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(("POST", url, kwargs.get("json") if isinstance(kwargs.get("json"), dict) else None))
        return self.response


def test_login_maps_bad_credentials_to_stable_error(monkeypatch) -> None:
    """Login is proxied through the PLG API now (/v1/auth/login), which returns
    401 for a bad guess; the UI-facing error_type must stay `invalid_credentials`."""
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(plg_cloud, "cloud_api_url", lambda: "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "app_version", lambda: "1.0.0")
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(
        _Response(401, {"detail": "Invalid email or password."}), calls
    ))

    result = plg_cloud.login("person@example.com", "wrong-password")

    assert result["ok"] is False
    assert result["error_type"] == "invalid_credentials"
    assert calls[0][1].endswith("/v1/auth/login")


def test_login_maps_lockout_to_rate_limit(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(plg_cloud, "cloud_api_url", lambda: "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "app_version", lambda: "1.0.0")
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(
        _Response(429, {"detail": "Too many failed sign-in attempts."}), calls
    ))

    result = plg_cloud.login("person@example.com", "wrong-password")

    assert result["ok"] is False
    assert result["error_type"] == "rate_limit"


def test_login_does_not_claim_device_registered_when_it_fails(monkeypatch) -> None:
    """A failed register_device() (e.g. MAX_DEVICES exceeded) must not be
    masked — otherwise ensure_session()'s ``not _device_registered`` retry
    guard never fires again for the rest of the app session."""
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(plg_cloud, "cloud_api_url", lambda: "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "app_version", lambda: "1.0.0")
    monkeypatch.setattr(plg_cloud, "_store_auth_response", lambda payload: None)
    monkeypatch.setattr(plg_cloud, "load_session", lambda: {"access_token": "tok"})
    monkeypatch.setattr(
        plg_cloud, "register_device", lambda: {"ok": False, "error": "Too many devices."}
    )
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(
        _Response(200, {"ok": True, "session": {"access_token": "tok", "refresh_token": "ref"}}), calls
    ))
    plg_cloud._device_registered = True  # start dirty so a no-op fix can't pass by accident

    result = plg_cloud.login("person@example.com", "password-123")

    assert result["ok"] is True
    assert plg_cloud._device_registered is False


def test_login_marks_device_registered_when_it_succeeds(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(plg_cloud, "cloud_api_url", lambda: "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "app_version", lambda: "1.0.0")
    monkeypatch.setattr(plg_cloud, "_store_auth_response", lambda payload: None)
    monkeypatch.setattr(plg_cloud, "load_session", lambda: {"access_token": "tok"})
    monkeypatch.setattr(plg_cloud, "register_device", lambda: {"ok": True})
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(
        _Response(200, {"ok": True, "session": {"access_token": "tok", "refresh_token": "ref"}}), calls
    ))
    plg_cloud._device_registered = False

    result = plg_cloud.login("person@example.com", "password-123")

    assert result["ok"] is True
    assert plg_cloud._device_registered is True
    assert calls[0][1].endswith("/v1/auth/login")


def test_signup_requires_email_verification_and_clears_old_session(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    cleared: list[bool] = []
    monkeypatch.setattr(plg_cloud, "cloud_api_url", lambda: "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "app_version", lambda: "1.0.0")
    monkeypatch.setattr(plg_cloud, "get_device_id", lambda: "device-1")
    monkeypatch.setattr(plg_cloud, "clear_session", lambda: cleared.append(True))
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(
        _Response(200, {"verification_required": True, "verification_user_id": "user-1"}), calls
    ))

    result = plg_cloud.signup("person@example.com", "password-123", accept_terms=True, confirm_age=True)

    assert result == {
        "ok": True,
        "message": "Verification code sent.",
        "verification_required": True,
        "verification_user_id": "user-1",
    }
    assert cleared == [True]


def test_refresh_session_replaces_expiring_access_token(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    stored: list[dict[str, object]] = []
    monkeypatch.setattr(plg_cloud, "load_session", lambda: {"refresh_token": "old-refresh"})
    monkeypatch.setattr(plg_cloud, "supabase_url", lambda: "https://api.example.test")
    monkeypatch.setattr(plg_cloud, "_supabase_headers", lambda: {"apikey": "test"})
    monkeypatch.setattr(plg_cloud, "_store_auth_response", lambda payload: stored.append(payload))
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(
        _Response(200, {"access_token": "new-access", "refresh_token": "new-refresh"}), calls
    ))

    result = plg_cloud.refresh_session()

    assert result["ok"] is True
    assert stored == [{"access_token": "new-access", "refresh_token": "new-refresh"}]
    assert calls[0][2] == {"refresh_token": "old-refresh"}


def test_token_refresh_without_user_field_keeps_known_email(monkeypatch) -> None:
    # Some GoTrue configs omit "user" from a refresh-token grant response
    # (only login/signup are guaranteed to include it). Losing the email here
    # used to surface as Account showing a login prompt for an already
    # signed-in, fully working session.
    saved: list[dict[str, object]] = []
    monkeypatch.setattr(
        plg_cloud,
        "load_session",
        lambda: {"refresh_token": "old-refresh", "email": "producer@plg.test", "user": {"email": "producer@plg.test"}},
    )
    monkeypatch.setattr(plg_cloud, "save_session", lambda data: saved.append(data))
    monkeypatch.setattr(plg_cloud, "supabase_url", lambda: "https://api.example.test")
    monkeypatch.setattr(plg_cloud, "_supabase_headers", lambda: {"apikey": "test"})
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(
        _Response(200, {"access_token": "new-access", "refresh_token": "new-refresh"}), []
    ))

    result = plg_cloud.refresh_session()

    assert result["ok"] is True
    assert saved[-1]["email"] == "producer@plg.test"


def test_complete_password_reset_maps_same_password_to_stable_error(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(plg_cloud, "cloud_api_url", lambda: "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "app_version", lambda: "1.0.0")
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(
        _Response(422, {"detail": "New password must be different from the old password."}), calls
    ))

    result = plg_cloud.complete_password_reset("person@example.com", "123456", "new-password-123")

    assert result["ok"] is False
    assert result["error_type"] == "same_password"


def test_complete_password_reset_leaves_unmapped_status_as_generic_auth(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(plg_cloud, "cloud_api_url", lambda: "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "app_version", lambda: "1.0.0")
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(
        _Response(400, {"detail": "Invalid or expired reset code."}), calls
    ))

    result = plg_cloud.complete_password_reset("person@example.com", "000000", "new-password-123")

    assert result["ok"] is False
    assert result["error_type"] == "auth"


def test_resend_verification_maps_too_many_attempts_to_rate_limit(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(plg_cloud, "cloud_api_url", lambda: "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "app_version", lambda: "1.0.0")
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(
        _Response(429, {"detail": "Wait 45s before requesting another code."}), calls
    ))

    result = plg_cloud.resend_verification("user-1")

    assert result["ok"] is False
    assert result["error_type"] == "rate_limit"


def test_logout_clears_local_session_even_if_network_is_down(monkeypatch) -> None:
    cleared: list[bool] = []
    monkeypatch.setattr(plg_cloud, "cloud_api_url", lambda: "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "_api_headers", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(plg_cloud, "clear_session", lambda: cleared.append(True))
    monkeypatch.setattr(plg_cloud.httpx, "Client", lambda **_kwargs: _Client(_Response(500, {}), []))

    result = plg_cloud.logout()

    assert result == {"ok": True}
    assert cleared == [True]
    assert plg_cloud._device_registered is False
