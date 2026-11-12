"""Tests for Phase 6 ops helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from cloud.app.email_cron import run_subscription_expiry_reminders
from cloud.app.feedback import submit_feedback
from cloud.app.status_page import status_payload


def test_health_accepts_head_not_just_get():
    # Uptime monitors (UptimeRobot et al.) probe with HEAD by default. A
    # GET-only route 405s every such probe, reporting the service as down
    # even while it's healthy — regression for that false-alarm class.
    from fastapi.testclient import TestClient

    from cloud.app import main

    client = TestClient(main.app)
    for path in ("/health", "/v1/health"):
        response = client.head(path)
        assert response.status_code == 200, path
        assert response.content == b""


def test_status_payload_shape():
    client = MagicMock()
    client.table.return_value.select.return_value.is_.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[]
    )
    with patch("cloud.app.status_page.redis_available", return_value=False):
        with patch("cloud.app.status_page._component_status", return_value="operational"):
            payload = status_payload(client)
    assert payload["ok"] is True
    assert payload["overall"] in ("operational", "degraded", "outage")
    assert "support" in payload
    assert payload["support"]["email"]


def test_submit_feedback_requires_message():
    client = MagicMock()
    with pytest.raises(HTTPException) as exc:
        submit_feedback(client, user_id="u1", email="a@b.c", category="bug", message="  ")
    assert exc.value.status_code == 400


def test_submit_feedback_ok():
    client = MagicMock()
    with patch("cloud.app.feedback.send_email"):
        result = submit_feedback(
            client,
            user_id="u1",
            email="user@test.com",
            category="bug",
            message="App crashed on generate",
        )
    assert result["ok"] is True
    client.table.assert_called_with("feedback_submissions")


def test_subscription_expiry_reminder_sends():
    client = MagicMock()
    ends = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    client.table.return_value.select.return_value.in_.return_value.gte.return_value.lt.return_value.execute.return_value = MagicMock(
        data=[{"id": "u1", "status": "active", "subscription_ends_at": ends}]
    )
    client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.gte.return_value.contains.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[]
    )
    with patch("cloud.app.email_cron._user_email", return_value="user@test.com"):
        with patch("cloud.app.email_cron.notify_subscription_expiring", return_value=True) as notify:
            result = run_subscription_expiry_reminders(client, warn_days=(3,))
    assert result["sent"] == 1
    notify.assert_called_once()
