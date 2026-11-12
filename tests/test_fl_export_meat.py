"""FL export meat — sampler CLIP/BOOST, dB staging, insert quarantine, intro."""

from __future__ import annotations

from typing import Any

import pytest

import flp_writer as fw
import mix_insert_fx


def _rage_pattern() -> dict[str, Any]:
    return {
        "bpm": 150,
        "style": "dark trap",
        "user_prompt": "rage evil 150 bpm",
        "plg_variety": {"seed": 1, "genre": "rage", "key": "F", "scale": "phrygian"},
        "tracks": {
            "kick": [{"time_step": float(i * 2), "note": "C1", "length": 0.3, "velocity": 112} for i in range(8)],
            "snare": [{"time_step": 2.0 + i * 4, "note": "D1", "length": 0.3, "velocity": 105} for i in range(4)],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#5", "length": 0.2, "velocity": 80} for i in range(32)],
            "sub_808": [{"time_step": float(i * 2), "note": "F2", "length": 1.5, "velocity": 110} for i in range(8)],
            "melody_lead": [
                {"time_step": float(i), "note": n, "length": 0.9, "velocity": 96}
                for i, n in enumerate(("F5", "G#5", "C6", "A#5") * 4)
            ],
        },
    }


def _mixer_plugins(blob: bytes) -> dict[str, list[str]]:
    mix = fw._extract_mixer_events_bytes(blob)
    if mix is None:
        return {}
    stream = memoryview(mix)
    off, current = 0, None
    out: dict[str, list[str]] = {}
    while off < len(stream):
        _, end, eid, payload = fw._next_event_bounds(stream, off)
        if eid == fw._INSERT_NAME and isinstance(payload, bytes):
            current = fw._read_text_event(payload)
        elif eid == fw._PLUGIN_INTERNAL and isinstance(payload, bytes) and current is not None:
            out.setdefault(current, []).append(fw._read_text_event(payload))
        off = end
    return out


def test_insert_chains_quarantined_by_default(monkeypatch):
    monkeypatch.delenv("PLG_INSERT_SPACE_FX", raising=False)
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    flat = [p for names in plugins.values() for p in names]
    assert "Fruity Delay 3" not in flat
    assert "Fruity Reeverb 2" not in flat
    assert "Fruity Blood Overdrive" not in flat
    # master specifically must never carry space FX
    assert all(p == "Fruity Soft Clipper" for p in plugins.get("Master", []))


@pytest.mark.legacy_mix
def test_insert_chains_opt_in_land_on_their_inserts(monkeypatch):
    monkeypatch.setenv("PLG_INSERT_SPACE_FX", "on")
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    assert plugins.get("PLG Sub 808") == ["Fruity Blood Overdrive"]
    assert plugins.get("PLG MIDI") == ["Fruity Reeverb 2", "Fruity Delay 3"]
    assert "Fruity Delay 3" not in plugins.get("Master", [])


def test_reverb_only_quarantined_by_default(monkeypatch):
    # PLG_REVERB_INSERT is a separate, narrower flag from PLG_INSERT_SPACE_FX —
    # both must be unset for the default-quarantine guarantee to hold.
    monkeypatch.delenv("PLG_REVERB_INSERT", raising=False)
    monkeypatch.delenv("PLG_INSERT_SPACE_FX", raising=False)
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    flat = [p for names in plugins.values() for p in names]
    assert "Fruity Reeverb 2" not in flat
    assert all(p == "Fruity Soft Clipper" for p in plugins.get("Master", []))


@pytest.mark.legacy_mix
def test_reverb_only_lands_alone_on_melody_insert(monkeypatch):
    monkeypatch.setenv("PLG_REVERB_INSERT", "on")
    monkeypatch.delenv("PLG_INSERT_SPACE_FX", raising=False)
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    assert plugins.get("PLG MIDI") == ["Fruity Reeverb 2"]
    assert "Fruity Delay 3" not in [p for names in plugins.values() for p in names]
    assert "Fruity Blood Overdrive" not in [p for names in plugins.values() for p in names]
    assert "Fruity Delay 3" not in plugins.get("Master", [])
    assert "Fruity Reeverb 2" not in plugins.get("Master", [])


@pytest.mark.legacy_mix
def test_reverb_only_never_overrides_the_full_combined_chain(monkeypatch):
    # An explicit plg_space_mix request (or the full quarantine already being
    # lifted) must always win and get BOTH plugins, never the solo one.
    monkeypatch.setenv("PLG_REVERB_INSERT", "on")
    monkeypatch.setenv("PLG_INSERT_SPACE_FX", "on")
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    assert plugins.get("PLG MIDI") == ["Fruity Reeverb 2", "Fruity Delay 3"]


