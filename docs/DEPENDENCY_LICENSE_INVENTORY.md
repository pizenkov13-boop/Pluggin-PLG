# Dependency & license inventory — DD-preparation

**Written 2026-08-27.** Scope: everything the shipped product (`PLG.exe`),
the cloud service and the public website depend on, with the license of each
item and the verification method used. Companion document:
[the asset inventory (not included)](the asset inventory (not included)) (non-code files). FLP byte-level
provenance lives in [../FLP_PROVENANCE.md](../FLP_PROVENANCE.md).

> **Round-2 update (2026-08-27, license-compliance remediation).** The
> shipping obligations below are now carried by the artifact itself:
> `THIRD_PARTY_NOTICES.md` + the canonical license texts of
> `third_party_licenses/` are bundled into `PLG.exe` (`PLG.spec` datas) and
> verified inside by `CArchiveReader`. The tables here were also re-checked
> against the **actual exe contents** (PYZ module table + data entries), not
> just against `requirements.txt`: `MIDIUtil` is declared but **not** bundled,
> and `mido`'s wheel in the build environment **does** carry its LICENSE
> (now shipped). See `../THIRD_PARTY_NOTICES.md`.

> **Round-3 update (2026-08-27, reproducibility).** `THIRD_PARTY_NOTICES.md`
> and `third_party_licenses/` are no longer hand-maintained: they are produced
> by `tools/gen_third_party_notices.py` from the built exe, the installed
> `dist-info` metadata, the TTF `name` tables and `web/node_modules`, and
> `--check` re-derives them and exits non-zero on drift. Regenerating found
> three things round 2 had missed: **`rpds-py` was shipped but absent from the
> notice entirely**, `sniffio`'s dual MIT/Apache-2.0 texts were not copied, and
> `motion`'s license source path was wrong (`LICENSE.md`, not `LICENSE`).
> Sections 6-8 of the notice stay human-curated in `tools/notice_curated.md`.

> Licenses below were read from the **installed package's own LICENSE file**
> (`importlib.metadata` → `dist-info/licenses/`) on the dev machine on
> 2026-08-27, not copied from memory. Installed versions are the versions
> verified; requirement pins are the declared floors. Two packages
> (`pydub`, `mido`) were originally listed as shipping no license file —
> corrected for `mido` in round 2 (its dist-info LICENSE ships in the exe).

---

## 1. Python runtime dependencies — `requirements.txt`

All of these are bundled into `PLG.exe` by PyInstaller (`PLG.spec`) whenever
the app imports them. **All 13 are permissively licensed. There is no
copyleft dependency in the shipped runtime.**

