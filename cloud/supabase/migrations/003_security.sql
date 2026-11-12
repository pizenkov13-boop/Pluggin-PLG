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
