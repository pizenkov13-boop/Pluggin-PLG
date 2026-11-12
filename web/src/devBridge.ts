// Dev-only mock of the pywebview bridge. `npm run dev` is loaded two ways:
// (1) inside the real desktop shell (`python plg_webview.py --dev`), which
// injects a real `window.pywebview` and fires `pywebviewready` itself, and
// (2) as a plain browser tab (design QA, screenshots), where no native shell
// exists and that injection never happens — every view now correctly waits
// forever instead of crashing, which is safe but useless for visual review.
//
// This fills that gap: if no real bridge shows up within a short window, it
// installs a fake one with plausible data so the whole app renders. Guarded
// by `import.meta.env.DEV`, which is false in the built `web/dist` the
// packaged app actually loads — this file has zero effect in production.

import type { PywebviewApi } from "./api";
import type { AppInfo, JobSnapshot, JobStatus, Quota, Settings, Status } from "./types";

const FALLBACK_DELAY_MS = 600;
const PHASES = ["plan", "arrange", "mix", "bake"];
const SHOW_AUTH_PREVIEW = import.meta.env.DEV && new URLSearchParams(window.location.search).has("auth-preview");
const SHOW_ACCOUNT_PREVIEW = import.meta.env.DEV && new URLSearchParams(window.location.search).has("account-preview");
const SHOW_SOUNDS_PREVIEW = import.meta.env.DEV && new URLSearchParams(window.location.search).has("sounds-preview");
// `?tester-preview` / `?tester-preview=expired`: the tester build's badge, message and missing account tab.
const TESTER_PARAM = import.meta.env.DEV ? new URLSearchParams(window.location.search).get("tester-preview") : null;
// `?tester-preview&fail-preview=quality_gate` (any backend error_type): the beat job fails with raw English text, to
// review the plain sentence the screen shows instead.
const FAIL_PARAM = import.meta.env.DEV ? new URLSearchParams(window.location.search).get("fail-preview") : null;
const PREVIEW_TIER = navigator.language.toLowerCase().startsWith("ru") ? "ru" : "other";
const PREVIEW_PRICE = PREVIEW_TIER === "ru" ? "499 ₽" : "$13.99";

const state = {
  cloudMode: SHOW_AUTH_PREVIEW || SHOW_ACCOUNT_PREVIEW,
  signedIn: SHOW_ACCOUNT_PREVIEW,
  hasApiKey: true,
  flBridgeReady: true,
  flInstalled: true,
  flVersion: "21.2.3",
  flExecutable: "C:\\Program Files\\Image-Line\\FL Studio 21\\FL64.exe",
  beatReady: false,
  lastPrompt: "",
  samplesDir: "",
  autoOpenFl: false,
  releaseBuild: false,
  quota: {
    // Browser first-run preview has no user library, but a just-created cloud
    // account does have its actual introductory quota. Keep those two facts
    // separate so Account can be reviewed without reviving fake file counts.
    used: 0,
    limit: SHOW_AUTH_PREVIEW || SHOW_ACCOUNT_PREVIEW ? 30 : 0,
    remaining: SHOW_AUTH_PREVIEW || SHOW_ACCOUNT_PREVIEW ? 30 : 0,
    days_until_reset: SHOW_AUTH_PREVIEW || SHOW_ACCOUNT_PREVIEW ? 30 : 0,
    skipped: !(SHOW_AUTH_PREVIEW || SHOW_ACCOUNT_PREVIEW),
    label: SHOW_AUTH_PREVIEW || SHOW_ACCOUNT_PREVIEW ? "30 из 30 генераций" : "",
    status: SHOW_AUTH_PREVIEW || SHOW_ACCOUNT_PREVIEW ? "trial" : undefined,
  } satisfies Quota,
};

// `?sounds-preview`: a library with sounds in it, so the Home actions and the sound browser can be reviewed.
const MOCK_SOUNDS = [
  "808 Dark.wav", "808 Long.wav", "Clap Wide.wav", "Clap Tight.wav", "Hat Closed 1.wav", "Hat Closed 2.wav",
  "Hat Open.wav", "Kick Punch.wav", "Kick Soft.wav", "Rim Click.wav", "Snare Crack.wav", "Snare Fat.wav",
].map((name) => ({ rel: `imports/${name}`, name, usable: name !== "Rim Click.wav" }));
const mockPins: Record<string, string> = {};
const mockExplicit = new Set<string>();

