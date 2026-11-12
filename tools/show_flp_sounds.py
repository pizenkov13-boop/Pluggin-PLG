#!/usr/bin/env python3
"""Show what an .flp really plays: per channel, the baked file it loads and YOUR source file.

A beat is not built from your files as they are: PLG bakes a warm / phase-flipped / PCM24 copy of each
sound into a cache and the channel loads that copy, whose name only hints at the original. This dev
tool (not part of the app) reads the .flp, reads the beat's own data (``plg_sample_origins``,
``plg_explicit_lanes``, ``plg_pinned_kit``, ``plg_sample_fallback``) and prints, for every channel:

    channel | file the channel loads | source file in your library | how it was chosen

Usage (PowerShell, from C:\\newPLUG):

    python tools\\show_flp_sounds.py                      # the newest .flp in the Projects folder
    python tools\\show_flp_sounds.py C:\\path\\beat.flp      # a given .flp
    python tools\\show_flp_sounds.py C:\\path\\folder        # the newest .flp of a folder
    python tools\\show_flp_sounds.py beat.flp --json data.json   # a given beat-data file

The beat data is ``<name>.json`` next to the .flp when it exists, else the app's ``output_pattern.json``
(the LAST generation). When the data does not belong to the .flp the tool says so instead of guessing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from flp_template import PATTERN_DISPLAY  # noqa: E402
from flp_writer import _CH_NAME, _CH_NEW, _CH_ROOT_NOTE, _CH_SAMPLE_PATH  # noqa: E402

LANE_OF_CHANNEL = {name: lane for lane, name in PATTERN_DISPLAY.items()}


def read_channels(flp: Path) -> list[dict]:
    """``[{"name", "sample", "root"}]`` in channel order, straight from the .flp event stream (``root`` is the Sampler's
    root note, None when the channel keeps FL's default C5 = 60)."""
    blob = flp.read_bytes()
    stream = memoryview(blob)[14 + 8 :]  # file header, then the FLdt chunk header
    channels: list[dict] = []
    off = 0
    while off < len(stream):
        eid = stream[off]
        off += 1
        if eid < 64:
            val = stream[off : off + 1]
            off += 1
        elif eid < 128:
            val = stream[off : off + 2]
            off += 2
        elif eid < 192:
            val = stream[off : off + 4]
            off += 4
        else:
            length, shift = 0, 0
            while True:
                byte = stream[off]
                off += 1
                length |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    break
                shift += 7
            val = stream[off : off + length]
            off += length
        if eid == _CH_NEW:
            channels.append({"name": None, "sample": None, "root": None})
        elif channels and eid == _CH_NAME and channels[-1]["name"] is None:
            channels[-1]["name"] = bytes(val).decode("utf-16-le", "ignore").rstrip("\x00")
        elif channels and eid == _CH_ROOT_NOTE:
            channels[-1]["root"] = int.from_bytes(bytes(val), "little")
        elif channels and eid == _CH_SAMPLE_PATH:
            channels[-1]["sample"] = bytes(val).decode("utf-16-le", "ignore").rstrip("\x00")
    return channels


def latest_flp(where: Path) -> Path:
    if where.is_file():
        return where
    found = sorted(where.rglob("*.flp"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise SystemExit(f"No .flp file in {where}")
    return found[-1]


def default_projects_dir() -> Path:
    from plg_paths import projects_dir

    return projects_dir()


def find_beat_data(flp: Path, explicit: str | None) -> tuple[dict, Path | None]:
    candidates = [Path(explicit)] if explicit else [flp.with_suffix(".json"), REPO / "output_pattern.json"]
    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8")), path
            except (OSError, ValueError):
                continue
    return {}, None


def norm(text: str) -> str:
    return re.sub(r"[\s_]+", " ", text.lower()).strip()


def stem_of(name: str) -> str:
    return re.sub(r"(_pcm24)?\.(wav|mp3|ogg|flac|aif|aiff)$", "", name, flags=re.I)


def strip_bake_marks(baked_name: str) -> str:
    """The original file name hidden in a baked name: ``kick__phase_inv_kick__warm_d1.40_hp0_<name>``."""
    stem = stem_of(baked_name)
    stem = re.sub(r"^(?:[a-z_0-9]+?__)+", "", stem)
    stem = re.sub(r"^(?:phase_inv_|reverse_)+", "", stem)
    stem = re.sub(r"^(?:[a-z_0-9]+?__)+", "", stem)
    return re.sub(r"^warm_d[\d.]+_hp\d+_", "", stem)


HASHED_808 = re.compile(r"^PLG_808_[0-9a-f]{6,}", re.I)  # the 808 copy FL loads is named by a hash, not by its source


def find_in_library(root: Path, wanted: str) -> list[Path]:
    """Files of the library whose name equals ``wanted`` (spaces and underscores count the same)."""
    if not root.is_dir():
        return []
    target = norm(stem_of(wanted))
    return [p for p in root.rglob("*") if p.is_file() and norm(stem_of(p.name)) == target]


def describe_choice(lane: str, data: dict, source_name: str) -> str:
    if lane in (data.get("plg_explicit_lanes") or []):
        return "YOUR PICK (sound browser)"
    pinned = ((data.get("plg_pinned_kit") or {}).get("pinned") or {}).get(lane)
    if pinned and source_name and norm(pinned) == norm(source_name):
        return "random pin (dice)"
    record = (data.get("plg_sample_fallback") or {}).get(lane)
    if isinstance(record, dict) and record.get("tier") == 1:
        return "closest file of yours (soft pass)"
    return "automatic"


def melody_source(data: dict, loaded: str | None = None) -> str:
    """Who put the sound on the melody channel: ``our one-shot`` (PLG's own, ``plg_melody_oneshot``), ``explicit user pin``
    (a file the user pinned by hand on the melody cell) or ``empty`` (no sound: the notes are the user's). ``loaded`` is the
    file the .flp's melody channel loads, when known: a sound that is none of those (the one-shot flag was off) is named."""
    record = data.get("plg_melody_oneshot")
    if isinstance(record, dict) and record.get("prepared"):
        return f"our one-shot ({record.get('type')}/{record.get('file')})"
    if "melody_lead" in (data.get("plg_explicit_lanes") or []):
        pinned = ((data.get("plg_pinned_kit") or {}).get("pinned") or {}).get("melody_lead") or loaded or "?"  # the file as the user pinned it
        return f"explicit user pin ({pinned})"
    if loaded:
        return f"another sound ({loaded}; picked automatically, the one-shot flag was off)"
    return "empty (no sound: the notes are yours)"


def melody_report(data: dict, loaded: str | None = None) -> list[str]:
    """Who sounds on the melody channel; what the melody is like (from its notes), the group the one-shot scheme puts it in,
    and how its one-shot was chosen. The scheme is an ESTIMATE (``melody_oneshots``): this is how Pavel checks it by ear."""
    from melody_oneshots import GROUP_TYPES, choose_group, melody_features

    head = ["", f"  melody: {melody_source(data, loaded)}"]
    features = melody_features(data) if data else None
    if features is None:
        return head + ["  melody: no melody notes in the beat data"]
    group = choose_group(features)
    lines = head + [
        "  melody (read from its notes):",
        f"    mean note length {features['mean_len_beats']:.2f} beats | 3+ notes sounding {features['polyphony'] * 100:.0f}% of the time | "
        f"median pitch {features['median_pitch']:.0f} (FL name, C5 = 60) | {features['notes_per_bar']:.1f} notes per bar",
        f"    one-shot group by the scheme (an estimate): {group} -> {', '.join(GROUP_TYPES[group])}",
    ]
    record = data.get("plg_melody_oneshot")
    if isinstance(record, dict):
        used = record.get("group_used")
        note = "" if used == record.get("group") else f"  (group {record.get('group')} had no file, used {used})"
        lines.append(
            f"    chosen: seed {record.get('seed')} / type {record.get('type')} / file {record.get('file')}{note}"
        )
        offset = int(record.get("root_offset") or 0)
        lines.append(
            f"    root offset: {offset:+d} semitones -> channel root {60 + offset}"
            + (" (the file sounds that much below MIDI 60; the audio is unchanged)" if offset else " (the file is on MIDI 60)")
        )
    else:
        lines.append("    chosen: no PLG melody one-shot in this beat (PLG_MELODY_ONESHOTS is off, or you pinned a sound on the melody)")
    return lines


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Show the source sounds behind each channel of an .flp")
    parser.add_argument("path", nargs="?", help=".flp file or a folder (default: the Projects folder)")
    parser.add_argument("--json", help="beat-data file (default: <flp>.json, else output_pattern.json)")
    args = parser.parse_args()

    flp = latest_flp(Path(args.path) if args.path else default_projects_dir())
    channels = read_channels(flp)
    data, data_path = find_beat_data(flp, args.json)
    origins = data.get("plg_sample_origins") or {}
    sound_paths = data.get("plg_sound_paths") or {}
    root = Path(data.get("sample_library") or REPO / "PLG_Sounds")

    print(f"{flp}")
    print(f"  modified {__import__('time').strftime('%Y-%m-%d %H:%M:%S', __import__('time').localtime(flp.stat().st_mtime))}, "
          f"{len(channels)} channels")

    # does the beat data belong to this .flp?  Every baked file must hint at the file the data recorded.
    mismatches = []
    for channel in channels:
        lane = LANE_OF_CHANNEL.get(channel["name"] or "")
        recorded = sound_paths.get(lane or "")
        if channel["sample"] and recorded and not HASHED_808.match(Path(channel["sample"]).name):
            a, b = norm(stem_of(Path(channel["sample"]).name)), norm(stem_of(Path(recorded).name))
            if a not in b and b not in a:
                mismatches.append(lane)
    if data_path is None:
        print("  beat data: NOT FOUND - sources are guessed from the baked names only")
    elif mismatches or not any(
        channel["sample"] and not HASHED_808.match(Path(channel["sample"]).name) and sound_paths.get(LANE_OF_CHANNEL.get(channel["name"] or "", ""))
        for channel in channels
    ):
        why = f"({', '.join(mismatches)})" if mismatches else "(nothing in it can be checked against the .flp)"
        print(f"  beat data: {data_path}  - does NOT match this .flp {why}; sources below are guessed")
        origins = {}
    else:
        print(f"  beat data: {data_path}  (matches this .flp)")

    rows = []
    for channel in channels:
        name = channel["name"] or "?"
        lane = LANE_OF_CHANNEL.get(name, "")
        baked = Path(channel["sample"]).name if channel["sample"] else ""
        if not baked:
            rows.append((name, "(no sound - the notes are yours)", "", "", None))
            continue
        origin = origins.get(lane) or {}
        source = origin.get("path") or ""
        how = ""
        oneshot = data.get("plg_melody_oneshot") if lane == "melody_lead" else None
        if isinstance(oneshot, dict) and oneshot.get("path") and not mismatches:
            source = oneshot["path"]
            how = (
                f"PLG melody one-shot (seed {oneshot.get('seed')}, type {oneshot.get('type')}, "
                f"file {oneshot.get('file')}; prepared copy: DC out, fades, -3 dB)"
            )
        elif source:
            how = describe_choice(lane, data, origin.get("file", ""))
        elif origin.get("file"):
            hits = find_in_library(root, origin["file"])
            source = str(hits[0]) if len(hits) == 1 else (f"{origin['file']}  (found {len(hits)} times in the library)" if hits else origin["file"])
            how = describe_choice(lane, data, origin["file"])
        elif HASHED_808.match(baked):
            source = "(the 808 copy is named by a hash; the beat data above is the only record of its source)"
            how = "?"
        else:
            guess = strip_bake_marks(baked)
            hits = find_in_library(root, guess)
            source = (str(hits[0]) if len(hits) == 1 else guess) + "  (guessed from the baked name)"
            how = describe_choice(lane, data, guess)
            if how == "automatic":
                how = "automatic (not recorded in the beat data)"
        rows.append((name, baked, source, how, channel["root"]))

    width = max((len(r[0]) for r in rows), default=8)
    for name, baked, source, how, root in rows:
        print()
        print(f"  {name:<{width}}  loads : {baked}")
        if root is not None:
            print(f"  {'':<{width}}  root  : {root} (the Sampler root note; FL's default is 60 = C5)")
        if source:
            print(f"  {'':<{width}}  source: {source}")
        if how:
            print(f"  {'':<{width}}  chosen: {how}")

    loaded = next((Path(c["sample"]).name for c in channels if c["name"] == PATTERN_DISPLAY.get("melody_lead") and c["sample"]), None)
    for line in melody_report(data, loaded):
        print(line)

    absent = [PATTERN_DISPLAY.get(lane, lane) for lane, row in origins.items() if row.get("origin") == "absent"]
    if absent:
        print(f"\n  no channel in the .flp (no file of yours for it): {', '.join(absent)}")
    explicit = data.get("plg_explicit_lanes") or []
    if explicit and not mismatches:
        names = ", ".join(PATTERN_DISPLAY.get(lane, lane) for lane in explicit)
        print(f"  your picks from the sound browser: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
