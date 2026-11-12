#!/usr/bin/env python3
"""Generate (or verify) the starter audio that `PLG.spec` packages into PLG.exe.

Why this exists
---------------
All 23 shipping WAVs under `assets/starter/` are gitignored, so a clean
checkout has none of them. `PLG.spec` used to guard every starter file with
`if ... .is_file()`, which meant a clean-room build produced a *silently
incomplete* exe: no error, no warning, just missing audio. This module closes
that hole by regenerating the assets from the first-party synthesis code
before packaging, and by refusing to continue if anything is missing or has
drifted.

It contains no synthesis of its own. Every sample is produced by the existing
generators with their existing defaults -- no parameter, seed, envelope, gain,
duration or sample-rate is touched here:

* `build_bundled_sounds.ensure_bundled_sounds()` writes the 16 files in
  `bundled_sounds/` from `BUNDLED_SPECS`.
* `starter_kit._ensure_synthetic_starter()` writes the 7 `PLG_starter_*.wav`.
  Five come straight from `starter_kit._synth_*`; `kick` and `snare` are
  copies of bundled files chosen by `pick_bundled_for_track()`, which is
  deterministic for an empty prompt (every candidate scores 0, so `max()`
  returns the first of a sorted list).

`GENERATED.json` is the tracked contract: every shipping WAV, the generator
that produces it, and the SHA256 it must have. It is what makes "the audio in
this exe came from our code" checkable rather than asserted.

Usage
-----
    python tools/build_starter_assets.py            # generate what is missing
    python tools/build_starter_assets.py --check    # verify only, writes nothing
    python tools/build_starter_assets.py --write-contract
                                                    # re-stamp GENERATED.json

`--check` exits non-zero on a missing or hash-mismatched required asset, and
so does a generation run that cannot reach the recorded hashes. Both are safe
to wire into CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
STARTER = REPO / "assets" / "starter"
BUNDLED = STARTER / "bundled_sounds"
CONTRACT = STARTER / "GENERATED.json"
RUNTIME_MANIFEST = STARTER / "starter_manifest.json"

# The seven names PLG.spec lists explicitly, minus the tracked text files.
STARTER_WAVS = (
    "PLG_starter_808.wav",
    "PLG_starter_808_bass.wav",
    "PLG_starter_hat.wav",
    "PLG_starter_melody.wav",
    "PLG_starter_kick.wav",
    "PLG_starter_snare.wav",
    "PLG_starter_clap.wav",
)

# Which generator actually produces each starter file. Established by
# regenerating each candidate and byte-comparing against the shipped asset --
# note that kick and snare are NOT `_synth_kick`/`_synth_snare` output:
# `_ensure_synthetic_starter` prefers the bundled pool over its own synth.
STARTER_GENERATORS = {
    "PLG_starter_808.wav": "starter_kit._synth_808",
    "PLG_starter_808_bass.wav": "starter_kit._synth_808_bass",
    "PLG_starter_hat.wav": "starter_kit._synth_hat",
    "PLG_starter_melody.wav": "starter_kit._synth_melody_pluck",
    "PLG_starter_clap.wav": "starter_kit._synth_clap",
    "PLG_starter_kick.wav": (
        "build_bundled_sounds.BUNDLED_SPECS[kick_boom_808.wav]"
        " (copied via starter_kit.pick_bundled_for_track)"
    ),
    "PLG_starter_snare.wav": (
        "build_bundled_sounds.BUNDLED_SPECS[snare_rim_layer.wav]"
        " (copied via starter_kit.pick_bundled_for_track)"
    ),
}


class AssetError(RuntimeError):
    """Raised when the shipping audio cannot be produced or does not verify."""


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundled_names() -> list[str]:
    """The bundled one-shot filenames, straight from the generator's own spec."""
    sys.path.insert(0, str(REPO))
    import build_bundled_sounds as bbs

    return [name for name, _samples in bbs.BUNDLED_SPECS]


def required_relative_paths() -> list[str]:
    """Every WAV PLG.spec packages, as paths relative to assets/starter."""
    return list(STARTER_WAVS) + ["bundled_sounds/%s" % n for n in bundled_names()]


def generator_for(relative: str) -> str:
    if relative.startswith("bundled_sounds/"):
        return "build_bundled_sounds.BUNDLED_SPECS[%s]" % relative.split("/", 1)[1]
    return STARTER_GENERATORS[relative]


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------

def generate() -> None:
    """Produce every missing shipping WAV. Idempotent: only writes what is absent.

    This deliberately does NOT call `starter_kit._ensure_synthetic_starter()`.
    That function prefers `pick_bundled_for_track()` over its own synthesis, so
    on an empty tree it resolves `sub_808`, `hi_hats` and `melody_lead` to the
    first sorted member of the bundled pool -- `808_glide_stab`, `hat_open_roll`
    and `melody_bell_hook` -- instead of `_synth_808`, `_synth_hat` and
    `_synth_melody_pluck`. The bytes PLG has always shipped are the synth ones;
    they survived only because the files already existed on the machines that
    ran it. A build step has to pin the exact asset, not re-run a keyword
    heuristic whose answer depends on what is already lying around.

    Runtime behaviour is untouched -- `_ensure_synthetic_starter` still governs
    what a user's machine does when its starter folder is empty.
    """
    import shutil

    sys.path.insert(0, str(REPO))
    import build_bundled_sounds as bbs
    import starter_kit as sk

    # 16 bundled one-shots, straight from BUNDLED_SPECS.
    bbs.ensure_bundled_sounds()

    # Five starter files come from starter_kit's own synthesis, called with its
    # existing defaults -- no parameter, seed or duration supplied here.
    direct = {
        "PLG_starter_808.wav": sk._synth_808,
        "PLG_starter_808_bass.wav": sk._synth_808_bass,
        "PLG_starter_hat.wav": sk._synth_hat,
        "PLG_starter_melody.wav": sk._synth_melody_pluck,
        "PLG_starter_clap.wav": sk._synth_clap,
    }
    # Two are copies of a named bundled file, as the shipped bytes prove.
    copies = {
        "PLG_starter_kick.wav": "kick_boom_808.wav",
        "PLG_starter_snare.wav": "snare_rim_layer.wav",
    }

    for name, synth in direct.items():
        target = STARTER / name
        if not target.is_file():
            sk._write_wav_mono(target, synth())

    for name, source_name in copies.items():
        target = STARTER / name
        if target.is_file():
            continue
        source = BUNDLED / source_name
        if not source.is_file():
            raise AssetError(
                "%s is a copy of %s, which was not generated" % (name, source_name)
            )
        shutil.copyfile(source, target)


