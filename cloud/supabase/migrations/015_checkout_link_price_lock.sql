-- Freeze the geo-resolved price onto a checkout link at first view, so the
-- amount shown on checkout.html can never change between render and the pay
-- click (see cloud/app/checkout_links.py:lock_checkout_link_price).
-- The link's price_tier is set from the visitor's IP country on the first
-- GET /v1/billing/checkout-link/{token}; price_locked flips false -> true then
-- and the POST that creates the payment reads the frozen value.

alter table public.billing_checkout_links
  add column if not exists price_locked boolean not null default false;
