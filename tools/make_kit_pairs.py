"""Build blind A/B listening pairs for the Kit Coherence Scorer (Phase 3).

For every (MIDI, seed) it generates the same beat three times with the same
pinned seed — ``PLG_KIT_COHERENCE`` = off, shadow, on — and then writes:

  PairNN_A.flp / PairNN_B.flp   one of them is the old kit, the other the
                                coherent one; which is which is random and is
                                NOT shown anywhere you will look
  _KEY.json                     the answer key (open it only after rating)
  _LISTENING_SHEET.md           blank rating sheet

Everything except the drum samples is identical inside a pair: the notes are
compared lane by lane and a pair whose notes differ is reported and dropped.
The shadow run must also equal the off run byte for byte (shadow may only
observe); a pair where it does not is reported.

The run is hermetic on purpose: kit history (the user's kit memory) is neither
read nor written, and the clock and uuid are frozen, so the same inputs always
give the same files. Nothing under %LOCALAPPDATA%\\PLG\\kit_history.json changes.

    python tools/make_kit_pairs.py --stage 0
    python tools/make_kit_pairs.py --stage 1
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import random
import shutil
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_LIBRARY = ROOT / "PLG_Beats_KitCoherence" / "_library_kitpack"
DEFAULT_OUT = ROOT / "PLG_Beats_KitCoherence"
MIDIS = {
    "Tragic": "Dragon_Orchestral_Loop_-_Tragic",
    "deyflo": "deyflo_kinect_dark_mainstream",
    "Sahara": "Dragon_Ethnic_Loop_-_Sahara",
    "BatonRouge": "Baton_Rouge_15",
    "Graveyard": "Graveyard_MIDI",
}
#: stage -> number of pairs; spread over whichever of MIDIS exist in PLG_MIDI
STAGE_PAIRS = {0: 3, 1: 20}
STAGE_LABELS = {0: ["Tragic"], 1: list(MIDIS)}
SEED_BASE = 80820


def _hermetic() -> None:
    os.environ["PLG_SKIP_BEAT_LIMIT"] = "1"
    time.time_ns = lambda: 1234567890123456789  # type: ignore[assignment]
    counter = itertools.count()

    class _U:
        def __init__(self, n: int) -> None:
            self.hex = format(n, "032x")

    uuid.uuid4 = lambda: _U(next(counter))  # type: ignore[assignment]
    import kit_variety

    kit_variety.save_kit_pick = lambda picks: None  # type: ignore[assignment]
    kit_variety._load_history = lambda: []  # type: ignore[assignment]


def _find_midi(fragment: str) -> Path | None:
    for path in sorted((ROOT / "PLG_MIDI").rglob("*.mid")):
        if fragment.lower() in path.stem.lower():
            return path
    return None


def _plan_stage(stage: int) -> list[tuple[str, Path]]:
    """(label, midi) per pair, round-robin over the MIDIs this machine has."""
    available = [(label, _find_midi(MIDIS[label])) for label in STAGE_LABELS[stage]]
    available = [(label, path) for label, path in available if path is not None]
    if not available:
        raise SystemExit("none of the listening MIDIs is in PLG_MIDI")
    missing = [label for label in STAGE_LABELS[stage] if label not in {a[0] for a in available}]
    if missing:
        print(f"note: not in PLG_MIDI, skipped: {', '.join(missing)}", flush=True)
    return [available[i % len(available)] for i in range(STAGE_PAIRS[stage])]


def _generate(midi: Path, library: Path, mode: str, seed: int, flp: Path) -> dict:
    os.environ["PLG_KIT_COHERENCE"] = mode
    os.environ["PLG_VARIETY_SEED"] = str(seed)
    random.seed(seed)
    from backend_core import run_pipeline
    from flp_writer import write_flp_session

    pattern = run_pipeline("", library, midi_path=str(midi))
    write_flp_session(pattern, flp)
    return pattern


def _notes(pattern: dict) -> str:
    """Every lane's notes, canonically — what must NOT differ inside a pair."""
    lanes = {k: v for k, v in sorted((pattern.get("tracks") or {}).items())}
    return hashlib.sha256(json.dumps(lanes, sort_keys=True, default=str).encode()).hexdigest()


