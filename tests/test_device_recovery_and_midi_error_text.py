"""Two error paths the user used to be misled by.

1. /v1/generate answering 403 "Unknown device" was typed ``cloud`` ("try again in
   a minute") and retrying never helped. The client now registers the device and
   retries once; if that cannot work the error is typed ``device``.
2. A MIDI that leaves the generator nothing to build on ended in
   "AI gateway request failed" — this product has no AI gateway.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

import plg_api
import plg_cloud

UNKNOWN_DEVICE = "Unknown device. Sign out and sign in again to bind this PC."


def _resp(status, body=None, text=""):
    resp = MagicMock(status_code=status, text=text or str(body))
    resp.json.return_value = body if body is not None else {}
    return resp


def _generate(monkeypatch, posts, *, register=None, register_raises=None):
    """Run cloud_generate against scripted /v1/generate responses."""
    monkeypatch.setenv("PLG_CLOUD_URL", "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "_api_headers", lambda: {})
    monkeypatch.setattr(plg_cloud, "get_device_id", lambda: "dev")
    monkeypatch.setattr(plg_cloud, "_device_registered", False)
    client = MagicMock()
    client.__enter__.return_value.post.side_effect = list(posts)
    reg = MagicMock(return_value=register, side_effect=register_raises)
    with patch("plg_cloud.httpx.Client", return_value=client), patch.object(plg_cloud, "register_device", reg):
        out = plg_cloud.cloud_generate("x")
    return out, client.__enter__.return_value.post, reg


OK_BODY = {"pattern": {"bpm": 140}, "quota": {"remaining": 5}}


def test_unknown_device_is_registered_and_the_generation_retried(monkeypatch):
    out, post, register = _generate(
        monkeypatch,
        [_resp(403, {"detail": UNKNOWN_DEVICE}), _resp(200, OK_BODY)],
        register={"ok": True},
    )
    assert out["ok"] is True and out["pattern"] == {"bpm": 140}
    assert post.call_count == 2
    register.assert_called_once()
    assert plg_cloud._device_registered is True


def test_still_unknown_after_registration_is_a_device_error_and_does_not_loop(monkeypatch):
    out, post, register = _generate(
        monkeypatch,
        [_resp(403, {"detail": UNKNOWN_DEVICE}), _resp(403, {"detail": UNKNOWN_DEVICE})],
        register={"ok": True},
    )
    assert out["ok"] is False and out["error_type"] == "device"
    assert post.call_count == 2
    register.assert_called_once()


def test_a_refused_registration_reports_its_own_reason(monkeypatch):
    limit = "Device limit reached (3). Remove an old device or contact support."
    out, post, register = _generate(
        monkeypatch,
        [_resp(403, {"detail": UNKNOWN_DEVICE})],
        register={"ok": False, "error": limit, "error_type": "device"},
    )
    assert out["error_type"] == "device" and out["error"] == limit
    assert post.call_count == 1  # nothing to retry with
    assert plg_cloud._device_registered is False


def test_no_network_during_registration_is_a_network_error(monkeypatch):
    out, post, _ = _generate(
        monkeypatch,
        [_resp(403, {"detail": UNKNOWN_DEVICE})],
        register_raises=httpx.ConnectError("getaddrinfo failed"),
    )
    assert out["error_type"] == "network"
    assert post.call_count == 1


def test_a_lost_session_during_registration_is_reported_as_auth(monkeypatch):
    auth = {"ok": False, "error": "Session expired. Sign in again.", "error_type": "auth"}
    out, _, _ = _generate(monkeypatch, [_resp(403, {"detail": UNKNOWN_DEVICE})], register=auth)
    assert out["error_type"] == "auth"


@pytest.mark.parametrize(
    ("status", "body"),
    [(403, {"detail": "Banned (device)."}), (403, {"detail": "Forbidden."}), (500, {"detail": "boom"}), (503, {"detail": "Maintenance"})],
)
def test_other_failures_do_not_trigger_a_registration(monkeypatch, status, body):
    out, post, register = _generate(monkeypatch, [_resp(status, body)], register={"ok": True})
    assert out["ok"] is False and out["error_type"] == "cloud"
    register.assert_not_called()
    assert post.call_count == 1


def test_a_gateway_page_saying_unknown_device_is_still_recognised(monkeypatch):
    page = MagicMock(status_code=403, text="403 Unknown device")
    page.json.side_effect = ValueError("not json")
    out, post, register = _generate(monkeypatch, [page, _resp(200, OK_BODY)], register={"ok": True})
    assert out["ok"] is True and post.call_count == 2
    register.assert_called_once()


# --- the drums-only MIDI message ---------------------------------------------------

GIVE_UP = (
    "No generation succeeded after 3 attempts; nothing was exported. Reasons — "
    "round 0: hard fail (score 60.0) — full-kit symbiosis gate: no_hats:P5; "
    "round 1: hard fail (score 60.0) — full-kit symbiosis gate: no_hats:P5"
)


def test_a_give_up_names_the_midi_and_never_an_ai_gateway():
    result = plg_api._pipeline_error(RuntimeError(GIVE_UP))
    assert result["ok"] is False and result["error_type"] == "midi_unusable"
    text = result["error"].lower()
    assert "midi" in text and "melody" in text
    for word in ("gateway", "ai ", "diagnostic", "openrouter", "llm"):
        assert word not in text


def test_the_internal_reasons_are_not_dumped_on_the_user():
    result = plg_api._pipeline_error(RuntimeError(GIVE_UP))
    assert "symbiosis" not in result["error"] and "no_hats" not in result["error"]


def test_the_more_specific_export_rule_still_wins():
    exc = RuntimeError("MIDI export blocked: 808_bass has no usable sample. No generation succeeded after 3 attempts")
    assert plg_api._pipeline_error(exc)["error_type"] == "midi_validation"


def test_unrelated_failures_keep_their_own_mapping():
    assert plg_api._pipeline_error(RuntimeError("503 UNAVAILABLE"))["error_type"] == "llm"
    assert plg_api._pipeline_error(ValueError("The selected MIDI could not be ingested; no fallback"))["error_type"] == "midi_unreadable"
