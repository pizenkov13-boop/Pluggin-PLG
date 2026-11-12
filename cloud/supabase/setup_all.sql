-- PLG Supabase — full setup (new project). Paste in SQL Editor and Run.


-- ========== schema.sql ==========

-- PLG Cloud schema for Supabase (SQL Editor в†’ New query в†’ Run)

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



create table if not exists public.user_devices (

  id uuid primary key default gen_random_uuid(),

  user_id uuid not null references auth.users (id) on delete cascade,

  device_id text not null,

  device_name text,

  last_seen timestamptz not null default now(),

  created_at timestamptz not null default now(),

  unique (user_id, device_id)

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

  payload jsonb,

  processed_at timestamptz not null default now(),

  unique (provider, idempotency_key)

);



create index if not exists payment_events_user_id_idx on public.payment_events (user_id);

create index if not exists payment_events_external_id_idx on public.payment_events (external_id);
create index if not exists payment_events_price_tier_idx on public.payment_events (price_tier, event_type);



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

alter table public.user_devices enable row level security;

alter table public.generation_logs enable row level security;

alter table public.payment_events enable row level security;



create policy "profiles_select_own" on public.profiles

  for select using (auth.uid() = id);



create policy "devices_select_own" on public.user_devices

  for select using (auth.uid() = user_id);



create policy "logs_select_own" on public.generation_logs

  for select using (auth.uid() = user_id);



-- ========== 002_payments.sql ==========

-- Phase 2: payments (run after schema.sql on existing projects)

alter table public.profiles
  add column if not exists price_tier text not null default 'ru'
    check (price_tier in ('cis', 'intl', 'ru', 'ru_promo', 'kz', 'by', 'us', 'ca', 'gb', 'eu', 'other')),
  add column if not exists billing_provider text
    check (billing_provider is null or billing_provider in ('yookassa', 'stripe', 'paddle', 'prodamus', 'robokassa', 'boosty')),
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
  price_tier text,
  payload jsonb,
  processed_at timestamptz not null default now(),
  unique (provider, idempotency_key)
);

create index if not exists payment_events_user_id_idx on public.payment_events (user_id);
create index if not exists payment_events_external_id_idx on public.payment_events (external_id);
create index if not exists payment_events_price_tier_idx on public.payment_events (price_tier, event_type);

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

-- ========== 003_security.sql ==========

-- Phase 3: abuse, bots, bans, trial farming protection

create table if not exists public.security_bans (
  id uuid primary key default gen_random_uuid(),
  ban_type text not null check (ban_type in ('user', 'device', 'ip', 'fingerprint')),
  ban_value text not null,
  reason text,
  banned_by text,
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  unique (ban_type, ban_value)
);

create index if not exists security_bans_lookup_idx on public.security_bans (ban_type, ban_value);

create table if not exists public.device_trial_claims (
  device_id text primary key,
  first_user_id uuid not null references auth.users (id) on delete cascade,
  claimed_at timestamptz not null default now()
);

create table if not exists public.abuse_alerts (
  id uuid primary key default gen_random_uuid(),
  alert_type text not null,
  ip_address text,
  user_id uuid references auth.users (id) on delete set null,
  details jsonb not null default '{}',
  acknowledged boolean not null default false,
  created_at timestamptz not null default now()
);

create index if not exists abuse_alerts_created_idx on public.abuse_alerts (created_at desc);

alter table public.generation_logs
  add column if not exists ip_address text,
  add column if not exists device_id text;

alter table public.profiles
  add column if not exists banned boolean not null default false,
  add column if not exists ban_reason text;

alter table public.security_bans enable row level security;
alter table public.device_trial_claims enable row level security;
alter table public.abuse_alerts enable row level security;

-- ========== 004_infra.sql ==========

-- Phase 4: infra вЂ” feature flags, waitlist, invite ramp

