# Security posture — Pluggin'

Honest map of what protects the product, what's already hardened, and the one
operational step you must do before each release.

> **Checked 2026-08-12. The auto-update section below is currently aspirational
> — neither side of it ships from this folder.**
>
> - `scripts/sign_release.py` (the signing tool) **is not in `C:\newPLUG`** —
>   there is no `scripts/` folder here at all.
> - `plg_updater.py` (the client that verifies signatures) was **moved to
>   `archive/updater-2026-08-11/` by product decision**.
>   It is not imported by the desktop app. `PLG_UPDATE_PUBKEY` remains in
>   `.env.release:24` but nothing reads it.
>
> So today there is no auto-update path at all — which is safe, but means the
> "every update is signed" guarantee below describes a feature that must be
> restored from the archive **and** given a signing tool before it is true.
> Decide deliberately: restore the vertical, or drop the claim.
>
> The cloud-side modules referenced further down (`cloud/app/quota.py`,
> `rate_limit.py`, `kill_switch.py`, `auth.py`, `admin_ops.py`) are all present.

## Anti-piracy: the value is server-side, not in the binary

The desktop `.exe` is a **thin client**. In release (cloud) mode it ships **no
valuable secrets** — `.env.release` contains only the backend URL and the
Supabase *anon* key (publishable by design; Row-Level Security protects the
data). The things worth stealing — the DeepSeek/Qwen gateway key, the Supabase
service key, YooKassa secret — live **only on the server**.

So pirating the binary gets you a client that can't generate anything without a
**paid, authenticated, quota-checked** account. There is nothing to crack.

> Client-side code obfuscation is **security theater** for a SaaS — a determined
> person can always decompile a local binary. We don't rely on it; we rely on
> the value living server-side. That's the correct and only real protection.

## Auto-update: signed against supply-chain tampering (⚠️ archived — see banner)

The scariest attack on a desktop app is a poisoned update pushing malware to
every user. HTTPS only proves you reached *a* server — not that the payload is
authentic if that server / CDN / pipeline is compromised.

So every update is **Ed25519-signed**. The private key is held offline by you;
the public key is baked into the client (`PLG_UPDATE_PUBKEY` in `.env.release`).
The updater refuses any update whose signature over `version + sha256` doesn't
verify — a spoofed or compromised manifest can't forge it without the private
key. The checksum is now **mandatory** too (an omitted one used to be skipped).

### Release procedure (do this for every build)

```bash
# ONCE — generate the keypair, paste the public key into .env.release
python scripts/sign_release.py keygen
#   -> PLG_UPDATE_PUBKEY=...   (put in .env.release, rebuild client)
#   -> plg_update_private.key  (keep OFFLINE / in CI secrets, back it up, .gitignored)

# EACH RELEASE — after building dist/PLG.exe and uploading it:
python scripts/sign_release.py sign \
  --version 1.1.0 --file dist/PLG.exe \
  --url https://.../PLG.exe --key plg_update_private.key
```

Set the printed fields on the cloud host so `/v1/release/manifest` serves them:
`PLG_RELEASE_DOWNLOAD_URL`, `PLG_RELEASE_SHA256`, `PLG_RELEASE_SIGNATURE`
(and `PLG_APP_VERSION`, optional `PLG_RELEASE_NOTES` / `PLG_RELEASE_MANDATORY`).

> **Losing `plg_update_private.key` = you can never ship a trusted update again**
> (users on the old public key will reject anything you sign with a new key).
> Back it up like a code-signing certificate.

## Already hardened (server, revenue, abuse)

- **Auth** — Supabase JWT; HS256 (self-host secret) or JWKS (asymmetric).
  alg-confusion is closed: HS256 is never accepted on the JWKS path.
- **Quota race** — `plg_consume_beat` reserves a credit in a row-locked
  transaction *before* the LLM call (008), refunds on failure. No over-spend.
- **Payments** — YooKassa handler re-fetches the payment from the API (can't be
  forged) + source-IP allowlist; idempotency keys stop duplicate webhooks.
- **Abuse** — per-user cooldown + hourly cap, per-IP hourly cap + daily alert,
  signup IP limit, CAPTCHA + honeypot + invite, device binding (max 3) +
  device-trial anti-farming, bans, kill-switch spend cap.
- **Transport/DoS** — CORS locked (no credentials), security headers, 2 MB body
  cap, LLM concurrency slots, Redis-backed rate limits (fail-loud in prod).
- **Privacy/legal** — full prompt text and audio are never stored; account
  delete cascades; retention purge cron. See `cloud/infra/legal-compliance.md`.

## Honest residual risks (operational, not code)

- **Account sharing** — device binding (3) limits it; tighten `PLG_MAX_DEVICES`
  or add device-approval emails if it becomes a problem.
- **Secrets hygiene** — never commit `cloud/.env` (gitignored). Rotate the
  service key / gateway key if a host is ever compromised.
- **Dependency CVEs** — run `pip-audit` periodically; keep `cryptography`,
  `httpx`, `fastapi` current.
- **Backups** — a self-hosted RU DB is only as safe as its backups
  (`cloud/infra/ru-hosting-runbook.md`, Step 5).
