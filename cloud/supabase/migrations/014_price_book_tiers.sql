-- Phase 1 of the multi-country price book (cloud/app/pricing.py).
-- Widen price_tier past the old cis/intl pair. Only `ru` and `other` are served
-- today; kz/by/us/ca/gb/eu are reserved so Phase 2 (Prodamus multi-currency) is
-- a code change only, no schema change. Legacy 'cis'/'intl' stay accepted for
-- deploy-order and rollback safety, and existing rows are backfilled.
-- See docs/PRODAMUS_INTL_BILLING_PLAN.md.

alter table public.profiles drop constraint if exists profiles_price_tier_check;
alter table public.profiles
  add constraint profiles_price_tier_check
  check (price_tier in ('cis', 'intl', 'ru', 'kz', 'by', 'us', 'ca', 'gb', 'eu', 'other'));

update public.profiles set price_tier = 'ru'    where price_tier = 'cis';
update public.profiles set price_tier = 'other' where price_tier = 'intl';

alter table public.profiles alter column price_tier set default 'ru';

-- Reserve the Prodamus provider name too (Phase 2 writes it via
-- activate_subscription(provider="prodamus")).
alter table public.profiles drop constraint if exists profiles_billing_provider_check;
alter table public.profiles
  add constraint profiles_billing_provider_check
  check (billing_provider is null or billing_provider in
    ('yookassa', 'stripe', 'paddle', 'prodamus', 'robokassa', 'boosty'));

alter table public.billing_checkout_links drop constraint if exists billing_checkout_links_price_tier_check;
alter table public.billing_checkout_links
  add constraint billing_checkout_links_price_tier_check
  check (price_tier in ('cis', 'intl', 'ru', 'kz', 'by', 'us', 'ca', 'gb', 'eu', 'other'));
