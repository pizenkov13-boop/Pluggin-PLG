"""Tests for Phase 4 infra helpers."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from cloud.app.feature_flags import _bucket, flag_enabled
from cloud.app.waitlist import _code_valid


def test_bucket_stable():
    assert _bucket("user-1", "regenerate_ui") == _bucket("user-1", "regenerate_ui")


def test_bucket_range():
    assert 0 <= _bucket("abc", "x") < 100


def test_flag_default_when_empty():
    class FakeClient:
        def table(self, *_a, **_k):
            return self

        def select(self, *_a, **_k):
            return self

        def execute(self):
            class R:
                data = []

            return R()

    assert flag_enabled(FakeClient(), "missing", default=False) is False


def test_invite_code_expired():
    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert _code_valid({"expires_at": past, "uses": 0, "max_uses": 5}) is False


def test_invite_code_max_uses():
    assert _code_valid({"uses": 5, "max_uses": 5}) is False
    assert _code_valid({"uses": 4, "max_uses": 5}) is True
