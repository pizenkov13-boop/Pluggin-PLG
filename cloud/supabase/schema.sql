-- PLG Cloud schema for Supabase (SQL Editor → New query → Run)
-- Auth users live in auth.users (Supabase Auth handles signup / magic link / reset).

create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  plan text not null default 'base' check (plan in ('base', 'premium')),
  status text not null default 'trial' check (status in ('trial', 'active', 'expired', 'cancelled', 'grace')),
  price_tier text not null default 'ru' check (price_tier in ('cis', 'intl', 'ru', 'ru_promo', 'kz', 'by', 'us', 'ca', 'gb', 'eu', 'other')),
  billing_provider text check (billing_provider is null or billing_provider in ('yookassa', 'stripe', 'paddle', 'prodamus', 'robokassa', 'boosty')),
  external_customer_id text,
  external_subscription_id text,
  subscription_ends_at timestamptz,
  grace_until timestamptz,
  trial_beats_used int not null default 0,
  period_start timestamptz not null default now(),
  beats_used int not null default 0,
  beats_today int not null default 0,
  daily_reset date not null default current_date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.user_devices (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  device_id text not null,
  device_name text,
  last_seen timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (user_id, device_id)
);

-- One short-lived, hashed verification code per account. The raw code is only
-- ever sent through Resend and never stored in Supabase.
create table if not exists public.email_verification_codes (
  user_id uuid primary key references auth.users (id) on delete cascade,
  code_hash text not null,
  expires_at timestamptz not null,
  attempts int not null default 0 check (attempts >= 0),
  last_sent_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create table if not exists public.generation_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  model text not null,
  prompt_chars int not null default 0,
  tokens_in int,
  tokens_out int,
  cost_usd numeric(12, 6),
  created_at timestamptz not null default now()
);

create table if not exists public.payment_events (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  idempotency_key text not null,
  event_type text not null,
  user_id uuid references auth.users (id) on delete set null,
  amount_cents int,
  currency text,
  external_id text,
  price_tier text,
  referral_code text,
  payload jsonb,
  processed_at timestamptz not null default now(),
  unique (provider, idempotency_key)
);

create index if not exists payment_events_user_id_idx on public.payment_events (user_id);
create index if not exists payment_events_external_id_idx on public.payment_events (external_id);
create index if not exists payment_events_price_tier_idx on public.payment_events (price_tier, event_type);
create index if not exists payment_events_referral_code_idx on public.payment_events (referral_code, event_type);

-- Opaque, one-use links from the authenticated desktop app to the web payment
-- page. The raw link token is never stored in the database.
create table if not exists public.billing_checkout_links (
  token_hash text primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  price_tier text not null check (price_tier in ('cis', 'intl', 'ru', 'ru_promo', 'kz', 'by', 'us', 'ca', 'gb', 'eu', 'other')),
  price_locked boolean not null default false,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists billing_checkout_links_expires_at_idx
  on public.billing_checkout_links (expires_at);

create table if not exists public.kill_switch (
  id int primary key default 1 check (id = 1),
  daily_spend_cap_usd numeric(12, 2) not null default 500,
  today_spend_usd numeric(12, 6) not null default 0,
  spend_reset date not null default current_date,
  enabled boolean not null default true
);

insert into public.kill_switch (id) values (1) on conflict (id) do nothing;

-- Auto-create profile on signup (trial: 1 free beat before paywall)
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, status) values (new.id, 'trial') on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- RLS: users read own profile only; service role bypasses for API server
alter table public.profiles enable row level security;
alter table public.user_devices enable row level security;
alter table public.email_verification_codes enable row level security;
alter table public.generation_logs enable row level security;
alter table public.payment_events enable row level security;
alter table public.billing_checkout_links enable row level security;

create policy "profiles_select_own" on public.profiles
  for select using (auth.uid() = id);

create policy "devices_select_own" on public.user_devices
  for select using (auth.uid() = user_id);

create policy "logs_select_own" on public.generation_logs
  for select using (auth.uid() = user_id);

