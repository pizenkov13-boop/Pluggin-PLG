"""Deterministic sidechain ("Kickstart") pump curve — the math, plugin-free.

A sidechain pump is a *tempo-synced* volume dip on every kick, not a per-note
effect — so it cannot be baked into a one-shot sample (it would desync as the
Sampler repitches). It belongs on a volume automation. This module is the
DeepSeek "geometric grid" half: it computes the exact pump envelope (sharp duck
on each kick, smooth bezier recovery to unity by mid-beat) as automation points.

Those points are stored on the pattern (plg_producer_meta.sidechain) so they feed
the build guide as an exact, ready-to-draw curve. Authoring them into a *native
FL volume automation clip* is a separate binary step that must be verified in FL
— this module deliberately stops at the math.
"""

from __future__ import annotations

from typing import Any

# duck depth 0..1 (0.85 = dip to 15% on the kick); recovery in beats.
_DEPTH_MIN, _DEPTH_MAX = 0.0, 0.98
_DEFAULT_DEPTH = 0.82
_DEFAULT_RECOVER_BEATS = 0.6
_DOWN_TENSION = -0.55  # fast drop into the duck
_UP_TENSION = 0.45     # smooth bezier climb back to unity


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _kick_beats(kick_notes: list[dict[str, Any]] | None, total_beats: float) -> list[float]:
    beats: list[float] = []
    for note in kick_notes or []:
        try:
            beats.append(float(note["time_step"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not beats:
        # four-on-the-floor fallback (pop / house pumping) — every beat.
        beats = [float(b) for b in range(int(round(total_beats)))]
    return sorted({round(b, 4) for b in beats if 0.0 <= b < total_beats})


def compute_sidechain_curve(
    bpm: float,
    kick_notes: list[dict[str, Any]] | None,
    total_beats: float,
    *,
    depth: float = _DEFAULT_DEPTH,
    recover_beats: float = _DEFAULT_RECOVER_BEATS,
    target: str = "melody_lead",
) -> dict[str, Any]:
    """Return a sidechain pump definition with automation points (value 0..1)."""
    depth = _clamp(float(depth), _DEPTH_MIN, _DEPTH_MAX)
    floor = 1.0 - depth
    recover_beats = max(0.05, float(recover_beats))
    kicks = _kick_beats(kick_notes, total_beats)

    points: list[dict[str, float]] = [{"pos": 0.0, "value": 1.0, "tension": 0.0}]
    for t in kicks:
        # sharp duck exactly on the kick…
        points.append({"pos": round(t, 4), "value": round(floor, 4), "tension": _DOWN_TENSION})
        # …then bezier climb back to unity, but never past the next kick.
        nxt = next((k for k in kicks if k > t), total_beats)
        up = min(t + recover_beats, max(t + 0.05, nxt - 0.001))
        points.append({"pos": round(up, 4), "value": 1.0, "tension": _UP_TENSION})

    # collapse points sharing a position (last wins) and sort
    merged: dict[float, dict[str, float]] = {}
    for p in points:
        merged[p["pos"]] = p
    ordered = [merged[k] for k in sorted(merged)]

    return {
        "target": target,
        "depth": round(depth, 4),
        "floor": round(floor, 4),
        "recover_beats": round(recover_beats, 4),
        "shape": "bezier",
        "kicks": len(kicks),
        "points": ordered,
    }


def attach_sidechain_curve(data: dict[str, Any], *, depth: float | None = None) -> dict[str, Any] | None:
    """Compute the pump from the pattern's kick + bpm and store it in-place."""
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return None
    bpm = float(data.get("bpm", 140) or 140)
    kick = tracks.get("kick") or []
    # pattern length in beats: prefer explicit, else max note end, else 16
    total = data.get("pattern_beats")
    if not total:
        ends = [
            float(n.get("time_step", 0)) + float(n.get("length", 0))
            for lane in tracks.values()
            if isinstance(lane, list)
            for n in lane
            if isinstance(n, dict)
        ]
        total = max(ends) if ends else 16.0
    eff_depth = _DEFAULT_DEPTH if depth is None else depth
    curve = compute_sidechain_curve(bpm, kick, float(total), depth=eff_depth)

    meta = data.setdefault("plg_producer_meta", {})
    if not isinstance(meta, dict):
        meta = {}
        data["plg_producer_meta"] = meta
    meta["sidechain_curve"] = curve  # distinct key — coexists with legacy hint
    return curve


if __name__ == "__main__":
    demo = {
        "bpm": 128,
        "tracks": {"kick": [{"time_step": float(b), "note": "C1", "length": 0.5, "velocity": 120} for b in range(8)]},
    }
    c = attach_sidechain_curve(demo, depth=0.85)
    print("floor:", c["floor"], "kicks:", c["kicks"], "points:", len(c["points"]))
    for p in c["points"][:6]:
        print(" ", p)
