from pathlib import Path

import mido
import pytest

from midi_ingest import (
    UPLOAD_MAX_FILE_BYTES,
    UPLOAD_MAX_NOTES,
    UPLOAD_RIGHTS_WARNING,
    find_library_midi,
    ingest_library_midi,
    ingest_uploaded_midi,
    pick_library_midi,
    read_midi_notes,
    resolve_midi_folder,
    split_by_register,
)


@pytest.fixture(autouse=True)
def _all_lanes(monkeypatch):
    # Full-router semantics (rnb etc.); the hip-hop focus gate is tested in
    # test_lane_focus.py.
    monkeypatch.setenv("PLG_LANES", "all")


def _write_midi(path: Path, notes, ticks_per_beat: int = 480) -> None:
    """notes: list of (midi_note, start_tick, dur_tick, velocity)."""
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    events = []
    for note, start, dur, vel in notes:
        events.append((start, "on", note, vel))
        events.append((start + dur, "off", note, 0))
    events.sort(key=lambda e: e[0])
    last = 0
    for tick, kind, note, vel in events:
        delta = tick - last
        last = tick
        msg_type = "note_on" if kind == "on" else "note_off"
        track.append(mido.Message(msg_type, note=note, velocity=vel, time=delta))
    mid.save(str(path))


def test_read_midi_notes_beats_and_names(tmp_path: Path):
    path = tmp_path / "loop.mid"
    # melody C5 (midi 60) 1 beat, bass C3 (midi 36) 2 beats, both at tick 0
    _write_midi(path, [(60, 0, 480, 100), (36, 0, 960, 110)])
    notes = read_midi_notes(path)
    assert len(notes) == 2
    by_name = {n["note"]: n for n in notes}
    assert by_name["C5"]["length"] == 1.0
    assert by_name["C5"]["velocity"] == 100
    assert by_name["C3"]["length"] == 2.0


def test_split_by_register():
    notes = [{"note": "C5", "time_step": 0, "length": 1.0, "velocity": 100},
             {"note": "C3", "time_step": 0, "length": 2.0, "velocity": 110}]
    melody, bass = split_by_register(notes)
    assert [n["note"] for n in melody] == ["C5"]
    assert [n["note"] for n in bass] == ["C3"]


def test_ingest_plays_matching_midi(tmp_path: Path):
    (tmp_path / "midi").mkdir()
    _write_midi(tmp_path / "midi" / "dark_melody_loop.mid", [(60, 0, 480, 100), (62, 480, 480, 96)])
    pattern = {
        "style": "rnb",
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}]},
    }
    ok = ingest_library_midi(pattern, library_root=tmp_path, prompt="dark melody", style="rnb")
    assert ok is True
    assert pattern["plg_midi_ingest"]["name"] == "dark_melody_loop.mid"
    assert len(pattern["tracks"]["melody_lead"]) == 2  # LLM melody replaced by the MIDI
    # Scope boundary (2026-07 product decision): the legacy whole-library
    # fallback is an AUTO-match, not an explicit upload — must not preserve.
    assert "plg_preserve_melody_sections" not in pattern


def test_ingest_skips_for_local_opium_composer(tmp_path: Path):
    """Opium/rage leads are composed locally — library MIDI must not hijack them."""
    (tmp_path / "midi").mkdir()
    _write_midi(tmp_path / "midi" / "dark_melody_loop.mid", [(60, 0, 480, 100)])
    pattern = {"style": "opium", "tracks": {"melody_lead": []}}
    ok = ingest_library_midi(pattern, library_root=tmp_path, prompt="dark melody", style="opium")
    assert ok is False
    assert "plg_midi_ingest" not in pattern


def test_ingest_skips_when_nothing_matches(tmp_path: Path):
    (tmp_path / "midi").mkdir()
    _write_midi(tmp_path / "midi" / "zzz_unrelated.mid", [(60, 0, 480, 100)])
    original = [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}]
    pattern = {"style": "trap", "tracks": {"melody_lead": list(original)}}
    ok = ingest_library_midi(pattern, library_root=tmp_path, prompt="trap beat", style="trap")
    assert ok is False
    assert pattern["tracks"]["melody_lead"] == original  # untouched


def test_ingest_no_midi_dir(tmp_path: Path):
    pattern = {"tracks": {}}
    assert ingest_library_midi(pattern, library_root=tmp_path / "missing") is False


