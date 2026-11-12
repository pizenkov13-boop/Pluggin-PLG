# Archived: signed auto-updater (client side)

Archived 2026-08-11 by product decision — "back to it separately, later".

## Contents

`plg_updater.py` + `test_plg_updater.py` — Ed25519-signature-checking update
client (`check_for_updates` / `download_update` / `apply_downloaded_update`).

## Why it went

No UI entry point: the three `plg_api` functions and their `api.ts` helpers had
no caller in `web/src`.

## ⚠️ Restore this before shipping an update

`SECURITY.md` describes signed auto-update as a shipped protection. That claim
now depends on **two** missing pieces:

1. `plg_updater.py` — here.
2. `scripts/sign_release.py` — never present in this folder at all; it has to
   come back from the parent project.

Until both exist, a released `.exe` has no update path. `PLG_UPDATE_PUBKEY` in
`.env.release` and `MIN_CLIENT_VERSION` on the cloud side are untouched, so
restoring is a straight move-back plus re-registering the three `plg_api`
entry points and the bridge methods.

## What changed in the live tree

- `plg_api`: the three functions removed (a comment block marks the spot).
- `plg_webview.Api`, `web/src/api.ts`, `devBridge.ts`: matching entries removed.
- `PLG.spec`: `plg_updater` dropped from `hiddenimports`.
