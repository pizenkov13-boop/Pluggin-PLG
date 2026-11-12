from __future__ import annotations

import llm_pipeline


def test_default_titan_budget_allows_qa(monkeypatch):
    # Luna-line defaults fit under the 0.50 RUB cap with QA enabled
    # (0.18 + 0.16 + 0.12 = 0.46).
    for key in (
        "PLG_MAX_BEAT_COST_RUB",
        "PLG_TITAN_ARCHITECT_COST_RUB",
        "PLG_TITAN_COMPOSER_COST_RUB",
        "PLG_TITAN_QA_COST_RUB",
        "PLG_TITAN_QA_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    costs = llm_pipeline._stage_costs_rub()

    assert costs["architect"] + costs["composer"] + costs["qa"] <= 0.50
    assert llm_pipeline._qa_model() == llm_pipeline.QA_MODEL_DEFAULT
    assert llm_pipeline._qa_allowed(costs) is True


def test_titan_budget_allows_qa_when_configured(monkeypatch):
    monkeypatch.setenv("PLG_MAX_BEAT_COST_RUB", "0.50")
    monkeypatch.setenv("PLG_TITAN_ARCHITECT_COST_RUB", "0.18")
    monkeypatch.setenv("PLG_TITAN_COMPOSER_COST_RUB", "0.16")
    monkeypatch.setenv("PLG_TITAN_QA_COST_RUB", "0.12")

    assert llm_pipeline._qa_allowed(llm_pipeline._stage_costs_rub()) is True


def test_tight_budget_still_skips_qa(monkeypatch):
    # Cap below architect+composer+qa so QA is gated off.
    monkeypatch.setenv("PLG_MAX_BEAT_COST_RUB", "0.30")
    monkeypatch.setenv("PLG_TITAN_ARCHITECT_COST_RUB", "0.18")
    monkeypatch.setenv("PLG_TITAN_COMPOSER_COST_RUB", "0.16")
    monkeypatch.setenv("PLG_TITAN_QA_COST_RUB", "0.12")

    assert llm_pipeline._qa_allowed(llm_pipeline._stage_costs_rub()) is False
