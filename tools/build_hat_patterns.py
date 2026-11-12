#!/usr/bin/env python3
"""Build ``assets/hat_patterns.json`` from hat-pattern MIDI packs: ``build_hat_patterns.py PACK.zip [PACK.zip ...]``.

Only the RHYTHM of each 8-bar pattern is kept (the position of every hit in 1/48 of a beat); the pitch and the velocity of
the source are dropped, because PLG decides those itself (``hihat_rolls``). The source packs are third-party: the file
names are kept as pattern ids so every cell can be traced back, and ``assets/hat_patterns.json`` says where they came from.
"""

from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from pathlib import Path

import mido

UNIT = 48  # positions are stored in 1/48 of a beat: 8th = 24, 16th = 12, 32nd = 6, triplet 8th = 16
BARS = 8
BEATS = BARS * 4


def cell_from_midi(blob: bytes) -> list[int] | None:
    midi = mido.MidiFile(file=io.BytesIO(blob))
    ppq = midi.ticks_per_beat
    hits: set[int] = set()
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type == "note_on" and message.velocity > 0:
                unit = int(round(tick / ppq * UNIT))
                if 0 <= unit < BEATS * UNIT:
                    hits.add(unit)
    return sorted(hits) if hits else None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cells: list[dict] = []
    seen: set[tuple[int, ...]] = set()
    for archive in argv[1:]:
        with zipfile.ZipFile(archive) as zf:
            for name in sorted(zf.namelist()):
                if not name.lower().endswith((".mid", ".midi")):
                    continue
                hits = cell_from_midi(zf.read(name))
                if not hits or tuple(hits) in seen:
                    continue
                seen.add(tuple(hits))
                stem = re.sub(r"\s*@\S+", "", Path(name).stem).strip()
                cells.append({"id": stem, "bars": BARS, "hits": hits})
    out = Path(__file__).resolve().parent.parent / "assets" / "hat_patterns.json"
    payload = {
        "version": 1,
        "unit": UNIT,
        "note": "Rhythm only (position of each hit in 1/48 beat, 8 bars). Source: hat pattern packs supplied by the beta "
        "tester (HH PATTERNZ, !HH MIDI); pitch and velocity were dropped. Ids are the source file names.",
        "cells": cells,
    }
    out.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(cells)} cells -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
