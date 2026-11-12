# FL Workflows — Don Toliver / Opium (PLG producer brain)

> **Scope note (2026-08-11):** this is a *manual mixing guide for the human*,
> not a description of what the code does. Two things in it are out of date and
> are corrected inline below: the channel list, and the claim that prompt text
> selects a style. The active generation contract is `ACTIVE_ROUTE.md`.

PLG generates the session + this guide. **Your wav = your sound.** Don-level mix = follow steps below in FL 2025.

---

## Open beat (PLG → FL)

1. Put a MIDI file in `PLG_MIDI` → Generate → the app opens FL with `PLG_Session.flp`
2. Channel Rack shows the seven active lanes: **PLG MIDI**, **PLG Kick**,
   **PLG 808 Bass**, **PLG Hi-Hats**, **PLG Clap**, **PLG Snare**,
   **PLG Snare Layer**. There is no `PLG Sub 808` channel — that lane is
   forbidden by `midi_validate.MIDI_DISABLED_LANES`.
3. Samples are already attached from `PLG_Sounds` (or the bundled starter pack).
   Drag your own onto any Sampler channel to swap.

---

## 808 (Don pocket)

| Step | FL action |
|------|-----------|
| Sample | Long 808 one-shot, mono, C root |
| Sampler | Sustain max, release ~200ms, **Portamento / glide** on |
| Notes | Roots on 1 and 3, slides on pitch changes (use PLG 808 Glide script) |
| FX | Precomputed boost 30% → **Fruity Fast Dist** drive 90% → **WaveShaper** boost 40% |
| Mixer | EQ: high-pass 25 Hz, boost 60–80 Hz gentle |

Don: sub is felt, not constant mud — space between 808 hits.

---

## Hi-hats (bounce, not only rage)

| Step | FL action |
|------|-----------|
| Sample | Short tight hat or opium roll wav |
| Pattern | Offbeats + 1/16 bursts every 2 bars |
| Script | Piano roll → Scripts → PLG → **Hat Roll** / **Pan Spread** |
| Mixer | Pan hats L/R slightly, -3 dB vs 808 |

Ken Carson = denser rolls. Don = **pocket + bounce**.

---

## Melody (vocal-ready)

| Step | FL action |
|------|-----------|
| Sound | Dark bell, pluck, or simple lead |
| Range | C4–C6, leave room for vocals |
| FX | Light reverb, cut 300–500 Hz on melody bus |
| Arrangement | Repeat hook 4 bars, variation bar 5–8 |

---

## Mixer (last 10 minutes)

1. **F9** Channel rack levels: 808 loudest, hats -6 dB, melody -4 dB
2. Drums bus optional — glue with Fruity Limiter soft
3. Sidechain: 808 triggers hat duck (light) if mix fights
4. Master: no clip — headroom for vocal later

---

## Hotkeys (Don speed)

| Key | Action |
|-----|--------|
| F5 | Piano roll |
| F6 | Channel rack |
| F9 | Mixer |
| Alt+F8 | Plugin picker |
| Ctrl+L | Toggle playlist |

---

## PLG scripts (Piano roll → Scripts → PLG)

- **Hat Roll** — opium rolls on selected hats
- **808 Glide** — slides on 808 notes
- **Pan Spread** — stereo hats
- **Loose Quantize** — loose trap swing (older docs called it "Quantize Opium";
  the file is `fl_scripts/PLG Loose Quantize.pyscript`)
- **Import ALL** — re-import JSON layers

---

## Style map

| Vibe | BPM | Hats | 808 | Melody |
|-------------|-----|------|-----|--------|
| Don Toliver | 145–155 | Bounce, space | Smooth slides, dist medium | Catchy minor hook |
| Ken Carson | 150–160 | Dense rolls | Heavy distorted | Sparse eerie |
| Opium dark | 140–150 | Triplet bursts | Long sub | Bell / pad |

**This table is a reference for you, not an input to the app.** The shipped UI
has no prompt field, and `plg_api.create_beat` treats its `prompt` argument as
compatibility plumbing only (see `ACTIVE_ROUTE.md`). BPM and key come from the
MIDI file. `artist_styles.py` is only reachable from the retired prompt/LLM
composer branch.

⚠️ **The genre lane does not currently vary either.** The UI genre picker was
removed, and `backend_core.run_pipeline` sets `genre_hint = genre_override or
"trap"` with `genre_override` always `None` — so every beat generates on the
`trap` lane whatever the MIDI is. `genre_brain` / `midi_genre_detect` still run
inside `create_beat`'s gate but no longer affect the sound. Open decision:
the project issue log (not included) §5a.
