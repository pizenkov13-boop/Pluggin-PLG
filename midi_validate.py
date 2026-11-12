"""Validate PLG pattern JSON and exported MIDI (pretty_midi)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from midi_roles import ROLE_LANES, role_lane_live
from pattern_utils import TRACK_KEYS, parse_note_name, track_notes

try:
    from sub_808_gen import MIN_SUB_LENGTH, SUB_HI, SUB_LO
except ImportError:  # pragma: no cover
    SUB_LO, SUB_HI, MIN_SUB_LENGTH = 24, 47, 0.5

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PATTERN = PROJECT_DIR / "output_pattern.json"
DEFAULT_MIDI_DIR = PROJECT_DIR / "output_midi"
COMBINED_MIDI = DEFAULT_MIDI_DIR / "PLG_Beat.mid"
MIDI_ALLOWED_LANES = (
    "melody_lead",
    "kick",
    "808_bass",
    "hi_hats",
    "clap",
    "snare",
    "snare_layer",
)
MIDI_DISABLED_LANES = ("sub_808", "counter_melody", "fx", "chords", "texture", "cowbell")
# GENERATED lanes stay disabled above. These two are open only as role lanes:
# the user's own notes, split out of the MIDI by midi_roles at the end of the
# run (midi_roles.role_lane_live is the one test of that).
MIDI_ROLE_LANES = ROLE_LANES
MIDI_REQUIRED_SAMPLE_LANES = ("kick", "808_bass", "hi_hats")


def sanitize_midi_mode(data: dict[str, Any]) -> list[str]:
    """Keep only the approved MIDI export contract: seven lanes, plus the two
    role lanes (``MIDI_ROLE_LANES``) when ``midi_roles`` split them out of the
    user's own notes.

    Uploaded MIDI owns ``melody_lead``. No generated harmony, texture, second
    bass lane or ear-candy lane may survive into the final MIDI-mode FLP.
    """
    if not data.get("plg_midi_melody"):
        return []
    fixes: list[str] = []
    tracks = data.setdefault("tracks", {})
    if isinstance(tracks, dict):
        for lane in tuple(tracks):
            if lane not in MIDI_ALLOWED_LANES and not role_lane_live(data, lane):
                tracks.pop(lane, None)
                fixes.append(f"removed track {lane}")

    for field in ("plg_sample_picks", "plg_sound_paths", "plg_mixer_levels"):
        mapped = data.get(field)
        if not isinstance(mapped, dict):
            continue
        for lane in tuple(mapped):
            if lane not in MIDI_ALLOWED_LANES and not role_lane_live(data, lane):
                mapped.pop(lane, None)
                fixes.append(f"removed {field}.{lane}")

    samples = data.get("samples")
    if isinstance(samples, list):
        kept = [
            item
            for item in samples
            if not isinstance(item, dict)
            or item.get("track") in MIDI_ALLOWED_LANES
            or role_lane_live(data, str(item.get("track")))
        ]
        if len(kept) != len(samples):
            fixes.append("removed disabled sample assignments")
            data["samples"] = kept

    meta = data.get("plg_producer_meta")
    if isinstance(meta, dict):
        sidechain = meta.get("sidechain")
        if isinstance(sidechain, list):
            retargeted = 0
            for point in sidechain:
                if isinstance(point, dict) and point.get("target") == "sub_808":
                    point["target"] = "808_bass"
                    retargeted += 1
            if retargeted:
                fixes.append(f"retargeted {retargeted} sidechain points to 808_bass")

    for key in ("plg_fx_kind", "plg_fx_auto"):
        if key in data:
            data.pop(key, None)
            fixes.append(f"removed {key}")

    from flp_template import build_playlist_clips

    build_playlist_clips(data)
    return fixes


def validate_midi_mode(data: dict[str, Any]) -> list[str]:
    """Return hard export errors for a MIDI-owned beat."""
    if not data.get("plg_midi_melody"):
        return []
    errors: list[str] = []
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return ["MIDI mode: tracks missing"]

    for lane in tuple(tracks):
        if lane not in MIDI_ALLOWED_LANES and not role_lane_live(data, lane) and track_notes(data, lane):
            errors.append(f"MIDI mode: disabled lane has notes: {lane}")

    # 2026-07: kick/808_bass note generation was restored (see
    # beat_humanize._apply_808_bass_lane / bass808_gen.build_kick_808_pocket)
    # — both lanes are required non-empty again, same as every other lane.
    # Do NOT re-add an empty-is-fine exemption here: that was a temporary
    # allowance for the (now-reverted) generation-removed state, and letting
    # it linger would silently hide a real bug if the generator ever produced
    # zero notes for a span that should have had some.
    for lane in ("melody_lead", "kick", "808_bass", "hi_hats"):
        if not track_notes(data, lane):
            errors.append(f"MIDI mode: required lane is empty: {lane}")

    paths = data.get("plg_sound_paths")
    if not isinstance(paths, dict):
        paths = {}
    from sample_match import rejection_reason_for_pattern

    for lane in MIDI_REQUIRED_SAMPLE_LANES:
        raw = paths.get(lane)
        if not raw:
            from lane_fallback import lane_is_absent

            # An explicit "no sound for this lane" (selection found no usable file of the user's and we
            # put none of ours) is a valid project: the lane is left out of the .flp. A pattern with
            # neither a sample nor that record is still a bug.
            if not lane_is_absent(data, lane):
                errors.append(f"MIDI mode: no sample assigned to {lane}")
            continue
        # The strict gate minus what selection recorded in plg_sample_fallback.
        reason = rejection_reason_for_pattern(Path(str(raw)), lane, data)
        if reason:
            errors.append(f"MIDI mode: invalid {lane} sample ({reason}): {raw}")

    clips = data.get("plg_playlist_clips")
    if not isinstance(clips, list):
        errors.append("MIDI mode: playlist clips missing")
    else:
        row_by_lane: dict[str, int] = {}
        pattern_owners: dict[int, str] = {}
        source_windows: dict[str, list[tuple[float, float]]] = {}
        for clip in clips:
            if not isinstance(clip, dict):
                errors.append("MIDI mode: malformed playlist clip")
                continue
            lane = str(clip.get("track_key") or "")
            if lane in MIDI_DISABLED_LANES and not role_lane_live(data, lane):
                errors.append(f"MIDI mode: disabled playlist lane: {lane}")
            if not track_notes(data, lane):
                errors.append(f"MIDI mode: empty playlist lane: {lane}")
            try:
                row = int(clip["playlist_track"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"MIDI mode: invalid playlist row for {lane}")
                continue
            previous_row = row_by_lane.setdefault(lane, row)
            if previous_row != row:
                errors.append(f"MIDI mode: playlist lane changes row: {lane}")

            try:
                pattern_iid = int(clip["pattern_iid"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"MIDI mode: invalid pattern id for {lane}")
            else:
                owner = pattern_owners.setdefault(pattern_iid, lane)
                if owner != lane:
                    errors.append(
                        f"MIDI mode: pattern id shared across lanes: {pattern_iid}"
                    )

            try:
                start = float(clip["start_bar"])
                length = float(clip["length_bars"])
                source_end = float(clip.get("source_end_bar") or (start + length))
            except (KeyError, TypeError, ValueError):
                errors.append(f"MIDI mode: invalid playlist timing for {lane}")
            else:
                if start < 0 or length <= 0 or source_end <= start:
                    errors.append(f"MIDI mode: invalid playlist timing for {lane}")
                else:
                    source_windows.setdefault(lane, []).append((start * 4.0, source_end * 4.0))

        rows = sorted(set(row_by_lane.values()))
        if rows and rows != list(range(len(rows))):
            errors.append(f"MIDI mode: playlist rows are not compact: {rows}")

        # Multiple clips on one lane are the intended sectioned layout. The
        # hard invariant is that their source windows assign every note onset
        # exactly once; playback tails may overlap the next visual section.
        for lane, windows in source_windows.items():
            for note in track_notes(data, lane):
                time = float(note.get("time_step", 0.0))
                matches = sum(1 for start, end in windows if start <= time < end)
                if matches != 1:
                    errors.append(
                        f"MIDI mode: playlist coverage mismatch: {lane} at {time:g}"
                    )
                    break

        layout_audit = data.get("plg_playlist_layout_audit")
        if isinstance(layout_audit, dict) and layout_audit.get("passed") is not True:
            errors.append("MIDI mode: playlist layout audit failed")

    arrangement = data.get("plg_arrangement")
    names = {
        str(section.get("name"))
        for section in arrangement
        if isinstance(section, dict)
    } if isinstance(arrangement, list) else set()
    missing_sections = {"intro", "verse", "hook", "drop", "outro"} - names
    if missing_sections:
        errors.append(
            "MIDI mode: arrangement missing sections: " + ", ".join(sorted(missing_sections))
        )
    return errors


def enforce_midi_mode(data: dict[str, Any], *, strict: bool = True) -> dict[str, Any]:
    """Sanitize, validate, and attach an auditable final MIDI-mode report."""
    fixes = sanitize_midi_mode(data)
    errors = validate_midi_mode(data)
    report = {"ok": not errors, "fixes": fixes, "errors": errors}
    data["plg_midi_validation"] = report
    if strict and errors:
        raise ValueError("MIDI export blocked: " + "; ".join(errors))
    return report


def expected_note_counts(data: dict[str, Any]) -> dict[str, int]:
    return {key: len(track_notes(data, key)) for key in TRACK_KEYS}


def validate_pattern(data: dict[str, Any]) -> list[str]:
    """Return human-readable warnings for pattern JSON."""
    warnings: list[str] = []

    bpm = data.get("bpm")
    if bpm is None:
        warnings.append("missing bpm")
    elif not isinstance(bpm, (int, float)) or bpm <= 0:
        warnings.append(f"invalid bpm: {bpm!r}")

    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        warnings.append("tracks missing or not an object")
        return warnings

    total = sum(expected_note_counts(data).values())
    if total == 0:
        warnings.append("no notes in any PLG track")

    for key in TRACK_KEYS:
        for index, note in enumerate(track_notes(data, key)):
            if not isinstance(note, dict):
                warnings.append(f"{key}[{index}] is not an object")
                continue
            for field in ("note", "time_step", "length", "velocity"):
                if field not in note:
                    warnings.append(f"{key}[{index}] missing {field}")
            if "note" in note:
                try:
                    parse_note_name(str(note["note"]))
                except (ValueError, TypeError):
                    warnings.append(f"{key}[{index}] bad note: {note.get('note')!r}")
            if "velocity" in note:
                try:
                    velocity = int(note["velocity"])
                    if not 0 <= velocity <= 127:
                        warnings.append(f"{key}[{index}] velocity out of range: {velocity}")
                except (TypeError, ValueError):
                    warnings.append(f"{key}[{index}] bad velocity: {note.get('velocity')!r}")

        if key == "sub_808":
            for index, note in enumerate(track_notes(data, key)):
                if not isinstance(note, dict) or "note" not in note:
                    continue
                try:
                    midi = parse_note_name(str(note["note"]))
                    if midi < SUB_LO or midi > SUB_HI:
                        warnings.append(
                            f"sub_808[{index}] out of sub register ({note.get('note')!r})"
                        )
                except (ValueError, TypeError):
                    pass
                try:
                    length = float(note.get("length", 0))
                    if length < MIN_SUB_LENGTH:
                        warnings.append(
                            f"sub_808[{index}] note too short ({length} beats, min {MIN_SUB_LENGTH})"
                        )
                except (TypeError, ValueError):
                    pass

    return warnings


def validate_midi_file(path: Path, *, min_notes: int = 1) -> dict[str, Any]:
    """Inspect a .mid file; requires pretty_midi."""
    import pretty_midi

    midi_path = Path(path)
    if not midi_path.is_file():
        return {"path": str(midi_path), "ok": False, "error": "file not found"}

    try:
        score = pretty_midi.PrettyMIDI(str(midi_path))
    except Exception as exc:
        return {"path": str(midi_path), "ok": False, "error": str(exc)}

    note_count = sum(len(inst.notes) for inst in score.instruments)
    duration = score.get_end_time()
    estimated_bpm: float | None = None
    try:
        estimated_bpm = float(score.estimate_tempo())
    except Exception:
        pass

    return {
        "path": str(midi_path.resolve()),
        "instruments": len(score.instruments),
        "notes": note_count,
        "duration_sec": round(duration, 2),
        "estimated_bpm": round(estimated_bpm, 1) if estimated_bpm else None,
        "ok": note_count >= min_notes,
    }


def validate_export(
    pattern: dict[str, Any],
    midi_dir: Path = DEFAULT_MIDI_DIR,
    combined: Path = COMBINED_MIDI,
) -> dict[str, Any]:
    """Cross-check JSON note counts vs exported MIDI on disk."""
    json_warnings = validate_pattern(pattern)
    expected = expected_note_counts(pattern)
    expected_total = sum(expected.values())

    from midi_export import STEM_FILENAMES

    track_reports: dict[str, Any] = {}
    for key in TRACK_KEYS:
        track_path = midi_dir / STEM_FILENAMES.get(key, f"{key}.mid")
        if expected[key] > 0 and track_path.is_file():
            track_reports[key] = validate_midi_file(track_path, min_notes=1)

    # The MIDI writer legally removes same-pitch overlaps before serialization.
    # The per-stem files are therefore the authoritative serialized count; a
    # raw JSON count may be higher than the count that correctly reached disk.
    exported_total = sum(
        int(report.get("notes") or 0)
        for report in track_reports.values()
        if isinstance(report, dict)
    )
    combined_report: dict[str, Any] | None = None
    if combined.is_file():
        combined_report = validate_midi_file(
            combined,
            min_notes=max(1, exported_total or expected_total),
        )

    midi_ok = True
    if expected_total > 0:
        if combined_report is None or not combined_report.get("ok"):
            midi_ok = False
        for key, count in expected.items():
            if count > 0:
                report = track_reports.get(key)
                if report is None or not report.get("ok"):
                    midi_ok = False

    return {
        "json_warnings": json_warnings,
        "expected_notes": expected,
        "tracks": track_reports,
        "combined": combined_report,
        "ok": not json_warnings and midi_ok,
    }


def log_validation_report(report: dict[str, Any]) -> None:
    expected = report.get("expected_notes") or {}
    logging.info(
        "Pattern notes: hi_hats=%s sub_808=%s melody=%s",
        expected.get("hi_hats", 0),
        expected.get("sub_808", 0),
        expected.get("melody_lead", 0),
    )
    for warning in report.get("json_warnings") or []:
        logging.warning("Pattern validation: %s", warning)

    combined = report.get("combined")
    if combined:
        logging.info(
            "MIDI %s: instruments=%s notes=%s duration=%ss bpm~%s",
            Path(combined["path"]).name,
            combined.get("instruments"),
            combined.get("notes"),
            combined.get("duration_sec"),
            combined.get("estimated_bpm"),
        )
        if combined.get("error"):
            logging.warning("MIDI validation: %s", combined["error"])

    if report.get("ok"):
        logging.info("Beat validation OK")
    else:
        logging.warning("Beat validation reported issues (see log above)")


def validate_on_disk(
    pattern_path: Path = DEFAULT_PATTERN,
    midi_dir: Path = DEFAULT_MIDI_DIR,
    combined: Path = COMBINED_MIDI,
) -> dict[str, Any]:
    data = json.loads(pattern_path.read_text(encoding="utf-8"))
    return validate_export(data, midi_dir=midi_dir, combined=combined)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    if not DEFAULT_PATTERN.is_file():
        raise SystemExit(f"No pattern at {DEFAULT_PATTERN} — run CREATE BEAT first.")
    result = validate_on_disk()
    log_validation_report(result)
    raise SystemExit(0 if result.get("ok") else 1)
