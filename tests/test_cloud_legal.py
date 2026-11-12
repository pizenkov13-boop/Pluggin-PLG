"""Tests for Phase 5 legal helpers."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from cloud.app.legal import legal_snapshot, require_terms_acceptance


def test_legal_snapshot_has_ownership():
    snap = legal_snapshot()
    assert "beat_ownership" in snap["disclaimers"]
    assert "similarity" in snap["disclaimers"]
    assert "data_processing" in snap["disclaimers"]
    assert "royalties" in snap["disclaimers"]["beat_ownership"].lower()
    assert snap["subscription"]["cancel_anytime"] is True
    # PLG sells a one-off 30-day access pack, not a recurring subscription —
    # see billing.py's price_label() (no "/mo" suffix) and the "No automatic
    # renewal." copy in the account.purchaseDesc translations.
    assert snap["subscription"]["auto_renewal"] is False
    assert snap["subscription"]["trial_beats"] >= 1


def test_require_terms_blocks():
    with pytest.raises(HTTPException) as exc:
        require_terms_acceptance(False, True)
    assert exc.value.status_code == 400


def test_require_age_blocks():
    with pytest.raises(HTTPException) as exc:
        require_terms_acceptance(True, False)
    assert exc.value.status_code == 400


def test_require_terms_ok():
    require_terms_acceptance(True, True)
