# FLP writer — provenance of every structure

**Purpose.** PLG writes FL Studio `.flp` project files. This document states,
for every structure in that writer, where the knowledge or the bytes came from.
It exists so the question *"is any of this someone else's work?"* has a written
answer with a `file:line` for each row, rather than a verbal one.

Written 2026-08-27. Re-verify the line numbers with `grep` — they drift.

---

## The three categories

| | Meaning |
|---|---|
| **A — independent** | Our own code and our own numbers. Musical decisions, and the parts of the format we established by our own experiment. |
| **B — format fact** | Numeric event IDs and field layouts of the `.flp` container, documented publicly by the PyFLP project. Facts about a file format, not copied code. |
| **C — captured bytes** | Byte sequences lifted from a real FL Studio installation or an FL-authored project. This is Image-Line's data. |

Category C is the only one with redistribution exposure. It is enumerated in
full below — nothing is omitted.

---

## 1. Independence from PyFLP — proven, not asserted

PyFLP is licensed **GPL-3.0**. It is *not* a dependency of the shipped product.

**Runtime proof.** A loader was installed in `sys.meta_path` that raises
`ImportError` on any attempt to import `pyflp`, and then a complete session was
serialized through the production path (`flp_writer`, `flp_template`,
`mix_gain_staging`, `mix_insert_fx`, `midi_validate`, `backend_core`, `plg_api`,
`arranger`, `beat_humanize`):

```
build_flp OK, 156329 bytes
pyflp in sys.modules after full export: NONE
construct (pyflp's dep) loaded:         NONE
```

**Every mention of PyFLP in the repository** — 25 total (excluding this file),
none of them an import in shipped code:

| Kind | Where |
|---|---|
| Comments in production modules (15) | `flp_writer.py:25 :70 :86 :103 :127 :166 :173 :184 :187 :339 :1366 :1559 :1561` (13), `mix_gain_staging.py:20 :102` (2) |
| Documentation (3) | `FL_BRIDGE.md:61 :110`, `FL_VERSIONS.md:48` |
| Optional extra, never installed by the app (3) | `requirements-optional.txt:7 :33`, `requirements.txt:18` |
| **Test files only** (4) | `tests/test_flp_writer.py:695 :1126 :1154 :1167` — `from pyflp.channel import ParametersEvent, StretchMode` |

`tests/` is not part of the distribution (verified by reading the built
`PLG.exe` archive — see `PLG.spec`, which lists no `tests` entry). PyFLP is
used as a *development cross-check tool*, the same way a linter is.

**Closed 2026-09-23:** `tests/fixtures/FL_20.8.4.flp` (190 KB) is PyFLP's own
test project — it contains the strings `PyFLP Test FLP`, `demberto`, and
`https://github.com/demberto/PyFLP`. It is *not* shipped. It now carries an
attribution file, `tests/fixtures/FL_20.8.4.flp.LICENSE.txt` (source, author,
GPL-3.0, link to the upstream repo).

---

## 2. The structure-by-structure table

### Written into every generated `.flp`

