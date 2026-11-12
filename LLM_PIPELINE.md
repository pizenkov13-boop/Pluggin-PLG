# Legacy Titans pipeline — not the active MIDI generation route

> **Do not use this document to modify normal PLG generation.** The active
> product route is material-first: `PLG_MIDI + PLG_Sounds -> deterministic
> generation -> symbiosis -> validation -> FLP writer`. In that route, an LLM
> may choose a curated FLEX role/preset only; it must not compose melody,
> drums, 808 notes, or a second lane. Read `ACTIVE_ROUTE.md`,
> the owner map (not included), and `EXPORT_COMPONENT_MAP.md` first.

The historical Titans material below is retained only for archive/cloud or
non-MIDI research. It is not authority for the desktop MIDI-first route.

A beat is no longer one generic model call. It's a **route → draft → refine →
finish → build** pipeline routed through one OpenAI-compatible gateway:

```
prompt + sample catalog + producer context
        │
        ▼
[0] GENRE BRAIN (genre_brain.py — tiny routing call)  →  lane + mood + energy
        │        free-form prompts («грустный бит для ночной поездки») are read
        │        like a producer would and routed to one of ~30 lanes. Explicit
        │        genre words and artist references resolve locally for free
        │        (keyword router / artist_styles resolver — names never reach
        │        the LLM). The lane drives ALL deterministic decisions: BPM
        │        pool, style-DNA progression corpus, drum-pocket blueprint,
        │        groove profile, and sample matching (lane+mood enrich search).
        ▼
[1] ARCHITECT  (gpt-5.6-luna-pro via AITUNNEL) →  rich JSON skeleton
        │        bpm / key / motifs / 808 glides / hat rolls / fx / mix / processing
        ▼
[2] COMPOSER   (gpt-5.6-luna, JSON engineer) → full output_pattern.json
        │        realizes the plan as valid note arrays / swing / automation
        ▼
[3] FINISHER   (gpt-5.6-luna, budget-gated QA)      →  repaired JSON
        │        clamps ranges, drops broken/dupe notes; keeps the music
        ▼
[4] BUILDER    (flp_writer.py — deterministic, NOT an LLM)  →  PLG_Session.flp
```

## Why the builder is code, not a model

`flp_writer.py` is a tested, byte-verified FLP writer. That determinism is *why*
the project opens first-click with no plugin conflicts. An LLM emitting build
code each run would re-introduce exactly the FL-corrupting bugs we removed — so
the builder titan is deliberately code. Luna composes the music and does a
finisher pass (repair, not rewrite).

## Gateway routing

All models go through one OpenAI-compatible gateway — **one key unlocks all**.
Works with OpenRouter or **aitunnel** (`https://api.aitunnel.ru/v1`) via
`OPENROUTER_BASE_URL`.

- `llm_pipeline.py` — orchestrates architect → composer → (optional) QA.
- `llm_client.generate_pattern()` — runs the pipeline when `titans_enabled()`,
  else the original single-stage path.
- `flp_writer.py` / `starter_kit.py` — unchanged; same `output_pattern.json`.

**Schema handling:** the composer/QA use `response_format: json_object` with the
schema described in the prompt, *not* strict `json_schema`. We validate `tracks`
and clamp ranges downstream, so loose JSON + a good model is both robust and
accurate.

## Config (.env / .env.local)

