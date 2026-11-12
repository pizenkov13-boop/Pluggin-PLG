// Typed wrapper around the pywebview bridge (window.pywebview.api).
//
// pywebview injects `window.pywebview` once the page is ready and fires a
// `pywebviewready` event. Every api.<method> call returns a Promise that
// resolves with the dict plg_api returned. We add `ready()` to await injection
// and `pollJob()` to drive the start_* → get_job polling loop.

import type {
  ApiResult,
  AppInfo,
  BillingInfo,
  JobHandle,
  JobSnapshot,
  PickResult,
  Quota,
  Settings,
  Status,
} from "./types";

export type ArchiveTarget = "midi" | "library";

export interface RandomKitResult extends ApiResult {
  picked?: Record<string, string>;
  kept?: string[];
  pinned?: Record<string, string>;
}

export interface SoundEntry {
  rel: string;
  name: string;
  /** False when PLG cannot read the file's audio format: shown, but it cannot be played or picked. */
  usable?: boolean;
}

export interface SoundsResult extends ApiResult {
  sounds?: SoundEntry[];
  /** Address prefix of the preview server (empty when it did not start). */
  preview_base?: string;
  pinned?: Record<string, string>;
  explicit?: string[];
}

export interface PywebviewApi {
  get_status(): Promise<Status>;
  get_settings(): Promise<Settings>;
  save_settings(updates: Partial<Settings>): Promise<ApiResult>;
  archive_material(targets: ArchiveTarget[]): Promise<ApiResult & { archive_dir?: string | null }>;
  random_kit(): Promise<RandomKitResult>;
  clear_pinned_kit(): Promise<ApiResult>;
  list_sounds(): Promise<SoundsResult>;
  pin_sound(lane: string, rel: string): Promise<SoundsResult>;
  unpin_sound(lane: string): Promise<SoundsResult>;
  check_fl_executable(path: string): Promise<ApiResult & { exists?: boolean }>;
  get_quota(): Promise<Quota>;
  scan_library(): Promise<ApiResult>;

  start_beat(
    prompt: string,
    locale?: string | null,
    smart_plan?: boolean | null,
    midi_path?: string | null,
    reference_audio_path?: string | null,
    respect_uploaded_scale?: boolean | null,
    genre_override?: string | null,
  ): Promise<JobHandle>;
  start_open_in_fl(): Promise<JobHandle>;
  get_job(jobId: string): Promise<JobSnapshot>;
  clear_finished_jobs(): Promise<ApiResult>;

  open_in_fl(): Promise<ApiResult>;
  install_fl_scripts(): Promise<ApiResult>;
  reveal_path(path: string): Promise<ApiResult>;
  // Re-bake family (chaos_roll / flip_beat / clipper mode / producer
  // blueprint) and beat rating are archived — not on the bridge. Re-bake's
  // archive folder was deleted 2026-09-23 (IP-hygiene cleanup);
  // history is in Git. See
  // archive/rating-2026-08-11/README.md for the rating vertical.