| Structure | file:line | Cat. | Source, verbatim from the code |
|---|---|---|---|
| `FLhd` header, TLV stream, varint lengths | [`flp_writer.py:25`](flp_writer.py:25) | **B** | "grounded in the PyFLP project" |
| Event-ID bases (`_BYTE`/`_WORD`/`_DWORD`/`_TEXT`/`_DATA`) + ~45 concrete IDs | [`flp_writer.py:70`](flp_writer.py:70)–`:143` | **B** | "see PyFLP `_events.py`" |
| `PPQ = 960` | [`flp_writer.py:66`](flp_writer.py:66) | **A** | our resolution choice |
| 24-byte note struct, neutral field values | [`flp_writer.py:166`](flp_writer.py:166) | **B** | "PyFLP NotesEvent defaults" |
| `_MIDI_CH = 128`, `_NOTE_FLAG_SELECTABLE = 1<<14` | [`flp_writer.py:173`](flp_writer.py:173)–`:190` | **A** ⭐ | PyFLP's documented default was **wrong**; corrected by our own experiment in a live FL 25.2.2 session. Bit 14 is absent from PyFLP entirely |
| `_NOTE_FLAG_SLIDE = 1<<3` | [`flp_writer.py:339`](flp_writer.py:339) | **B** | "PyFLP's reverse-engineered `_NoteFlags.Slide`" |
| Playlist clip struct (60 B/item), `_PLAYLIST_LENGTH_PPQ = 96` | [`flp_writer.py:1366`](flp_writer.py:1366)–`:1378` | **B + A** | struct "New in FL 21 per pyflp"; PPQ=96 found by **our** experiment (clips were 10× too long) |
| `_MELODY_STRETCH_PARAMS`, `_SUB_808_STRETCH_PARAMS` (158 B each) | [`flp_writer.py:235`](flp_writer.py:235), [`:269`](flp_writer.py:269) | **C** | cross-checked against `FL_20.8.4.flp` + FL 2025 factory `NewStuff.flp` |
| `_TRACK_TAIL` (66 B × 500 playlist tracks = 33 000 B) | [`flp_writer.py:154`](flp_writer.py:154) | **C** | "captured verbatim from FL 2025" |
| `_ARR_EXTRA_PAYLOAD` (event 242, 28 B) | [`flp_writer.py:159`](flp_writer.py:159) | **C** | "captured verbatim from FL 2025" |
| `_INSERT_FLAGS_LEFT` / `_INSERT_FLAGS_MASTER` (12 B each) | [`flp_writer.py:220`](flp_writer.py:220) | **C** | flags from a real FL project |
| Fruity Soft Clipper wrapper + data (52 + 8 B) | [`flp_writer.py:226`](flp_writer.py:226) | **C** | "FL 20.8.4 wire order" |
| **FLEX wrapper + plugin state (52 + 2707 B)** | `assets/flex/*.bin` — **removed from the tree 2026-08-27** (the project issue log (not included) §0ag) | **C** | "captured from `flp_templates/sidechain_pump.flp`" — serialized state of Image-Line's FLEX generator. No longer shipped and no longer emitted: the melody channel exports via the Sampler path |
| **Mixer reference params (48 780 B)** | `assets/plg_mixer_reference_params.bin`, [`mix_gain_staging.py:272`](mix_gain_staging.py:272) | **C?** ⚠️ | "captured from the verified reference project" — **this classification is disputed, see the correction below** |
| `_envlfo()` 17×int32 envelope struct | [`flp_writer.py:1558`](flp_writer.py:1558) | **B + C** | "struct layout = PyFLP"; baseline values "captured VERBATIM from a real FL save" |
| Envelope values for 808 / kick / clap / melody | [`flp_writer.py:1578`](flp_writer.py:1578)–`:1618` | **A** | our musical numbers on top of the C-category template |
| Mixer insert-EQ frequency anchors (90 Hz↔5777, 1500 Hz↔33145) | [`mix_gain_staging.py:102`](mix_gain_staging.py:102) | **B** | "PyFLP defaults" |
| `_full_mixer_params_baseline()` full 127-insert matrix | [`mix_gain_staging.py:205`](mix_gain_staging.py:205) | **A** | **constructed by a loop**, not a byte dump |
| Polyphony / cut-group words (4–5 B) | [`flp_writer.py:241`](flp_writer.py:241) | **A** | `struct.pack` of our own values |
| All musical content — arrangement, density, 808 harmony, humanize, sections | `arranger.py`, `beat_humanize.py`, `drum_density.py`, `midi_*.py` | **A** | ours |

### Present in the repository, gated OFF at runtime

| Structure | file:line | Cat. | Gate |
|---|---|---|---|
| Fruity Reeverb 2 wrapper + state (52 + 53 B) | [`mix_insert_fx.py:283`](mix_insert_fx.py:283), `:305` | **C** | `PLG_REVERB_INSERT`, default `off` |
| Fruity Delay 3 (52 + 156 B) | [`mix_insert_fx.py:287`](mix_insert_fx.py:287), `:309` | **C** | `PLG_DELAY_INSERT`, default `off` |
| Fruity Blood Overdrive (52 + 36 B) | [`mix_insert_fx.py:291`](mix_insert_fx.py:291), `:314` | **C** | `PLG_INSERT_SPACE_FX`, default `off` |
| Maximus (52 + 1371 B) | [`mix_insert_fx.py:299`](mix_insert_fx.py:299), `:317` | **C** | `PLG_MAXIMUS_INSERT`, default `off` |

Quarantine verified: all four env gates default to `off`; the only code that
could bypass them (`apply_variant_mix_macros`) was called exclusively from
`archive/rebake/`, which was off the active route and was deleted entirely on
2026-09-23 (IP-hygiene cleanup — `legal/IMAGE_LINE_IP_QUESTIONS_RU.md` §1.4).

### Generated at runtime, not shipped

| Artefact | Note |
|---|---|
| `assets/plg_template.flp` | produced by our own `build_template_flp()` ([`flp_template.py:684`](flp_template.py:684)). Confirmed **absent** from the built `PLG.exe`. Contains the C-category Soft Clipper blob |

---

## 2a. ⚠️ Correction, 2026-08-27 — the mixer blob is probably NOT captured

The first version of this document put `assets/plg_mixer_reference_params.bin`
in category C and named it the single largest redistribution exposure. Evidence
found the same day says that is likely wrong, and it is corrected here rather
than quietly amended.

Decoding `tests/fixtures/FL_20.8.4.flp` — a genuine FL-authored project — shows
FL writes `MixerParams` at base **`0x2000`**, never at the `0x7000` this repo
uses. The asset sits at `0x7000`; its EQ frequencies equal `eq_freq_msg()` to
the unit; its fader values equal `DEFAULT_MIXER_FADER_VALUES` to the unit; its
param-id profile is identical to `_full_mixer_params_baseline`'s output.

That is the signature of **a dump of this project's own output**, not of
Image-Line data. The docstring calling it a capture appears to be as inaccurate
as the neighbouring `newmixer123.flp` comment, which the FL-authored file in
this repo directly contradicts.