| Var | Default | Meaning |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Gateway key. Required to enable the pipeline. |
| `OPENROUTER_BASE_URL` | `openrouter.ai/api/v1` | Set to `https://api.aitunnel.ru/v1` for aitunnel. |
| `PLG_LLM_PIPELINE` | `titans` | `single` / `off` forces the old one-shot path. |
| `PLG_TITAN_ARCHITECT_MODEL` | `gpt-5.6-luna-pro` | Stage 1 structural skeleton. |
| `PLG_TITAN_COMPOSER_MODEL` | `gpt-5.6-luna` | Stage 2 full beat JSON. |
| `PLG_TITAN_QA_MODEL` | `gpt-5.6-luna` | Stage 3 finisher. Set `off` to disable. |
| `PLG_OPENROUTER_TIMEOUT_MS` | `180000` | Per-call timeout for gateway models. |
| `PLG_TITAN_COMPOSER_TIMEOUT_MS` | `90000` | Composer leash independent of the global timeout. |
| `PLG_TITAN_COMPOSER_FALLBACK_MODEL` | composer model | One retry on composer timeout/error. `off` disables. |
| `PLG_MAX_BEAT_COST_RUB` | `0.50` | Hard estimated budget per beat. |
| `PLG_TITAN_ARCHITECT_COST_RUB` | `0.18` | Architect stage estimate. |
| `PLG_TITAN_COMPOSER_COST_RUB` | `0.16` | Composer stage estimate. |
| `PLG_TITAN_QA_COST_RUB` | `0.12` | QA estimate; skipped if it would exceed budget. |
| `PLG_TITAN_ARCHITECT_MAX_TOKENS` | `5000` | Keeps the architect rich but bounded. |
| `PLG_TITAN_COMPOSER_MAX_TOKENS` | `4096` | Fast sparse pattern JSON; PLG expands details deterministically. |

Default: **titans run whenever a gateway key is present**. With no key, PLG
fails clearly instead of silently falling back to Gemini/Claude.

## Failure handling

- Architect fails → composer runs solo (infers structure from the prompt).
- Composer fails / no `tracks` → pipeline raises, `generate_pattern` falls back
  to single-stage. Worst case = today's behaviour.
- QA fails or returns no tracks → composer output is kept (best-effort, never
  degrades).
- Each pattern is tagged `plg_pipeline: {mode, architect, composer, qa}`.

## Cost / latency

Default budget math: architect `0.18₽` + composer `0.16₽` + QA `0.12₽` = `0.46₽`,
under the `0.50₽` cap. The deterministic builder remains Python code, not an LLM.

## Beat quality engine (style corpus + best-of-N)

Two deterministic layers close the "generic LLM beat" gap without extra LLM cost:

**Style DNA (`style_corpus.py`).** Every generation gets a proven foundation
picked deterministically from a curated corpus per genre: a real chord
progression (per-bar scale degrees), an authentic drum-pocket cell, a genre BPM
range and 2–3 production-idiom recipe lines.

- The DNA is injected into the architect prompt as a `[PLG STYLE DNA]` block
  ("realize it, don't reinvent"), with the concrete 808 root walk spelled out.