create table if not exists public.feature_flags (
  key text primary key,
  enabled boolean not null default false,
  description text,
  rollout_pct int not null default 100 check (rollout_pct between 0 and 100),
  metadata jsonb not null default '{}',
  updated_at timestamptz not null default now()
);

insert into public.feature_flags (key, enabled, description) values
  ('regenerate_ui', true, 'Show regenerate button in desktop UI'),
  ('premium_plan', false, 'Enable premium Sonnet plan'),
  ('stem_split', true, 'Stem split tool'),
  ('filth_mode', true, 'Filth mode toggle'),
  ('waitlist_gate', false, 'Block signup without invite code'),
  ('maintenance_mode', false, 'Read-only maintenance banner')
on conflict (key) do nothing;

create table if not exists public.waitlist_entries (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  status text not null default 'pending'
    check (status in ('pending', 'invited', 'activated', 'rejected')),
  ramp_tier text not null default 'wave_200',
  invite_code text,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now(),
  invited_at timestamptz
);

create index if not exists waitlist_status_idx on public.waitlist_entries (status);

create table if not exists public.invite_codes (
  code text primary key,
  ramp_tier text not null default 'wave_200',
  max_uses int,
  uses int not null default 0,
  expires_at timestamptz,
  created_at timestamptz not null default now()
);

alter table public.profiles
  add column if not exists ramp_tier text,
  add column if not exists invite_code text;

alter table public.feature_flags enable row level security;
alter table public.waitlist_entries enable row level security;
alter table public.invite_codes enable row level security;

-- ========== 005_legal.sql ==========

-- Phase 5: legal / GDPR

alter table public.profiles
  add column if not exists terms_version text,
  add column if not exists terms_accepted_at timestamptz,
  add column if not exists privacy_version text,
  add column if not exists age_confirmed_at timestamptz,
  add column if not exists deletion_requested_at timestamptz,
  add column if not exists data_region text default 'cis' check (data_region in ('eu', 'us', 'cis'));

create table if not exists public.legal_acceptances (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  doc_type text not null check (doc_type in ('terms', 'privacy', 'age')),
  doc_version text not null,
  accepted_at timestamptz not null default now(),
  ip_address text,
  user_agent text
);

create index if not exists legal_acceptances_user_idx on public.legal_acceptances (user_id);

-- Prompt text must NOT be stored; generation_logs keeps metadata only.
-- Retention purge deletes rows older than PLG_PROMPT_LOG_RETENTION_DAYS.

alter table public.legal_acceptances enable row level security;

-- ========== 006_ops.sql ==========

-- Phase 6: ops вЂ” feedback, attribution, notifications, analytics

create table if not exists public.feedback_submissions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users (id) on delete set null,
  email text,
  category text not null default 'general',
  message text not null,
  app_version text,
  platform text,
  log_excerpt text,
  created_at timestamptz not null default now()
);

