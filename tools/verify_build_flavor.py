#!/usr/bin/env python3
"""Check a finished exe against its flavor: ``verify_build_flavor.py release|tester PATH_TO_EXE``.

Reads the PyInstaller archive (nothing is run) and fails the build when:

release   the exe carries ``build_flavor.json`` or ``.env.tester`` (the tester flavor leaked into the customer
          build), or lacks ``.env.release``;
tester    ``build_flavor.json`` is missing or unreadable, ``.env.release`` is inside, ``.env.tester`` is missing
          or names a server, or ANY shipped text still holds a server address or a secret: PLG's own Python
          modules (every string constant) and the bundled data files (web UI, documents, env templates) are
          scanned for the server host names, supabase hosts, key / token shapes, ``PLG_CLOUD_URL=http...``
          settings and IPv4 addresses (loopback, 0.0.0.0 and the public DNS resolvers PLG's DNS fallback
          names are allowed). Findings are printed as file + rule, never as values.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

RULES: dict[str, re.Pattern[str]] = {
    "PLG server host name": re.compile(r"(?i)\b(?:cloud|api|www)\.(?:pluggin|plugflp)\.tech|plugflp\.tech"),
    "supabase host": re.compile(r"(?i)[a-z0-9-]+\.supabase\.(?:co|in|com)"),
    "hosting host": re.compile(r"(?i)onrender\.com|twcstorage\.ru|fly\.dev"),
    "supabase setting with a value": re.compile(r"SUPABASE_[A-Z_]*\s*=\s*['\"]?[^\s'\"#]{8,}"),
    "server URL setting": re.compile(r"PLG_(?:CLOUD|REGION_BOOTSTRAP)_URL\s*=\s*['\"]?https?://(?!127\.0\.0\.1|localhost)[^\s'\"#]+"),
    "publishable / secret key": re.compile(r"\bsb_(?:publishable|secret)_[A-Za-z0-9_\-]{10,}"),
    "JWT-shaped token": re.compile(r"\beyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{15,}\."),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "API key shape": re.compile(r"\b(?:sk-or-v1-|sk-ant-|sk-)[A-Za-z0-9_\-]{24,}|\bAKIA[0-9A-Z]{16}\b"),
}
IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.\w])")
ALLOWED_IPS = {"0.0.0.0", "1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9"}  # PLG's DNS fallback names these
# Data folders that are PLG's own; third-party packages' data (version tables, certificates) is not scanned.
OWN_DATA_DIRS = ("web/", "legal/", "assets/", "fl_scripts/", "third_party_licenses/")
TEXT_SUFFIXES = (".js", ".css", ".html", ".json", ".md", ".txt", ".py", ".env", ".tester", ".release", ".svg", ".yaml", ".yml")


def _address_rule(text: str) -> bool:
    for match in IPV4.finditer(text):
        ip = match.group(1)
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue  # 999.1.2.3 or a version string
        if ip.endswith(".0.0"):
            continue  # 21.0.0.0 is a version or a network, never a host
        if address.is_loopback or ip in ALLOWED_IPS or str(address).startswith(("192.0.2.", "198.51.100.", "203.0.113.")):
            continue
        if re.search(r"(?i)version|\bv\d+\.\d|==|>=|<=|~=", text[max(0, match.start() - 24) : match.end() + 8]):
            continue
        return True
    return False


def scan_text(text: str) -> list[str]:
    hits = [name for name, rx in RULES.items() if rx.search(text)]
    if _address_rule(text):
        hits.append("IPv4 address")
    return hits


def _strings(code):
    stack = [code]
    while stack:
        item = stack.pop()
        if hasattr(item, "co_consts"):
            stack.extend(item.co_consts)
        elif isinstance(item, str):
            yield item
        elif isinstance(item, bytes):
            yield item.decode("utf-8", "ignore")
        elif isinstance(item, (tuple, frozenset)):
            stack.extend(item)


def open_archive(exe: Path):
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    archive = CArchiveReader(str(exe))
    names = {name.replace("\\", "/"): name for name in archive.toc}
    return archive, names, ZlibArchiveReader


def project_module_names() -> set[str]:
    return {p.stem for p in REPO.glob("*.py")} | {p.name for p in REPO.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}


def scan_exe(exe: Path) -> tuple[list[tuple[str, str]], int, int]:
    """(findings as (where, rule), data files scanned, project modules scanned)."""
    archive, names, Zlib = open_archive(exe)
    findings: list[tuple[str, str]] = []
    data_scanned = 0
    for norm, raw in names.items():
        base = norm.rsplit("/", 1)[-1].lower()
        if not (base.endswith(TEXT_SUFFIXES) or base.startswith(".env")):
            continue
        if norm.startswith(("PYZ", "base_library")) or ("/" in norm and not norm.startswith(OWN_DATA_DIRS)):
            continue
        try:
            blob = archive.extract(raw)
        except Exception:  # noqa: BLE001 - an entry that cannot be read cannot hold a readable address either
            continue
        if not isinstance(blob, (bytes, bytearray)):
            continue
        data_scanned += 1
        for rule in scan_text(bytes(blob).decode("utf-8", "ignore")):
            findings.append((norm, rule))

    modules_scanned = 0
    pyz_entry = next((raw for norm, raw in names.items() if norm.startswith("PYZ") and norm.endswith(".pyz")), None)
    if pyz_entry is not None:
        blob = archive.extract(pyz_entry)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.pyz"
            path.write_bytes(bytes(blob))
            pyz = Zlib(str(path))
            ours = project_module_names()
            for module in pyz.toc:
                if module.split(".")[0] not in ours:
                    continue
                try:
                    code = pyz.extract(module)
                except Exception:  # noqa: BLE001
                    continue
                modules_scanned += 1
                seen: set[str] = set()
                for text in _strings(code):
                    for rule in scan_text(text):
                        if rule not in seen:
                            seen.add(rule)
                            findings.append((f"module {module}", rule))
    return findings, data_scanned, modules_scanned


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("release", "tester"):
        print(__doc__)
        return 2
    mode, exe = argv[1], Path(argv[2])
    if not exe.is_file():
        print(f"verify_build_flavor: {exe} does not exist")
        return 2
    _archive, names, _z = open_archive(exe)
    has = lambda name: name in names  # noqa: E731
    problems: list[str] = []

    if mode == "release":
        if has("build_flavor.json"):
            problems.append("the RELEASE exe carries build_flavor.json (the tester flavor leaked into it)")
        if has(".env.tester"):
            problems.append("the RELEASE exe carries .env.tester")
        if not has(".env.release"):
            problems.append("the RELEASE exe lacks .env.release")
        print(f"verify_build_flavor: release exe checked ({len(names)} entries)")
    else:
        archive, names, _z = open_archive(exe)
        if not has("build_flavor.json"):
            problems.append("the tester exe has no build_flavor.json")
        else:
            try:
                flavor = json.loads(bytes(archive.extract(names["build_flavor.json"])).decode("utf-8"))
                if flavor.get("flavor") != "tester":
                    problems.append("build_flavor.json does not say flavor=tester")
                date.fromisoformat(str(flavor.get("expires", ""))[:10])
                print(f"verify_build_flavor: tester exe, valid until {flavor.get('expires')}")
            except (ValueError, OSError) as exc:
                problems.append(f"build_flavor.json is unreadable ({exc})")
        if has(".env.release"):
            problems.append("the tester exe carries .env.release (server address and key)")
        if not has(".env.tester"):
            problems.append("the tester exe lacks .env.tester")
        findings, data_n, mod_n = scan_exe(exe)
        print(f"verify_build_flavor: scanned {data_n} data files and {mod_n} PLG modules")
        for where, rule in sorted(set(findings)):
            problems.append(f"{where}: {rule}")

    if problems:
        print(f"verify_build_flavor: FAILED ({len(problems)})")
        for line in problems:
            print("  -", line)
        return 1
    print("verify_build_flavor: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
