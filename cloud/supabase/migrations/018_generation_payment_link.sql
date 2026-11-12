-- Tie each generation to the payment/entitlement period that was active when
-- it happened, so refund eligibility ("0 paid generations since purchase X")
-- can be answered by a direct FK match instead of inferring from timestamps.
--
-- Both columns are nullable, no default, no backfill: existing generation_logs
-- rows simply stay NULL. NULL means one of two things depending on when the
-- row was created relative to this migration:
--   - before the user's first purchase (the free trial beat) — correct, this
--     is the "not paid" signal and needs no further handling;
--   - after a purchase but before this migration shipped (legacy, untagged) —
--     cannot be proven paid or free from payment_event_id alone. A manual
--     refund-lookup query must treat these as "cannot prove 0 paid
--     generations" (manual review), never as evidence of eligibility —
--     see tests/test_cloud_refund_linkage.py for the decision rule.
--
-- No new tables, no new status values, no backfill UPDATE, no refund
-- endpoint/module — additive schema only; eligibility stays a manual
-- read-only SQL lookup run by a support person.

alter table public.generation_logs
  add column if not exists payment_event_id uuid references public.payment_events (id);

create index if not exists generation_logs_payment_event_id_idx
  on public.generation_logs (payment_event_id);

-- Cache of "which payment_events row currently funds this profile's active
-- entitlement period" — set once per purchase in activate_subscription(), so
-- /v1/generate can stamp generation_logs.payment_event_id from the already-
-- loaded profile row instead of an extra query per generation. Untouched by
-- calendar quota rollover (roll_profile) — a rollover doesn't change which
-- payment funds the subscription, only that its beats_used counter resets.
alter table public.profiles
  add column if not exists current_payment_event_id uuid references public.payment_events (id);
