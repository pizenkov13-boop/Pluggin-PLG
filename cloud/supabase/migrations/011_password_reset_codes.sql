-- Dedicated short-lived codes for PLG/Unisender password reset.
create table if not exists public.password_reset_codes (
  user_id uuid primary key references auth.users (id) on delete cascade,
  code_hash text not null,
  expires_at timestamptz not null,
  attempts int not null default 0 check (attempts >= 0),
  last_sent_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

alter table public.password_reset_codes enable row level security;

create or replace function public.auth_user_id_by_email(p_email text)
returns uuid
language sql
security definer
set search_path = auth, public
as $$
  select id from auth.users where lower(email) = lower(trim(p_email)) limit 1;
$$;

revoke all on function public.auth_user_id_by_email(text) from public, anon, authenticated;
grant execute on function public.auth_user_id_by_email(text) to service_role;
