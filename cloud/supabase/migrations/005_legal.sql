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