# --- legacy whole-library fallback: drum-lane exclusion (2026-07 audit) ----
# Mirrors midi_corpus._detect_lane's "drums" bucket — a stray one-shot MIDI
# stub from a drum kit (kick/snare/perc/vox/riser/…) must never win the
# "melody" via this fallback, even as the library's only/best-scoring file.


def test_find_library_midi_excludes_drum_lane_stubs(tmp_path: Path):
    _write_midi(tmp_path / "Kick_One_Shot.mid", [(36, 0, 480, 100)])
    _write_midi(tmp_path / "dark_melody_loop.mid", [(60, 0, 480, 100)])

    found = {p.name for p in find_library_midi(tmp_path)}
    assert found == {"dark_melody_loop.mid"}


def test_ingest_library_midi_ignores_stray_drum_kit_stub_as_sole_candidate(tmp_path: Path):
    """The exact scenario a user worried about: a one-shot drum stub is the
    ONLY .mid anywhere in the library, and the prompt carries a generic
    explicit-MIDI trigger word — before this fix, pick_library_midi's
    score>0-or-explicit-trigger gate could still hand this stub back as the
    "melody". Now it's filtered out before scoring even happens."""
    _write_midi(tmp_path / "XPOSED_Snap.mid", [(38, 0, 240, 110)])
    original = [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}]
    pattern = {"style": "trap", "tracks": {"melody_lead": list(original)}}

    ok = ingest_library_midi(pattern, library_root=tmp_path, prompt="play some midi", style="trap")
    assert ok is False
    assert pattern["tracks"]["melody_lead"] == original  # LLM melody untouched


def test_find_library_midi_drum_lane_exclusion_applies_to_extra_root_too(tmp_path: Path):
    library_root = tmp_path / "library"
    library_root.mkdir()
    midi_root = tmp_path / "PLG_MIDI"
    midi_root.mkdir()
    _write_midi(midi_root / "Kick_One_Shot.mid", [(36, 0, 480, 100)])

    found = find_library_midi(library_root, extra_root=midi_root)
    assert found == []


# --- ingest_uploaded_midi: explicit user upload (audit 2026-07) -----------


def test_uploaded_midi_replaces_the_llm_melody(tmp_path: Path):
    path = tmp_path / "my_favorite_melody.mid"
    _write_midi(path, [(64, 0, 480, 100), (67, 480, 480, 96)])
    pattern = {
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}]},
    }
    result = ingest_uploaded_midi(pattern, path)
    assert result["ok"] is True
    assert result["notes"] == 2
    assert result["truncated"] is False
    assert result["warning"] == UPLOAD_RIGHTS_WARNING
    assert len(pattern["tracks"]["melody_lead"]) == 2
    assert pattern["plg_midi_melody"] is True
    assert pattern["plg_midi_uploaded"] is True
    # 2026-07 product decision: explicit upload -> preserve, no opt-out.
    assert pattern["plg_preserve_melody_sections"] is True
    assert pattern["plg_midi_ingest"]["name"] == "my_favorite_melody.mid"
    assert pattern["plg_midi_structure"]["status"] == "ok"
    assert pattern["plg_midi_structure"]["span"]["note_count"] == 2


def test_uploaded_note_only_midi_locks_bpm_from_its_filename(tmp_path: Path):
    path = tmp_path / "Fast_Pluck_150_BPM_Dmin.mid"
    _write_midi(path, [(64, 0, 480, 100)])
    pattern = {"bpm": 130, "tracks": {}}

    result = ingest_uploaded_midi(pattern, path)

    assert result["ok"] is True
    assert pattern["bpm"] == 150.0
    assert pattern["plg_midi_tempo_bpm"] == 150.0
    assert pattern["plg_midi_tempo_source"] == "filename_bpm"


