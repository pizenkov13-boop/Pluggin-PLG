from mix_blueprint import _localized_meta_steps, build_mix_blueprint, session_slug


def test_key_lock_step_does_not_crash_on_key_param():
    # Regression: blueprint_text(loc, "step_key_lock", key=...) used to raise
    # "got multiple values for argument 'key'" and broke beat creation.
    data = {"plg_key": "E minor", "plg_genre": "rage", "plg_drum_blueprint": True}
    steps = _localized_meta_steps(data, "en")
    key_step = next(s for s in steps if s["id"] == "key-lock")
    assert "E minor" in key_step["text"]
    # Russian locale path too (the UI was set to RU when it crashed).
    ru = _localized_meta_steps({"plg_key": "F# phrygian"}, "ru")
    assert any(s["id"] == "key-lock" and "F# phrygian" in s["text"] for s in ru)


def test_session_slug_sanitizes_style():
    data = {"bpm": 145, "style": "opium rage!!! ken"}
    slug = session_slug(data)
    assert slug.startswith("PLG_Stems_145bpm_")
    assert "!!!" not in slug


def test_blueprint_mentions_808_and_stems():
    data = {
        "bpm": 150,
        "style": "opium rage ken carson",
        "user_prompt": "dark trap",
        "plg_hat_rolls": 8,
        "plg_producer_meta": {"master_soft_clip": True},
        "plg_sample_picks": {"sub_808": "808_opium_heavy.wav"},
        "manual_steps": ["mono legato applied"],
    }
    text = build_mix_blueprint(data, stem_folder="C:/out/PLG_Stems_150bpm_opium")
    assert "808" in text
    # Saturation is baked by default (sample_saturate.py) — the guide should say
    # so instead of asking the user to add Blood Overdrive/Fast Dist by hand.
    assert "already baked into the sample" in text
    assert "Kick.mid" in text
    assert "FL Studio Blueprint" in text


def test_blueprint_has_fx_recipe_without_brands():
    data = {"bpm": 150, "style": "dark rage trap", "plg_producer_meta": {"master_soft_clip": True}}
    text = build_mix_blueprint(data)
    # Master soft clip + 808 warmth are baked by default — no "add Pre-Amp
    # 40% / +4 dB" instruction should remain (see mix_blueprint._channel_block).
    assert "already baked" in text
    assert "Fruity Soft Clipper" in text
    assert "Fruity Reeverb 2" in text
    assert "5 kHz" in text
    assert "F1LTHY" not in text.upper()
    assert "OPIUM" not in text.upper()
