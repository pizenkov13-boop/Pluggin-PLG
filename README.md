# PLG: from your MIDI and your drum kit to an editable FL Studio project

![Windows](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/backend-Python%20%2F%20FastAPI-3776AB)
![React](https://img.shields.io/badge/UI-React%20%2B%20TypeScript-61DAFB)
![Status](https://img.shields.io/badge/status-closed%20(Nov%202026)-lightgrey)

**PLG** is a Windows desktop app I built alone, from idea to tested release.
You drop in a MIDI melody and your own drum samples. PLG recognises what every
sample is, builds a drum arrangement around the melody, and opens a ready,
fully editable **FL Studio `.flp` project**. No render, no loop pack: every
note and every sound is a normal channel you can change.

> I stopped the project in November 2026 after testing it with a working
> beatmaker. This README covers what I built and how it works.

<!-- Add 2-3 screenshots here: the PLG home screen with the sound browser, and a generated project in FL Studio's Playlist. Put the images in /docs/img and link them. -->

---

## What it does

1. **Reads your sounds.** Point PLG at a folder of drum samples (kick, 808, hi-hat, clap, snare, and so on). It works out what each file is from the file name *and* from the audio itself.
2. **Reads your MIDI.** It measures the melody: note length, how many notes sound together, pitch range, density.
3. **Builds the beat.** It writes drum patterns around the melody, then picks a melody sound from a small one-shot library that fits the MIDI (that library is not part of this repository).
4. **Writes a real FL Studio project.** The `.flp` has one channel and one pattern per instrument, clips placed in the playlist, and your own samples loaded in the channels.
5. **Opens it in FL Studio.** One click. If you are missing a sound (no kick in the folder), that track is simply left out instead of being filled with a stand-in.

## Architecture

```
React + TypeScript UI (Vite)
        │  pywebview (desktop window, local only)
        ▼
Python core ── sound recognition ── beat generator ── FLP writer ──► .flp
        │            │                    │
        │            └── sound browser    └── pattern libraries
        │               (local preview server, token-protected)
        ▼
FastAPI backend + self-hosted Supabase   (accounts and quota, used in the
                                          cloud version only)
```

- **Packaging:** PyInstaller one-file `PLG.exe`, separate release and *tester* builds.
- **Tester build:** a build flavour baked into the executable: fully local, no login and no network calls, with an expiry date. A verification script scans the built archive for server strings and keys before anything is shared.
- **Offline dev mode:** `PLG_OFFLINE_DEV=1` runs everything from source with no backend.
- **Feature flags:** risky behaviour (for example melody one-shots) ships behind flags so it can be tested and switched off.

## How it was built

I designed the product, the architecture and the test strategy, and I directed and reviewed the work. The code was written with AI tools (Claude, ChatGPT and Cursor), not typed by hand. I decided what to build, checked the output against real FL Studio projects, and tested it with a working beatmaker.

## The main lesson

I spent four months building PLG, and I was proud of it the whole way. I am not a beatmaker. I wanted to help beginners make beats, and while I was building alone everything looked great to me. Then I put it in front of a real beatmaker, and the picture changed fast. He saw problems I couldn't even name: arrangements that were wrong, drum parts that repeated, a structure that didn't match how a real starter is built. I kept improving the product, but I did not understand the craft well enough to know what "good" looked like.

It cost me time, money and a lot of nerves.

**Never launch a venture in a domain you understand only superficially. First master the subject and validate the customer's real needs; commit time and money only after that.**

## Run from source (development)

Requires Windows, Python 3.11+ and Node 18+.

```powershell
# UI
cd web
npm install
npm run build
cd ..

# run without a backend
$env:PLG_OFFLINE_DEV = '1'
python PLG.pyw

# inspect which sound ended up in each channel of a generated project
python tools\show_flp_sounds.py "path\to\project.flp"
```

Sound libraries, MIDI patterns and one-shot samples are not included in this repository (licensing). Bring your own drum kit and MIDI.

## Tech

Python · FastAPI · pywebview · React · TypeScript · Vite · PyInstaller · numpy · Supabase (self-hosted) · FL Studio `.flp` format

License: All rights reserved. Source is available for review only, see [LICENSE](LICENSE).

## Author

Built by **Pavel Izenkin**, solo developer and founder.
Questions or feedback are welcome via GitHub issues.