  import_kit_folder(source: string, organize?: boolean): Promise<ApiResult & { destination?: string; mode?: string }>;
  begin_material_import(): Promise<ApiResult & { import_id?: string }>;
  import_material_chunk(importId: string, relativePath: string, offset: number, dataBase64: string): Promise<ApiResult>;
  finish_material_import(importId: string): Promise<ApiResult & { audio_total?: number; midi_total?: number; material_total?: number }>;
  cancel_material_import(importId: string): Promise<ApiResult>;
  get_app_info(): Promise<AppInfo>;
  get_docs(): Promise<ApiResult & { docs?: Record<string, string> }>;
  open_document(docId: string): Promise<ApiResult>;
  pick_folder(): Promise<PickResult>;
  pick_fl_executable(): Promise<PickResult>;
  cloud_login(email: string, password: string): Promise<ApiResult>;
  cloud_signup(
    email: string,
    password: string,
    captchaToken?: string | null,
    acceptTerms?: boolean,
    confirmAge?: boolean,
  ): Promise<ApiResult>;
  cloud_delete_account(): Promise<ApiResult>;
  cloud_logout(): Promise<ApiResult>;
  cloud_reset_password(email: string): Promise<ApiResult>;
  cloud_complete_password_reset(email: string, code: string, newPassword: string): Promise<ApiResult>;
  cloud_verify_email(userId: string, code: string): Promise<ApiResult>;
  cloud_resend_verification(userId: string): Promise<ApiResult>;
  cloud_billing_status(): Promise<BillingStatusResult>;
  cloud_billing_checkout(priceTier?: string | null, acceptPaymentTerms?: boolean): Promise<ApiResult & { confirmation_url?: string; opened?: boolean }>;
  cloud_billing_checkout_link(): Promise<ApiResult & { checkout_url?: string; opened?: boolean }>;
  cloud_fetch_status(): Promise<CloudStatusResult>;
  ping_cloud_connection(): Promise<ApiResult & { online?: boolean; cloud_url?: string; error?: string }>;
  cloud_submit_feedback(category: string, message: string, attachLog?: boolean): Promise<ApiResult & { message?: string }>;
  open_external_url(url: string): Promise<ApiResult>;
  set_ui_locale(locale: string): Promise<ApiResult & { locale?: string }>;
  get_auth_status(): Promise<AuthStatus>;
}

export interface CloudStatusResult {
  ok: boolean;
  overall?: string;
  support?: {
    email?: string;
    telegram?: string;
    sla_hours?: string;
    updates_url?: string;
    status_url?: string;
  };
  error?: string;
}

export interface BillingStatusResult {
  ok: boolean;
  billing?: BillingInfo;
  error?: string;
}

export type { BillingInfo };

export interface AuthStatus {
  ok: boolean;
  cloud_mode: boolean;
  signed_in: boolean;
  email?: string | null;
  captcha_required?: boolean;
  captcha_site_key?: string | null;
  captcha_provider?: string | null;
  feature_flags?: Record<string, boolean>;
}

declare global {
  interface Window {
    pywebview?: { api: PywebviewApi };
  }
}

/** Resolve once the pywebview bridge is available. */
export function ready(): Promise<void> {
  return new Promise((resolve) => {
    if (window.pywebview?.api) {
      resolve();
      return;
    }
    window.addEventListener("pywebviewready", () => resolve(), { once: true });
  });
}

function api(): PywebviewApi {
  if (!window.pywebview?.api) {
    throw new Error("pywebview bridge not ready — call ready() first.");
  }
  return window.pywebview.api;
}

// Direct passthroughs ------------------------------------------------------
export const getStatus = () => api().get_status();
export const getSettings = () => api().get_settings();
export const saveSettings = (u: Partial<Settings>) => api().save_settings(u);
export const archiveMaterial = (targets: ArchiveTarget[]) => api().archive_material(targets);
export const randomKit = () => api().random_kit();
export const clearPinnedKit = () => api().clear_pinned_kit();
export const listSounds = () => api().list_sounds();
export const pinSound = (lane: string, rel: string) => api().pin_sound(lane, rel);
export const unpinSound = (lane: string) => api().unpin_sound(lane);
export const checkFlExecutable = (path: string) => api().check_fl_executable(path);
export const getQuota = () => api().get_quota();
export const scanLibrary = () => api().scan_library();
// Home drag-and-drop is the only UI material entry point. The legacy picker
// arguments stay null because the Python bridge still accepts them for
// backwards-compatible scripts.
export const startBeat = (prompt: string, locale?: string | null) =>
  api().start_beat(prompt, locale ?? null, null, null, null, null, null);
export const setUiLocale = (locale: string) => api().set_ui_locale(locale);
export const startOpenInFl = () => api().start_open_in_fl();
export const installFlScripts = () => api().install_fl_scripts();
export const revealPath = (path: string) => api().reveal_path(path);
export const importKitFolder = (source: string, organize = false) => api().import_kit_folder(source, organize);
export const beginMaterialImport = () => api().begin_material_import();
export const importMaterialChunk = (importId: string, relativePath: string, offset: number, dataBase64: string) =>
  api().import_material_chunk(importId, relativePath, offset, dataBase64);
