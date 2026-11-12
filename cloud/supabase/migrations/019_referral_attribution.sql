-- Paid-placement referral attribution (example.com/r/<code> -> plg_ref cookie
-- -> checkout.js -> YooKassa payment metadata.referral_code -> here).
--
-- Codes are hand-issued strings (partner names), not a self-serve referral
-- program — no partners table, no UI. See cloud/app/checkout_links.py's
-- clean_referral_code and cloud/app/analytics_ops.py's admin_referral_report
-- (GET /v1/admin/referrals) for the payout report.

alter table public.payment_events
  add column if not exists referral_code text;

create index if not exists payment_events_referral_code_idx
  on public.payment_events (referral_code, event_type);
