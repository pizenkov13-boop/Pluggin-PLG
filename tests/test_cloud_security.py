"""Tests for Phase 3 security modules."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from cloud.app.moderation import moderate_prompt
from cloud.app.rate_limit import SlidingWindow, check_generate_limits
from cloud.app.security import (
    check_honeypot,
    claim_device_trial,
    fingerprint,
    ip_in_allowlist,
)


class _TrialClaimClient:
    """Minimal Supabase stand-in for claim_device_trial: one claims table."""

    def __init__(self, existing: dict | None = None):
        self._existing = existing
        self.inserted: dict | None = None
        self.updated: dict | None = None
        self._op = None

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, row):
        self._op = "insert"
        self.inserted = row
        return self

    def update(self, row):
        self._op = "update"
        self.updated = row
        return self

    def eq(self, *_a, **_k):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        if self._op == "select":
            return type("R", (), {"data": self._existing})()
        return type("R", (), {"data": [self.inserted or self.updated]})()


def _iso_days_ago(days: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_claim_device_trial_first_time_records_claim():
    client = _TrialClaimClient(existing=None)
    assert claim_device_trial(client, "dev-1", "user-1") is True
    assert client.inserted == {"device_id": "dev-1", "first_user_id": "user-1"}


def test_claim_device_trial_blocks_second_account_on_same_device():
    client = _TrialClaimClient(
        existing={"first_user_id": "user-1", "claimed_at": _iso_days_ago(2)}
    )
    assert claim_device_trial(client, "dev-1", "user-2") is False


def test_claim_device_trial_exempt_device_never_blocks(monkeypatch):
    monkeypatch.setenv("PLG_TRIAL_FARM_EXEMPT_DEVICE_IDS", "dev-qa-1, dev-qa-2")
    client = _TrialClaimClient(existing={"first_user_id": "someone-else"})
    assert claim_device_trial(client, "dev-qa-1", "user-2") is True
    assert client.inserted is None  # exempt devices don't record a claim


def test_claim_device_trial_ages_out_after_ttl(monkeypatch):
    # A claim older than the TTL is re-issued to the new account automatically.
    import cloud.app.config as cfg

    monkeypatch.setattr(cfg, "TRIAL_CLAIM_TTL_DAYS", 180)
    client = _TrialClaimClient(
        existing={"first_user_id": "old-user", "claimed_at": _iso_days_ago(200)}
    )
    assert claim_device_trial(client, "dev-1", "new-user") is True
    assert client.updated["first_user_id"] == "new-user"
    assert "claimed_at" in client.updated  # re-stamped, next claimant waits again


def test_claim_device_trial_within_ttl_still_blocks(monkeypatch):
    import cloud.app.config as cfg

    monkeypatch.setattr(cfg, "TRIAL_CLAIM_TTL_DAYS", 180)
    client = _TrialClaimClient(
        existing={"first_user_id": "old-user", "claimed_at": _iso_days_ago(30)}
    )
    assert claim_device_trial(client, "dev-1", "new-user") is False


def test_claim_device_trial_ttl_zero_means_permanent(monkeypatch):
    import cloud.app.config as cfg

    monkeypatch.setattr(cfg, "TRIAL_CLAIM_TTL_DAYS", 0)
    client = _TrialClaimClient(
        existing={"first_user_id": "old-user", "claimed_at": _iso_days_ago(9999)}
    )
    assert claim_device_trial(client, "dev-1", "new-user") is False


def test_claim_device_trial_blocks_after_original_account_deleted():
    # Migration 021: deleting the account NULLs first_user_id instead of
    # cascading the claim away, so delete-and-recreate on the same PC is blocked.
    client = _TrialClaimClient(
        existing={"first_user_id": None, "claimed_at": _iso_days_ago(2)}
    )
    assert claim_device_trial(client, "dev-1", "new-user") is False
    assert client.updated is None and client.inserted is None


def test_claim_device_trial_orphaned_claim_ages_out_after_ttl(monkeypatch):
    import cloud.app.config as cfg

    monkeypatch.setattr(cfg, "TRIAL_CLAIM_TTL_DAYS", 180)
    client = _TrialClaimClient(
        existing={"first_user_id": None, "claimed_at": _iso_days_ago(200)}
    )
    assert claim_device_trial(client, "dev-1", "new-user") is True
    assert client.updated["first_user_id"] == "new-user"


def test_claim_migration_keeps_claim_on_user_delete():
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1]
        / "cloud" / "supabase" / "migrations" / "021_trial_claim_survives_deletion.sql"
    ).read_text(encoding="utf-8")
    assert "drop not null" in sql
    assert "on delete set null" in sql
    assert "on delete cascade" not in sql  # lowercase = executable SQL, not the comment


def test_honeypot_blocks():
    with pytest.raises(HTTPException) as exc:
        check_honeypot("spam@bot.com")
    assert exc.value.status_code == 400


def test_honeypot_allows_empty():
    check_honeypot("")
    check_honeypot(None)


def test_fingerprint_stable():
    a = fingerprint("device-1", "1.2.3.4")
    b = fingerprint("device-1", "1.2.3.4")
    assert a == b
    assert fingerprint("device-2", "1.2.3.4") != a


def test_moderation_blocks_illegal():
    with pytest.raises(HTTPException):
        moderate_prompt("make child porn beat")


def test_moderation_allows_trap():
    assert moderate_prompt("dark trap beat 140 bpm") == "dark trap beat 140 bpm"


def test_sliding_window():
    w = SlidingWindow()
    assert w.allow("k", 2, 60.0)
    assert w.allow("k", 2, 60.0)
    assert not w.allow("k", 2, 60.0)


def test_ip_allowlist():
    cidrs = ["203.0.113.0/27", "203.0.113.40", "2001:db8::/32"]
    assert ip_in_allowlist("203.0.113.5", cidrs)
    assert ip_in_allowlist("203.0.113.40", cidrs)
    assert ip_in_allowlist("2001:db8:1::abcd", cidrs)
    assert not ip_in_allowlist("8.8.8.8", cidrs)
    assert not ip_in_allowlist("203.0.113.41", cidrs)
    assert not ip_in_allowlist("", cidrs)
    assert not ip_in_allowlist("not-an-ip", cidrs)


def test_ip_limit_single_hit():
    w = SlidingWindow()
    key = "rl:ip:test-single"
    assert w.allow(key, 2, 3600.0)
    assert w.allow(key, 2, 3600.0)
    assert not w.allow(key, 2, 3600.0)


def test_check_ip_limit_raises_at_capacity(monkeypatch):
    import cloud.app.rate_limit as rl
    from cloud.app.rate_limit import check_ip_limit

    monkeypatch.setattr(rl, "IP_HOURLY_LIMIT", 3)
    rl._ip_window = SlidingWindow()
    ip = "10.0.0.99"
    check_ip_limit(ip)
    check_ip_limit(ip)
    check_ip_limit(ip)
    with pytest.raises(HTTPException) as exc:
        check_ip_limit(ip)
    assert exc.value.status_code == 429


def test_generate_cooldown():
    user = "test-user-cooldown-xyz"
    check_generate_limits(user)
    with pytest.raises(HTTPException) as exc:
        check_generate_limits(user)
    assert exc.value.status_code == 429
