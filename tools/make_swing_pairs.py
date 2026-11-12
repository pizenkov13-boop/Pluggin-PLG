"""Blind A/B pairs for ONE change: the hi-hat swing fix (apply_hi_hat_swing).

Same MIDI, same pinned seed, same library, same kit settings; the only thing that
differs between the two arms is which apply_hi_hat_swing runs:

  FIX   the working tree's version (off-beat eighth lands LATE, rolls untouched)
  OLD   the version in git HEAD (index-based, every swung hat rushed EARLY)

The swing shares the generation's rng with every pass after it, so merely swapping
the function re-draws kick, 808, snares and even the melody arrangement (measured:
all seven lanes differ). To keep this a HAT test, the OLD arm runs the fixed function
first on a spare copy of the rng state, leaves the real rng exactly where the fix
leaves it, and only then returns the old function's notes. Downstream passes then see
the same random stream in both arms; what still differs elsewhere is only what reads
the hat times, and the key lists it.

    python tools/make_swing_pairs.py [--library PLG_Sounds] [--out PLG_Beats_HatSwing_mykit]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import make_kit_pairs as mk  # noqa: E402  (hermetic setup, MIDI lookup, generation)

PAIRS = [("Tragic", 0), ("deyflo", 1), ("Sahara", 2), ("deyflo", 3)]


def _old_swing():
    source = subprocess.run(
        ["git", "show", "HEAD:beat_humanize.py"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "apply_hi_hat_swing")
    return ast.get_source_segment(source, node)


def _lane_hashes(pattern: dict) -> dict[str, str]:
    return {
        lane: hashlib.sha256(json.dumps(notes, sort_keys=True, default=str).encode()).hexdigest()[:10]
        for lane, notes in sorted((pattern.get("tracks") or {}).items())
    }


def _and_offsets(pattern: dict) -> dict:
    """How far the "&" (x.5) hats sit from the grid, in beats."""
    offs = []
    for n in (pattern.get("tracks") or {}).get("hi_hats") or []:
        t = float(n["time_step"])
        if abs((t % 1.0) - 0.5) < 0.2 and not n.get("hat_roll"):
            offs.append((t % 1.0) - 0.5)
    if not offs:
        return {}
    return {"n": len(offs), "mean": round(sum(offs) / len(offs), 4), "min": round(min(offs), 4), "max": round(max(offs), 4)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", type=Path, default=ROOT / "PLG_Sounds")
    parser.add_argument("--out", type=Path, default=ROOT / "PLG_Beats_HatSwing_mykit")
    parser.add_argument("--answer-seed", type=int, default=11)
    args = parser.parse_args()

    mk._hermetic()
    import beat_humanize

    fixed = beat_humanize.apply_hi_hat_swing
    namespace = beat_humanize.__dict__
    exec(compile(_old_swing(), "HEAD:beat_humanize.py", "exec"), namespace)   # redefines apply_hi_hat_swing
    old_raw = namespace["apply_hi_hat_swing"]
    namespace["apply_hi_hat_swing"] = fixed

    def old(notes, rng, *args, **kwargs):
        before = rng.getstate()
        fixed(notes, rng, *args, **kwargs)          # consume what the fix consumes ...
        after = rng.getstate()
        rng.setstate(before)
        result = old_raw(notes, rng, *args, **kwargs)  # ... but return the old behaviour
        rng.setstate(after)                         # ... and leave the shared stream as the fix does
        return result

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    work = args.out / "_work"
    work.mkdir()

    flags = [True, True, False, False]            # FIX is A in exactly half the pairs
    random.Random(args.answer_seed).shuffle(flags)
    key = []
    for number, ((label, offset), fix_is_a) in enumerate(zip(PAIRS, flags), 1):
        seed = mk.SEED_BASE + 100 * number
        tag = f"Pair{number}"
        midi = mk._find_midi(mk.MIDIS[label])
        print(f"[{tag}] {label} seed={seed}", flush=True)
        arms = {}
        for arm, fn in (("fix", fixed), ("old", old)):
            namespace["apply_hi_hat_swing"] = fn
            flp = work / f"{tag}_{arm}.flp"
            arms[arm] = (mk._generate(midi, args.library, "on", seed, flp), flp)
        namespace["apply_hi_hat_swing"] = fixed

        (fix_p, fix_flp), (old_p, old_flp) = arms["fix"], arms["old"]
        a_src, b_src = (fix_flp, old_flp) if fix_is_a else (old_flp, fix_flp)
        shutil.copyfile(a_src, args.out / f"{tag}_A.flp")
        shutil.copyfile(b_src, args.out / f"{tag}_B.flp")
        fh, oh = _lane_hashes(fix_p), _lane_hashes(old_p)
        key.append(
            {
                "pair": tag, "midi": label, "seed": seed,
                "A_is": "FIX (swing late, rolls untouched)" if fix_is_a else "OLD (index swing, rushes early)",
                "B_is": "OLD (index swing, rushes early)" if fix_is_a else "FIX (swing late, rolls untouched)",
                "lanes_whose_notes_differ": sorted(l for l in set(fh) | set(oh) if fh.get(l) != oh.get(l)),
                "same_kit": mk._kit(fix_p) == mk._kit(old_p),
                "kit": mk._kit(fix_p),
                "and_offset_beats_fix": _and_offsets(fix_p),
                "and_offset_beats_old": _and_offsets(old_p),
            }
        )

    shutil.rmtree(work, ignore_errors=True)
    (args.out / "_KEY.json").write_text(json.dumps(key, indent=2, ensure_ascii=False), encoding="utf-8")
    sheet = [
        "# Hi-hat swing — listening sheet", "",
        "Same notes' grid, same kit, same tempo; only the hi-hat groove differs.",
        "Listen to the hats against the kick/808: which one grooves better (bounce, not rushed, rolls clean)?",
        "Rate each 1-5.", "",
        "| Pair | better (A/B/=) | A 1-5 | B 1-5 | note |", "|---|---|---|---|---|",
    ] + [f"| {r['pair']} |  |  |  |  |" for r in key]
    (args.out / "_LISTENING_SHEET.md").write_text("\n".join(sheet) + "\n", encoding="utf-8")
    print(f"\n{len(key)} pairs written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
