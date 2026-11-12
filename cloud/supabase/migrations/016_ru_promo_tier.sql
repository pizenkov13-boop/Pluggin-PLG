-- Launch promo tier: `ru_promo` (299 ₽) — the first-100-buyers price.
-- Same domestic RUB / YooKassa rail as `ru`; a distinct price-book key so the
-- frozen checkout-link price, the webhook amount check and profiles.price_tier
-- each record which price the buyer actually paid. See cloud/app/promo.py.

alter table public.profiles drop constraint if exists profiles_price_tier_check;
alter table public.profiles
  add constraint profiles_price_tier_check
  check (price_tier in
    ('cis', 'intl', 'ru', 'ru_promo', 'kz', 'by', 'us', 'ca', 'gb', 'eu', 'other'));

alter table public.billing_checkout_links drop constraint if exists billing_checkout_links_price_tier_check;
alter table public.billing_checkout_links
  add constraint billing_checkout_links_price_tier_check
  check (price_tier in
    ('cis', 'intl', 'ru', 'ru_promo', 'kz', 'by', 'us', 'ca', 'gb', 'eu', 'other'));

-- The promo gate counts links already frozen to `ru_promo` (price_locked, and
-- either consumed or still within TTL). Keep that count cheap under a spike.
create index if not exists billing_checkout_links_promo_idx
  on public.billing_checkout_links (price_tier, price_locked, expires_at);
