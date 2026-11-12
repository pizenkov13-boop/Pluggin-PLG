"""Make WAV samples safe for FL Studio's sampler.

FL Studio (notably trial / older builds) loads 32-bit IEEE-float WAVs — it draws
the waveform fine — but the sampler plays them as *silence*. Library one-shots in
this project are a mix of 24-bit PCM (kick / 808: play fine) and 32-bit float
(snare / clap / hats / melody: silent). We transcode any non-PCM (or float)
WAV to plain 24-bit PCM so every channel actually sounds in FL.

Pure ``struct`` parsing + numpy for the sample math (numpy is already a project
dependency via the preview engine); imported lazily so importing this module
never hard-fails in a numpy-less context.
"""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path

# WAVE format tags
_WAVE_FORMAT_PCM = 1
_WAVE_FORMAT_FLOAT = 3
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


def _parse_wav(raw: bytes):
    """Return (fmt_tag, channels, sample_rate, bits, data_bytes) or None."""
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None
    off = 12
    fmt = None
    data = None
    while off + 8 <= len(raw):
        cid = raw[off : off + 4]
        csz = struct.unpack_from("<I", raw, off + 4)[0]
        chunk = raw[off + 8 : off + 8 + csz]
        if cid == b"fmt ":
            tag, ch, sr, _, _, bits = struct.unpack_from("<HHIIHH", chunk, 0)
            if tag == _WAVE_FORMAT_EXTENSIBLE and len(chunk) >= 26:
                tag = struct.unpack_from("<H", chunk, 24)[0]
            fmt = (tag, ch, sr, bits)
        elif cid == b"data":
            data = chunk
        off += 8 + csz + (csz & 1)
    if fmt is None or data is None:
        return None
    tag, ch, sr, bits = fmt
    return tag, ch, sr, bits, data


def inspect_wav(path: Path):
    try:
        return _parse_wav(path.read_bytes())
    except OSError:
        return None


def needs_fl_conversion(path: Path) -> bool:
    """True when FL's sampler would choke on this WAV (non-PCM / float)."""
    info = inspect_wav(path)
    if info is None:
        return False  # not a WAV we recognise; leave it alone
    tag, _ch, _sr, bits, _data = info
    if tag != _WAVE_FORMAT_PCM:
        return True
    if bits not in (16, 24):  # 8 / 32-bit-int PCM are risky too
        return True
    return False


