from types import SimpleNamespace

from backend_core import _choose_best_passing_variant


def test_best_of_rejects_higher_scoring_candidate_when_it_failed_quality():
    reports = [
        SimpleNamespace(score=92.0, passed=False),
        SimpleNamespace(score=78.0, passed=True),
        SimpleNamespace(score=76.0, passed=True),
    ]
    assert _choose_best_passing_variant(reports) == (1, True)


def test_best_of_marks_emergency_fallback_when_every_candidate_failed():
    reports = [SimpleNamespace(score=70.0, passed=False), SimpleNamespace(score=75.0, passed=False)]
    assert _choose_best_passing_variant(reports) == (1, False)