def _kit(pattern: dict) -> dict:
    return {lane: Path(p).name for lane, p in sorted((pattern.get("plg_sound_paths") or {}).items())
            if lane in ("808_bass", "kick", "snare", "clap", "snare_layer", "hi_hats")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", type=int, choices=sorted(STAGE_PAIRS), required=True)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--answer-seed", type=int, default=7, help="decides which of A/B is the new kit")
    args = parser.parse_args()

    if not args.library.is_dir():
        raise SystemExit(f"library not found: {args.library}")
    _hermetic()
    out = args.out / f"stage{args.stage}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out / "_work"
    work.mkdir()
    chooser = random.Random(args.answer_seed)

    key: list[dict] = []
    problems: list[str] = []
    number = 0
    plan = _plan_stage(args.stage)
    # exactly half the pairs have the new kit as A, so "I usually prefer A" cannot masquerade as a result
    new_is_a_flags = [True] * (len(plan) // 2) + [False] * (len(plan) - len(plan) // 2)
    chooser.shuffle(new_is_a_flags)
    for (label, midi), new_is_a in zip(plan, new_is_a_flags):
        number += 1
        seed = SEED_BASE + 100 * number
        tag = f"Pair{number:02d}"
        print(f"[{tag}] {label} seed={seed}", flush=True)
        runs = {}
        for mode in ("off", "shadow", "on"):
            flp = work / f"{tag}_{mode}.flp"
            runs[mode] = (_generate(midi, args.library, mode, seed, flp), flp)

        off_p, off_flp = runs["off"]
        shadow_p, shadow_flp = runs["shadow"]
        on_p, on_flp = runs["on"]
        if off_flp.read_bytes() != shadow_flp.read_bytes():
            problems.append(f"{tag}: SHADOW changed the FLP (it must only observe)")
        if _notes(off_p) != _notes(on_p):
            problems.append(f"{tag}: notes differ between off and on — pair dropped")
            continue
        if _kit(off_p) == _kit(on_p):
            problems.append(f"{tag}: old and new kit are identical — nothing to compare, pair dropped")
            continue

        a_src, b_src = (on_flp, off_flp) if new_is_a else (off_flp, on_flp)
        shutil.copyfile(a_src, out / f"{tag}_A.flp")
        shutil.copyfile(b_src, out / f"{tag}_B.flp")
        card = shadow_p.get("plg_kit_coherence") or {}
        key.append(
            {
                "pair": tag, "midi": label, "seed": seed,
                "A_is": "NEW (coherent)" if new_is_a else "OLD",
                "B_is": "OLD" if new_is_a else "NEW (coherent)",
                "old_kit": _kit(off_p), "new_kit": _kit(on_p),
                "scorer_total_old": (card.get("legacy") or {}).get("total"),
                "scorer_total_new": (card.get("proposal") or {}).get("total"),
                "scorer_terms_old": (card.get("legacy") or {}).get("terms"),
                "scorer_terms_new": (card.get("proposal") or {}).get("terms"),
            }
        )

    shutil.rmtree(work, ignore_errors=True)
    (out / "_KEY.json").write_text(json.dumps(key, indent=2, ensure_ascii=False), encoding="utf-8")
    sheet = [
        "# Kit coherence — listening sheet",
        "",
        "Open Pair NN A and B in FL (same notes, same tempo — only the drum samples differ).",
        "For each pair: which one sounds more like ONE kit (cohesive, 'в одну линию')? "
        "Rate each 1-5 (1 = bad mismatched kit, 5 = clearly one kit).",
        "",
        "| Pair | MIDI is in the key | better (A/B/=) | A 1-5 | B 1-5 | note |",
        "|---|---|---|---|---|---|",
    ]
    for row in key:
        sheet.append(f"| {row['pair']} | {row['midi']} |  |  |  |  |")
    (out / "_LISTENING_SHEET.md").write_text("\n".join(sheet) + "\n", encoding="utf-8")

    print(f"\n{len(key)} pair(s) written to {out}")
    for line in problems:
        print("PROBLEM:", line)
    return 1 if problems and not key else 0


if __name__ == "__main__":
    raise SystemExit(main())
