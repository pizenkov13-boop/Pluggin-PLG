from copy import deepcopy

from beat_humanize import humanize_pattern
from genre_profiles import profile_for
from pattern_score import score_humanized_pattern


def _rage_fixture() -> dict:
    return {
        "bpm": 150,
        "style": "rage",
        "user_prompt": "rage",
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


def test_humanized_scores_higher_than_raw():
    profile = profile_for(style="rage", prompt="rage")
    raw = _rage_fixture()
    out = humanize_pattern(deepcopy(raw), profile=profile)
    assert score_humanized_pattern(out, profile) >= score_humanized_pattern(raw, profile)


def test_drive_does_not_inflate_proxy_score():
    from dataclasses import replace

    from genre_profiles import PROFILES

    profile = PROFILES["rage"]
    pattern = _rage_fixture()
    low = score_humanized_pattern(pattern, replace(profile, drive=0.1))
    high = score_humanized_pattern(pattern, replace(profile, drive=1.0))
    assert low == high
