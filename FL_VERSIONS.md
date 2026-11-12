# FL Studio — supported versions

Pluggin' opens beats via **`.flp` project files** and optional **FL Scripts** (Piano roll import).

> **Current policy (2026-09-13).** PLG targets **FL Studio 25 and newer**
> only. Both FL Studio 25.2.2 and FL Studio 26 are manually verified (real
> generated projects open, play, and survive playlist resize correctly —
> 25.2.2 reconfirmed after reverting the FL21/24 compatibility work below;
> 26 verified separately the same way). **FL Studio 24 and older are not
> supported, and no further engineering time is being spent on them** — see
> the archived investigation below for why, and don't repeat it.

## Compatibility matrix

| FL product name | PLG status |
|-----------------|-------------|
| FL Studio 24 and older | **Not supported.** No compatibility work is planned or in progress. |
| FL Studio 25 (25.2.2) | ✅ **Verified** — manual smoke test: opens, plays, playlist resize stable |
| FL Studio 26 | ✅ **Verified** — manual smoke test: opens, plays, playlist resize stable |

**Not supported:** FL Studio Mobile, FL Studio Fruity Edition without full project export, beta/nightly builds, and (per the policy above) FL Studio 24 and any earlier version.

## How PLG finds FL (Windows)

Priority:

1. **`PLG_FL_EXE`** in `.env` (optional override)
2. **`fl_executable`** in `user_profile.json` (Settings → FL Studio)
3. **Windows registry** — all Image-Line installs
4. **Filesystem scan** — `Program Files\Image-Line\FL Studio *\FL64.exe` (+ drives D–Z)

If multiple versions exist, **newest** wins unless you pick one in Settings. On a machine with only FL Studio 24 or older, PLG-generated projects are not expected to work correctly — install FL Studio 25 or newer.

`.flp` files carry a conservative `fl_detect.FLP_HEADER_VERSION = "21.0.0.0"` header tag. This is legacy — it predates the FL Studio 25+-only policy above and was never revisited, since the header tag alone does not change what actually works. Not a support claim for FL 21.

## Without FL Studio

You can still generate beats; the stem MIDI lands in `output_midi/PLG_Stems_<bpm>_<style>/` for manual import into any DAW.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "FL Studio not found" | Install FL Studio 25+, or open `PLG_Session.flp` manually from the project folder |
| FL opens empty | Run **Install FL Scripts** in Settings, then Open in FL again |
| Wrong FL version opens | Uninstall old FL or rename older `Image-Line` folder |
| Project doesn't work correctly | Confirm you're on FL Studio 25 or newer — older versions are not supported (see policy above) |

---

## Archived: the FL 20/21/24 compatibility investigation (2026-09-12/13)

**Status: closed, not supported, do not resume.** This section is kept only
as a record of what was tried and why it was abandoned, so nobody repeats
the same dead ends. None of the findings below apply to the current FL
Studio 25+-only policy.

### What was tried

A real-hardware investigation (FL 21.2.3, FL 24.2.2, FL 25.2.2, FL 26)
found two confirmed defects that made FL 21/24 behave incorrectly:

1. **TrackID.Data (event 238) length** — PLG wrote a 70-byte record; real
   FL 21 only accepted 66 bytes, causing FL 21 to reject the whole file
   with "invalid data" on open. A fix (shrinking to 66 bytes, commit
   `cdc84ad`) was written, tested, and **committed**.
2. **A separate audio-silencing bug** — even after fix #1, FL 21 and FL 24
   opened PLG's generated projects but produced no audio on any
   sample-based lane (only the MIDI-sourced melody channel played), and
   playlist clips still flickered on playlist-panel resize. Real-hardware
   bisection (see git history for the full methodology — differential byte
   diffing between pre/post "activation" saves, a channel-order-swap probe,
   ruling out FL's "Smart Disable" CPU feature) narrowed this to something
   about how FL 21/24 lazily activates a Sampler channel's audio engine —
   specifically, only the **first-declared channel** (whichever occupies
   Channel Rack slot 0) played without the user manually opening its Piano
   Roll once per session. Manually confirmed this activation state is
   **never persisted to the file** — even FL 21's own native re-save of a
   fully-activated project reverts to the same broken state on a fresh
   reopen. This means the defect (if it is one at all, rather than a
   genuine FL 21/24 engine behavior around channel focus/lazy
   instantiation) cannot be fixed by writing different bytes into the
   `.flp` — there is no persisted flag to set.

### What was reverted

- The 66-byte TrackID.Data fix (`cdc84ad`) was **reverted** (`0768735`)
  once a production smoke test surfaced an FL 25.2.2 regression at that
  commit, and separately because it did not fix the underlying issue on
  its own. `flp_writer.py` is back to its pre-investigation (`9ef088b`)
  behavior, re-verified working on FL 25.2.2.
- A follow-up experiment (writing one universal 168-byte `_CH_PARAMETERS`
  sampler-settings page for every channel, based on a byte captured from a
  real FL 21 post-activation save) was built, tested, and found **not** to
  fix the silence issue on a fresh, untouched load — it was never
  committed and has been fully removed from the working tree.

### Why this is closed, not paused

The root cause narrowed to something that isn't representable as file
bytes at all (see point 2 above) — continuing would mean either reverse-
engineering an internal FL 21/24 runtime behavior with no guarantee it's
fixable from the file format side, or accepting a permanent "click every
channel once" workaround for users on those versions. Given the project
now targets FL Studio 25+ only, neither is worth further engineering time.
If this is ever revisited, start by re-reading this section and the git
history around commits `cdc84ad` and its revert — don't re-derive the
TrackID.Data or CH_PARAMETERS findings from scratch.
