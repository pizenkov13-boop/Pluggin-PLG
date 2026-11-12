-- Phase 4: infra — feature flags, waitlist, invite ramp

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
