-- Phase 2: payments (run after schema.sql on existing projects)

alter table public.profiles
  add column if not exists price_tier text not null default 'cis'
    check (price_tier in ('cis', 'intl')),
  add column if not exists billing_provider text
    check (billing_provider is null or billing_provider in ('yookassa', 'stripe', 'paddle', 'robokassa', 'boosty')),
  add column if not exists external_customer_id text,
  add column if not exists external_subscription_id text,
  add column if not exists subscription_ends_at timestamptz,
  add column if not exists grace_until timestamptz,
  add column if not exists trial_beats_used int not null default 0;

alter table public.profiles drop constraint if exists profiles_status_check;
alter table public.profiles add constraint profiles_status_check
  check (status in ('trial', 'active', 'expired', 'cancelled', 'grace'));

create table if not exists public.payment_events (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  idempotency_key text not null,
  event_type text not null,
  user_id uuid references auth.users (id) on delete set null,
  amount_cents int,
  currency text,
  external_id text,
  payload jsonb,
  processed_at timestamptz not null default now(),
  unique (provider, idempotency_key)
);

create index if not exists payment_events_user_id_idx on public.payment_events (user_id);
create index if not exists payment_events_external_id_idx on public.payment_events (external_id);

alter table public.payment_events enable row level security;

-- Service role only (no client policies)

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, status)
  values (new.id, 'trial')
  on conflict (id) do nothing;
  return new;
end;
$$;
