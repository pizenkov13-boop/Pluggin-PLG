import type { ApiResult, JobSnapshot } from "./types";

type ErrorCarrier = {
  error?: string | null;
  error_type?: string | null;
  flp_name?: string | null;
  result?: { error?: string; error_type?: string; flp_name?: string } | null;
};

export function resolveErrorType(target: ErrorCarrier): string | undefined {
  return (
    target.error_type ||
    (target.result?.error_type as string | undefined) ||
    undefined
  );
}

function resolveFlpName(target: ErrorCarrier): string | undefined {
  return target.flp_name || target.result?.flp_name || undefined;
}

/** Map API / job error_type to a user-facing message. */
export function apiErrorMessage(
  result: ErrorCarrier & Pick<ApiResult, "error">,
  t: (key: string, params?: Record<string, string | number>) => string,
): string {
  const type = resolveErrorType(result);
  // "network": the request never got an answer. "cloud": the server answered
  // with an error — the connection is fine, so don't tell the user to check it.
  if (type === "tester_expired") {
    return t("tester.expired", { date: (result as { expires?: string | null }).expires ?? "" });
  }
  if (type === "local_only") return t("tester.linksOff");
  if (type === "network") return t("errors.network");
  if (type === "cloud") return t("errors.server");
  // Localized auth cases — ignore the raw English server message and translate
  // by the stable code (set in plg_cloud by HTTP status, not by string match).
  if (type === "email_taken") return t("auth.emailTaken");
  if (type === "email_not_confirmed") return t("auth.emailNotConfirmed");
  if (type === "invalid_credentials") return t("auth.invalidCreds");
  if (type === "same_password") return t("auth.passwordMustDiffer");
  if (type === "auth") return result.error ?? t("errors.auth");
  if (type === "quota") return result.error ?? t("errors.quota");
  if (type === "rate_limit") return t("errors.rateLimit");
  if (type === "subscription" || type === "billing") return result.error ?? t("errors.subscription");
  if (type === "device") return result.error ?? t("errors.device");
  if (type === "fl_not_found") {
    const name = resolveFlpName(result);
    return name ? t("flOnboard.openFailedNamed", { name }) : t("flOnboard.openFailed");
  }
  if (type === "fl_bridge_setup") return t("errors.flIntegrationFailed");
  if (type === "no_beat") return result.error ?? t("console.needBeat");
  if (type === "no_supported_files") return t("errors.noSupportedFiles");
  if (type === "validation") return result.error ?? t("errors.validation");
  if (type === "config") return result.error ?? t("errors.config");
  if (type === "settings_save_failed") return t("errors.settingsSaveFailed");
  if (type === "busy") return t("errors.archiveBusy");
  if (type === "no_library") return t("errors.noLibrary");
  if (type === "archive_failed") return t("errors.archiveFailed");
  if (type === "cross_device" || type === "custom_folder") return result.error ?? t("errors.archiveFolder");
  if (type === "account") return result.error ?? t("errors.account");
  return result.error ?? t("common.error");
}

// Engine / pipeline failures of a beat job carry English text written for the developer (some even point at an
// API gateway that the MIDI-first route does not use). The screen shows a localized sentence chosen by the stable
// error_type instead; a type nobody has mapped yet still gets a plain "could not create the beat" sentence,
// never the raw text and never nothing.
const BEAT_ERROR_KEYS: Record<string, string> = {
  flp_write_failed: "errors.genNoFlp",
  no_midi: "errors.genNoMidi",
  midi_unreadable: "errors.genMidiUnreadable",
  midi_unusable: "errors.genMidiUnusable",
  midi_validation: "errors.genKitProblem",
  library: "errors.genKitProblem",
  missing_file: "errors.genKitProblem",
  quality_gate: "errors.genQualityGate",
};

// error_types apiErrorMessage already turns into a localized sentence of its own.
const API_LOCALIZED_TYPES = new Set([
  "tester_expired",
  "local_only",
  "network",
  "cloud",
  "rate_limit",
  "no_library",
]);

export function beatErrorMessage(
  final: JobSnapshot,
  t: (key: string, params?: Record<string, string | number>) => string,
): string {
  const type = resolveErrorType({
    error_type: final.error_type,
    result: final.result as { error?: string; error_type?: string } | null,
  });
  const key = type ? BEAT_ERROR_KEYS[type] : undefined;
  if (key) return t(key);
  if (type && API_LOCALIZED_TYPES.has(type)) return jobErrorMessage(final, t);
  return t("errors.genFailed");
}

export function jobErrorMessage(
  final: JobSnapshot,
  t: (key: string, params?: Record<string, string | number>) => string,
): string {
  return apiErrorMessage(
    {
      error: final.error ?? undefined,
      error_type: final.error_type,
      result: final.result as { error?: string; error_type?: string; flp_name?: string } | null,
    },
    t,
  );
}