def test_uploaded_midi_keeps_long_real_material_past_the_old_8_bar_cap(tmp_path: Path):
    """2026-07 audit follow-up: UPLOAD_MAX_BEATS raised from 32 (8 bars) to
    128 (32 bars) — arranger._tile's cursor-tiling fix only ever engages for
    material longer than a single section, so an explicit upload (the
    highest-trust source, and the one resolve_midi_folder's PLG_MIDI folder
    reuses this same cap for) needs real headroom past 8 bars to exercise it
    on actual user MIDI packs, not just transcribed reference audio."""
    path = tmp_path / "long_pack.mid"
    notes = [(60 + (bar % 12), bar * 480 * 4, 480, 100) for bar in range(24)]  # 24 bars, 1 note/bar
    _write_midi(path, notes)
    pattern = {"tracks": {}}

    result = ingest_uploaded_midi(pattern, path)
    assert result["ok"] is True
    assert result["truncated"] is False
    assert result["notes"] == 24
    melody = pattern["tracks"]["melody_lead"]
    assert len(melody) == 24
    assert max(n["time_step"] for n in melody) == pytest.approx(23 * 4.0)


def test_uploaded_midi_rejects_non_midi_extension(tmp_path: Path):
    path = tmp_path / "not_midi.txt"
    path.write_text("hello")
    pattern = {"tracks": {}}
    result = ingest_uploaded_midi(pattern, path)
    assert result["ok"] is False
    assert "warning" in result and result["warning"]
    assert "plg_midi_melody" not in pattern


def test_uploaded_midi_rejects_missing_file(tmp_path: Path):
    pattern = {"tracks": {}}
    result = ingest_uploaded_midi(pattern, tmp_path / "does_not_exist.mid")
    assert result["ok"] is False
    assert result["warning"]


def test_uploaded_midi_rejects_oversized_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("midi_ingest.UPLOAD_MAX_FILE_BYTES", 10)
    path = tmp_path / "small_but_over_the_test_cap.mid"
    _write_midi(path, [(60, 0, 480, 100)])
    pattern = {"tracks": {}}
    result = ingest_uploaded_midi(pattern, path)
    assert result["ok"] is False
    assert "too large" in result["warning"]
    assert "plg_midi_melody" not in pattern


def test_uploaded_midi_with_no_notes_warns(tmp_path: Path):
    path = tmp_path / "empty.mid"
    _write_midi(path, [])
    pattern = {"tracks": {}}
    result = ingest_uploaded_midi(pattern, path)
    assert result["ok"] is False
    assert "No readable notes" in result["warning"]


def test_uploaded_midi_truncates_past_the_note_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("midi_ingest.UPLOAD_MAX_NOTES", 3)
    path = tmp_path / "many_notes.mid"
    notes = [(60 + i, i * 480, 240, 100) for i in range(5)]
    _write_midi(path, notes)
    pattern = {"tracks": {}}
    result = ingest_uploaded_midi(pattern, path)
    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["notes"] == 3
    assert len(pattern["tracks"]["melody_lead"]) + len(pattern["tracks"].get("sub_808", [])) == 3


def test_uploaded_midi_length_cap_passes_through_to_read_midi_notes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("midi_ingest.UPLOAD_MAX_BEATS", 2.0)
    path = tmp_path / "long_loop.mid"
    # beats 0, 1, 2, 3 -- only beats < 2.0 (max_beats) should survive
    notes = [(60 + i, i * 480, 240, 100) for i in range(4)]
    _write_midi(path, notes)
    pattern = {"tracks": {}}
    result = ingest_uploaded_midi(pattern, path)
    assert result["ok"] is True
    assert result["notes"] == 2


def test_respect_uploaded_scale_false_leaves_pattern_scale_untouched(tmp_path: Path):
    path = tmp_path / "melody.mid"
    _write_midi(path, [(60, 0, 480, 100), (62, 480, 480, 100), (64, 960, 480, 100)])
    pattern = {"scale": "phrygian", "tracks": {}}
    result = ingest_uploaded_midi(pattern, path, respect_uploaded_scale=False)
    assert result["ok"] is True
    assert pattern["scale"] == "phrygian"  # genre profile's scale still wins
    assert "plg_uploaded_scale_lock" not in pattern


def test_respect_uploaded_scale_true_detects_the_uploaded_melodys_own_scale(tmp_path: Path):
    path = tmp_path / "c_major_run.mid"
    # C D E F G A B -- an unambiguous C major run (root ties resolve to C, the
    # lowest pitch class, and major is the only SCALES entry containing all 7).
    degrees = [60, 62, 64, 65, 67, 69, 71]
    notes = [(pitch, i * 480, 460, 100) for i, pitch in enumerate(degrees)]
    _write_midi(path, notes)
    pattern = {"scale": "phrygian", "tracks": {}}
    result = ingest_uploaded_midi(pattern, path, respect_uploaded_scale=True)
    assert result["ok"] is True
    assert pattern["scale"] == "major"
    assert pattern["plg_uploaded_scale_lock"] == {"root_pc": 0, "scale": "major"}


