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

create policy "beats_select_own" on public.beats
  for select using (auth.uid() = user_id);

create policy "beat_patterns_select_own" on public.beat_patterns
  for select using (auth.uid() = user_id);

create policy "beat_events_select_own" on public.beat_events
  for select using (auth.uid() = user_id);

create policy "beat_ratings_select_own" on public.beat_ratings
  for select using (auth.uid() = user_id);
