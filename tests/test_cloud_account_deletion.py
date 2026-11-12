"""Payment-record handling on account deletion and the 5-year purge.

privacy.html §2 promises: payment data survives account deletion for 5 years
(for accounting), then is destroyed. These tests pin both halves:
  - delete_user_account() strips the user UUID from payment_events, including
    the copy inside the stored YooKassa webhook body, and stamps anonymized_at;
    amount/status/dates/price_tier stay intact.
  - purge_payment_events() deletes only rows whose account was deleted over
    PAYMENT_LOG_RETENTION_DAYS ago AND whose payment year is past the term;
    rows of live accounts are never selected.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import cloud.app.legal as legal
from cloud.app.config import PAYMENT_LOG_RETENTION_DAYS

USER = "11111111-2222-3333-4444-555555555555"
EMAIL = "Buyer@Example.com"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self._db = db
        self._table = table
        self._mode = "select"
        self._patch: dict | None = None
        self._filters: list = []

    def select(self, *_a, **_k):
        self._mode = "select"
        return self

    def update(self, patch):
        self._mode = "update"
        self._patch = patch
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def eq(self, field, value):
        self._filters.append(lambda r, f=field, v=value: r.get(f) == v)
        return self

    def lt(self, field, value):
        self._filters.append(lambda r, f=field, v=value: r.get(f) is not None and r[f] < v)
        return self

    def in_(self, field, values):
        self._filters.append(lambda r, f=field, v=tuple(values): r.get(f) in v)
        return self

    def execute(self):
        rows = self._db.tables.setdefault(self._table, [])
        hit = [r for r in rows if all(f(r) for f in self._filters)]
        if self._mode == "update":
            for r in hit:
                r.update(self._patch or {})
        elif self._mode == "delete":
            self._db.tables[self._table] = [r for r in rows if r not in hit]
        return _Result([dict(r) for r in hit])


class _Admin:
    def __init__(self):
        self.deleted: list[str] = []

    def delete_user(self, user_id):
        self.deleted.append(user_id)


class _FakeDB:
    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables
        self.auth = type("Auth", (), {"admin": _Admin()})()

    def table(self, name):
        return _Query(self, name)


def _webhook(payment_id: str, user_id: str) -> dict:
    return {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": "499.00", "currency": "RUB"},
            "created_at": "2026-09-01T10:00:00.000Z",
            "metadata": {"user_id": user_id, "price_tier": "ru"},
        },
    }


def test_delete_account_scrubs_user_from_payment_payload(monkeypatch):
    monkeypatch.setattr(legal, "_user_email", lambda _c, _u: EMAIL)
    other = "99999999-0000-0000-0000-000000000000"
    db = _FakeDB(
        {
            "profiles": [{"id": USER}],
            "payment_events": [
                {
                    "id": "evt_1",
                    "user_id": USER,
                    "amount_cents": 49900,
                    "currency": "RUB",
                    "price_tier": "ru",
                    "external_id": "pay_1",
                    "payload": {**_webhook("pay_1", USER), "note": EMAIL.lower()},
                },
                {"id": "evt_other", "user_id": other, "payload": _webhook("pay_2", other)},
            ],
        }
    )

    legal.delete_user_account(db, USER)

    mine = next(r for r in db.tables["payment_events"] if r["id"] == "evt_1")
    assert mine["user_id"] is None
    assert mine["anonymized_at"]
    assert USER not in repr(mine) and EMAIL.lower() not in repr(mine)
    # Accounting fields survive.
    obj = mine["payload"]["object"]
    assert obj["amount"] == {"value": "499.00", "currency": "RUB"}
    assert obj["status"] == "succeeded" and obj["id"] == "pay_1"
    assert obj["created_at"] == "2026-09-01T10:00:00.000Z"
    assert obj["metadata"] == {"price_tier": "ru"}
    assert mine["amount_cents"] == 49900 and mine["price_tier"] == "ru"

    untouched = next(r for r in db.tables["payment_events"] if r["id"] == "evt_other")
    assert untouched["user_id"] == other
    assert untouched["payload"]["object"]["metadata"]["user_id"] == other
    assert "anonymized_at" not in untouched

    assert db.auth.admin.deleted == [USER]
    assert db.tables["profiles"] == []


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_purge_deletes_only_expired_rows_of_deleted_accounts():
    now = datetime(2032, 6, 1, tzinfo=timezone.utc)
    term = timedelta(days=PAYMENT_LOG_RETENTION_DAYS)
    long_ago = now - term - timedelta(days=10)
    db = _FakeDB(
        {
            "payment_events": [
                # Deleted account, both clocks expired -> purged.
                {
                    "id": "expired",
                    "anonymized_at": _iso(long_ago),
                    "processed_at": _iso(datetime(2026, 11, 1, tzinfo=timezone.utc)),
                },
                # Deleted account, but only recently -> kept.
                {"id": "recent_delete", "anonymized_at": _iso(now - timedelta(days=100)), "processed_at": _iso(long_ago)},
                # Deleted long ago, but the payment's reporting year is still
                # inside the term (Dec 2027 -> kept until 2033-01-01).
                {
                    "id": "year_not_over",
                    "anonymized_at": _iso(long_ago),
                    "processed_at": _iso(datetime(2027, 12, 31, tzinfo=timezone.utc)),
                },
                # Live account: never anonymized -> never purged.
                {"id": "live", "user_id": USER, "anonymized_at": None, "processed_at": _iso(long_ago)},
            ]
        }
    )

    result = legal.purge_payment_events(db, now=now)

    assert result["purged"] == 1
    assert sorted(r["id"] for r in db.tables["payment_events"]) == ["live", "recent_delete", "year_not_over"]


def test_purge_default_term_is_five_years():
    assert PAYMENT_LOG_RETENTION_DAYS == 1825
