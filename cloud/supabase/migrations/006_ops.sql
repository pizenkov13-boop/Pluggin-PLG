-- Phase 6: ops — feedback, attribution, notifications, analytics

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