def test_reverb_only_channel_rack_not_eaten_by_mixer_stream(monkeypatch):
    """Same regression guard as test_channel_rack_not_eaten_by_mixer_stream,
    exercised for the new isolated single-plugin path — this is exactly the
    corruption mode (Reeverb 2 appearing as a fake generator channel) the
    isolation is meant to reduce exposure to, so it needs its own guard."""
    monkeypatch.setenv("PLG_REVERB_INSERT", "on")
    monkeypatch.delenv("PLG_INSERT_SPACE_FX", raising=False)
    from pattern_utils import CHANNEL_NAMES, TRACK_KEYS

    blob = fw.build_flp(_rage_pattern())
    stream = fw._fl_dt_payload(blob)
    mix = fw._extract_mixer_events_bytes(blob) or b""
    channel_zone = bytes(stream)[: len(stream) - len(mix)] if mix else bytes(stream)
    names: list[str] = []
    off = 0
    view = memoryview(channel_zone)
    while off < len(view):
        _, end, eid, payload = fw._next_event_bounds(view, off)
        if eid == fw._CH_NAME and isinstance(payload, bytes):
            names.append(fw._read_text_event(payload))
        off = end
    expected = {CHANNEL_NAMES.get(k, k) for k in TRACK_KEYS}
    assert set(names) <= expected | {k for k in TRACK_KEYS}
    for bad in ("Fruity Reeverb 2", "Fruity Delay 3", "Fruity Blood Overdrive"):
        assert bad not in names


def test_delay_only_quarantined_by_default(monkeypatch):
    # PLG_DELAY_INSERT is a separate, narrower flag from PLG_INSERT_SPACE_FX —
    # both must be unset for the default-quarantine guarantee to hold.
    monkeypatch.delenv("PLG_DELAY_INSERT", raising=False)
    monkeypatch.delenv("PLG_INSERT_SPACE_FX", raising=False)
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    flat = [p for names in plugins.values() for p in names]
    assert "Fruity Delay 3" not in flat
    assert all(p == "Fruity Soft Clipper" for p in plugins.get("Master", []))


@pytest.mark.legacy_mix
def test_delay_only_lands_alone_on_melody_insert(monkeypatch):
    monkeypatch.setenv("PLG_DELAY_INSERT", "on")
    monkeypatch.delenv("PLG_REVERB_INSERT", raising=False)
    monkeypatch.delenv("PLG_INSERT_SPACE_FX", raising=False)
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    assert plugins.get("PLG MIDI") == ["Fruity Delay 3"]
    assert "Fruity Reeverb 2" not in [p for names in plugins.values() for p in names]
    assert "Fruity Blood Overdrive" not in [p for names in plugins.values() for p in names]
    assert "Fruity Delay 3" not in plugins.get("Master", [])
    assert "Fruity Reeverb 2" not in plugins.get("Master", [])


@pytest.mark.legacy_mix
def test_delay_only_never_overrides_the_full_combined_chain(monkeypatch):
    # An explicit plg_space_mix request (or the full quarantine already being
    # lifted) must always win and get BOTH plugins, never the solo one.
    monkeypatch.setenv("PLG_DELAY_INSERT", "on")
    monkeypatch.setenv("PLG_INSERT_SPACE_FX", "on")
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    assert plugins.get("PLG MIDI") == ["Fruity Reeverb 2", "Fruity Delay 3"]


def test_delay_only_channel_rack_not_eaten_by_mixer_stream(monkeypatch):
    """Same regression guard as test_reverb_only_channel_rack_not_eaten_by_mixer_stream,
    exercised for the delay-solo isolated path."""
    monkeypatch.setenv("PLG_DELAY_INSERT", "on")
    monkeypatch.delenv("PLG_REVERB_INSERT", raising=False)
    monkeypatch.delenv("PLG_INSERT_SPACE_FX", raising=False)
    from pattern_utils import CHANNEL_NAMES, TRACK_KEYS

    blob = fw.build_flp(_rage_pattern())
    stream = fw._fl_dt_payload(blob)
    mix = fw._extract_mixer_events_bytes(blob) or b""
    channel_zone = bytes(stream)[: len(stream) - len(mix)] if mix else bytes(stream)
    names: list[str] = []
    off = 0
    view = memoryview(channel_zone)
    while off < len(view):
        _, end, eid, payload = fw._next_event_bounds(view, off)
        if eid == fw._CH_NAME and isinstance(payload, bytes):
            names.append(fw._read_text_event(payload))
        off = end
    expected = {CHANNEL_NAMES.get(k, k) for k in TRACK_KEYS}
    assert set(names) <= expected | {k for k in TRACK_KEYS}
    for bad in ("Fruity Reeverb 2", "Fruity Delay 3", "Fruity Blood Overdrive"):
        assert bad not in names


