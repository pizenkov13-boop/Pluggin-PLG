-- One-time web checkout links issued only by an authenticated desktop client.
-- Store only a SHA-256 hash: a database read never exposes a usable payment link.
create table if not exists public.billing_checkout_links (
  token_hash text primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  price_tier text not null check (price_tier in ('cis', 'intl')),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists billing_checkout_links_expires_at_idx
  on public.billing_checkout_links (expires_at);

alter table public.billing_checkout_links enable row level security;
-- Service role only. Browser clients use an opaque short-lived token through
-- the Cloud API and never get direct table access.
