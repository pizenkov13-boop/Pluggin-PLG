from copy import deepcopy

from beat_humanize import humanize_pattern
from beat_quality import evaluate, max_retries, min_score, scorer_enabled
from genre_profiles import profile_for
from song_dramaturgy import score_song_dramaturgy


def _rage_like_pattern() -> dict:
    return {
        "bpm": 150,
        "style": "rage opium trap",
        "user_prompt": "rage dark trap",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.4, "velocity": 110}],
            "snare": [{"time_step": 1.0, "note": "D1", "length": 0.35, "velocity": 105}],
            "clap": [{"time_step": 1.0, "note": "D1", "length": 0.25, "velocity": 90}],
            "hi_hats": [
                {"time_step": i * 0.5, "note": "C5", "length": 0.125, "velocity": 100}
                for i in range(8)
            ],
            "sub_808": [{"time_step": 0.0, "note": "A1", "length": 2.0, "velocity": 127}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 2.0, "velocity": 100}],
        },
    }


def test_good_humanized_pattern_passes():
    profile = profile_for(style="rage", prompt="rage")
    out = humanize_pattern(deepcopy(_rage_like_pattern()), profile=profile)
    report = evaluate(out)
    assert report.score >= min_score()
    assert report.passed is True
    assert report.hard_fail is False


def test_empty_pattern_hard_fails():
    report = evaluate({"bpm": 140, "style": "trap", "tracks": {}})
    assert report.hard_fail is True
    assert report.passed is False


def test_scorer_disabled_zero_retries(monkeypatch):
    monkeypatch.setenv("PLG_USE_BEAT_SCORER", "0")
    assert scorer_enabled() is False
    assert max_retries() == 0


def test_dramaturgy_score_rewards_phrase_conversation_not_hat_spam():
    pattern = {
        "plg_midi_fullkit_plan": {
            "enabled": True,
            "phrases": [
                {"strong_entry": True, "interaction": "impact"},
                {"interaction": "space"},
                {"interaction": "answer"},
                {"interaction": "support"},
            ],
        },
        "tracks": {
            "hi_hats": [
                {"midi_motif_answer": True},
                {"midi_boundary_fill": True},
                {"midi_section_pulse_switch": True},
            ],
            "snare": [{"midi_motif_pickup": True}],
            "snare_layer": [{"midi_signature_perc": True}],
        },
    }
    good = score_song_dramaturgy(pattern)
    pattern["tracks"]["hi_hats"].extend({"midi_boundary_fill": True} for _ in range(8))
    assert good > score_song_dramaturgy(pattern)
