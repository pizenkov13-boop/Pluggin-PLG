"""Windows-only console suppression for the supported pydub decoder paths.

``pydub==0.25.1`` starts ffmpeg via ``audio_segment.subprocess.Popen`` and
ffprobe via the ``utils.Popen`` alias.  A windowed PLG process has no inherited
console, so those console-subsystem tools otherwise create a transient window
for every decode/probe.  This module touches only those two pydub references
while ``AudioSegment.from_file`` is executing; it never changes stdlib
``subprocess.Popen`` globally.
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator

logger = logging.getLogger("plg.pydub_windows_compat")

SUPPORTED_PYDUB_VERSION = "0.25.1"
_PATCH_LOCK = threading.RLock()
_MISMATCH_LOGGED = False


def _running_on_windows() -> bool:
    return os.name == "nt"


def installed_pydub_version() -> str | None:
    """Return the installed pydub distribution version without importing it."""
    try:
        return importlib.metadata.version("pydub")
    except importlib.metadata.PackageNotFoundError:
        return None


def supports_installed_pydub() -> bool:
    """Whether pydub's internals are the explicitly supported layout."""
    return installed_pydub_version() == SUPPORTED_PYDUB_VERSION


def _command_name(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    command = args[0] if args else kwargs.get("args")
    if isinstance(command, (list, tuple)) and command:
        command = command[0]
    if command is None:
        return ""
    return Path(str(command)).name.casefold()


def _is_pydub_decoder_command(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    return _command_name(args, kwargs) in {"ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe"}


def _hidden_decoder_popen(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap one pydub-only Popen reference, preserving its call contract."""

    def launch(*args: Any, **kwargs: Any) -> Any:
        if not _running_on_windows() or not _is_pydub_decoder_command(args, kwargs):
            return original(*args, **kwargs)
        forwarded = dict(kwargs)
        forwarded["creationflags"] = int(forwarded.get("creationflags", 0)) | subprocess.CREATE_NO_WINDOW
        return original(*args, **forwarded)

    return launch


class _PopenModuleProxy:
    """Module-local proxy: only pydub.audio_segment sees this replacement."""

    def __init__(self, original: ModuleType, popen: Callable[..., Any]) -> None:
        self._original = original
        self.Popen = popen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


@contextmanager
def hidden_pydub_decoder_windows() -> Iterator[None]:
    """Temporarily hide only pydub's ffmpeg/ffprobe console children.

    Unsupported pydub versions are deliberately left untouched.  The lock keeps
    nested PLG audio reads from restoring either pydub reference too early.
    """
    global _MISMATCH_LOGGED

    if not _running_on_windows():
        yield
        return
    version = installed_pydub_version()
    if version != SUPPORTED_PYDUB_VERSION:
        if not _MISMATCH_LOGGED:
            logger.warning(
                "pydub console compatibility skipped: supported=%s installed=%s",
                SUPPORTED_PYDUB_VERSION,
                version or "not installed",
            )
            _MISMATCH_LOGGED = True
        yield
        return

    from pydub import audio_segment, utils

    original_subprocess = audio_segment.subprocess
    original_utils_popen = utils.Popen
    if not isinstance(original_subprocess, ModuleType) or not callable(original_subprocess.Popen) or not callable(original_utils_popen):
        logger.warning("pydub console compatibility skipped: 0.25.1 launch layout is unavailable")
        yield
        return

    with _PATCH_LOCK:
        audio_segment.subprocess = _PopenModuleProxy(
            original_subprocess,
            _hidden_decoder_popen(original_subprocess.Popen),
        )
        utils.Popen = _hidden_decoder_popen(original_utils_popen)
        try:
            yield
        finally:
            audio_segment.subprocess = original_subprocess
            utils.Popen = original_utils_popen


def audio_segment_from_file(*args: Any, **kwargs: Any) -> Any:
    """The unchanged pydub decode call, with Windows console handling scoped in."""
    from pydub import AudioSegment

    with hidden_pydub_decoder_windows():
        return AudioSegment.from_file(*args, **kwargs)
