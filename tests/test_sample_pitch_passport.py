import math
import struct
import wave

from sample_pitch_passport import sample_pitch_passport
from flp_writer import _CH_ROOT_NOTE, _channel_sampler_settings_events, build_flp


def _write_sine(path, frequency: float, rate: int = 44100):
    frames = []
    for index in range(rate):
        attack = min(1.0, index / 300.0)
        value = int(26000 * attack * math.sin(2 * math.pi * frequency * index / rate))
        frames.append(struct.pack("<h", value))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes(b"".join(frames))


def test_pitch_passport_detects_808_root_pitch_class(tmp_path):
    sample = tmp_path / "808_A.wav"
    _write_sine(sample, 55.0)
    passport = sample_pitch_passport(sample)
    assert passport["status"] == "ok"
    assert passport["root_pc"] == 9
    assert passport["detected_midi"] == 33
    assert passport["fl_root_note"] == 33
    assert abs(passport["cents"]) < 10


def test_pitch_passport_prefers_embedded_author_root_note(tmp_path):
    sample = tmp_path / "tagged_808.wav"
    _write_sine(sample, 98.0)
    raw = bytearray(sample.read_bytes())
    smpl = struct.pack("<9I", 0, 0, 0, 43, 0, 0, 0, 0, 0)  # author root: G3
    raw.extend(b"smpl" + struct.pack("<I", len(smpl)) + smpl)
    struct.pack_into("<I", raw, 4, len(raw) - 8)
    sample.write_bytes(raw)

    passport = sample_pitch_passport(sample)

    assert passport["fl_root_note"] == 43
    assert passport["root_source"] == "wav_smpl"


def test_pitch_passport_rejects_a_default_tag_that_conflicts_with_body(tmp_path):
    sample = tmp_path / "default_tag_808.wav"
    _write_sine(sample, 32.7)
    raw = bytearray(sample.read_bytes())
    smpl = struct.pack("<9I", 0, 0, 0, 60, 0, 0, 0, 0, 0)
    raw.extend(b"smpl" + struct.pack("<I", len(smpl)) + smpl)
    struct.pack_into("<I", raw, 4, len(raw) - 8)
    sample.write_bytes(raw)

    passport = sample_pitch_passport(sample)

    assert passport["fl_root_note"] == 24
    assert passport["root_source"] == "spectral_ambiguous"
    assert passport["status"] == "uncertain"


def test_808_sampler_writes_detected_root_note_event():
    payload = _channel_sampler_settings_events("808_bass", root_note=33)
    assert bytes([_CH_ROOT_NOTE]) + struct.pack("<I", 33) in payload


def test_full_flp_export_applies_the_selected_808_sample_passport(tmp_path):
    sample = tmp_path / "808_A.wav"
    _write_sine(sample, 55.0)
    pattern = {
        "bpm": 140,
        "plg_total_bars": 8,
        "plg_midi_melody": True,
        "plg_sound_paths": {"808_bass": str(sample)},
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 90}],
            "kick": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 124}],
            "808_bass": [{"time_step": 0.0, "note": "A2", "length": 0.5, "velocity": 116}],
        },
    }
    blob = build_flp(pattern)
    assert pattern["plg_sample_pitch_passports"]["808_bass"]["root_pc"] == 9
    assert bytes([_CH_ROOT_NOTE]) + struct.pack("<I", 33) in blob


def test_flp_keeps_original_author_root_after_fl_safe_conversion(tmp_path, monkeypatch):
    """The PCM24 export copy may lose RIFF ``smpl`` metadata.

    Root calibration must therefore use the selected source WAV, not the
    already-converted path that FL loads.
    """
    import flp_writer as fw

    source = tmp_path / "tagged_source_808.wav"
    converted = tmp_path / "tagged_source_808_pcm24.wav"
    _write_sine(source, 98.0)
    raw = bytearray(source.read_bytes())
    smpl = struct.pack("<9I", 0, 0, 0, 43, 0, 0, 0, 0, 0)
    raw.extend(b"smpl" + struct.pack("<I", len(smpl)) + smpl)
    struct.pack_into("<I", raw, 4, len(raw) - 8)
    source.write_bytes(raw)
    _write_sine(converted, 98.0)  # conversion copy: intentionally no smpl tag

    monkeypatch.setattr(fw, "_sample_paths_from_data", lambda data: {"808_bass": converted})
    pattern = {
        "bpm": 140,
        "plg_total_bars": 8,
        "plg_midi_melody": True,
        "plg_sound_paths": {"808_bass": str(source)},
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 90}],
            "kick": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 124}],
            "808_bass": [{"time_step": 0.0, "note": "G3", "length": 0.5, "velocity": 116}],
        },
    }

    blob = fw.build_flp(pattern)

    assert bytes([_CH_ROOT_NOTE]) + struct.pack("<I", 43) in blob


# --- audio passport rejects a bad 808 instead of shipping it ----------------