def test_ingest_does_not_hard_lock_kit_pack_bell_template(tmp_path: Path):
    path = tmp_path / "bell_loop.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Adieu - Bell - Ding", time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=1920))
    track.append(mido.Message("note_on", note=64, velocity=90, time=0))
    track.append(mido.Message("note_off", note=64, velocity=0, time=0))
    mid.save(str(path))
    pattern = {"tracks": {}}
    result = ingest_uploaded_midi(pattern, path)
    assert result["ok"] is True
    # Pack template must not stamp Bell as a hard instrument lock.
    assert pattern.get("melody_dna") != "Bell"
    assert "Bell" not in str(pattern.get("plg_instrument_tag") or "")
    hints = pattern.get("plg_midi_instrument_hints") or {}
    assert hints.get("dna_from_name") is None


def test_ingest_stamps_hard_lock_from_gm_program(tmp_path: Path):
    path = tmp_path / "gm_piano.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("program_change", program=0, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.save(str(path))
    pattern = {"tracks": {}}
    result = ingest_uploaded_midi(pattern, path)
    assert result["ok"] is True
    assert pattern.get("melody_dna") == "Piano"
    assert "Piano" in str(pattern.get("plg_instrument_tag") or "")
    assert pattern.get("plg_midi_system_lock", {}).get("source") == "program_change"
    assert pattern.get("plg_midi_system_lock", {}).get("dna") == "Piano"


# --- resolve_midi_folder: the midi/ folder IS the control surface ---------
# (audit 2026-07, "final decision" follow-up) — no separate explicit/auto UI
# toggle: exactly one file in library_root/midi/ wins unconditionally (no
# keyword-score reject gate), 2+ files always resolve to SOMETHING (never a
# probabilistic or score-threshold reject), empty/missing folder is a no-op.


def test_resolve_midi_folder_missing_directory_is_a_noop(tmp_path: Path):
    pattern = {"tracks": {}}
    assert resolve_midi_folder(pattern, tmp_path, prompt="dark trap") is None


def test_resolve_midi_folder_empty_directory_is_a_noop(tmp_path: Path):
    (tmp_path / "midi").mkdir()
    pattern = {"tracks": {}}
    assert resolve_midi_folder(pattern, tmp_path, prompt="dark trap") is None


def test_single_file_always_used_even_with_zero_keyword_overlap(tmp_path: Path):
    """The core guarantee: one file in the folder is used UNCONDITIONALLY —
    no score>0 requirement, no explicit trigger word needed in the prompt.
    Run across many unrelated prompts to prove there's no hidden reject path."""
    (tmp_path / "midi").mkdir()
    _write_midi(tmp_path / "midi" / "completely_unrelated_filename.mid", [(60, 0, 480, 100)])

    for prompt in ("aggressive rage beat", "", "dark phonk 150 bpm", "xyz123 nonsense"):
        pattern = {"tracks": {}}
        result = resolve_midi_folder(pattern, tmp_path, prompt=prompt)
        assert result is not None
        assert result["ok"] is True
        assert pattern["plg_midi_ingest"]["name"] == "completely_unrelated_filename.mid"


def test_single_file_used_even_across_many_seeds(tmp_path: Path):
    """No RNG path should ever reject the lone candidate — vary plg_variety
    seed across many values, the single file must win every single time."""
    (tmp_path / "midi").mkdir()
    _write_midi(tmp_path / "midi" / "only_one.mid", [(60, 0, 480, 100)])

    for seed in range(30):
        pattern = {"tracks": {}, "plg_variety": {"seed": seed}}
        result = resolve_midi_folder(pattern, tmp_path, prompt="")
        assert result is not None and result["ok"] is True
        assert pattern["plg_midi_ingest"]["name"] == "only_one.mid"


