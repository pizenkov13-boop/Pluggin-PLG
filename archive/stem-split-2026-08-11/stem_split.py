"""AI stem splitter — backend stub for PLG.

Split a reference track (MP3/WAV) into 4 stems (vocals, drums, bass, other) so a
producer can pull a drum loop or bassline into FL. Runs locally with Demucs — no
cloud, no upload.

This is a backend API only; the PLG app will add a drop-zone tab later. Demucs +
torch are heavy and optional, so they are NOT in the base requirements. Install:

    pip install -U demucs

(That pulls in a CPU build of torch automatically. ~2 GB.)

Usage:
    from stem_split import split_stems
    stems = split_stems(Path("ref.mp3"), Path("output_stems"))
    # -> {"vocals": Path(...), "drums": ..., "bass": ..., "other": ...}
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

STEM_NAMES = ("vocals", "drums", "bass", "other")
DEFAULT_MODEL = "htdemucs"

ProgressCb = Callable[[float, str], None]


class StemSplitError(RuntimeError):
    """Raised when stems cannot be produced (missing deps, bad input, etc.)."""


def stems_available() -> bool:
    """True if Demucs (and torch) are importable — cheap capability check."""
    try:
        import demucs.api  # noqa: F401
    except Exception:
        return False
    return True


def _emit(progress_cb: ProgressCb | None, fraction: float, message: str) -> None:
    if progress_cb is not None:
        progress_cb(max(0.0, min(1.0, fraction)), message)


def split_stems(
    input_path: Path,
    output_dir: Path,
    *,
    model: str = DEFAULT_MODEL,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Path]:
    """Separate ``input_path`` into 4 stems written under ``output_dir``.

    Returns a mapping ``{"vocals": Path, "drums": Path, "bass": Path, "other": Path}``.
    Raises :class:`StemSplitError` if Demucs/torch are missing or input is bad.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    if not input_path.is_file():
        raise StemSplitError(f"Input file not found: {input_path}")

    try:
        from demucs.api import Separator, save_audio
    except Exception as exc:  # ImportError or torch load failure
        raise StemSplitError(
            "Stem splitting needs Demucs.\n\nInstall it once with:\n"
            "    pip install -U demucs\n\n"
            f"(import failed: {exc})"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    _emit(progress_cb, 0.05, "Loading model…")

    def _demucs_progress(data: dict) -> None:
        # Demucs reports per-segment progress as a dict; map it onto 0.1..0.9.
        try:
            state = data.get("state")
            if state == "start":
                _emit(progress_cb, 0.1, "Separating…")
            elif state == "end":
                done = float(data.get("segment_offset", 0)) + float(data.get("segment", 0))
                total = float(data.get("audio_length", 0)) or 1.0
                _emit(progress_cb, 0.1 + 0.8 * (done / total), "Separating…")
        except Exception:
            pass

    try:
        separator = Separator(model=model, callback=_demucs_progress)
    except TypeError:
        # Older demucs without callback kwarg.
        separator = Separator(model=model)

    try:
        _origin, sources = separator.separate_audio_file(input_path)
    except Exception as exc:
        raise StemSplitError(f"Demucs failed on {input_path.name}: {exc}") from exc

    _emit(progress_cb, 0.9, "Writing stems…")
    samplerate = getattr(separator, "samplerate", 44100)
    written: dict[str, Path] = {}
    for name in STEM_NAMES:
        if name not in sources:
            continue
        out_path = output_dir / f"{name}.wav"
        save_audio(sources[name], str(out_path), samplerate=samplerate)
        written[name] = out_path

    if not written:
        raise StemSplitError("Demucs produced no stems (unexpected model output).")

    _emit(progress_cb, 1.0, "Done")
    return written


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python stem_split.py <audio file> [output_dir]")
        raise SystemExit(2)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output_stems")
    try:
        result = split_stems(src, dst, progress_cb=lambda f, m: print(f"{f*100:5.1f}% {m}"))
    except StemSplitError as err:
        print(err)
        raise SystemExit(1)
    for stem, path in result.items():
        print(f"{stem}: {path}")
