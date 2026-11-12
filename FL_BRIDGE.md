# FL Bridge — how OPEN IN FL works

**Goal:** Generate → OPEN IN FL → FL Studio opens with the seven active
MIDI-first lanes already containing the pattern notes:

1. **PLG MIDI** (`melody_lead`)
2. **PLG Kick** (`kick`)
3. **PLG 808 Bass** (`808_bass`)
4. **PLG Hi-Hats** (`hi_hats`)
5. **PLG Clap** (`clap`)
6. **PLG Snare** (`snare`)
7. **PLG Snare Layer** (`snare_layer`)

No manual drag. No File → Import dialog. No "run a script per layer".

---

## The approach: PLG writes a `.flp` and FL opens it

FL Studio reliably opens a project file passed on the command line:

```
FL64.exe C:\newPLUG\PLG_Session.flp
```

So PLG generates a valid `.flp` and hands it to FL. That is the whole bridge.
There are two entry points and they take the pattern from different places:

```
Create beat:  in-memory pattern  ──▶ flp_writer.write_flp_session() ──▶ PLG_Session.flp
Open in FL:   output_pattern.json ──▶ fl_launch.open_beat_in_fl()   ──▶ PLG_Session.flp ──▶ FL64.exe <file>
```

`plg_api.create_beat` writes the canonical session directly from the pattern it
just validated — it does not round-trip through `output_pattern.json`.
`open_in_fl` is the one that reloads the saved JSON. `build_flp()` is the byte
serializer both paths end in.

All seven lanes are **Sampler channels with notes** (2026-08-27: the native-FLEX
melody route is disabled — its captured-state blobs were removed from the tree
and the bundle, the project issue log (not included) §0ag; the melody is an editable Sampler
channel and the producer assigns their own synth — FLEX, Serum, Omnisphere,
Kontakt, anything — after opening the FLP). When your kit folder is
empty, PLG embeds **starter wav paths** (`assets/starter/`) so FL loads audible
sounds out of the box. When you add your own library, those paths are used
instead — swap anytime for *your sound*.

Melody caveat (2026-08-27): the bundled `PLG_Samples` melodic pool is **no
longer shipped** in the exe (the project issue log (not included) §0af). Without it, a melody
with no library match keeps the bundled starter
one-shot (`PLG_starter_melody.wav`) that `resolve_track_samples` seeds —
nothing from the old pool is substituted (the project issue log (not included) §0af).

> Bundled-starter caveat, verified by a live run on 2026-08-11: the bundled
> fallback 808 is short enough that `library_quality.bass_808_tail_rejection_reason`
> rejects it, the `808_bass` lane is dropped, and `midi_validate.enforce_midi_mode`
> then blocks the export outright. An empty/unreachable sample library is
> therefore a hard generation failure, not a degraded beat. See
> the project issue log (not included).

### Why not the other approaches?

Everything below was tried and abandoned (see git history / the spec):

| Approach | Why it failed |
|---|---|
| `File → Import → MIDI` keystroke automation | Fragile: RU/EN menus, focus loss, custom dialogs. This is what produced `imported=False method=explorer_fallback`. |
| `WM_DROPFILES` / simulated drag onto FL | FL uses OLE drag-and-drop; the messages are ignored. |
| `FL64.exe PLG_Beat.mid` (MIDI on argv) | FL opens but ignores the MIDI. |
| `.mid` file association | Points at Media Player, not FL. |
| PyFLP parsing the user's `.flp` | `EventEnum` parse error on FL 2025 files (confirmed: PyFLP 2.2.1 crashes on the first event under Python 3.12). |
| Piano-roll script alone | Can only write the **current** channel — cannot create the seven channels a session needs. |
| MIDI controller (device) script | The `channels` API has no documented "create channel + write pattern notes" path, and it needs a one-time MIDI-port assignment (loopMIDI) — friction we explicitly wanted to avoid. |

Writing a project file sidesteps all of it.

---

## File format (for maintainers)

`flp_writer.py` emits the FLP TLV event stream directly (no third-party dep):

* **Header** `FLhd` + `u32 size(=6)` + `i16 format(=0)` + `u16 channel_count` + `u16 ppq(=960)`
* **Data** `FLdt` + `u32 size` + event stream
* **Events** — id `0-63`→1 byte, `64-127`→2 bytes (LE), `128-191`→4 bytes (LE),
  `192-255`→variable (LEB128 length, then bytes)

Events written, in order:

| Event | ID | Value |
|---|---|---|
| Version | 199 (TEXT+7) | `fl_detect.FLP_HEADER_VERSION` = `"21.0.0.0"` (ASCII) |
| Tempo | 156 (DWORD+28) | `bpm * 1000` |
| New channel ×N | 64 (WORD) | channel index, one per active lane |
| Channel type ×N | 21 | `0` (Sampler) |
| Channel name ×N | 192 (TEXT) | UTF-16-LE name |
| New pattern | 65 (WORD+1) | pattern index |
| Pattern name | 193 (TEXT+1) | e.g. `"PLG Snare Layer Main 5"` |
| Notes | 224 (DATA+16) | N × 24-byte note struct |

`N` is the active-lane count, not 3. A normal MIDI-mode session writes the
seven lanes listed at the top of this file and one pattern per arrangement
section per lane (a 2026-08-11 control run produced 60 Playlist clips over
7 lanes).