def test_multiple_files_always_resolve_to_something_even_with_no_prompt_match(tmp_path: Path):
    """Core guarantee for the 2+ case: never a probabilistic/score-threshold
    reject. None of these filenames share any token with the prompt."""
    (tmp_path / "midi").mkdir()
    names = ["aaa_unrelated.mid", "bbb_unrelated.mid", "ccc_unrelated.mid"]
    for name in names:
        _write_midi(tmp_path / "midi" / name, [(60, 0, 480, 100)])

    for seed in range(20):
        pattern = {"tracks": {}, "plg_variety": {"seed": seed}}
        result = resolve_midi_folder(pattern, tmp_path, prompt="dark aggressive rage 150 bpm")
        assert result is not None
        assert result["ok"] is True
        assert pattern["plg_midi_ingest"]["name"] in names


def test_multiple_files_prefer_the_prompt_matching_one(tmp_path: Path):
    (tmp_path / "midi").mkdir()
    _write_midi(tmp_path / "midi" / "unrelated_track.mid", [(60, 0, 480, 100)])
    _write_midi(tmp_path / "midi" / "dark_rage_hook.mid", [(60, 0, 480, 100)])

    pattern = {"tracks": {}}
    result = resolve_midi_folder(pattern, tmp_path, prompt="dark rage beat")
    assert result is not None and result["ok"] is True
    assert pattern["plg_midi_ingest"]["name"] == "dark_rage_hook.mid"


def test_multiple_files_vary_across_seeds_when_equally_matched(tmp_path: Path):
    (tmp_path / "midi").mkdir()
    names = ["hook_one.mid", "hook_two.mid", "hook_three.mid"]
    # Different melodies: identical copies are one candidate (see test_service_midi.py), not several chances.
    for offset, name in enumerate(names):
        _write_midi(tmp_path / "midi" / name, [(60 + offset, 0, 480, 100)])

    picked = set()
    for seed in range(30):
        pattern = {"tracks": {}, "plg_variety": {"seed": seed}}
        result = resolve_midi_folder(pattern, tmp_path, prompt="hook")
        picked.add(pattern["plg_midi_ingest"]["name"])
    assert len(picked) > 1  # not pinned to the same file every generation


def test_resolve_midi_folder_respects_uploaded_scale_option(tmp_path: Path):
    (tmp_path / "midi").mkdir()
    degrees = [60, 62, 64, 65, 67, 69, 71]  # unambiguous C major run
    notes = [(pitch, i * 480, 460, 100) for i, pitch in enumerate(degrees)]
    _write_midi(tmp_path / "midi" / "only_one.mid", notes)

    pattern = {"tracks": {}, "scale": "phrygian"}
    resolve_midi_folder(pattern, tmp_path, prompt="", respect_uploaded_scale=True)
    assert pattern["scale"] == "major"


# --- PLG_MIDI as its own top-level sibling (2026-07) — merged in, never
# replacing the legacy library_root/midi/ nested layout -------------------


def test_resolve_midi_folder_uses_midi_root_when_legacy_folder_is_missing(tmp_path: Path):
    """No library_root/midi/ at all — the file lives only in the new
    top-level PLG_MIDI sibling — must still resolve."""
    library_root = tmp_path / "library"
    library_root.mkdir()
    midi_root = tmp_path / "PLG_MIDI"
    midi_root.mkdir()
    _write_midi(midi_root / "only_one.mid", [(60, 0, 480, 100)])

    pattern = {"tracks": {}}
    result = resolve_midi_folder(pattern, library_root, midi_root=midi_root, prompt="")
    assert result is not None and result["ok"] is True
    assert pattern["plg_midi_ingest"]["name"] == "only_one.mid"


def test_resolve_midi_folder_merges_legacy_and_midi_root_candidates(tmp_path: Path):
    """Merge, not replace: a file in the OLD nested library_root/midi/ and a
    file in the NEW top-level PLG_MIDI sibling must both be real candidates —
    the best prompt match wins regardless of which root it came from."""
    library_root = tmp_path / "library"
    (library_root / "midi").mkdir(parents=True)
    _write_midi(library_root / "midi" / "unrelated_track.mid", [(60, 0, 480, 100)])

    midi_root = tmp_path / "PLG_MIDI"
    midi_root.mkdir()
    _write_midi(midi_root / "dark_rage_hook.mid", [(60, 0, 480, 100)])

    pattern = {"tracks": {}}
    result = resolve_midi_folder(
        pattern, library_root, midi_root=midi_root, prompt="dark rage beat"
    )
    assert result is not None and result["ok"] is True
    assert pattern["plg_midi_ingest"]["name"] == "dark_rage_hook.mid"


