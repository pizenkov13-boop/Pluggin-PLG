import type { BillingInfo, Quota } from "./types";

/** Trial user still has a free beat — hide counters and subscribe prompts. */
export function isSilentTrial(quota: Quota | null | undefined): boolean {
  return Boolean(
    quota && !quota.skipped && quota.status === "trial" && (quota.remaining ?? 0) > 0,
  );
}

export function isSilentTrialBilling(billing: BillingInfo | null | undefined): boolean {
  return Boolean(
    billing?.status === "trial" && (billing.trial_remaining ?? 0) > 0,
  );
}
