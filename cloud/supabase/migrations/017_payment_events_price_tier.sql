-- Record the frozen price-book tier on every payment_events row.
--
-- The launch-promo counter (cloud/app/promo.py :: promo_paid_count) needs an
-- authoritative "which offer did this buyer take" signal. It used to match on
-- amount_cents == promo price, which breaks the moment the promo price is
-- anything but its final value — e.g. a 10 ₽ YooKassa smoke test made the
-- counter tally 10 ₽ payments. price_tier is the frozen key ('ru' / 'ru_promo'
-- / 'eu' / …) written by claim_idempotency from the payment metadata; matching
-- on it is immune to price edits and test amounts.

alter table public.payment_events
  add column if not exists price_tier text;

-- Backfill from the stored provider payload where the tier is recoverable
-- (YooKassa: object.metadata.price_tier). Rows without it stay NULL — NULL is
-- "not a promo sale", the safe direction for the counter.
update public.payment_events
set price_tier = payload -> 'object' -> 'metadata' ->> 'price_tier'
where price_tier is null
  and payload -> 'object' -> 'metadata' ->> 'price_tier' is not null;

create index if not exists payment_events_price_tier_idx
  on public.payment_events (price_tier, event_type);
