"""Tests for server-side quota logic."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from fastapi import HTTPException

from types import SimpleNamespace

import cloud.app.quota as quota_mod
from cloud.app.quota import ensure_can_generate, quota_snapshot, reserve_beat, roll_profile


def _row(
    *,
    beats_used: int = 0,
    beats_today: int = 0,
    period_start: datetime | None = None,
    daily_reset: date | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "plan": "base",
        "status": "active",
        "period_start": (period_start or now).isoformat(),
        "beats_used": beats_used,
        "beats_today": beats_today,
        "daily_reset": (daily_reset or now.date()).isoformat(),
        "trial_beats_used": 0,
    }


def test_quota_snapshot_remaining():
    snap = quota_snapshot(_row(beats_used=5))
    assert snap["remaining"] == 25
    assert snap["limit"] == 30


def test_daily_activity_does_not_block_generation():
    row = _row(beats_used=0, beats_today=10)
    assert ensure_can_generate(row)["beats_today"] == 10


def test_monthly_limit_blocks():
    row = _row(beats_used=30, beats_today=0)
    with pytest.raises(HTTPException) as exc:
        ensure_can_generate(row)
    assert exc.value.status_code == 429


def test_roll_period_resets_monthly():
    old_start = datetime.now(timezone.utc) - timedelta(days=31)
    row = _row(beats_used=30, beats_today=2, period_start=old_start)
    rolled = roll_profile(row)
    assert rolled["beats_used"] == 0


def test_roll_daily_resets_today():
    # roll_profile compares against UTC today — use UTC here too so the test
    # doesn't flake at the local/UTC date boundary.
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    row = _row(beats_today=10, daily_reset=yesterday)
    rolled = roll_profile(row)
    assert rolled["beats_today"] == 0


def test_expired_blocks():
    row = _row(beats_used=0, beats_today=0)
    row["status"] = "expired"
    with pytest.raises(HTTPException) as exc:
        ensure_can_generate(row)
    assert exc.value.status_code == 402


# --- reserve_beat (atomic reservation + safe legacy fallback) ----------------


class _FakeRPC:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    def execute(self):
        if self._exc:
            raise self._exc
        return SimpleNamespace(data=self._result)


class _FakeClient:
    def __init__(self, *, rpc_result=None, rpc_exc=None):
        self._rpc_result = rpc_result
        self._rpc_exc = rpc_exc

    def rpc(self, _name, _params):
        return _FakeRPC(self._rpc_result, self._rpc_exc)


def test_reserve_beat_atomic_allows():
    quota_mod._RPC_AVAILABLE = True
    client = _FakeClient(rpc_result={"allowed": True, "consumed_trial": False})
    assert reserve_beat(client, "u1", _row()) == {"atomic": True, "was_trial": False}


def test_reserve_beat_atomic_marks_trial():
    quota_mod._RPC_AVAILABLE = True
    client = _FakeClient(rpc_result={"allowed": True, "consumed_trial": True})
    assert reserve_beat(client, "u1", _row())["was_trial"] is True


def test_reserve_beat_atomic_rejects_monthly():
    quota_mod._RPC_AVAILABLE = True
    client = _FakeClient(rpc_result={"allowed": False, "reason": "monthly"})
    with pytest.raises(HTTPException) as exc:
        reserve_beat(client, "u1", _row())
    assert exc.value.status_code == 429


def test_reserve_beat_falls_back_when_rpc_missing():
    quota_mod._RPC_AVAILABLE = True
    client = _FakeClient(rpc_exc=Exception("Could not find the function plg_consume_beat"))
    # within limits -> legacy precheck passes, caller will consume after generation
    assert reserve_beat(client, "u1", _row(beats_used=0, beats_today=0)) == {
        "atomic": False,
        "was_trial": False,
    }


def test_reserve_beat_fallback_still_enforces_limit():
    quota_mod._RPC_AVAILABLE = True
    client = _FakeClient(rpc_exc=Exception("does not exist"))
    with pytest.raises(HTTPException) as exc:
        reserve_beat(client, "u1", _row(beats_used=30))
    assert exc.value.status_code == 429
    quota_mod._RPC_AVAILABLE = True  # reset module cache for other tests
