from __future__ import annotations

from llm_client import format_llm_error, safe_error_summary


def test_provider_error_summary_does_not_echo_sensitive_text() -> None:
    secret = "email=person@example.com password=super-secret-token Bearer abc123"
    exc = RuntimeError(secret)

    assert safe_error_summary(exc) == "RuntimeError"
    message = format_llm_error(exc)
    assert secret not in message
    assert "person@example.com" not in message
    assert "super-secret-token" not in message


def test_provider_error_summary_keeps_safe_category() -> None:
    exc = RuntimeError("HTTP 429 body: email=person@example.com")

    assert safe_error_summary(exc) == "rate_limited"
    assert "429" in format_llm_error(exc)