def test_resolve_midi_folder_without_midi_root_keeps_old_behavior(tmp_path: Path):
    """Opt-in: omitting midi_root entirely must behave exactly like before —
    only the legacy nested folder is searched."""
    (tmp_path / "midi").mkdir()
    _write_midi(tmp_path / "midi" / "only_one.mid", [(60, 0, 480, 100)])

    pattern = {"tracks": {}}
    result = resolve_midi_folder(pattern, tmp_path, prompt="")
    assert result is not None and result["ok"] is True
    assert pattern["plg_midi_ingest"]["name"] == "only_one.mid"


def test_find_library_midi_merges_extra_root(tmp_path: Path):
    library_root = tmp_path / "library"
    library_root.mkdir()
    _write_midi(library_root / "in_library.mid", [(60, 0, 480, 100)])

    extra_root = tmp_path / "PLG_MIDI"
    extra_root.mkdir()
    _write_midi(extra_root / "in_midi_root.mid", [(60, 0, 480, 100)])

    found = {p.name for p in find_library_midi(library_root, extra_root=extra_root)}
    assert found == {"in_library.mid", "in_midi_root.mid"}


def test_find_library_midi_extra_root_missing_is_harmless(tmp_path: Path):
    library_root = tmp_path / "library"
    library_root.mkdir()
    _write_midi(library_root / "in_library.mid", [(60, 0, 480, 100)])

    found = find_library_midi(library_root, extra_root=tmp_path / "does_not_exist")
    assert [p.name for p in found] == ["in_library.mid"]


def test_pick_library_midi_finds_prompt_match_in_extra_root(tmp_path: Path):
    library_root = tmp_path / "library"
    library_root.mkdir()
    _write_midi(library_root / "unrelated.mid", [(60, 0, 480, 100)])

    extra_root = tmp_path / "PLG_MIDI"
    extra_root.mkdir()
    _write_midi(extra_root / "dark_rage_hook.mid", [(60, 0, 480, 100)])

    picked = pick_library_midi(library_root, prompt="dark rage", extra_root=extra_root)
    assert picked is not None and picked.name == "dark_rage_hook.mid"


def test_ingest_library_midi_finds_match_only_in_midi_root(tmp_path: Path):
    """library_root itself has no matching .mid at all — the only candidate
    lives in the new PLG_MIDI sibling, passed as midi_root."""
    library_root = tmp_path / "library"
    library_root.mkdir()

    midi_root = tmp_path / "PLG_MIDI"
    midi_root.mkdir()
    _write_midi(midi_root / "dark_melody_loop.mid", [(60, 0, 480, 100), (62, 480, 480, 96)])

    pattern = {
        "style": "rnb",
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}]},
    }
    ok = ingest_library_midi(
        pattern, library_root=library_root, midi_root=midi_root, prompt="dark melody", style="rnb"
    )
    assert ok is True
    assert pattern["plg_midi_ingest"]["name"] == "dark_melody_loop.mid"


def test_note_only_midi_gets_a_deterministic_locked_bpm(tmp_path: Path):
    """2026-08-15: a file with no set_tempo event and no BPM in its name used
    to fall through to the time-seeded variety roll — the same file shipped a
    different tempo every run (Graveyard 144->147, Shuttle 148->153 between
    batches). The third tier must lock a deterministic estimate instead."""
    from midi_ingest import _attach_midi_file_meta

    # eighth-note grid: one onset every half beat, 8 bars.
    notes = [(64, int(i * 240), 200, 96) for i in range(32)]
    path = tmp_path / "nocturne_loop.mid"  # no BPM in the name
    _write_midi(path, notes)

    pattern: dict = {}
    _attach_midi_file_meta(pattern, path)
    assert pattern.get("plg_bpm_locked_from_midi") is True
    assert pattern.get("plg_midi_tempo_source") == "note_onset_estimate"
    first = pattern["bpm"]

    # Deterministic: a second ingest of the same file agrees exactly.
    pattern2: dict = {}
    _attach_midi_file_meta(pattern2, path)
    assert pattern2["bpm"] == first
    assert 70.0 <= float(first) <= 200.0