def _write_stony_808(path, rate: int = 44100, seconds: float = 1.0):
    """A near-sine sub with a very slow attack — the 'moan' failure mode: it
    measures loud, wins a name/loudness rank, then disappears under the kick."""
    frames = []
    total = int(rate * seconds)
    attack_samples = int(rate * 0.25)  # ~250 ms fade-in, no transient at all
    for index in range(total):
        env = min(1.0, index / attack_samples)
        value = int(26000 * env * math.sin(2 * math.pi * 42.0 * index / rate))
        frames.append(struct.pack("<h", value))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes(b"".join(frames))


def _write_healthy_808(path, rate: int = 44100, seconds: float = 1.0):
    """Real 808: instant transient, then a long decaying body."""
    frames = []
    total = int(rate * seconds)
    for index in range(total):
        t = index / rate
        env = math.exp(-3.0 * t)
        click = 0.5 * math.exp(-400.0 * t) * math.sin(2 * math.pi * 1400.0 * t)
        value = int(24000 * (env * math.sin(2 * math.pi * 55.0 * t) + click))
        frames.append(struct.pack("<h", max(-32000, min(32000, value))))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes(b"".join(frames))


def test_audio_passport_rejects_a_slow_attack_sub_only_808(tmp_path):
    from library_quality import audio_rejection_reason

    stony = tmp_path / "MOODY 808.wav"
    _write_stony_808(stony)
    assert audio_rejection_reason(stony, track="808_bass") == "sub_only_slow_808_bass"


def test_audio_passport_rejects_an_808_with_no_body(tmp_path):
    from library_quality import audio_rejection_reason

    clicky = tmp_path / "SHORT 808.wav"
    _write_healthy_808(clicky, seconds=0.2)  # under the 0.35 s body minimum
    assert audio_rejection_reason(clicky, track="808_bass") == "too_short_for_808_body"


def test_a_healthy_808_passes_the_passport(tmp_path):
    from library_quality import audio_rejection_reason

    good = tmp_path / "GOOD 808.wav"
    _write_healthy_808(good)
    assert audio_rejection_reason(good, track="808_bass") is None


def test_bad_808_is_replaced_from_the_library_not_silently_used(tmp_path):
    """End-to-end: a rejected 808 must be swapped for a healthy one, and the
    rejection recorded — never quietly exported as-is."""
    from library_catalog import scan_library
    from starter_kit import attach_sounds_to_pattern

    library = tmp_path / "PLG_Sounds"
    library.mkdir()
    stony = library / "DEEP 808 Bass.wav"
    _write_stony_808(stony)
    healthy = library / "HARD 808 Bass.wav"
    _write_healthy_808(healthy)

    catalog = scan_library(library, quarantine=False)
    pattern = {
        "bpm": 140,
        "style": "trap",
        "plg_genre": "trap",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 124}],
            "808_bass": [{"time_step": 0.0, "note": "C3", "length": 2.0, "velocity": 116}],
        },
        # Force the bad pick in, exactly as a stale/LLM-chosen sample would arrive.
        "plg_sound_paths": {"808_bass": str(stony)},
    }
    sound_map = attach_sounds_to_pattern(
        pattern, catalog, library_root=library, prompt="dark trap 808"
    )

    chosen = sound_map.get("808_bass")
    if chosen is not None:
        # Whatever was chosen must itself be healthy — that is the contract.
        from pathlib import Path

        from library_quality import audio_rejection_reason

        assert audio_rejection_reason(Path(chosen), track="808_bass") is None
        assert Path(chosen).resolve() != stony.resolve()
    else:
        # The only legal alternative is dropping the lane, never shipping the
        # bad sample.
        assert not pattern["tracks"].get("808_bass")


# --- harmony coverage: a verified root owns every pitch class in C2-C3 ---


def test_register_scores_do_not_ban_a_required_harmony_pitch_class(tmp_path, monkeypatch):
    """Register preference must not rewrite or reject MIDI harmony."""
    import sample_pitch_passport as spp

    sample = tmp_path / "controlled_808.wav"
    _write_sine(sample, 55.0)

    note_map = {str(pc): 48 + pc for pc in range(12)}
    note_scores = {str(pc): 2.6 for pc in range(12)}
    note_scores["2"] = 0.5  # D: far below every other pc's score -> weak
    note_scores["4"] = 1.9  # E: below the single best, but within margin -> not weak

    def fake_register(signal, rate, root_hz):
        return 4, {"C3": 0.0, "C4": 2.6, "C5": 0.0}, note_map, note_scores

    monkeypatch.setattr(spp, "_preferred_808_register", fake_register)
    monkeypatch.setattr(spp, "_audible_808_window", lambda *args: (70.0, 150.0, set(range(12))))
    passport = spp.sample_pitch_passport(sample)

    assert passport["weak_pitch_classes"] == []


