// TypeScript shapes mirroring the dicts plg_api returns. Kept loose on purpose
// (optional fields) so the UI never crashes if the backend adds keys.

export interface Quota {
  used: number;
  limit: number;
  remaining: number;
  days_until_reset: number;
  period_days?: number;
  skipped: boolean;
  label: string;
  status?: string;
}

export interface BillingInfo {
  status: string;
  price_tier?: string;
  price_label?: string;
  // Regular price, present only while `price_tier === "ru_promo"` — the account
  // screen strikes it through next to the promo price.
  regular_price_label?: string | null;
  trial_remaining?: number;
  grace_days_left?: number;
  days_until_renewal?: number;
  can_subscribe?: boolean;
  needs_payment?: boolean;
  // Why a non-active account can't generate: "trial" (still has a free beat),
  // "trial_spent", "device_trial_used", "subscription_ended". Server-derived so
  // the UI never tells a never-subscriber their "subscription ended".
  lapsed_reason?: string;
}

export interface Status {
  ok: boolean;
  cloud_mode?: boolean;
  cloud_url?: string | null;
  cloud_error?: string | null;
  cloud_error_type?: string | null;
  local_mode_available?: boolean;
  release_build?: boolean;
  signed_in?: boolean;
  auth_email?: string | null;
  app_version?: string;
  // null/absent while the first server probe is still running (the UI shows no offline banner yet).
  network_online?: boolean | null;
  // True while the server probe has not answered inside the status budget; ask again soon.
  network_checking?: boolean;
  // PLG_OFFLINE_DEV (development only): no server, sign-in or quota.
  offline_dev?: boolean;
  // Tester build (a flag stamped into that exe alone): local only, expires on a date.
  tester_build?: boolean;
  tester_expires?: string | null;
  tester_days_left?: number | null;
  tester_expired?: boolean;
  provider: string;
  has_api_key: boolean;
  fl_bridge_ready: boolean;
  fl_installed?: boolean;
  fl_version?: string | null;
  fl_executable: string | null;
  beat_ready: boolean;
  auto_open_fl: boolean;
  auto_preview?: boolean;
  library_audio_total: number;
  library_midi_total?: number;
  material_total?: number;
  // True while the boot rescan of the library runs: the counters are not settled yet.
  library_syncing?: boolean;
  last_prompt: string;
  sample_picks?: Record<string, string>;
  // Lane -> file the user pinned with "Random from library" (empty = automatic).
  pinned_kit?: Record<string, string>;
  // Lanes whose pin the user chose by hand in the sound browser (the gates yield to those).
  pinned_explicit?: string[];
  bpm?: number;
  style?: string;
  stem_session?: string;
  stem_files?: string[];
  sample_chop?: { chop_count?: number; pitch_semitones?: number; tempo_ratio?: number };
  clipper_mode?: boolean;
  beat_id?: string;
  beat_rating?: number;
  quality_score?: number;
  reward_learning?: boolean;
  reward_model_ready?: boolean;
  reward_ratings?: number;
  reward_min_samples?: number;
  reward_progress?: number;
  reward_favorite_genre?: string | null;
  reference_reverse?: Record<string, unknown>;
  reference_reverse_ready?: boolean;
  reference_reverse_demucs?: boolean;
  quota: Quota;
}

export interface FlInstallation {
  path: string;
  version: string;
  selected: boolean;
}

export interface Settings {
  ok: boolean;
  provider: string;
  gemini_key: string;
  anthropic_key: string;
  has_gemini_key: boolean;
  has_anthropic_key: boolean;
  samples_dir: string;
  gemini_model: string;
  claude_model: string;
  auto_open_fl: boolean;
  auto_preview?: boolean;
  cloud_mode?: boolean;
  cloud_url?: string;
  release_build?: boolean;
  fl_executable?: string;
  fl_installations?: FlInstallation[];
}

// Outcome of an explicit MIDI/reference-audio upload attached to a beat (see
// midi_ingest.ingest_uploaded_midi / reference_reverse.ingest_uploaded_reference_audio).
export interface UploadResult {
  ok: boolean;
  warning?: string | null;
  notes?: number;
  truncated?: boolean;
}

// A Library file the user picked to use as the core melody source for the
// NEXT beat (Layer 2A: attach-from-list, no native file picker).
export interface AttachedFile {
  path: string;
  name: string;
  kind: "midi" | "reference";
}

// Genre inferred from an attached MIDI's own musical content (see
// detect_midi_genre) — shown to the user for confirm/override BEFORE
// generation runs, never applied silently.
export interface DetectedGenre {
  genre: string;
  confidence: number;
}

// Result payload of a successful create_beat / open_in_fl job.
export interface BeatResult {
  ok: boolean;
  bpm?: number;
  style?: string;
  note_count?: number;
  sample_count?: number;
  provider?: string;
  auto_open_fl?: boolean;
  auto_preview?: boolean;
  preview_audio?: string;
  preview_seconds?: number;
  preview_path?: string;
  sample_picks?: Record<string, string>;
  // Lanes ("kick" | "808_bass" | "hi_hats") where the user's own files could
  // not be used and a built-in sound stands in.
  sample_fallback_lanes?: string[];
  // Lanes where the user's random pick could not serve this track and another
  // file of the library was used.
  pinned_replaced_lanes?: string[];
  // Full path of the .flp the beat was written to (create_beat sets it on success).
  flp?: string | null;
  stem_session?: string;
  stem_files?: string[];
  clipper_mode?: boolean;
  beat_id?: string;
  beat_rating?: number;
  quality_score?: number;
  quota?: Quota;
  midi_upload?: UploadResult | null;
  reference_upload?: UploadResult | null;
}

export type JobStatus = "running" | "done" | "error" | "unknown";

export interface JobSnapshot {
  ok: boolean;
  job_id: string;
  kind: string;
  status: JobStatus;
  phase: string;
  progress: number;
  message: string;
  result: Record<string, unknown> | null;
  error: string | null;
  error_type: string | null;
  started_at: number;
  elapsed: number;
}

// Generic error/ok envelope returned by sync actions.
export interface ApiResult {
  ok: boolean;
  error?: string;
  error_type?: string;
  [key: string]: unknown;
}

export interface KitPreviewResult {
  ok: boolean;
  audio_total?: number;
  picks?: Record<string, { name: string; path: string }>;
  error?: string;
}

export interface JobHandle {
  ok: boolean;
  job_id: string;
  kind: string;
  status: JobStatus;
}

export interface PickResult {
  ok: boolean;
  path?: string;
  cancelled?: boolean;
  error?: string;
}

export interface AppInfo {
  ok: boolean;
  version: string;
  project_dir: string;
  fl_executable: string | null;
  fl_version?: string | null;
  fl_installations?: FlInstallation[];
  fl_bridge_ready: boolean;
  demucs_available: boolean;
  starter: {
    dir: string;
    bundle_dir: string;
    source: string;
    bundled: boolean;
    incoming_dir: string;
  };
  docs: Record<string, string>;
  quota: Quota;
}
