from beat_planner import (
    apply_brief_metadata,
    plan_beat_brief,
    prompt_detail_level,
    should_use_smart_plan,
    smart_plan_hint,
)


def test_short_prompt_no_auto_plan():
    assert prompt_detail_level("dark trap") == "short"
    assert should_use_smart_plan("dark trap", None) is False


def test_detailed_prompt_auto_plan():
    p = (
        "Create a massive cinematic dark trap beat 135 BPM E minor "
        "atmospheric reverb spacey lead drums from bar 9"
    )
    assert prompt_detail_level(p) in ("medium", "detailed")
    assert should_use_smart_plan(p, None) is True


def test_manual_override():
    assert should_use_smart_plan("dark trap", True) is True
    assert should_use_smart_plan("long " * 40, False) is False


def test_smart_plan_hint():
    hint = smart_plan_hint("135 bpm E minor dark cinematic")
    assert "detail_level" in hint
    assert hint["recommended"] is True


def test_apply_brief_metadata(monkeypatch):
    monkeypatch.setattr(
        "llm_client.complete_json",
        lambda *a, **k: {
            "bpm": 132,
            "key": "E minor",
            "style": "dark rage trap",
            "producer_note": "sparse hook",
        },
    )
    brief = plan_beat_brief("test prompt")
    pattern = apply_brief_metadata({"bpm": 140, "style": "x"}, brief)
    assert pattern["bpm"] == 132
    assert pattern["plg_key"] == "E minor"
    assert pattern["plg_smart_plan"] is True