def _decode_to_float(tag: int, bits: int, data: bytes):
    import numpy as np

    # Only PCM and IEEE float are stored as plain samples. Reading any other tag (A-law, ADPCM,
    # Ogg-in-WAV...) by its bit depth turns the file into noise: say no, the callers decide.
    if tag not in (_WAVE_FORMAT_PCM, _WAVE_FORMAT_FLOAT):
        raise ValueError(f"unsupported WAV format tag {tag}")

    if tag == _WAVE_FORMAT_FLOAT:
        if bits == 64:
            return np.frombuffer(data, dtype="<f8").astype(np.float64)
        return np.frombuffer(data, dtype="<f4").astype(np.float64)
    if bits == 16:
        return np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    if bits == 24:
        b = np.frombuffer(data, dtype=np.uint8)
        n = b.size - (b.size % 3)
        b = b[:n].reshape(-1, 3)
        a = (
            b[:, 0].astype(np.int32)
            | (b[:, 1].astype(np.int32) << 8)
            | (b[:, 2].astype(np.int32) << 16)
        )
        a = np.where(a & 0x800000, a - 0x1000000, a)
        return a.astype(np.float64) / 8388608.0
    if bits == 32:
        return np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648.0
    if bits == 8:
        return (np.frombuffer(data, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    raise ValueError(f"unsupported bits={bits}")


# 24-bit PCM cannot represent a value above full scale, so a float source that
# peaks over 1.0 MUST be brought down by something. Hard-clipping it (what this
# did until 2026-08-14) squares off the waveform and bakes broadband distortion
# into the one and only copy FL ever plays — worst on the two lanes that reach
# this function un-attenuated (``808_bass`` and ``hi_hats`` are the only lanes
# sample_saturate deliberately skips, so nothing upstream has applied its 0.97
# ceiling to them). Measured on this project's own library: "XPOSED 808" peaks
# at 1.261, and clipping it flat-topped 14.5 % of the file in plateaus up to
# 4.24 ms — 13.8 % of a whole cycle at 32.5 Hz, i.e. a sub turned part-square.
#
# A single transparent gain preserves the waveform exactly instead. Samples at
# or below full scale are untouched, so this is byte-identical for everything
# that was already representable: measured over the shipping library, 151 of
# the 159 wavs that reach this converter come out unchanged, and the 8 that
# differ are exactly the 8 that were being destroyed.
def _peak_normalize(samples):
    """Scale a float signal down to full scale iff it overshoots it."""
    import numpy as np

    x = np.nan_to_num(np.asarray(samples, dtype=np.float64), nan=0.0,
                      posinf=1.0, neginf=-1.0)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak > 1.0:
        x = x * (1.0 / peak)
    return x


def _float_to_pcm24_bytes(samples) -> bytes:
    import numpy as np

    # Normalize first; the clip is now only a rounding safety net.
    clipped = np.clip(_peak_normalize(samples), -1.0, 1.0)
    ints = np.round(clipped * 8388607.0).astype(np.int32)
    ints &= 0xFFFFFF
    out = np.empty((ints.size, 3), dtype=np.uint8)
    out[:, 0] = ints & 0xFF
    out[:, 1] = (ints >> 8) & 0xFF
    out[:, 2] = (ints >> 16) & 0xFF
    return out.tobytes()


def _pcm24_wav_bytes(pcm: bytes, channels: int, sample_rate: int) -> bytes:
    block_align = channels * 3
    byte_rate = sample_rate * block_align
    fmt_chunk = struct.pack(
        "<HHIIHH", _WAVE_FORMAT_PCM, channels, sample_rate, byte_rate, block_align, 24
    )
    riff_size = 4 + (8 + len(fmt_chunk)) + (8 + len(pcm))
    return (
        b"RIFF" + struct.pack("<I", riff_size) + b"WAVE"
        + b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
        + b"data" + struct.pack("<I", len(pcm)) + pcm
    )


def describe_wav_damage(path: Path) -> str | None:
    """``None`` if ``path`` is a complete, loadable WAV, else why it is not.

    Checks the DECLARED chunk sizes against what is actually on disk. That is
    exactly what a half-written file fails and what FL chokes on: the states
    captured from the pre-2026-08-15 writer were a 0-byte file and a RIFF
    header advertising 667134 bytes over 33-278 KB of real audio.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return f"unreadable ({type(exc).__name__})"
    if not raw:
        return "empty (0 bytes)"
    if len(raw) < 44:
        return f"header truncated ({len(raw)} bytes)"
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return "not a RIFF/WAVE file"
    declared = struct.unpack_from("<I", raw, 4)[0]
    if declared != len(raw) - 8:
        return (f"RIFF size mismatch: header declares {declared}, file holds "
                f"{len(raw) - 8} (short by {declared - (len(raw) - 8)} bytes)")
    off = 12
    while off + 8 <= len(raw):
        cid = raw[off : off + 4]
        csz = struct.unpack_from("<I", raw, off + 4)[0]
        if cid == b"data":
            present = len(raw) - (off + 8)
            if csz <= 0:
                return "data chunk is empty"
            if csz > present:
                return (f"data chunk short: declares {csz}, {present} present "
                        f"(missing {csz - present} bytes of audio)")
            return None
        off += 8 + csz + (csz & 1)
    return "no data chunk"


def _is_intact_wav(path: Path) -> bool:
    return describe_wav_damage(path) is None


def _content_mismatch(path: Path, payload: bytes) -> str | None:
    """``None`` when ``path`` holds exactly ``payload``, else how it differs.

    The container checks above answer "is this a loadable WAV". They cannot
    answer "is this the RIGHT audio" — a structurally perfect file carrying
    some other sample passes every one of them. This is the second question,
    and it is the only one that catches a published file that is whole but
    wrong.
    """
    try:
        on_disk = path.read_bytes()
    except OSError as exc:
        return f"unreadable ({type(exc).__name__})"
    if on_disk == payload:
        return None
    if len(on_disk) != len(payload):
        return f"size differs: on disk {len(on_disk)}, expected {len(payload)}"
    first = next(
        (i for i, (a, b) in enumerate(zip(on_disk, payload)) if a != b), None
    )
    return f"same size, audio differs from byte {first}"


def published_content_mismatch(src: Path, dst: Path) -> str | None:
    """``None`` when ``dst`` holds exactly what converting ``src`` produces.

    The last gate's content half. ``_write_pcm24_wav`` already refuses to
    publish anything but this payload, so on the ordinary path this can only
    agree — it exists for the paths that skip it: a lost os.replace race, and
    anything that edits these files behind the engine's back between exports.
    Returns a reason string when it cannot tell, never a false alarm.
    """
    info = inspect_wav(src)
    if info is None:
        return None  # unreadable source is a different owner's failure
    try:
        samples, channels, sample_rate = _samples_of(src, info)
    except (ValueError, ImportError):
        return None
    payload = _pcm24_wav_bytes(
        _float_to_pcm24_bytes(samples), max(1, channels), max(8000, sample_rate)
    )
    return _content_mismatch(dst, payload)


def _write_pcm24_wav(path: Path, pcm: bytes, channels: int, sample_rate: int) -> None:
    """Publish ``path`` atomically, and only when its content would change.

    These files are NOT scratch output: every .flp this project has ever
    exported points its sampler channels at them by absolute path, and
    flp_writer re-converts the 808's canonical name on every single export.
    The previous ``open(path, "wb")`` truncated a file that a still-open FL
    project was referencing and then streamed ~650 KB into it, so an
    interrupted or contended write left a live project pointing at a
    truncated wav. Writing a sibling temp file and os.replace()-ing it means a
    reader sees either the whole old file or the whole new one, never a
    partial one — and the skip below means a re-export that produces identical
    audio does not touch the file at all.

    On Windows os.replace raises PermissionError while another process holds
    the target open, which is exactly the case this function exists for (FL
    has the project loaded). Measured: it fails both for an ordinary open
    handle and under a byte-range lock. Letting that propagate would make
    convert_to_pcm24 report failure, and flp_writer then points the channel at
    the raw library path — whose U+007F chars load "red"/silent in FL. So a
    lost race falls back to KEEPING the intact file already published under
    this name. That name is keyed by the source path, so the worst case is an
    older conversion of the same source sample, and the next uncontended
    export corrects it. A valid slightly-stale 808 beats both a corrupt one
    and a silent one.
    """
    payload = _pcm24_wav_bytes(pcm, channels, sample_rate)
    try:
        if path.is_file() and path.read_bytes() == payload:
            return
    except OSError:
        pass  # unreadable destination — fall through and republish it
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(payload)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    for delay in (0.0, 0.05, 0.15, 0.3):
        if delay:
            time.sleep(delay)
        try:
            os.replace(tmp, path)
            return
        except OSError as exc:
            last = exc
    tmp.unlink(missing_ok=True)
    import logging

    if _is_intact_wav(path):
        # Intact is not the same as CORRECT. `payload` is exactly what this
        # export wanted published, so comparing against it costs nothing here
        # — it is already in memory — and it is the only moment the two can be
        # compared at all. Keeping the file is still right (see above: a valid
        # slightly-stale 808 beats a silent channel), but a mismatch means the
        # FLP now points at audio this generation did not choose, and that has
        # to be provable rather than silent. 2026-08-25.
        stale = _content_mismatch(path, payload)
        if stale:
            logging.error(
                "FL transcode: %s is held open by another process (%s) AND its "
                "content differs from this export's (%s); the project will use "
                "the older audio", path.name, last, stale,
            )
        else:
            logging.warning(
                "FL transcode: %s is held open by another process (%s); kept the "
                "existing intact copy rather than replacing it", path.name, last,
            )
        return
    try:
        readable = True
        path.read_bytes()
    except OSError:
        readable = False
    if not readable and path.is_file() and path.stat().st_size > 44:
        # Locked so hard we cannot even inspect it. This function is the only
        # writer of these files and now only ever publishes complete ones, so
        # what is there is almost certainly a good earlier copy — and keeping
        # it beats the alternative, which is flp_writer falling back to the
        # raw library path and FL loading the channel silent.
        logging.warning(
            "FL transcode: %s is locked and unreadable (%s); leaving the "
            "existing file in place", path.name, last,
        )
        return
    raise last


def _log_if_hot(src: Path, samples) -> None:
    """Record any source that overshoots full scale.

    A user's own kit is the one input this project cannot inspect ahead of
    time, and an over-full-scale one-shot used to be destroyed here silently.
    """
    import logging

    import numpy as np

    peak = float(np.max(np.abs(np.nan_to_num(samples)))) if getattr(samples, "size", 0) else 0.0
    if peak > 1.0:
        logging.info(
            "FL transcode: %s peaks at %.3f (+%.2f dBFS) — scaled to full scale "
            "instead of clipped",
            Path(src).name, peak, 20.0 * np.log10(peak),
        )


def _samples_of(src: Path, info) -> tuple["object", int, int]:
    """``(interleaved float samples, channels, rate)`` of a WAV whatever its format; raises ValueError."""
    tag, channels, sample_rate, bits, data = info
    if tag in _special_tags():
        from wav_decode import WavDecodeError, read_wav

        try:
            audio = read_wav(src)
        except WavDecodeError as exc:
            raise ValueError(str(exc)) from exc
        return audio.samples.reshape(-1), audio.channels, audio.rate
    return _decode_to_float(tag, bits, data), channels, sample_rate


def _special_tags():
    from wav_decode import SPECIAL_TAGS

    return SPECIAL_TAGS


def convert_to_pcm24(src: Path, dst: Path) -> bool:
    """Transcode ``src`` to 24-bit PCM at ``dst``. Returns True on success; False for a format it cannot
    decode (it never writes noise)."""
    info = inspect_wav(src)
    if info is None:
        return False
    try:
        samples, channels, sample_rate = _samples_of(src, info)
    except (ValueError, ImportError):
        return False
    _log_if_hot(src, samples)
    pcm = _float_to_pcm24_bytes(samples)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _write_pcm24_wav(dst, pcm, max(1, channels), max(8000, sample_rate))
    except OSError:
        return False
    return True
