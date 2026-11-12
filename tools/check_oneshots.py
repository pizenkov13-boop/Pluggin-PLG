#!/usr/bin/env python3
"""Check the melody one-shots in assets/melody_oneshots: pitch, length, peak, DC.

For every ``.wav`` under ``assets/melody_oneshots`` (``_rejected/`` included, marked) it prints a table:

    file | seconds | peak dB | DC dBFS | f0 Hz | cents from MIDI 60 | status

The files were played on MIDI 60 (FL's "C5", 261.63 Hz, the Sampler's default root note), so f0 should sit at
261.63 Hz: ``status`` is OK within +-50 cents, ``OCT`` when the pitch is right but an octave (or more) away, ``OFF``
otherwise. Nothing is corrected in the wavs: this shows, and ``--write-offsets`` records for each file the channel root offset (whole
semitones, only for a deviation of 6 semitones or more) that ``flp_writer`` writes as the channel's root note.

The pitch is NOT an FFT peak (the strongest partial is often not the fundamental). It is a YIN-style detector:
the difference function of a few loud frames by FFT, cumulative mean normalisation, the first dip below a
threshold (else the deepest), then the median over the frames. ``f0 conf`` is the YIN dip depth of the
median frame (lower is steadier; > 0.3 means the pitch is a guess).

Usage (PowerShell, from C:\\newPLUG):

    python tools\\check_oneshots.py                 # the shipped folder
    python tools\\check_oneshots.py C:\\some\\folder   # another folder
    python tools\\check_oneshots.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OFFSETS_FILE = "root_offsets.json"  # next to the wavs: {"offsets": {"<type>/<file>.wav": semitones}}, read by melody_oneshots
ROOT_MIDI = 60  # FL "C5"
ROOT_HZ = 440.0 * 2.0 ** ((ROOT_MIDI - 69) / 12.0)  # 261.626
TOLERANCE_CENTS = 50.0
FRAME = 4096  # 93 ms at 44.1 kHz: two periods of 50 Hz fit
HOP = 2048
OFFSET_MIN_CENTS = 600.0  # a pitch off the root by less than 6 semitones is a tuning matter, not a register one: offset 0
MIN_HZ, MAX_HZ = 50.0, 1500.0
YIN_THRESHOLD = 0.15
LOUD_FRAME_FRACTION = 0.5  # frames within this share of the loudest frame's RMS vote
MAX_VOTING_FRAMES = 24


def _yin_frame(frame: np.ndarray, rate: int) -> tuple[float, float] | None:
    """``(f0 Hz, dip depth)`` of one mono frame, or None when it is silent."""
    n = len(frame)
    tau_min = max(2, int(rate / MAX_HZ))
    tau_max = min(n // 2 - 1, int(rate / MIN_HZ))
    if tau_max <= tau_min + 2 or not np.any(frame):
        return None
    size = 1 << int(math.ceil(math.log2(2 * n)))
    spec = np.fft.rfft(frame, size)
    acf = np.fft.irfft(spec * np.conj(spec), size)[: tau_max + 1]  # r(tau) = sum x[j] x[j+tau]
    squares = np.concatenate(([0.0], np.cumsum(frame * frame)))
    taus = np.arange(tau_max + 1)
    # d(tau) = sum_j (x[j] - x[j+tau])^2 over j < n - tau, from the energy of the two windows and the correlation
    head = squares[n - taus]
    tail = squares[n] - squares[taus]
    diff = head + tail - 2.0 * acf
    diff[0] = 0.0
    running = np.cumsum(diff[1:])
    norm = np.ones_like(diff)
    norm[1:] = diff[1:] * taus[1:] / np.where(running == 0.0, 1.0, running)
    window = norm[tau_min : tau_max + 1]
    below = np.nonzero(window < YIN_THRESHOLD)[0]
    if below.size:
        idx = int(below[0])
        while idx + 1 < window.size and window[idx + 1] < window[idx]:
            idx += 1  # walk to the bottom of the first dip
    else:
        idx = int(np.argmin(window))
    tau = tau_min + idx
    if 1 <= idx < window.size - 1:  # parabolic interpolation around the minimum
        a, b, c = window[idx - 1], window[idx], window[idx + 1]
        denom = a - 2.0 * b + c
        if denom > 1e-12:
            tau = tau + 0.5 * (a - c) / denom
    return rate / tau, float(window[idx])


def detect_pitch(mono: np.ndarray, rate: int) -> tuple[float | None, float | None]:
    """``(f0 Hz, confidence)``: the median YIN pitch of the loudest frames; ``(None, None)`` for silence."""
    mono = np.asarray(mono, dtype=np.float64)
    mono = mono - mono.mean()
    if len(mono) < FRAME:
        mono = np.pad(mono, (0, FRAME - len(mono)))
    starts = range(0, len(mono) - FRAME + 1, HOP)
    frames = [mono[s : s + FRAME] for s in starts]
    levels = np.array([float(np.sqrt(np.mean(f * f))) for f in frames])
    if levels.max() <= 1e-6:
        return None, None
    order = [i for i in np.argsort(-levels) if levels[i] >= LOUD_FRAME_FRACTION * levels.max()][:MAX_VOTING_FRAMES]
    votes = [r for i in sorted(order) if (r := _yin_frame(frames[i] * np.hanning(FRAME), rate)) is not None]
    if not votes:
        return None, None
    votes.sort(key=lambda v: v[0])
    f0, depth = votes[len(votes) // 2]
    return float(f0), float(depth)


def cents_from_root(f0: float) -> float:
    return 1200.0 * math.log2(f0 / ROOT_HZ)


def fold_cents(cents: float) -> float:
    """The same pitch class, nearest octave: -600..+600."""
    return (cents + 600.0) % 1200.0 - 600.0


def root_offset_semitones(cents: float | None) -> int:
    """The channel root offset for a file whose pitch is ``cents`` from MIDI 60: whole semitones, and only when the
    deviation is clearly a register one (>= 6 semitones). An octave below (-1200 cents) is -12: the channel root
    becomes 60 - 12 = 48, so MIDI 60 plays the sample an octave up, at the pitch the note says."""
    if cents is None or abs(cents) < OFFSET_MIN_CENTS:
        return 0
    return int(round(cents / 100.0))


def measure(path: Path) -> dict:
    from wav_decode import read_wav

    audio = read_wav(path)
    samples = audio.samples if audio.samples.ndim > 1 else audio.samples[:, None]
    mono = samples.mean(axis=1)
    peak = float(np.abs(samples).max())
    dc = float(abs(mono.mean()))
    f0, conf = detect_pitch(mono, audio.rate)
    row = {
        "file": str(path),
        "seconds": round(len(mono) / audio.rate, 3),
        "rate": audio.rate,
        "channels": audio.channels,
        "peak_db": round(20 * math.log10(peak), 2) if peak > 0 else None,
        "dc": round(dc, 6),
        "dc_db": round(20 * math.log10(dc), 1) if dc > 0 else None,
        "f0_hz": round(f0, 2) if f0 else None,
        "f0_conf": round(conf, 3) if conf is not None else None,
    }
    if f0:
        cents = cents_from_root(f0)
        row["cents"] = round(cents, 1)
        row["cents_pitch_class"] = round(fold_cents(cents), 1)
        row["root_offset"] = root_offset_semitones(cents)
        row["status"] = (
            "OK" if abs(cents) <= TOLERANCE_CENTS else "OCT" if abs(fold_cents(cents)) <= TOLERANCE_CENTS else "OFF"
        )
    else:
        row["cents"] = row["cents_pitch_class"] = None
        row["root_offset"] = 0
        row["status"] = "SILENT"
    return row


def collect(root: Path) -> list[dict]:
    rows = []
    for wav in sorted(root.rglob("*.wav")):
        row = measure(wav)
        row["name"] = str(wav.relative_to(root)).replace("\\", "/")
        row["rejected"] = any(part.startswith("_rejected") for part in wav.relative_to(root).parts)
        rows.append(row)
    return rows


def format_table(rows: list[dict]) -> str:
    head = f"{'file':<31} {'sec':>5} {'peak dB':>8} {'DC dBFS':>8} {'f0 Hz':>8} {'conf':>5} {'cents':>8} {'pc cents':>9} {'root':>5}  status"
    lines = [head, "-" * len(head)]
    for r in rows:
        mark = " (rejected)" if r.get("rejected") else ""
        f = lambda v, spec: format(v, spec) if v is not None else "-"  # noqa: E731
        lines.append(
            f"{r['name']:<31} {r['seconds']:>5.2f} {f(r['peak_db'], '8.2f'):>8} {f(r['dc_db'], '8.1f'):>8} "
            f"{f(r['f0_hz'], '8.1f'):>8} {f(r['f0_conf'], '5.2f'):>5} {f(r['cents'], '+8.0f'):>8} "
            f"{f(r['cents_pitch_class'], '+9.0f'):>9} {r.get('root_offset', 0):>+5d}  {r['status']}{mark}"
        )
    shipped = [r for r in rows if not r.get("rejected")]
    counts = {s: sum(1 for r in shipped if r["status"] == s) for s in ("OK", "OCT", "OFF", "SILENT")}
    lines.append("")
    lines.append(
        f"{len(shipped)} files (rejected not counted): OK {counts['OK']}, OCT {counts['OCT']} (pitch class right, octave away), "
        f"OFF {counts['OFF']}, SILENT {counts['SILENT']}   |  target MIDI {ROOT_MIDI} = {ROOT_HZ:.2f} Hz, tolerance +-{TOLERANCE_CENTS:.0f} cents"
    )
    return "\n".join(lines)


def write_offsets(root: Path, rows: list[dict]) -> Path:
    """``root_offsets.json`` for the shipped files (not ``_rejected``): the measured offset of each, 0 included."""
    offsets = {r["name"]: r["root_offset"] for r in rows if not r.get("rejected")}
    path = root / OFFSETS_FILE
    path.write_text(
        json.dumps(
            {
                "about": "Channel root offset in semitones per file, from tools/check_oneshots.py (YIN pitch vs MIDI 60, whole "
                "semitones, only when the deviation is >= 6 semitones). The wav is not changed; the .flp channel root becomes 60 + offset.",
                "root_midi": ROOT_MIDI,
                "offsets": dict(sorted(offsets.items())),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Pitch, length, peak and DC of the melody one-shots")
    parser.add_argument("folder", nargs="?", help="default: assets/melody_oneshots")
    parser.add_argument("--json", help="also write the rows to this file")
    parser.add_argument("--write-offsets", action="store_true", help=f"write {OFFSETS_FILE} next to the wavs (the shipped files only)")
    args = parser.parse_args()
    root = Path(args.folder) if args.folder else REPO / "assets" / "melody_oneshots"
    rows = collect(root)
    if not rows:
        print(f"No .wav in {root}")
        return 1
    print(format_table(rows))
    if args.write_offsets:
        print(f"wrote {write_offsets(root, rows)}")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
