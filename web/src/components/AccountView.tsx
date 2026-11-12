import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import {
  getQuota,
  cloudLogout,
  cloudDeleteAccount,
  cloudBillingStatus,
  cloudBillingCheckoutLink,
  openExternalUrl,
  ready,
} from "../api";
import type { BillingInfo, Quota } from "../types";
import { useI18n } from "../i18n";
import { apiErrorMessage } from "../errors";
import ConfirmModal from "./ConfirmModal";
import "./SettingsView.css";
import "./PageView.css";
import "./AccountView.css";

type Props = {
  onSaved: () => void;
  cloudMode?: boolean;
  authEmail?: string | null;
  appVersion?: string;
};

type AccountPreviewScenario =
  | "trial"
  | "trialEnded"
  | "expired"
  | "active"
  | "grace"
  | "checkout";

const ACCOUNT_PREVIEW_ENABLED =
  import.meta.env.DEV && new URLSearchParams(window.location.search).has("account-preview");

const PREVIEW_QUOTA: Record<AccountPreviewScenario, Quota> = {
  // TRIAL_BEATS on the real backend (cloud/app/config.py) is 1 — a brand-new
  // signup gets exactly one free generation before PLG asks for payment.
  trial: {
    used: 0,
    limit: 1,
    remaining: 1,
    days_until_reset: 0,
    skipped: false,
    label: "1 из 1 генерации",
  },
  // Trial row, free beat spent. The backend flips status trial -> expired at
  // that point and quota_snapshot() then reports 0/0 (not the monthly 30) for a
  // never-subscribed account. `lapsed_reason: "trial_spent"` carries the copy.
  trialEnded: {
    used: 0,
    limit: 0,
    remaining: 0,
    days_until_reset: 0,
    skipped: false,
    label: "",
  },
  expired: {
    used: 30,
    limit: 30,
    remaining: 0,
    days_until_reset: 0,
    skipped: false,
    label: "",
  },
  checkout: {
    used: 30,
    limit: 30,
    remaining: 0,
    days_until_reset: 0,
    skipped: false,
    label: "",
  },
  active: {
    used: 12,
    limit: 30,
    remaining: 18,
    days_until_reset: 17,
    skipped: false,
    label: "18 из 30 генераций",
  },
  // A canceled/declined payment attempt while the account was still active.
  // Access keeps working via hasPaidCloudAccess below. With generations left
  // this is the "payment failed, retry" state; once the quota is also spent
  // the account gets the same paywall as an expired one (see quotaSpent).
  grace: {
    used: 12,
    limit: 30,
    remaining: 18,
    days_until_reset: 2,
    skipped: false,
    label: "18 из 30 генераций",
  },
};

const PREVIEW_BILLING: Record<AccountPreviewScenario, BillingInfo> = {
  trial: { status: "trial", trial_remaining: 1, needs_payment: false, can_subscribe: true, price_tier: "ru_promo", price_label: "299 ₽", regular_price_label: "499 ₽", lapsed_reason: "trial" },
  // Backend flips status trial -> expired once the free beat is spent; the
  // "free beat used" copy is driven by lapsed_reason, not status.
  trialEnded: { status: "expired", trial_remaining: 0, needs_payment: true, can_subscribe: true, price_tier: "ru_promo", price_label: "299 ₽", regular_price_label: "499 ₽", lapsed_reason: "trial_spent" },
  expired: { status: "expired", needs_payment: true, can_subscribe: true, price_tier: "ru_promo", price_label: "299 ₽", regular_price_label: "499 ₽", lapsed_reason: "subscription_ended" },
  checkout: { status: "expired", needs_payment: true, can_subscribe: true, price_tier: "ru_promo", price_label: "299 ₽", regular_price_label: "499 ₽", lapsed_reason: "subscription_ended" },
  active: { status: "active", days_until_renewal: 17, needs_payment: false, price_tier: "cis", price_label: "499 ₽" },
  grace: { status: "grace", needs_payment: true, can_subscribe: true, price_tier: "cis", price_label: "499 ₽" },
};