- After composition the same DNA is pinned on the pattern as `plg_progression`
  (anchored to the *final* melody's detected root), and the 808 engines
  (`sub_808_gen`, `compose_kick_locked_808`) walk those changes bar-by-bar —
  no more single-pitch 808 drone. The hook loops over the tonic; the bass
  movement carries the harmony, which is exactly how commercial trap works.
- The deterministic fallback (`deterministic_pattern_from_skeleton`) also
  builds from corpus drum cells + progression, so even a full LLM outage lands
  an authentic genre grid.

**Best-of-N (`backend_core._humanize_and_ingest`).** Each LLM pattern is
expanded into N deterministic variants (different groove/roll/velocity seeds,
same pinned harmony), each is scored by `beat_quality.evaluate` (proxy score +
learned 👍/👎 reward when trained), and the winner ships. CPU-only.

**Any-genre engine.** 39 `GenreProfile` entries exist in `genre_profiles` (EN+RU
keyword detection) and each gets an *honest* skeleton, not a re-skinned trap
beat. Only the **ten** `DETAILED_GENRES` lanes actually ship — `clamp_to_focus`
redirects the other 29 to a tuned lane before generation (see `ACTIVE_ROUTE.md`):

- drum-grid archetypes in `drum_blueprint` — four-on-floor (house/techno/
  trance/hardstyle/edm), backbeat 2&4 (boombap/lofi/funk/soul/rock), dembow
  3+3+2 (reggaeton), two-step (garage/dnb), half-time lurch (dubstep), afro
  pockets (afrobeats/amapiano), epic hits (cinematic);
- genre BPM ranges and scales (house=dorian 120–126, dnb 170–176, lofi 72–92…);
- non-808 genres get an idiomatic bassline synthesized from the pinned
  progression when the LLM leaves the lane empty (off-beat house 8ths, funk
  syncopation, reese sustains), and existing LLM basslines are transposed
  bar-by-bar onto the progression (`walk_bass_to_progression`);
- per-genre sound descriptor families steer sample matching, so the user's own
  library supplies the right timbres (dusty vinyl for lofi, log drums for
  amapiano, supersaws for trance) — the library is the sound, PLG is the brain.

**Genre brain (`genre_brain.py`).** Nobody is required to name a genre or an
artist. Routing is tiered, cheap → smart: (1) explicit genre word → keyword
router, free; (2) artist reference (EN/RU, inflection-tolerant) → local
`artist_styles` resolver, free, and the name is replaced by a production vibe
descriptor before any LLM call; (3) otherwise one ~200-token LLM call
(temperature 0, cached) picks the lane + mood words + energy. Any failure →
legacy trap default, generation never blocks.

**Hook composer (`hook_composer.py`).** The Opium engine owns trap-family
melodies; every other lane gets this one. When the LLM melody is weak (sparse
staccato plinks / one repeated pitch — long sustains count as intentional), a
real hook is composed over the pinned progression: a 1-bar genre-family rhythm
cell (anthem / smooth / pluck / groove / epic) in call-and-response, every bar
re-pitched onto that bar's chord with voice leading, scale passing tones,
2-bar arch contour, a variation each 4th bar, phrase ends landing on the bar
root. The melody follows the changes — the core of "expensive" writing.

**Beat switch (`arranger.py` + late cut in `beat_humanize`).** Songs evolve
instead of looping: section occurrences are tracked, the 2nd verse rides
off-beat hats (reads as a different beat), the FINAL chorus peaks (octave-up
melody layer, denser hats, +energy), and the drums cut for one bar right before
it while the 808 sustains through the gap — the classic "pull out, come back
bigger" moment. The cut is applied at the end of humanize so no drum rebuild
can refill it.

**Mix DNA (`style_corpus.MIX_RECIPES` → `mix_insert_fx` → flp).** Every lane
gets its own insert-FX character on the native FL plugins: Blood Overdrive
preamp on the 808 bus (rage/phonk ≈ +6.5–7 dB — the Carti/Ken дисторшн; pop
lanes stay clean), Reeverb 2 wet/decay per lane (opium = huge 3.4 s wash,
rage = dry 1.8 s punch, cinematic = 4.5 s hall, lofi = dark), and per-genre
sidechain pump depth (house 0.9 breathes, boombap 0.25 barely ducks) used
whenever the architect doesn't specify one. The trap default drives the 808
(tail-free) but ships no melody reverb/delay — the old muted-playback
complaint stays fixed; space lanes opt in deliberately. A section-aware FX
auto-plan (crash on chorus landings, riser into each chorus, impact at the
final one, texture beds for opium/lofi) fills `fx_intent` when the LLM gave
none. Verse hats are genre-authentic too: off-beat open hats on
four-on-floor lanes, 1/16 shaker waves on afro/latin lanes.

| Var | Default | Meaning |
|---|---|---|
| `PLG_USE_STYLE_CORPUS` | `auto` (on) | Progression/pocket/BPM DNA + prompt block. |
| `PLG_BEST_OF_VARIANTS` | `3` | Deterministic variants per beat (1 = off, max 6). |
| `PLG_GENRE_BRAIN` | `auto` (on with key) | LLM lane routing for free-form prompts. |
| `PLG_GENRE_BRAIN_MODEL` | composer model | Override the tiny routing model. |
| `PLG_HOOK_COMPOSER` | `auto` (on) | Chord-following hook repair on non-trap lanes. |
| `PLG_BEAT_SWITCH` | `auto` (on) | Occurrence-aware sections + pre-final-chorus drum cut. |