type MockJob = { kind: string; startedAt: number; durationMs: number };
const jobs = new Map<string, MockJob>();
let jobSeq = 0;

function startJob(kind: string, durationMs = 5200) {
  const id = `mock-job-${++jobSeq}`;
  jobs.set(id, { kind, startedAt: Date.now(), durationMs });
  return { ok: true as const, job_id: id, kind, status: "running" as JobStatus };
}

function getJob(jobId: string): JobSnapshot {
  const job = jobs.get(jobId);
  if (!job) {
    return {
      ok: true,
      job_id: jobId,
      kind: "unknown",
      status: "unknown",
      phase: "",
      progress: 0,
      message: "",
      result: null,
      error: null,
      error_type: null,
      started_at: 0,
      elapsed: 0,
    };
  }

  const elapsedMs = Date.now() - job.startedAt;
  const elapsed = elapsedMs / 1000;

  if (elapsedMs < job.durationMs) {
    const idx = Math.min(PHASES.length - 1, Math.floor((elapsedMs / job.durationMs) * PHASES.length));
    return {
      ok: true,
      job_id: jobId,
      kind: job.kind,
      status: "running",
      phase: PHASES[idx],
      progress: elapsedMs / job.durationMs,
      message: "",
      result: null,
      error: null,
      error_type: null,
      started_at: job.startedAt / 1000,
      elapsed,
    };
  }

  if (FAIL_PARAM && job.kind === "beat") {
    const raw = `RAW English developer text (${FAIL_PARAM})`;
    return {
      ok: false,
      job_id: jobId,
      kind: job.kind,
      status: "error",
      phase: "error",
      progress: 1,
      message: "",
      result: { ok: false, error: raw, error_type: FAIL_PARAM },
      error: raw,
      error_type: FAIL_PARAM,
      started_at: job.startedAt / 1000,
      elapsed,
    };
  }

  let result: Record<string, unknown> = { ok: true };
  if (job.kind === "beat" || job.kind === "regenerate") {
    state.beatReady = true;
    result = {
      ok: true,
      bpm: 145,
      style: "dark trap",
      note_count: 132,
      sample_count: 26,
      provider: "anthropic",
      auto_open_fl: false,
      flp: "C:\\PLG_Tester\\Projects\\PLG_Mock__v01.flp",
      beat_id: `mock-${jobId}`,
      beat_rating: 0,
      quality_score: 0.82,
      quota: state.quota,
    };
  } else if (job.kind === "open_in_fl") {
    result = { ok: true, message: "Открыто в FL Studio" };
  }

  return {
    ok: true,
    job_id: jobId,
    kind: job.kind,
    status: "done",
    phase: "done",
    progress: 1,
    message: "",
    result,
    error: null,
    error_type: null,
    started_at: job.startedAt / 1000,
    elapsed,
  };
}