export const finishMaterialImport = (importId: string) => api().finish_material_import(importId);
export const cancelMaterialImport = (importId: string) => api().cancel_material_import(importId);
export const getAppInfo = () => api().get_app_info();
export const getDocs = () => api().get_docs();
export const openDocument = (docId: string) => api().open_document(docId);
export const pickFolder = () => api().pick_folder();
export const pickFlExecutable = () => api().pick_fl_executable();
export const getAuthStatus = () => api().get_auth_status();
export const cloudLogin = (email: string, password: string) => api().cloud_login(email, password);
export const cloudSignup = (
  email: string,
  password: string,
  captchaToken?: string | null,
  acceptTerms?: boolean,
  confirmAge?: boolean,
) => api().cloud_signup(email, password, captchaToken ?? null, acceptTerms ?? false, confirmAge ?? false);
export const cloudDeleteAccount = () => api().cloud_delete_account();
export const cloudLogout = () => api().cloud_logout();
export const cloudResetPassword = (email: string) => api().cloud_reset_password(email);
export const cloudCompletePasswordReset = (email: string, code: string, newPassword: string) =>
  api().cloud_complete_password_reset(email, code, newPassword);
export const cloudVerifyEmail = (userId: string, code: string) => api().cloud_verify_email(userId, code);
export const cloudResendVerification = (userId: string) => api().cloud_resend_verification(userId);
export const cloudBillingStatus = () => api().cloud_billing_status();
export const cloudBillingCheckout = (priceTier?: string | null, acceptPaymentTerms = false) =>
  api().cloud_billing_checkout(priceTier ?? null, acceptPaymentTerms);
export const cloudBillingCheckoutLink = () => api().cloud_billing_checkout_link();
export const cloudFetchStatus = () => api().cloud_fetch_status();
export const pingCloudConnection = () => api().ping_cloud_connection();
export const cloudSubmitFeedback = (category: string, message: string, attachLog = false) =>
  api().cloud_submit_feedback(category, message, attachLog);
export const openExternalUrl = (url: string) => api().open_external_url(url);

export class JobPollError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "JobPollError";
  }
}

// One failed bridge call must not abandon a job that is still running (the
// backend thread would keep going and, in cloud mode, keep its credit
// reserved), so get_job is retried with a growing pause before giving up.
const POLL_MAX_CONSECUTIVE_FAILURES = 5;
const POLL_RETRY_BASE_MS = 500;
const POLL_RETRY_MAX_MS = 4000;
// Longer than a generation can legitimately take, and longer than the
// backend's own "presumed dead" limit for a job (plg_api._JOB_STALE_S).
const POLL_MAX_TOTAL_MS = 20 * 60 * 1000;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Poll a job until it finishes. `onUpdate` fires on every tick (running too),
 * so the UI can show phase/elapsed live. Resolves with the final snapshot and
 * rejects with a `JobPollError` when the job can no longer be reached.
 */
export async function pollJob(
  jobId: string,
  onUpdate?: (snap: JobSnapshot) => void,
  intervalMs = 500,
): Promise<JobSnapshot> {
  const startedAt = Date.now();
  let failures = 0;
  for (;;) {
    let snap: JobSnapshot;
    try {
      snap = await api().get_job(jobId);
      failures = 0;
    } catch (err) {
      failures += 1;
      if (failures >= POLL_MAX_CONSECUTIVE_FAILURES) {
        throw new JobPollError(err instanceof Error ? err.message : String(err));
      }
      await sleep(Math.min(POLL_RETRY_BASE_MS * 2 ** (failures - 1), POLL_RETRY_MAX_MS));
      continue;
    }
    onUpdate?.(snap);
    if (snap.status === "done" || snap.status === "error" || snap.status === "unknown") return snap;
    if (Date.now() - startedAt > POLL_MAX_TOTAL_MS) throw new JobPollError("job poll timed out");
    await sleep(intervalMs);
  }
}