@pytest.mark.legacy_mix
def test_reverb_and_delay_only_flags_together_still_pick_one_plugin(monkeypatch):
    # Both narrow solo flags set simultaneously is not a supported
    # configuration; reverb-only wins deterministically (see
    # melody_delay_only_chain_enabled's docstring in mix_insert_fx.py).
    monkeypatch.setenv("PLG_REVERB_INSERT", "on")
    monkeypatch.setenv("PLG_DELAY_INSERT", "on")
    monkeypatch.delenv("PLG_INSERT_SPACE_FX", raising=False)
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    assert plugins.get("PLG MIDI") == ["Fruity Reeverb 2"]


@pytest.mark.legacy_mix
def test_maximus_only_quarantined_by_default(monkeypatch):
    # PLG_MAXIMUS_INSERT unset must leave Master exactly as it is today —
    # Soft Clipper alone, same invariant test_insert_chains_quarantined_by_default
    # already locks in for the other three plugins.
    monkeypatch.delenv("PLG_MAXIMUS_INSERT", raising=False)
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    assert plugins.get("Master") == ["Fruity Soft Clipper"]
    flat = [p for names in plugins.values() for p in names]
    assert "Maximus" not in flat


@pytest.mark.legacy_mix
def test_maximus_only_lands_on_master_ahead_of_soft_clipper(monkeypatch):
    monkeypatch.setenv("PLG_MAXIMUS_INSERT", "on")
    blob = fw.build_flp(_rage_pattern())
    plugins = _mixer_plugins(blob)
    # Multiband dynamics before the final clipping stage — Maximus in slot 0,
    # Soft Clipper pushed to slot 1, not replaced.
    assert plugins.get("Master") == ["Maximus", "Fruity Soft Clipper"]
    # No other insert is touched by this flag.
    assert "Maximus" not in plugins.get("PLG MIDI", [])
    assert "Maximus" not in plugins.get("PLG Sub 808", [])


@pytest.mark.legacy_mix
def test_maximus_only_alone_when_soft_clip_disabled(monkeypatch):
    monkeypatch.setenv("PLG_MAXIMUS_INSERT", "on")
    pattern = _rage_pattern()
    pattern["plg_use_template"] = False  # disables the template-default Soft Clipper
    blob = fw.build_flp(pattern)
    plugins = _mixer_plugins(blob)
    assert plugins.get("Master") == ["Maximus"]


def test_maximus_only_channel_rack_not_eaten_by_mixer_stream(monkeypatch):
    """Same regression guard as the reverb/delay-only versions, exercised for
    the Master-insert isolated path."""
    monkeypatch.setenv("PLG_MAXIMUS_INSERT", "on")
    from pattern_utils import CHANNEL_NAMES, TRACK_KEYS

    blob = fw.build_flp(_rage_pattern())
    stream = fw._fl_dt_payload(blob)
    mix = fw._extract_mixer_events_bytes(blob) or b""
    channel_zone = bytes(stream)[: len(stream) - len(mix)] if mix else bytes(stream)
    names: list[str] = []
    off = 0
    view = memoryview(channel_zone)
    while off < len(view):
        _, end, eid, payload = fw._next_event_bounds(view, off)
        if eid == fw._CH_NAME and isinstance(payload, bytes):
            names.append(fw._read_text_event(payload))
        off = end
    expected = {CHANNEL_NAMES.get(k, k) for k in TRACK_KEYS}
    assert set(names) <= expected | {k for k in TRACK_KEYS}
    for bad in ("Fruity Reeverb 2", "Fruity Delay 3", "Fruity Blood Overdrive", "Maximus"):
        assert bad not in names