create table if not exists public.notification_log (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users (id) on delete set null,
  channel text not null check (channel in ('email', 'telegram')),
  template text not null,
  recipient text not null,
  status text not null default 'queued' check (status in ('queued', 'sent', 'failed')),
  error text,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create table if not exists public.analytics_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users (id) on delete set null,
  event_name text not null,
  properties jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create index if not exists analytics_events_name_idx on public.analytics_events (event_name, created_at desc);
create index if not exists analytics_events_user_idx on public.analytics_events (user_id);

create table if not exists public.status_incidents (
  id uuid primary key default gen_random_uuid(),
  component text not null,
  status text not null check (status in ('investigating', 'degraded', 'outage', 'resolved')),
  title text not null,
  message text,
  started_at timestamptz not null default now(),
  resolved_at timestamptz
);

alter table public.profiles
  add column if not exists utm_source text,
  add column if not exists utm_medium text,
  add column if not exists utm_campaign text,
  add column if not exists utm_content text,
  add column if not exists referrer text,
  add column if not exists first_beat_at timestamptz;

alter table public.feedback_submissions enable row level security;
alter table public.notification_log enable row level security;
alter table public.analytics_events enable row level security;
alter table public.status_incidents enable row level security;

-- ========== 007_intl_billing_flag.sql ==========

-- Phase 7 polish: international billing behind feature flag

insert into public.feature_flags (key, enabled, description) values
  ('intl_billing', false, 'Prodamus international checkout (USD $13.99 / EUR €12.99)')
on conflict (key) do nothing;


-- ========== 008_atomic_quota.sql ==========

-- 008_atomic_quota.sql
-- Race-free beat consumption.
--
-- The old /v1/generate flow read beats_used, checked the limit, ran the
-- (expensive) LLM, then wrote beats_used+1. Concurrent requests on one account
-- could each pass the check and over-generate / over-spend, and the final write
-- was last-writer-wins (lost increments). These functions move the roll + check
-- + increment into a single row-locked transaction so it is atomic.
--
-- The server reserves a credit via plg_consume_beat BEFORE calling the LLM, and
-- refunds via plg_refund_beat if generation fails. Idempotent to re-run.

create or replace function plg_consume_beat(
  p_user uuid,
  p_beat_limit int,
  p_daily_limit int,
  p_trial_beats int,
  p_period_days int
) returns jsonb
language plpgsql
as $$
declare
  v_status text;
  v_period_start timestamptz;
  v_daily_reset date;
  v_beats_used int;
  v_beats_today int;
  v_trial_used int;
  v_grace_until timestamptz;
  v_today date := (now() at time zone 'utc')::date;
  v_consumed_trial boolean := false;
begin
  -- Row lock serialises concurrent consumes for this user.
  select status, period_start, daily_reset, beats_used, beats_today,
         trial_beats_used, grace_until
    into v_status, v_period_start, v_daily_reset, v_beats_used, v_beats_today,
         v_trial_used, v_grace_until
  from public.profiles
  where id = p_user
  for update;

  if not found then
    return jsonb_build_object('allowed', false, 'reason', 'no_profile');
  end if;

  v_status := coalesce(v_status, 'expired');
  v_period_start := coalesce(v_period_start, now());
  v_daily_reset := coalesce(v_daily_reset, v_today);
  v_beats_used := coalesce(v_beats_used, 0);
  v_beats_today := coalesce(v_beats_today, 0);
  v_trial_used := coalesce(v_trial_used, 0);

  -- Grace expiry.
  if v_status = 'grace' and v_grace_until is not null and now() > v_grace_until then
    v_status := 'expired';
    v_grace_until := null;
  end if;

  -- Daily reset (applies to every status).
  if v_today > v_daily_reset then
    v_beats_today := 0;
    v_daily_reset := v_today;
  end if;

  -- Monthly period roll (paid statuses only), mirrors roll_profile().
  if v_status in ('active', 'grace') then
    while now() >= v_period_start + make_interval(days => p_period_days) loop
      v_period_start := v_period_start + make_interval(days => p_period_days);
      v_beats_used := 0;
    end loop;
  end if;

  -- Daily cap is disabled when p_daily_limit is 0.
  if p_daily_limit > 0 and v_beats_today >= p_daily_limit then
    update public.profiles set
      status=v_status, period_start=v_period_start, daily_reset=v_daily_reset,
      beats_used=v_beats_used, beats_today=v_beats_today, grace_until=v_grace_until,
      updated_at=now()
    where id=p_user;
    return jsonb_build_object('allowed', false, 'reason', 'daily', 'status', v_status);
  end if;

  if v_status = 'trial' then
    if v_trial_used >= p_trial_beats then
      update public.profiles set status='expired', updated_at=now() where id=p_user;
      return jsonb_build_object('allowed', false, 'reason', 'trial_ended', 'status', 'expired');
    end if;
    v_trial_used := v_trial_used + 1;
    v_beats_today := v_beats_today + 1;
    v_consumed_trial := true;
    if v_trial_used >= p_trial_beats then
      v_status := 'expired';
    end if;
  elsif v_status in ('active', 'grace') then
    if v_beats_used >= p_beat_limit then
      update public.profiles set
        status=v_status, period_start=v_period_start, daily_reset=v_daily_reset,
        beats_used=v_beats_used, beats_today=v_beats_today, grace_until=v_grace_until,
        updated_at=now()
      where id=p_user;
      return jsonb_build_object('allowed', false, 'reason', 'monthly', 'status', v_status);
    end if;
    v_beats_used := v_beats_used + 1;
    v_beats_today := v_beats_today + 1;
  else
    return jsonb_build_object('allowed', false, 'reason', 'inactive', 'status', v_status);
  end if;

  update public.profiles set
    status = v_status,
    period_start = v_period_start,
    daily_reset = v_daily_reset,
    beats_used = v_beats_used,
    beats_today = v_beats_today,
    trial_beats_used = v_trial_used,
    grace_until = v_grace_until,
    updated_at = now()
  where id = p_user;

  return jsonb_build_object(
    'allowed', true,
    'status', v_status,
    'consumed_trial', v_consumed_trial,
    'beats_used', v_beats_used,
    'beats_today', v_beats_today,
    'trial_beats_used', v_trial_used
  );
end;
$$;

create or replace function plg_refund_beat(p_user uuid, p_was_trial boolean)
returns void
language plpgsql
as $$
begin
  if p_was_trial then
    update public.profiles set
      trial_beats_used = greatest(0, coalesce(trial_beats_used, 0) - 1),
      beats_today = greatest(0, coalesce(beats_today, 0) - 1),
      updated_at = now()
    where id = p_user;
  else
    update public.profiles set
      beats_used = greatest(0, coalesce(beats_used, 0) - 1),
      beats_today = greatest(0, coalesce(beats_today, 0) - 1),
      updated_at = now()
    where id = p_user;
  end if;
end;
$$;

-- Server-only: these are called with the service role. Clients must never
-- reach them directly via PostgREST.
revoke all on function plg_consume_beat(uuid, int, int, int, int) from public, anon, authenticated;
revoke all on function plg_refund_beat(uuid, boolean) from public, anon, authenticated;
grant execute on function plg_consume_beat(uuid, int, int, int, int) to service_role;
grant execute on function plg_refund_beat(uuid, boolean) to service_role;


-- ========== 009_data_moat.sql ==========

-- 009_data_moat.sql
-- Product-learning dataset: generated beats, user actions, ratings, and
-- sanitized pattern JSON. Raw audio / FLP / stems are intentionally excluded.

create table if not exists public.beats (
  id uuid primary key default gen_random_uuid(),
  beat_id text not null unique,
  user_id uuid not null references auth.users (id) on delete cascade,
  parent_beat_id text,
  prompt_hash text,
  prompt_preview text,
  locale text,
  bpm numeric(8, 2),
  style text,
  genre text,
  key text,
  scale text,
  quality_score numeric(6, 2),
  proxy_score numeric(6, 2),
  reward_score numeric(6, 2),
  passed boolean,
  hard_fail boolean,
  model text,
  pipeline jsonb not null default '{}',
  sample_picks jsonb not null default '{}',
  features jsonb not null default '{}',
  status text not null default 'active' check (status in ('active', 'deleted')),
  source text,
  generated_at timestamptz not null default now(),
  synced_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists beats_user_generated_idx on public.beats (user_id, generated_at desc);
create index if not exists beats_genre_idx on public.beats (genre, generated_at desc);
create index if not exists beats_quality_idx on public.beats (quality_score desc);

create table if not exists public.beat_patterns (
  beat_id text primary key references public.beats (beat_id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  pattern jsonb not null,
  pattern_version int not null default 1,
  size_bytes int,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists beat_patterns_user_idx on public.beat_patterns (user_id);

create table if not exists public.beat_events (
  id uuid primary key default gen_random_uuid(),
  beat_id text not null references public.beats (beat_id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  event_type text not null check (
    event_type in ('generated', 'liked', 'disliked', 'deleted', 'exported', 'regenerated')
  ),
  rating int check (rating is null or rating in (-1, 1)),
  source text,
  properties jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create index if not exists beat_events_user_idx on public.beat_events (user_id, created_at desc);
create index if not exists beat_events_beat_idx on public.beat_events (beat_id, created_at desc);
create index if not exists beat_events_type_idx on public.beat_events (event_type, created_at desc);

create table if not exists public.beat_ratings (
  id uuid primary key default gen_random_uuid(),
  beat_id text not null references public.beats (beat_id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  rating int not null check (rating in (-1, 1)),
  genre text,
  quality_score numeric(6, 2),
  proxy_score numeric(6, 2),
  features jsonb not null default '{}',
  pipeline jsonb not null default '{}',
  sample_picks jsonb not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, beat_id)
);

create index if not exists beat_ratings_user_idx on public.beat_ratings (user_id, updated_at desc);
create index if not exists beat_ratings_label_idx on public.beat_ratings (rating, updated_at desc);
create index if not exists beat_ratings_genre_idx on public.beat_ratings (genre, rating);

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

create table if not exists public.beat_ranker_snapshots (
  id uuid primary key default gen_random_uuid(),
  scope text not null default 'global',
  genre text,
  samples int not null default 0,
  thumbs_up int not null default 0,
  thumbs_down int not null default 0,
  model_ready boolean not null default false,
  feature_weights jsonb not null default '{}',
  top_positive_features jsonb not null default '[]',
  created_at timestamptz not null default now()
);

create index if not exists beat_ranker_snapshots_scope_idx
  on public.beat_ranker_snapshots (scope, genre, created_at desc);

alter table public.beats enable row level security;
alter table public.beat_patterns enable row level security;
alter table public.beat_events enable row level security;
alter table public.beat_ratings enable row level security;
alter table public.beat_ranker_snapshots enable row level security;
alter table public.billing_checkout_links enable row level security;
-- 015: freeze the geo-resolved price at first view of a checkout link.
alter table public.billing_checkout_links
  add column if not exists price_locked boolean not null default false;

create policy "beats_select_own" on public.beats
  for select using (auth.uid() = user_id);

create policy "beat_patterns_select_own" on public.beat_patterns
  for select using (auth.uid() = user_id);

create policy "beat_events_select_own" on public.beat_events
  for select using (auth.uid() = user_id);

create policy "beat_ratings_select_own" on public.beat_ratings
  for select using (auth.uid() = user_id);

-- 016: launch promo tier `ru_promo` (299 ₽) — see cloud/app/promo.py.
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
create index if not exists billing_checkout_links_promo_idx
  on public.billing_checkout_links (price_tier, price_locked, expires_at);

-- 017: record the frozen price-book tier on payment_events (cloud/app/promo.py).
alter table public.payment_events add column if not exists price_tier text;
update public.payment_events
set price_tier = payload -> 'object' -> 'metadata' ->> 'price_tier'
where price_tier is null
  and payload -> 'object' -> 'metadata' ->> 'price_tier' is not null;
create index if not exists payment_events_price_tier_idx
  on public.payment_events (price_tier, event_type);

-- 020: 5-year payment retention clock after account deletion (cloud/app/legal.py).
alter table public.payment_events add column if not exists anonymized_at timestamptz;
create index if not exists payment_events_anonymized_at_idx
  on public.payment_events (anonymized_at)
  where anonymized_at is not null;
update public.payment_events
set anonymized_at = now(),
    payload = payload #- '{object,metadata,user_id}'
where user_id is null
  and anonymized_at is null;

-- 021: device trial claim survives account deletion (cloud/app/security.py).
alter table public.device_trial_claims alter column first_user_id drop not null;
alter table public.device_trial_claims
  drop constraint if exists device_trial_claims_first_user_id_fkey;
alter table public.device_trial_claims
  add constraint device_trial_claims_first_user_id_fkey
  foreign key (first_user_id) references auth.users (id) on delete set null;
