import sample_fx


def test_raw_sample_mode_defaults_off():
    assert sample_fx.raw_sample_mode_enabled() is False


def test_raw_sample_mode_on_via_env(monkeypatch):
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")
    assert sample_fx.raw_sample_mode_enabled() is True


def test_raw_sample_mode_accepts_common_truthy_spellings(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("PLG_RAW_SAMPLES", value)
        assert sample_fx.raw_sample_mode_enabled() is True


def test_raw_sample_mode_off_is_the_default_spelling_too(monkeypatch):
    for value in ("0", "false", "off", "no", ""):
        monkeypatch.setenv("PLG_RAW_SAMPLES", value)
        assert sample_fx.raw_sample_mode_enabled() is False


_GATES = [
    ("PLG_MELODY_FX", sample_fx.melody_fx_enabled),
    ("PLG_SAMPLE_REALISM", sample_fx.sample_realism_enabled),
    ("PLG_CHORD_INTERVAL_LAYER", sample_fx.chord_interval_layer_enabled),
    ("PLG_STEREO_ASYMMETRY", sample_fx.stereo_asymmetry_enabled),
    ("PLG_MELODY_DELAY_LAYER", sample_fx.melody_delay_layer_enabled),
    ("PLG_REVERSE_CHOP", sample_fx.reverse_chop_enabled),
    ("PLG_808_PUNCH", sample_fx.sub_808_punch_enabled),
    ("PLG_808_MONO_BELOW", sample_fx.sub_808_mono_below_enabled),
]


def test_every_individual_gate_is_on_by_default():
    # Most FX gates default on; 808 punch/mono default OFF so FL loads the
    # folder wav unchanged (opt in with PLG_808_*=1).
    on_by_default = [g for g in _GATES if g[0] not in ("PLG_808_PUNCH", "PLG_808_MONO_BELOW")]
    off_by_default = [g for g in _GATES if g[0] in ("PLG_808_PUNCH", "PLG_808_MONO_BELOW")]
    for name, fn in on_by_default:
        assert fn() is True, name
    for name, fn in off_by_default:
        assert fn() is False, name


def test_raw_sample_mode_overrides_every_individual_gate(monkeypatch):
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")
    for _env_name, fn in _GATES:
        assert fn() is False, fn.__name__


def test_raw_sample_mode_wins_even_when_individual_gate_explicitly_on(monkeypatch):
    # The master switch must override an explicit per-effect opt-in too, not
    # just the "auto" default — otherwise "raw mode" would be a lie for any
    # deployment that has individually turned an effect on in its .env.
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")
    for env_name, fn in _GATES:
        monkeypatch.setenv(env_name, "on")
        assert fn() is False, fn.__name__