def test_sub_808_sampler_gets_clip_and_boost(monkeypatch):
    monkeypatch.delenv("PLG_INSERT_SPACE_FX", raising=False)
    pattern = _rage_pattern()
    mix_insert_fx.configure_space_mix(pattern)
    events = fw._channel_fx_events("sub_808", {}, enabled=False, data=pattern)
    assert events, "808 sampler drive must be on by default for rage"
    # word event: [id][lo][hi]
    preamp_id = fw._CH_PREAMP
    idx = events.find(bytes([preamp_id]))
    assert idx >= 0
    boost = int.from_bytes(events[idx + 1 : idx + 3], "little")
    assert 64 <= boost <= 102  # 25%–40% of 256
    assert bytes([fw._CH_FX_FLAGS]) in bytes(events)


def test_calm_lane_has_no_default_808_drive():
    pattern = _rage_pattern()
    pattern["plg_variety"]["genre"] = "lofi"
    pattern["user_prompt"] = "lofi study beat"
    mix_insert_fx.configure_space_mix(pattern)
    events = fw._channel_fx_events("sub_808", {}, enabled=False, data=pattern)
    assert events == b""


def test_channel_rack_not_eaten_by_mixer_stream(monkeypatch):
    """Live-FL confirmed corruption mode: with insert chains on, FL 2025 parsed
    our slot plugins as GENERATOR CHANNELS (Reeverb 2 appeared in the channel
    rack, six channels vanished, editors auto-opened on Master, then AV).
    Guard: a default build's channel list must be exactly the PLG lanes."""
    monkeypatch.delenv("PLG_INSERT_SPACE_FX", raising=False)
    from pattern_utils import CHANNEL_NAMES, TRACK_KEYS

    blob = fw.build_flp(_rage_pattern())
    stream = fw._fl_dt_payload(blob)
    mix = fw._extract_mixer_events_bytes(blob) or b""
    channel_zone = bytes(stream)[: len(stream) - len(mix)] if mix else bytes(stream)
    names: list[str] = []
    off = 0
    view = memoryview(channel_zone)
    while off < len(view):
        _, end, eid, payload = fw._next_event_bounds(view, off)
        if eid == fw._CH_NAME and isinstance(payload, bytes):
            names.append(fw._read_text_event(payload))
        off = end
    expected = {CHANNEL_NAMES.get(k, k) for k in TRACK_KEYS}
    assert set(names) <= expected | {k for k in TRACK_KEYS}
    for bad in ("Fruity Reeverb 2", "Fruity Delay 3", "Fruity Blood Overdrive"):
        assert bad not in names


def test_mix_table_is_us_studio_staging():
    vol = fw._DEFAULT_CHANNEL_VOL
    assert vol["kick"] == pytest.approx(10000, abs=3)     # 0 dB
    assert vol["snare"] == pytest.approx(3162, abs=3)     # -10 dB
    assert vol["clap"] == pytest.approx(3162, abs=3)      # -10 dB
    assert vol["snare_layer"] == pytest.approx(1259, abs=3)  # -18 dB
    # 2026-07: -3 dB, not unity — the 808's long slabs must sit under the
    # Current 808 balance is -0.5 dB; kick transient ownership comes from its
    # dedicated preamp/clipper path rather than a large fader gap.
    assert vol["sub_808"] == pytest.approx(9441, abs=3)  # -0.5 dB
    assert vol["hi_hats"] == pytest.approx(2512, abs=3)   # -12 dB
    assert vol["melody_lead"] == pytest.approx(2512, abs=3)  # -12 dB
    # Kick alone is the loudest tier into the Soft Clipper; the 808 sits under
    # it (2026-07 producer law — they no longer share a level), but still well
    # above the rest of the kit.
    assert vol["kick"] == max(vol.values())
    assert vol["sub_808"] < vol["kick"]
    assert vol["sub_808"] > vol["snare"]
    assert "master" not in vol  # master stays at 0 dB (future limiter slot)


def test_song_knocks_after_short_intro():
    from arranger import arrange_song

    pattern = _rage_pattern()
    arrange_song(pattern)
    arrangement = pattern["plg_arrangement"]
    assert arrangement[0]["name"] == "intro" and arrangement[0]["bars"] == 4
    assert arrangement[1]["name"] == "hook"
    kicks = pattern["tracks"]["kick"]
    assert not [k for k in kicks if float(k["time_step"]) < 16.0]  # intro = melody only
    assert [k for k in kicks if 16.0 <= float(k["time_step"]) < 48.0]  # hook knocks


def test_roll_probability_matches_spec():
    from hihat_rolls import ROLL_PROB

    assert ROLL_PROB == pytest.approx(0.30)
