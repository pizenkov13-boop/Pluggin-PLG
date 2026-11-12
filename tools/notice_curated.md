<!-- Curated sections 6-8 of THIRD_PARTY_NOTICES.md.
     These are human judgement, not derivable facts: no script can conclude
     "NEEDS EXTERNAL REVIEW". tools/gen_third_party_notices.py appends this
     file verbatim, and fails the run if a runtime binary named in section 6
     is no longer present in the exe. Edit here, never in the generated file. -->

## 6. Runtime binaries embedded without their own license text

| Component | License (named) | Text status | Note |
|---|---|---|---|
| CPython 3.11 (`python311.dll`, stdlib `base_library.zip`) | PSF License | UNKNOWN - text not bundled | interpreter |
| OpenSSL 3 (`libcrypto-3.dll`, `libssl-3.dll`) | Apache-2.0 | canonical Apache-2.0 text ships at `third_party_licenses/cryptography-49.0.0/LICENSE.APACHE` (DLLs arrive via the cryptography wheel, same license family) | TLS |
| OpenBLAS (`numpy.libs/libscipy_openblas64_*.dll`) | BSD-3-Clause | UNKNOWN - text not bundled | arrives with numpy |
| .NET reference assemblies (`pythonnet/runtime/System.*.dll`) | MIT (.NET Runtime) | UNKNOWN - text not bundled | pywebview .NET backend |
| Microsoft WebView2 runtime DLLs (`webview/lib/*WebView2*`) | Microsoft redistributable | NEEDS EXTERNAL REVIEW | redistributed as part of pywebview wheels |
| MSVC runtime (`VCRUNTIME140*.dll`, `msvcp140*.dll`) | Microsoft VC++ redistributable terms | NEEDS EXTERNAL REVIEW | standard PyInstaller payload |
| pywin32 extras (Scintilla/MAPI/adodbapi texts) | various per component | full pywin32 license set copied to `third_party_licenses/pywin32-312/` | only `win32`/`win32com`/`pythoncom` parts are imported |

## 7. Declared but NOT shipped (no notice obligation for the artifact)

* `MIDIUtil` (requirements.txt) - absent from the exe.
* dev tools: `pytest`, `pyinstaller` (GPLv2 + bootloader exception), `ruff`.
* optional extras (`requirements-optional.txt`): `demucs`, `scikit-learn`,
  `music21`, **`pyflp` (GPL-3.0)** - never imported by shipped code; zero
  `pyflp`/`construct` modules in the PYZ (re-verified this round).
* The exe still carries server-side packages pulled in transitively
  (`fastapi`, `uvicorn`, `redis`, `aiohttp`) and `_pytest` - all
  permissively licensed and covered by the tables above; their presence is
  build-graph ballast documented in the shipping audit, intentionally left
  untouched this round.

## 8. Open items (UNKNOWN / NEEDS EXTERNAL REVIEW)

1. `proxy_tools` license text - not available locally; marked UNKNOWN.
2. OpenBLAS / PSF / .NET reference assemblies - license names recorded,
   canonical texts not bundled (no local canonical source to copy from).
3. WebView2 and MSVC runtime DLLs - governed by Microsoft redistributable
   terms; confirm before external DD presentation.
4. The repository still has no root `LICENSE` for the PLG code itself.
5. The website font Unbounded-700.ttf (landing/, not in the exe) is also
   OFL; its license text belongs with the website, not this artifact.


## 9. Trademarks

PLG writes FL Studio project files and names the product it is compatible
with. That is nominative use of someone else's mark, so it has to be stated:

* **FL Studio** and **Image-Line** are trademarks of Image-Line NV (Belgium).
* Pluggin' (PLG) is **not affiliated with, sponsored by, or endorsed by**
  Image-Line NV.
* PLG does not embed, bundle, or redistribute FL Studio. The user needs their
  own licensed copy of FL Studio to open the `.flp` files PLG produces.

This section is about the mark only.