function buildMockApi(): PywebviewApi {
  return {
    async get_status(): Promise<Status> {
      return {
        ok: true,
        cloud_mode: state.cloudMode,
        signed_in: state.signedIn,
        auth_email: state.signedIn ? "producer@plg.test" : null,
        release_build: state.releaseBuild,
        app_version: "0.9.0-dev",
        network_online: true,
        provider: "anthropic",
        has_api_key: state.hasApiKey,
        fl_bridge_ready: state.flBridgeReady,
        fl_installed: state.flInstalled,
        fl_version: state.flVersion,
        fl_executable: state.flExecutable,
        beat_ready: state.beatReady,
        auto_open_fl: state.autoOpenFl,
        library_audio_total: SHOW_SOUNDS_PREVIEW || TESTER_PARAM !== null ? MOCK_SOUNDS.length : 0,
        library_midi_total: SHOW_SOUNDS_PREVIEW || TESTER_PARAM !== null ? 2 : 0,
        material_total: SHOW_SOUNDS_PREVIEW || TESTER_PARAM !== null ? MOCK_SOUNDS.length + 2 : 0,
        ...(TESTER_PARAM !== null
          ? {
              tester_build: true,
              tester_expires: "2026-11-11",
              tester_days_left: TESTER_PARAM === "expired" ? -2 : 9,
              tester_expired: TESTER_PARAM === "expired",
            }
          : {}),
        pinned_kit: { ...mockPins },
        pinned_explicit: [...mockExplicit],
        last_prompt: state.lastPrompt,
        quota: state.quota,
      };
    },
    async get_settings(): Promise<Settings> {
      return {
        ok: true,
        provider: "anthropic",
        gemini_key: "",
        anthropic_key: "sk-mock-***",
        has_gemini_key: false,
        has_anthropic_key: true,
        samples_dir: state.samplesDir,
        gemini_model: "gemini-2.0-flash",
        claude_model: "claude-sonnet-4-5",
        auto_open_fl: state.autoOpenFl,
        cloud_mode: state.cloudMode,
        release_build: state.releaseBuild,
        fl_executable: state.flExecutable ?? "",
        fl_installations: [{ path: state.flExecutable ?? "", version: state.flVersion ?? "", selected: true }],
      };
    },
    async random_kit() {
      return { ok: true, picked: {}, kept: ["808_bass", "kick", "snare", "clap", "hi_hats"], pinned: {} };
    },
    async clear_pinned_kit() {
      for (const lane of Object.keys(mockPins)) delete mockPins[lane];
      mockExplicit.clear();
      return { ok: true };
    },
    async list_sounds() {
      return { ok: true, sounds: MOCK_SOUNDS, preview_base: "", pinned: { ...mockPins }, explicit: [...mockExplicit] };
    },
    async pin_sound(lane: string, rel: string) {
      const hit = MOCK_SOUNDS.find((sound) => sound.rel === rel);
      if (!hit) return { ok: false, error: "That sound is no longer in your library.", error_type: "not_found" };
      mockPins[lane] = hit.name;
      mockExplicit.add(lane);
      return { ok: true, pinned: { ...mockPins }, explicit: [...mockExplicit] };
    },
    async unpin_sound(lane: string) {
      delete mockPins[lane];
      mockExplicit.delete(lane);
      return { ok: true, pinned: { ...mockPins }, explicit: [...mockExplicit] };
    },
    async archive_material() {
      return { ok: true, archive_dir: null };
    },
    async save_settings(updates) {
      if (updates.samples_dir !== undefined) state.samplesDir = updates.samples_dir;
      if (updates.auto_open_fl !== undefined) state.autoOpenFl = Boolean(updates.auto_open_fl);
      if (updates.cloud_mode !== undefined) state.cloudMode = Boolean(updates.cloud_mode);
      if (updates.fl_executable !== undefined) state.flExecutable = updates.fl_executable || "";
      return { ok: true };
    },
    async check_fl_executable(path: string) {
      // No real filesystem here — only the one path this mock "installed"
      // counts as existing, so the dev preview still shows a real
      // red/green distinction instead of matching any plausible filename.
      const exists = Boolean(path) && path.trim().toLowerCase() === (state.flExecutable ?? "").toLowerCase();
      return { ok: true, exists };
    },
    async get_quota(): Promise<Quota> {
      return state.quota;
    },
    async scan_library() {
      return {
        ok: true,
        root: state.samplesDir,
        total: 0,
        audio_total: 0,
        audio: {},
        midi: [],
      };
    },
    async start_beat(prompt) {
      state.lastPrompt = prompt;
      return startJob("beat");
    },
    async start_open_in_fl() {
      return startJob("open_in_fl", 1200);
    },
    async get_job(jobId) {
      return getJob(jobId);
    },
    async clear_finished_jobs() {
      return { ok: true };
    },
    async open_in_fl() {
      return { ok: true };
    },
    async install_fl_scripts() {
      state.flBridgeReady = true;
      return { ok: true, message: "Скрипты FL установлены" };
    },
    async reveal_path() {
      return { ok: true };
    },
    async import_kit_folder(_source, organize) {
      return {
        ok: true,
        destination: `${state.samplesDir}\\imports\\kit`,
        mode: organize ? "organized" : "flat",
        message: "Кит импортирован",
      };
    },
    async begin_material_import() {
      return { ok: true, import_id: "mock-material-import" };
    },
    async import_material_chunk() {
      return { ok: true };
    },
    async finish_material_import() {
      return { ok: true, audio_total: 0, midi_total: 0, material_total: 0 };
    },
    async cancel_material_import() {
      return { ok: true };
    },
    async get_app_info(): Promise<AppInfo> {
      return {
        ok: true,
        version: "0.9.0-dev",
        project_dir: "C:\\PLUG.FLP",
        fl_executable: state.flExecutable,
        fl_version: state.flVersion,
        fl_installations: [{ path: state.flExecutable ?? "", version: state.flVersion ?? "", selected: true }],
        fl_bridge_ready: state.flBridgeReady,
        demucs_available: true,
        starter: {
          dir: "C:\\PLG\\starter",
          bundle_dir: "C:\\PLG\\starter\\bundle",
          source: "bundled",
          bundled: true,
          incoming_dir: "C:\\PLG\\starter\\incoming",
        },
        docs: { terms: "terms.md", privacy: "privacy.md", refund: "refund.md" },
        quota: state.quota,
      };
    },
    async get_docs() {
      return { ok: true, docs: { terms: "terms.md", privacy: "privacy.md", refund: "refund.md" } };
    },
    async open_document() {
      return { ok: true };
    },
    async pick_folder() {
      return { ok: true, path: `${state.samplesDir}\\imports\\NewKit` };
    },
    async pick_fl_executable() {
      return { ok: true, path: state.flExecutable ?? "" };
    },
    async cloud_login() {
      state.signedIn = true;
      return { ok: true };
    },
    async cloud_signup() {
      state.signedIn = true;
      return { ok: true, session: { access_token: "preview-session" } };
    },
    async cloud_delete_account() {
      return { ok: true };
    },
    async cloud_logout() {
      state.signedIn = false;
      return { ok: true };
    },
    async cloud_reset_password() {
      return { ok: true };
    },
    async cloud_complete_password_reset() {
      return { ok: true, message: "Password changed. Sign in with your new password." };
    },
    async cloud_verify_email() {
      return { ok: true };
    },
    async cloud_resend_verification() {
      return { ok: true };
    },
    async cloud_billing_status() {
      return {
        ok: true,
        billing: {
          status: "trial",
          trial_remaining: 30,
          days_until_renewal: 30,
          can_subscribe: true,
          price_tier: PREVIEW_TIER,
          price_label: PREVIEW_PRICE,
        },
      };
    },
    async cloud_billing_checkout() {
      return { ok: true, opened: false };
    },
    async cloud_billing_checkout_link() {
      // Reuse the deployed checkout's built-in preview mode.  It shows the
      // real consent and checkout UI without fabricating a second payment flow.
      return { ok: true, checkout_url: "https://example.com/checkout.html?preview=1", opened: false };
    },
    async cloud_fetch_status() {
      return {
        ok: true,
        overall: "ok",
        support: {
          email: "support@example.com",
          telegram: "@support_handle",
          sla_hours: "24",
          updates_url: "https://plg.app/updates",
          status_url: "https://plg.app/status",
        },
      };
    },
    async ping_cloud_connection() {
      return { ok: true, online: true };
    },
    async cloud_submit_feedback() {
      return { ok: true, message: "Спасибо за отзыв" };
    },
    async open_external_url(url) {
      const checkout = window.open(url, "_blank");
      if (checkout) checkout.opener = null;
      return checkout ? { ok: true } : { ok: false, error: "Could not open the checkout preview." };
    },
    async set_ui_locale(locale) {
      return { ok: true, locale };
    },
    async get_auth_status() {
      return { ok: true, cloud_mode: state.cloudMode, signed_in: state.signedIn };
    },
  };
}

export function installDevBridgeFallback() {
  if (!import.meta.env.DEV) return;
  if (window.pywebview?.api) return;

  window.setTimeout(() => {
    if (window.pywebview?.api) return; // real desktop bridge won the race
    window.pywebview = { api: buildMockApi() };
    window.dispatchEvent(new Event("pywebviewready"));
    // eslint-disable-next-line no-console
    console.info("[PLG dev] No pywebview bridge detected — using mock data for browser preview.");
  }, FALLBACK_DELAY_MS);
}