def test_verified_808_supports_all_harmony_pitch_classes_in_target_lane(tmp_path, monkeypatch):
    """All pitch classes remain legal when root and target lane are verified."""
    import sample_pitch_passport as spp

    sample = tmp_path / "even_808.wav"
    _write_sine(sample, 55.0)

    note_map = {str(pc): 48 + pc for pc in range(12)}
    note_scores = {str(pc): 2.6 + 0.01 * pc for pc in range(12)}

    def fake_register(signal, rate, root_hz):
        return 4, {"C3": 0.0, "C4": 2.6, "C5": 0.0}, note_map, note_scores

    monkeypatch.setattr(spp, "_preferred_808_register", fake_register)
    monkeypatch.setattr(spp, "_audible_808_window", lambda *args: (70.0, 150.0, set(range(12))))
    passport = spp.sample_pitch_passport(sample)
    assert passport["weak_pitch_classes"] == []


def test_pure_sub_keeps_harmony_pitch_classes_in_c2_c3_lane(tmp_path):
    sample = tmp_path / "pure_sub.wav"
    _write_sine(sample, 32.7)

    passport = sample_pitch_passport(sample)

    assert 0 in passport["supported_pitch_classes"]
    assert 4 in passport["supported_pitch_classes"]
    assert passport["weak_pitch_classes"] == []


# --- starter_kit: a sample weak on the song's actual needed notes gets swapped ---


def test_needed_pitch_classes_reads_the_808_track():
    from starter_kit import _needed_pitch_classes

    notes = [
        {"time_step": 0.0, "note": "D3"},
        {"time_step": 4.0, "note": "F3"},
        {"time_step": 8.0, "note": "D3"},  # repeat, not double-counted
        {"time_step": 12.0, "note": "A3", "slide": True},  # slides are bends, not attacks
    ]
    from music_theory import parse_note_name

    assert {"D3", "F3"} == {
        n["note"] for n in notes if not n.get("slide") and n["note"] in ("D3", "F3")
    }
    assert _needed_pitch_classes(notes) == {parse_note_name("D3") % 12, parse_note_name("F3") % 12}


def test_sample_weak_on_checks_the_passports_weak_list(tmp_path, monkeypatch):
    from starter_kit import _sample_weak_on
    import sample_pitch_passport as spp

    sample = tmp_path / "weak.wav"
    sample.write_bytes(b"stub")
    # _sample_weak_on does `from sample_pitch_passport import sample_pitch_passport`
    # locally, so the source module (not starter_kit's namespace) is the patch target.
    monkeypatch.setattr(spp, "sample_pitch_passport", lambda path: {"weak_pitch_classes": [2, 4]})
    assert _sample_weak_on(sample, {2}) is True
    assert _sample_weak_on(sample, {7}) is False
    assert _sample_weak_on(sample, set()) is False


def test_attach_sounds_swaps_a_sample_weak_on_the_songs_needed_notes(tmp_path):
    """End-to-end: a picked 808 that outright passes the audio-passport gate
    (real body, not a click) but is weak on a pitch class the song actually
    uses must still be swapped for a healthier candidate when one exists."""
    from pathlib import Path

    from library_catalog import scan_library
    from starter_kit import attach_sounds_to_pattern

    library = tmp_path / "PLG_Sounds"
    library.mkdir()
    weak_on_d = library / "NARROW 808 Bass.wav"
    _write_healthy_808(weak_on_d)
    healthy = library / "WIDE 808 Bass.wav"
    _write_healthy_808(healthy)

    catalog = scan_library(library, quarantine=False)
    pattern = {
        "bpm": 140,
        "style": "trap",
        "plg_genre": "trap",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 124}],
            "808_bass": [{"time_step": 0.0, "note": "D3", "length": 2.0, "velocity": 116}],
        },
        "plg_sound_paths": {"808_bass": str(weak_on_d)},
    }

    import sample_pitch_passport as spp

    real_passport = spp.sample_pitch_passport

    def fake_passport(path):
        result = real_passport(path)
        if Path(path).resolve() == weak_on_d.resolve():
            result = dict(result)
            result["weak_pitch_classes"] = [2]  # D
        return result

    import unittest.mock

    # attach_sounds_to_pattern's various helpers (starter_kit, sample_match,
    # _sample_weak_on) all do a fresh local `from sample_pitch_passport import
    # sample_pitch_passport` — patching the source module covers every one.
    with unittest.mock.patch.object(spp, "sample_pitch_passport", side_effect=fake_passport):
        sound_map = attach_sounds_to_pattern(
            pattern, catalog, library_root=library, prompt="dark trap 808"
        )

    # pick_best_for_track's match ranking isn't pinned here — it may land on
    # the library candidate or fall through to the bundled starter kit, same
    # as the outright-rejection swap above. The contract is just that the
    # sample weak on D never survives, matching test_bad_808_is_replaced_...
    chosen = Path(sound_map["808_bass"]).resolve()
    assert chosen != weak_on_d.resolve()
    from starter_kit import _sample_weak_on

    assert not _sample_weak_on(chosen, {2})