def write_runtime_manifest() -> None:
    """Rewrite starter_manifest.json without machine-specific absolute paths.

    `starter_kit._write_manifest` stamps absolute paths of whatever machine
    ran it, which is right at runtime but meaningless once committed -- the
    tracked copy had a `C:\\PLUG.FLP\\...` path from a long-dead project root.
    Only the `source` key is ever read back (starter_kit.py:405, :1390), so
    replacing the path map with provenance is safe.
    """
    RUNTIME_MANIFEST.write_text(
        json.dumps(
            {
                "source": "plg_synth",
                "bundle_version": 1,
                "note": (
                    "Starter audio is generated by first-party synthesis, not shipped "
                    "from a sample pack. Per-file generator identity and SHA256 live in "
                    "GENERATED.json. Regenerate with tools/build_starter_assets.py. "
                    "PLG rewrites this file at runtime with local paths; only the "
                    "'source' key is read back."
                ),
                "tracks": {
                    "kick": "PLG_starter_kick.wav",
                    "snare": "PLG_starter_snare.wav",
                    "snare_layer": "PLG_starter_snare.wav",
                    "clap": "PLG_starter_clap.wav",
                    "hi_hats": "PLG_starter_hat.wav",
                    "sub_808": "PLG_starter_808.wav",
                    "808_bass": "PLG_starter_808_bass.wav",
                    "melody_lead": "PLG_starter_melody.wav",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_contract() -> dict:
    """Stamp GENERATED.json from the assets currently on disk."""
    missing = [r for r in required_relative_paths() if not (STARTER / r).is_file()]
    if missing:
        raise AssetError(
            "cannot stamp the contract while %d asset(s) are missing: %s"
            % (len(missing), ", ".join(missing[:5]))
        )

    files = {
        relative: {
            "generator": generator_for(relative),
            "sha256": sha256(STARTER / relative),
        }
        for relative in sorted(required_relative_paths())
    }
    payload = {
        "version": 1,
        "description": (
            "Reproducibility contract for the generated audio that PLG.spec packages "
            "into PLG.exe. Every file below is produced by first-party synthesis from "
            "tracked source -- no sample packs, no external presets. Regenerate and "
            "verify with tools/build_starter_assets.py."
        ),
        "generated_by": "tools/build_starter_assets.py",
        "count": len(files),
        "files": files,
    }
    CONTRACT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

def load_contract() -> dict:
    if not CONTRACT.is_file():
        raise AssetError(
            "no reproducibility contract at %s -- run --write-contract from a tree "
            "whose assets are already verified" % CONTRACT
        )
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def verify(*, quiet: bool = False) -> list[str]:
    """Compare on-disk assets against the contract. Never writes anything."""
    contract = load_contract()
    expected = contract["files"]

    problems: list[str] = []

    declared = set(expected)
    required = set(required_relative_paths())
    for relative in sorted(required - declared):
        problems.append("packaged but absent from the contract: %s" % relative)
    for relative in sorted(declared - required):
        problems.append("in the contract but no longer packaged: %s" % relative)

    for relative in sorted(declared & required):
        path = STARTER / relative
        if not path.is_file():
            problems.append("missing: %s" % relative)
            continue
        actual = sha256(path)
        if actual != expected[relative]["sha256"]:
            problems.append(
                "hash mismatch: %s (expected %s, got %s)"
                % (relative, expected[relative]["sha256"][:16], actual[:16])
            )

    if not quiet:
        if problems:
            print("STARTER AUDIO FAILED VERIFICATION (%d):" % len(problems))
            for problem in problems:
                print("  - %s" % problem)
        else:
            print("OK: %d generated WAVs match GENERATED.json." % len(expected))
    return problems


def ensure_ready() -> None:
    """Generate whatever is missing, then verify. Used by PLG.spec at build time.

    Raises AssetError rather than returning a flag, so a packaging run cannot
    quietly continue with incomplete audio.
    """
    generate()
    write_runtime_manifest()
    problems = verify(quiet=True)
    if problems:
        raise AssetError(
            "generated starter audio does not match GENERATED.json:\n  %s"
            % "\n  ".join(problems)
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="verify against GENERATED.json, write nothing")
    parser.add_argument("--write-contract", action="store_true",
                        help="re-stamp GENERATED.json from the assets on disk")
    args = parser.parse_args(argv)

    try:
        if args.check:
            return 1 if verify() else 0

        if args.write_contract:
            payload = write_contract()
            print("stamped %s with %d files" % (CONTRACT, payload["count"]))
            return 0

        generate()
        write_runtime_manifest()
        problems = verify()
        return 1 if problems else 0
    except AssetError as exc:
        print("ASSET ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
