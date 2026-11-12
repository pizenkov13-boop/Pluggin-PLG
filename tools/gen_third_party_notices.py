#!/usr/bin/env python3
"""Regenerate THIRD_PARTY_NOTICES.md + third_party_licenses/ from local evidence.

Why this exists
---------------
The first version of those two artefacts was produced by a script that was
never committed, so the outputs could not be reproduced or re-checked when a
dependency moves. This tool replaces that gap. It derives everything it can
from things that can be *pointed at* -- the built `dist/PLG.exe`, the
`dist-info` metadata of the installed packages, the TTF `name` tables, and
`web/node_modules` -- and it copies license texts byte-for-byte instead of
embedding them in this file. Nothing here retypes a license.

What is derived vs. what is curated
-----------------------------------
Sections 1-5 of the notice are derived mechanically on every run.
Sections 6-8 are human judgement ("NEEDS EXTERNAL REVIEW" is not a thing a
script can conclude) and live verbatim in `tools/notice_curated.md`. They are
not invented here, but they are not blindly trusted either: every runtime
binary section 6 claims is embedded must still be present in the exe, or the
run fails and tells you to revisit the curated text.

Evidence rules (each one was validated against the committed notice before
being written down here):

* license string -- `License-Expression` if the metadata has one, else the
  `License ::` trove classifiers joined with " / ", else a short `License`
  field, else UNKNOWN. Never guessed from the package name.
* shipped-ness -- a distribution is in the notice only if at least one of its
  top-level names appears in the exe's PYZ module table or as an exe data
  entry. `top_level.txt` when the wheel ships one, otherwise the top-level
  names are recovered from RECORD.
* license text -- `dist-info/licenses/**` when the wheel uses the modern
  layout, otherwise the `License-File` metadata entries, otherwise a
  conservative root-level glob. Copied, never rendered.

Usage
-----
    python tools/gen_third_party_notices.py            # write to a temp dir
    python tools/gen_third_party_notices.py --out DIR  # write to DIR
    python tools/gen_third_party_notices.py --check    # compare, touch nothing

`--check` exits non-zero on any drift, and so does a generation run that
cannot find the evidence it needs. Both are meant to be safe to wire into CI.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import pathlib
import re
import shutil
import struct
import sys
import tempfile
import unicodedata
from datetime import date, datetime

REPO = pathlib.Path(__file__).resolve().parent.parent

EXE = REPO / "dist" / "PLG.exe"
NOTICE_NAME = "THIRD_PARTY_NOTICES.md"
LICENSE_DIR_NAME = "third_party_licenses"
CURATED = REPO / "tools" / "notice_curated.md"

# The canonical SIL OFL 1.1 text is an upstream document; we cannot re-fetch it
# offline and we refuse to retype it, so the vendored copy is the input and its
# digest is pinned. Tamper with the file and generation stops.
OFL_SOURCE = REPO / LICENSE_DIR_NAME / "fonts" / "OFL-1.1.txt"
OFL_SHA256 = "1d361a8f8e8ce6e68457dcd93fb56e162e6baa3bbb7e7573a290d44399f6b57e"

# Runtime binaries that section 6 of the curated text talks about. If one of
# these stops matching, the curated section is stale and the run fails.
CURATED_BINARY_CLAIMS = [
    ("CPython", r"^python3\d*\.dll$"),
    ("CPython stdlib", r"^base_library\.zip$"),
    ("OpenSSL", r"^lib(crypto|ssl)-3.*\.dll$"),
    ("OpenBLAS", r"^numpy\.libs/.*openblas.*\.dll$"),
    (".NET reference assemblies", r"^pythonnet/runtime/System\..*\.dll$"),
    ("WebView2", r"(?i)^webview/lib/.*webview2.*"),
    ("MSVC runtime", r"(?i)^(vcruntime|msvcp)\d+.*\.dll$"),
]

WEB_DEPS = ["lucide-react", "motion", "react", "react-dom"]

LICENSE_GLOBS = ("LICENSE*", "LICENCE*", "COPYING*", "NOTICE*")


class EvidenceError(RuntimeError):
    """Raised when the generator cannot prove something it would have to state."""


# --------------------------------------------------------------------------
# exe inventory
# --------------------------------------------------------------------------

def read_exe_inventory(exe: pathlib.Path):
    """Return (toc, pyz_modules, data_top_dirs) read out of the frozen exe."""
    try:
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    except ImportError as exc:  # pragma: no cover - environment problem
        raise EvidenceError(
            "PyInstaller is required to read the built exe: %s" % exc
        ) from exc

    if not exe.is_file():
        raise EvidenceError(
            "no built artefact at %s -- build it before generating notices" % exe
        )

    reader = CArchiveReader(str(exe))
    toc = sorted(name.replace("\\", "/") for name in reader.toc)

    pyz_entries = [name for name in reader.toc if name.endswith(".pyz")]
    if not pyz_entries:
        raise EvidenceError("no PYZ archive inside %s" % exe)

    handle = tempfile.NamedTemporaryFile(suffix=".pyz", delete=False)
    try:
        handle.write(reader.extract(pyz_entries[0]))
        handle.close()
        modules = set(ZlibArchiveReader(handle.name).toc)
    finally:
        os.unlink(handle.name)

    data_tops = {name.split("/")[0] for name in toc if "/" in name}
    return toc, modules, data_tops


# --------------------------------------------------------------------------
# installed distribution metadata
# --------------------------------------------------------------------------

def installed_distributions():
    import importlib.metadata as md

    found = {}
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        # A duplicate name means two site dirs disagree; keep the first and let
        # the caller notice via the version column rather than silently merging.
        found.setdefault(name, dist)
    return found


def dist_info_path(dist) -> pathlib.Path:
    return pathlib.Path(str(dist._path))


def license_string(dist) -> str:
    """The license label, by the rule validated against the committed notice."""
    meta = dist.metadata

    expression = meta.get("License-Expression")
    if expression and expression.strip():
        return expression.strip()

    classifiers = [
        line.split("::")[-1].strip()
        for line in (meta.get_all("Classifier") or [])
        if line.startswith("License")
    ]
    if classifiers:
        return " / ".join(classifiers)

    # Some older wheels put the whole license text in the License field; only a
    # short one is a label rather than a document.
    legacy = meta.get("License")
    if legacy and "\n" not in legacy and len(legacy) < 60:
        return legacy.strip()

    return "UNKNOWN"


def top_level_names(dist) -> list[str]:
    """Top-level importable names, from top_level.txt or recovered from RECORD."""
    declared = dist.read_text("top_level.txt")
    if declared:
        return sorted({line.strip() for line in declared.splitlines() if line.strip()})

    names = set()
    record = dist.read_text("RECORD") or ""
    for row in csv.reader(io.StringIO(record)):
        if not row:
            continue
        path = row[0].replace("\\", "/")
        if path.startswith(".."):
            continue
        head = path.split("/")[0]
        if not head or head.endswith((".dist-info", ".data")):
            continue
        if head.endswith(".py"):
            names.add(head[:-3])
        elif head.endswith((".pyd", ".so", ".dll")):
            names.add(head.split(".")[0])
        elif "/" in path:
            names.add(head)
    return sorted(names)


def shipped_evidence(dist, pyz_modules, data_tops) -> str:
    """Human-readable proof that this distribution is inside the artefact."""
    tops = top_level_names(dist)
    modules = sorted({name for name in tops if name in pyz_modules})
    entries = sorted({name for name in tops if name in data_tops})
    parts = ["PYZ module %s" % name for name in modules]
    parts += ["exe data entry %s/" % name for name in entries]
    return "; ".join(parts)


def license_files_for(dist) -> list[tuple[str, pathlib.Path]]:
    """(relative destination, source path) for every canonical text we can copy."""
    base = dist_info_path(dist)

    modern = base / "licenses"
    if modern.is_dir():
        return sorted(
            (path.relative_to(modern).as_posix(), path)
            for path in modern.rglob("*")
            if path.is_file()
        )

    declared = dist.metadata.get_all("License-File") or []
    picked = []
    for entry in declared:
        candidate = base / entry
        if candidate.is_file():
            picked.append((entry.replace("\\", "/"), candidate))
    if picked:
        return sorted(picked)

    fallback = []
    for pattern in LICENSE_GLOBS:
        for path in base.glob(pattern):
            if path.is_file():
                fallback.append((path.name, path))
    return sorted(set(fallback))


# --------------------------------------------------------------------------
# fonts
# --------------------------------------------------------------------------

def ttf_name_table(path: pathlib.Path) -> dict[int, str]:
    """Minimal TTF `name` table reader -- enough for copyright and family."""
    data = path.read_bytes()
    if len(data) < 12:
        raise EvidenceError("not a font file: %s" % path)

    table_count = struct.unpack(">H", data[4:6])[0]
    tables = {}
    cursor = 12
    for _ in range(table_count):
        tag = data[cursor:cursor + 4].decode("latin-1")
        offset, length = struct.unpack(">II", data[cursor + 8:cursor + 16])
        tables[tag] = (offset, length)
        cursor += 16

    if "name" not in tables:
        raise EvidenceError("font has no name table: %s" % path)

    base, _ = tables["name"]
    count, strings_offset = struct.unpack(">HH", data[base + 2:base + 6])

    records: dict[int, str] = {}
    for index in range(count):
        record = data[base + 6 + index * 12: base + 18 + index * 12]
        platform, _encoding, _lang, name_id, length, offset = struct.unpack(">HHHHHH", record)
        raw = data[base + strings_offset + offset: base + strings_offset + offset + length]
        try:
            text = raw.decode("utf-16-be") if platform == 3 else raw.decode("latin-1")
        except UnicodeDecodeError:
            continue
        records.setdefault(name_id, text)
    return records


def font_rows(toc: list[str]) -> list[dict]:
    """One row per font family shipped in the exe, copyright read from the file."""
    shipped = [name for name in toc if name.lower().endswith(".ttf")]
    if not shipped:
        raise EvidenceError("no TTF fonts found inside the exe")

    # Group by the copyright string, because that -- not the styled family name
    # -- is what the attribution obligation attaches to. "Manrope SemiBold" and
    # "Manrope" share one notice; the label is the least-decorated family name
    # in the group, so "Fjalla One" survives intact while the weight suffixes go.
    families: dict[str, dict] = {}
    for entry in shipped:
        local = REPO / entry
        if not local.is_file():
            raise EvidenceError(
                "font %s is in the exe but not on disk -- cannot read its name table"
                % entry
            )
        table = ttf_name_table(local)
        copyright_text = table.get(0, "").strip()
        family = table.get(1, "").strip()
        if not copyright_text or not family:
            raise EvidenceError("font %s has no copyright/family name record" % entry)

        bucket = families.setdefault(
            copyright_text,
            {"family": family, "copyright": copyright_text, "files": []},
        )
        if len(family) < len(bucket["family"]):
            bucket["family"] = family
        bucket["files"].append(entry)

    for bucket in families.values():
        bucket["files"].sort()
    return sorted(families.values(), key=lambda bucket: bucket["family"])


# --------------------------------------------------------------------------
# javascript dependencies
# --------------------------------------------------------------------------

def web_rows() -> tuple[list[dict], str]:
    import json

    modules = REPO / "web" / "node_modules"
    if not modules.is_dir():
        raise EvidenceError(
            "web/node_modules is missing -- run npm install before generating notices"
        )

    bundles = sorted((REPO / "web" / "dist" / "assets").glob("index-*.js"))
    if len(bundles) != 1:
        raise EvidenceError(
            "expected exactly one web/dist/assets/index-*.js bundle, found %d"
            % len(bundles)
        )
    bundle = bundles[0].name

    rows = []
    for package in WEB_DEPS:
        root = modules / package
        manifest = root / "package.json"
        if not manifest.is_file():
            raise EvidenceError("web dependency %s is not installed" % package)
        meta = json.loads(manifest.read_text(encoding="utf-8"))

        candidates = [
            path
            for pattern in ("LICENSE*", "LICENCE*")
            for path in sorted(root.glob(pattern))
            if path.is_file()
        ]
        if not candidates:
            raise EvidenceError("no LICENSE file shipped by web dependency %s" % package)

        rows.append(
            {
                "name": package,
                "version": meta.get("version", "UNKNOWN"),
                "license": meta.get("license", "UNKNOWN"),
                "source": candidates[0],
                "dest": "%s-LICENSE.txt" % package,
            }
        )
    return rows, bundle


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def ascii_safe(text: str) -> str:
    """The committed notice is plain ASCII; keep it that way, losslessly enough."""
    swaps = {
        "—": " - ", "–": "-", "‘": "'", "’": "'",
        "“": '"', "”": '"', "…": "...", " ": " ",
    }
    for bad, good in swaps.items():
        text = text.replace(bad, good)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def render_notice(ctx) -> str:
    out: list[str] = []
    add = out.append

    add("# THIRD_PARTY_NOTICES - PLG desktop (`dist/PLG.exe`)")
    add("")
    add("Generated by `tools/gen_third_party_notices.py` from the build dated")
    add("%s. This notice covers everything actually shipped inside" % ctx["build_date"])
    add("`dist/PLG.exe`, as proven by reading the built archive - not by reading")
    add("`requirements.txt`. License texts are canonical copies taken from the")
    add("installed package's own `dist-info` metadata or from the upstream license")
    add("document; none were retyped. Companion folder shipped inside the exe:")
    add("`third_party_licenses/`.")
    add("")

    add("## 1. Fonts (SIL OFL 1.1)")
    add("")
    add("Copyright statements below are read from the TTF `name` tables of the font")
    add("files themselves (canonical source). The full OFL 1.1 text ships at")
    add("`third_party_licenses/fonts/OFL-1.1.txt` (canonical upstream copy from")
    add("openfontlicense.org).")
    add("")
    add("| Font | Copyright (from the font file) | License | Shipped as |")
    add("|---|---|---|---|")
    for font in ctx["fonts"]:
        shipped = ", ".join("`%s`" % name for name in font["files"])
        add(
            "| %s | %s | SIL OFL 1.1 (name table; OFL.txt shipped) | %s in exe |"
            % (font["family"], font["copyright"], shipped)
        )
    add("")

    add("## 2. CA certificate bundle (certifi / Mozilla)")
    add("")
    certifi = ctx["certifi"]
    add("`certifi` %s ships `%s` (the Mozilla CA Root" % (certifi["version"], certifi["pem"]))
    add("Certificates bundle) inside the exe. Its canonical license notice is the")
    add("package's own dist-info license file, copied to")
    add("`third_party_licenses/%s/`. Recorded license: %s."
        % (certifi["dirname"], certifi["license"]))
    add("")

    add("## 3. Python dependencies - direct (from `requirements.txt`), shipped")
    add("")
    add("| Component | Version | License | Source of license text | Shipped artifact evidence |")
    add("|---|---|---|---|---|")
    for row in ctx["direct"]:
        add(render_dist_row(row))
    add("")
    for name in ctx["declared_not_shipped"]:
        add("`%s` is declared in `requirements.txt` but is **not present** in the" % name)
        add("current exe (no matching modules in the PYZ) - no notice obligation for")
        add("this artifact.")
        add("")

    add("## 4. Python dependencies - transitive, shipped")
    add("")
    add("Enumerated by matching every installed distribution's top-level names")
    add("against the exe PYZ module table and data entries (evidence column).")
    add("")
    add("| Component | Version | License | Source of license text | Shipped artifact evidence |")
    add("|---|---|---|---|---|")
    for row in ctx["transitive"]:
        add(render_dist_row(row))
    add("")
    missing = [row for row in ctx["direct"] + ctx["transitive"] if not row["texts"]]
    if missing:
        add("Shipped components carrying no license text in their installed metadata")
        add("(recorded as UNKNOWN, not retyped):")
        add("")
        for row in missing:
            add("* `%s` %s (%s)" % (row["name"], row["version"], row["evidence"]))
        add("")

    add("## 5. JavaScript runtime dependencies (compiled into `web/dist`)")
    add("")
    add("Built from `web/src` (ours) with the runtime deps below compiled into")
    add("`web/dist/assets/%s`. Canonical LICENSE copies are at" % ctx["bundle"])
    add("`third_party_licenses/web/`. Build tools (vite/typescript) do not ship.")
    add("")
    add("| Component | Version | License | Source of license text | Shipped artifact evidence |")
    add("|---|---|---|---|---|")
    for row in ctx["web"]:
        add(
            "| `%s` | %s | %s | web/node_modules/%s/%s (copied: third_party_licenses/web/) | compiled into web/dist/assets/%s |"
            % (row["name"], row["version"], row["license"], row["name"],
               row["source"].name, ctx["bundle"])
        )
    add("")

    out.extend(ctx["curated"].rstrip("\n").split("\n"))
    return "\r\n".join(ascii_safe(line) for line in out) + "\r\n"


def render_dist_row(row) -> str:
    if row["texts"]:
        source = "installed package dist-info (copied: third_party_licenses/%s/)" % row["dirname"]
    else:
        source = "none available locally - UNKNOWN"
    return "| `%s` | %s | %s | %s | %s |" % (
        row["name"], row["version"], row["license"], source, row["evidence"],
    )


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------

def requirement_names() -> list[str]:
    path = REPO / "requirements.txt"
    if not path.is_file():
        raise EvidenceError("requirements.txt not found")
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=!~\[;]", line)[0].strip()
        if name:
            names.append(name)
    return names


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def collect(exe: pathlib.Path):
    toc, pyz_modules, data_tops = read_exe_inventory(exe)
    dists = installed_distributions()

    direct_declared = requirement_names()
    direct_keys = {canonical(name) for name in direct_declared}

    shipped: dict[str, dict] = {}
    for name, dist in dists.items():
        evidence = shipped_evidence(dist, pyz_modules, data_tops)
        if not evidence:
            continue
        texts = license_files_for(dist)
        shipped[name] = {
            "name": name,
            "version": dist.version,
            "license": license_string(dist),
            "evidence": evidence,
            "texts": texts,
            "dirname": "%s-%s" % (name, dist.version),
        }

    if not shipped:
        raise EvidenceError("no installed distribution matched the exe contents")

    direct, transitive = [], []
    for key in sorted(shipped, key=lambda n: canonical(n)):
        (direct if canonical(key) in direct_keys else transitive).append(shipped[key])

    declared_not_shipped = sorted(
        name for name in direct_declared
        if canonical(name) not in {canonical(row["name"]) for row in direct}
    )

    certifi_row = shipped.get("certifi")
    if certifi_row is None:
        raise EvidenceError("certifi is not shipped -- section 2 would be a lie")
    pem = next((name for name in toc if name.endswith("cacert.pem")), None)
    if pem is None:
        raise EvidenceError("certifi ships no cacert.pem inside the exe")

    if not OFL_SOURCE.is_file():
        raise EvidenceError(
            "canonical OFL text missing at %s -- this generator copies it, it "
            "will not retype it" % OFL_SOURCE
        )
    # Read it now, not at write time: generating in place wipes the output tree
    # first, and the vendored OFL text lives inside that tree.
    ofl_bytes = OFL_SOURCE.read_bytes()
    actual_ofl = hashlib.sha256(ofl_bytes).hexdigest()
    if actual_ofl != OFL_SHA256:
        raise EvidenceError(
            "vendored OFL text has changed (%s != pinned %s) -- verify it against "
            "openfontlicense.org and update OFL_SHA256 deliberately"
            % (actual_ofl, OFL_SHA256)
        )

    web, bundle = web_rows()
    for row in web:
        row["bytes"] = row["source"].read_bytes()
    verify_curated_claims(toc)

    build_date = datetime.fromtimestamp(exe.stat().st_mtime).date().isoformat()

    return {
        "toc": toc,
        "fonts": font_rows(toc),
        "certifi": {
            "version": certifi_row["version"],
            "dirname": certifi_row["dirname"],
            "license": certifi_row["license"],
            "pem": pem,
        },
        "direct": direct,
        "transitive": transitive,
        "declared_not_shipped": declared_not_shipped,
        "web": web,
        "bundle": bundle,
        "curated": read_curated(),
        "ofl_bytes": ofl_bytes,
        "build_date": build_date,
        "all_rows": direct + transitive,
    }


def read_curated() -> str:
    """The curated sections, minus the maintainer comment that heads the file."""
    text = CURATED.read_text(encoding="utf-8").replace("\r\n", "\n")
    return re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.S)


def verify_curated_claims(toc: list[str]) -> None:
    """Fail loudly if the curated section 6 describes binaries we no longer ship."""
    if not CURATED.is_file():
        raise EvidenceError(
            "curated sections missing at %s -- sections 6-8 are human judgement "
            "and this tool will not invent them" % CURATED
        )
    stale = [
        label for label, pattern in CURATED_BINARY_CLAIMS
        if not any(re.search(pattern, name) for name in toc)
    ]
    if stale:
        raise EvidenceError(
            "tools/notice_curated.md still claims these are embedded, but they are "
            "not in the exe any more: %s -- update the curated text" % ", ".join(stale)
        )


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def write_outputs(ctx, out_dir: pathlib.Path) -> None:
    """Materialise the notice and the license tree under out_dir.

    The tree is built in a sibling staging directory and swapped in at the end,
    so regenerating in place cannot leave a half-written tree behind if the run
    dies partway -- and cannot delete its own inputs before reading them.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    licenses = out_dir / LICENSE_DIR_NAME
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".licenses-", dir=out_dir))

    try:
        for row in ctx["all_rows"]:
            for relative, source in row["texts"]:
                target = staging / row["dirname"] / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

        fonts = staging / "fonts"
        fonts.mkdir(parents=True, exist_ok=True)
        (fonts / OFL_SOURCE.name).write_bytes(ctx["ofl_bytes"])

        web = staging / "web"
        web.mkdir(parents=True, exist_ok=True)
        for row in ctx["web"]:
            (web / row["dest"]).write_bytes(row["bytes"])

        notice = render_notice(ctx).encode("ascii")

        if licenses.exists():
            shutil.rmtree(licenses)
        staging.replace(licenses)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    (out_dir / NOTICE_NAME).write_bytes(notice)


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digests(root: pathlib.Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run_check(ctx) -> int:
    staging = pathlib.Path(tempfile.mkdtemp(prefix="plg-notices-check-"))
    try:
        write_outputs(ctx, staging)

        problems: list[str] = []

        fresh = tree_digests(staging / LICENSE_DIR_NAME)
        committed = tree_digests(REPO / LICENSE_DIR_NAME)

        for missing in sorted(set(fresh) - set(committed)):
            problems.append("license text not committed: %s" % missing)
        for extra in sorted(set(committed) - set(fresh)):
            problems.append("committed license text no longer derivable: %s" % extra)
        for shared in sorted(set(fresh) & set(committed)):
            if fresh[shared] != committed[shared]:
                problems.append("license text differs from its source: %s" % shared)

        generated = (staging / NOTICE_NAME).read_bytes()
        current = (REPO / NOTICE_NAME).read_bytes()
        if generated != current:
            problems.append(
                "%s differs from the regenerated notice "
                "(committed %d bytes, regenerated %d bytes)"
                % (NOTICE_NAME, len(current), len(generated))
            )

        if problems:
            print("DRIFT (%d):" % len(problems))
            for problem in problems:
                print("  - %s" % problem)
            print("\nRegenerated tree kept for inspection: %s" % staging)
            return 1

        print("OK: committed notices match the regenerated output exactly.")
        shutil.rmtree(staging, ignore_errors=True)
        return 0
    except Exception:
        print("Regenerated tree kept for inspection: %s" % staging)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--exe", type=pathlib.Path, default=EXE,
                        help="built artefact to read evidence from")
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="write here instead of a temp dir")
    parser.add_argument("--check", action="store_true",
                        help="compare against the committed outputs, write nothing")
    args = parser.parse_args(argv)

    try:
        ctx = collect(args.exe)
    except EvidenceError as exc:
        print("EVIDENCE ERROR: %s" % exc, file=sys.stderr)
        return 2

    if args.check:
        return run_check(ctx)

    out_dir = args.out or pathlib.Path(tempfile.mkdtemp(prefix="plg-notices-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    write_outputs(ctx, out_dir)

    rows = len(ctx["all_rows"])
    texts = sum(len(row["texts"]) for row in ctx["all_rows"])
    print("wrote %s" % (out_dir / NOTICE_NAME))
    print("wrote %s/ (%d python distributions, %d license files, %d fonts, %d web deps)"
          % (out_dir / LICENSE_DIR_NAME, rows, texts, len(ctx["fonts"]), len(ctx["web"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