def test_filename_bpm_still_beats_the_note_estimate(tmp_path: Path):
    from midi_ingest import _attach_midi_file_meta

    notes = [(64, int(i * 240), 200, 96) for i in range(16)]
    path = tmp_path / "loop_150_BPM.mid"
    _write_midi(path, notes)

    pattern: dict = {}
    _attach_midi_file_meta(pattern, path)
    assert pattern.get("plg_midi_tempo_source") == "filename_bpm"
    assert float(pattern["bpm"]) == 150.0


# --- drum parts never become the melody (hat-melody bug) ------------------


def _write_arrangement(path: Path, *, drum_channel: int, drum_track_name: str = "") -> None:
    """Melody on channel 0 plus a closed-hat line (GM note 42) on its own track."""
    mid = mido.MidiFile(ticks_per_beat=480)
    lead = mido.MidiTrack()
    mid.tracks.append(lead)
    for pitch in (64, 67, 71, 72):
        lead.append(mido.Message("note_on", note=pitch, velocity=100, time=0, channel=0))
        lead.append(mido.Message("note_off", note=pitch, velocity=0, time=480, channel=0))
    drums = mido.MidiTrack()
    mid.tracks.append(drums)
    if drum_track_name:
        drums.append(mido.MetaMessage("track_name", name=drum_track_name, time=0))
    for _ in range(16):
        drums.append(mido.Message("note_on", note=42, velocity=80, time=0, channel=drum_channel))
        drums.append(mido.Message("note_off", note=42, velocity=0, time=120, channel=drum_channel))
    mid.save(str(path))


def test_read_midi_notes_skips_the_gm_drum_channel(tmp_path: Path):
    path = tmp_path / "arrangement.mid"
    _write_arrangement(path, drum_channel=9)
    notes = read_midi_notes(path)
    assert sorted({n["note"] for n in notes}) == ["B5", "C6", "E5", "G5"]


def test_read_midi_notes_skips_a_track_named_like_a_drum_part(tmp_path: Path):
    path = tmp_path / "arrangement.mid"
    _write_arrangement(path, drum_channel=0, drum_track_name="Hi-Hats")
    notes = read_midi_notes(path)
    assert len(notes) == 4
    assert "F#3" not in {n["note"] for n in notes}


def test_drum_word_match_is_whole_word_only(tmp_path: Path):
    path = tmp_path / "arrangement.mid"
    _write_arrangement(path, drum_channel=0, drum_track_name="Thatcher Keys")
    assert len(read_midi_notes(path)) == 4 + 16


def test_uploaded_arrangement_keeps_hats_out_of_melody_lead(tmp_path: Path):
    path = tmp_path / "arrangement.mid"
    _write_arrangement(path, drum_channel=9)
    pattern = {"tracks": {}}
    assert ingest_uploaded_midi(pattern, path)["ok"] is True
    assert "F#3" not in {n["note"] for n in pattern["tracks"]["melody_lead"]}


def test_resolve_midi_folder_prefers_melody_over_drum_named_file(tmp_path: Path):
    midi_dir = tmp_path / "midi"
    midi_dir.mkdir()
    _write_midi(midi_dir / "Hats_loop.mid", [(42, 0, 120, 80)])
    _write_midi(midi_dir / "dark_keys.mid", [(60, 0, 480, 100)])
    for seed in range(8):
        pattern = {"plg_variety": {"seed": seed}, "tracks": {}}
        result = resolve_midi_folder(pattern, tmp_path)
        assert result and result["ok"]
        assert pattern["plg_midi_ingest"]["name"] == "dark_keys.mid"


def test_resolve_midi_folder_falls_through_a_drum_only_pick(tmp_path: Path):
    midi_dir = tmp_path / "midi"
    midi_dir.mkdir()
    drums_only = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    drums_only.tracks.append(track)
    track.append(mido.Message("note_on", note=42, velocity=80, time=0, channel=9))
    track.append(mido.Message("note_off", note=42, velocity=0, time=120, channel=9))
    drums_only.save(str(midi_dir / "groove_a.mid"))
    _write_midi(midi_dir / "groove_b.mid", [(60, 0, 480, 100)])
    for seed in range(8):
        pattern = {"plg_variety": {"seed": seed}, "tracks": {}}
        result = resolve_midi_folder(pattern, tmp_path)
        assert result and result["ok"]
        assert pattern["plg_midi_ingest"]["name"] == "groove_b.mid"