Note struct (24 bytes, little-endian): `position u32, flags u16, rack_channel
u16, length u32, key u16, group u16, fine_pitch u8, _u1 u8, release u8,
midi_channel u8, pan u8, velocity u8, mod_x u8, mod_y u8`. Neutral values:
pan 64, velocity 100 (or the note's own), mod_x/mod_y 128, fine_pitch 120,
release 64.

**Timing:** `time_step` 1.0 = 1 beat. In the `.flp`, ticks = `time_step * 960`
(`flp_writer.PPQ`). 0.25 → 240 ticks, 4.0 → 3840 ticks.

**Two different PPQ scales.** The project header and note/pattern positions use
`PPQ = 960`. Playlist *clip lengths* are stored on a separate fixed 96-PPQ
scale (`flp_writer._PLAYLIST_LENGTH_PPQ = 96`). Using 960 for clip length made
every Playlist cube 10× too wide. Do not "unify" these two constants.

The event IDs and note layout are taken from the PyFLP project (the most current
open FLP reference). Several body blobs — the Playlist-track record, the
arrangement trailer (event 242) and the note flag/midi-channel defaults — are
captured verbatim from a real FL Studio 25.2.2 session; see the comments around
`flp_writer.py:150`, `:155` and `:168`.

---

## Python entry points

* `fl_launch.open_beat_in_fl(project_dir)` — exports MIDI (kept for manual/other
  DAWs), writes `PLG_Session.flp`, installs the FL scripts, finds `FL64.exe`,
  launches it with the `.flp`. Returns:

  ```python
  {
    "fl_exe": Path, "midi": Path, "flp": Path, "script": Path,
    "pattern": Path, "imported": True,
    "import_method": "flp_session", "import_configured": True,
  }
  ```

* `fl_setup.install_all(project_dir)` — installs the single-layer importer
  (`PLG Pluggin'.pyscript`, source `plugin_script.py`) **and** the V2 script
  pack (see FL_SCRIPTS.md).
* `fl_setup.is_fl_bridge_ready(project_dir)` — for the GUI setup strip.
* `flp_writer.write_flp_session(data, path)` — pattern dict → `.flp`.
* `plg_api.create_beat` calls `write_flp_session` itself right after generation,
  so a session exists before the user ever presses OPEN IN FL.

---

## One-time setup

**None.** The `.flp` carries everything. The first OPEN IN FL also installs the
piano-roll scripts automatically.

---

## Re-running OPEN IN FL (second beat)

Each CREATE BEAT rewrites `output_pattern.json`; each OPEN IN FL regenerates
`PLG_Session.flp` and launches FL with it.

* **FL not running** → opens fresh with the new beat.
* **FL already running** → FL is single-instance, so it prompts to save the
  current project, then loads the new `PLG_Session.flp`. No duplicate/ghost
  channels — it is a clean project load, not a merge.

---

## Verify it yourself (FL 2025)

1. Put a MIDI file in `PLG_MIDI`, then hit **Generate**.
2. Hit **OPEN IN FL**.
3. FL Studio 2025 launches and loads `PLG_Session.flp`.
4. **Expected:** the Channel Rack/Playlist contains MIDI, Kick, 808 Bass,
   Hi-Hats, Clap, Snare and Snare Layer. Open a pattern in the piano roll to
   inspect its notes.
5. Drag your own samples onto the named channels, then press play.

Quick check without the app:

```powershell
python flp_writer.py output_pattern.json      # writes PLG_Session.flp
& "C:\Program Files\Image-Line\FL Studio 2025\FL64.exe" "C:\newPLUG\PLG_Session.flp"
```

> **Status:** the writer is structurally verified, but "opens cleanly in FL
> 2025" should be confirmed on your machine with the steps above — that is the
> one thing this repo cannot self-test. If FL reports a problem loading the
> file, grab `plg_session.log` and the FL error text.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "FL Studio not found" | Install FL, or set the path in Settings. Discovery lives in `fl_detect.py` (env override → `user_profile.json` → registry → filesystem scan). |
| FL opens empty | Confirm `PLG_Session.flp` exists in the project folder and is non-zero. Re-run CREATE BEAT first. |
| Channels there but silent | Expected — load your own sounds onto them (that's the product). For a quick test, drop any sample or add 3xOsc. |
| Notes look off-grid | Check `time_step` in the JSON (1.0 = 1 beat). |
| Want all lanes on one channel | Use the **PLG Import ALL** piano-roll script — it now imports all seven active lanes (FL_SCRIPTS.md). |
| "Invalid AI gateway API key" right after Create | Probably **not** the key. `plg_api.create_beat` reports every pipeline exception through `llm_client.format_llm_error`, which matches the substring `INVALID`. Read `logs/`+`plg_session.log` for the real error. See the project issue log (not included). |

---

## Fallback

If `.flp` generation ever fails, `open_beat_in_fl` raises rather than launching
an empty FL. `output_midi/PLG_Stems_<bpm>_<style>/PLG_Beat.mid` still contains
the combined session for a manual `File → Import → MIDI`.

The piano-roll importers (`PLG Pluggin'` / **PLG Import ALL**) were brought up
to the seven-lane contract on 2026-08-11 and can serve as a manual fallback
again. See FL_SCRIPTS.md.

*Legacy note:* the `explorer_fallback` branch + drag messagebox in
`plg_app._on_fl_opened` belongs to the tkinter fallback UI, which
`run_plg.bat` only starts when pywebview fails to launch.