| Package | Pin | Installed | License | Verified via | Role in PLG |
|---|---|---|---|---|---|
| `google-genai` | >=1.0.0 | 2.10.0 | Apache-2.0 | dist-info LICENSE | LLM transport (optional path) |
| `anthropic` | >=0.49.0 | 0.112.0 | MIT | dist-info LICENSE | LLM transport (optional path) |
| `python-dotenv` | >=1.0.0 | 1.2.2 | BSD-3-Clause | dist-info LICENSE | `.env` config loading |
| `MIDIUtil` | >=1.2.1 | 1.2.1 | BSD-style | package `License.txt` | stem-MIDI export — ⚠️ **declared but NOT bundled** in the current exe (0 matching PYZ modules, verified 2026-08-27 round 2) |
| `pydub` | >=0.25.1 | 0.25.1 | MIT (upstream; wheel carries no license file) | upstream repo | sample processing |
| `Pillow` | >=10.0.0 | 12.2.0 | HPND (historical PIL license) | dist-info LICENSE | image handling (logos/themes) |
| `pywebview` | >=5.0 | 6.2.1 | BSD-3-Clause | dist-info LICENSE | desktop UI shell |
| `httpx` | >=0.27.0 | 0.28.1 | BSD-3-Clause | dist-info LICENSE | HTTP client (cloud, LLM) |
| `cryptography` | >=42.0.0 | 49.0.0 | Apache-2.0 **OR** BSD-3-Clause (dual) | dist-info LICENSE + LICENSE.APACHE + LICENSE.BSD | Ed25519 auto-update signature check |
| `sentry-sdk` | >=2.0.0 | 2.63.0 | MIT | dist-info LICENSE | optional crash reporting |
| `pretty_midi` | >=0.2.10 | 0.2.11 | MIT | dist-info LICENSE | MIDI analysis |
| `mido` | >=1.3.2 | 1.3.3 | MIT (the installed wheel's dist-info LICENSE is shipped in the exe — corrected round 2) | dist-info LICENSE inside exe | `.mid` reading |
| `numpy` | >=1.26.0 | 2.4.6 | BSD-3-Clause | dist-info LICENSE.txt | numeric arrays |

**Transitive dependencies** are not enumerated here. They can be regenerated
at any time with `pip-licenses --format=csv`; no copyleft package is known
to be reachable from the shipped code path.

## 2. Dev / build tools — `requirements-dev.txt` (not shipped)

| Package | Pin | License | Note |
|---|---|---|---|
| `ruff` | >=0.8.0 | MIT / Apache-2.0 (upstream) | linter; not installed in this venv |
| `pytest` | >=8.0.0 | MIT | dist-info LICENSE verified |
| `pyinstaller` | >=6.0.0 | GPLv2-or-later **with bootloader exception** | the exception explicitly permits building and distributing non-free/commercial programs; `PLG.exe` output is ours |

`tests/` is not part of the distribution (`PLG.spec` lists no `tests` entry —
verified by reading the built archive, see `FLP_PROVENANCE.md` §1).

## 3. Optional extras — `requirements-optional.txt` (user-installed, never bundled)

Installed only on demand by the user/developer; `PLG.spec` additionally
excludes `torch`/`torchaudio`/`torchvision`/`demucs`/`tensorflow` so a
transitive import can never drag them into the exe.

| Package | License | Note |
|---|---|---|
| `demucs` | MIT | stem splitter, archived feature |
| `scikit-learn` | BSD-3-Clause | beat-reward learning, off by default |
| `music21` | BSD-3-Clause | theory checks, experimental |
| **`pyflp`** | **GPL-3.0** | **the only copyleft package in the whole repository.** Dev cross-check tool only — never imported by any shipped code path. Proof (runtime import-blocker through a full 156 KB export) is in `FLP_PROVENANCE.md` §1 |
| `basic-pitch` / `laion-clap` | various (Apache-2.0 / MIT) | mentioned in the file header, each in its own venv. `magenta` was dropped 2026-08-27 with the GrooVAE route — the model weights it downloaded never had recorded licence, version or training-data provenance |

## 4. JavaScript dependencies shipped inside the exe via `web/dist`

`web/dist` is bundled whole into `PLG.exe` (`PLG.spec` `web_files`). Runtime
dependencies only — the build tools (vite, typescript, @types/*) do not ship:

| Package | Pin | License |
|---|---|---|
| `react` | ^18.3.1 | MIT |
| `react-dom` | ^18.3.1 | MIT |
| `motion` | ^12.42.2 | MIT |
| `lucide-react` | ^1.29.0 | ISC |

⚠️ **The current `web/dist` is stale relative to `web/src`.** The FL-support
strings were corrected in `web/src/i18n/locales/{en,ru}.ts` on 2026-08-27
("Works with FL 20–2025+" → "Verified on FL Studio 2025 (25.2.2)…"), but the
built bundle still contains the old text. A rebuild (`npm run build` in
`web/`) is required before the next release build; see the project issue log (not included) §0ae.

## 5. Cloud service — `cloud/requirements.txt` (server-side, not in the exe)

Separate FastAPI service, never bundled into the desktop binary:
`fastapi`, `uvicorn[standard]`, `supabase`, `yookassa`, `stripe`, `redis`,
`sentry-sdk[fastapi]` plus helpers. All permissively licensed (BSD/MIT/Apache)
per upstream; the service runs on our infrastructure, so no redistribution
question arises for it.

## 6. Open license obligations

1. ✅ **CLOSED 2026-08-27 (round 2):** the artifact now carries
   `THIRD_PARTY_NOTICES.md` + `third_party_licenses/` (canonical texts from
   installed dist-info metadata and upstream OFL 1.1) — bundled via `PLG.spec`
   and proven present by `CArchiveReader`. **CLOSED 2026-09-23:** the
   repository now has a root `LICENSE` for the PLG code itself.
2. ✅ **CLOSED 2026-08-27 (round 2):** the OFL license text now ships with the
   fonts (`third_party_licenses/fonts/OFL-1.1.txt` inside the exe); copyright
   statements taken from the TTF `name` tables of Anton / FjallaOne / Manrope.
   The website font (Unbounded) closed 2026-09-13: its text ships at
   `landing/assets/fonts/OFL-1.1.txt`, next to `Unbounded-700.ttf`.
3. ✅ **CLOSED 2026-09-23:** `tests/fixtures/FL_20.8.4.flp` is PyFLP's own test
   project (GPL-3.0, author `demberto`). It now carries an attribution file,
   `FL_20.8.4.flp.LICENSE.txt` (`FLP_PROVENANCE.md` §1). Still not shipped.
4. **`assets/starter/PLG_Samples/`** — closed 2026-08-27: the folder is no
   longer bundled into the exe (`PLG.spec`, the project issue log (not included) §0af). The
   13 commercial-pack WAVs remain in dev checkouts only.
5. ✅ **certifi / Mozilla CA bundle** — attribution requirement closed round 2:
   the package's own MPL-2.0 notice ships at
   `third_party_licenses/certifi-2026.6.17/LICENSE`.
6. ⚠️ Remaining runtime-binary items without bundled texts: PSF Python,
   OpenBLAS, .NET reference assemblies (UNKNOWN), WebView2 + MSVC runtime
   DLLs (NEEDS EXTERNAL REVIEW — Microsoft redistributable terms). See
   `../THIRD_PARTY_NOTICES.md` §6 and §8.
