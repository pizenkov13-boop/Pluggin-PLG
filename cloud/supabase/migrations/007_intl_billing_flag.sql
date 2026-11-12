-- Phase 7 polish: international billing behind feature flag

insert into public.feature_flags (key, enabled, description) values
  ('intl_billing', false, 'Prodamus international checkout (USD $13.99 / EUR €12.99)')
on conflict (key) do nothing;