Consequences, none of them yet acted on:

- if confirmed, the file carries **no third-party exposure at all** and the
  category-C byte total below drops from ≈85 000 to ≈36 000;
- it is also inert — FL has never applied it, see the project issue log (not included) §0ad;
- the same doubt should be applied to every other constant whose only evidence
  of provenance is a comment saying "captured". The comments in this repository
  have now been wrong twice about the same subsystem.

**Do not present the category-C total below as final until this is settled.**

## 3. Category C — the honest summary

Total captured bytes in one generated `.flp`: **≈ 82 200** (the FLEX
wrapper + state, 2 759 B, was removed 2026-08-27 — the project issue log (not included) §0ag),
of which:

| Payload | Bytes | Gated? |
|---|---|---|
| Mixer reference params ⚠️ *classification disputed — see §2a* | 48 780 | no |
| `_TRACK_TAIL` × 500 | 33 000 | no |
| Stretch params ×2 | 316 | no |
| Soft Clipper | 60 | no |
| Arrangement trailer | 28 | no |
| Insert flags ×2 | 24 | no |

### ⚠️ The provenance of category C is not reproducible

`flp_templates/sidechain_pump.flp` — named in [`flp_writer.py:108`](flp_writer.py:108)
and [`:1559`](flp_writer.py:1559) as the source of the FLEX state and the
envelope baselines — **does not exist in this repository**; the directory is
absent. The same is true of the FL 2025 factory `NewStuff.flp` referenced at
[`flp_writer.py:257`](flp_writer.py:257).

Not redistributing them is correct. The consequence is that the category-C
blobs **cannot be regenerated from their source**, and the question "show us
where these bytes came from" has no answer beyond these comments.

### Reduction path, in order of cost

1. **Mixer reference params (48 780 B)** — removable. `_full_mixer_params_baseline`
   is a category-A reimplementation that produces the same structure; measured
   against the capture on the routing the export actually uses, it agrees on
   **4032 shared records with zero differing values**. Blocked only on an
   addressing fix in `_mixer_param_item`.
2. **Soft Clipper, insert flags, arrangement trailer, stretch params** (≈ 430 B)
   — small, field-documented, reimplementable from the existing comments.
3. **`_TRACK_TAIL`** (33 000 B) — 66 bytes repeated 500×; needs the fields named
   before it can be constructed rather than dumped.
4. **FLEX state (2 759 B)** — **closed 2026-08-27 by removal**
   (the project issue log (not included) §0ag): the blobs are gone from the tree and the bundle;
   the melody lane exports via the Sampler path. If native-FLEX export is
   ever wanted again, it must be rebuilt first-party, not restored from the
   captured bytes.

---

## 4. Third-party assets in the distribution

Separate from the writer, but part of the same question.

| Asset | Origin | In `PLG.exe` | Status |
|---|---|---|---|
| `assets/starter/PLG_Samples/` — 13 WAV | third-party packs — **commercial sample packs** | ❌ **removed from the bundle 2026-08-27** (`PLG.spec` no longer lists the folder; the project issue log (not included) §0af) | ✅ closed — the files stay in dev checkouts only; their licenses never granted redistribution inside software |
| `assets/starter/bundled_sounds/` — 17 files | synthesized by [`build_bundled_sounds.py`](build_bundled_sounds.py) | ✅ | ✅ ours |
| `assets/starter/PLG_starter_*.wav` — 6 files | `"source": "plg_synth"` | ✅ | 🟡 provenance asserted only by a JSON field |
| `assets/fonts/Anton-Regular.ttf` | Google Fonts, SIL OFL 1.1 | ✅ | 🟠 OFL requires the licence text to ship; it does not |
| `tests/fixtures/FL_20.8.4.flp` | PyFLP test project (GPL-3.0) | ❌ not shipped | ✅ closed 2026-09-23 — attribution added (`FL_20.8.4.flp.LICENSE.txt`) |

The repository now has a root `LICENSE` file for PLG's own code (closed
2026-09-23); third-party notices ship separately via `THIRD_PARTY_NOTICES.md`
and `third_party_licenses/`.

---

## 5. How to re-verify this document

```bash
# PyFLP is never imported by the export path
python -c "
import sys
class B:
    def find_module(self, n, p=None):
        if n.split('.')[0] == 'pyflp': raise ImportError(n)
sys.meta_path.insert(0, B())
import json, flp_writer
flp_writer.build_flp(json.load(open('output_pattern.json', encoding='utf-8')))
print('no pyflp:', not [m for m in sys.modules if m.startswith('pyflp')])
"
```

```bash
python -c "
from PyInstaller.archive.readers import CArchiveReader
r = CArchiveReader('dist/PLG.exe')
for k in sorted(r.toc):
    if k.lower().startswith('assets'): print(k, r.toc[k][1])
"
```

Every captured constant carries the word `captured` in its comment, so the
category-C inventory can be regenerated with:

```bash
grep -rniE "captured|verbatim|real FL|factory .fst" --include=*.py . | grep -v tests/ | grep -v archive/
```
