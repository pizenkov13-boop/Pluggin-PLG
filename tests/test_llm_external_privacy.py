from __future__ import annotations

from llm_client import redact_external_ai_text


def test_external_ai_filter_removes_credentials_and_identifiers() -> None:
    source = (
        "dark trap email=person@example.com password=hunter2 "
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
        "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepart "
        r"sample root C:\Users\someone\PLG_Library"
    )

    sanitized = redact_external_ai_text(source)

    assert "person@example.com" not in sanitized
    assert "hunter2" not in sanitized
    assert "abcdefghijklmnopqrstuvwxyz" not in sanitized
    assert "eyJhbGciOiJIUzI1NiJ9" not in sanitized
    assert r"C:\Users\someone" not in sanitized
    assert "dark trap" in sanitized
    assert "[redacted-email]" in sanitized


def test_external_ai_filter_keeps_normal_music_brief() -> None:
    brief = "Dark melodic trap, 140 BPM, C# minor, tight hats and distorted 808."

    assert redact_external_ai_text(brief) == brief