export default function AccountView({ onSaved, cloudMode, authEmail, appVersion }: Props) {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [checkoutOpened, setCheckoutOpened] = useState(false);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [billing, setBilling] = useState<BillingInfo | null>(null);
  const [subscribing, setSubscribing] = useState(false);
  const [previewScenario, setPreviewScenario] = useState<AccountPreviewScenario>("active");

  const refreshAccount = useCallback(async () => {
    const [nextQuota, billingResult] = await Promise.all([
      getQuota(),
      cloudMode ? cloudBillingStatus() : Promise.resolve(null),
    ]);
    setQuota(nextQuota);
    if (billingResult?.ok && billingResult.billing) {
      setBilling(billingResult.billing);
      // Payment confirmed — drop the "checkout opened, finish in your browser"
      // notice so an active subscriber doesn't still see "Завершите оплату".
      if (
        billingResult.billing.status === "active" ||
        billingResult.billing.status === "grace"
      ) {
        setCheckoutOpened(false);
      }
    }
    return billingResult?.billing ?? null;
  }, [cloudMode]);

  useEffect(() => {
    ready().then(() => {
      refreshAccount()
        .finally(() => setLoading(false));
    });
  }, [refreshAccount]);

  useEffect(() => {
    if (!cloudMode) return;
    const onFocus = () => { void refreshAccount(); };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [cloudMode, refreshAccount]);

  if (loading) {
    return <div className="page page--loading">{t("common.loading")}</div>;
  }

  const displayedQuota =
    ACCOUNT_PREVIEW_ENABLED ? PREVIEW_QUOTA[previewScenario] : quota;
  const displayedBilling =
    ACCOUNT_PREVIEW_ENABLED ? PREVIEW_BILLING[previewScenario] : billing;
  const displayedCloudMode = ACCOUNT_PREVIEW_ENABLED || cloudMode;
  const displayedEmail = ACCOUNT_PREVIEW_ENABLED ? "producer@plg.test" : authEmail;
  const displayedQuotaPct =
    displayedQuota && !displayedQuota.skipped && displayedQuota.limit > 0
      ? Math.round((displayedQuota.remaining / displayedQuota.limit) * 100)
      : 0;

  async function onRenew() {
    if (ACCOUNT_PREVIEW_ENABLED) {
      const checkout = window.open("https://example.com/checkout.html?preview=1", "_blank");
      if (!checkout) {
        setError(t("account.checkoutOpenFailed"));
        return;
      }
      checkout.opener = null;
      setPreviewScenario("checkout");
      setError(null);
      setCheckoutOpened(true);
      return;
    }
    setSubscribing(true);
    setError(null);
    try {
      const result = await cloudBillingCheckoutLink();
      if (!result.ok) {
        setError(apiErrorMessage(result, t));
        return;
      }
      if (!result.checkout_url) {
        setError(t("account.checkoutLinkFailed"));
        return;
      }
      // The desktop bridge opens the checkout URL itself. The dev bridge does
      // not, so retain a browser fallback only for that explicit case.
      if (!result.opened) {
        const opened = await openExternalUrl(result.checkout_url);
        if (!opened.ok) {
          setError(apiErrorMessage(opened, t));
          return;
        }
      }
      setCheckoutOpened(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("account.checkoutOpenFailed"));
    } finally {
      setSubscribing(false);
    }
  }

  function onDeleteAccount() {
    if (ACCOUNT_PREVIEW_ENABLED) return;
    setConfirmDeleteOpen(true);
  }

  async function runDeleteAccount() {
    setDeleting(true);
    setError(null);
    const result = await cloudDeleteAccount();
    setDeleting(false);
    setConfirmDeleteOpen(false);
    if (!result.ok) {
      setError(apiErrorMessage(result, t));
      return;
    }
    onSaved();
  }

  // A quota belongs on Account even when it is empty: the meter must report
  // the backend value rather than disappear at zero.
  const hasPaidCloudAccess =
    !displayedCloudMode ||
    displayedBilling?.status === "active" ||
    displayedBilling?.status === "grace" ||
    displayedBilling?.status === "trial";
  const hasQuotaData = Boolean(
    displayedQuota && !displayedQuota.skipped && hasPaidCloudAccess,
  );
  const showQuota = hasQuotaData;
  // An active/grace subscriber who has spent this period's 30 generations gets
  // the same paywall as an expired account: any payment starts a new full
  // 30-day period, so the card reads "subscription ended" + "get access".
  const quotaSpent = Boolean(
    displayedCloudMode &&
      hasQuotaData &&
      displayedQuota!.remaining <= 0 &&
      (displayedBilling?.status === "active" || displayedBilling?.status === "grace"),
  );
  const isGrace = displayedBilling?.status === "grace" && !quotaSpent;
  const showRenewal = Boolean(
    displayedCloudMode && (displayedBilling?.needs_payment || (hasQuotaData && displayedQuota!.remaining <= 0)),
  );
  const showPurchase = Boolean(displayedCloudMode && displayedBilling?.can_subscribe && !showRenewal);
  // The proportional bar shows on every real allowance (trial 1/1 included — it
  // reads as "1 of 1 left"). The "resets in N days" line does NOT: it's a
  // paid-cycle concept. A cloud trial has no cycle (its days_until_reset is just
  // row-age + 30, i.e. noise); during grace the number that matters is
  // grace_days_left, shown in the card. So the countdown is for an active paid
  // subscription only, plus the local-mode 30/30 model.
  const showQuotaMeter = Boolean(displayedQuota && displayedQuota.limit >= 1);
  const showResetCountdown =
    (displayedBilling?.status === "active" && !quotaSpent) ||
    (!displayedCloudMode && Boolean(displayedQuota && displayedQuota.limit > 1));
  const isTrial =
    displayedBilling?.status === "trial" || displayedBilling?.lapsed_reason === "trial";
  // A soft upsell (trial still has its free beat) must not read as "buy the
  // trial": the paid plan gets its own heading, separate from the state line.
  const showPlanHeading = showPurchase || showRenewal;
  const purchasePrice = displayedBilling?.price_label ?? "499 ₽";
  // Launch promo: strike the regular price through before the 299 ₽ one. The
  // server sends `regular_price_label` only while `price_tier === "ru_promo"`;
  // once the promo hits 100 the tier is plain `ru` and the button is just 499 ₽.
  const promoRegular =
    displayedBilling?.price_tier === "ru_promo"
      ? displayedBilling?.regular_price_label ?? null
      : null;
  const priceNode = promoRegular ? (
    <>
      <s className="account__cta-was">{promoRegular}</s>
      {purchasePrice}
    </>
  ) : (
    purchasePrice
  );
  const checkoutNotice =
    checkoutOpened || (ACCOUNT_PREVIEW_ENABLED && previewScenario === "checkout")
      ? t("account.checkoutOpenedNotice")
      : null;
  // A never-subscribed account (free beat spent, or blocked because this device
  // already claimed a trial) and a lapsed paid one all land on showRenewal, but
  // they're different situations for the user. The backend flips status
  // trial -> expired the moment the last trial beat is consumed, so status
  // alone can't tell them apart — `lapsed_reason` (cloud/app/billing) does.
  const lapsed = displayedBilling?.lapsed_reason;
  // status "grace" means a renewal payment attempt was canceled/declined while
  // the account was still active — access keeps working (see
  // hasPaidCloudAccess above), so this must not read as "subscription ended".
  // Its own short, honest branch — no auto-renewal framing, no days-left
  // countdown. Once the quota is spent (quotaSpent) both active and grace fall
  // through to the same "subscription ended" state as an expired account.
  const accessStatus = quotaSpent
    ? t("account.accessInactive")
    : displayedBilling?.status === "active"
      ? t("account.accessActive")
      : displayedBilling?.status === "grace"
        ? t("account.accessPaymentFailed")
        : lapsed === "trial"
          ? t("account.accessTrial")
          : lapsed === "trial_spent" || lapsed === "device_trial_used"
            ? t("account.accessTrialEnded")
            : lapsed === "subscription_ended"
              ? t("account.accessInactive")
              : displayedBilling?.status === "trial"
                ? t("account.accessTrial")
                : t("account.accessInactive");

  return (
    <>
      <ConfirmModal
        open={confirmDeleteOpen}
        title={t("settings.deleteAccount")}
        message={t("settings.deleteConfirm")}
        confirmLabel={t("settings.deleteAccount")}
        cancelLabel={t("common.cancel")}
        destructive
        busy={deleting}
        onConfirm={runDeleteAccount}
        onCancel={() => setConfirmDeleteOpen(false)}
      />
    <div className="page page--figma page--figma-account">
      <div className="page__head">
        <h1>{t("account.title")}</h1>
      </div>

      {showQuota && (
        <div className="account__quota">
          <div className="account__quota-cols">
            <div className="account__row">
              <span className="account__label">{t("quota.remaining")}</span>
              <p className="account__value">
                {displayedQuota!.remaining}
                <em>/{displayedQuota!.limit}</em>
              </p>
            </div>
            {showResetCountdown && (
              <div className="account__row account__row--end">
                <span className="account__label">{t("quota.daysReset")}</span>
                <p className="account__value">
                  {displayedQuota!.days_until_reset}
                  <em> {t("quota.daysUnit")}</em>
                </p>
              </div>
            )}
          </div>
          {showQuotaMeter && (
            <div className="account__quota-track" aria-hidden>
              <div
                className="account__quota-fill"
                style={{ "--fill": displayedQuotaPct / 100 } as CSSProperties}
              />
            </div>
          )}
        </div>
      )}

      {displayedCloudMode && !showQuota && !showRenewal && (
        <div className="account__quota">
          <div className="account__row">
            <span className="account__label">{t("settings.accountQuota")}</span>
            <p className="account__value">—</p>
          </div>
          <p className="account__caption">{t("account.quotaUnavailable")} {t("account.quotaUnavailableDesc")}</p>
          <button type="button" className="account__text-link" onClick={() => void refreshAccount()}>
            {t("account.retry")}
          </button>
        </div>
      )}

      {displayedCloudMode && (
        <p className="account__profile-line">
          <span className="account__label">{t("account.profile")}</span> —{" "}
          {/* AccountView's own loading gate only waits on refreshAccount()
              (quota + billing) — App.tsx's status fetch that actually supplies
              authEmail is a separate, independent request. Reaching here
              before that one resolves left this reading auth.title ("Вход в
              PLG"), a login prompt shown to someone already signed in and
              looking at their own loaded quota. A real "not signed in" case
              never reaches this screen at all (App.tsx redirects to AuthView
              first), so the only real code path into this fallback is the
              race — a loading placeholder is honest, a login CTA never is. */}
          <span className="account__email">{displayedEmail || t("common.loading")}</span>
        </p>
      )}

      {displayedCloudMode && displayedBilling && (
        <section className="account__access-card page__card page__card--figma page__card--figma-account">
          <p className="account__card-title">{accessStatus}</p>
          {isTrial && (
            <p className="account__caption">{t("account.trialBeatHint")}</p>
          )}
          {/* "Действует ещё N дн." would duplicate the "Истекает через N дней"
              countdown already shown in the quota block above (same value:
              period_start and subscription_ends_at are both set to now+30d at
              payment), so the active-subscription card carries only its title. */}

          {(showPurchase || showRenewal) && (
            <div className="account__purchase">
              {/* Grace (with generations left) already has working access
                  (hasPaidCloudAccess above), so "Full access" / "Get access" would
                  read as if there is none: that button only retries the failed
                  payment. Everyone else, including an active/grace account with
                  the quota spent, gets the regular paywall. */}
              {showPlanHeading && !isGrace && (
                <p className="account__card-title">{t("account.purchaseTitle")}</p>
              )}
              <p className="account__caption">{t("account.purchasePlanLine")}</p>
              <button
                type="button"
                className="account__cta"
                onClick={onRenew}
                disabled={subscribing}
              >
                {subscribing ? (
                  t("settings.subscribing")
                ) : (
                  // Split the label around {price} so the struck regular price
                  // can be a real <s> element, not text baked into the string.
                  (() => {
                    const buttonKey = isGrace
                      ? "account.retryPaymentButton"
                      : "account.purchaseButton";
                    const [before, after] = t(buttonKey, { price: "\uE000" }).split("\uE000");
                    return (
                      <>
                        {before}
                        {priceNode}
                        {after}
                      </>
                    );
                  })()
                )}
              </button>
            </div>
          )}

          {checkoutNotice && (
            <div className="account__checkout-status" role="status">
              <p className="account__caption">
                <strong>{t("account.checkoutOpened")}</strong> {checkoutNotice}
              </p>
              <button type="button" className="account__text-link" onClick={() => void refreshAccount()}>
                {t("account.refreshStatus")}
              </button>
            </div>
          )}
        </section>
      )}

      {displayedCloudMode && (
        <div className="account__secondary-links">
          <button
            type="button"
            className="account__text-link"
            onClick={async () => {
              await cloudLogout();
              onSaved();
            }}
          >
            {t("auth.logout")}
          </button>
          <button type="button" className="account__text-link account__text-link--danger" onClick={onDeleteAccount}>
            {t("settings.deleteAccount")}
          </button>
        </div>
      )}

      {!showQuota && !displayedCloudMode && (
        <p className="account__empty">{t("account.localDesc")}</p>
      )}

      {error && <p className="page__error">{error}</p>}
      {ACCOUNT_PREVIEW_ENABLED && (
        <aside className="account__preview" aria-label={t("account.previewTitle")}>
          <span>{t("account.previewTitle")}</span>
          <div>
            {([
              ["trial", t("account.accessTrial")],
              ["trialEnded", t("account.previewTrialEnded")],
              ["active", t("account.previewActive")],
              ["grace", t("account.accessPaymentFailed")],
              ["expired", t("account.previewExpired")],
              ["checkout", t("account.previewCheckout")],
            ] as const).map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={previewScenario === id ? "is-active" : undefined}
                onClick={() => {
                  setPreviewScenario(id);
                  setError(null);
                  setCheckoutOpened(false);
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <p>{t("account.previewDesc")}</p>
        </aside>
      )}

      {appVersion && <p className="account__version">PLG {appVersion}</p>}
    </div>
    </>
  );
}
