"""Which sounds PLG may put in a project: the single owner of two product decisions (2026-10).

* ``melody_sample_enabled()`` - ``PLG_MELODY_SAMPLE``. OFF by default: the melody channel of the .flp is an
  empty Sampler that carries the user's notes, with no sound of ours; the user puts their own synth on it.
  ``on`` brings back the bundled melody sample (and its warmth / FX / width copies, and the sample chop).
* ``substitutes_allowed()`` - ``PLG_SUBSTITUTE_SOUNDS``. OFF by default: when the user has no file for a drum
  lane (kick, snare, clap, hats, 808), the lane is NOT in the .flp (no channel, no pattern, no playlist
  row; the snare layer goes with the snare). Inside the generation its notes live on, recorded as
  "absent" (``lane_fallback``). No bundled starter sound and no synthetic 808 / hat is put anywhere.
  ``on`` is the earlier behaviour (the whole last-resort ladder), kept for tests and for a tester who
  asks for it. The melody is the exception: its channel stays, with the notes and no sample.

* ``melody_oneshots_enabled()`` - ``PLG_MELODY_ONESHOTS``. ON by default (2026-11; ``off`` / ``0`` / ``false`` / ``no`` switches it
  off): the melody channel carries one of PLG's OWN melody one-shots (``assets/melody_oneshots``, made in Serum 2 from its
  factory presets, see ``melody_oneshots``). No automatic pick from the user's folder may sit there (a file named "Sound"
  says nothing about its type): only a file the user pinned BY HAND on the melody cell (``pinned_kit``, explicit) does, and it
  wins over the one-shot. Off: the earlier behaviour (``PLG_MELODY_SAMPLE`` decides, no one-shot, no melody pin).

All are read at call time, so a test can flip them with ``monkeypatch.setenv``.
"""

from __future__ import annotations

import os

_ON = ("1", "on", "true", "yes")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _ON


def melody_sample_enabled() -> bool:
    return _flag("PLG_MELODY_SAMPLE")


def substitutes_allowed() -> bool:
    return _flag("PLG_SUBSTITUTE_SOUNDS")


_OFF = ("0", "off", "false", "no")


def melody_oneshots_enabled() -> bool:
    """On unless the variable says off: the one flag that defaults to ON."""
    return os.environ.get("PLG_MELODY_ONESHOTS", "").strip().lower() not in _OFF


__all__ = ["melody_oneshots_enabled", "melody_sample_enabled", "substitutes_allowed"]
